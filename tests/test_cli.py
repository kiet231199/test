import ast
import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from fractions import Fraction
from pathlib import Path
from typing import Set
from unittest.mock import patch

import yaml

from media_checker.checker import check, normalize_metrics
from media_checker.cli import (
    EXIT_CONFIGURATION,
    EXIT_METRIC_FAILED,
    EXIT_SUCCESS,
    _print_short_result,
    build_parser,
    run,
)
from media_checker.errors import ConfigurationError
from media_checker.metrics import METRIC_HANDLERS
from media_checker.models import (
    RAW_MEDIA_TYPE,
    CheckRequest,
    CheckResult,
    MediaDescriptor,
    MetricResult,
)


class TtyStringIO(io.StringIO):
    def isatty(self):
        return True


class BrokenStringIO(io.StringIO):
    def write(self, value):
        raise OSError("stdout unavailable")


def _raw_document(path: str = "input.raw"):
    return "\n".join([
        "input:",
        "  path: {}".format(path),
        "  width: 4",
        "  height: 2",
        "  format: GRAY8",
    ])


def _raw_pair_document():
    return _raw_document() + "\n" + "\n".join([
        "reference:",
        "  path: reference.raw",
        "  width: 4",
        "  height: 2",
        "  format: GRAY8",
    ])


