from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from valle.brain.household import HouseholdStore, household_prompt
from valle.brain.memory import LessonStore, memory_prompt, similar_runs
from valle.brain.notify import notify_mission_result
from valle.brain.skills import apply_skill, skill_prompt
from valle.brain.vision import SceneInspector


class HouseholdTest(unittest.TestCase):
    def test_round_trip_and_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = HouseholdStore(Path(tmp) / "h.json")
            self.assertEqual(store.get()["rules"], [])
            store.put({"rules": ["Never enter the nursery"], "places": {"front_door": "end of hallway"}})
            prompt = household_prompt(store.get())
        self.assertIn("Never enter the nursery", prompt)
        self.assertIn("front_door: end of hallway", prompt)

    def test_rejects_bad_rules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                HouseholdStore(Path(tmp) / "h.json").put({"rules": "no"})


class SkillsTest(unittest.TestCase):
    def test_default_targets_and_prompt(self) -> None:
        mission = apply_skill({"goal": "prep", "skill": "vacuum_prep"})
        self.assertIn("toy", mission["targets"])
        self.assertIn("blockers_found", skill_prompt(mission))

    def test_explicit_targets_kept_and_unknown_skill_ignored(self) -> None:
        self.assertEqual(
            apply_skill({"goal": "g", "skill": "check_door_locks", "targets": ["back_door"]})["targets"],
            ["back_door"],
        )
        self.assertEqual(skill_prompt({"goal": "g", "skill": "nope"}), "")


class MemoryTest(unittest.TestCase):
    def test_add_relevant_remove(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LessonStore(Path(tmp) / "l.jsonl")
            a = store.add("bolt visible from 1m left", skill="check_door_locks")
            store.add("rug edge snags wheels", skill="vacuum_prep")
            relevant = store.relevant({"goal": "g", "skill": "check_door_locks"})
            self.assertEqual([l["id"] for l in relevant], [a["id"]])
            self.assertTrue(store.remove(a["id"]))
            self.assertFalse(store.remove(a["id"]))
            self.assertEqual(len(store.list()), 1)

    def test_similar_runs_and_prompt(self) -> None:
        runs = [
            {"mission": {"goal": "x", "skill": "check_door_locks"}, "status": "completed",
             "result": {"state": "secure", "summary": "ok"}, "started_at": "t1"},
            {"mission": {"goal": "other"}, "status": "failed"},
        ]
        past = similar_runs(runs, {"goal": "y", "skill": "check_door_locks"})
        self.assertEqual(len(past), 1)
        self.assertIn("state=secure", memory_prompt([], past))


class VisionTest(unittest.TestCase):
    def _client(self, content: str) -> Mock:
        message = SimpleNamespace(content=content)
        client = Mock()
        client.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=message)]
        )
        return client

    def test_parses_json_answer(self) -> None:
        client = self._client(json.dumps({"answer": "bolt engaged", "confidence": 0.9}))
        result = SceneInspector(client, "gpt").inspect(np.zeros((8, 8, 3), np.uint8), "bolt?")
        self.assertEqual(result["answer"], "bolt engaged")
        sent = client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
        self.assertTrue(sent[1]["image_url"]["url"].startswith("data:image/jpeg;base64,"))

    def test_non_json_raises(self) -> None:
        with self.assertRaises(RuntimeError):
            SceneInspector(self._client("sure!"), "gpt").inspect(np.zeros((8, 8, 3), np.uint8), "q")


class NotifyFindingsTest(unittest.TestCase):
    def test_hazards_in_message(self) -> None:
        record = {"status": "completed", "mission": {"goal": "vacuum prep"},
                  "findings": [{"kind": "hazard", "item": "furry toy", "location": "rug"},
                               {"kind": "observation", "item": "lamp"}]}
        with patch.dict("os.environ", {"VALLE_NOTIFY_URL": "http://ntfy/x"}):
            with patch("requests.post", return_value=Mock()) as post:
                notify_mission_result(record)
        body = post.call_args.kwargs["data"].decode()
        self.assertIn("Hazard: furry toy (rug)", body)
        self.assertNotIn("lamp", body)


class ApiEndpointsTest(unittest.TestCase):
    def test_household_memory_findings(self) -> None:
        from valle.brain.api import create_app

        with tempfile.TemporaryDirectory() as tmp:
            env = {
                "VALLE_HOUSEHOLD_FILE": f"{tmp}/h.json",
                "VALLE_LESSONS_FILE": f"{tmp}/l.jsonl",
                "VALLE_RUNS_FILE": f"{tmp}/r.jsonl",
                "VALLE_MISSIONS_FILE": f"{tmp}/m.json",
            }
            with patch.dict("os.environ", env):
                Path(env["VALLE_RUNS_FILE"]).write_text(json.dumps(
                    {"run_id": "r1", "findings": [{"kind": "hazard", "item": "sock"}]}) + "\n")
                client = create_app().test_client()
                self.assertEqual(client.put("/household", json={"rules": 1}).status_code, 400)
                self.assertEqual(client.put("/household", json={"rules": ["stay out of office"]}).status_code, 200)
                self.assertEqual(client.get("/household").get_json()["household"]["rules"], ["stay out of office"])
                self.assertIn("vacuum_prep", client.get("/skills").get_json()["skills"])
                lesson = LessonStore.from_env().add("x")
                self.assertEqual(len(client.get("/memory").get_json()["lessons"]), 1)
                self.assertEqual(client.delete(f"/memory/{lesson['id']}").status_code, 200)
                self.assertEqual(client.get("/findings").get_json()["findings"][0]["item"], "sock")


if __name__ == "__main__":
    unittest.main()
