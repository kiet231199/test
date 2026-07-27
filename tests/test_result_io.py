import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from media_checker.cli import EXIT_CONFIGURATION, build_parser, run
from media_checker.models import CheckResult, MetricResult
from media_checker.result_io import write_result


class ResultFormatTests(unittest.TestCase):
    def test_every_output_name_writes_the_same_ordered_yaml_mapping(self):
        result = CheckResult(
            status = "partial",
            metrics = {
                "codec" : MetricResult.success("h265"),
                "bitrate" : MetricResult.success(123456),
                "psnr" : MetricResult.failure("reference is required"),
                "crop" : MetricResult.not_checked(),
            },
        )

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            paths = (
                root / "result.yaml",
                root / "result.txt",
                root / "result.json",
                root / "result.report",
                root / "result",
            )

            for path in paths:
                write_result(result, path)

            contents = [path.read_text(encoding = "utf-8") for path in paths]
            self.assertEqual(contents, [contents[0]] * len(contents))
            self.assertEqual(
                yaml.safe_load(contents[0]),
                {
                    "codec" : "h265",
                    "bitrate" : 123456,
                    "psnr" : None,
                    "crop" : None,
                },
            )

    def test_arbitrary_output_names_are_accepted(self):
        result = CheckResult(status = "success")

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)

            for name in ("result.TXT", "result.YAML", "result.JSON", "report", ".hidden"):
                with self.subTest(name = name):
                    output = root / name
                    write_result(result, output)
                    self.assertTrue(output.is_file())
                    self.assertEqual(
                        yaml.safe_load(output.read_text(encoding = "utf-8")),
                        {},
                    )

    def test_arbitrary_output_suffix_replaces_existing_file(self):
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
            self.assertEqual(
                yaml.safe_load(output.read_text(encoding = "utf-8")),
                {},
            )

    def test_failed_atomic_replace_preserves_existing_file(self):
        result = CheckResult(status = "success")

        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "result.report"
            output.write_text("preserve me", encoding = "utf-8")

            with patch(
                "media_checker.result_io.os.replace",
                side_effect = OSError("replace failed"),
            ):
                with self.assertRaises(OSError):
                    write_result(result, output)

            self.assertEqual(output.read_text(encoding = "utf-8"), "preserve me")
            self.assertEqual(list(output.parent.glob(".result.report-*.tmp")), [])

    def test_interrupted_write_removes_the_temporary_file(self):
        result = CheckResult(
            status = "success",
            metrics = {
                "codec" : MetricResult.success("h264"),
            },
        )

        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "result.json"
            output.write_text("preserve me", encoding = "utf-8")

            with patch(
                "media_checker.result_io.yaml.safe_dump",
                side_effect = KeyboardInterrupt,
            ):
                with self.assertRaises(KeyboardInterrupt):
                    write_result(result, output)

            self.assertEqual(output.read_text(encoding = "utf-8"), "preserve me")
            self.assertEqual(list(output.parent.glob(".result.json-*.tmp")), [])

    def test_configuration_failure_writes_an_empty_yaml_mapping(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "result.JSON"

            with patch("sys.stderr"):
                exit_status = run([
                    "--input", "missing.yaml",
                    "--check", "unknown",
                    "--output", str(output),
                ])

            result = yaml.safe_load(output.read_text(encoding = "utf-8"))
            self.assertEqual(exit_status, EXIT_CONFIGURATION)
            self.assertEqual(result, {})

    def test_arbitrary_output_suffix_creates_parent_directory(self):
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
            self.assertEqual(
                yaml.safe_load(output.read_text(encoding = "utf-8")),
                {},
            )

    def test_help_lists_every_new_metric_and_yaml_output_behavior(self):
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

        self.assertIn("always YAML", help_text)
        self.assertIn("default: result.yaml", help_text)


if __name__ == "__main__":
    unittest.main()
