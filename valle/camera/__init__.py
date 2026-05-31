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
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Condition


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


def serve(config: CameraConfig | None = None) -> None:
    config = config or CameraConfig.from_env()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("valle.camera")

    try:
        from picamera2 import Picamera2
        from picamera2.encoders import MJPEGEncoder
        from picamera2.outputs import FileOutput
    except ImportError as exc:
        raise RuntimeError(
            "picamera2 is not installed. Install it on the Raspberry Pi via "
            "`sudo apt install -y python3-picamera2`."
        ) from exc

    picam2 = Picamera2()
    video_config = picam2.create_video_configuration(
        main={"size": (config.width, config.height)},
        controls={"FrameRate": config.framerate},
    )
    picam2.configure(video_config)

    output = _StreamingOutput()
    picam2.start_recording(MJPEGEncoder(), FileOutput(output))
    log.info(
        "valle camera streaming on http://%s:%d/stream.mjpg (%dx%d @ %d fps)",
        config.host,
        config.port,
        config.width,
        config.height,
        config.framerate,
    )
    server = _ThreadedHTTPServer((config.host, config.port), _make_handler(output))
    try:
        server.serve_forever()
    finally:
        server.server_close()
        picam2.stop_recording()


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
