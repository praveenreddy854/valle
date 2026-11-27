from __future__ import annotations

import logging
import threading
from typing import Any

from flask import Flask, jsonify, request, send_from_directory

from .agent.config import AgentConfig, normalize_mission
from .agent.runner import _result_to_jsonable, run_agent
from .evidence import evidence_root
from .household import HouseholdStore
from .memory import LessonStore
from .skills import SKILLS
from .loop import AutopilotController
from .portal import create_portal_blueprint
from .runs import RunStore
from .scheduler import MissionScheduler
from ..observability import configure_logging, configure_tracing, get_tracer
from ..telemetry import register_host_gauges


log = logging.getLogger("valle.brain.api")


def create_app(
    scheduler: MissionScheduler | None = None,
    autopilot: AutopilotController | None = None,
) -> Flask:
    app = Flask(__name__)
    app.register_blueprint(create_portal_blueprint())
    services = BrainApiServices()
    runs = RunStore.from_env()
    autopilot = autopilot or AutopilotController()
    # The Pi grants one motor session at a time, so foreground tasks (seek,
    # agent missions) run one-at-a-time and pause autopilot while they hold it.
    task_lock = threading.Lock()

    def borrow_motor_session(run: Any) -> Any:
        with task_lock, autopilot.paused():
            return run()

    def run_scheduled_mission(mission: dict[str, Any]) -> None:
        with get_tracer(__name__).start_as_current_span(
            "brain.scheduled_mission"
        ) as span:
            span.set_attribute("mission.goal", str(mission.get("goal", "")))
            borrow_motor_session(lambda: run_agent(AgentConfig.from_env(), mission))

    if scheduler is None:
        scheduler = MissionScheduler.from_env(run_scheduled_mission)
    app.config["VALLE_SCHEDULER"] = scheduler
    app.config["VALLE_AUTOPILOT"] = autopilot

    @app.get("/health")
    def health() -> Any:
        return jsonify({"ok": True, "service": "valle.brain.api"})

    @app.post("/autopilot/start")
    def autopilot_start() -> Any:
        return jsonify({"ok": True, **autopilot.start()})

    @app.post("/autopilot/stop")
    def autopilot_stop() -> Any:
        return jsonify({"ok": True, **autopilot.stop()})

    @app.get("/autopilot/status")
    def autopilot_status() -> Any:
        return jsonify({"ok": True, **autopilot.status()})

    @app.post("/agent/run")
    def agent_run() -> Any:
        body = _json_body()
        with get_tracer(__name__).start_as_current_span("brain.agent_run") as span:
            try:
                mission = normalize_mission(body)
                span.set_attribute("mission.goal", mission["goal"])
                result = borrow_motor_session(
                    lambda: run_agent(AgentConfig.from_env(), mission)
                )
            except ValueError as exc:
                return _error(str(exc), status=400)
            except RuntimeError as exc:
                return _error(str(exc), status=503)
            except Exception as exc:
                log.exception("agent mission failed")
                return _error(f"agent mission failed: {exc}", status=500)
            payload = _result_to_jsonable(result)
            if isinstance(payload, dict):
                _set_result_attributes(span, payload, ("state", "status", "run_id"))
            return jsonify({"ok": True, "result": payload})

    @app.post("/find")
    def find() -> Any:
        body = _json_body()
        object_query = body.get("object") or body.get("query")
        if not isinstance(object_query, str) or not object_query.strip():
            return _error("object is required", status=400)
        with get_tracer(__name__).start_as_current_span("brain.find") as span:
            span.set_attribute("object.query", object_query.strip())
            try:
                result = services.find(object_query.strip())
            except RuntimeError as exc:
                return _error(str(exc), status=503)
            except Exception as exc:
                log.exception("find failed")
                return _error(f"find failed: {exc}", status=500)
            _set_result_attributes(span, result, ("found", "capture_seconds"))
            span.set_attribute("find.results", len(result.get("results") or []))
            if result.get("error"):
                return _error(str(result["error"]), status=503)
            return jsonify({"ok": True, **result})

    @app.post("/seek")
    def seek() -> Any:
        body = _json_body()
        object_query = body.get("object") or body.get("query")
        if not isinstance(object_query, str) or not object_query.strip():
            return _error("object is required", status=400)
        with get_tracer(__name__).start_as_current_span("brain.seek") as span:
            span.set_attribute("object.query", object_query.strip())
            try:
                max_seconds = _optional_float(body, "max_seconds")
                speed = _optional_float(body, "speed")
                if max_seconds is not None:
                    span.set_attribute("seek.max_seconds", max_seconds)
                result = borrow_motor_session(
                    lambda: services.seek(
                        object_query.strip(), max_seconds=max_seconds, speed=speed
                    )
                )
            except ValueError as exc:
                return _error(str(exc), status=400)
            except RuntimeError as exc:
                return _error(str(exc), status=503)
            except Exception as exc:
                log.exception("seek failed")
                return _error(f"seek failed: {exc}", status=500)
            _set_result_attributes(
                span, result, ("found", "reason", "ticks", "elapsed_seconds", "score")
            )
            if result.get("error"):
                return _error(str(result["error"]), status=503)
            return jsonify({"ok": True, **result})

    @app.get("/runs")
    def list_runs() -> Any:
        try:
            limit = int(request.args.get("limit", "50"))
        except ValueError:
            return _error("limit must be an integer", status=400)
        return jsonify({"ok": True, "runs": runs.list(limit=limit)})

    @app.get("/runs/<run_id>")
    def get_run(run_id: str) -> Any:
        record = runs.get(run_id)
        if record is None:
            return _error("run not found", status=404)
        return jsonify({"ok": True, "run": record})

    @app.get("/evidence/<run_id>/<path:filename>")
    def get_evidence(run_id: str, filename: str) -> Any:
        root = evidence_root().resolve()
        directory = (root / run_id).resolve()
        if directory != root and root not in directory.parents:
            return _error("run not found", status=404)
        return send_from_directory(directory, filename)

    @app.get("/household")
    def get_household() -> Any:
        return jsonify({"ok": True, "household": HouseholdStore.from_env().get()})

    @app.put("/household")
    def put_household() -> Any:
        try:
            profile = HouseholdStore.from_env().put(_json_body())
        except ValueError as exc:
            return _error(str(exc), status=400)
        return jsonify({"ok": True, "household": profile})

    @app.get("/skills")
    def list_skills() -> Any:
        return jsonify({"ok": True, "skills": SKILLS})

    @app.get("/memory")
    def list_memory() -> Any:
        return jsonify({"ok": True, "lessons": LessonStore.from_env().list()})

    @app.delete("/memory/<lesson_id>")
    def forget_lesson(lesson_id: str) -> Any:
        if not LessonStore.from_env().remove(lesson_id):
            return _error("lesson not found", status=404)
        return jsonify({"ok": True, "removed": lesson_id})

    @app.get("/map")
    def room_map() -> Any:
        from .room_map import RoomMapStore

        return jsonify({"ok": True, "map": RoomMapStore.from_env().get()})

    @app.delete("/map")
    def reset_room_map() -> Any:
        from .room_map import RoomMapStore

        RoomMapStore.from_env().reset()
        return jsonify({"ok": True, "reset": True})

    @app.get("/findings")
    def list_findings() -> Any:
        findings = [
            {**f, "run_id": r.get("run_id"), "at": r.get("started_at")}
            for r in runs.list(limit=50)
            for f in r.get("findings") or []
        ]
        return jsonify({"ok": True, "findings": findings})

    @app.get("/missions")
    def list_missions() -> Any:
        return jsonify({"ok": True, "missions": scheduler.list()})

    @app.post("/missions")
    def add_mission() -> Any:
        body = _json_body()
        schedule = body.pop("schedule", None)
        if not isinstance(schedule, str):
            return _error("schedule is required (HH:MM)", status=400)
        try:
            entry = scheduler.add(schedule, body)
        except ValueError as exc:
            return _error(str(exc), status=400)
        return jsonify({"ok": True, "mission": entry}), 201

    @app.patch("/missions/<mission_id>")
    def update_mission(mission_id: str) -> Any:
        try:
            entry = scheduler.update(mission_id, _json_body())
        except ValueError as exc:
            return _error(str(exc), status=400)
        if entry is None:
            return _error("mission not found", status=404)
        return jsonify({"ok": True, "mission": entry})

    @app.get("/portal/home")
    def home_page() -> Any:
        from .home_page import HOME_HTML

        return HOME_HTML

    @app.delete("/missions/<mission_id>")
    def remove_mission(mission_id: str) -> Any:
        if not scheduler.remove(mission_id):
            return _error("mission not found", status=404)
        return jsonify({"ok": True, "removed": mission_id})

    return app


