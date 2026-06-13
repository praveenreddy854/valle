"""Digital twin entry point.

One process that stands in for the whole robot:

- The real Pi control server (``valle.app``) on ``VALLE_PORT`` with a
  simulated motor driver, so sessions, reflex gating, and watchdogs run
  the production code paths.
- An MJPEG camera stream on ``VALLE_CAMERA_PORT`` rendering the robot's
  first-person view of the simulated room.
- Ground-truth endpoints under ``/sim/*`` for verification scripts.

Point the Mac-side brain at it with VALLE_PI_BASE_URL=http://127.0.0.1:8080
and VALLE_CAMERA_URL=http://127.0.0.1:8081/stream.mjpg.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

import cv2
from flask import Blueprint, jsonify, render_template_string, request

from .driver import SimMotorDriver
from .render import Renderer
from .ui import SIM_UI_HTML
from .world import SimWorld, build_default_world
from ..app import create_app
from ..camera import _make_handler, _StreamingOutput, _ThreadedHTTPServer
from ..config import ValleConfig
from ..observability import configure_logging, configure_tracing


log = logging.getLogger("valle.sim")


class SimLoop:
    """Steps the world physics and publishes rendered MJPEG frames."""

    def __init__(
        self,
        world: SimWorld,
        renderer: Renderer,
        output: _StreamingOutput,
        *,
        fps: float = 10.0,
    ) -> None:
        self._world = world
        self._renderer = renderer
        self._output = output
        self._interval = 1.0 / max(1.0, fps)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="valle-sim-loop", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _run(self) -> None:
        previous = time.monotonic()
        while not self._stop_event.is_set():
            now = time.monotonic()
            self._world.step(min(now - previous, 0.25))
            previous = now
            try:
                frame, _ = self._renderer.render()
                ok, jpeg = cv2.imencode(".jpg", frame)
                if ok:
                    self._output.write(jpeg.tobytes())
            except Exception:
                log.exception("sim render failed")
            remaining = self._interval - (time.monotonic() - now)
            if remaining > 0:
                self._stop_event.wait(remaining)


def create_sim_blueprint(world: SimWorld, *, camera_port: int = 8081) -> Blueprint:
    sim = Blueprint("sim", __name__)

    @sim.get("/sim/ui")
    def ui() -> Any:
        return render_template_string(SIM_UI_HTML, camera_port=camera_port)

    @sim.get("/sim/world")
    def world_geometry() -> Any:
        xs = [c for w in world.walls for c in (w.ax, w.bx)]
        ys = [c for w in world.walls for c in (w.ay, w.by)]
        return jsonify(
            {
                "ok": True,
                "bounds": {
                    "min_x": min(xs),
                    "max_x": max(xs),
                    "min_y": min(ys),
                    "max_y": max(ys),
                },
                "walls": [
                    {"ax": w.ax, "ay": w.ay, "bx": w.bx, "by": w.by, "kind": w.kind}
                    for w in world.walls
                ],
                "objects": [
                    {
                        "name": o.name,
                        "x": o.x,
                        "y": o.y,
                        "radius": o.radius,
                        "color": _bgr_to_hex(o.color),
                    }
                    for o in world.objects
                ],
            }
        )

    @sim.get("/sim/state")
    def state() -> Any:
        return jsonify({"ok": True, **world.state()})

    @sim.post("/sim/door")
    def door() -> Any:
        body = request.get_json(silent=True) or {}
        locked = body.get("locked")
        if not isinstance(locked, bool):
            return jsonify({"ok": False, "error": "locked must be a boolean"}), 400
        world.set_door_locked(locked)
        return jsonify({"ok": True, "door": {"locked": world.door_locked}})

    @sim.post("/sim/reset")
    def reset() -> Any:
        world.reset()
        return jsonify({"ok": True, **world.state()})

    return sim


def _bgr_to_hex(color: tuple[int, int, int]) -> str:
    blue, green, red = color
    return f"#{red:02x}{green:02x}{blue:02x}"


def main() -> None:
    configure_logging("valle-sim")
    config = ValleConfig.from_env()
    camera_port = int(os.getenv("VALLE_CAMERA_PORT", "8081"))
    width = int(os.getenv("VALLE_CAMERA_WIDTH", "640"))
    height = int(os.getenv("VALLE_CAMERA_HEIGHT", "480"))
    fps = float(os.getenv("VALLE_SIM_FPS", "10"))

    world = build_default_world()
    renderer = Renderer(world, width=width, height=height)
    output = _StreamingOutput()
    loop = SimLoop(world, renderer, output, fps=fps)
    loop.start()

    camera_server = _ThreadedHTTPServer(
        (config.host, camera_port), _make_handler(output)
    )
    threading.Thread(
        target=camera_server.serve_forever, name="valle-sim-camera", daemon=True
    ).start()
    log.info(
        "sim camera streaming on http://%s:%d/stream.mjpg", config.host, camera_port
    )

    app = create_app(config, driver=SimMotorDriver(world))
    app.register_blueprint(create_sim_blueprint(world, camera_port=camera_port))
    configure_tracing("valle-sim", flask_app=app)
    log.info("sim Pi API on http://%s:%d (driver=sim)", config.host, config.port)
    log.info("sim UI on http://127.0.0.1:%d/sim/ui", config.port)
    try:
        app.run(host=config.host, port=config.port)
    finally:
        camera_server.shutdown()
        loop.stop()


if __name__ == "__main__":
    main()
