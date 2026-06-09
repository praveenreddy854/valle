from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class BrainApiConfig:
    host: str = "0.0.0.0"
    port: int = 8090

    @classmethod
    def from_env(cls) -> "BrainApiConfig":
        return cls(
            host=os.getenv("VALLE_BRAIN_API_HOST", cls.host),
            port=_env_int("VALLE_BRAIN_API_PORT", cls.port),
        )


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
