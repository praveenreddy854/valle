from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class BrainPortalTest(unittest.TestCase):
    def test_portal_lists_and_tails_log_files(self) -> None:
        try:
            from valle.brain.api import create_app
        except ModuleNotFoundError as exc:
            if exc.name == "flask":
                self.skipTest("Flask is not installed")
            raise

        with tempfile.TemporaryDirectory() as temp_dir:
            log_file = Path(temp_dir) / "valle-brain-api.log"
            log_file.write_text(
                json.dumps({"level": "INFO", "message": "portal hello"}) + "\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"VALLE_LOG_DIR": temp_dir}):
                client = create_app().test_client()
                files_response = client.get("/portal/api/files")
                tail_response = client.get(
                    "/portal/api/files/valle-brain-api.log?tail=10"
                )

        self.assertEqual(files_response.status_code, 200)
        files_payload = files_response.get_json()
        assert files_payload is not None
        self.assertEqual(files_payload["files"][0]["name"], "valle-brain-api.log")

        self.assertEqual(tail_response.status_code, 200)
        tail_payload = tail_response.get_json()
        assert tail_payload is not None
        self.assertTrue(tail_payload["ok"])
        self.assertEqual(tail_payload["lines"][0]["message"], "portal hello")

    def test_portal_lists_metrics_files(self) -> None:
        from valle.brain.api import create_app

        with tempfile.TemporaryDirectory() as temp_dir:
            (Path(temp_dir) / "valle-pi.metrics.jsonl").write_text(
                json.dumps({"name": "valle.pi.motor_commands", "unit": "1", "value": 2}) + "\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"VALLE_LOG_DIR": temp_dir}):
                client = create_app().test_client()
                files = client.get("/portal/api/files").get_json()
                tail = client.get("/portal/api/files/valle-pi.metrics.jsonl?tail=5").get_json()

        self.assertEqual(files["files"][0]["kind"], "metrics")
        self.assertEqual(tail["lines"][0]["value"], 2)

    def test_camera_proxy_requests_capped_view(self) -> None:
        from unittest.mock import MagicMock

        from valle.brain.api import create_app

        upstream = MagicMock()
        upstream.headers = {"content-type": "multipart/x-mixed-replace; boundary=FRAME"}
        upstream.iter_content.return_value = [b"--FRAME"]
        with patch.dict(
            os.environ,
            {"VALLE_PORTAL_CAMERA_URL": "http://pi:8081/stream.mjpg", "VALLE_PORTAL_CAMERA_SECONDS": "60"},
        ), patch("requests.get", return_value=upstream) as get:
            response = create_app().test_client().get("/portal/camera.mjpg")
            response.get_data()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(get.call_args.args[0], "http://pi:8081/stream.mjpg?max_seconds=60")

    def test_portal_rejects_path_traversal(self) -> None:
        try:
            from valle.brain.api import create_app
        except ModuleNotFoundError as exc:
            if exc.name == "flask":
                self.skipTest("Flask is not installed")
            raise

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(os.environ, {"VALLE_LOG_DIR": temp_dir}):
                client = create_app().test_client()
                response = client.get("/portal/api/files/../secret.log")

        self.assertEqual(response.status_code, 400)
        payload = response.get_json()
        assert payload is not None
        self.assertFalse(payload["ok"])

    def test_portal_masks_otlp_endpoint_userinfo(self) -> None:
        try:
            from valle.brain.api import create_app
        except ModuleNotFoundError as exc:
            if exc.name == "flask":
                self.skipTest("Flask is not installed")
            raise

        with patch.dict(
            os.environ,
            {
                "OTEL_EXPORTER_OTLP_ENDPOINT": (
                    "https://token:secret@example.test:4318/v1/traces"
                )
            },
        ):
            client = create_app().test_client()
            response = client.get("/portal/api/status")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        assert payload is not None
        self.assertEqual(
            payload["otel_exporter_otlp_endpoint"],
            "https://example.test:4318/v1/traces",
        )

    def test_favicon_returns_no_content(self) -> None:
        try:
            from valle.brain.api import create_app
        except ModuleNotFoundError as exc:
            if exc.name == "flask":
                self.skipTest("Flask is not installed")
            raise

        client = create_app().test_client()
        response = client.get("/favicon.ico")

        self.assertEqual(response.status_code, 204)


if __name__ == "__main__":
    unittest.main()
