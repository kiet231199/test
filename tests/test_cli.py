import ast
import os
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

import yaml

from media_checker.checker import check, normalize_metrics
from media_checker.cli import (
    EXIT_CONFIGURATION,
    EXIT_METRIC_FAILED,
    EXIT_SUCCESS,
    run,
)
from media_checker.errors import ConfigurationError
from media_checker.models import CheckRequest, MediaDescriptor, RAW_MEDIA_TYPE


class CliTests(unittest.TestCase):
    def test_cli_writes_success_result_with_short_arguments(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "input.raw").write_bytes(bytes(8))
            descriptor = root / "input.yaml"
            descriptor.write_text(
                "\n".join([
                    "path: input.raw",
                    "width: 4",
                    "height: 2",
                    'framerate: "24/1"',
                    "format: GRAY8",
                    "frame_count: 1",
                    "stride: 4",
                    "sliceheight: 2",
                ]),
                encoding = "utf-8",
            )
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
            descriptor.write_text(
                "\n".join([
                    "path: input.raw",
                    "width: 4",
                    "height: 2",
                    'framerate: "24/1"',
                    "format: GRAY8",
                    "frame_count: 1",
                    "stride: 4",
                    "sliceheight: 2",
                ]),
                encoding = "utf-8",
            )
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


class IsolationTests(unittest.TestCase):
    def test_runtime_does_not_import_subprocess_or_existing_scripts(self):
        source_root = Path(__file__).parents[1] / "src" / "media_checker"
        forbidden   = {"subprocess", "scripts"}

        for path in source_root.glob("*.py"):
            with self.subTest(path = path.name):
                tree = ast.parse(path.read_text(encoding = "utf-8"))
                imported_roots = set()

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
