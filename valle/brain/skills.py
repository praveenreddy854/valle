"""Built-in inspection skills: checklist instructions plus default targets."""
from __future__ import annotations

from typing import Any


SKILLS: dict[str, dict[str, Any]] = {
    "check_door_locks": {
        "description": "Check that doors are closed and their deadbolts are engaged.",
        "targets": ["front_door", "back_door"],
        "result_states": ["secure", "unlocked", "ajar", "uncertain"],
        "instructions": (
            "For each target door: navigate until the door fills most of the view, "
            "capture a wide evidence frame, then use inspect_scene to ask whether the "
            "door is fully closed (no gap at the frame/edge) and whether the deadbolt "
            "thumb-turn or bolt is in the locked position (horizontal turn / bolt "
            "extended into the strike). Capture a close-up of the lock. A door is "
            "'secure' only when both closed and bolted with confidence >= 0.7; "
            "otherwise report 'unlocked', 'ajar', or 'uncertain' and set needs_followup."
        ),
    },
    "vacuum_prep": {
        "description": "Sweep the floor before the nightly robot vacuum run.",
        "targets": ["toy", "sock", "cable", "hair tie", "pet toy", "shoelace", "clump of hair or fur"],
        "result_states": ["clear", "blockers_found", "uncertain"],
        "instructions": (
            "Slowly survey the floor areas the robot vacuum covers. At each viewpoint "
            "pivot in steps, use find_object for each target and inspect_scene to ask "
            "about small floor items that tangle vacuum brushes (toys with hair or fur, "
            "strings, cords, socks, tassels). For every blocker call record_finding "
            "with kind='hazard', its location, and an evidence frame. Report "
            "'blockers_found' with a pick-up list in the summary, or 'clear'."
        ),
    },
    "scout": {
        "description": "Freely explore the home and suggest things worth attention.",
        "targets": [],
        "result_states": ["done", "uncertain"],
        "instructions": (
            "Explore reachable rooms with short reflex-gated moves. Use inspect_scene "
            "at each viewpoint to notice anything unusual: open doors or windows, "
            "items on the floor, spills, lights or appliances left on, pet issues. "
            "Record each with record_finding (hazard, suggestion, or observation) "
            "and evidence. Remember lessons about the layout with remember_lesson."
        ),
    },
}


def apply_skill(mission: dict[str, Any]) -> dict[str, Any]:
    """Fill default targets from the mission's skill when none were given."""
    skill = SKILLS.get(str(mission.get("skill", "")))
    if skill is None or mission.get("targets"):
        return mission
    return {**mission, "targets": list(skill["targets"])}


def skill_prompt(mission: dict[str, Any]) -> str:
    skill = SKILLS.get(str(mission.get("skill", "")))
    if skill is None:
        return ""
    return (
        f"Skill checklist ({mission['skill']}): {skill['instructions']}\n"
        f"Allowed result states: {', '.join(skill['result_states'])}."
    )
