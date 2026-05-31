from __future__ import annotations

from typing import Any

import requests


class PiClientError(RuntimeError):
    pass


class SessionRejectedError(PiClientError):
    pass


class PiClient:
    def __init__(self, base_url: str, *, timeout_seconds: float = 1.0) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._session = requests.Session()

    def start(
        self,
        *,
        max_seconds: float | None = None,
        idle_seconds: float | None = None,
    ) -> dict[str, Any]:
        body: dict[str, float] = {}
        if max_seconds is not None:
            body["max_seconds"] = max_seconds
        if idle_seconds is not None:
            body["idle_seconds"] = idle_seconds
        response = self._session.post(
            f"{self._base}/autopilot/start", json=body, timeout=self._timeout
        )
        if response.status_code == 409:
            raise SessionRejectedError(response.text)
        response.raise_for_status()
        return response.json()

    def drive(
        self,
        session_id: str,
        *,
        direction: str,
        duration: float,
        speed: float,
    ) -> dict[str, Any]:
        body = {"direction": direction, "duration": duration, "speed": speed}
        response = self._session.post(
            f"{self._base}/autopilot/{session_id}/drive",
            json=body,
            timeout=self._timeout,
        )
        if response.status_code == 409:
            raise SessionRejectedError(response.text)
        response.raise_for_status()
        return response.json()

    def stop(self, session_id: str, *, reason: str = "manual") -> dict[str, Any]:
        response = self._session.post(
            f"{self._base}/autopilot/{session_id}/stop",
            json={"reason": reason},
            timeout=self._timeout,
        )
        if response.status_code == 409:
            raise SessionRejectedError(response.text)
        response.raise_for_status()
        return response.json()

    def panic_stop(self) -> None:
        try:
            self._session.post(f"{self._base}/stop", timeout=self._timeout)
        except requests.RequestException:
            pass
