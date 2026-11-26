from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from valle.observability import _metrics_to_records, _otlp_metrics_endpoint
from valle.telemetry import read_cpu_temperature_c, read_wifi_signal_dbm


class TelemetryTest(unittest.TestCase):
    def test_cpu_temperature_reads_millidegrees(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "temp"
            path.write_text("48312\n")
            self.assertAlmostEqual(read_cpu_temperature_c(path), 48.312)
            self.assertIsNone(read_cpu_temperature_c(Path(temp_dir) / "missing"))

    def test_wifi_signal_parses_proc_net_wireless(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "wireless"
            path.write_text(
                "Inter-| sta-|   Quality        |   Discarded packets\n"
                " face | tus | link level noise |  nwid  crypt   frag\n"
                "wlan0: 0000   58.  -52.  -256        0      0      0\n"
            )
            self.assertEqual(read_wifi_signal_dbm(path), -52.0)

    def test_otlp_metrics_endpoint_from_traces_or_base_url(self) -> None:
        self.assertEqual(
            _otlp_metrics_endpoint("http://h:4318/v1/traces"), "http://h:4318/v1/metrics"
        )
        self.assertEqual(_otlp_metrics_endpoint("http://h:4318/"), "http://h:4318/v1/metrics")

    def test_metrics_records_skip_empty_histograms(self) -> None:
        sum_point = SimpleNamespace(time_unix_nano=1, attributes={"loop": "seek"}, value=3)
        empty_hist = SimpleNamespace(
            time_unix_nano=1, attributes={}, bucket_counts=[], count=0, sum=0, min=0, max=0
        )
        data = SimpleNamespace(
            resource_metrics=[
                SimpleNamespace(
                    scope_metrics=[
                        SimpleNamespace(
                            metrics=[
                                SimpleNamespace(
                                    name="valle.brain.ticks", unit="{tick}",
                                    data=SimpleNamespace(data_points=[sum_point]),
                                ),
                                SimpleNamespace(
                                    name="valle.brain.tick.duration", unit="s",
                                    data=SimpleNamespace(data_points=[empty_hist]),
                                ),
                            ]
                        )
                    ]
                )
            ]
        )
        records = _metrics_to_records(data)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["value"], 3)
        self.assertEqual(records[0]["attributes"], {"loop": "seek"})


class SeekTelemetryTest(unittest.TestCase):
    def test_seek_blind_result_is_recorded(self) -> None:
        from unittest.mock import patch

        from valle.brain.find import seek as seek_module

        loop = seek_module.SeekLoop.__new__(seek_module.SeekLoop)
        loop._run = Mock(return_value={"found": False, "reason": "blind", "elapsed_seconds": 2.0})
        with patch.object(seek_module.telemetry, "record_seek_result") as record:
            result = loop.run(object_query="box", max_seconds=30)
        self.assertEqual(result["reason"], "blind")
        record.assert_called_once_with(found=False, reason="blind", seconds=2.0)


if __name__ == "__main__":
    unittest.main()
