"""Recurring mission scheduler for the brain API.

Missions are stored in a JSON file with a daily ``HH:MM`` schedule. A
background loop checks for due missions and runs them through the same
agent path as ``POST /agent/run``, so scheduled runs get run history,
evidence, and notifications for free. At most one run fires per mission
per day.
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .agent.config import normalize_mission


log = logging.getLogger("valle.brain.scheduler")

_SCHEDULE_PATTERN = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class MissionScheduler:
    def __init__(
        self,
        path: Path | str,
        run_mission: Callable[[dict[str, Any]], Any],
        *,
        poll_seconds: float = 20.0,
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        self._path = Path(path)
        self._run_mission = run_mission
        self._poll_seconds = poll_seconds
        self._now = now
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._entries: list[dict[str, Any]] = self._load()

    @classmethod
    def from_env(
        cls, run_mission: Callable[[dict[str, Any]], Any]
    ) -> "MissionScheduler":
        return cls(
            os.getenv("VALLE_MISSIONS_FILE", "missions.json"),
            run_mission,
            poll_seconds=float(os.getenv("VALLE_SCHEDULER_POLL_SECONDS", "20")),
        )

    def add(self, schedule: str, mission: dict[str, Any]) -> dict[str, Any]:
        if not _SCHEDULE_PATTERN.match(schedule):
            raise ValueError("schedule must be HH:MM (24-hour)")
        entry = {
            "id": secrets.token_hex(4),
            "schedule": schedule,
            "mission": normalize_mission(mission),
            "enabled": True,
            "last_run_date": None,
            "last_run_status": None,
        }
        with self._lock:
            self._entries.append(entry)
            self._save()
        return dict(entry)

    def remove(self, mission_id: str) -> bool:
        with self._lock:
            kept = [e for e in self._entries if e["id"] != mission_id]
            removed = len(kept) != len(self._entries)
            if removed:
                self._entries = kept
                self._save()
        return removed

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(entry) for entry in self._entries]

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="valle-mission-scheduler", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def check_due(self) -> list[str]:
        """Run every due mission; returns the ids that ran."""
        now = self._now()
        today = now.date().isoformat()
        current = now.strftime("%H:%M")
        ran: list[str] = []
        for entry in self.list():
            if not entry.get("enabled"):
                continue
            if entry.get("last_run_date") == today:
                continue
            if current < entry["schedule"]:
                continue
            ran.append(entry["id"])
            status = "completed"
            try:
                self._run_mission(entry["mission"])
            except Exception:
                status = "failed"
                log.exception(
                    "scheduled mission %s (%r) failed",
                    entry["id"],
                    entry["mission"].get("goal"),
                )
            self._mark_ran(entry["id"], today, status)
        return ran

    def _mark_ran(self, mission_id: str, date: str, status: str) -> None:
        with self._lock:
            for entry in self._entries:
                if entry["id"] == mission_id:
                    entry["last_run_date"] = date
                    entry["last_run_status"] = status
            self._save()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.check_due()
            except Exception:
                log.exception("scheduler poll failed")
            self._stop_event.wait(self._poll_seconds)

    def _load(self) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            log.exception("could not read missions file %s", self._path)
            return []
        return [entry for entry in data if isinstance(entry, dict)] if isinstance(data, list) else []

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(self._entries, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