class BrainApiServices:
    def __init__(self) -> None:
        self._find_server: Any = None

    def find(self, object_query: str) -> dict[str, Any]:
        server = self._server()
        return server.handle_find_request(object_query)

    def seek(
        self,
        object_query: str,
        *,
        max_seconds: float | None,
        speed: float | None,
    ) -> dict[str, Any]:
        server = self._server()
        return server.handle_seek_request(
            object_query,
            max_seconds=max_seconds,
            speed=speed,
        )

    def _server(self) -> Any:
        if self._find_server is None:
            try:
                from .find.server import build_server
            except ImportError as exc:
                raise RuntimeError(
                    "brain find/seek dependencies are missing. "
                    "Run `make install-brain-api`."
                ) from exc
            self._find_server = build_server()
            self._find_server.load()
        return self._find_server


def main() -> None:
    from .agent.config import load_dotenv
    from .api_config import BrainApiConfig

    # Load .env before any service reads its settings (seek, find, autopilot).
    load_dotenv()
    config = BrainApiConfig.from_env()
    configure_logging("valle-brain-api")
    app = create_app()
    configure_tracing("valle-brain-api", flask_app=app)
    register_host_gauges("valle-brain-api")
    scheduler = app.config["VALLE_SCHEDULER"]
    autopilot = app.config["VALLE_AUTOPILOT"]
    scheduler.start()
    try:
        app.run(host=config.host, port=config.port)
    finally:
        scheduler.stop()
        autopilot.stop()


def _json_body() -> dict[str, Any]:
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else {}


def _optional_float(body: dict[str, Any], key: str) -> float | None:
    value = body.get(key)
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be a number") from exc


def _set_result_attributes(span: Any, result: dict[str, Any], keys: tuple[str, ...]) -> None:
    for key in keys:
        value = result.get(key)
        if isinstance(value, (bool, int, float, str)):
            span.set_attribute(f"result.{key}", value)
    if result.get("error"):
        span.set_attribute("result.error", str(result["error"]))


def _error(message: str, *, status: int) -> tuple[Any, int]:
    return jsonify({"ok": False, "error": message}), status


if __name__ == "__main__":
    main()
