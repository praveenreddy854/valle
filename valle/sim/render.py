"""First-person renderer for the digital twin.

Raycasts the 2D world into a perspective view (walls, floor, ceiling),
then draws billboard sprites — the lemon, the door knob, and the
deadbolt — with per-column depth testing. The deadbolt is drawn as a
horizontal gold bar when the door is locked and a vertical one when
unlocked, so vision models have a visible state to inspect.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np

from .world import (
    CAMERA_HEIGHT,
    WALL_HEIGHT,
    SimObject,
    SimWorld,
    raycast_segments,
)


WALL_COLORS = {
    "wall": (172, 166, 152),  # warm gray (BGR)
    "box": (96, 88, 84),  # dark box obstacle
    "door": (38, 74, 134),  # brown door
}
CEILING_COLOR = (212, 208, 200)
FLOOR_COLOR = (94, 102, 110)
KNOB_COLOR = (40, 170, 230)  # gold
BOLT_COLOR = (20, 150, 210)
MAX_SHADE_RANGE = 8.0


class Renderer:
    def __init__(
        self,
        world: SimWorld,
        *,
        width: int = 640,
        height: int = 480,
        fov_degrees: float = 66.0,
    ) -> None:
        self._world = world
        self._width = width
        self._height = height
        self._tan_half_fov = math.tan(math.radians(fov_degrees) / 2)
        self._focal = (width / 2) / self._tan_half_fov
        # Camera-plane offsets per column; column 0 is the left edge.
        self._plane = np.linspace(
            -self._tan_half_fov, self._tan_half_fov, width
        )
        self._background = self._build_background()
        self._wall_kinds = np.array(
            [WALL_COLORS.get(w.kind, WALL_COLORS["wall"]) for w in world.walls],
            dtype=np.float64,
        )
        self._segments = np.array(
            [[w.ax, w.ay, w.bx, w.by] for w in world.walls], dtype=np.float64
        )

    def render(self) -> tuple[np.ndarray, np.ndarray]:
        """Render the robot's view. Returns (BGR image, per-column depth)."""
        pose = self._world.pose
        forward = np.array([math.cos(pose.heading), math.sin(pose.heading)])
        right = np.array([math.sin(pose.heading), -math.cos(pose.heading)])
        dirs = forward[None, :] + self._plane[:, None] * right[None, :]

        depth, wall_index, u = raycast_segments(
            self._segments, np.array([pose.x, pose.y]), dirs
        )
        frame = self._background.copy()
        self._draw_walls(frame, depth, wall_index, u)
        for sprite, distance in sorted(
            self._sprites(), key=lambda item: -item[1]
        ):
            self._draw_sprite(frame, depth, sprite, forward, right)
        return frame, depth

    def _draw_walls(
        self,
        frame: np.ndarray,
        depth: np.ndarray,
        wall_index: np.ndarray,
        u: np.ndarray,
    ) -> None:
        height = self._height
        horizon = height / 2
        for col in range(self._width):
            dist = depth[col]
            if not np.isfinite(dist):
                continue
            top = horizon - (WALL_HEIGHT - CAMERA_HEIGHT) * self._focal / dist
            bottom = horizon + CAMERA_HEIGHT * self._focal / dist
            y0 = max(0, int(top))
            y1 = min(height, int(bottom) + 1)
            if y1 <= y0:
                continue
            shade = float(np.clip(1.1 - dist / MAX_SHADE_RANGE, 0.3, 1.0))
            shade *= 0.94 + 0.06 * math.sin(u[col] * 24.0)
            frame[y0:y1, col] = self._wall_kinds[wall_index[col]] * shade

    def _sprites(self) -> list[tuple[dict[str, Any], float]]:
        pose = self._world.pose
        sprites: list[tuple[dict[str, Any], float]] = []
        for obj in self._world.objects:
            sprites.append(
                (
                    {
                        "x": obj.x,
                        "y": obj.y,
                        "half_width": obj.radius,
                        "half_height": obj.radius,
                        "height": obj.height,
                        "color": obj.color,
                    },
                    math.hypot(obj.x - pose.x, obj.y - pose.y),
                )
            )
        sprites.extend(self._door_decals())
        return sprites

    def _door_decals(self) -> list[tuple[dict[str, Any], float]]:
        door = self._world.door_segment()
        if door is None:
            return []
        pose = self._world.pose
        mid = np.array([(door.ax + door.bx) / 2, (door.ay + door.by) / 2])
        # Nudge decals slightly off the door plane toward the robot so the
        # depth test puts them in front of the door surface.
        toward_robot = np.array([pose.x, pose.y]) - mid
        norm = float(np.hypot(*toward_robot)) or 1.0
        offset = mid + toward_robot / norm * 0.04
        along = np.array([door.bx - door.ax, door.by - door.ay])
        along = along / (float(np.hypot(*along)) or 1.0)
        knob = offset + along * 0.3
        locked = self._world.door_locked
        decals = [
            {
                "x": knob[0],
                "y": knob[1],
                "half_width": 0.04,
                "half_height": 0.04,
                "height": 0.35,
                "color": KNOB_COLOR,
            },
            {
                "x": offset[0],
                "y": offset[1],
                "half_width": 0.14 if locked else 0.03,
                "half_height": 0.03 if locked else 0.14,
                "height": 0.5,
                "color": BOLT_COLOR,
            },
        ]
        distance = float(np.hypot(offset[0] - pose.x, offset[1] - pose.y))
        return [(decal, distance) for decal in decals]

    def _draw_sprite(
        self,
        frame: np.ndarray,
        depth: np.ndarray,
        sprite: dict[str, Any],
        forward: np.ndarray,
        right: np.ndarray,
    ) -> None:
        pose = self._world.pose
        rel = np.array([sprite["x"] - pose.x, sprite["y"] - pose.y])
        z = float(rel @ forward)  # perpendicular distance ahead
        if z < 0.15:
            return
        lateral = float(rel @ right)
        center_col = (lateral / z / self._tan_half_fov + 1) / 2 * self._width
        center_row = (
            self._height / 2
            + (CAMERA_HEIGHT - sprite["height"]) * self._focal / z
        )
        half_w = max(1, int(sprite["half_width"] * self._focal / z))
        half_h = max(1, int(sprite["half_height"] * self._focal / z))
        c0 = max(0, int(center_col) - half_w)
        c1 = min(self._width, int(center_col) + half_w + 1)
        for col in range(c0, c1):
            if z >= depth[col]:
                continue
            dx = (col - center_col) / max(half_w, 1)
            column_half = half_h * math.sqrt(max(0.0, 1.0 - dx * dx))
            y0 = max(0, int(center_row - column_half))
            y1 = min(self._height, int(center_row + column_half) + 1)
            if y1 > y0:
                frame[y0:y1, col] = sprite["color"]

    def _build_background(self) -> np.ndarray:
        frame = np.zeros((self._height, self._width, 3), dtype=np.uint8)
        horizon = self._height // 2
        frame[:horizon] = CEILING_COLOR
        floor_rows = self._height - horizon
        fade = np.linspace(0.55, 1.0, floor_rows)[:, None]
        frame[horizon:] = (np.array(FLOOR_COLOR)[None, None, :] * fade[:, :, None]).astype(
            np.uint8
        )
        return frame


__all__ = ["Renderer", "SimObject"]
