from __future__ import annotations

import threading
import time
import unittest
from typing import Any

from valle.brain.loop import AutopilotController


class FakeBrain:
    """A Brain stand-in whose run() blocks until request_stop()."""

    def __init__(self) -> None:
        self.runs = 0
        self._stop = threading.Event()
        self.started = threading.Event()

    def run(self) -> None:
        self.runs += 1
        self._stop.clear()
        self.started.set()
        while not self._stop.is_set():
            self._stop.wait(0.01)

    def request_stop(self) -> None:
        self._stop.set()


def _wait_until(predicate: Any, timeout: float = 1.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)


class AutopilotControllerTest(unittest.TestCase):
    def test_start_stop_status_are_idempotent(self) -> None:
        brain = FakeBrain()
        ctrl = AutopilotController(build=lambda: brain)

        self.assertEqual(ctrl.status(), {"running": False})
        self.assertEqual(ctrl.start(), {"running": True, "changed": True})
        self.assertTrue(brain.started.wait(1.0))
        self.assertTrue(ctrl.is_running)
        self.assertEqual(ctrl.start(), {"running": True, "changed": False})

        self.assertEqual(ctrl.stop(), {"running": False, "changed": True})
        self.assertFalse(ctrl.is_running)
        self.assertEqual(ctrl.stop(), {"running": False, "changed": False})

    def test_one_brain_reused_across_cycles(self) -> None:
        brain = FakeBrain()
        ctrl = AutopilotController(build=lambda: brain)

        ctrl.start()
        brain.started.wait(1.0)
        ctrl.stop()
        brain.started.clear()
        ctrl.start()
        brain.started.wait(1.0)
        ctrl.stop()

        self.assertEqual(brain.runs, 2)

    def test_paused_stops_then_resumes_when_running(self) -> None:
        brain = FakeBrain()
        ctrl = AutopilotController(build=lambda: brain)
        ctrl.start()
        brain.started.wait(1.0)

        with ctrl.paused():
            self.assertFalse(ctrl.is_running)

        _wait_until(lambda: ctrl.is_running)
        self.assertTrue(ctrl.is_running)
        ctrl.stop()

    def test_paused_is_noop_when_not_running(self) -> None:
        brain = FakeBrain()
        ctrl = AutopilotController(build=lambda: brain)

        with ctrl.paused():
            self.assertFalse(ctrl.is_running)

        self.assertFalse(ctrl.is_running)
        self.assertEqual(brain.runs, 0)


class BrainApiAutopilotTest(unittest.TestCase):
    def setUp(self) -> None:
        try:
            from valle.brain.api import create_app
        except ModuleNotFoundError as exc:
            if exc.name == "flask":
                self.skipTest("Flask is not installed")
            raise
        self._create_app = create_app

    def test_autopilot_status_endpoint(self) -> None:
        client = self._create_app().test_client()
        response = client.get("/autopilot/status")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"ok": True, "running": False})

    def test_agent_run_pauses_autopilot_around_the_mission(self) -> None:
        from unittest.mock import patch

        events: list[str] = []

        class RecordingAutopilot(AutopilotController):
            def __init__(self) -> None:
                super().__init__(build=FakeBrain)

            def stop(self) -> dict[str, Any]:
                events.append("stop")
                return super().stop()

            def start(self) -> dict[str, Any]:
                events.append("start")
                return super().start()

        autopilot = RecordingAutopilot()
        autopilot.start()  # pretend autopilot is already driving
        _wait_until(lambda: autopilot.is_running)

        def fake_run_agent(config: Any, mission: dict[str, Any]) -> dict[str, Any]:
            events.append("mission")
            self.assertFalse(autopilot.is_running)  # paused during the task
            return {"state": "done"}

        with patch("valle.brain.api.run_agent", side_effect=fake_run_agent):
            client = self._create_app(autopilot=autopilot).test_client()
            response = client.post("/agent/run", json={"goal": "check the lock"})

        self.assertEqual(response.status_code, 200)
        # initial start, then pause (stop) -> mission -> resume (start)
        self.assertEqual(events, ["start", "stop", "mission", "start"])
        autopilot.stop()


if __name__ == "__main__":
    unittest.main()
