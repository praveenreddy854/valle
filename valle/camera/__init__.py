"""On-demand MJPEG camera streamer for Valle.

The MJPEG server listens all the time, but the camera itself records only
while something needs it:

- a client is connected to ``/stream.mjpg`` (brain seek/autopilot/agent, or
  the portal, which asks for a capped view with ``?max_seconds=60``), or
- the robot is driving, and for ``VALLE_CAMERA_IDLE_SECONDS`` after it stops.

The server runs on its own port and threads so streaming clients never block
the motor API.
"""
from __future__ import annotations

import io
import logging
import os
import socketserver
import threading
import time
from dataclasses import dataclass
from typing import Callable
from urllib.parse import parse_qs, urlsplit
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
    # Keep recording this long after the last client leaves or driving stops.
    idle_seconds: float = 60.0

    @classmethod
    def from_env(cls) -> "CameraConfig":
        return cls(
            host=os.getenv("VALLE_CAMERA_HOST", cls.host),
            port=_env_int("VALLE_CAMERA_PORT", cls.port),
            width=_env_int("VALLE_CAMERA_WIDTH", cls.width),
            height=_env_int("VALLE_CAMERA_HEIGHT", cls.height),
            framerate=_env_int("VALLE_CAMERA_FPS", cls.framerate),
            idle_seconds=_env_float("VALLE_CAMERA_IDLE_SECONDS", cls.idle_seconds),
        )


class _StreamingOutput(io.BufferedIOBase):
    def __init__(self) -> None:
        self.frame: bytes | None = None
        self.sequence = 0
        self.condition = Condition()

    def write(self, buf: bytes) -> int:  # type: ignore[override]
        with self.condition:
            self.frame = bytes(buf)
            self.sequence += 1
            self.condition.notify_all()
        telemetry.record_camera_frame()
        return len(buf)

    def clear(self) -> None:
        with self.condition:
            self.frame = None
            self.condition.notify_all()


