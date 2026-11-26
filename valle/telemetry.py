"""Valle's OpenTelemetry instruments: robot spans, metrics, and host health.

Instruments are created lazily so they bind to the meter provider that
``configure_tracing`` installs at startup; before that they are no-ops.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from .observability import get_meter


@lru_cache(maxsize=None)
def _meter() -> Any:
    return get_meter("valle")


@lru_cache(maxsize=None)
def _counter(name: str, unit: str, description: str) -> Any:
    return _meter().create_counter(name, unit=unit, description=description)


@lru_cache(maxsize=None)
def _histogram(name: str, unit: str, description: str) -> Any:
    return _meter().create_histogram(name, unit=unit, description=description)


# ---- brain (seek / autopilot) ----------------------------------------------


def record_tick(
    loop: str,
    *,
    action: str,
    seconds: float,
    depth_seconds: float | None = None,
    detect_seconds: float | None = None,
) -> None:
    attrs = {"loop": loop, "action": action}
    _counter("valle.brain.ticks", "{tick}", "Perception ticks by loop and action").add(1, attrs)
    _histogram("valle.brain.tick.duration", "s", "Time spent deciding one tick").record(
        seconds, {"loop": loop}
    )
    if depth_seconds is not None:
        _histogram("valle.brain.depth.duration", "s", "Depth inference time").record(
            depth_seconds, {"loop": loop}
        )
    if detect_seconds is not None:
        _histogram("valle.brain.detect.duration", "s", "Object detection time").record(
            detect_seconds, {"loop": loop}
        )


def record_drive_failure(loop: str) -> None:
    _counter("valle.brain.drive_failures", "{request}", "Drive requests to the Pi that failed").add(
        1, {"loop": loop}
    )


def record_blind_tick(loop: str) -> None:
    _counter("valle.brain.blind_ticks", "{tick}", "Ticks skipped because no fresh frame arrived").add(
        1, {"loop": loop}
    )


def record_detection_score(loop: str, score: float) -> None:
    _histogram("valle.brain.detection.top_score", "1", "Highest detector score per tick").record(
        score, {"loop": loop}
    )


def record_seek_result(*, found: bool, reason: str, seconds: float) -> None:
    attrs = {"found": found, "reason": reason}
    _counter("valle.brain.seeks", "{seek}", "Completed seeks by outcome").add(1, attrs)
    _histogram("valle.brain.seek.duration", "s", "Seek duration").record(seconds, attrs)


def record_frame_received() -> None:
    _counter("valle.brain.frames", "{frame}", "Camera frames received by the brain").add(1)


# ---- Pi (controller / reflex gate / camera) ---------------------------------


def record_motor_command(command: str, source: str) -> None:
    _counter("valle.pi.motor_commands", "{command}", "Motor pulses applied").add(
        1, {"command": command, "source": source}
    )


def record_reflex_decision(direction: str, *, allowed: bool, reason: str) -> None:
    _counter("valle.pi.reflex_decisions", "{decision}", "Reflex gate decisions").add(
        1, {"direction": direction, "allowed": allowed, "reason": reason}
    )


def record_session_end(kind: str, reason: str) -> None:
    _counter("valle.pi.sessions_ended", "{session}", "Motor sessions ended by reason").add(
        1, {"kind": kind, "reason": reason}
    )


def record_camera_frame() -> None:
    _counter("valle.pi.camera.frames", "{frame}", "Frames encoded by the Pi camera").add(1)


# ---- host health ------------------------------------------------------------


def register_host_gauges(service: str) -> None:
    """Observable gauges for CPU temperature and Wi-Fi signal (Linux only)."""
    meter = _meter()
    attrs = {"service": service}

    def cpu_temp(_options: Any) -> list[Any]:
        value = read_cpu_temperature_c()
        return [] if value is None else [_observation(value, attrs)]

    def wifi_signal(_options: Any) -> list[Any]:
        value = read_wifi_signal_dbm()
        return [] if value is None else [_observation(value, attrs)]

    def load_avg(_options: Any) -> list[Any]:
        try:
            return [_observation(os.getloadavg()[0], attrs)]
        except (OSError, AttributeError):
            return []

    meter.create_observable_gauge(
        "valle.host.cpu.temperature", callbacks=[cpu_temp], unit="Cel",
        description="SoC temperature",
    )
    meter.create_observable_gauge(
        "valle.host.wifi.signal", callbacks=[wifi_signal], unit="dBm",
        description="Wi-Fi signal level",
    )
    meter.create_observable_gauge(
        "valle.host.load.1m", callbacks=[load_avg], unit="1",
        description="1-minute load average",
    )


def read_cpu_temperature_c(
    path: Path = Path("/sys/class/thermal/thermal_zone0/temp"),
) -> float | None:
    try:
        return int(path.read_text().strip()) / 1000.0
    except (OSError, ValueError):
        return None


def read_wifi_signal_dbm(path: Path = Path("/proc/net/wireless")) -> float | None:
    # Format: header lines, then "wlan0: 0000   70.  -40.  -256 ..."
    try:
        lines = path.read_text().splitlines()[2:]
    except OSError:
        return None
    for line in lines:
        fields = line.split()
        if len(fields) >= 4:
            try:
                return float(fields[3].rstrip("."))
            except ValueError:
                continue
    return None


def _observation(value: float, attrs: dict[str, Any]) -> Any:
    from opentelemetry.metrics import Observation

    return Observation(value, attrs)
