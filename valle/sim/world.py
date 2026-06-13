"""Simulated world for the Valle digital twin.

A 2D top-down room in meters: wall segments (including a door with a
lock), free-standing objects rendered as billboards (a lemon to seek),
and the robot pose. The motor driver sets the current motion; ``step``
integrates it over wall-clock time with wall collision.
"""
from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from typing import Any

import numpy as np


ROBOT_RADIUS = 0.12
CAMERA_HEIGHT = 0.10
WALL_HEIGHT = 0.80
MAX_LINEAR_SPEED = 0.5  # m/s at full motor speed
MAX_TURN_RATE = 2.0  # rad/s at full motor speed
CLEARANCE_RANGE = 3.0  # distance mapped to closeness 0 for reflex strips


@dataclass(frozen=True)
class Wall:
    ax: float
    ay: float
    bx: float
    by: float
    kind: str = "wall"  # wall | box | door


@dataclass
class SimObject:
    name: str
    x: float
    y: float
    radius: float
    height: float  # center height above the floor
    color: tuple[int, int, int]  # BGR


@dataclass
class Pose:
    x: float
    y: float
    heading: float  # radians; 0 = +x, pi/2 = +y, counterclockwise


class SimWorld:
    def __init__(
        self,
        *,
        walls: list[Wall],
        objects: list[SimObject],
        start_pose: Pose,
        door_locked: bool = True,
    ) -> None:
        self.walls = walls
        self.objects = objects
        self._start = Pose(start_pose.x, start_pose.y, start_pose.heading)
        self._start_door_locked = door_locked
        self.pose = Pose(start_pose.x, start_pose.y, start_pose.heading)
        self.door_locked = door_locked
        self.action = "stopped"
        self.speed = 0.0
        self._lock = threading.Lock()
        self._segments = np.array(
            [[w.ax, w.ay, w.bx, w.by] for w in walls], dtype=np.float64
        )

    # ----- motor interface -----

    def set_motion(self, action: str, speed: float) -> None:
        with self._lock:
            self.action = action
            self.speed = min(max(float(speed), 0.0), 1.0)

    # ----- physics -----

    def step(self, dt: float) -> None:
        with self._lock:
            action, speed = self.action, self.speed
            if action in ("forward", "backward") and speed > 0:
                sign = 1.0 if action == "forward" else -1.0
                distance = sign * speed * MAX_LINEAR_SPEED * dt
                nx = self.pose.x + math.cos(self.pose.heading) * distance
                ny = self.pose.y + math.sin(self.pose.heading) * distance
                if self._clear_of_walls(nx, ny):
                    self.pose.x, self.pose.y = nx, ny
            elif action in ("left", "right") and speed > 0:
                sign = 1.0 if action == "left" else -1.0
                self.pose.heading = _wrap_angle(
                    self.pose.heading + sign * speed * MAX_TURN_RATE * dt
                )

    def _clear_of_walls(self, x: float, y: float) -> bool:
        return _min_distance_to_segments(self._segments, x, y) >= ROBOT_RADIUS

    # ----- ground truth -----

    def raycast(self, angles: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Cast rays from the robot at absolute world angles.

        Returns (distance, wall_index, u) per ray; distance is inf on miss
        and u is the hit position along the wall segment in [0, 1].
        """
        dirs = np.stack([np.cos(angles), np.sin(angles)], axis=1)
        return raycast_segments(
            self._segments, np.array([self.pose.x, self.pose.y]), dirs
        )

    def clearance_strips(
        self, *, fov_radians: float = math.radians(66), rays: int = 60
    ) -> tuple[float, float, float]:
        """Reflex-style closeness per strip in [0, 1]; higher = more blocked."""
        offsets = np.linspace(fov_radians / 2, -fov_radians / 2, rays)
        distances, _, _ = self.raycast(self.pose.heading + offsets)
        closeness = np.clip(1.0 - distances / CLEARANCE_RANGE, 0.0, 1.0)
        third = rays // 3
        left = float(np.percentile(closeness[:third], 90))
        center = float(np.percentile(closeness[third : rays - third], 90))
        right = float(np.percentile(closeness[rays - third :], 90))
        return left, center, right

    def set_door_locked(self, locked: bool) -> None:
        self.door_locked = bool(locked)

    def reset(self) -> None:
        with self._lock:
            self.pose = Pose(self._start.x, self._start.y, self._start.heading)
            self.door_locked = self._start_door_locked
            self.action = "stopped"
            self.speed = 0.0

    def door_segment(self) -> Wall | None:
        for wall in self.walls:
            if wall.kind == "door":
                return wall
        return None

    def state(self) -> dict[str, Any]:
        door = self.door_segment()
        objects = []
        for obj in self.objects:
            dx, dy = obj.x - self.pose.x, obj.y - self.pose.y
            objects.append(
                {
                    "name": obj.name,
                    "x": obj.x,
                    "y": obj.y,
                    "distance": round(math.hypot(dx, dy), 3),
                    "bearing_deg": round(
                        math.degrees(
                            _wrap_angle(math.atan2(dy, dx) - self.pose.heading)
                        ),
                        1,
                    ),
                }
            )
        state: dict[str, Any] = {
            "robot": {
                "x": round(self.pose.x, 3),
                "y": round(self.pose.y, 3),
                "heading_deg": round(math.degrees(self.pose.heading), 1),
            },
            "driver": {"action": self.action, "speed": round(self.speed, 3)},
            "objects": objects,
        }
        if door is not None:
            mid_x, mid_y = (door.ax + door.bx) / 2, (door.ay + door.by) / 2
            state["door"] = {
                "locked": self.door_locked,
                "distance": round(
                    math.hypot(mid_x - self.pose.x, mid_y - self.pose.y), 3
                ),
            }
        return state


def build_default_world() -> SimWorld:
    """A 6m x 5m room: door with deadbolt on the north wall, a lemon on
    the floor, and a box obstacle near the east side."""
    walls = [
        Wall(0, 0, 6, 0),  # south
        Wall(0, 0, 0, 5),  # west
        Wall(6, 0, 6, 5),  # east
        Wall(0, 5, 2.5, 5),  # north, left of door
        Wall(2.5, 5, 3.5, 5, kind="door"),
        Wall(3.5, 5, 6, 5),  # north, right of door
        # box obstacle
        Wall(4.2, 2.0, 4.9, 2.0, kind="box"),
        Wall(4.9, 2.0, 4.9, 2.7, kind="box"),
        Wall(4.9, 2.7, 4.2, 2.7, kind="box"),
        Wall(4.2, 2.7, 4.2, 2.0, kind="box"),
    ]
    objects = [
        SimObject(
            name="lemon", x=1.5, y=3.5, radius=0.05, height=0.05, color=(0, 215, 255)
        ),
    ]
    return SimWorld(
        walls=walls,
        objects=objects,
        start_pose=Pose(x=3.0, y=1.2, heading=math.pi / 2),  # facing the door
        door_locked=True,
    )


def raycast_segments(
    segments: np.ndarray, origin: np.ndarray, dirs: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Vectorized ray/segment intersection.

    ``segments`` is (M, 4) rows of (ax, ay, bx, by); ``dirs`` is (N, 2).
    Returns (distance, segment_index, u) arrays of length N. Distance is
    measured in units of ``dirs`` length (pass unit vectors for meters).
    """
    a = segments[:, 0:2]
    e = segments[:, 2:4] - a  # (M, 2)
    ap = a[None, :, :] - origin[None, None, :]  # (1, M, 2)
    d = dirs[:, None, :]  # (N, 1, 2)

    denom = _cross(d, e[None, :, :])  # (N, M)
    with np.errstate(divide="ignore", invalid="ignore"):
        t = _cross(ap, e[None, :, :]) / denom
        u = _cross(ap, d) / denom
    valid = (np.abs(denom) > 1e-12) & (u >= 0.0) & (u <= 1.0) & (t > 1e-6)
    t = np.where(valid, t, np.inf)

    index = np.argmin(t, axis=1)
    rows = np.arange(t.shape[0])
    return t[rows, index], index, u[rows, index]


def _cross(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return a[..., 0] * b[..., 1] - a[..., 1] * b[..., 0]


def _min_distance_to_segments(segments: np.ndarray, x: float, y: float) -> float:
    p = np.array([x, y])
    a = segments[:, 0:2]
    e = segments[:, 2:4] - a
    length_sq = np.maximum((e * e).sum(axis=1), 1e-12)
    s = np.clip(((p - a) * e).sum(axis=1) / length_sq, 0.0, 1.0)
    nearest = a + s[:, None] * e
    return float(np.sqrt(((p - nearest) ** 2).sum(axis=1)).min())


def _wrap_angle(angle: float) -> float:
    return math.atan2(math.sin(angle), math.cos(angle))
