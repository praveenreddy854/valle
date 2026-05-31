from __future__ import annotations

from dataclasses import dataclass

import numpy as np


FORWARD = "forward"
LEFT = "left"
RIGHT = "right"
BACKWARD = "backward"

ACTIONS = (FORWARD, LEFT, RIGHT, BACKWARD)


@dataclass(frozen=True)
class Strips:
    """Closeness per vertical strip - higher means closer (more blocked)."""

    left: float
    center: float
    right: float


@dataclass(frozen=True)
class StripLayout:
    left_strip_end: float
    right_strip_start: float
    top_crop: float
    bottom_crop: float


def reduce_to_strips(depth: np.ndarray, layout: StripLayout) -> Strips:
    """Reduce a normalised depth map to per-strip closeness in [0, 1]."""
    height, width = depth.shape[:2]
    top = int(layout.top_crop * height)
    bottom = int((1.0 - layout.bottom_crop) * height)
    cropped = depth[top:bottom, :]

    left_end = max(1, int(layout.left_strip_end * width))
    right_start = min(width - 1, int(layout.right_strip_start * width))

    left = float(np.percentile(cropped[:, :left_end], 90))
    center = float(np.percentile(cropped[:, left_end:right_start], 90))
    right = float(np.percentile(cropped[:, right_start:], 90))
    return Strips(left=left, center=center, right=right)


class ReflexPolicy:
    """Maps a strips reading to one of {forward, left, right, backward}.

    Discrete-action policy (Q13 a-i) with reverse-and-retry when all
    strips are blocked (Q13 c-i) and a hysteresis margin to avoid
    forward-reverse oscillation at the threshold boundary.
    """

    def __init__(self, blocked_threshold: float, hysteresis_margin: float) -> None:
        self._blocked = blocked_threshold
        self._margin = hysteresis_margin
        self._previous_action: str | None = None

    @property
    def previous_action(self) -> str | None:
        return self._previous_action

    def decide(self, strips: Strips) -> str:
        forward_threshold = self._blocked
        if self._previous_action == BACKWARD:
            forward_threshold = max(0.0, self._blocked - self._margin)

        if strips.center < forward_threshold:
            action = FORWARD
        elif strips.left < self._blocked and strips.left <= strips.right:
            action = LEFT
        elif strips.right < self._blocked:
            action = RIGHT
        else:
            action = BACKWARD

        self._previous_action = action
        return action

    def reset(self) -> None:
        self._previous_action = None
