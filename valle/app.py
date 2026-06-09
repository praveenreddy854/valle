from __future__ import annotations

import atexit
import signal
from collections.abc import Callable
from typing import Any

from flask import Flask, jsonify, request

from .bridge import (
    BrainBridge,
    BrainOfflineError,
    BrainResponseError,
    BrainTimeoutError,
)
from .config import ValleConfig
from .controller import (
    SessionAlreadyActiveError,
    SessionNotActiveError,
    ValleController,
)
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
            autopilot_max_seconds=config.autopilot_max_seconds,
            autopilot_idle_seconds=config.autopilot_idle_seconds,
        )

    app = Flask(__name__)
    app.config["VALLE_CONTROLLER"] = controller
    bridge = BrainBridge()
    app.config["VALLE_BRAIN_BRIDGE"] = bridge
    _register_brain_bridge(app, bridge, config)

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

    @app.post("/autopilot/start")
    def autopilot_start() -> Any:
        try:
            max_seconds = _optional_float("max_seconds")
            idle_seconds = _optional_float("idle_seconds")
            result = controller.start_autopilot(
                max_seconds=max_seconds, idle_seconds=idle_seconds
            )
        except SessionAlreadyActiveError as exc:
            return _error(
                "autopilot session already active",
                status=409,
                extra={"session_id": exc.session_id},
            )
        except ValueError as exc:
            return _error(str(exc), status=400)
        return jsonify(result), 201

    @app.post("/autopilot/<session_id>/drive")
    def autopilot_drive(session_id: str) -> Any:
        direction = _request_value("direction")
        if not direction:
            return _error("Missing direction.", status=400)
        try:
            speed = _optional_float("speed")
            duration = _optional_float("duration")
            result = controller.autopilot_drive(
                session_id,
                direction=direction,
                duration_seconds=duration,
                speed_percent=speed,
            )
        except SessionNotActiveError:
            return _error("session not active", status=409)
        except ValueError as exc:
            return _error(str(exc), status=400)
        return jsonify({"ok": True, **result})

    @app.post("/autopilot/<session_id>/stop")
    def autopilot_stop(session_id: str) -> Any:
        reason = _request_value("reason") or "manual"
        try:
            controller.stop_autopilot(session_id, reason=str(reason))
        except SessionNotActiveError:
            return _error("session not active", status=409)
        return jsonify({"ok": True, "ended_reason": "autopilot_" + str(reason)})

    @app.post("/agent/start")
    def agent_start() -> Any:
        try:
            mission = _agent_mission()
            max_seconds = _optional_float("max_seconds")
            idle_seconds = _optional_float("idle_seconds")
            result = controller.start_agent_session(
                mission=mission,
                max_seconds=max_seconds, idle_seconds=idle_seconds
            )
        except SessionAlreadyActiveError as exc:
            return _error(
                "session already active",
                status=409,
                extra={"session_id": exc.session_id},
            )
        except ValueError as exc:
            return _error(str(exc), status=400)
        return jsonify(result), 201

    @app.post("/agent/reflex")
    def agent_reflex() -> Any:
        try:
            left = _required_float("left")
            center = _required_float("center")
            right = _required_float("right")
        except ValueError as exc:
            return _error(str(exc), status=400)
        source = str(_request_value("source") or "unknown")
        return jsonify(
            controller.update_reflex(
                left=left, center=center, right=right, source=source
            )
        )

    @app.post("/agent/<session_id>/intent")
    def agent_intent(session_id: str) -> Any:
        intent_type = str(_request_value("type") or "drive_pulse")
        reason = str(_request_value("reason") or "")
        if intent_type == "stop":
            try:
                result = controller.stop_agent_session(
                    session_id, reason=reason or "manual"
                )
            except SessionNotActiveError:
                return _error("session not active", status=409)
            return jsonify({"ok": True, "ended_reason": result["last_stop_reason"]})
        if intent_type != "drive_pulse":
            return _error("intent type must be drive_pulse or stop", status=400)

        direction = _request_value("direction")
        if not direction:
            return _error("Missing direction.", status=400)
        try:
            speed = _optional_float("speed")
            duration = _optional_float("duration")
            result = controller.agent_intent(
                session_id,
                direction=str(direction),
                duration_seconds=duration,
                speed_percent=speed,
                reason=reason,
            )
        except SessionNotActiveError:
            return _error("session not active", status=409)
        except ValueError as exc:
            return _error(str(exc), status=400)
        return jsonify({"ok": True, **result})

    @app.post("/agent/<session_id>/observe")
    def agent_observe(session_id: str) -> Any:
        try:
            result = controller.agent_observe(session_id)
        except SessionNotActiveError:
            return _error("session not active", status=409)
        return jsonify({"ok": True, **result})

    @app.get("/find")
    def find() -> Any:
        object_query = _request_value("object")
        if not object_query:
            return _error("Missing object. Use /find?object=toy.", status=400)
        try:
            result = bridge.find(
                object_query=str(object_query),
                timeout_seconds=config.find_timeout_seconds,
            )
        except BrainOfflineError:
            return _error("brain offline", status=503)
        except BrainTimeoutError:
            return _error("brain timed out", status=504)
        except BrainResponseError as exc:
            return _error(str(exc), status=502)
        return jsonify(result)

    @app.get("/seek")
    def seek() -> Any:
        object_query = _request_value("object")
        if not object_query:
            return _error("Missing object. Use /seek?object=toy.", status=400)
        try:
            max_seconds_raw = _optional_float("max_seconds")
            speed = _optional_float("speed")
        except ValueError as exc:
            return _error(str(exc), status=400)
        max_seconds = min(
            max_seconds_raw if max_seconds_raw is not None else config.seek_max_seconds,
            config.seek_max_seconds,
        )
        timeout = max_seconds + config.seek_timeout_buffer_seconds
        try:
            result = bridge.seek(
                object_query=str(object_query),
                max_seconds=max_seconds,
                timeout_seconds=timeout,
                speed=speed,
            )
        except BrainOfflineError:
            return _error("brain offline", status=503)
        except BrainTimeoutError:
            return _error("brain timed out", status=504)
        except BrainResponseError as exc:
            return _error(str(exc), status=502)
        return jsonify(result)

    return app


