from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AgentConfig:
    pi_base_url: str = "http://rpi.local:8080"
    azure_api_key: str = ""
    azure_deployment: str = ""
    azure_endpoint: str = ""
    azure_api_version: str = "2024-06-01"
    max_steps: int = 20

    @classmethod
    def from_env(cls) -> "AgentConfig":
        load_dotenv()
        deployment = _first_env(
            "AZURE_OPENAI_DEPLOYMENT",
            "AZURE_OPENAI_DEPLOYMENT_NAME",
            "AZURE_DEPLOYMENT",
        )
        endpoint = _azure_endpoint(deployment)
        return cls(
            pi_base_url=os.getenv("VALLE_PI_BASE_URL", cls.pi_base_url).rstrip("/"),
            azure_api_key=_first_env("AZURE_API_KEY", "AZURE_OPENAI_API_KEY"),
            azure_deployment=deployment,
            azure_endpoint=endpoint,
            azure_api_version=os.getenv("AZURE_API_VERSION", cls.azure_api_version),
            max_steps=_env_int("VALLE_AGENT_MAX_STEPS", cls.max_steps),
        )

    def validate(self) -> None:
        missing = []
        if not self.azure_api_key:
            missing.append("AZURE_API_KEY")
        if not self.azure_deployment:
            missing.append("AZURE_OPENAI_DEPLOYMENT")
        if not self.azure_endpoint:
            missing.append("AZURE_ENDPOINT or AZURE_RESOURCE_NAME")
        if missing:
            raise ValueError("missing required env vars: " + ", ".join(missing))


def load_mission(argv: list[str] | None = None) -> dict[str, Any]:
    load_dotenv()
    raw = os.getenv("VALLE_AGENT_MISSION_JSON")
    if raw:
        mission = json.loads(raw)
    else:
        argv = sys.argv[1:] if argv is None else argv
        goal = " ".join(argv).strip()
        if not goal:
            raise ValueError(
                "provide a mission goal as CLI text or VALLE_AGENT_MISSION_JSON"
            )
        mission = {"goal": goal}

    return normalize_mission(mission)


def normalize_mission(mission: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(mission, dict):
        raise ValueError("mission must be a JSON object")
    goal = mission.get("goal")
    if not isinstance(goal, str) or not goal.strip():
        raise ValueError("mission goal is required")

    normalized = dict(mission)
    normalized["goal"] = goal.strip()
    for key in ("task", "skill", "report_to"):
        value = normalized.get(key)
        if value is None:
            continue
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"mission {key} must be a non-empty string")
        normalized[key] = value.strip()

    targets = normalized.get("targets")
    if targets is not None:
        if not isinstance(targets, list):
            raise ValueError("mission targets must be a list")
        normalized["targets"] = [
            target.strip()
            for target in targets
            if isinstance(target, str) and target.strip()
        ]

    return normalized


def load_dotenv(path: str | None = None) -> None:
    env_path = _dotenv_path(path)
    if env_path is None:
        return
    with env_path.open(encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = _clean_env_value(value.strip())
            if not key or (key in os.environ and os.environ[key] != ""):
                continue
            os.environ[key] = value


def _azure_endpoint(deployment: str) -> str:
    explicit = (
        os.getenv("AZURE_ENDPOINT")
        or os.getenv("AZURE_OPENAI_ENDPOINT")
        or os.getenv("AZURE_API_BASE")
    )
    if explicit:
        return explicit.rstrip("/")

    resource_name = _first_env("AZURE_RESOURCE_NAME", "AZURE_OPENAI_RESOURCE")
    if resource_name and deployment:
        return (
            f"https://{resource_name}.openai.azure.com/openai/deployments/"
            f"{deployment}"
        )
    return ""


def _dotenv_path(path: str | None) -> Path | None:
    configured = os.getenv("VALLE_DOTENV_PATH") if path is None else path
    if configured:
        candidate = Path(configured)
        return candidate if candidate.exists() else None

    candidates = [
        Path.cwd() / ".env",
        Path(__file__).resolve().parents[3] / ".env",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _first_env(*names: str) -> str:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return ""


def _clean_env_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
