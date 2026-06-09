from __future__ import annotations

import json
from typing import Any, Type

from pydantic import BaseModel, Field

from .client import AgentPiClient

try:
    from crewai.tools import BaseTool
except ImportError as exc:  # pragma: no cover - import guard for missing extra
    raise RuntimeError(
        "CrewAI is required for valle.brain.agent. "
        "Install with: pip install -e '.[agent]'"
    ) from exc


class AgentSessionState:
    def __init__(self) -> None:
        self.session_id: str | None = None
        self.result: dict[str, Any] | None = None


class StartAgentSessionInput(BaseModel):
    max_seconds: float | None = Field(
        default=None, description="Optional session hard cap in seconds."
    )
    idle_seconds: float | None = Field(
        default=None, description="Optional no-progress watchdog in seconds."
    )


class StartAgentSessionTool(BaseTool):
    name: str = "start_agent_session"
    description: str = "Start the Pi-side reflex-gated agent session."
    args_schema: Type[BaseModel] = StartAgentSessionInput
    client: AgentPiClient
    mission: dict[str, Any]
    state: AgentSessionState

    def _run(
        self, max_seconds: float | None = None, idle_seconds: float | None = None
    ) -> str:
        if self.state.session_id is not None:
            return _json({"ok": True, "session_id": self.state.session_id})

        payload = dict(self.mission)
        if max_seconds is not None:
            payload["max_seconds"] = max_seconds
        if idle_seconds is not None:
            payload["idle_seconds"] = idle_seconds
        result = self.client.start(payload)
        self.state.session_id = _required_str(result, "session_id")
        return _json(result)


class ObserveInput(BaseModel):
    pass


class ObserveTool(BaseTool):
    name: str = "observe"
    description: str = "Read current agent session status and reflex clearance."
    args_schema: Type[BaseModel] = ObserveInput
    client: AgentPiClient
    state: AgentSessionState

    def _run(self) -> str:
        return _json(self.client.observe(_session_id(self.state)))


class DrivePulseInput(BaseModel):
    direction: str = Field(
        description="One of forward, backward, left, or right."
    )
    duration: float = Field(
        default=0.2, ge=0.0, le=0.5, description="Short pulse duration."
    )
    speed: float = Field(default=35.0, ge=0.0, le=50.0)
    reason: str = Field(description="Why this movement is needed.")


class DrivePulseTool(BaseTool):
    name: str = "drive_pulse"
    description: str = (
        "Request a short movement pulse. The Pi reflex gate may veto it."
    )
    args_schema: Type[BaseModel] = DrivePulseInput
    client: AgentPiClient
    state: AgentSessionState

    def _run(
        self, direction: str, duration: float = 0.2, speed: float = 35.0, reason: str = ""
    ) -> str:
        if direction not in {"forward", "backward", "left", "right"}:
            return _json({"ok": False, "error": "unsupported direction"})
        return _json(
            self.client.drive_pulse(
                _session_id(self.state),
                direction=direction,
                duration=duration,
                speed=speed,
                reason=reason,
            )
        )


class StopAgentSessionInput(BaseModel):
    reason: str = Field(default="manual")


class StopAgentSessionTool(BaseTool):
    name: str = "stop_agent_session"
    description: str = "Stop the active Pi-side agent session."
    args_schema: Type[BaseModel] = StopAgentSessionInput
    client: AgentPiClient
    state: AgentSessionState

    def _run(self, reason: str = "manual") -> str:
        if self.state.session_id is None:
            return _json({"ok": True, "already_stopped": True})
        session_id = self.state.session_id
        self.state.session_id = None
        return _json(self.client.stop(session_id, reason=reason))


class RecordInspectionResultInput(BaseModel):
    state: str = Field(description="Observed final state or outcome.")
    confidence: float = Field(ge=0.0, le=1.0)
    summary: str
    evidence: list[str] = Field(default_factory=list)
    needs_followup: bool = False


class RecordInspectionResultTool(BaseTool):
    name: str = "record_inspection_result"
    description: str = "Record final mission result for logs and scheduler output."
    args_schema: Type[BaseModel] = RecordInspectionResultInput
    mission: dict[str, Any]
    state: AgentSessionState

    def _run(
        self,
        state: str,
        confidence: float,
        summary: str,
        evidence: list[str] | None = None,
        needs_followup: bool = False,
    ) -> str:
        result = {
            "mission": self.mission,
            "state": state,
            "confidence": confidence,
            "summary": summary,
            "evidence": evidence or [],
            "needs_followup": needs_followup,
        }
        self.state.result = result
        return _json({"ok": True, "result": result})


def _session_id(state: AgentSessionState) -> str:
    if state.session_id is None:
        raise RuntimeError("agent session has not been started")
    return state.session_id


def _required_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"response missing {key}")
    return value


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True)
