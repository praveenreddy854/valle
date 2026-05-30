from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .config import ValleConfig


class MotorDriver(Protocol):
    name: str

    def forward(self, speed: float) -> None:
        ...

    def backward(self, speed: float) -> None:
        ...

    def turn_left(self, speed: float) -> None:
        ...

    def turn_right(self, speed: float) -> None:
        ...

    def stop(self) -> None:
        ...

    def close(self) -> None:
        ...


class GpioZeroMotorDriver:
    name = "gpiozero"

    def __init__(self, config: ValleConfig) -> None:
        try:
            from gpiozero import DigitalOutputDevice, PWMOutputDevice
        except ImportError as exc:
            raise RuntimeError(
                "gpiozero is not installed. Install requirements.txt on the Raspberry Pi."
            ) from exc

        self._left = _L298NMotor(
            forward_device=DigitalOutputDevice(
                config.left_forward_pin, initial_value=False
            ),
            backward_device=DigitalOutputDevice(
                config.left_backward_pin, initial_value=False
            ),
            enable_device=PWMOutputDevice(config.left_enable_pin, initial_value=0.0),
        )
        self._right = _L298NMotor(
            forward_device=DigitalOutputDevice(
                config.right_forward_pin, initial_value=False
            ),
            backward_device=DigitalOutputDevice(
                config.right_backward_pin, initial_value=False
            ),
            enable_device=PWMOutputDevice(config.right_enable_pin, initial_value=0.0),
        )
        self.stop()

    def forward(self, speed: float) -> None:
        self._left.forward(speed)
        self._right.forward(speed)

    def backward(self, speed: float) -> None:
        self._left.backward(speed)
        self._right.backward(speed)

    def turn_left(self, speed: float) -> None:
        self._left.backward(speed)
        self._right.forward(speed)

    def turn_right(self, speed: float) -> None:
        self._left.forward(speed)
        self._right.backward(speed)

    def stop(self) -> None:
        self._left.stop()
        self._right.stop()

    def close(self) -> None:
        self.stop()
        self._left.close()
        self._right.close()


class _L298NMotor:
    def __init__(
        self,
        *,
        forward_device: object,
        backward_device: object,
        enable_device: object,
    ) -> None:
        self._forward = forward_device
        self._backward = backward_device
        self._enable = enable_device

    def forward(self, speed: float) -> None:
        self._drive(forward=True, speed=speed)

    def backward(self, speed: float) -> None:
        self._drive(forward=False, speed=speed)

    def stop(self) -> None:
        self._enable.value = 0.0
        self._forward.off()
        self._backward.off()

    def close(self) -> None:
        self.stop()
        self._enable.close()
        self._forward.close()
        self._backward.close()

    def _drive(self, *, forward: bool, speed: float) -> None:
        self._enable.value = 0.0
        if forward:
            self._backward.off()
            self._forward.on()
        else:
            self._forward.off()
            self._backward.on()
        self._enable.value = speed


@dataclass
class MockMotorDriver:
    name: str = "mock"
    current_action: str = "stopped"
    current_speed: float = 0.0
    history: list[tuple[str, float]] = field(default_factory=list)

    def forward(self, speed: float) -> None:
        self._set("forward", speed)

    def backward(self, speed: float) -> None:
        self._set("backward", speed)

    def turn_left(self, speed: float) -> None:
        self._set("left", speed)

    def turn_right(self, speed: float) -> None:
        self._set("right", speed)

    def stop(self) -> None:
        self._set("stopped", 0.0)

    def close(self) -> None:
        self.stop()

    def _set(self, action: str, speed: float) -> None:
        self.current_action = action
        self.current_speed = speed
        self.history.append((action, speed))


def create_motor_driver(config: ValleConfig) -> MotorDriver:
    driver = config.driver.strip().lower()
    if driver == "auto":
        driver = "gpiozero" if is_raspberry_pi() else "mock"

    if driver == "gpiozero":
        return GpioZeroMotorDriver(config)
    if driver == "mock":
        return MockMotorDriver()

    raise ValueError("VALLE_DRIVER must be one of: auto, gpiozero, mock")


def is_raspberry_pi() -> bool:
    model_path = Path("/proc/device-tree/model")
    if not model_path.exists():
        return False
    try:
        return "raspberry pi" in model_path.read_text(errors="ignore").lower()
    except OSError:
        return False
