from __future__ import annotations

import unittest

import numpy as np

from valle.brain.policy import (
    BACKWARD,
    FORWARD,
    LEFT,
    RIGHT,
    ReflexPolicy,
    Strips,
    StripLayout,
    reduce_to_strips,
)


class ReflexPolicyTest(unittest.TestCase):
    def test_center_clear_drives_forward(self) -> None:
        policy = ReflexPolicy(blocked_threshold=0.5, hysteresis_margin=0.05)
        self.assertEqual(
            policy.decide(Strips(left=0.4, center=0.2, right=0.4)), FORWARD
        )

    def test_center_blocked_left_clearer_turns_left(self) -> None:
        policy = ReflexPolicy(blocked_threshold=0.5, hysteresis_margin=0.05)
        self.assertEqual(
            policy.decide(Strips(left=0.3, center=0.7, right=0.45)), LEFT
        )

    def test_center_blocked_right_clearer_turns_right(self) -> None:
        policy = ReflexPolicy(blocked_threshold=0.5, hysteresis_margin=0.05)
        self.assertEqual(
            policy.decide(Strips(left=0.6, center=0.7, right=0.3)), RIGHT
        )

    def test_all_blocked_reverses(self) -> None:
        policy = ReflexPolicy(blocked_threshold=0.5, hysteresis_margin=0.05)
        self.assertEqual(
            policy.decide(Strips(left=0.9, center=0.9, right=0.9)), BACKWARD
        )

    def test_hysteresis_keeps_reversing_at_boundary(self) -> None:
        policy = ReflexPolicy(blocked_threshold=0.5, hysteresis_margin=0.1)
        policy.decide(Strips(left=0.9, center=0.9, right=0.9))
        # Center is just under raw threshold but not by the margin -> sides win or back again.
        result = policy.decide(Strips(left=0.6, center=0.45, right=0.6))
        self.assertEqual(result, BACKWARD)

    def test_hysteresis_releases_when_clearly_open(self) -> None:
        policy = ReflexPolicy(blocked_threshold=0.5, hysteresis_margin=0.1)
        policy.decide(Strips(left=0.9, center=0.9, right=0.9))
        result = policy.decide(Strips(left=0.6, center=0.2, right=0.6))
        self.assertEqual(result, FORWARD)


class StripReducerTest(unittest.TestCase):
    def test_strips_pick_up_local_closeness(self) -> None:
        depth = np.zeros((100, 300), dtype=np.float32)
        depth[:, :100] = 0.2  # left strip far
        depth[:, 100:200] = 0.8  # center strip close
        depth[:, 200:] = 0.4  # right strip mid
        layout = StripLayout(
            left_strip_end=1 / 3,
            right_strip_start=2 / 3,
            top_crop=0.0,
            bottom_crop=0.0,
        )
        strips = reduce_to_strips(depth, layout)
        self.assertAlmostEqual(strips.left, 0.2, places=2)
        self.assertAlmostEqual(strips.center, 0.8, places=2)
        self.assertAlmostEqual(strips.right, 0.4, places=2)


if __name__ == "__main__":
    unittest.main()
