from __future__ import annotations

import logging
from typing import Any

from flask import Flask, jsonify, request

from .agent.config import AgentConfig, normalize_mission
from .agent.runner import _result_to_jsonable, run_agent


log = logging.getLogger("valle.brain.api")


def create_app() -> Flask:
    app = Flask(__name__)
    services = BrainApiServices()

    @app.get("/health")
    def health() -> Any:
        return jsonify({"ok": True, "service": "valle.brain.api"})

    @app.post("/agent/run")
    def agent_run() -> Any:
        body = _json_body()
        try:
            mission = normalize_mission(body)
            result = run_agent(AgentConfig.from_env(), mission)
        except ValueError as exc:
            return _error(str(exc), status=400)
        except RuntimeError as exc:
            return _error(str(exc), status=503)
        except Exception as exc:
            log.exception("agent mission failed")
            return _error(f"agent mission failed: {exc}", status=500)
        return jsonify({"ok": True, "result": _result_to_jsonable(result)})

    @app.post("/find")
    def find() -> Any:
        body = _json_body()
        object_query = body.get("object") or body.get("query")
        if not isinstance(object_query, str) or not object_query.strip():
            return _error("object is required", status=400)
        try:
            result = services.find(object_query.strip())
        except RuntimeError as exc:
            return _error(str(exc), status=503)
        except Exception as exc:
            log.exception("find failed")
            return _error(f"find failed: {exc}", status=500)
        return jsonify({"ok": True, **result})

    @app.post("/seek")
    def seek() -> Any:
        body = _json_body()
        object_query = body.get("object") or body.get("query")
        if not isinstance(object_query, str) or not object_query.strip():
            return _error("object is required", status=400)
        try:
            max_seconds = _optional_float(body, "max_seconds")
            speed = _optional_float(body, "speed")
            result = services.seek(
                object_query.strip(), max_seconds=max_seconds, speed=speed
            )
        except ValueError as exc:
            return _error(str(exc), status=400)
        except RuntimeError as exc:
            return _error(str(exc), status=503)
        except Exception as exc:
            log.exception("seek failed")
            return _error(f"seek failed: {exc}", status=500)
        return jsonify({"ok": True, **result})

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
                    "Run `make install-brain`."
                ) from exc
            self._find_server = build_server()
            self._find_server.load()
        return self._find_server


def main() -> None:
    from .api_config import BrainApiConfig

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = BrainApiConfig.from_env()
    create_app().run(host=config.host, port=config.port)


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


def _error(message: str, *, status: int) -> tuple[Any, int]:
    return jsonify({"ok": False, "error": message}), status


if __name__ == "__main__":
    main()
