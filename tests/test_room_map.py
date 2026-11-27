from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np

from valle.brain.exploration import WaypointPlanner
from valle.brain.find.config import FindConfig
from valle.brain.find.explore import MapSeek
from valle.brain.config import BrainConfig
from valle.brain.room_map import RoomMapStore, map_prompt

VIEWS = {"ahead": "hall", "right": "couch", "behind": "wall", "left": "door"}


def _reply(**overrides):
    reply = {
        "matched_place_id": "",
        "summary": "living room",
        "views": VIEWS,
        "openings": ["ahead", "left"],
        "object_heading": "none",
        "heading_offset": 0,
        "next_heading": "left",
        "reason": "door unexplored",
    }
    reply.update(overrides)
    return reply


class RoomMapStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.store = RoomMapStore(Path(self._dir.name) / "map.json")

    def tearDown(self) -> None:
        self._dir.cleanup()

    def test_add_link_and_persist(self) -> None:
        a = self.store.add_place(summary="kitchen", views=VIEWS, openings=["ahead", "bogus"])
        b = self.store.add_place(
            summary="hall", views=VIEWS, openings=["behind"], came_from=a["id"], came_heading="ahead"
        )
        data = RoomMapStore(self.store._path).get()
        self.assertEqual(data["places"][0]["openings"], ["ahead"])
        self.assertEqual(data["places"][0]["explored"], ["ahead"])
        self.assertEqual(data["links"], [{"from": a["id"], "heading": "ahead", "to": b["id"]}])
        self.assertIn("kitchen", map_prompt(data))

    def test_reset_and_corrupt_file(self) -> None:
        self.store.add_place(summary="x", views=VIEWS, openings=[])
        self.store.reset()
        self.assertEqual(self.store.get()["places"], [])
        self.store._path.write_text("not json")
        self.assertEqual(self.store.get(), {"places": [], "links": []})


class WaypointPlannerTest(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.store = RoomMapStore(Path(self._dir.name) / "map.json")
        self.inspector = Mock()

    def tearDown(self) -> None:
        self._dir.cleanup()

    def test_new_place_is_recorded_and_images_labelled(self) -> None:
        self.inspector.ask_json.return_value = _reply()
        plan = WaypointPlanner(self.inspector, self.store).plan(
            dict(VIEWS), "toy", came_from=None, came_heading=None
        )
        self.assertTrue(plan.is_new_place)
        self.assertEqual(plan.next_heading, "left")
        self.assertEqual(self.store.get()["places"][0]["searched_for"], ["toy"])
        kwargs = self.inspector.ask_json.call_args.kwargs
        self.assertEqual(kwargs["labels"][0], "View ahead:")
        self.assertEqual(kwargs["effort"], "low")

    def test_matched_place_is_reused_not_duplicated(self) -> None:
        known = self.store.add_place(summary="kitchen", views=VIEWS, openings=["ahead"])
        start = self.store.add_place(summary="hall", views=VIEWS, openings=["right"])
        self.inspector.ask_json.return_value = _reply(matched_place_id=known["id"])
        plan = WaypointPlanner(self.inspector, self.store).plan(
            dict(VIEWS), "toy", came_from=start["id"], came_heading="right"
        )
        data = self.store.get()
        self.assertFalse(plan.is_new_place)
        self.assertEqual(len(data["places"]), 2)
        self.assertIn({"from": start["id"], "heading": "right", "to": known["id"]}, data["links"])

    def test_revisit_from_another_direction_converts_headings(self) -> None:
        known = self.store.add_place(summary="kitchen", views=VIEWS, openings=["ahead", "right"])
        # Robot now faces what was stored as "right": one clockwise quarter turn.
        self.inspector.ask_json.return_value = _reply(
            matched_place_id=known["id"], heading_offset=1, next_heading="ahead"
        )
        plan = WaypointPlanner(self.inspector, self.store).plan(
            dict(VIEWS), "toy", came_from=None, came_heading=None
        )
        self.assertEqual(plan.heading_offset, 1)
        self.assertEqual(plan.to_place_frame("ahead"), "right")
        self.assertEqual(plan.to_place_frame("left"), "ahead")
        # Leaving "ahead" now must mark the stored "right" explored, not "ahead".
        nxt = self.store.add_place(
            summary="hall", views=VIEWS, openings=[],
            came_from=known["id"], came_heading=plan.to_place_frame("ahead"),
        )
        kitchen = next(p for p in self.store.get()["places"] if p["id"] == known["id"])
        self.assertEqual(kitchen["explored"], ["right"])
        self.assertIn({"from": known["id"], "heading": "right", "to": nxt["id"]}, self.store.get()["links"])

    def test_new_place_ignores_offset_and_prompt_has_stored_views(self) -> None:
        self.store.add_place(summary="kitchen", views=VIEWS, openings=[])
        self.inspector.ask_json.return_value = _reply(heading_offset=2)
        plan = WaypointPlanner(self.inspector, self.store).plan(
            dict(VIEWS), "toy", came_from=None, came_heading=None
        )
        self.assertEqual(plan.heading_offset, 0)
        prompt = self.inspector.ask_json.call_args.args[1]
        self.assertIn("ahead=hall", prompt)
        self.assertIn("left=door", prompt)

    def test_unknown_match_id_and_bad_heading_are_sanitised(self) -> None:
        self.inspector.ask_json.return_value = _reply(matched_place_id="zzz", next_heading="up")
        plan = WaypointPlanner(self.inspector, self.store).plan(
            dict(VIEWS), "toy", came_from=None, came_heading=None
        )
        self.assertTrue(plan.is_new_place)
        self.assertEqual(plan.next_heading, "done")


class AskJsonTest(unittest.TestCase):
    def test_openai_path_puts_schema_in_system_prompt(self) -> None:
        from valle.brain.vision import SceneInspector

        client = Mock()
        client.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"a": 1}'))]
        )
        schema = {"type": "object", "properties": {"next_heading": {"type": "string"}}}
        SceneInspector(client, "gpt").ask_json(
            [np.zeros((4, 4, 3), np.uint8)] * 2, "plan", schema=schema, system="sys",
            labels=["View ahead:", "View right:"],
        )
        messages = client.chat.completions.create.call_args.kwargs["messages"]
        self.assertIn("next_heading", messages[0]["content"])
        self.assertEqual(messages[1]["content"][1]["text"], "View ahead:")


