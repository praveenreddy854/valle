"""LLM waypoint planner for seek: reads a 4-view spin scan plus the room map,
records the place, and chooses which heading to explore next."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .room_map import HEADINGS, RoomMapStore, map_prompt

_SYSTEM = (
    "You plan exploration for a small home robot searching for an object. It has "
    "one low camera and no odometry. At each stop it pivots to take four photos: "
    "ahead, right, behind, left (roughly 90 degrees apart). You receive those "
    "photos and a map of places it has recorded before. Decide whether this stop "
    "matches a known place, describe it, list headings with an open path "
    "(a doorway, hallway, or floor gap the robot can drive through), and pick the "
    "next heading to explore. Prefer unexplored openings and areas not yet "
    "searched for this object; if the object is visible, say which heading. "
    "Use 'done' only if every reachable opening has been explored.\n"
    "Headings in your reply (openings, object_heading, next_heading, views) "
    "are relative to the robot's CURRENT scan. Each known place's views, "
    "openings, and explored lists use the frame of that place's first visit. "
    "If you match a known place, set heading_offset to how many 90-degree "
    "clockwise turns the current 'ahead' is from that place's stored 'ahead' "
    "(0-3; e.g. 1 if the current ahead shows what was stored as 'right'), and "
    "use it to judge which current headings are already explored."
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "matched_place_id": {
            "type": "string",
            "description": "Id of the known place this matches, or empty string if new.",
        },
        "summary": {
            "type": "string",
            "description": "Short name for this place, e.g. 'living room by grey couch'.",
        },
        "views": {
            "type": "object",
            "properties": {h: {"type": "string"} for h in HEADINGS},
            "required": list(HEADINGS),
            "additionalProperties": False,
        },
        "openings": {"type": "array", "items": {"type": "string", "enum": list(HEADINGS)}},
        "object_heading": {
            "type": "string",
            "enum": [*HEADINGS, "none"],
            "description": "Heading where the searched object is visible, or none.",
        },
        "heading_offset": {
            "type": "integer",
            "enum": [0, 1, 2, 3],
            "description": "Clockwise quarter turns from the matched place's stored "
            "ahead to the current ahead; 0 for a new place.",
        },
        "next_heading": {"type": "string", "enum": [*HEADINGS, "done"]},
        "reason": {"type": "string"},
    },
    "required": [
        "matched_place_id",
        "summary",
        "views",
        "openings",
        "object_heading",
        "heading_offset",
        "next_heading",
        "reason",
    ],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class WaypointPlan:
    place_id: str
    is_new_place: bool
    summary: str
    openings: list[str]
    object_heading: str
    next_heading: str
    reason: str
    # Clockwise quarter turns from the place's stored "ahead" to the robot's
    # current "ahead" (always 0 for a new place).
    heading_offset: int = 0

    def to_place_frame(self, heading: str) -> str:
        """Convert a heading in the current scan to this place's stored frame."""
        return rotate_heading(heading, self.heading_offset)


def rotate_heading(heading: str, quarter_turns: int) -> str:
    return HEADINGS[(HEADINGS.index(heading) + quarter_turns) % len(HEADINGS)]


class WaypointPlanner:
    def __init__(self, inspector: Any, store: RoomMapStore, *, effort: str = "low") -> None:
        self._inspector = inspector
        self._store = store
        self._effort = effort

    def plan(
        self,
        views: dict[str, Any],
        object_query: str,
        *,
        came_from: str | None,
        came_heading: str | None,
    ) -> WaypointPlan:
        data = self._store.get()
        headings = [h for h in HEADINGS if h in views]
        prompt = (
            f"Searching for: {object_query}\n"
            f"The robot arrived here by driving {came_heading or 'nowhere (start)'}"
            f" from place {came_from or '-'}.\n\n{map_prompt(data)}"
        )
        reply = self._inspector.ask_json(
            [views[h] for h in headings],
            prompt,
            schema=_SCHEMA,
            system=_SYSTEM,
            labels=[f"View {h}:" for h in headings],
            effort=self._effort,
        )

        known_ids = {p["id"] for p in data["places"]}
        matched = str(reply.get("matched_place_id") or "")
        openings = [h for h in reply.get("openings") or [] if h in HEADINGS]
        offset = reply.get("heading_offset")
        offset = offset if isinstance(offset, int) and 0 <= offset < 4 else 0
        if matched in known_ids:
            self._store.match_place(matched, searched_for=object_query)
            if came_from and came_heading:
                self._store.link(came_from, came_heading, matched)
            place_id, is_new = matched, False
        else:
            place = self._store.add_place(
                summary=str(reply.get("summary") or "unnamed place"),
                views={h: str((reply.get("views") or {}).get(h, "")) for h in HEADINGS},
                openings=openings,
                came_from=came_from,
                came_heading=came_heading,
                searched_for=object_query,
            )
            place_id, is_new, offset = place["id"], True, 0

        next_heading = str(reply.get("next_heading") or "done")
        if next_heading not in (*HEADINGS, "done"):
            next_heading = "done"
        object_heading = str(reply.get("object_heading") or "none")
        return WaypointPlan(
            place_id=place_id,
            is_new_place=is_new,
            summary=str(reply.get("summary") or ""),
            openings=openings,
            object_heading=object_heading if object_heading in HEADINGS else "none",
            next_heading=next_heading,
            reason=str(reply.get("reason") or ""),
            heading_offset=offset,
        )