def _register_brain_bridge(app: Flask, bridge: BrainBridge, config: ValleConfig) -> None:
    try:
        from flask_sock import Sock
    except ImportError:
        app.logger.warning(
            "flask-sock is not installed; /brain/find WebSocket route disabled "
            "and /find will always return 503. Install with: "
            "pip install -r requirements.txt"
        )
        return

    sock = Sock(app)

    @sock.route("/brain/find")
    def brain_find(ws: Any) -> None:
        bridge.attach(ws)
        try:
            while True:
                message = ws.receive()
                if message is None:
                    break
                bridge.handle_response(message)
        finally:
            bridge.detach(ws)


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
    except SessionAlreadyActiveError as exc:
        return _error(
            "autopilot session active",
            status=409,
            extra={"session_id": exc.session_id},
        )
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


def _required_float(name: str) -> float:
    raw = _request_value(name)
    if raw in (None, ""):
        raise ValueError(f"{name} is required")
    try:
        return float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc


def _agent_mission() -> dict[str, Any]:
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise ValueError("agent mission is required")

    nested = body.get("mission")
    if isinstance(nested, dict):
        return dict(nested)

    return {
        key: value
        for key, value in body.items()
        if key not in {"max_seconds", "idle_seconds"}
    }


def _request_value(name: str) -> Any:
    if name in request.values:
        return request.values.get(name)

    body = request.get_json(silent=True)
    if isinstance(body, dict):
        return body.get(name)
    return None


def _error(
    message: str, *, status: int, extra: dict[str, Any] | None = None
) -> tuple[Any, int]:
    payload: dict[str, Any] = {"ok": False, "error": message}
    if extra:
        payload.update(extra)
    return jsonify(payload), status


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
