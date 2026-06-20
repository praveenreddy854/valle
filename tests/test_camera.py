from __future__ import annotations

import unittest

from valle.camera import start_camera_server


class CameraTest(unittest.TestCase):
    def test_start_returns_none_without_picamera2(self) -> None:
        # picamera2 is Pi-only; off the Pi the camera is disabled gracefully so
        # the Pi app can still run the motor API.
        try:
            import picamera2  # noqa: F401
        except ImportError:
            self.assertIsNone(start_camera_server())
        else:  # pragma: no cover - only on a real Pi
            self.skipTest("picamera2 is installed; skipping no-camera path")


if __name__ == "__main__":
    unittest.main()
