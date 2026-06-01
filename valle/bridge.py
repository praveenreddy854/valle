"""Pi-side bridge to the off-device brain over a single WebSocket.

The brain dials in and stays connected. The Pi sends find requests over
the socket and waits for the matching response by ``id``. There is at
most one brain connected at a time - a later connect replaces the
older one.
"""
from __future__ import annotations

import json
import queue
import secrets
import threading
from dataclasses import dataclass
from typing import Any


@dataclass
class _Pending:
    response: queue.Queue[dict[str, Any]]


class BrainBridge:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._ws: Any = None
        self._pending: dict[str, _Pending] = {}

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._ws is not None

    def attach(self, ws: Any) -> None:
        with self._lock:
            previous = self._ws
            self._ws = ws
        if previous is not None:
            try:
                previous.close()
            except Exception:
                pass

    def detach(self, ws: Any) -> None:
        with self._lock:
            if self._ws is ws:
                self._ws = None
            for pending in self._pending.values():
                pending.response.put({"error": "brain disconnected"})
            self._pending.clear()

    def handle_response(self, raw: str) -> None:
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            return
        message_id = message.get("id")
        if not isinstance(message_id, str):
            return
        with self._lock:
            pending = self._pending.pop(message_id, None)
        if pending is not None:
            pending.response.put(message)

    def find(self, *, object_query: str, timeout_seconds: float) -> dict[str, Any]:
        return self.request(
            request_type="find",
            payload={"object": object_query},
            timeout_seconds=timeout_seconds,
        )

    def seek(
        self,
        *,
        object_query: str,
        max_seconds: float,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        return self.request(
            request_type="seek",
            payload={"object": object_query, "max_seconds": max_seconds},
            timeout_seconds=timeout_seconds,
        )

    def request(
        self,
        *,
        request_type: str,
        payload: dict[str, Any],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        with self._lock:
            ws = self._ws
            if ws is None:
                raise BrainOfflineError()
            message_id = secrets.token_hex(8)
            pending = _Pending(response=queue.Queue(maxsize=1))
            self._pending[message_id] = pending

        body = {"id": message_id, "type": request_type, **payload}
        try:
            ws.send(json.dumps(body))
        except Exception as exc:
            with self._lock:
                self._pending.pop(message_id, None)
            raise BrainOfflineError() from exc

        try:
            response = pending.response.get(timeout=timeout_seconds)
        except queue.Empty:
            with self._lock:
                self._pending.pop(message_id, None)
            raise BrainTimeoutError()

        if "error" in response:
            raise BrainResponseError(response["error"])
        return response


class BrainOfflineError(RuntimeError):
    pass


class BrainTimeoutError(RuntimeError):
    pass


class BrainResponseError(RuntimeError):
    pass
