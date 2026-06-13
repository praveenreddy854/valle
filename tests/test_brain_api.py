from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


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


class BrainApiRunsAndMissionsTest(unittest.TestCase):
    def setUp(self) -> None:
        try:
            from valle.brain.api import create_app
        except ModuleNotFoundError as exc:
            if exc.name == "flask":
                self.skipTest("Flask is not installed")
            raise
        from valle.brain.scheduler import MissionScheduler

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        env = patch.dict(
            "os.environ", {"VALLE_RUNS_FILE": str(tmp / "runs.jsonl")}
        )
        env.start()
        self.addCleanup(env.stop)
        self.run_mission = Mock()
        scheduler = MissionScheduler(tmp / "missions.json", self.run_mission)
        self.client = create_app(scheduler).test_client()

    def test_runs_endpoints(self) -> None:
        from valle.brain.runs import RunStore

        RunStore.from_env().record({"run_id": "r1", "status": "completed"})

        listed = self.client.get("/runs")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(
            listed.get_json()["runs"], [{"run_id": "r1", "status": "completed"}]
        )

        found = self.client.get("/runs/r1")
        self.assertEqual(found.status_code, 200)
        self.assertEqual(found.get_json()["run"]["run_id"], "r1")

        self.assertEqual(self.client.get("/runs/nope").status_code, 404)

    def test_mission_crud(self) -> None:
        created = self.client.post(
            "/missions",
            json={"schedule": "22:00", "goal": "check the back door lock"},
        )
        self.assertEqual(created.status_code, 201)
        mission = created.get_json()["mission"]
        self.assertEqual(mission["schedule"], "22:00")
        self.assertEqual(mission["mission"]["goal"], "check the back door lock")

        listed = self.client.get("/missions")
        self.assertEqual(len(listed.get_json()["missions"]), 1)

        removed = self.client.delete(f"/missions/{mission['id']}")
        self.assertEqual(removed.status_code, 200)
        self.assertEqual(self.client.get("/missions").get_json()["missions"], [])
        self.assertEqual(
            self.client.delete(f"/missions/{mission['id']}").status_code, 404
        )

    def test_mission_validation_errors(self) -> None:
        no_schedule = self.client.post("/missions", json={"goal": "x"})
        self.assertEqual(no_schedule.status_code, 400)

        bad_schedule = self.client.post(
            "/missions", json={"schedule": "9pm", "goal": "x"}
        )
        self.assertEqual(bad_schedule.status_code, 400)

        no_goal = self.client.post("/missions", json={"schedule": "21:00"})
        self.assertEqual(no_goal.status_code, 400)


if __name__ == "__main__":
    unittest.main()
