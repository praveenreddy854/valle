from __future__ import annotations

from .world import SimWorld


class SimMotorDriver:
    """MotorDriver that drives the simulated robot instead of GPIO pins."""

    name = "sim"

    def __init__(self, world: SimWorld) -> None:
        self._world = world

    def forward(self, speed: float) -> None:
        self._world.set_motion("forward", speed)

    def backward(self, speed: float) -> None:
        self._world.set_motion("backward", speed)

    def turn_left(self, speed: float) -> None:
        self._world.set_motion("left", speed)

    def turn_right(self, speed: float) -> None:
        self._world.set_motion("right", speed)

    def stop(self) -> None:
        self._world.set_motion("stopped", 0.0)

    def close(self) -> None:
        self.stop()
