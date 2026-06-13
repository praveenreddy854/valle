from __future__ import annotations

import json
import logging
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from valle.observability import (
    PortalRequestLogFilter,
    configure_logging,
    configure_tracing,
    get_tracer,
)


class ObservabilityTest(unittest.TestCase):
    def test_configure_logging_writes_json_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(
                os.environ,
                {
                    "VALLE_LOG_DIR": temp_dir,
                    "VALLE_LOG_CONSOLE": "0",
                },
            ):
                log_file = configure_logging("valle-test")
                logging.getLogger("valle.test").info("hello")
                logging.shutdown()

            lines = Path(log_file).read_text(encoding="utf-8").splitlines()

        self.assertGreaterEqual(len(lines), 1)
        payload = json.loads(lines[-1])
        self.assertEqual(payload["service"], "valle-test")
        self.assertEqual(payload["message"], "hello")

    def test_portal_polling_filter_drops_successful_portal_requests(self) -> None:
        record = logging.LogRecord(
            "werkzeug",
            logging.INFO,
            __file__,
            1,
            '127.0.0.1 - - [09/Jun/2026] "GET /portal/api/files HTTP/1.1" 200 -',
            (),
            None,
        )

        self.assertFalse(PortalRequestLogFilter().filter(record))

    def test_portal_polling_filter_keeps_errors(self) -> None:
        record = logging.LogRecord(
            "werkzeug",
            logging.INFO,
            __file__,
            1,
            '127.0.0.1 - - [09/Jun/2026] "GET /portal/api/files HTTP/1.1" 500 -',
            (),
            None,
        )

        self.assertTrue(PortalRequestLogFilter().filter(record))

    def test_configure_tracing_writes_jsonl_file(self) -> None:
        try:
            from opentelemetry import trace
        except ImportError:
            self.skipTest("OpenTelemetry is not installed")

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(
                os.environ,
                {
                    "VALLE_LOG_DIR": temp_dir,
                    "VALLE_LOG_CONSOLE": "0",
                    "VALLE_OTEL_ENABLED": "1",
                },
            ):
                configure_logging("valle-trace-test")
                configure_tracing("valle-trace-test")
                with get_tracer("valle.test").start_as_current_span("test-span"):
                    pass
                trace.get_tracer_provider().force_flush()

            trace_file = Path(temp_dir) / "valle-trace-test.traces.jsonl"
            lines = trace_file.read_text(encoding="utf-8").splitlines()

        self.assertGreaterEqual(len(lines), 1)
        payload = json.loads(lines[-1])
        self.assertEqual(payload["name"], "test-span")


if __name__ == "__main__":
    unittest.main()
