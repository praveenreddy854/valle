"""Push notifications for finished missions.

Posts a plain-text message with a ``Title`` header to ``VALLE_NOTIFY_URL``,
which makes any ntfy topic (https://ntfy.sh) or similar webhook receiver
work without extra configuration. When the variable is unset this is a
no-op.
"""
from __future__ import annotations

import logging
import os
from typing import Any


log = logging.getLogger("valle.brain.notify")


def notify(title: str, message: str) -> bool:
    url = os.getenv("VALLE_NOTIFY_URL", "").strip()
    if not url:
        return False
    import requests

    try:
        response = requests.post(
            url,
            data=message.encode("utf-8"),
            headers={"Title": title},
            timeout=5,
        )
        response.raise_for_status()
    except requests.RequestException:
        log.exception("notification to %s failed", url)
        return False
    return True


def notify_mission_result(record: dict[str, Any]) -> bool:
    mission = record.get("mission") or {}
    goal = str(mission.get("goal", "mission"))
    status = str(record.get("status", "unknown"))
    title = f"Valle: {goal}"

    lines = [f"Status: {status}"]
    result = record.get("result")
    if isinstance(result, dict):
        lines.append(f"State: {result.get('state', 'unknown')}")
        confidence = result.get("confidence")
        if isinstance(confidence, (int, float)):
            lines.append(f"Confidence: {confidence:.2f}")
        if result.get("summary"):
            lines.append(str(result["summary"]))
        if result.get("needs_followup"):
            lines.append("Needs follow-up.")
    if record.get("error"):
        lines.append(f"Error: {record['error']}")
    evidence = record.get("evidence") or []
    if evidence:
        lines.append(f"Evidence images: {len(evidence)} (run {record.get('run_id')})")
    return notify(title, "\n".join(lines))