class _Frames:
    def __init__(self, url: str) -> None:
        self.frame = SimpleNamespace(image=np.zeros((4, 4, 3), np.uint8))

    def start(self) -> None: ...
    def stop(self) -> None: ...

    def wait_for_frame(self, timeout_seconds: float) -> bool:
        return True

    def latest(self):
        return self.frame


class MapSeekTest(unittest.TestCase):
    def _seek(self, planner, detector):
        client = Mock()
        client.start.return_value = {"session_id": "s1"}
        depth = Mock()
        seek = MapSeek(
            brain_config=BrainConfig(),
            find_config=FindConfig(seek_leg_seconds=0.0),
            detector=detector,
            depth=depth,
            planner=planner,
            client=client,
            frames_factory=_Frames,
            sleep=lambda _s: None,
        )
        return seek, client

    def test_spin_scan_finds_object_and_stops(self) -> None:
        detector = Mock()
        detector.detect.side_effect = [[], [{"score": 0.9, "label": "toy", "box": [0, 0, 1, 1]}]]
        planner = Mock()
        seek, client = self._seek(planner, detector)
        result = seek.run(object_query="toy", max_seconds=30)
        self.assertTrue(result["found"])
        self.assertEqual(result["heading"], "right")
        planner.plan.assert_not_called()
        client.stop.assert_called()

    def test_no_motion_after_planner_uses_up_deadline(self) -> None:
        detector = Mock()
        detector.detect.return_value = []
        planner = Mock()
        seek, client = self._seek(planner, detector)

        def slow_plan(*args, **kwargs):
            seek._deadline = 0.0  # the LLM call consumed the whole budget
            return SimpleNamespace(
                place_id="p1", is_new_place=True, summary="room", openings=["ahead"],
                object_heading="none", next_heading="ahead", reason="go", heading_offset=0,
                to_place_frame=lambda h: h,
            )

        planner.plan.side_effect = slow_plan
        result = seek.run(object_query="toy", max_seconds=30)
        self.assertEqual(result["reason"], "max_seconds")
        self.assertEqual(client.start.call_count, 1)  # only the scan session
        directions = [c.kwargs["direction"] for c in client.drive.call_args_list]
        self.assertNotIn("forward", directions)

    def test_seek_exception_records_error_outcome(self) -> None:
        from unittest.mock import patch

        from valle.brain.find import explore

        detector = Mock()
        detector.load.side_effect = RuntimeError("model download failed")
        seek, _client = self._seek(Mock(), detector)
        with patch.object(explore.telemetry, "record_seek_result") as record:
            with self.assertRaises(RuntimeError):
                seek.run(object_query="toy", max_seconds=30)
        self.assertEqual(record.call_args.kwargs["reason"], "error")

    def test_explored_ends_after_planner_says_done(self) -> None:
        detector = Mock()
        detector.detect.return_value = []
        planner = Mock()
        planner.plan.return_value = SimpleNamespace(
            place_id="p1", is_new_place=True, summary="room", openings=[],
            object_heading="none", next_heading="done", reason="all explored",
        )
        seek, client = self._seek(planner, detector)
        result = seek.run(object_query="toy", max_seconds=30)
        self.assertFalse(result["found"])
        self.assertEqual(result["reason"], "explored")
        self.assertEqual(sorted(planner.plan.call_args.args[0]), sorted(VIEWS))
        # Four quarter turns: three between views and one back to "ahead".
        turns = [c for c in client.drive.call_args_list if c.kwargs["direction"] == "right"]
        self.assertEqual(len(turns), 4)


if __name__ == "__main__":
    unittest.main()
