from __future__ import annotations

import math
import threading
import time
import unittest

import numpy as np

from valle.app import create_app
from valle.config import ValleConfig
from valle.sim.driver import SimMotorDriver
from valle.sim.render import Renderer
from valle.sim.world import Pose, build_default_world


LEMON_BGR = (0, 215, 255)


class WorldPhysicsTest(unittest.TestCase):
    def test_forward_moves_along_heading(self) -> None:
        world = build_default_world()  # starts at (3.0, 1.2) facing north
        world.set_motion("forward", 1.0)
        world.step(1.0)

        self.assertAlmostEqual(world.pose.x, 3.0, places=5)
        self.assertAlmostEqual(world.pose.y, 1.7, places=5)  # 0.5 m/s

    def test_wall_collision_stops_the_robot(self) -> None:
        world = build_default_world()
        world.set_motion("forward", 1.0)
        for _ in range(200):
            world.step(0.1)  # drive north into the door wall at y=5

        self.assertLessEqual(world.pose.y, 5.0 - 0.1)  # never through the wall

    def test_turns_change_heading(self) -> None:
        world = build_default_world()
        start = world.pose.heading
        world.set_motion("left", 0.5)
        world.step(0.1)
        self.assertGreater(world.pose.heading, start)

        world.set_motion("right", 0.5)
        world.step(0.2)
        self.assertLess(world.pose.heading, start)

    def test_driver_maps_to_world_motion(self) -> None:
        world = build_default_world()
        driver = SimMotorDriver(world)

        driver.forward(0.6)
        self.assertEqual((world.action, world.speed), ("forward", 0.6))
        driver.stop()
        self.assertEqual(world.action, "stopped")

    def test_clearance_strips_reflect_distance(self) -> None:
        world = build_default_world()
        _, center_far, _ = world.clearance_strips()

        world.pose = Pose(x=3.0, y=4.7, heading=math.pi / 2)  # 0.3 m from wall
        _, center_near, _ = world.clearance_strips()

        self.assertLess(center_far, 0.55)
        self.assertGreater(center_near, 0.55)
        self.assertGreater(center_near, center_far)

    def test_state_reports_door_and_objects(self) -> None:
        world = build_default_world()
        state = world.state()

        self.assertTrue(state["door"]["locked"])
        lemon = next(o for o in state["objects"] if o["name"] == "lemon")
        self.assertAlmostEqual(lemon["distance"], math.hypot(1.5, 2.3), places=2)

        world.set_door_locked(False)
        self.assertFalse(world.state()["door"]["locked"])

    def test_reset_restores_start(self) -> None:
        world = build_default_world()
        world.set_motion("forward", 1.0)
        world.step(1.0)
        world.set_door_locked(False)

        world.reset()

        self.assertAlmostEqual(world.pose.y, 1.2, places=5)
        self.assertTrue(world.door_locked)
        self.assertEqual(world.action, "stopped")


class RendererTest(unittest.TestCase):
    def test_frame_shape_and_speed(self) -> None:
        world = build_default_world()
        renderer = Renderer(world, width=320, height=240)

        started = time.monotonic()
        frame, depth = renderer.render()
        elapsed = time.monotonic() - started

        self.assertEqual(frame.shape, (240, 320, 3))
        self.assertEqual(depth.shape, (320,))
        self.assertLess(elapsed, 0.5)

    def test_lemon_visible_when_facing_it(self) -> None:
        world = build_default_world()
        world.pose = Pose(x=1.5, y=2.0, heading=math.pi / 2)  # lemon dead ahead
        frame, _ = Renderer(world, width=320, height=240).render()
        facing = _count_color(frame, LEMON_BGR)

        world.pose = Pose(x=1.5, y=2.0, heading=-math.pi / 2)  # facing away
        frame, _ = Renderer(world, width=320, height=240).render()
        away = _count_color(frame, LEMON_BGR)

        self.assertGreater(facing, 10)
        self.assertEqual(away, 0)

    def test_deadbolt_rendering_changes_with_lock_state(self) -> None:
        world = build_default_world()
        world.pose = Pose(x=3.0, y=3.8, heading=math.pi / 2)  # facing the door
        renderer = Renderer(world, width=320, height=240)

        locked_frame, _ = renderer.render()
        world.set_door_locked(False)
        unlocked_frame, _ = renderer.render()

        self.assertGreater(int(np.count_nonzero(locked_frame != unlocked_frame)), 100)

    def test_depth_shrinks_as_robot_approaches_wall(self) -> None:
        world = build_default_world()
        renderer = Renderer(world, width=320, height=240)
        _, depth_far = renderer.render()

        world.pose = Pose(x=3.0, y=4.0, heading=math.pi / 2)
        _, depth_near = renderer.render()

        center = slice(140, 180)
        self.assertLess(
            float(depth_near[center].mean()), float(depth_far[center].mean())
        )


