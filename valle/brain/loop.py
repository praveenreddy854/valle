from __future__ import annotations

import logging
import signal
import time

import requests

from .client import PiClient, PiClientError, SessionRejectedError
from .config import BrainConfig
from .depth import DepthEstimator
from .frames import Frame, FrameReader
from .policy import BACKWARD, FORWARD, LEFT, RIGHT, ReflexPolicy, StripLayout, reduce_to_strips


log = logging.getLogger("valle.brain")


class Brain:
    def __init__(
        self,
        config: BrainConfig,
        *,
        client: PiClient,
        frames: FrameReader,
        depth: DepthEstimator,
        policy: ReflexPolicy,
    ) -> None:
        self._config = config
        self._client = client
        self._frames = frames
        self._depth = depth
        self._policy = policy
        self._layout = StripLayout(
            left_strip_end=config.left_strip_end,
            right_strip_start=config.right_strip_start,
            top_crop=config.top_crop,
            bottom_crop=config.bottom_crop,
        )
        self._stop_requested = False

    def request_stop(self) -> None:
        self._stop_requested = True

    def run(self) -> None:
        self._depth.load()
        self._frames.start()

        try:
            session = self._client.start(
                max_seconds=self._config.session_max_seconds,
                idle_seconds=self._config.session_idle_seconds,
            )
        except SessionRejectedError as exc:
            log.error("Pi rejected session start: %s", exc)
            self._frames.stop()
            return

        session_id = session["session_id"]
        log.info(
            "autopilot session %s started (max=%.1fs, idle=%.1fs)",
            session_id,
            session["max_seconds"],
            session["idle_seconds"],
        )

        tick_interval = 1.0 / max(0.5, self._config.tick_hz)
        blind_since: float | None = None
        ended_reason = "manual"

        try:
            while not self._stop_requested:
                tick_started = time.monotonic()

                frame = self._frames.latest()
                if frame is None:
                    ended_reason = self._handle_blind(
                        session_id, blind_since, tick_started
                    )
                    if ended_reason != "":
                        break
                    blind_since = blind_since or tick_started
                    self._sleep_remainder(tick_started, tick_interval)
                    continue

                blind_since = None

                try:
                    action = self._decide(frame)
                except Exception:
                    log.exception("perception error; pausing tick")
                    self._sleep_remainder(tick_started, tick_interval)
                    continue

                if not self._send_drive(session_id, action):
                    ended_reason = "manual"
                    break

                self._sleep_remainder(tick_started, tick_interval)
        finally:
            self._end(session_id, ended_reason)
            self._frames.stop()

    def _decide(self, frame: Frame) -> str:
        depth = self._depth.infer(frame.image)
        strips = reduce_to_strips(depth, self._layout)
        action = self._policy.decide(strips)
        log.debug(
            "strips L=%.2f C=%.2f R=%.2f -> %s",
            strips.left,
            strips.center,
            strips.right,
            action,
        )
        return action

    def _send_drive(self, session_id: str, action: str) -> bool:
        duration, speed = self._action_params(action)
        try:
            self._client.drive(
                session_id, direction=action, duration=duration, speed=speed
            )
            return True
        except SessionRejectedError:
            log.warning("session no longer active on Pi; exiting loop")
            return False
        except (requests.RequestException, PiClientError) as exc:
            log.warning("drive request failed: %s", exc)
            return True  # transient; let next tick try again or grace fire

    def _handle_blind(
        self, session_id: str, blind_since: float | None, now: float
    ) -> str:
        if blind_since is None:
            log.warning("no fresh frame; entering grace window")
            return ""
        if now - blind_since >= self._config.grace_seconds:
            log.error(
                "no frames for %.1fs - ending session as blind",
                now - blind_since,
            )
            try:
                self._client.stop(session_id, reason="blind")
            except Exception:
                self._client.panic_stop()
            return "blind"
        return ""

    def _end(self, session_id: str, reason: str) -> None:
        try:
            self._client.stop(session_id, reason=reason)
        except SessionRejectedError:
            pass
        except Exception:
            log.warning("clean stop failed; sending panic /stop")
            self._client.panic_stop()

    def _action_params(self, action: str) -> tuple[float, float]:
        if action == FORWARD:
            return self._config.pulse_forward, self._config.speed_forward
        if action == BACKWARD:
            return self._config.pulse_backward, self._config.speed_backward
        if action in (LEFT, RIGHT):
            return self._config.pulse_turn, self._config.speed_turn
        raise ValueError(f"unknown action: {action}")

    @staticmethod
    def _sleep_remainder(tick_started: float, tick_interval: float) -> None:
        elapsed = time.monotonic() - tick_started
        remaining = tick_interval - elapsed
        if remaining > 0:
            time.sleep(remaining)


def build_brain(config: BrainConfig | None = None) -> Brain:
    config = config or BrainConfig.from_env()
    client = PiClient(
        config.pi_base_url, timeout_seconds=config.request_timeout_seconds
    )
    frames = FrameReader(config.camera_url)
    depth = DepthEstimator(config.depth_model, config.depth_device)
    policy = ReflexPolicy(
        blocked_threshold=config.blocked_threshold,
        hysteresis_margin=config.hysteresis_margin,
    )
    return Brain(config, client=client, frames=frames, depth=depth, policy=policy)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    brain = build_brain()

    def _handle_signal(signum: int, frame: object) -> None:
        log.info("signal %d received; stopping", signum)
        brain.request_stop()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    brain.run()
