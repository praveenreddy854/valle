from __future__ import annotations

import secrets
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

AUTOPILOT_DIRECTIONS = ("forward", "backward", "left", "right")
PROGRESS_DIRECTIONS = ("forward", "backward")

AUTOPILOT_END_REASONS = {
    "manual": "autopilot_manual",
    "hard_cap": "autopilot_hard_cap",
    "idle": "autopilot_idle",
    "blind": "autopilot_blind",
}


@dataclass(frozen=True)
class ActiveCommand:
    command: str
    speed_percent: float
    started_at: float
    deadline: float


@dataclass(frozen=True)
class AutopilotSession:
    session_id: str
    started_at_epoch: float
    started_at_monotonic: float
    max_seconds: float
    idle_seconds: float


class SessionAlreadyActiveError(RuntimeError):
    def __init__(self, session_id: str) -> None:
        super().__init__(f"autopilot session already active: {session_id}")
        self.session_id = session_id


class SessionNotActiveError(RuntimeError):
    def __init__(self, session_id: str | None) -> None:
        super().__init__("autopilot session not active")
        self.session_id = session_id


class ValleController:
    def __init__(
        self,
        driver: MotorDriver,
        *,
        default_speed_percent: float = 60.0,
        default_duration_seconds: float = 5.0,
        max_duration_seconds: float = 5.0,
        default_turn_duration_seconds: float = 0.25,
        autopilot_max_seconds: float = 1800.0,
        autopilot_idle_seconds: float = 20.0,
    ) -> None:
        self._driver = driver
        self._default_speed_percent = default_speed_percent
        self._default_duration_seconds = default_duration_seconds
        self._max_duration_seconds = max_duration_seconds
        self._default_turn_duration_seconds = default_turn_duration_seconds
        self._autopilot_max_seconds = autopilot_max_seconds
        self._autopilot_idle_seconds = autopilot_idle_seconds
        self._lock = threading.RLock()
        self._timer: threading.Timer | None = None
        self._sequence = 0
        self._active: ActiveCommand | None = None
        self._last_stop_reason = "startup"
        self._session: AutopilotSession | None = None
        self._session_hard_cap_timer: threading.Timer | None = None
        self._session_idle_timer: threading.Timer | None = None
        self._session_last_progress_monotonic: float = 0.0
        self._driver.stop()

    @property
    def driver_name(self) -> str:
        return self._driver.name

    def autopilot_session_id(self) -> str | None:
        with self._lock:
            return self._session.session_id if self._session is not None else None

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

        with self._lock:
            if self._session is not None:
                raise SessionAlreadyActiveError(self._session.session_id)

            speed = clamp(
                speed_percent,
                default=self._default_speed_percent,
                low=0.0,
                high=100.0,
            )
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
            self._pulse_locked(normalized, speed, duration)
            return self.status()

    def stop(self, *, reason: str = "manual") -> dict[str, Any]:
        with self._lock:
            if self._session is not None:
                self._end_session_locked(reason="manual")
            else:
                self._sequence += 1
                self._cancel_timer_locked()
                self._stop_locked(reason=reason)
            return self.status()

    def start_autopilot(
        self,
        *,
        max_seconds: float | None = None,
        idle_seconds: float | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            if self._session is not None:
                raise SessionAlreadyActiveError(self._session.session_id)

            max_s = clamp(
                max_seconds,
                default=self._autopilot_max_seconds,
                low=0.0,
                high=self._autopilot_max_seconds,
            )
            idle_s = clamp(
                idle_seconds,
                default=self._autopilot_idle_seconds,
                low=0.0,
                high=self._autopilot_idle_seconds,
            )

            session_id = secrets.token_hex(8)
            now_mono = time.monotonic()
            now_epoch = time.time()
            self._session = AutopilotSession(
                session_id=session_id,
                started_at_epoch=now_epoch,
                started_at_monotonic=now_mono,
                max_seconds=max_s,
                idle_seconds=idle_s,
            )
            self._session_last_progress_monotonic = now_mono
            self._arm_hard_cap_locked(max_s)
            self._arm_idle_watchdog_locked(idle_s)
            return {
                "session_id": session_id,
                "max_seconds": max_s,
                "idle_seconds": idle_s,
                "started_at": now_epoch,
            }

    def autopilot_drive(
        self,
        session_id: str,
        *,
        direction: str,
        duration_seconds: float | None = None,
        speed_percent: float | None = None,
    ) -> dict[str, Any]:
        normalized = normalize_command(direction)
        if normalized not in AUTOPILOT_DIRECTIONS:
            raise ValueError(
                f"autopilot direction must be one of {AUTOPILOT_DIRECTIONS}"
            )

        with self._lock:
            if self._session is None or self._session.session_id != session_id:
                raise SessionNotActiveError(session_id)

            speed = clamp(
                speed_percent,
                default=self._default_speed_percent,
                low=0.0,
                high=100.0,
            )
            duration = clamp(
                duration_seconds,
                default=self._default_duration_seconds,
                low=0.0,
                high=self._max_duration_seconds,
            )
            self._pulse_locked(normalized, speed, duration)

            if normalized in PROGRESS_DIRECTIONS:
                self._session_last_progress_monotonic = time.monotonic()
                self._arm_idle_watchdog_locked(self._session.idle_seconds)

            return self._autopilot_status_locked()

    def stop_autopilot(self, session_id: str, *, reason: str = "manual") -> dict[str, Any]:
        with self._lock:
            if self._session is None or self._session.session_id != session_id:
                raise SessionNotActiveError(session_id)
            mapped = reason if reason in AUTOPILOT_END_REASONS else "manual"
            self._end_session_locked(reason=mapped)
            return self.status()

    def status(self) -> dict[str, Any]:
        with self._lock:
            base: dict[str, Any]
            now = time.monotonic()
            if self._active is None:
                base = {
                    "active_command": "stopped",
                    "driver": self.driver_name,
                    "last_stop_reason": self._last_stop_reason,
                    "remaining_seconds": 0.0,
                }
            else:
                remaining = max(0.0, self._active.deadline - now)
                base = {
                    "active_command": self._active.command,
                    "driver": self.driver_name,
                    "speed_percent": round(self._active.speed_percent, 2),
                    "remaining_seconds": round(remaining, 3),
                }

            if self._session is not None:
                base["autopilot"] = self._autopilot_status_locked()
            return base

    def close(self) -> None:
        with self._lock:
            if self._session is not None:
                self._end_session_locked(reason="manual")
            self._sequence += 1
            self._cancel_timer_locked()
            self._stop_locked(reason="shutdown")
        self._driver.close()

    def _pulse_locked(self, command: str, speed_percent: float, duration: float) -> None:
        self._cancel_timer_locked()
        self._sequence += 1
        sequence = self._sequence
        speed_fraction = speed_percent / 100.0

        try:
            self._apply_movement(command, speed_fraction)
        except Exception:
            self._driver.stop()
            self._active = None
            self._last_stop_reason = "driver_error"
            raise

        now = time.monotonic()
        self._active = ActiveCommand(
            command=command,
            speed_percent=speed_percent,
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

    def _arm_hard_cap_locked(self, seconds: float) -> None:
        self._cancel_hard_cap_locked()
        timer = threading.Timer(seconds, self._hard_cap_fired)
        timer.daemon = True
        self._session_hard_cap_timer = timer
        timer.start()

    def _cancel_hard_cap_locked(self) -> None:
        if self._session_hard_cap_timer is not None:
            self._session_hard_cap_timer.cancel()
            self._session_hard_cap_timer = None

    def _arm_idle_watchdog_locked(self, seconds: float) -> None:
        self._cancel_idle_watchdog_locked()
        timer = threading.Timer(seconds, self._idle_watchdog_fired)
        timer.daemon = True
        self._session_idle_timer = timer
        timer.start()

    def _cancel_idle_watchdog_locked(self) -> None:
        if self._session_idle_timer is not None:
            self._session_idle_timer.cancel()
            self._session_idle_timer = None

    def _hard_cap_fired(self) -> None:
        with self._lock:
            if self._session is None:
                return
            self._end_session_locked(reason="hard_cap")

    def _idle_watchdog_fired(self) -> None:
        with self._lock:
            if self._session is None:
                return
            since = time.monotonic() - self._session_last_progress_monotonic
            if since + 1e-6 < self._session.idle_seconds:
                remaining = max(0.05, self._session.idle_seconds - since)
                self._arm_idle_watchdog_locked(remaining)
                return
            self._end_session_locked(reason="idle")

    def _end_session_locked(self, *, reason: str) -> None:
        mapped = AUTOPILOT_END_REASONS.get(reason, AUTOPILOT_END_REASONS["manual"])
        self._cancel_hard_cap_locked()
        self._cancel_idle_watchdog_locked()
        self._sequence += 1
        self._cancel_timer_locked()
        self._stop_locked(reason=mapped)
        self._session = None
        self._session_last_progress_monotonic = 0.0

    def _autopilot_status_locked(self) -> dict[str, Any]:
        assert self._session is not None
        now = time.monotonic()
        session_remaining = max(
            0.0,
            self._session.started_at_monotonic + self._session.max_seconds - now,
        )
        idle_remaining = max(
            0.0,
            self._session_last_progress_monotonic + self._session.idle_seconds - now,
        )
        return {
            "session_id": self._session.session_id,
            "started_at": self._session.started_at_epoch,
            "max_seconds": self._session.max_seconds,
            "idle_seconds": self._session.idle_seconds,
            "session_remaining_seconds": round(session_remaining, 3),
            "idle_remaining_seconds": round(idle_remaining, 3),
        }


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
