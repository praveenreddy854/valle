from __future__ import annotations

import json
import logging
import signal
import time
from typing import Any

import cv2
import numpy as np
import websocket

from .config import FindConfig
from .detector import Detector


log = logging.getLogger("valle.find")


class FindServer:
    def __init__(self, config: FindConfig, *, detector: Detector) -> None:
        self._config = config
        self._detector = detector
        self._stop_requested = False

    def request_stop(self) -> None:
        self._stop_requested = True

    def run(self) -> None:
        self._detector.load()
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
        if message.get("type") != "find":
            return None
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
    detector = Detector(
        config.detector_model,
        config.detector_device,
        score_threshold=config.score_threshold,
        max_results=config.max_results,
    )
    return FindServer(config, detector=detector)


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
