from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class BrainConfig:
    pi_base_url: str = "http://rpi.local:8080"
    camera_url: str = "http://rpi.local:8081/stream.mjpg"

    tick_hz: float = 4.0
    grace_seconds: float = 2.0
    request_timeout_seconds: float = 3.0

    session_max_seconds: float | None = None
    session_idle_seconds: float | None = None

    depth_model: str = "depth-anything/Depth-Anything-V2-Small-hf"
    depth_device: str = "auto"

    # Relative-depth threshold above which a strip counts as blocked.
    # Depth Anything V2 outputs higher = closer, normalised per-frame to [0, 1].
    blocked_threshold: float = 0.55
    # A direction must beat the current blocked threshold by this margin to
    # be re-chosen after a reverse. Prevents forward/reverse oscillation.
    hysteresis_margin: float = 0.05

    # Strip layout (fractions of frame width).
    left_strip_end: float = 0.33
    right_strip_start: float = 0.67
    # Vertical region considered for clearance (skips ceiling and immediate floor).
    top_crop: float = 0.15
    bottom_crop: float = 0.10

    # Micro-pulse durations (seconds) per action.
    pulse_forward: float = 0.30
    pulse_turn: float = 0.20
    pulse_backward: float = 0.30

    # Speed percentages per action.
    speed_forward: float = 55.0
    speed_turn: float = 55.0
    speed_backward: float = 45.0

    @classmethod
    def from_env(cls) -> "BrainConfig":
        return cls(
            pi_base_url=os.getenv("VALLE_PI_BASE_URL", cls.pi_base_url),
            camera_url=os.getenv("VALLE_CAMERA_URL", cls.camera_url),
            tick_hz=_env_float("VALLE_BRAIN_TICK_HZ", cls.tick_hz),
            grace_seconds=_env_float("VALLE_BRAIN_GRACE_SECONDS", cls.grace_seconds),
            request_timeout_seconds=_env_float(
                "VALLE_BRAIN_REQUEST_TIMEOUT", cls.request_timeout_seconds
            ),
            session_max_seconds=_env_optional_float("VALLE_BRAIN_SESSION_MAX_SECONDS"),
            session_idle_seconds=_env_optional_float("VALLE_BRAIN_SESSION_IDLE_SECONDS"),
            depth_model=os.getenv("VALLE_DEPTH_MODEL", cls.depth_model),
            depth_device=os.getenv("VALLE_DEPTH_DEVICE", cls.depth_device),
            blocked_threshold=_env_float(
                "VALLE_BLOCKED_THRESHOLD", cls.blocked_threshold
            ),
            hysteresis_margin=_env_float(
                "VALLE_HYSTERESIS_MARGIN", cls.hysteresis_margin
            ),
            left_strip_end=_env_float("VALLE_LEFT_STRIP_END", cls.left_strip_end),
            right_strip_start=_env_float(
                "VALLE_RIGHT_STRIP_START", cls.right_strip_start
            ),
            top_crop=_env_float("VALLE_TOP_CROP", cls.top_crop),
            bottom_crop=_env_float("VALLE_BOTTOM_CROP", cls.bottom_crop),
            pulse_forward=_env_float("VALLE_PULSE_FORWARD", cls.pulse_forward),
            pulse_turn=_env_float("VALLE_PULSE_TURN", cls.pulse_turn),
            pulse_backward=_env_float("VALLE_PULSE_BACKWARD", cls.pulse_backward),
            speed_forward=_env_float("VALLE_SPEED_FORWARD", cls.speed_forward),
            speed_turn=_env_float("VALLE_SPEED_TURN", cls.speed_turn),
            speed_backward=_env_float("VALLE_SPEED_BACKWARD", cls.speed_backward),
        )


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc


def _env_optional_float(name: str) -> float | None:
    value = os.getenv(name)
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
