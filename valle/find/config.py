from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class FindConfig:
    pi_ws_url: str = "ws://rpi.local:8080/brain/find"
    camera_url: str = "http://rpi.local:8081/stream.mjpg"

    detector_model: str = "google/owlv2-base-patch16-ensemble"
    detector_device: str = "auto"
    score_threshold: float = 0.10
    max_results: int = 5

    reconnect_seconds: float = 3.0

    seek_found_score: float = 0.20
    seek_default_max_seconds: float = 60.0

    @classmethod
    def from_env(cls) -> "FindConfig":
        return cls(
            pi_ws_url=os.getenv("VALLE_PI_WS_URL", cls.pi_ws_url),
            camera_url=os.getenv("VALLE_CAMERA_URL", cls.camera_url),
            detector_model=os.getenv("VALLE_DETECTOR_MODEL", cls.detector_model),
            detector_device=os.getenv("VALLE_DETECTOR_DEVICE", cls.detector_device),
            score_threshold=_env_float("VALLE_SCORE_THRESHOLD", cls.score_threshold),
            max_results=_env_int("VALLE_MAX_RESULTS", cls.max_results),
            reconnect_seconds=_env_float(
                "VALLE_RECONNECT_SECONDS", cls.reconnect_seconds
            ),
            seek_found_score=_env_float(
                "VALLE_SEEK_FOUND_SCORE", cls.seek_found_score
            ),
            seek_default_max_seconds=_env_float(
                "VALLE_SEEK_DEFAULT_MAX_SECONDS", cls.seek_default_max_seconds
            ),
        )


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
