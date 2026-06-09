from __future__ import annotations

import unittest
from unittest.mock import patch


class BrainApiTest(unittest.TestCase):
    def test_agent_run_requires_goal(self) -> None:
        try:
            from valle.brain.api import create_app
        except ModuleNotFoundError as exc:
            if exc.name == "flask":
                self.skipTest("Flask is not installed")
            raise

        client = create_app().test_client()
        response = client.post("/agent/run", json={})

        self.assertEqual(response.status_code, 400)
        payload = response.get_json()
        assert payload is not None
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "mission goal is required")

    def test_agent_run_returns_runner_result(self) -> None:
        try:
            from valle.brain.api import create_app
        except ModuleNotFoundError as exc:
            if exc.name == "flask":
                self.skipTest("Flask is not installed")
            raise

        with patch("valle.brain.api.run_agent", return_value={"state": "done"}):
            client = create_app().test_client()
            response = client.post(
                "/agent/run", json={"goal": "check the back door lock"}
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        assert payload is not None
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["result"], {"state": "done"})


if __name__ == "__main__":
    unittest.main()
