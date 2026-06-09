from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from valle.brain.agent.config import AgentConfig, load_mission, normalize_mission


class AgentConfigTest(unittest.TestCase):
    def test_load_mission_from_cli_goal(self) -> None:
        mission = load_mission(["check", "the", "back", "door"])
        self.assertEqual(mission["goal"], "check the back door")

    def test_load_mission_from_json_env(self) -> None:
        with patch.dict(
            os.environ,
            {"VALLE_AGENT_MISSION_JSON": '{"goal":"seek toy","targets":["toy"]}'},
        ):
            mission = load_mission([])
        self.assertEqual(mission["goal"], "seek toy")
        self.assertEqual(mission["targets"], ["toy"])

    def test_normalize_mission_requires_goal(self) -> None:
        with self.assertRaises(ValueError):
            normalize_mission({})

    def test_config_builds_azure_endpoint_from_resource_name(self) -> None:
        with patch.dict(
            os.environ,
            {
                "AZURE_RESOURCE_NAME": "valle-ai",
                "AZURE_OPENAI_DEPLOYMENT": "gpt-4o-mini",
                "AZURE_API_KEY": "key",
                "VALLE_DOTENV_PATH": "/tmp/valle-test-missing.env",
            },
            clear=True,
        ):
            config = AgentConfig.from_env()
        self.assertEqual(
            config.azure_endpoint,
            "https://valle-ai.openai.azure.com/openai/deployments/gpt-4o-mini",
        )
        config.validate()

    def test_dotenv_overrides_empty_exported_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text(
                "\n".join(
                    [
                        "AZURE_RESOURCE_NAME=valle-ai",
                        "AZURE_API_KEY=key",
                        "AZURE_OPENAI_DEPLOYMENT=gpt-4o-mini",
                    ]
                ),
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {
                    "VALLE_DOTENV_PATH": str(env_path),
                    "AZURE_API_KEY": "",
                    "AZURE_OPENAI_DEPLOYMENT": "",
                },
                clear=True,
            ):
                config = AgentConfig.from_env()

        self.assertEqual(config.azure_api_key, "key")
        self.assertEqual(config.azure_deployment, "gpt-4o-mini")
        config.validate()

    def test_config_accepts_common_azure_openai_aliases(self) -> None:
        with patch.dict(
            os.environ,
            {
                "AZURE_OPENAI_API_KEY": "key",
                "AZURE_OPENAI_DEPLOYMENT_NAME": "gpt-4o-mini",
                "AZURE_OPENAI_RESOURCE": "valle-ai",
                "VALLE_DOTENV_PATH": "/tmp/valle-test-missing.env",
            },
            clear=True,
        ):
            config = AgentConfig.from_env()

        self.assertEqual(config.azure_api_key, "key")
        self.assertEqual(config.azure_deployment, "gpt-4o-mini")
        self.assertEqual(
            config.azure_endpoint,
            "https://valle-ai.openai.azure.com/openai/deployments/gpt-4o-mini",
        )


if __name__ == "__main__":
    unittest.main()
