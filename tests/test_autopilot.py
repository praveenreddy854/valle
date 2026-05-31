from __future__ import annotations

import time
import unittest

from valle.controller import (
    SessionAlreadyActiveError,
    SessionNotActiveError,
    ValleController,
)
from valle.motors import MockMotorDriver


def _make_controller(
    *,
    autopilot_max_seconds: float = 1.0,
    autopilot_idle_seconds: float = 0.2,
    max_duration_seconds: float = 1.0,
) -> tuple[ValleController, MockMotorDriver]:
    driver = MockMotorDriver()
    controller = ValleController(
        driver,
        default_speed_percent=60,
        default_duration_seconds=0.05,
        max_duration_seconds=max_duration_seconds,
        autopilot_max_seconds=autopilot_max_seconds,
        autopilot_idle_seconds=autopilot_idle_seconds,
    )
    return controller, driver


class AutopilotSessionTest(unittest.TestCase):
    def test_start_returns_token_and_marks_session_active(self) -> None:
        controller, _ = _make_controller()
        result = controller.start_autopilot()
        self.assertIn("session_id", result)
        self.assertEqual(result["max_seconds"], 1.0)
        self.assertEqual(result["idle_seconds"], 0.2)
        self.assertEqual(controller.autopilot_session_id(), result["session_id"])
        controller.stop_autopilot(result["session_id"])

    def test_start_clamps_caller_overrides_to_env_max(self) -> None:
        controller, _ = _make_controller(
            autopilot_max_seconds=2.0, autopilot_idle_seconds=0.5
        )
        result = controller.start_autopilot(max_seconds=999, idle_seconds=999)
        self.assertEqual(result["max_seconds"], 2.0)
        self.assertEqual(result["idle_seconds"], 0.5)
        controller.stop_autopilot(result["session_id"])

    def test_cannot_start_two_sessions(self) -> None:
        controller, _ = _make_controller()
        first = controller.start_autopilot()
        with self.assertRaises(SessionAlreadyActiveError):
            controller.start_autopilot()
        controller.stop_autopilot(first["session_id"])

    def test_manual_run_rejected_during_session(self) -> None:
        controller, _ = _make_controller()
        session = controller.start_autopilot()
        with self.assertRaises(SessionAlreadyActiveError):
            controller.run("forward")
        controller.stop_autopilot(session["session_id"])

    def test_stop_ends_active_session(self) -> None:
        controller, driver = _make_controller()
        session = controller.start_autopilot()
        controller.autopilot_drive(
            session["session_id"], direction="forward", duration_seconds=0.05
        )
        result = controller.stop(reason="manual")
        self.assertIsNone(controller.autopilot_session_id())
        self.assertEqual(result["last_stop_reason"], "autopilot_manual")
        self.assertEqual(driver.current_action, "stopped")

    def test_autopilot_drive_requires_matching_session_id(self) -> None:
        controller, _ = _make_controller()
        session = controller.start_autopilot()
        with self.assertRaises(SessionNotActiveError):
            controller.autopilot_drive("wrong-token", direction="forward")
        controller.stop_autopilot(session["session_id"])

    def test_autopilot_drive_pulses_motors(self) -> None:
        controller, driver = _make_controller()
        session = controller.start_autopilot()
        controller.autopilot_drive(
            session["session_id"],
            direction="forward",
            duration_seconds=0.05,
            speed_percent=50,
        )
        self.assertEqual(driver.current_action, "forward")
        self.assertAlmostEqual(driver.current_speed, 0.5, places=2)
        controller.stop_autopilot(session["session_id"])

    def test_idle_watchdog_fires_when_only_pivoting(self) -> None:
        controller, _ = _make_controller(autopilot_idle_seconds=0.15)
        session = controller.start_autopilot()
        for _ in range(3):
            controller.autopilot_drive(
                session["session_id"], direction="left", duration_seconds=0.02
            )
            time.sleep(0.04)
        time.sleep(0.25)
        self.assertIsNone(controller.autopilot_session_id())
        status = controller.status()
        self.assertEqual(status["last_stop_reason"], "autopilot_idle")

    def test_forward_resets_idle_watchdog(self) -> None:
        controller, _ = _make_controller(autopilot_idle_seconds=0.2)
        session = controller.start_autopilot()
        for _ in range(4):
            controller.autopilot_drive(
                session["session_id"], direction="forward", duration_seconds=0.02
            )
            time.sleep(0.08)
        self.assertEqual(controller.autopilot_session_id(), session["session_id"])
        controller.stop_autopilot(session["session_id"])

    def test_hard_cap_ends_session(self) -> None:
        controller, _ = _make_controller(
            autopilot_max_seconds=0.15, autopilot_idle_seconds=10.0
        )
        session = controller.start_autopilot()
        controller.autopilot_drive(
            session["session_id"], direction="forward", duration_seconds=0.02
        )
        time.sleep(0.25)
        self.assertIsNone(controller.autopilot_session_id())
        status = controller.status()
        self.assertEqual(status["last_stop_reason"], "autopilot_hard_cap")

    def test_stop_autopilot_with_blind_reason(self) -> None:
        controller, _ = _make_controller()
        session = controller.start_autopilot()
        controller.stop_autopilot(session["session_id"], reason="blind")
        status = controller.status()
        self.assertEqual(status["last_stop_reason"], "autopilot_blind")

    def test_status_includes_autopilot_block_during_session(self) -> None:
        controller, _ = _make_controller()
        session = controller.start_autopilot()
        status = controller.status()
        self.assertIn("autopilot", status)
        self.assertEqual(status["autopilot"]["session_id"], session["session_id"])
        controller.stop_autopilot(session["session_id"])


if __name__ == "__main__":
    unittest.main()
