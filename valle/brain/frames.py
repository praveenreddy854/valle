from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

import cv2
import numpy as np


log = logging.getLogger("valle.brain.frames")


@dataclass(frozen=True)
class Frame:
    image: np.ndarray
    timestamp: float


class FrameReader:
    """Always exposes the most recently received MJPEG frame.

    Background-thread VideoCapture reader; ``latest()`` returns the freshest
    frame regardless of how slow the consumer is. Frames older than the
    consumer's tick are simply overwritten - we never queue.
    """

    def __init__(self, url: str, *, max_frame_age_seconds: float = 1.0) -> None:
        self._url = url
        self._max_age = max_frame_age_seconds
        self._lock = threading.Lock()
        self._latest: Frame | None = None
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="valle-frame-reader", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def latest(self) -> Frame | None:
        with self._lock:
            frame = self._latest
        if frame is None:
            return None
        if time.monotonic() - frame.timestamp > self._max_age:
            return None
        return frame

    def _run(self) -> None:
        while not self._stop_event.is_set():
            cap = cv2.VideoCapture(self._url)
            if not cap.isOpened():
                log.warning("MJPEG stream not opening at %s; retrying", self._url)
                cap.release()
                self._stop_event.wait(1.0)
                continue
            try:
                while not self._stop_event.is_set():
                    ok, image = cap.read()
                    if not ok or image is None:
                        log.warning("MJPEG read failed; reconnecting")
                        break
                    with self._lock:
                        self._latest = Frame(image=image, timestamp=time.monotonic())
            finally:
                cap.release()
