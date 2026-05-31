from __future__ import annotations

import atexit
import signal
from collections.abc import Callable
from typing import Any

from flask import Flask, jsonify, request

from .config import ValleConfig
from .controller import ValleController
from .motors import MotorDriver, create_motor_driver


SIMPLE_ROUTES = {
    "/forward": "forward",
    "/forwards": "forward",
    "/backward": "backward",
    "/backwards": "backward",
    "/reverse": "backward",
    "/left": "left",
    "/turn-left": "left",
    "/right": "right",
    "/turn-right": "right",
    "/stop": "stop",
}


def create_app(
    config: ValleConfig | None = None,
    *,
    driver: MotorDriver | None = None,
    controller: ValleController | None = None,
) -> Flask:
    config = config or ValleConfig.from_env()
    if controller is None:
        driver = driver or create_motor_driver(config)
        controller = ValleController(
            driver,
            default_speed_percent=config.default_speed_percent,
            default_duration_seconds=config.default_duration_seconds,
            max_duration_seconds=config.max_duration_seconds,
            default_turn_duration_seconds=config.turn_duration_seconds,
        )

    app = Flask(__name__)
    app.config["VALLE_CONTROLLER"] = controller

    @app.get("/health")
    def health() -> Any:
        return jsonify({"ok": True, "driver": controller.driver_name})

    @app.get("/status")
    def status() -> Any:
        return jsonify(controller.status())

    @app.route("/drive", methods=["GET", "POST"])
    def drive() -> Any:
        command = _request_value("command")
        if not command:
            return _error("Missing command. Use /drive?command=forward.", status=400)
        return _handle_command(controller, command)

    @app.route("/drive/<command>", methods=["GET", "POST"])
    def drive_command(command: str) -> Any:
        return _handle_command(controller, command)

    for route, command in SIMPLE_ROUTES.items():
        endpoint = "command_" + route.strip("/").replace("-", "_")
        app.add_url_rule(
            route,
            endpoint=endpoint,
            view_func=_make_simple_handler(controller, command),
            methods=["GET", "POST"],
        )

    return app


def main() -> None:
    config = ValleConfig.from_env()
    app = create_app(config)
    controller = app.config["VALLE_CONTROLLER"]
    _register_shutdown(controller)
    app.run(host=config.host, port=config.port)


def _make_simple_handler(
    controller: ValleController, command: str
) -> Callable[[], tuple[Any, int] | Any]:
    def handler() -> Any:
        return _handle_command(controller, command)

    return handler


def _handle_command(controller: ValleController, command: str) -> Any:
    try:
        speed = _optional_float("speed")
        duration = _optional_float("duration")
        result = controller.run(command, speed_percent=speed, duration_seconds=duration)
    except ValueError as exc:
        return _error(str(exc), status=400)
    return jsonify(result)


def _optional_float(name: str) -> float | None:
    raw = _request_value(name)
    if raw in (None, ""):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc


def _request_value(name: str) -> Any:
    if name in request.values:
        return request.values.get(name)

    body = request.get_json(silent=True)
    if isinstance(body, dict):
        return body.get(name)
    return None


def _error(message: str, *, status: int) -> tuple[Any, int]:
    return jsonify({"ok": False, "error": message}), status


def _register_shutdown(controller: ValleController) -> None:
    closed = False

    def close_once(*_: object) -> None:
        nonlocal closed
        if closed:
            return
        closed = True
        controller.close()

    atexit.register(close_once)

    for sig in (signal.SIGINT, signal.SIGTERM):
        previous_handler = signal.getsignal(sig)

        def handler(signum: int, frame: object, *, previous: Any = previous_handler) -> None:
            close_once()
            if callable(previous):
                previous(signum, frame)
            else:
                raise SystemExit(0)

        signal.signal(sig, handler)


if __name__ == "__main__":
    main()
