"""Map-guided seek: spin-scan at waypoints, ask the LLM where to go, drive a leg.

Each stop the robot pivots through four headings, checking every view with the
detector. The LLM then records the place in the persistent room map and picks
the next heading; the fast depth reflex drives that leg. The Pi's idle
watchdog ends sessions that only pivot, so each motion phase uses its own
short session and LLM thinking happens with the motors stopped.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable

import requests

from ..client import PiClient, PiClientError, SessionRejectedError
from ..exploration import WaypointPlanner
from ..frames import FrameReader
from ..policy import BACKWARD, FORWARD, LEFT, RIGHT, ReflexPolicy, StripLayout, reduce_to_strips
from ..room_map import HEADINGS
from ... import telemetry
from ...observability import get_tracer

log = logging.getLogger("valle.brain.find.explore")

# Clockwise order matches HEADINGS: ahead -> right -> behind -> left.
_STEPS_RIGHT = {h: i for i, h in enumerate(HEADINGS)}


class _Expired(Exception):
    """The seek deadline passed before a motion session could start."""


class MapSeek:
    def __init__(
        self,
        *,
        brain_config: Any,
        find_config: Any,
        detector: Any,
        depth: Any,
        planner: WaypointPlanner,
        client: PiClient | None = None,
        frames_factory: Callable[[str], FrameReader] = FrameReader,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._brain = brain_config
        self._find = find_config
        self._detector = detector
        self._depth = depth
        self._planner = planner
        self._client = client or PiClient(
            brain_config.pi_base_url, timeout_seconds=brain_config.request_timeout_seconds
        )
        self._frames_factory = frames_factory
        self._sleep = sleep
        self._layout = StripLayout(
            left_strip_end=brain_config.left_strip_end,
            right_strip_start=brain_config.right_strip_start,
            top_crop=brain_config.top_crop,
            bottom_crop=brain_config.bottom_crop,
        )

    def run(
        self, *, object_query: str, max_seconds: float, speed: float | None = None
    ) -> dict[str, Any]:
        started = time.monotonic()
        try:
            result = self._run(object_query=object_query, max_seconds=max_seconds, speed=speed)
        except Exception:
            telemetry.record_seek_result(
                found=False, reason="error", seconds=time.monotonic() - started
            )
            raise
        telemetry.record_seek_result(
            found=bool(result.get("found")),
            reason=str(result.get("reason") or "unknown"),
            seconds=float(result.get("elapsed_seconds") or 0.0),
        )
        return result

    def _run(
        self, *, object_query: str, max_seconds: float, speed: float | None = None
    ) -> dict[str, Any]:
        self._depth.load()
        self._detector.load()
        frames = self._frames_factory(self._brain.camera_url)
        frames.start()
        started = time.monotonic()
        self._deadline = started + max_seconds
        self._speed = speed
        result: dict[str, Any] = {"found": False, "object": object_query, "waypoints": []}
        came_from: str | None = None
        came_heading: str | None = None
        try:
            while not self._expired():
                with get_tracer(__name__).start_as_current_span("brain.seek.waypoint") as span:
                    views, hit = self._spin_scan(frames, object_query)
                    if hit is not None:
                        result.update(found=True, reason="found", **hit)
                        break
                    if len(views) < len(HEADINGS):
                        result["reason"] = "blind" if not views else "scan_incomplete"
                        break
                    if self._expired():
                        break
                    plan = self._planner.plan(
                        views, object_query, came_from=came_from, came_heading=came_heading
                    )
                    span.set_attribute("place.id", plan.place_id)
                    span.set_attribute("place.new", plan.is_new_place)
                    span.set_attribute("next_heading", plan.next_heading)
                    result["waypoints"].append(
                        {
                            "place_id": plan.place_id,
                            "summary": plan.summary,
                            "new": plan.is_new_place,
                            "next_heading": plan.next_heading,
                            "reason": plan.reason,
                        }
                    )
                    log.info(
                        "waypoint place=%s new=%s next=%s (%s)",
                        plan.place_id, plan.is_new_place, plan.next_heading, plan.reason,
                    )
                    if plan.next_heading == "done":
                        result["reason"] = "explored"
                        break
                    # The LLM call can use up the budget; never start motion after it.
                    if self._expired():
                        break
                    heading = (
                        plan.object_heading if plan.object_heading != "none" else plan.next_heading
                    )
                    hit = self._drive_leg(frames, object_query, heading)
                    if hit is not None:
                        result.update(found=True, reason="found", **hit)
                        break
                    came_from, came_heading = plan.place_id, plan.to_place_frame(heading)
        except SessionRejectedError as exc:
            result["error"] = str(exc)
        except _Expired:
            pass
        finally:
            frames.stop()
        result.setdefault("reason", "max_seconds")
        result["elapsed_seconds"] = round(time.monotonic() - started, 2)
        return result

    # ---- phases -------------------------------------------------------------

    def _spin_scan(
        self, frames: FrameReader, query: str
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        """Capture ahead/right/behind/left; return views and a detection hit if any."""
        views: dict[str, Any] = {}
        session = self._start_session()
        try:
            for index, heading in enumerate(HEADINGS):
                if index:
                    self._pivot(session, RIGHT, 1)
                image = self._settled_frame(frames)
                if image is None:
                    return views, None
                hit = self._detect(image, query)
                if hit is not None:
                    return views, {**hit, "heading": heading}
                views[heading] = image
            # Turn back to face "ahead" so headings stay meaningful for the plan.
            self._pivot(session, RIGHT, 1)
        finally:
            self._stop_session(session)
        return views, None

    def _drive_leg(self, frames: FrameReader, query: str, heading: str) -> dict[str, Any] | None:
        session = self._start_session()
        try:
            steps = _STEPS_RIGHT[heading]
            if steps == 3:
                self._pivot(session, LEFT, 1)
            elif steps:
                self._pivot(session, RIGHT, steps)
            policy = ReflexPolicy(
                blocked_threshold=self._brain.blocked_threshold,
                hysteresis_margin=self._brain.hysteresis_margin,
            )
            leg_end = min(self._deadline, time.monotonic() + self._find.seek_leg_seconds)
            tick_interval = 1.0 / max(0.5, self._brain.tick_hz)
            blocked_ticks = 0
            while time.monotonic() < leg_end:
                tick_started = time.monotonic()
                frame = frames.latest()
                if frame is None:
                    telemetry.record_blind_tick("seek")
                    self._sleep(tick_interval)
                    continue
                hit = self._detect(frame.image, query)
                if hit is not None:
                    return {**hit, "heading": heading}
                strips = reduce_to_strips(self._depth.infer(frame.image), self._layout)
                action = policy.decide(strips)
                telemetry.record_tick("seek", action=action, seconds=time.monotonic() - tick_started)
                if action != FORWARD:
                    # The path is closing: this leg has reached a new spot.
                    blocked_ticks += 1
                    if blocked_ticks >= 2:
                        break
                    continue
                blocked_ticks = 0
                self._drive(session, FORWARD, self._brain.pulse_forward * 2)
                elapsed = time.monotonic() - tick_started
                if elapsed < tick_interval:
                    self._sleep(tick_interval - elapsed)
        finally:
            self._stop_session(session)
        return None

    # ---- helpers ------------------------------------------------------------

    def _detect(self, image: Any, query: str) -> dict[str, Any] | None:
        detections = self._detector.detect(image, query)
        top = max((d["score"] for d in detections), default=0.0)
        telemetry.record_detection_score("seek", float(top))
        for detection in detections:
            if float(detection.get("score", 0.0)) >= self._find.seek_found_score:
                return {
                    "score": detection["score"],
                    "label": detection["label"],
                    "box": detection["box"],
                }
        return None

    def _pivot(self, session: str, direction: str, quarter_turns: int) -> None:
        for _ in range(quarter_turns):
            self._drive(session, direction, self._find.seek_quarter_turn_seconds)
            # Let the pulse finish and the camera settle before the next view.
            self._sleep(self._find.seek_quarter_turn_seconds + 0.3)

    def _drive(self, session: str, direction: str, duration: float) -> None:
        if direction == FORWARD:
            speed = self._brain.speed_forward
        elif direction == BACKWARD:
            speed = self._brain.speed_backward
        else:
            speed = self._brain.speed_turn
        if self._speed is not None:
            speed = self._speed
        try:
            self._client.drive(session, direction=direction, duration=duration, speed=speed)
        except SessionRejectedError:
            raise
        except (requests.RequestException, PiClientError) as exc:
            telemetry.record_drive_failure("seek")
            log.warning("drive failed: %s", exc)

    def _settled_frame(self, frames: FrameReader) -> Any:
        waited = 0.0
        while waited < self._brain.grace_seconds:
            frame = frames.latest()
            if frame is not None:
                return frame.image
            self._sleep(0.1)
            waited += 0.1
        return None

    def _start_session(self) -> str:
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise _Expired()
        session = self._client.start(
            max_seconds=min(remaining, self._brain.session_max_seconds or remaining),
            idle_seconds=self._brain.session_idle_seconds,
        )
        return session["session_id"]

    def _stop_session(self, session: str) -> None:
        try:
            self._client.stop(session, reason="manual")
        except SessionRejectedError:
            pass
        except Exception:
            self._client.panic_stop()

    def _expired(self) -> bool:
        return time.monotonic() >= self._deadline
