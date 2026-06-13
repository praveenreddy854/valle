from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

from valle.brain.evidence import EvidenceStore
from valle.brain.notify import notify, notify_mission_result
from valle.brain.runs import RunStore, new_run_id
from valle.brain.scheduler import MissionScheduler


class EvidenceStoreTest(unittest.TestCase):
    def test_save_writes_numbered_jpegs(self) -> None:
        image = np.zeros((10, 10, 3), dtype=np.uint8)
        with tempfile.TemporaryDirectory() as tmp:
            store = EvidenceStore(tmp, "run-1")

            first = store.save(image, "Back Door (wide)")
            second = store.save(image, "lock crop")

            self.assertEqual(first["file"], "01-back_door_wide.jpg")
            self.assertEqual(second["file"], "02-lock_crop.jpg")
            self.assertTrue((Path(tmp) / "run-1" / first["file"]).exists())
            self.assertEqual(len(store.items()), 2)


class RunStoreTest(unittest.TestCase):
    def test_record_list_and_get(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = RunStore(Path(tmp) / "runs.jsonl")
            store.record({"run_id": "a", "status": "completed"})
            store.record({"run_id": "b", "status": "failed"})

            runs = store.list()
            self.assertEqual([r["run_id"] for r in runs], ["b", "a"])
            self.assertEqual(store.list(limit=1), [{"run_id": "b", "status": "failed"}])
            self.assertEqual(store.get("a"), {"run_id": "a", "status": "completed"})
            self.assertIsNone(store.get("missing"))

    def test_new_run_id_is_unique(self) -> None:
        self.assertNotEqual(new_run_id(), new_run_id())


class MissionSchedulerTest(unittest.TestCase):
    def _scheduler(
        self, path: Path, run_mission: Mock, when: datetime
    ) -> MissionScheduler:
        return MissionScheduler(path, run_mission, now=lambda: when)

    def test_runs_due_mission_once_per_day(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "missions.json"
            run_mission = Mock()
            before = self._scheduler(path, run_mission, datetime(2026, 6, 10, 21, 59))
            entry = before.add("22:00", {"goal": "check the back door lock"})

            self.assertEqual(before.check_due(), [])
            run_mission.assert_not_called()

            due = self._scheduler(path, run_mission, datetime(2026, 6, 10, 22, 0))
            self.assertEqual(due.check_due(), [entry["id"]])
            run_mission.assert_called_once_with({"goal": "check the back door lock"})
            self.assertEqual(due.check_due(), [])  # not twice the same day

            next_day = self._scheduler(path, run_mission, datetime(2026, 6, 11, 22, 5))
            self.assertEqual(next_day.check_due(), [entry["id"]])

    def test_failed_mission_is_marked_and_does_not_raise(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "missions.json"
            run_mission = Mock(side_effect=RuntimeError("Pi offline"))
            scheduler = self._scheduler(path, run_mission, datetime(2026, 6, 10, 8, 0))
            entry = scheduler.add("07:30", {"goal": "scout the floor"})

            self.assertEqual(scheduler.check_due(), [entry["id"]])
            self.assertEqual(scheduler.list()[0]["last_run_status"], "failed")

    def test_add_validates_schedule_and_mission(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            scheduler = MissionScheduler(Path(tmp) / "missions.json", Mock())
            with self.assertRaises(ValueError):
                scheduler.add("9pm", {"goal": "x"})
            with self.assertRaises(ValueError):
                scheduler.add("21:00", {})

    def test_remove_and_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "missions.json"
            scheduler = MissionScheduler(path, Mock())
            entry = scheduler.add("06:00", {"goal": "check pet bowls"})

            reloaded = MissionScheduler(path, Mock())
            self.assertEqual(len(reloaded.list()), 1)
            self.assertTrue(reloaded.remove(entry["id"]))
            self.assertFalse(reloaded.remove(entry["id"]))
            self.assertEqual(MissionScheduler(path, Mock()).list(), [])


class NotifyTest(unittest.TestCase):
    def test_noop_without_url(self) -> None:
        with patch.dict("os.environ", {"VALLE_NOTIFY_URL": ""}):
            self.assertFalse(notify("title", "message"))

    def test_posts_message_with_title_header(self) -> None:
        response = Mock()
        with patch.dict("os.environ", {"VALLE_NOTIFY_URL": "http://ntfy/valle"}):
            with patch("requests.post", return_value=response) as post:
                self.assertTrue(notify("Valle: mission", "Status: completed"))

        args, kwargs = post.call_args
        self.assertEqual(args[0], "http://ntfy/valle")
        self.assertEqual(kwargs["data"], b"Status: completed")
        self.assertEqual(kwargs["headers"]["Title"], "Valle: mission")

    def test_mission_result_message_includes_state_and_evidence(self) -> None:
        record = {
            "run_id": "r1",
            "status": "completed",
            "mission": {"goal": "check the back door lock"},
            "result": {
                "state": "locked",
                "confidence": 0.88,
                "summary": "Deadbolt is engaged.",
                "needs_followup": False,
            },
            "evidence": [{"file": "01-wide.jpg"}],
        }
        with patch.dict("os.environ", {"VALLE_NOTIFY_URL": "http://ntfy/valle"}):
            with patch("requests.post", return_value=Mock()) as post:
                self.assertTrue(notify_mission_result(record))

        body = post.call_args.kwargs["data"].decode()
        self.assertIn("State: locked", body)
        self.assertIn("Confidence: 0.88", body)
        self.assertIn("Evidence images: 1", body)


if __name__ == "__main__":
    unittest.main()
