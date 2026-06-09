from __future__ import annotations

import unittest

from valle.controller import SessionNotActiveError, ValleController
from valle.motors import MockMotorDriver


def _make_controller() -> tuple[ValleController, MockMotorDriver]:
    driver = MockMotorDriver()
    controller = ValleController(
        driver,
        default_speed_percent=60,
        default_duration_seconds=0.05,
        max_duration_seconds=0.2,
        autopilot_max_seconds=1.0,
        autopilot_idle_seconds=0.5,
    )
    return controller, driver


def _mission() -> dict[str, object]:
    return {
        "goal": "check the back door lock",
        "task": "nightly_door_lock_check",
        "skill": "check_door_locks",
        "targets": ["back_door"],
    }


class AgentSessionControllerTest(unittest.TestCase):
    def test_agent_intent_without_reflex_reading_is_vetoed(self) -> None:
        controller, driver = _make_controller()
        session = controller.start_agent_session(mission=_mission())

        result = controller.agent_intent(
            session["session_id"], direction="forward", duration_seconds=0.05
        )

        self.assertFalse(result["executed"])
        self.assertEqual(result["reflex"]["reason"], "no_reflex_reading")
        self.assertEqual(driver.current_action, "stopped")
        controller.stop_agent_session(session["session_id"])

    def test_agent_intent_executes_when_reflex_allows_direction(self) -> None:
        controller, driver = _make_controller()
        session = controller.start_agent_session(mission=_mission())
        controller.update_reflex(left=0.3, center=0.2, right=0.4, source="test")

        result = controller.agent_intent(
            session["session_id"],
            direction="forward",
            duration_seconds=0.05,
            speed_percent=40,
            reason="approach inspection spot",
        )

        self.assertTrue(result["executed"])
        self.assertEqual(result["direction"], "forward")
        self.assertEqual(result["reflex"]["reason"], "center_clear")
        self.assertEqual(driver.current_action, "forward")
        self.assertAlmostEqual(driver.current_speed, 0.4, places=2)
        controller.stop_agent_session(session["session_id"])

    def test_agent_intent_is_vetoed_when_direction_is_blocked(self) -> None:
        controller, driver = _make_controller()
        session = controller.start_agent_session(mission=_mission())
        controller.update_reflex(left=0.3, center=0.8, right=0.7, source="test")

        result = controller.agent_intent(
            session["session_id"], direction="forward", duration_seconds=0.05
        )

        self.assertFalse(result["executed"])
        self.assertEqual(result["reflex"]["reason"], "center_blocked")
        self.assertEqual(result["reflex"]["recommended_direction"], "left")
        self.assertEqual(driver.current_action, "stopped")
        controller.stop_agent_session(session["session_id"])

    def test_agent_session_is_not_valid_for_autopilot_drive(self) -> None:
        controller, _ = _make_controller()
        session = controller.start_agent_session(mission=_mission())
        with self.assertRaises(SessionNotActiveError):
            controller.autopilot_drive(session["session_id"], direction="forward")
        controller.stop_agent_session(session["session_id"])

    def test_status_uses_agent_key_during_agent_session(self) -> None:
        controller, _ = _make_controller()
        session = controller.start_agent_session(mission=_mission())
        status = controller.status()
        self.assertIn("agent", status)
        self.assertNotIn("autopilot", status)
        self.assertEqual(status["agent"]["session_id"], session["session_id"])
        self.assertEqual(status["agent"]["mission"]["goal"], "check the back door lock")
        controller.stop_agent_session(session["session_id"])

    def test_agent_session_requires_mission_goal(self) -> None:
        controller, _ = _make_controller()
        with self.assertRaises(ValueError):
            controller.start_agent_session(mission={})


class AgentSessionAppTest(unittest.TestCase):
    def test_agent_routes_start_update_reflex_and_execute_intent(self) -> None:
        try:
            from valle.app import create_app
        except ModuleNotFoundError as exc:
            if exc.name == "flask":
                self.skipTest("Flask is not installed")
            raise

        controller, driver = _make_controller()
        app = create_app(controller=controller)
        client = app.test_client()

        start_response = client.post("/agent/start", json=_mission())
        self.assertEqual(start_response.status_code, 201)
        start = start_response.get_json()
        assert start is not None
        session_id = start["session_id"]
        self.assertEqual(start["mission"]["goal"], "check the back door lock")

        reflex = client.post(
            "/agent/reflex",
            json={"left": 0.2, "center": 0.2, "right": 0.2, "source": "test"},
        )
        self.assertEqual(reflex.status_code, 200)

        intent = client.post(
            f"/agent/{session_id}/intent",
            json={
                "type": "drive_pulse",
                "direction": "forward",
                "duration": 0.05,
                "speed": 35,
            },
        )
        payload = intent.get_json()
        assert payload is not None
        self.assertEqual(intent.status_code, 200)
        self.assertTrue(payload["executed"])
        self.assertEqual(driver.current_action, "forward")

        client.post(f"/agent/{session_id}/intent", json={"type": "stop"})


if __name__ == "__main__":
    unittest.main()
