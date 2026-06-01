"""Drive-and-find loop.

While a seek is active, the brain drives the car with the same depth-based
reflex policy the autopilot uses and runs the OWLv2 detector on every
tick. The loop ends on first hit, on max_seconds, or on a session error.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import requests

from ..brain.client import PiClient, PiClientError, SessionRejectedError
from ..brain.config import BrainConfig
from ..brain.depth import DepthEstimator
from ..brain.frames import FrameReader
from ..brain.policy import (
    BACKWARD,
    FORWARD,
    LEFT,
    RIGHT,
    ReflexPolicy,
    StripLayout,
    reduce_to_strips,
)
from .config import FindConfig
from .detector import Detector


log = logging.getLogger("valle.find.seek")


class SeekLoop:
    def __init__(
        self,
        *,
        brain_config: BrainConfig,
        find_config: FindConfig,
        detector: Detector,
        depth: DepthEstimator,
    ) -> None:
        self._brain = brain_config
        self._find = find_config
        self._detector = detector
        self._depth = depth
        self._client = PiClient(
            brain_config.pi_base_url,
            timeout_seconds=brain_config.request_timeout_seconds,
        )
        self._layout = StripLayout(
            left_strip_end=brain_config.left_strip_end,
            right_strip_start=brain_config.right_strip_start,
            top_crop=brain_config.top_crop,
            bottom_crop=brain_config.bottom_crop,
        )

    def run(self, *, object_query: str, max_seconds: float) -> dict[str, Any]:
        self._depth.load()
        self._detector.load()

        frames = FrameReader(self._brain.camera_url)
        frames.start()
        policy = ReflexPolicy(
            blocked_threshold=self._brain.blocked_threshold,
            hysteresis_margin=self._brain.hysteresis_margin,
        )

        try:
            session = self._client.start(
                max_seconds=min(max_seconds, self._brain.session_max_seconds or max_seconds),
                idle_seconds=self._brain.session_idle_seconds,
            )
        except SessionRejectedError as exc:
            frames.stop()
            return {"found": False, "object": object_query, "error": str(exc)}

        session_id = session["session_id"]
        log.info(
            "seek session %s for object=%r max=%.1fs", session_id, object_query, max_seconds
        )

        started = time.monotonic()
        tick_interval = 1.0 / max(0.5, self._brain.tick_hz)
        result: dict[str, Any] = {
            "found": False,
            "object": object_query,
            "elapsed_seconds": 0.0,
            "ticks": 0,
        }
        ticks = 0
        blind_since: float | None = None

        try:
            while True:
                now = time.monotonic()
                elapsed = now - started
                if elapsed >= max_seconds:
                    result["elapsed_seconds"] = round(elapsed, 2)
                    result["ticks"] = ticks
                    result["reason"] = "max_seconds"
                    break

                frame = frames.latest()
                if frame is None:
                    if blind_since is None:
                        blind_since = now
                    elif now - blind_since >= self._brain.grace_seconds:
                        result["elapsed_seconds"] = round(elapsed, 2)
                        result["ticks"] = ticks
                        result["reason"] = "blind"
                        break
                    time.sleep(tick_interval)
                    continue
                blind_since = None

                detections = self._detector.detect(frame.image, object_query)
                hit = self._first_above(detections, self._find.seek_found_score)
                if hit is not None:
                    self._send_drive(session_id, "stop_pulse")
                    result.update(
                        {
                            "found": True,
                            "score": hit["score"],
                            "label": hit["label"],
                            "box": hit["box"],
                            "elapsed_seconds": round(elapsed, 2),
                            "ticks": ticks + 1,
                            "reason": "found",
                        }
                    )
                    break

                action = self._reflex_action(frame.image, policy)
                self._send_drive(session_id, action)
                ticks += 1
                self._sleep_remainder(now, tick_interval)
        finally:
            try:
                self._client.stop(session_id, reason="manual")
            except Exception:
                self._client.panic_stop()
            frames.stop()

        return result

    def _reflex_action(self, image: Any, policy: ReflexPolicy) -> str:
        depth = self._depth.infer(image)
        strips = reduce_to_strips(depth, self._layout)
        return policy.decide(strips)

    def _send_drive(self, session_id: str, action: str) -> bool:
        if action == "stop_pulse":
            return True
        duration, speed = self._action_params(action)
        try:
            self._client.drive(
                session_id, direction=action, duration=duration, speed=speed
            )
            return True
        except (requests.RequestException, PiClientError):
            return False

    def _action_params(self, action: str) -> tuple[float, float]:
        if action == FORWARD:
            return self._brain.pulse_forward, self._brain.speed_forward
        if action == BACKWARD:
            return self._brain.pulse_backward, self._brain.speed_backward
        if action in (LEFT, RIGHT):
            return self._brain.pulse_turn, self._brain.speed_turn
        raise ValueError(f"unknown action: {action}")

    @staticmethod
    def _first_above(detections: list[dict[str, Any]], threshold: float) -> dict[str, Any] | None:
        for detection in detections:
            if float(detection.get("score", 0.0)) >= threshold:
                return detection
        return None

    @staticmethod
    def _sleep_remainder(tick_started: float, tick_interval: float) -> None:
        elapsed = time.monotonic() - tick_started
        remaining = tick_interval - elapsed
        if remaining > 0:
            time.sleep(remaining)
