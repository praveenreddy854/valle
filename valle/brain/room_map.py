"""Persistent landmark map of the home, built by a vision LLM during seek.

Valle has only a camera (no wheel encoders or IMU), so this is a topological
map, not a floor plan: a graph of *places* the robot stopped at, what it saw
in each of four headings there, and which heading led to which next place.
The LLM uses it to avoid re-searching areas and to pick unexplored openings.
"""
from __future__ import annotations

import json
import os
import secrets
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

HEADINGS = ("ahead", "right", "behind", "left")


class RoomMapStore:
    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls) -> "RoomMapStore":
        return cls(os.getenv("VALLE_ROOM_MAP_FILE", "logs/room-map.json"))

    def get(self) -> dict[str, Any]:
        with self._lock:
            return self._read_locked()

    def reset(self) -> None:
        with self._lock:
            self._write_locked(_empty_map())

    def add_place(
        self,
        *,
        summary: str,
        views: dict[str, str],
        openings: list[str],
        came_from: str | None = None,
        came_heading: str | None = None,
        searched_for: str = "",
    ) -> dict[str, Any]:
        """Record a new place; link it from the place the robot drove out of."""
        place = {
            "id": secrets.token_hex(3),
            "summary": summary.strip(),
            "views": {h: views.get(h, "") for h in HEADINGS},
            "openings": [h for h in openings if h in HEADINGS],
            "explored": [],
            "searched_for": [searched_for] if searched_for else [],
            "visited_at": _now(),
        }
        with self._lock:
            data = self._read_locked()
            data["places"].append(place)
            if came_from and came_heading:
                data["links"].append(
                    {"from": came_from, "heading": came_heading, "to": place["id"]}
                )
                _mark_explored(data, came_from, came_heading)
            self._write_locked(data)
        return place

    def match_place(self, place_id: str, *, searched_for: str = "") -> None:
        """The LLM recognised a known place: refresh it instead of adding a duplicate."""
        with self._lock:
            data = self._read_locked()
            for place in data["places"]:
                if place["id"] == place_id:
                    place["visited_at"] = _now()
                    if searched_for and searched_for not in place["searched_for"]:
                        place["searched_for"].append(searched_for)
            self._write_locked(data)

    def link(self, from_id: str, heading: str, to_id: str) -> None:
        with self._lock:
            data = self._read_locked()
            if from_id != to_id and not any(
                l["from"] == from_id and l["heading"] == heading for l in data["links"]
            ):
                data["links"].append({"from": from_id, "heading": heading, "to": to_id})
            _mark_explored(data, from_id, heading)
            self._write_locked(data)

    def mark_explored(self, place_id: str, heading: str) -> None:
        with self._lock:
            data = self._read_locked()
            _mark_explored(data, place_id, heading)
            self._write_locked(data)

    def _read_locked(self) -> dict[str, Any]:
        if not self._path.exists():
            return _empty_map()
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return _empty_map()
        data.setdefault("places", [])
        data.setdefault("links", [])
        return data

    def _write_locked(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self._path)


def map_prompt(data: dict[str, Any]) -> str:
    """Compact text form of the map for the LLM prompt."""
    places = data.get("places") or []
    if not places:
        return "The room map is empty: this is the first place recorded."
    lines = [
        "Known places (id: summary | openings | explored | searched for), each "
        "followed by what was seen in each heading of its stored frame:"
    ]
    for place in places[-30:]:
        lines.append(
            f"- {place['id']}: {place['summary']} | openings={','.join(place['openings']) or '-'}"
            f" | explored={','.join(place['explored']) or '-'}"
            f" | searched_for={','.join(place['searched_for']) or '-'}"
        )
        views = place.get("views") or {}
        lines.append(
            "    views: "
            + "; ".join(f"{h}={views.get(h) or '?'}" for h in HEADINGS)
        )
    links = data.get("links") or []
    if links:
        lines.append("Links (from --heading--> to):")
        lines.extend(f"- {l['from']} --{l['heading']}--> {l['to']}" for l in links[-60:])
    return "\n".join(lines)


def _mark_explored(data: dict[str, Any], place_id: str, heading: str) -> None:
    for place in data["places"]:
        if place["id"] == place_id and heading not in place["explored"]:
            place["explored"].append(heading)


def _empty_map() -> dict[str, Any]:
    return {"places": [], "links": []}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