class CameraPower:
    """Starts and stops recording based on stream clients and driving activity.

    ``start``/``stop`` are the picamera2 calls; they are injected so the
    on/off logic can be tested without a camera.
    """

    def __init__(
        self,
        *,
        start: Callable[[], None],
        stop: Callable[[], None],
        idle_seconds: float,
        is_driving: Callable[[], bool] = lambda: False,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._start = start
        self._stop = stop
        self._idle_seconds = idle_seconds
        self._is_driving = is_driving
        self._clock = clock
        self._lock = threading.Lock()
        self._clients = 0
        self._recording = False
        self._last_needed = clock()

    @property
    def recording(self) -> bool:
        return self._recording

    @property
    def clients(self) -> int:
        return self._clients

    def acquire(self) -> None:
        with self._lock:
            self._clients += 1
            self._last_needed = self._clock()
            self._ensure_on_locked("stream client")

    def release(self) -> None:
        with self._lock:
            self._clients = max(0, self._clients - 1)
            self._last_needed = self._clock()

    def tick(self) -> None:
        """Called periodically: turn on for driving, off after the idle delay."""
        driving = self._is_driving()
        with self._lock:
            now = self._clock()
            if driving or self._clients:
                self._last_needed = now
                if driving:
                    self._ensure_on_locked("driving")
                return
            if self._recording and now - self._last_needed >= self._idle_seconds:
                log.info("camera idle for %.0fs; turning off", now - self._last_needed)
                self._recording = False
                self._stop()

    def close(self) -> None:
        with self._lock:
            if self._recording:
                self._recording = False
                self._stop()

    def _ensure_on_locked(self, reason: str) -> None:
        if self._recording:
            return
        log.info("camera turning on (%s)", reason)
        self._start()
        self._recording = True


def _make_handler(
    output: _StreamingOutput, power: CameraPower
) -> type[BaseHTTPRequestHandler]:
    class StreamingHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            logging.getLogger("valle.camera").debug("%s - %s", self.address_string(), format % args)

        def do_GET(self) -> None:  # noqa: N802
            url = urlsplit(self.path)
            if url.path in ("/", "/stream", "/stream.mjpg"):
                self._stream(_max_seconds(url.query))
            elif url.path == "/health":
                body = (
                    '{"ok":true,"recording":%s,"clients":%d}'
                    % ("true" if power.recording else "false", power.clients)
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_error(404)

        def _stream(self, max_seconds: float | None) -> None:
            power.acquire()
            try:
                self.send_response(200)
                self.send_header("Cache-Control", "no-cache, private")
                self.send_header("Pragma", "no-cache")
                self.send_header(
                    "Content-Type",
                    "multipart/x-mixed-replace; boundary=FRAME",
                )
                self.end_headers()
                deadline = time.monotonic() + max_seconds if max_seconds else None
                last_sequence = -1
                while deadline is None or time.monotonic() < deadline:
                    with output.condition:
                        output.condition.wait(timeout=1.0)
                        frame, sequence = output.frame, output.sequence
                    if frame is None or sequence == last_sequence:
                        continue
                    last_sequence = sequence
                    self.wfile.write(b"--FRAME\r\n")
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Content-Length", str(len(frame)))
                    self.end_headers()
                    self.wfile.write(frame)
                    self.wfile.write(b"\r\n")
            except (BrokenPipeError, ConnectionResetError):
                return
            finally:
                power.release()

    return StreamingHandler


def _max_seconds(query: str) -> float | None:
    raw = parse_qs(query).get("max_seconds", [""])[0]
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value > 0 else None


class _ThreadedHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class CameraHandle:
    """Owns the picamera2 recorder and MJPEG server so callers can shut down."""

    def __init__(
        self, server: _ThreadedHTTPServer, power: CameraPower, stop_event: threading.Event
    ) -> None:
        self._server = server
        self._power = power
        self._stop_event = stop_event

    @property
    def power(self) -> CameraPower:
        return self._power

    def close(self) -> None:
        self._stop_event.set()
        self._server.shutdown()
        self._server.server_close()
        self._power.close()


def start_camera_server(
    config: CameraConfig | None = None,
    *,
    is_driving: Callable[[], bool] = lambda: False,
) -> CameraHandle | None:
    """Start the MJPEG server; the camera records only while it is needed.

    Returns a :class:`CameraHandle`, or ``None`` when picamera2 is unavailable
    or no camera is connected, so the Pi app can still run the motor API.
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

    try:
        picam2 = Picamera2()
    except (RuntimeError, IndexError) as exc:
        log.warning("no camera detected (%s); camera stream disabled", exc)
        return None
    video_config = picam2.create_video_configuration(
        main={"size": (config.width, config.height)},
        controls={"FrameRate": config.framerate},
    )
    picam2.configure(video_config)

    output = _StreamingOutput()

    def start() -> None:
        picam2.start_recording(MJPEGEncoder(), FileOutput(output))

    def stop() -> None:
        picam2.stop_recording()
        output.clear()

    power = CameraPower(
        start=start, stop=stop, idle_seconds=config.idle_seconds, is_driving=is_driving
    )
    server = _ThreadedHTTPServer((config.host, config.port), _make_handler(output, power))
    threading.Thread(
        target=server.serve_forever, name="valle-camera", daemon=True
    ).start()

    stop_event = threading.Event()

    def watch() -> None:
        while not stop_event.wait(1.0):
            try:
                power.tick()
            except Exception:
                log.exception("camera power check failed")

    threading.Thread(target=watch, name="valle-camera-power", daemon=True).start()
    log.info(
        "valle camera server on http://%s:%d/stream.mjpg (%dx%d @ %d fps); "
        "recording on demand, off after %.0fs idle",
        config.host,
        config.port,
        config.width,
        config.height,
        config.framerate,
        config.idle_seconds,
    )
    return CameraHandle(server, power, stop_event)


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


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
