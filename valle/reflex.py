from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ReflexReading:
    """Current front-camera clearance estimate.

    Values are normalized closeness readings in [0, 1]; higher means closer
    and therefore more blocked.
    """

    left: float
    center: float
    right: float
    captured_at_monotonic: float
    source: str = "unknown"

    def as_dict(self) -> dict[str, Any]:
        return {
            "left": round(self.left, 3),
            "center": round(self.center, 3),
            "right": round(self.right, 3),
            "source": self.source,
            "age_seconds": round(time.monotonic() - self.captured_at_monotonic, 3),
        }


@dataclass(frozen=True)
class ReflexDecision:
    allowed: bool
    reason: str
    clearance: ReflexReading | None = None
    recommended_direction: str | None = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "allowed": self.allowed,
            "reason": self.reason,
        }
        if self.clearance is not None:
            payload["clearance"] = self.clearance.as_dict()
        if self.recommended_direction is not None:
            payload["recommended_direction"] = self.recommended_direction
        return payload


class ReflexGate:
    """Vetoes agent movement intents using the latest clearance reading."""

    def __init__(
        self,
        *,
        blocked_threshold: float = 0.55,
        max_age_seconds: float = 2.0,
        enabled: bool = True,
    ) -> None:
        self._blocked_threshold = blocked_threshold
        self._max_age_seconds = max_age_seconds
        self._enabled = enabled
        self._latest: ReflexReading | None = None

    def update(
        self,
        *,
        left: float,
        center: float,
        right: float,
        source: str = "unknown",
    ) -> ReflexReading:
        reading = ReflexReading(
            left=_clamp01(left),
            center=_clamp01(center),
            right=_clamp01(right),
            captured_at_monotonic=time.monotonic(),
            source=source,
        )
        self._latest = reading
        return reading

    def latest(self) -> ReflexReading | None:
        return self._latest

    def decide(self, direction: str) -> ReflexDecision:
        if not self._enabled:
            return ReflexDecision(allowed=True, reason="reflex_disabled")

        reading = self._latest
        if reading is None:
            return ReflexDecision(
                allowed=False,
                reason="no_reflex_reading",
                recommended_direction="stop",
            )

        age = time.monotonic() - reading.captured_at_monotonic
        if age > self._max_age_seconds:
            return ReflexDecision(
                allowed=False,
                reason="stale_reflex_reading",
                clearance=reading,
                recommended_direction="stop",
            )

        if direction == "forward":
            return self._allow_if(
                reading.center < self._blocked_threshold,
                "center_clear",
                "center_blocked",
                reading,
            )
        if direction == "left":
            return self._allow_if(
                reading.left < self._blocked_threshold,
                "left_clear",
                "left_blocked",
                reading,
            )
        if direction == "right":
            return self._allow_if(
                reading.right < self._blocked_threshold,
                "right_clear",
                "right_blocked",
                reading,
            )
        if direction == "backward":
            return ReflexDecision(
                allowed=True,
                reason="bounded_escape_reverse",
                clearance=reading,
            )
        return ReflexDecision(
            allowed=False,
            reason="unsupported_direction",
            clearance=reading,
            recommended_direction="stop",
        )

    def _allow_if(
        self,
        allowed: bool,
        allowed_reason: str,
        blocked_reason: str,
        reading: ReflexReading,
    ) -> ReflexDecision:
        if allowed:
            return ReflexDecision(
                allowed=True,
                reason=allowed_reason,
                clearance=reading,
            )
        return ReflexDecision(
            allowed=False,
            reason=blocked_reason,
            clearance=reading,
            recommended_direction=_recommend_direction(reading, self._blocked_threshold),
        )


def _recommend_direction(reading: ReflexReading, blocked_threshold: float) -> str:
    if reading.left < blocked_threshold and reading.left <= reading.right:
        return "left"
    if reading.right < blocked_threshold:
        return "right"
    return "backward"


def _clamp01(value: float) -> float:
    return min(max(float(value), 0.0), 1.0)