class CliTests(unittest.TestCase):
    def test_cli_writes_success_result_with_short_arguments(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "input.raw").write_bytes(bytes(8))
            (root / "reference.raw").write_bytes(bytes(8))
            descriptor = root / "input.yaml"
            descriptor.write_text(_raw_pair_document(), encoding = "utf-8")
            output = root / "result.yaml"

            console = io.StringIO()

            with redirect_stdout(console):
                exit_status = run([
                    "-i", str(descriptor),
                    "-c", "psnr", "psnr",
                    "-o", str(output),
                ])
            result = yaml.safe_load(output.read_text(encoding = "utf-8"))

            self.assertEqual(exit_status, EXIT_SUCCESS)
            self.assertEqual(result["status"], "success")
            self.assertEqual(list(result["metrics"]), ["psnr"])
            self.assertNotIn("schema_version", result)
            self.assertEqual(console.getvalue(), "psnr: success\n")
            self.assertNotIn("1000.0", console.getvalue())

    def test_cli_uses_default_output_and_reports_partial_result(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "input.raw").write_bytes(bytes(8))
            (root / "reference.raw").write_bytes(bytes(8))
            descriptor = root / "input.yaml"
            descriptor.write_text(_raw_pair_document(), encoding = "utf-8")
            previous_directory = Path.cwd()

            try:
                os.chdir(root)
                console = io.StringIO()

                with redirect_stdout(console):
                    exit_status = run([
                        "--input", str(descriptor),
                        "--check", "width", "psnr",
                    ])
            finally:
                os.chdir(previous_directory)

            result_path = root / "result.yaml"
            result = yaml.safe_load(result_path.read_text(encoding = "utf-8"))

            self.assertEqual(exit_status, EXIT_METRIC_FAILED)
            self.assertEqual(result["status"], "partial")
            self.assertEqual(result["metrics"]["width"]["status"], "error")
            self.assertEqual(
                result["metrics"]["width"]["value"],
                "Unsupported metrics",
            )
            self.assertEqual(result["metrics"]["psnr"]["value"], 1000.0)
            self.assertNotIn("error", result["metrics"]["width"])
            self.assertFalse((root / "metrics-result.yaml").exists())
            self.assertEqual(
                console.getvalue(),
                "width: error\n  Unsupported metrics\npsnr: success\n",
            )

    def test_cli_loads_reference_from_the_input_document(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "input.raw").write_bytes(bytes(8))
            (root / "reference.raw").write_bytes(bytes(8))
            descriptor = root / "input.yaml"
            descriptor.write_text(
                _raw_pair_document(),
                encoding = "utf-8",
            )
            output = root / "result.yaml"

            with redirect_stdout(io.StringIO()):
                exit_status = run([
                    "--input", str(descriptor),
                    "--check", "psnr",
                    "--output", str(output),
                ])
            result = yaml.safe_load(output.read_text(encoding = "utf-8"))

            self.assertEqual(exit_status, EXIT_SUCCESS)
            self.assertEqual(result["metrics"]["psnr"]["value"], 1000.0)

    def test_metric_error_is_written_in_the_value_field(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "input.raw").write_bytes(bytes(8))
            descriptor = root / "input.yaml"
            descriptor.write_text(_raw_document(), encoding = "utf-8")
            output = root / "result.yaml"

            with redirect_stdout(io.StringIO()):
                exit_status = run([
                    "--input", str(descriptor),
                    "--check", "psnr",
                    "--output", str(output),
                ])
            result = yaml.safe_load(output.read_text(encoding = "utf-8"))

            self.assertEqual(exit_status, EXIT_METRIC_FAILED)
            self.assertEqual(result["metrics"]["psnr"]["status"], "error")
            self.assertIsInstance(result["metrics"]["psnr"]["value"], str)
            self.assertNotIn("error", result["metrics"]["psnr"])

    def test_configuration_error_has_no_schema_version_or_error_code(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "result.yaml"

            console = io.StringIO()

            with redirect_stdout(console):
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
            self.assertEqual(console.getvalue(), "")

    def test_metric_names_are_deduplicated_in_order(self):
        self.assertEqual(
            normalize_metrics(["width", "psnr", "width"]),
            ("width", "psnr"),
        )

    def test_bare_check_selects_every_metric_in_registry_order(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "input.raw").write_bytes(bytes(8))
            (root / "reference.raw").write_bytes(bytes(8))
            descriptor = root / "input.yaml"
            descriptor.write_text(_raw_pair_document(), encoding = "utf-8")
            output = root / "result.yaml"

            console = io.StringIO()

            with redirect_stdout(console):
                exit_status = run([
                    "--input", str(descriptor),
                    "--check",
                    "--output", str(output),
                ])
            result = yaml.safe_load(output.read_text(encoding = "utf-8"))

            self.assertEqual(exit_status, EXIT_METRIC_FAILED)
            self.assertEqual(list(result["metrics"]), list(METRIC_HANDLERS))
            self.assertEqual(result["metrics"]["width"]["status"], "error")
            self.assertEqual(result["metrics"]["psnr"]["status"], "success")
            self.assertTrue(console.getvalue().startswith("width: error\n"))
            self.assertTrue(console.getvalue().endswith("psnr: success\n"))

    def test_short_result_colors_only_statuses_for_a_terminal(self):
        result = CheckResult(
            status = "partial",
            metrics = {
                "width" : MetricResult.success(16),
                "level" : MetricResult.failure("first line\nsecond line"),
            },
        )
        terminal_output = TtyStringIO()
        redirected_output = io.StringIO()

        _print_short_result(result, file = terminal_output)
        _print_short_result(result, file = redirected_output)

        self.assertEqual(
            terminal_output.getvalue(),
            "width: \x1b[32msuccess\x1b[0m\n"
            "level: \x1b[31merror\x1b[0m\n"
            "  first line\n"
            "  second line\n",
        )
        self.assertEqual(
            redirected_output.getvalue(),
            "width: success\n"
            "level: error\n"
            "  first line\n"
            "  second line\n",
        )

    def test_output_write_failure_does_not_print_short_result(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "input.raw").write_bytes(bytes(8))
            (root / "reference.raw").write_bytes(bytes(8))
            descriptor = root / "input.yaml"
            descriptor.write_text(_raw_pair_document(), encoding = "utf-8")
            console = io.StringIO()
            errors = io.StringIO()

            with patch(
                "media_checker.cli.write_result",
                side_effect = OSError("write failed"),
            ), redirect_stdout(console), redirect_stderr(errors):
                exit_status = run([
                    "--input", str(descriptor),
                    "--check", "psnr",
                    "--output", str(root / "result.yaml"),
                ])

            self.assertEqual(exit_status, EXIT_CONFIGURATION)
            self.assertEqual(console.getvalue(), "")
            self.assertIn("Cannot write result", errors.getvalue())

    def test_stdout_failure_preserves_written_result(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "input.raw").write_bytes(bytes(8))
            (root / "reference.raw").write_bytes(bytes(8))
            descriptor = root / "input.yaml"
            descriptor.write_text(_raw_pair_document(), encoding = "utf-8")
            output = root / "result.yaml"
            errors = io.StringIO()

            with redirect_stdout(BrokenStringIO()), redirect_stderr(errors):
                exit_status = run([
                    "--input", str(descriptor),
                    "--check", "psnr",
                    "--output", str(output),
                ])

            self.assertEqual(exit_status, EXIT_CONFIGURATION)
            self.assertTrue(output.is_file())
            self.assertIn("Cannot display result", errors.getvalue())

    def test_check_flag_itself_remains_required(self):
        error_output = io.StringIO()

        with redirect_stderr(error_output):
            with self.assertRaisesRegex(SystemExit, "2"):
                run(["--input", "input.yaml"])

        self.assertIn(
            "Error: The following arguments are required",
            error_output.getvalue(),
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
        self.assertIn("-c, --check [METRIC ...]", help_text)
        self.assertIn("omit names to calculate all", help_text)
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
