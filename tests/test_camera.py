from __future__ import annotations

import threading
import time
import unittest
import urllib.request
from http.server import HTTPServer
from unittest.mock import Mock

from valle.camera import CameraPower, _make_handler, _max_seconds, _StreamingOutput, start_camera_server


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


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

    def test_max_seconds_query(self) -> None:
        self.assertEqual(_max_seconds("max_seconds=60"), 60.0)
        self.assertIsNone(_max_seconds(""))
        self.assertIsNone(_max_seconds("max_seconds=abc"))
        self.assertIsNone(_max_seconds("max_seconds=0"))


class CameraPowerTest(unittest.TestCase):
    def _power(self, driving: bool = False):
        self.start, self.stop, self.clock = Mock(), Mock(), _Clock()
        self.driving = driving
        return CameraPower(
            start=self.start, stop=self.stop, idle_seconds=60,
            is_driving=lambda: self.driving, clock=self.clock,
        )

    def test_off_at_startup(self) -> None:
        power = self._power()
        power.tick()
        self.assertFalse(power.recording)
        self.start.assert_not_called()

    def test_client_turns_on_then_off_after_idle(self) -> None:
        power = self._power()
        power.acquire()
        power.acquire()  # second client does not restart the camera
        self.assertEqual(self.start.call_count, 1)
        self.clock.now = 300
        power.tick()  # clients still connected: stays on
        self.assertTrue(power.recording)
        power.release()
        power.release()
        self.clock.now = 359
        power.tick()
        self.assertTrue(power.recording)
        self.clock.now = 360
        power.tick()
        self.assertFalse(power.recording)
        self.stop.assert_called_once()

    def test_driving_turns_on_and_holds_for_idle_period(self) -> None:
        power = self._power(driving=True)
        power.tick()
        self.assertTrue(power.recording)
        self.clock.now = 10
        power.tick()
        self.driving = False
        self.clock.now = 69
        power.tick()
        self.assertTrue(power.recording)
        self.clock.now = 70
        power.tick()
        self.assertFalse(power.recording)

    def test_reacquire_after_off_restarts(self) -> None:
        power = self._power()
        power.acquire()
        power.release()
        self.clock.now = 60
        power.tick()
        power.acquire()
        self.assertEqual(self.start.call_count, 2)


class StreamCapTest(unittest.TestCase):
    def test_capped_stream_ends_and_releases_camera(self) -> None:
        output = _StreamingOutput()
        power = CameraPower(start=Mock(), stop=Mock(), idle_seconds=60)
        server = HTTPServer(("127.0.0.1", 0), _make_handler(output, power))
        threading.Thread(target=server.handle_request, daemon=True).start()

        def feed() -> None:
            for _ in range(20):
                output.write(b"\xff\xd8jpeg\xff\xd9")
                time.sleep(0.05)

        threading.Thread(target=feed, daemon=True).start()
        url = f"http://127.0.0.1:{server.server_port}/stream.mjpg?max_seconds=0.3"
        started = time.monotonic()
        with urllib.request.urlopen(url, timeout=5) as response:
            body = response.read()
        server.server_close()

        self.assertLess(time.monotonic() - started, 3)
        self.assertIn(b"--FRAME", body)
        self.assertTrue(power.recording)  # stays on for the idle period...
        self.assertEqual(power.clients, 0)  # ...but the client was released


if __name__ == "__main__":
    unittest.main()
