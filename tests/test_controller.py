from __future__ import annotations

import time
import unittest

from valle.controller import ValleController, normalize_command
from valle.motors import MockMotorDriver


class ValleControllerTest(unittest.TestCase):
    def test_forward_command_auto_stops_after_duration(self) -> None:
        driver = MockMotorDriver()
        controller = ValleController(
            driver,
            default_speed_percent=60,
            default_duration_seconds=0.05,
            max_duration_seconds=0.05,
        )

        result = controller.run("forward", speed_percent=120, duration_seconds=1)

        self.assertEqual(result["active_command"], "forward")
        self.assertEqual(result["speed_percent"], 100)
        self.assertEqual(driver.current_action, "forward")
        self.assertEqual(driver.current_speed, 1.0)

        time.sleep(0.08)

        status = controller.status()
        self.assertEqual(status["active_command"], "stopped")
        self.assertEqual(status["last_stop_reason"], "auto_timeout")
        self.assertEqual(driver.current_action, "stopped")

    def test_stop_overrides_active_command(self) -> None:
        driver = MockMotorDriver()
        controller = ValleController(driver, default_duration_seconds=5)

        controller.run("left", speed_percent=40)
        result = controller.run("stop")

        self.assertEqual(result["active_command"], "stopped")
        self.assertEqual(result["last_stop_reason"], "manual")
        self.assertEqual(driver.current_action, "stopped")

    def test_aliases_normalize_to_canonical_commands(self) -> None:
        self.assertEqual(normalize_command("turn-left"), "left")
        self.assertEqual(normalize_command("backwards"), "backward")
        self.assertEqual(normalize_command("brake"), "stop")


if __name__ == "__main__":
    unittest.main()
