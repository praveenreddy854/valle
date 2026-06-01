from __future__ import annotations

import json
import threading
import unittest

from valle.bridge import (
    BrainBridge,
    BrainOfflineError,
    BrainTimeoutError,
)


class FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.closed = False

    def send(self, payload: str) -> None:
        if self.closed:
            raise ConnectionError("closed")
        self.sent.append(payload)

    def close(self) -> None:
        self.closed = True


class BrainBridgeTest(unittest.TestCase):
    def test_find_without_attached_brain_raises_offline(self) -> None:
        bridge = BrainBridge()
        with self.assertRaises(BrainOfflineError):
            bridge.find(object_query="toy", timeout_seconds=0.1)

    def test_find_round_trips_via_response_handler(self) -> None:
        bridge = BrainBridge()
        ws = FakeWebSocket()
        bridge.attach(ws)

        result_box: dict = {}

        def caller() -> None:
            result_box["value"] = bridge.find(
                object_query="toy", timeout_seconds=2.0
            )

        thread = threading.Thread(target=caller)
        thread.start()

        # Wait for the find call to enqueue its request before responding.
        for _ in range(100):
            if ws.sent:
                break
            threading.Event().wait(0.01)
        self.assertTrue(ws.sent, "find did not dispatch over the WebSocket")

        request = json.loads(ws.sent[0])
        self.assertEqual(request["type"], "find")
        self.assertEqual(request["object"], "toy")

        bridge.handle_response(
            json.dumps(
                {
                    "id": request["id"],
                    "type": "find_result",
                    "found": True,
                    "results": [{"score": 0.5, "label": "toy"}],
                }
            )
        )
        thread.join(timeout=2.0)

        self.assertTrue(result_box["value"]["found"])
        self.assertEqual(result_box["value"]["results"][0]["label"], "toy")

    def test_find_times_out_when_no_response(self) -> None:
        bridge = BrainBridge()
        bridge.attach(FakeWebSocket())
        with self.assertRaises(BrainTimeoutError):
            bridge.find(object_query="toy", timeout_seconds=0.1)

    def test_detach_unblocks_pending_callers(self) -> None:
        bridge = BrainBridge()
        ws = FakeWebSocket()
        bridge.attach(ws)
        errors: list[Exception] = []

        def caller() -> None:
            try:
                bridge.find(object_query="toy", timeout_seconds=5.0)
            except Exception as exc:
                errors.append(exc)

        thread = threading.Thread(target=caller)
        thread.start()
        for _ in range(100):
            if ws.sent:
                break
            threading.Event().wait(0.01)
        bridge.detach(ws)
        thread.join(timeout=2.0)
        self.assertEqual(len(errors), 1)

    def test_attach_replaces_previous_connection(self) -> None:
        bridge = BrainBridge()
        first = FakeWebSocket()
        second = FakeWebSocket()
        bridge.attach(first)
        bridge.attach(second)
        self.assertTrue(first.closed)
        self.assertFalse(second.closed)

    def test_seek_dispatches_seek_typed_message(self) -> None:
        bridge = BrainBridge()
        ws = FakeWebSocket()
        bridge.attach(ws)
        result_box: dict = {}

        def caller() -> None:
            result_box["value"] = bridge.seek(
                object_query="toy", max_seconds=30, timeout_seconds=2.0
            )

        thread = threading.Thread(target=caller)
        thread.start()
        for _ in range(100):
            if ws.sent:
                break
            threading.Event().wait(0.01)
        request = json.loads(ws.sent[0])
        self.assertEqual(request["type"], "seek")
        self.assertEqual(request["object"], "toy")
        self.assertEqual(request["max_seconds"], 30)
        bridge.handle_response(
            json.dumps(
                {
                    "id": request["id"],
                    "type": "seek_result",
                    "found": True,
                    "score": 0.5,
                }
            )
        )
        thread.join(timeout=2.0)
        self.assertTrue(result_box["value"]["found"])


if __name__ == "__main__":
    unittest.main()
