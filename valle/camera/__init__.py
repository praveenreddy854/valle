"""Standalone MJPEG camera streamer for Valle.

Runs in its own process on the Raspberry Pi so that the motor controller's
HTTP thread pool is never blocked by streaming clients. Brain consumes the
stream from ``http://<pi>:<port>/stream.mjpg``.
"""
from __future__ import annotations

import io
import logging
import os
import socketserver
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Condition

from ..observability import setup_observability


from .. import telemetry

log = logging.getLogger("valle.camera")


@dataclass(frozen=True)
class CameraConfig:
    host: str = "0.0.0.0"
    port: int = 8081
    width: int = 640
    height: int = 480
    framerate: int = 10

    @classmethod
    def from_env(cls) -> "CameraConfig":
        return cls(
            host=os.getenv("VALLE_CAMERA_HOST", cls.host),
            port=_env_int("VALLE_CAMERA_PORT", cls.port),
            width=_env_int("VALLE_CAMERA_WIDTH", cls.width),
            height=_env_int("VALLE_CAMERA_HEIGHT", cls.height),
            framerate=_env_int("VALLE_CAMERA_FPS", cls.framerate),
        )


class _StreamingOutput(io.BufferedIOBase):
    def __init__(self) -> None:
        self.frame: bytes | None = None
        self.condition = Condition()

    def write(self, buf: bytes) -> int:  # type: ignore[override]
        with self.condition:
            self.frame = bytes(buf)
            self.condition.notify_all()
        telemetry.record_camera_frame()
        return len(buf)


def _make_handler(output: _StreamingOutput) -> type[BaseHTTPRequestHandler]:
    class StreamingHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            logging.getLogger("valle.camera").debug("%s - %s", self.address_string(), format % args)

        def do_GET(self) -> None:  # noqa: N802
            if self.path in ("/", "/stream", "/stream.mjpg"):
                self.send_response(200)
                self.send_header("Cache-Control", "no-cache, private")
                self.send_header("Pragma", "no-cache")
                self.send_header(
                    "Content-Type",
                    "multipart/x-mixed-replace; boundary=FRAME",
                )
                self.end_headers()
                try:
                    while True:
                        with output.condition:
                            output.condition.wait()
                            frame = output.frame
                        if frame is None:
                            continue
                        self.wfile.write(b"--FRAME\r\n")
                        self.send_header("Content-Type", "image/jpeg")
                        self.send_header("Content-Length", str(len(frame)))
                        self.end_headers()
                        self.wfile.write(frame)
                        self.wfile.write(b"\r\n")
                except (BrokenPipeError, ConnectionResetError):
                    return
            elif self.path == "/health":
                body = b'{"ok":true}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_error(404)

    return StreamingHandler


class _ThreadedHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class CameraHandle:
    """Owns the picamera2 recorder and MJPEG server so callers can shut down."""

    def __init__(self, server: _ThreadedHTTPServer, picam2: object) -> None:
        self._server = server
        self._picam2 = picam2

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._picam2.stop_recording()  # type: ignore[attr-defined]


def start_camera_server(config: CameraConfig | None = None) -> CameraHandle | None:
    """Start the picamera2 recorder and MJPEG server on background threads.

    Returns a :class:`CameraHandle`, or ``None`` when picamera2 is unavailable
    (e.g. off the Pi) so the caller can run without a camera. The MJPEG server
    runs on its own port and thread, never on the motor API's request threads.
    """
    config = config or CameraConfig.from_env()

    try:
        from picamera2 import Picamera2
        from picamera2.encoders import MJPEGEncoder
        from picamera2.outputs import FileOutput
    except ImportError:
        log.warning(
            "picamera2 is not installed; camera stream disabled. Install it on "
            "the Raspberry Pi via `sudo apt install -y python3-picamera2`."
        )
        return None

    picam2 = Picamera2()
    video_config = picam2.create_video_configuration(
        main={"size": (config.width, config.height)},
        controls={"FrameRate": config.framerate},
    )
    picam2.configure(video_config)

    output = _StreamingOutput()
    picam2.start_recording(MJPEGEncoder(), FileOutput(output))
    server = _ThreadedHTTPServer((config.host, config.port), _make_handler(output))
    threading.Thread(
        target=server.serve_forever, name="valle-camera", daemon=True
    ).start()
    log.info(
        "valle camera streaming on http://%s:%d/stream.mjpg (%dx%d @ %d fps)",
        config.host,
        config.port,
        config.width,
        config.height,
        config.framerate,
    )
    return CameraHandle(server, picam2)


def serve(config: CameraConfig | None = None) -> None:
    config = config or CameraConfig.from_env()
    setup_observability("valle-camera")
    handle = start_camera_server(config)
    if handle is None:
        raise RuntimeError(
            "picamera2 is not installed. Install it on the Raspberry Pi via "
            "`sudo apt install -y python3-picamera2`."
        )
    try:
        threading.Event().wait()
    finally:
        handle.close()


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
