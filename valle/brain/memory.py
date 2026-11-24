"""Lessons Valle learns across runs, plus recall of recent run outcomes."""
from __future__ import annotations

import json
import os
import secrets
import threading
from datetime import datetime
from pathlib import Path
from typing import Any


class LessonStore:
    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls) -> "LessonStore":
        return cls(os.getenv("VALLE_LESSONS_FILE", "logs/agent-lessons.jsonl"))

    def add(self, lesson: str, *, skill: str = "", target: str = "", run_id: str = "") -> dict[str, Any]:
        record = {
            "id": secrets.token_hex(4),
            "lesson": lesson.strip(),
            "skill": skill,
            "target": target,
            "run_id": run_id,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True) + "\n")
        return record

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            if not self._path.exists():
                return []
            lines = self._path.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines if line.strip()]

    def remove(self, lesson_id: str) -> bool:
        with self._lock:
            if not self._path.exists():
                return False
            lines = self._path.read_text(encoding="utf-8").splitlines()
            kept = [l for l in lines if l.strip() and json.loads(l).get("id") != lesson_id]
            if len(kept) == len([l for l in lines if l.strip()]):
                return False
            self._path.write_text("".join(l + "\n" for l in kept), encoding="utf-8")
        return True

    def relevant(self, mission: dict[str, Any], limit: int = 20) -> list[dict[str, Any]]:
        skill = str(mission.get("skill", ""))
        targets = set(mission.get("targets") or [])
        hits = [
            l for l in self.list()
            if not l["skill"] or l["skill"] == skill or (l["target"] and l["target"] in targets)
        ]
        return hits[-limit:]


def memory_prompt(lessons: list[dict[str, Any]], past_runs: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    if lessons:
        lines.append("Lessons learned on previous runs:")
        lines += [f"- {l['lesson']}" for l in lessons]
    if past_runs:
        lines.append("Recent runs of this mission (newest first):")
        for run in past_runs:
            result = run.get("result") or {}
            lines.append(
                f"- {run.get('started_at')}: {run.get('status')}, state="
                f"{result.get('state', 'none')}, {result.get('summary', run.get('error') or '')}"
            )
    return "\n".join(lines)


def similar_runs(runs: list[dict[str, Any]], mission: dict[str, Any], limit: int = 3) -> list[dict[str, Any]]:
    key = mission.get("skill") or mission.get("goal")
    return [
        r for r in runs
        if ((r.get("mission") or {}).get("skill") or (r.get("mission") or {}).get("goal")) == key
    ][:limit]
