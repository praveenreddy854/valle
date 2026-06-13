"""Persistent history of agent mission runs.

One JSON object per line, newest appended last. Every run path (CLI,
brain API, scheduler) records here so `GET /runs` can answer "what did
the robot do and what did it find".
"""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any


def new_run_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)


class RunStore:
    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls) -> "RunStore":
        return cls(os.getenv("VALLE_RUNS_FILE", "logs/agent-runs.jsonl"))

    def record(self, record: dict[str, Any]) -> None:
        line = json.dumps(record, sort_keys=True)
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        return list(reversed(self._read()))[: max(1, limit)]

    def get(self, run_id: str) -> dict[str, Any] | None:
        for record in self._read():
            if record.get("run_id") == run_id:
                return record
        return None

    def _read(self) -> list[dict[str, Any]]:
        with self._lock:
            if not self._path.exists():
                return []
            lines = self._path.read_text(encoding="utf-8").splitlines()
        records = []
        for line in lines:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                records.append(record)
        return records
