import ast
import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr
from fractions import Fraction
from pathlib import Path
from typing import Set

import yaml

from media_checker.checker import check, normalize_metrics
from media_checker.cli import (
    EXIT_CONFIGURATION,
    EXIT_METRIC_FAILED,
    EXIT_SUCCESS,
    build_parser,
    run,
)
from media_checker.errors import ConfigurationError
from media_checker.models import CheckRequest, MediaDescriptor, RAW_MEDIA_TYPE


class TtyStringIO(io.StringIO):
    def isatty(self):
        return True


def _raw_document():
    return "\n".join([
        "input:",
        "  path: input.raw",
        "  width: 4",
        "  height: 2",
        '  framerate: "24/1"',
        "  format: GRAY8",
        "  frame_count: 1",
        "  stride: 4",
        "  sliceheight: 2",
    ])


class CliTests(unittest.TestCase):
    def test_cli_writes_success_result_with_short_arguments(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "input.raw").write_bytes(bytes(8))
            descriptor = root / "input.yaml"
            descriptor.write_text(_raw_document(), encoding = "utf-8")
            output = root / "result.yaml"

            exit_status = run([
                "-i", str(descriptor),
                "-c", "width", "height", "width",
                "-o", str(output),
            ])
            result = yaml.safe_load(output.read_text(encoding = "utf-8"))

            self.assertEqual(exit_status, EXIT_SUCCESS)
            self.assertEqual(result["status"], "success")
            self.assertEqual(list(result["metrics"]), ["width", "height"])
            self.assertNotIn("schema_version", result)

    def test_cli_uses_default_output_and_reports_partial_result(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "input.raw").write_bytes(bytes(8))
            descriptor = root / "input.yaml"
            descriptor.write_text(_raw_document(), encoding = "utf-8")
            previous_directory = Path.cwd()

            try:
                os.chdir(root)
                exit_status = run([
                    "--input", str(descriptor),
                    "--check", "width", "psnr",
                ])
            finally:
                os.chdir(previous_directory)

            result_path = root / "metrics-result.yaml"
            result = yaml.safe_load(result_path.read_text(encoding = "utf-8"))

            self.assertEqual(exit_status, EXIT_METRIC_FAILED)
            self.assertEqual(result["status"], "partial")
            self.assertEqual(result["metrics"]["psnr"]["status"], "error")
            self.assertIsInstance(result["metrics"]["psnr"]["error"], str)
            self.assertNotIn("code", result["metrics"]["psnr"])

    def test_cli_loads_reference_from_the_input_document(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "input.raw").write_bytes(bytes(8))
            (root / "reference.raw").write_bytes(bytes(8))
            descriptor = root / "input.yaml"
            descriptor.write_text(
                _raw_document() + "\n" + "\n".join([
                    "reference:",
                    "  path: reference.raw",
                    "  width: 4",
                    "  height: 2",
                    '  framerate: "24/1"',
                    "  format: GRAY8",
                    "  frame_count: 1",
                    "  stride: 4",
                    "  sliceheight: 2",
                ]),
                encoding = "utf-8",
            )
            output = root / "result.yaml"

            exit_status = run([
                "--input", str(descriptor),
                "--check", "psnr",
                "--output", str(output),
            ])
            result = yaml.safe_load(output.read_text(encoding = "utf-8"))

            self.assertEqual(exit_status, EXIT_SUCCESS)
            self.assertEqual(result["metrics"]["psnr"]["value"], 1000.0)

    def test_configuration_error_has_no_schema_version_or_error_code(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "result.yaml"

            exit_status = run([
                "--input", "unused.yaml",
                "--check", "unknown",
                "--output", str(output),
            ])
            result = yaml.safe_load(output.read_text(encoding = "utf-8"))

            self.assertEqual(exit_status, EXIT_CONFIGURATION)
            self.assertEqual(result["status"], "failed")
            self.assertIsInstance(result["error"], str)
            self.assertEqual(result["metrics"], {})
            self.assertNotIn("schema_version", result)
            self.assertNotIn("code", result)

    def test_metric_names_are_deduplicated_in_order(self):
        self.assertEqual(
            normalize_metrics(["width", "psnr", "width"]),
            ("width", "psnr"),
        )

    def test_core_checker_rejects_an_empty_metric_request(self):
        descriptor = MediaDescriptor(
            path        = Path("unused.raw"),
            media_type  = RAW_MEDIA_TYPE,
            extension   = ".raw",
            width       = 4,
            height      = 2,
            framerate   = Fraction(24, 1),
            format      = "GRAY8",
            frame_count = 1,
            stride      = 4,
            sliceheight = 2,
        )

        with self.assertRaises(ConfigurationError):
            check(CheckRequest(
                input     = descriptor,
                reference = None,
                metrics   = (),
            ))


class HelpTests(unittest.TestCase):
    def test_help_uses_expected_capitalization_layout_and_metrics(self):
        output = io.StringIO()

        build_parser().print_help(file = output)
        help_text = output.getvalue()

        self.assertIn("Usage: media-check", help_text)
        self.assertIn("Options:", help_text)
        self.assertIn("-h, --help", help_text)
        self.assertIn("Show this help message and exit", help_text)
        self.assertIn("-i, --input INPUT", help_text)
        self.assertIn("-c, --check METRIC [METRIC ...]", help_text)
        self.assertIn("-o, --output OUTPUT", help_text)
        self.assertNotIn("-i INPUT, --input INPUT", help_text)

        metric_positions = [
            help_text.index("  - {}".format(metric))
            for metric in ("width", "height", "framerate", "level", "profile", "psnr")
        ]
        self.assertEqual(metric_positions, sorted(metric_positions))

    def test_help_colors_only_the_usage_command_for_a_terminal(self):
        terminal_output = TtyStringIO()
        redirected_output = io.StringIO()

        parser = build_parser()
        parser.print_help(file = terminal_output)
        parser.print_help(file = redirected_output)

        terminal_help = terminal_output.getvalue()
        redirected_help = redirected_output.getvalue()
        self.assertIn("Usage: \x1b[33mmedia-check", terminal_help)
        self.assertIn("\x1b[0m", terminal_help)
        self.assertNotIn("\x1b[", redirected_help)

    def test_removed_reference_argument_prints_full_help_and_error(self):
        error_output = io.StringIO()

        with redirect_stderr(error_output):
            with self.assertRaisesRegex(SystemExit, "2"):
                run([
                    "--input", "input.yaml",
                    "--check", "width",
                    "--reference", "reference.yaml",
                ])

        error_text = error_output.getvalue()
        self.assertIn("Usage: media-check", error_text)
        self.assertIn("Options:", error_text)
        self.assertIn("Error: Unrecognized arguments", error_text)


class IsolationTests(unittest.TestCase):
    def test_runtime_does_not_import_subprocess_or_existing_scripts(self):
        source_root = Path(__file__).parents[1] / "src" / "media_checker"
        forbidden   = {"subprocess", "scripts"}

        for path in source_root.glob("*.py"):
            with self.subTest(path = path.name):
                tree = ast.parse(path.read_text(encoding = "utf-8"))
                imported_roots = set()  # type: Set[str]

                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        imported_roots.update(
                            alias.name.split(".", 1)[0]
                            for alias in node.names
                        )
                    elif isinstance(node, ast.ImportFrom) and node.module:
                        imported_roots.add(node.module.split(".", 1)[0])

                self.assertFalse(imported_roots & forbidden)


if __name__ == "__main__":
    unittest.main()