class SimEndToEndTest(unittest.TestCase):
    """Drive the real Pi control server against the simulated robot."""

    def setUp(self) -> None:
        self.world = build_default_world()
        self.app = create_app(ValleConfig(), driver=SimMotorDriver(self.world))
        from valle.sim.server import create_sim_blueprint

        self.app.register_blueprint(create_sim_blueprint(self.world))
        self.client = self.app.test_client()
        self._stepping = threading.Event()
        self._thread = threading.Thread(target=self._step_world, daemon=True)
        self._thread.start()
        self.addCleanup(self._stop_stepping)

    def _step_world(self) -> None:
        while not self._stepping.is_set():
            self.world.step(0.01)
            time.sleep(0.01)

    def _stop_stepping(self) -> None:
        self._stepping.set()
        self._thread.join(timeout=1.0)

    def _post_reflex(self) -> None:
        left, center, right = self.world.clearance_strips()
        response = self.client.post(
            "/agent/reflex",
            json={"left": left, "center": center, "right": right, "source": "sim"},
        )
        self.assertEqual(response.status_code, 200)

    def test_momentary_command_moves_the_sim_robot(self) -> None:
        start_y = self.world.pose.y
        response = self.client.get("/forward?duration=0.3&speed=100")
        self.assertEqual(response.status_code, 200)

        time.sleep(0.5)  # pulse runs 0.3s, then auto-stop
        self.assertGreater(self.world.pose.y, start_y + 0.05)
        self.assertEqual(self.world.action, "stopped")

    def test_agent_mission_drive_is_gated_and_moves_robot(self) -> None:
        started = self.client.post("/agent/start", json={"goal": "find the lemon"})
        self.assertEqual(started.status_code, 201)
        session_id = started.get_json()["session_id"]

        # No reflex reading yet: intent must be vetoed.
        vetoed = self.client.post(
            f"/agent/{session_id}/intent",
            json={"type": "drive_pulse", "direction": "forward", "duration": 0.2},
        ).get_json()
        self.assertFalse(vetoed["executed"])
        self.assertEqual(vetoed["reflex"]["reason"], "no_reflex_reading")

        # Fresh ground-truth clearance from the sim: intent executes and moves.
        self._post_reflex()
        start_y = self.world.pose.y
        executed = self.client.post(
            f"/agent/{session_id}/intent",
            json={
                "type": "drive_pulse",
                "direction": "forward",
                "duration": 0.3,
                "speed": 100,
            },
        ).get_json()
        self.assertTrue(executed["executed"])
        time.sleep(0.5)
        self.assertGreater(self.world.pose.y, start_y + 0.05)

        self.client.post(
            f"/agent/{session_id}/intent", json={"type": "stop", "reason": "manual"}
        )

    def test_forward_intent_vetoed_when_sim_wall_is_close(self) -> None:
        self.world.pose = Pose(x=3.0, y=4.7, heading=math.pi / 2)  # nose to wall
        started = self.client.post("/agent/start", json={"goal": "wall test"})
        session_id = started.get_json()["session_id"]
        self._post_reflex()

        result = self.client.post(
            f"/agent/{session_id}/intent",
            json={"type": "drive_pulse", "direction": "forward", "duration": 0.2},
        ).get_json()

        self.assertFalse(result["executed"])
        self.assertEqual(result["reflex"]["reason"], "center_blocked")

    def test_sim_endpoints_report_and_mutate_ground_truth(self) -> None:
        state = self.client.get("/sim/state").get_json()
        self.assertTrue(state["door"]["locked"])
        self.assertEqual(state["robot"]["heading_deg"], 90.0)

        toggled = self.client.post("/sim/door", json={"locked": False})
        self.assertEqual(toggled.status_code, 200)
        self.assertFalse(self.client.get("/sim/state").get_json()["door"]["locked"])

        self.assertEqual(
            self.client.post("/sim/door", json={"locked": "yes"}).status_code, 400
        )

        self.client.get("/forward?duration=0.2&speed=100")
        time.sleep(0.3)
        reset = self.client.post("/sim/reset").get_json()
        self.assertEqual(reset["robot"]["y"], 1.2)

    def test_sim_ui_and_world_geometry(self) -> None:
        page = self.client.get("/sim/ui")
        self.assertEqual(page.status_code, 200)
        body = page.get_data(as_text=True)
        self.assertIn("<canvas", body)
        self.assertIn("/stream.mjpg", body)

        world = self.client.get("/sim/world").get_json()
        self.assertEqual(world["bounds"]["max_x"], 6)
        kinds = {wall["kind"] for wall in world["walls"]}
        self.assertEqual(kinds, {"wall", "box", "door"})
        lemon = next(o for o in world["objects"] if o["name"] == "lemon")
        self.assertEqual(lemon["color"], "#ffd700")


def _count_color(frame: np.ndarray, bgr: tuple[int, int, int]) -> int:
    return int(np.count_nonzero((frame == np.array(bgr, dtype=np.uint8)).all(axis=2)))


if __name__ == "__main__":
    unittest.main()
