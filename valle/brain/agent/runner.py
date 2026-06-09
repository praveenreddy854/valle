from __future__ import annotations

import json
import logging
import signal
from typing import Any

from .config import AgentConfig, load_mission

log = logging.getLogger("valle.brain.agent")


def run_agent(config: AgentConfig, mission: dict[str, Any]) -> Any:
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
        DrivePulseTool,
        ObserveTool,
        RecordInspectionResultTool,
        StartAgentSessionTool,
        StopAgentSessionTool,
    )

    client = AgentPiClient(config.pi_base_url)
    client.validate_agent_api()
    state = AgentSessionState()
    tools = [
        StartAgentSessionTool(client=client, mission=mission, state=state),
        ObserveTool(client=client, state=state),
        DrivePulseTool(client=client, state=state),
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
            "go through the reflex-gated drive_pulse tool. If movement is vetoed, "
            "observe and choose a safer step or report uncertainty."
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
            "Start an agent session, observe the robot status, request only "
            "short reflex-gated drive pulses when movement is needed, record "
            "the inspection result, stop the session, and then provide a concise "
            "final summary. If evidence is insufficient, record an uncertain "
            "result instead of guessing."
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
    )

    try:
        return crew.kickoff(inputs=_crew_inputs(mission))
    finally:
        if state.session_id is not None:
            try:
                client.stop(state.session_id, reason="agent_runner_exit")
            except Exception:
                log.exception("failed to stop active agent session")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = AgentConfig.from_env()
    mission = load_mission()

    def _handle_signal(signum: int, frame: object) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    result = run_agent(config, mission)
    print(json.dumps(_result_to_jsonable(result), indent=2, sort_keys=True))


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
