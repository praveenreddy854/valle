from __future__ import annotations

import json
import logging
import os
import signal
from datetime import datetime
from typing import Any

from .config import AgentConfig, load_mission
from ..evidence import EvidenceStore
from ..notify import notify_mission_result
from ..runs import RunStore, new_run_id
from ...observability import get_tracer, setup_observability

log = logging.getLogger("valle.brain.agent")


def run_agent(config: AgentConfig, mission: dict[str, Any]) -> Any:
    _prepare_crewai_runtime()
    try:
        from crewai import Agent, Crew, LLM, Process, Task
    except ImportError as exc:
        raise RuntimeError(
            "CrewAI is required. Install with: pip install -e '.[agent]'"
        ) from exc

    config.validate()
    from .client import AgentPiClient
    from .tools import (
        AgentSessionState,
        CaptureEvidenceTool,
        DrivePulseTool,
        FindObjectTool,
        ObserveTool,
        RecordInspectionResultTool,
        StartAgentSessionTool,
        StopAgentSessionTool,
    )

    client = AgentPiClient(config.pi_base_url)
    client.validate_agent_api()
    perception = _build_perception(client)
    run_id = new_run_id()
    runs = RunStore.from_env()
    evidence = EvidenceStore.from_env(run_id)
    state = AgentSessionState()
    tools = [
        StartAgentSessionTool(client=client, mission=mission, state=state),
        ObserveTool(client=client, state=state),
        DrivePulseTool(client=client, state=state),
        FindObjectTool(perception=perception),
        CaptureEvidenceTool(perception=perception, evidence=evidence),
        StopAgentSessionTool(client=client, state=state),
        RecordInspectionResultTool(mission=mission, state=state),
    ]

    llm = _build_llm(LLM, config)
    inspector = Agent(
        role="Valle scheduled inspection agent",
        goal="Complete the requested Valle inspection while respecting robot safety.",
        backstory=(
            "You operate a small Raspberry Pi robot car. You can plan inspection "
            "missions, but you cannot drive motors directly. All movement must "
            "go through the reflex-gated drive_pulse tool. A perception loop "
            "keeps the robot's reflex gate updated while you work; if movement "
            "is vetoed, observe and choose a safer step or report uncertainty. "
            "Use find_object to check whether a target is visible and whether "
            "it sits left, center, or right in the camera view, then pivot "
            "toward it before driving."
        ),
        tools=tools,
        llm=llm,
        verbose=True,
        allow_delegation=False,
        max_iter=config.max_steps,
    )

    task = Task(
        description=(
            "Mission: {goal}\n"
            "Task: {task}\n"
            "Skill: {skill}\n"
            "Targets: {targets}\n\n"
            "Start an agent session, observe the robot status, use find_object "
            "to look for mission targets, and request only short reflex-gated "
            "drive pulses when movement is needed (pivot toward the target's "
            "reported position, then approach). Save camera frames with "
            "capture_evidence at inspection viewpoints and reference the saved "
            "files in the result. Record the inspection result, stop the "
            "session, and then provide a concise final summary. If evidence is "
            "insufficient, record an uncertain result instead of guessing."
        ),
        expected_output=(
            "A concise mission summary that includes the final state, confidence, "
            "evidence, and whether follow-up is needed."
        ),
        agent=inspector,
    )

    crew = Crew(
        agents=[inspector],
        tasks=[task],
        process=Process.sequential,
        verbose=True,
        tracing=False,
    )

    with get_tracer(__name__).start_as_current_span("crew.agent_mission") as span:
        span.set_attribute("mission.goal", mission["goal"])
        span.set_attribute("mission.skill", str(mission.get("skill", "")))
        span.set_attribute("run.id", run_id)
        started_at = datetime.now().isoformat(timespec="seconds")
        error: str | None = None
        perception.start()
        try:
            return crew.kickoff(inputs=_crew_inputs(mission))
        except Exception as exc:
            error = str(exc)
            raise
        finally:
            if state.session_id is not None:
                try:
                    client.stop(state.session_id, reason="agent_runner_exit")
                except Exception:
                    log.exception("failed to stop active agent session")
            perception.stop()
            record = {
                "run_id": run_id,
                "mission": mission,
                "started_at": started_at,
                "ended_at": datetime.now().isoformat(timespec="seconds"),
                "status": "failed" if error else "completed",
                "error": error,
                "result": state.result,
                "evidence": evidence.items(),
            }
            try:
                runs.record(record)
                notify_mission_result(record)
            except Exception:
                log.exception("failed to record mission run %s", run_id)


def main() -> None:
    setup_observability("valle-brain-agent")
    config = AgentConfig.from_env()
    mission = load_mission()

    def _handle_signal(signum: int, frame: object) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    result = run_agent(config, mission)
    print(json.dumps(_result_to_jsonable(result), indent=2, sort_keys=True))


def _build_perception(client: Any) -> Any:
    try:
        from .perception import build_agent_perception
    except ImportError as exc:
        raise RuntimeError(
            "agent perception requires the brain extras (depth + detector). "
            "Run `make install-brain-api` or "
            "`.venv/bin/pip install -e '.[brain,agent]'`."
        ) from exc
    return build_agent_perception(client)


def _crew_inputs(mission: dict[str, Any]) -> dict[str, str]:
    return {
        "goal": str(mission.get("goal", "")),
        "task": str(mission.get("task", "")),
        "skill": str(mission.get("skill", "")),
        "targets": ", ".join(mission.get("targets") or []),
    }


def _build_llm(llm_class: Any, config: AgentConfig) -> Any:
    if _uses_openai_v1_endpoint(config.azure_endpoint):
        return llm_class(
            model=config.azure_deployment,
            provider="openai",
            api_key=config.azure_api_key,
            base_url=config.azure_endpoint,
            temperature=0.2,
        )

    try:
        return llm_class(
            model=f"azure/{config.azure_deployment}",
            api_key=config.azure_api_key,
            endpoint=config.azure_endpoint,
            api_version=config.azure_api_version,
            temperature=0.2,
        )
    except ImportError as exc:
        raise RuntimeError(
            "CrewAI Azure provider dependencies are missing. Run "
            "`make install-agent` or "
            "`.venv/bin/pip install -e '.[agent]'`."
        ) from exc


def _prepare_crewai_runtime() -> None:
    _set_env_default("CREWAI_TRACING_ENABLED", "false")
    _set_env_default("CREWAI_DISABLE_TELEMETRY", "true")
    _set_env_default("CREWAI_DISABLE_TRACKING", "true")


def _set_env_default(name: str, value: str) -> None:
    if not os.getenv(name):
        os.environ[name] = value


def _uses_openai_v1_endpoint(endpoint: str) -> bool:
    return "/openai/v1" in endpoint.rstrip("/")


def _result_to_jsonable(result: Any) -> Any:
    if isinstance(result, dict):
        return result
    if hasattr(result, "to_dict"):
        return result.to_dict()
    if hasattr(result, "raw"):
        return {"raw": result.raw}
    return {"raw": str(result)}
