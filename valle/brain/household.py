"""User-defined household profile: rules, named places, vacuum settings.

Rules guide the planner only; the Pi reflex gate stays the hard safety
boundary. Stored as a JSON object at ``VALLE_HOUSEHOLD_FILE``.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any


class HouseholdStore:
    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls) -> "HouseholdStore":
        return cls(os.getenv("VALLE_HOUSEHOLD_FILE", "household.json"))

    def get(self) -> dict[str, Any]:
        with self._lock:
            if not self._path.exists():
                return normalize_household({})
            return normalize_household(json.loads(self._path.read_text(encoding="utf-8")))

    def put(self, profile: dict[str, Any]) -> dict[str, Any]:
        normalized = normalize_household(profile)
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(normalized, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
        return normalized


def normalize_household(profile: Any) -> dict[str, Any]:
    if not isinstance(profile, dict):
        raise ValueError("household profile must be a JSON object")
    rules = profile.get("rules", [])
    if not isinstance(rules, list) or not all(isinstance(r, str) for r in rules):
        raise ValueError("rules must be a list of strings")
    places = profile.get("places", {})
    if not isinstance(places, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in places.items()
    ):
        raise ValueError("places must map place names to description strings")
    vacuum = profile.get("vacuum", {})
    if not isinstance(vacuum, dict):
        raise ValueError("vacuum must be an object")
    return {
        "rules": [r.strip() for r in rules if r.strip()],
        "places": {k.strip(): v.strip() for k, v in places.items() if k.strip()},
        "vacuum": vacuum,
    }


def household_prompt(profile: dict[str, Any]) -> str:
    lines: list[str] = []
    if profile.get("rules"):
        lines.append("Household rules from the owner (always follow):")
        lines += [f"- {rule}" for rule in profile["rules"]]
    if profile.get("places"):
        lines.append("Known places:")
        lines += [f"- {name}: {hint}" for name, hint in profile["places"].items()]
    if profile.get("vacuum"):
        lines.append(f"Robot vacuum settings: {json.dumps(profile['vacuum'], sort_keys=True)}")
    return "\n".join(lines)
