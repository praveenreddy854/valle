from __future__ import annotations

from typing import Any


class AgentPiClient:
    def __init__(self, base_url: str, *, timeout_seconds: float = 10.0) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout_seconds

    @property
    def base_url(self) -> str:
        return self._base

    def validate_agent_api(self) -> None:
        response = _requests().post(
            self._base + "/agent/start",
            json={},
            timeout=self._timeout,
        )
        if response.status_code == 400:
            return
        if response.status_code == 404:
            raise AgentPiClientError(
                "Pi agent API is not available at "
                f"{self._base}/agent/start. Check VALLE_PI_BASE_URL points to "
                "the Pi motor server, not the brain API, and restart the Pi "
                "with the updated valle.app."
            )
        if response.status_code >= 500:
            raise AgentPiClientError(
                f"Pi agent API check failed with {response.status_code}: "
                f"{response.text}"
            )

    def start(self, mission: dict[str, Any]) -> dict[str, Any]:
        return self._post("/agent/start", mission)

    def observe(self, session_id: str) -> dict[str, Any]:
        return self._post(f"/agent/{session_id}/observe", {})

    def post_reflex(
        self, *, left: float, center: float, right: float, source: str
    ) -> dict[str, Any]:
        return self._post(
            "/agent/reflex",
            {"left": left, "center": center, "right": right, "source": source},
        )

    def drive_pulse(
        self,
        session_id: str,
        *,
        direction: str,
        duration: float,
        speed: float,
        reason: str,
    ) -> dict[str, Any]:
        return self._post(
            f"/agent/{session_id}/intent",
            {
                "type": "drive_pulse",
                "direction": direction,
                "duration": duration,
                "speed": speed,
                "reason": reason,
            },
        )

    def stop(self, session_id: str, *, reason: str) -> dict[str, Any]:
        return self._post(
            f"/agent/{session_id}/intent",
            {"type": "stop", "reason": reason},
        )

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = _requests().post(
            self._base + path, json=payload, timeout=self._timeout
        )
        try:
            body = response.json()
        except ValueError:
            body = {"raw": response.text}
        if response.status_code >= 400:
            raise AgentPiClientError(
                f"{path} failed with {response.status_code}: {body}"
            )
        return body


class AgentPiClientError(RuntimeError):
    pass


def _requests() -> Any:
    try:
        import requests
    except ImportError as exc:  # pragma: no cover - import guard for missing extra
        raise RuntimeError(
            "requests is required for valle.brain.agent. Run `make install-agent` "
            "or `.venv/bin/pip install -e '.[agent]'`."
        ) from exc
    return requests
