from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

from .motors import MotorDriver


COMMAND_ALIASES = {
    "forward": "forward",
    "forwards": "forward",
    "ahead": "forward",
    "back": "backward",
    "backward": "backward",
    "backwards": "backward",
    "reverse": "backward",
    "left": "left",
    "turn_left": "left",
    "turn-left": "left",
    "pivot_left": "left",
    "pivot-left": "left",
    "right": "right",
    "turn_right": "right",
    "turn-right": "right",
    "pivot_right": "right",
    "pivot-right": "right",
    "stop": "stop",
    "halt": "stop",
    "brake": "stop",
}


@dataclass(frozen=True)
class ActiveCommand:
    command: str
    speed_percent: float
    started_at: float
    deadline: float


class ValleController:
    def __init__(
        self,
        driver: MotorDriver,
        *,
        default_speed_percent: float = 60.0,
        default_duration_seconds: float = 5.0,
        max_duration_seconds: float = 5.0,
        default_turn_duration_seconds: float = 0.25,
    ) -> None:
        self._driver = driver
        self._default_speed_percent = default_speed_percent
        self._default_duration_seconds = default_duration_seconds
        self._max_duration_seconds = max_duration_seconds
        self._default_turn_duration_seconds = default_turn_duration_seconds
        self._lock = threading.RLock()
        self._timer: threading.Timer | None = None
        self._sequence = 0
        self._active: ActiveCommand | None = None
        self._last_stop_reason = "startup"
        self._driver.stop()

    @property
    def driver_name(self) -> str:
        return self._driver.name

    def run(
        self,
        command: str,
        *,
        speed_percent: float | None = None,
        duration_seconds: float | None = None,
    ) -> dict[str, Any]:
        normalized = normalize_command(command)
        if normalized == "stop":
            return self.stop(reason="manual")

        speed = clamp(speed_percent, default=self._default_speed_percent, low=0.0, high=100.0)
        if duration_seconds is None and normalized in ("left", "right"):
            default_duration = self._default_turn_duration_seconds
        else:
            default_duration = self._default_duration_seconds
        duration = clamp(
            duration_seconds,
            default=default_duration,
            low=0.0,
            high=self._max_duration_seconds,
        )
        speed_fraction = speed / 100.0

        with self._lock:
            self._cancel_timer_locked()
            self._sequence += 1
            sequence = self._sequence

            try:
                self._apply_movement(normalized, speed_fraction)
            except Exception:
                self._driver.stop()
                self._active = None
                self._last_stop_reason = "driver_error"
                raise

            now = time.monotonic()
            self._active = ActiveCommand(
                command=normalized,
                speed_percent=speed,
                started_at=now,
                deadline=now + duration,
            )
            self._last_stop_reason = ""

            if duration <= 0:
                self._stop_locked(reason="zero_duration")
            else:
                self._timer = threading.Timer(duration, self._auto_stop, args=(sequence,))
                self._timer.daemon = True
                self._timer.start()

            return self.status()

    def stop(self, *, reason: str = "manual") -> dict[str, Any]:
        with self._lock:
            self._sequence += 1
            self._cancel_timer_locked()
            self._stop_locked(reason=reason)
            return self.status()

    def status(self) -> dict[str, Any]:
        with self._lock:
            now = time.monotonic()
            if self._active is None:
                return {
                    "active_command": "stopped",
                    "driver": self.driver_name,
                    "last_stop_reason": self._last_stop_reason,
                    "remaining_seconds": 0.0,
                }

            remaining = max(0.0, self._active.deadline - now)
            return {
                "active_command": self._active.command,
                "driver": self.driver_name,
                "speed_percent": round(self._active.speed_percent, 2),
                "remaining_seconds": round(remaining, 3),
            }

    def close(self) -> None:
        with self._lock:
            self._sequence += 1
            self._cancel_timer_locked()
            self._stop_locked(reason="shutdown")
        self._driver.close()

    def _apply_movement(self, command: str, speed: float) -> None:
        if command == "forward":
            self._driver.forward(speed)
        elif command == "backward":
            self._driver.backward(speed)
        elif command == "left":
            self._driver.turn_left(speed)
        elif command == "right":
            self._driver.turn_right(speed)
        else:
            raise ValueError(f"Unsupported movement command: {command}")

    def _auto_stop(self, sequence: int) -> None:
        with self._lock:
            if sequence != self._sequence:
                return
            self._stop_locked(reason="auto_timeout")

    def _stop_locked(self, *, reason: str) -> None:
        self._driver.stop()
        self._active = None
        self._last_stop_reason = reason

    def _cancel_timer_locked(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None


def normalize_command(command: str) -> str:
    normalized = command.strip().lower().replace(" ", "_")
    try:
        return COMMAND_ALIASES[normalized]
    except KeyError as exc:
        allowed = ", ".join(sorted(COMMAND_ALIASES))
        raise ValueError(f"Unknown command '{command}'. Allowed commands: {allowed}") from exc


def clamp(
    value: float | None,
    *,
    default: float,
    low: float,
    high: float,
) -> float:
    if value is None:
        value = default
    return min(max(float(value), low), high)
