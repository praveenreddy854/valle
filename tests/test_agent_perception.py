from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import Mock, patch

import numpy as np

from valle.brain.agent.client import AgentPiClient
from valle.brain.agent.perception import AgentPerception, _horizontal_position
from valle.brain.frames import Frame
from valle.brain.policy import StripLayout


LAYOUT = StripLayout(
    left_strip_end=0.33, right_strip_start=0.67, top_crop=0.0, bottom_crop=0.0
)


class FakeFrames:
    def __init__(self, image: np.ndarray | None) -> None:
        self.image = image
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def latest(self) -> Frame | None:
        if self.image is None:
            return None
        return Frame(image=self.image, timestamp=0.0)


class FakeDepth:
    def __init__(self, depth: np.ndarray) -> None:
        self.depth = depth
        self.loaded = False

    def load(self) -> None:
        self.loaded = True

    def infer(self, image: np.ndarray) -> np.ndarray:
        return self.depth


def _perception(
    *,
    frames: Any,
    depth: Any = None,
    detector: Any = None,
    client: Any = None,
) -> AgentPerception:
    return AgentPerception(
        client=client or Mock(),
        frames=frames,
        depth=depth or FakeDepth(np.zeros((30, 30))),
        detector=detector or Mock(),
        layout=LAYOUT,
        tick_hz=4.0,
    )


class ReflexTickTest(unittest.TestCase):
    def test_tick_posts_strip_clearance_from_depth(self) -> None:
        depth = np.zeros((30, 30))
        depth[:, 20:] = 0.9  # right strip blocked
        client = Mock()
        perception = _perception(
            frames=FakeFrames(np.zeros((30, 30, 3), dtype=np.uint8)),
            depth=FakeDepth(depth),
            client=client,
        )

        self.assertTrue(perception._tick())

        kwargs = client.post_reflex.call_args.kwargs
        self.assertEqual(kwargs["source"], "depth")
        self.assertLess(kwargs["left"], 0.1)
        self.assertLess(kwargs["center"], 0.1)
        self.assertGreater(kwargs["right"], 0.8)

    def test_tick_without_frame_posts_nothing(self) -> None:
        client = Mock()
        perception = _perception(frames=FakeFrames(None), client=client)

        self.assertFalse(perception._tick())

        client.post_reflex.assert_not_called()

    def test_start_and_stop_manage_collaborators(self) -> None:
        frames = FakeFrames(None)
        depth = FakeDepth(np.zeros((30, 30)))
        perception = _perception(frames=frames, depth=depth)

        perception.start()
        try:
            self.assertTrue(depth.loaded)
            self.assertTrue(frames.started)
        finally:
            perception.stop()
        self.assertTrue(frames.stopped)


class FindObjectTest(unittest.TestCase):
    def test_find_adds_horizontal_position(self) -> None:
        detector = Mock()
        detector.detect.return_value = [
            {"score": 0.4, "label": "toy", "box": {"xmin": 0, "xmax": 20, "ymin": 0, "ymax": 10}}
        ]
        perception = _perception(
            frames=FakeFrames(np.zeros((30, 90, 3), dtype=np.uint8)),
            detector=detector,
        )

        result = perception.find("toy")

        self.assertTrue(result["found"])
        self.assertEqual(result["results"][0]["position"], "left")

    def test_find_without_frame_reports_error(self) -> None:
        perception = _perception(frames=FakeFrames(None))

        result = perception.find("toy")

        self.assertFalse(result["ok"])
        self.assertFalse(result["found"])
        self.assertIn("no camera frame", result["error"])

    def test_horizontal_position_thirds(self) -> None:
        self.assertEqual(_horizontal_position({"xmin": 0, "xmax": 10}, 90), "left")
        self.assertEqual(_horizontal_position({"xmin": 40, "xmax": 50}, 90), "center")
        self.assertEqual(_horizontal_position({"xmin": 80, "xmax": 90}, 90), "right")


class PostReflexTest(unittest.TestCase):
    def test_post_reflex_hits_agent_reflex_endpoint(self) -> None:
        response = Mock(status_code=200)
        response.json.return_value = {"ok": True}
        requests = Mock(post=Mock(return_value=response))
        with patch("valle.brain.agent.client._requests", return_value=requests):
            AgentPiClient("http://pi.local:8080").post_reflex(
                left=0.1, center=0.2, right=0.3, source="depth"
            )

        args, kwargs = requests.post.call_args
        self.assertEqual(args[0], "http://pi.local:8080/agent/reflex")
        self.assertEqual(
            kwargs["json"],
            {"left": 0.1, "center": 0.2, "right": 0.3, "source": "depth"},
        )


if __name__ == "__main__":
    unittest.main()
