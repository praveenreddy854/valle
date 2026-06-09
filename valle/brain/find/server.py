from __future__ import annotations

import json
import logging
import signal
import time
from typing import Any

import cv2
import numpy as np
import websocket

from ..config import BrainConfig
from ..depth import DepthEstimator
from .config import FindConfig
from .detector import Detector
from .seek import SeekLoop


log = logging.getLogger("valle.brain.find")


class FindServer:
    def __init__(
        self,
        config: FindConfig,
        *,
        detector: Detector,
        brain_config: BrainConfig,
        depth: DepthEstimator,
    ) -> None:
        self._config = config
        self._detector = detector
        self._brain_config = brain_config
        self._depth = depth
        self._stop_requested = False
        self._seek: SeekLoop | None = None

    def request_stop(self) -> None:
        self._stop_requested = True

    def load(self) -> None:
        self._detector.load()

    def run(self) -> None:
        self.load()
        while not self._stop_requested:
            try:
                self._serve_one_connection()
            except Exception:
                log.exception("WebSocket session ended with error")
            if self._stop_requested:
                break
            log.info(
                "reconnecting to %s in %.1fs",
                self._config.pi_ws_url,
                self._config.reconnect_seconds,
            )
            time.sleep(self._config.reconnect_seconds)

    def _serve_one_connection(self) -> None:
        ws = websocket.create_connection(self._config.pi_ws_url, timeout=10)
        log.info("connected to %s", self._config.pi_ws_url)
        try:
            while not self._stop_requested:
                try:
                    raw = ws.recv()
                except websocket.WebSocketTimeoutException:
                    continue
                if not raw:
                    log.info("Pi closed WebSocket")
                    return
                response = self._handle(raw)
                if response is not None:
                    ws.send(json.dumps(response))
        finally:
            try:
                ws.close()
            except Exception:
                pass

    def _handle(self, raw: str) -> dict[str, Any] | None:
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("ignoring non-JSON message: %r", raw)
            return None
        message_type = message.get("type")
        if message_type == "find":
            return self._handle_find(message)
        if message_type == "seek":
            return self._handle_seek(message)
        return None

    def _handle_find(self, message: dict[str, Any]) -> dict[str, Any]:
        message_id = message.get("id")
        query = message.get("object")
        if not isinstance(message_id, str) or not isinstance(query, str):
            return {"id": message_id, "error": "malformed find request"}

        image, capture_seconds = self._capture_one()
        if image is None:
            return {"id": message_id, "error": "no frame available"}

        try:
            results = self._detector.detect(image, query)
        except Exception as exc:
            log.exception("detection failed")
            return {"id": message_id, "error": f"detection failed: {exc}"}

        return {
            "id": message_id,
            "type": "find_result",
            "object": query,
            "found": len(results) > 0,
            "results": results,
            "capture_seconds": capture_seconds,
        }

    def handle_find_request(self, object_query: str) -> dict[str, Any]:
        response = self._handle_find(
            {"id": "brain-api", "type": "find", "object": object_query}
        )
        response.pop("id", None)
        return response

    def _handle_seek(self, message: dict[str, Any]) -> dict[str, Any]:
        message_id = message.get("id")
        query = message.get("object")
        max_seconds_raw = message.get("max_seconds")
        speed_raw = message.get("speed")
        if not isinstance(message_id, str) or not isinstance(query, str):
            return {"id": message_id, "error": "malformed seek request"}
        max_seconds = (
            float(max_seconds_raw)
            if isinstance(max_seconds_raw, (int, float))
            else self._config.seek_default_max_seconds
        )
        speed = float(speed_raw) if isinstance(speed_raw, (int, float)) else None

        try:
            seek = self._seek_loop()
            result = seek.run(
                object_query=query, max_seconds=max_seconds, speed=speed
            )
        except Exception as exc:
            log.exception("seek failed")
            return {"id": message_id, "error": f"seek failed: {exc}"}

        return {"id": message_id, "type": "seek_result", **result}

    def handle_seek_request(
        self,
        object_query: str,
        *,
        max_seconds: float | None = None,
        speed: float | None = None,
    ) -> dict[str, Any]:
        response = self._handle_seek(
            {
                "id": "brain-api",
                "type": "seek",
                "object": object_query,
                "max_seconds": (
                    max_seconds
                    if max_seconds is not None
                    else self._config.seek_default_max_seconds
                ),
                "speed": speed,
            }
        )
        response.pop("id", None)
        return response

    def _seek_loop(self) -> SeekLoop:
        if self._seek is None:
            self._seek = SeekLoop(
                brain_config=self._brain_config,
                find_config=self._config,
                detector=self._detector,
                depth=self._depth,
            )
        return self._seek

    def _capture_one(self) -> tuple[np.ndarray | None, float]:
        log.debug("capturing one frame from %s", self._config.camera_url)
        started = time.monotonic()
        cap = cv2.VideoCapture(self._config.camera_url)
        try:
            if not cap.isOpened():
                return None, 0.0
            ok, image = cap.read()
            if not ok or image is None:
                return None, 0.0
            return image, round(time.monotonic() - started, 3)
        finally:
            cap.release()


def build_server(config: FindConfig | None = None) -> FindServer:
    config = config or FindConfig.from_env()
    brain_config = BrainConfig.from_env()
    detector = Detector(
        config.detector_model,
        config.detector_device,
        score_threshold=config.score_threshold,
        max_results=config.max_results,
    )
    depth = DepthEstimator(brain_config.depth_model, brain_config.depth_device)
    return FindServer(
        config, detector=detector, brain_config=brain_config, depth=depth
    )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    server = build_server()

    def _handle_signal(signum: int, frame: object) -> None:
        log.info("signal %d received; stopping", signum)
        server.request_stop()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    server.run()
