from __future__ import annotations

import os
import unittest
from typing import Any
from unittest.mock import Mock, patch

from valle.brain.agent.client import AgentPiClient, AgentPiClientError
from valle.brain.agent.config import AgentConfig
from valle.brain.agent.runner import _build_llm, _prepare_crewai_runtime


class FakeLLM:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


class AgentRunnerTest(unittest.TestCase):
    def test_openai_v1_endpoint_uses_openai_compatible_provider(self) -> None:
        config = AgentConfig(
            azure_api_key="key",
            azure_deployment="gpt-5.4",
            azure_endpoint="https://example.services.ai.azure.com/openai/v1",
        )

        llm = _build_llm(FakeLLM, config)

        self.assertEqual(llm.kwargs["model"], "gpt-5.4")
        self.assertEqual(llm.kwargs["provider"], "openai")
        self.assertEqual(
            llm.kwargs["base_url"],
            "https://example.services.ai.azure.com/openai/v1",
        )
        self.assertNotIn("api_version", llm.kwargs)

    def test_deployments_endpoint_uses_native_azure_provider(self) -> None:
        config = AgentConfig(
            azure_api_key="key",
            azure_deployment="gpt-4o-mini",
            azure_endpoint=(
                "https://valle-ai.openai.azure.com/openai/deployments/gpt-4o-mini"
            ),
            azure_api_version="2024-06-01",
        )

        llm = _build_llm(FakeLLM, config)

        self.assertEqual(llm.kwargs["model"], "azure/gpt-4o-mini")
        self.assertEqual(llm.kwargs["endpoint"], config.azure_endpoint)
        self.assertEqual(llm.kwargs["api_version"], "2024-06-01")

    def test_pi_agent_api_probe_accepts_missing_goal_400(self) -> None:
        response = Mock(status_code=400, text='{"error":"mission goal is required"}')
        requests = Mock(post=Mock(return_value=response))
        with patch("valle.brain.agent.client._requests", return_value=requests):
            AgentPiClient("http://pi.local:8080").validate_agent_api()

    def test_pi_agent_api_probe_explains_404(self) -> None:
        response = Mock(status_code=404, text="not found")
        requests = Mock(post=Mock(return_value=response))
        with patch("valle.brain.agent.client._requests", return_value=requests):
            with self.assertRaises(AgentPiClientError) as raised:
                AgentPiClient("http://127.0.0.1:8090").validate_agent_api()

        self.assertIn("VALLE_PI_BASE_URL", str(raised.exception))

    def test_prepare_crewai_runtime_disables_crewai_external_tracing_by_default(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "CREWAI_TRACING_ENABLED": "",
                "CREWAI_DISABLE_TELEMETRY": "",
                "CREWAI_DISABLE_TRACKING": "",
            },
            clear=True,
        ):
            _prepare_crewai_runtime()

            self.assertEqual(os.environ["CREWAI_TRACING_ENABLED"], "false")
            self.assertEqual(os.environ["CREWAI_DISABLE_TELEMETRY"], "true")
            self.assertEqual(os.environ["CREWAI_DISABLE_TRACKING"], "true")

        with patch.dict("os.environ", {}, clear=True):
            _prepare_crewai_runtime()

            self.assertEqual(os.environ["CREWAI_TRACING_ENABLED"], "false")
            self.assertEqual(os.environ["CREWAI_DISABLE_TELEMETRY"], "true")
            self.assertEqual(os.environ["CREWAI_DISABLE_TRACKING"], "true")


if __name__ == "__main__":
    unittest.main()
