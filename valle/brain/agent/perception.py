"""Mission perception for the CrewAI agent runner.

While a mission runs, a background loop reads camera frames, reduces
monocular depth to per-strip clearance, and posts it to the Pi's
``POST /agent/reflex`` so the reflex gate can authorize movement intents.
The same frames also answer the agent's ``find_object`` queries through
the OWLv2 detector.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

from .client import AgentPiClient
from ..config import BrainConfig
from ..policy import StripLayout, reduce_to_strips

log = logging.getLogger("valle.brain.agent.perception")


class AgentPerception:
    def __init__(
        self,
        *,
        client: AgentPiClient,
        frames: Any,
        depth: Any,
        detector: Any,
        layout: StripLayout,
        tick_hz: float,
    ) -> None:
        self._client = client
        self._frames = frames
        self._depth = depth
        self._detector = detector
        self._layout = layout
        self._tick_interval = 1.0 / max(0.5, tick_hz)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._depth.load()
        self._frames.start()
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="valle-agent-perception", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        self._frames.stop()

    def latest_image(self) -> Any | None:
        frame = self._frames.latest()
        return None if frame is None else frame.image

    def find(self, object_query: str) -> dict[str, Any]:
        frame = self._frames.latest()
        if frame is None:
            return {
                "ok": False,
                "object": object_query,
                "found": False,
                "error": "no camera frame available",
            }
        detections = self._detector.detect(frame.image, object_query)
        width = int(frame.image.shape[1])
        for detection in detections:
            detection["position"] = _horizontal_position(detection["box"], width)
        return {
            "ok": True,
            "object": object_query,
            "found": len(detections) > 0,
            "results": detections,
        }

    def _run(self) -> None:
        while not self._stop_event.is_set():
            tick_started = time.monotonic()
            try:
                self._tick()
            except Exception:
                log.exception("reflex tick failed")
            remaining = self._tick_interval - (time.monotonic() - tick_started)
            if remaining > 0:
                self._stop_event.wait(remaining)

    def _tick(self) -> bool:
        frame = self._frames.latest()
        if frame is None:
            return False
        depth = self._depth.infer(frame.image)
        strips = reduce_to_strips(depth, self._layout)
        self._client.post_reflex(
            left=strips.left,
            center=strips.center,
            right=strips.right,
            source="depth",
        )
        return True


def build_agent_perception(client: AgentPiClient) -> AgentPerception:
    from ..depth import DepthEstimator
    from ..frames import FrameReader
    from ..find.config import FindConfig
    from ..find.detector import Detector

    brain_config = BrainConfig.from_env()
    find_config = FindConfig.from_env()
    layout = StripLayout(
        left_strip_end=brain_config.left_strip_end,
        right_strip_start=brain_config.right_strip_start,
        top_crop=brain_config.top_crop,
        bottom_crop=brain_config.bottom_crop,
    )
    return AgentPerception(
        client=client,
        frames=FrameReader(brain_config.camera_url),
        depth=DepthEstimator(brain_config.depth_model, brain_config.depth_device),
        detector=Detector(
            find_config.detector_model,
            find_config.detector_device,
            score_threshold=find_config.score_threshold,
            max_results=find_config.max_results,
        ),
        layout=layout,
        tick_hz=brain_config.tick_hz,
    )


def _horizontal_position(box: dict[str, Any], frame_width: int) -> str:
    center_x = (float(box["xmin"]) + float(box["xmax"])) / 2.0
    if center_x < frame_width / 3.0:
        return "left"
    if center_x > frame_width * 2.0 / 3.0:
        return "right"
    return "center"
