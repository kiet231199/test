import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from media_checker.cli import EXIT_CONFIGURATION, build_parser, run
from media_checker.models import CheckResult, MetricResult
from media_checker.result_io import write_result


class ResultFormatTests(unittest.TestCase):
    def test_yaml_text_and_json_preserve_the_same_mapping(self):
        result = CheckResult(
            status = "success",
            metrics = {
                "codec" : MetricResult.success("h265"),
                "bitrate" : MetricResult.success(123456),
            },
        )

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            yaml_path = root / "result.yaml"
            text_path = root / "result.txt"
            json_path = root / "result.json"

            write_result(result, yaml_path)
            write_result(result, text_path)
            write_result(result, json_path)

            yaml_text = yaml_path.read_text(encoding = "utf-8")
            text_text = text_path.read_text(encoding = "utf-8")
            json_text = json_path.read_text(encoding = "utf-8")

            self.assertEqual(text_text, yaml_text)
            self.assertEqual(json.loads(json_text), yaml.safe_load(yaml_text))
            self.assertTrue(json_text.endswith("\n"))

    def test_output_suffixes_are_case_insensitive(self):
        result = CheckResult(status = "success")

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)

            for suffix in (".TXT", ".YAML", ".JSON"):
                with self.subTest(suffix = suffix):
                    output = root / ("result" + suffix)
                    write_result(result, output)
                    self.assertTrue(output.is_file())

    def test_invalid_output_suffix_does_not_replace_existing_file(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "result.yml"
            output.write_text("preserve me", encoding = "utf-8")

            with patch("sys.stderr"):
                exit_status = run([
                    "--input", "missing.yaml",
                    "--check", "width",
                    "--output", str(output),
                ])

            self.assertEqual(exit_status, EXIT_CONFIGURATION)
            self.assertEqual(output.read_text(encoding = "utf-8"), "preserve me")

    def test_failed_atomic_replace_preserves_existing_file(self):
        result = CheckResult(status = "success")

        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "result.yaml"
            output.write_text("preserve me", encoding = "utf-8")

            with patch(
                "media_checker.result_io.os.replace",
                side_effect = OSError("replace failed"),
            ):
                with self.assertRaises(OSError):
                    write_result(result, output)

            self.assertEqual(output.read_text(encoding = "utf-8"), "preserve me")
            self.assertEqual(list(output.parent.glob(".result.yaml-*.tmp")), [])

    def test_json_configuration_failure_uses_the_existing_result_schema(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "result.JSON"

            with patch("sys.stderr"):
                exit_status = run([
                    "--input", "missing.yaml",
                    "--check", "unknown",
                    "--output", str(output),
                ])

            result = json.loads(output.read_text(encoding = "utf-8"))
            self.assertEqual(exit_status, EXIT_CONFIGURATION)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["metrics"], {})
            self.assertNotIn("schema_version", result)
            self.assertNotIn("code", result)

    def test_invalid_output_suffix_does_not_create_parent_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            parent = Path(folder) / "missing"
            output = parent / "result.yml"

            with patch("sys.stderr"):
                exit_status = run([
                    "--input", "missing.yaml",
                    "--check", "width",
                    "--output", str(output),
                ])

            self.assertEqual(exit_status, EXIT_CONFIGURATION)
            self.assertFalse(parent.exists())

    def test_help_lists_every_new_metric_and_output_format(self):
        help_text = build_parser().format_help()

        for metric in (
            "codec",
            "bitrate",
            "gop",
            "interval-intraframe",
            "pframes",
            "bframes",
            "refframes",
            "frame_count",
            "scan_type",
            "crop",
        ):
            self.assertIn("  - {}".format(metric), help_text)

        self.assertIn(".txt, .yaml, or .json", help_text)
        self.assertIn("default: result.yaml", help_text)


if __name__ == "__main__":
    unittest.main()
