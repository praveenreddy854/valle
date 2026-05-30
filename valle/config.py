from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ValleConfig:
    host: str = "0.0.0.0"
    port: int = 8080
    driver: str = "auto"

    left_forward_pin: int = 5
    left_backward_pin: int = 6
    left_enable_pin: int = 12

    right_forward_pin: int = 20
    right_backward_pin: int = 21
    right_enable_pin: int = 13

    default_speed_percent: float = 60.0
    default_duration_seconds: float = 5.0
    max_duration_seconds: float = 5.0

    @classmethod
    def from_env(cls) -> "ValleConfig":
        return cls(
            host=os.getenv("VALLE_HOST", cls.host),
            port=_env_int("VALLE_PORT", cls.port),
            driver=os.getenv("VALLE_DRIVER", cls.driver),
            left_forward_pin=_env_int("VALLE_LEFT_FORWARD_PIN", cls.left_forward_pin),
            left_backward_pin=_env_int("VALLE_LEFT_BACKWARD_PIN", cls.left_backward_pin),
            left_enable_pin=_env_int("VALLE_LEFT_ENABLE_PIN", cls.left_enable_pin),
            right_forward_pin=_env_int("VALLE_RIGHT_FORWARD_PIN", cls.right_forward_pin),
            right_backward_pin=_env_int("VALLE_RIGHT_BACKWARD_PIN", cls.right_backward_pin),
            right_enable_pin=_env_int("VALLE_RIGHT_ENABLE_PIN", cls.right_enable_pin),
            default_speed_percent=_env_float(
                "VALLE_DEFAULT_SPEED_PERCENT", cls.default_speed_percent
            ),
            default_duration_seconds=_env_float(
                "VALLE_DEFAULT_DURATION_SECONDS", cls.default_duration_seconds
            ),
            max_duration_seconds=_env_float(
                "VALLE_MAX_DURATION_SECONDS", cls.max_duration_seconds
            ),
        )


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
