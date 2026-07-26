import ast
import io
import os
import signal
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from fractions import Fraction
from pathlib import Path
from typing import Set
from unittest.mock import call, patch

import yaml

from media_checker.checker import CheckInterrupted, check, normalize_metrics
from media_checker.cli import (
    EXIT_CONFIGURATION,
    EXIT_INTERRUPTED,
    EXIT_METRIC_FAILED,
    EXIT_SUCCESS,
    _InterruptSignals,
    _METRIC_DESCRIPTIONS,
    _format_metrics_help,
    _print_short_result,
    build_parser,
    run,
)
from media_checker.errors import ConfigurationError, MetricError
from media_checker.metrics import METRIC_HANDLERS
from media_checker.models import (
    ENCODED_MEDIA_TYPE,
    RAW_MEDIA_TYPE,
    STATUS_NOT_CHECKED,
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


class StubMetric:
    def __init__(self, name, calls, interrupt = False, error = False):
        self.name      = name
        self.calls     = calls
        self.interrupt = interrupt
        self.error     = error

    def supports(self, context):
        return True

    def calculate(self, context):
        self.calls.append(self.name)

        if self.interrupt:
            raise KeyboardInterrupt

        if self.error:
            raise MetricError("{} failed".format(self.name))

        return self.name


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
    def test_checker_maps_effort_to_both_decoder_thread_budgets(self):
        input_descriptor = MediaDescriptor(
            path       = Path("input.264"),
            media_type = ENCODED_MEDIA_TYPE,
            extension  = ".264",
        )
        reference_descriptor = MediaDescriptor(
            path       = Path("reference.264"),
            media_type = ENCODED_MEDIA_TYPE,
            extension  = ".264",
        )
        cases = (
            ("light", 1),
            ("medium", 4),
            ("high", 0),
        )

        for effort, decoder_threads in cases:
            with self.subTest(effort = effort):
                metric_calls = []

                with patch.dict(
                    METRIC_HANDLERS,
                    {"width" : StubMetric("width", metric_calls)},
                    clear = True,
                ), patch(
                    "media_checker.checker.create_video_source",
                    return_value = object(),
                ) as create_source:
                    result = check(CheckRequest(
                        input     = input_descriptor,
                        reference = reference_descriptor,
                        metrics   = ("width",),
                        effort    = effort,
                    ))

                self.assertEqual(result.status, "success")
                self.assertEqual(metric_calls, ["width"])
                self.assertEqual(
                    create_source.call_args_list,
                    [
                        call(
                            input_descriptor,
                            decoder_threads = decoder_threads,
                        ),
                        call(
                            reference_descriptor,
                            decoder_threads = decoder_threads,
                        ),
                    ],
                )

    def test_core_checker_rejects_an_unknown_effort(self):
        descriptor = MediaDescriptor(
            path       = Path("unused.264"),
            media_type = ENCODED_MEDIA_TYPE,
            extension  = ".264",
        )

        with patch(
            "media_checker.checker.create_video_source"
        ) as create_source:
            with self.assertRaisesRegex(
                ConfigurationError,
                "Unsupported effort 'extreme'",
            ):
                check(CheckRequest(
                    input     = descriptor,
                    reference = None,
                    metrics   = ("width",),
                    effort    = "extreme",
                ))

        create_source.assert_not_called()

    def test_checker_preserves_ordered_partial_results_when_interrupted(self):
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
        metric_names = ("width", "height", "codec")

        for interrupt_index in range(len(metric_names)):
            with self.subTest(interrupt_index = interrupt_index):
                calls = []
                handlers = {
                    name : StubMetric(
                        name,
                        calls,
                        interrupt = index == interrupt_index,
                    )
                    for index, name in enumerate(metric_names)
                }

                with patch.dict(
                    METRIC_HANDLERS,
                    handlers,
                    clear = True,
                ), patch(
                    "media_checker.checker.create_video_source",
                    return_value = object(),
                ):
                    with self.assertRaises(CheckInterrupted) as raised:
                        check(CheckRequest(
                            input     = descriptor,
                            reference = None,
                            metrics   = metric_names,
                        ))

                result = raised.exception.result
                self.assertEqual(list(result.metrics), list(metric_names))
                self.assertEqual(calls, list(metric_names[:interrupt_index + 1]))
                self.assertEqual(
                    [
                        result.metrics[name].status
                        for name in metric_names
                    ],
                    (
                        ["success"] * interrupt_index
                        + [STATUS_NOT_CHECKED] * (len(metric_names) - interrupt_index)
                    ),
                )
                self.assertTrue(all(
                    result.metrics[name].value is None
                    for name in metric_names[interrupt_index:]
                ))
                self.assertEqual(
                    result.status,
                    "failed" if interrupt_index == 0 else "partial",
                )

    def test_checker_preserves_completed_errors_when_interrupted(self):
        descriptor = MediaDescriptor(
            path       = Path("unused.264"),
            media_type = ENCODED_MEDIA_TYPE,
            extension  = ".264",
        )
        calls = []
        handlers = {
            "width" : StubMetric("width", calls, error = True),
            "height" : StubMetric("height", calls, interrupt = True),
            "codec" : StubMetric("codec", calls),
        }

        with patch.dict(
            METRIC_HANDLERS,
            handlers,
            clear = True,
        ), patch(
            "media_checker.checker.create_video_source",
            return_value = object(),
        ):
            with self.assertRaises(CheckInterrupted) as raised:
                check(CheckRequest(
                    input     = descriptor,
                    reference = None,
                    metrics   = ("width", "height", "codec"),
                ))

        result = raised.exception.result
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.metrics["width"].status, "error")
        self.assertEqual(result.metrics["width"].value, "width failed")
        self.assertEqual(result.metrics["height"].status, STATUS_NOT_CHECKED)
        self.assertEqual(result.metrics["codec"].status, STATUS_NOT_CHECKED)

    def test_cli_writes_partial_result_without_printing_unfinished_metrics(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "input.raw").write_bytes(bytes(8))
            descriptor = root / "input.yaml"
            descriptor.write_text(_raw_document(), encoding = "utf-8")
            output = root / "result.yaml"
            partial_result = CheckResult(
                status = "partial",
                metrics = {
                    "width" : MetricResult.success(4),
                    "height" : MetricResult.not_checked(),
                },
            )
            console = io.StringIO()

            with patch(
                "media_checker.cli.check",
                side_effect = CheckInterrupted(partial_result),
            ), redirect_stdout(console), redirect_stderr(io.StringIO()):
                exit_status = run([
                    "--input", str(descriptor),
                    "--check", "width", "height",
                    "--output", str(output),
                ])

            serialized = yaml.safe_load(output.read_text(encoding = "utf-8"))
            self.assertEqual(exit_status, EXIT_INTERRUPTED)
            self.assertEqual(console.getvalue(), "width: success\n")
            self.assertEqual(
                serialized["metrics"]["height"],
                {
                    "status" : STATUS_NOT_CHECKED,
                    "value"  : None,
                },
            )

    def test_sigterm_uses_the_conventional_interrupted_exit_status(self):
        interrupts = _InterruptSignals()

        with self.assertRaises(KeyboardInterrupt):
            interrupts._handle(signal.SIGTERM, None)

        self.assertEqual(interrupts.exit_status, 128 + signal.SIGTERM)

    def test_interrupt_signal_handlers_are_restored(self):
        previous = {
            signal_number : signal.getsignal(signal_number)
            for signal_number in (signal.SIGINT, signal.SIGTERM)
        }

        with _InterruptSignals():
            pass

        self.assertEqual(
            {
                signal_number : signal.getsignal(signal_number)
                for signal_number in (signal.SIGINT, signal.SIGTERM)
            },
            previous,
        )

    def test_cli_accepts_a_direct_encoded_input_path(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            media_path = root / "input.H264"
            media_path.write_bytes(b"encoded")
            output = root / "result.yaml"
            checked_result = CheckResult(
                status = "success",
                metrics = {
                    "width" : MetricResult.success(16),
                },
            )

            with patch(
                "media_checker.cli.check",
                return_value = checked_result,
            ) as check_mock, redirect_stdout(io.StringIO()):
                exit_status = run([
                    "--input", str(media_path),
                    "--check", "width",
                    "--effort", "light",
                    "--output", str(output),
                ])

            request = check_mock.call_args.args[0]
            self.assertEqual(exit_status, EXIT_SUCCESS)
            self.assertEqual(request.input.path, media_path.resolve())
            self.assertEqual(request.input.extension, ".h264")
            self.assertIsNone(request.reference)
            self.assertEqual(request.effort, "light")

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

    def test_omitted_check_selects_every_metric_in_registry_order(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "input.raw").write_bytes(bytes(8))
            (root / "reference.raw").write_bytes(bytes(8))
            descriptor = root / "input.yaml"
            descriptor.write_text(_raw_pair_document(), encoding = "utf-8")
            output = root / "result.yaml"

            with redirect_stdout(io.StringIO()):
                exit_status = run([
                    "--input", str(descriptor),
                    "--output", str(output),
                ])

            result = yaml.safe_load(output.read_text(encoding = "utf-8"))

            self.assertEqual(exit_status, EXIT_METRIC_FAILED)
            self.assertEqual(list(result["metrics"]), list(METRIC_HANDLERS))

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
    def test_effort_defaults_to_medium_and_accepts_three_levels(self):
        parser = build_parser()
        default = parser.parse_args(["--input", "unused.264"])

        self.assertEqual(default.effort, "medium")

        for option in ("-e", "--effort"):
            for effort in ("light", "medium", "high"):
                with self.subTest(option = option, effort = effort):
                    args = parser.parse_args([
                        "--input", "unused.264",
                        option, effort,
                    ])
                    self.assertEqual(args.effort, effort)

        with redirect_stderr(io.StringIO()):
            with self.assertRaisesRegex(SystemExit, "2"):
                parser.parse_args([
                    "--input", "unused.264",
                    "--effort", "extreme",
                ])

    def test_metric_help_is_complete_aligned_and_wrapped(self):
        expected_descriptions = {
            "width"               : "Encoded video width in pixels",
            "height"              : "Encoded video height in pixels",
            "framerate"           : (
                "Estimated displayed frame rate as numerator/denominator"
            ),
            "level"               : (
                "H.264 or H.265 level signaled by the codec SPS"
            ),
            "profile"             : (
                "Codec profile reported by the selected video stream"
            ),
            "codec"               : (
                "Selected video codec normalized to h264 or h265"
            ),
            "bitrate"             : (
                "Selected-video bitrate in bits per second, excluding audio "
                "and container overhead"
            ),
            "gop"                 : (
                "Largest observed I-picture group size in frames"
            ),
            "interval-intraframe" : (
                "Largest presentation-order frame distance between adjacent "
                "I pictures"
            ),
            "pframes"             : (
                "P-picture count strictly inside the earliest longest "
                "I-picture interval"
            ),
            "bframes"             : (
                "B-picture count strictly inside the earliest longest "
                "I-picture interval"
            ),
            "refframes"           : (
                "Maximum SPS-signaled reference-picture capacity"
            ),
            "frame_count"         : (
                "Number of completely decoded selected-video frames"
            ),
            "scan_type"           : (
                "Scan type: progressive, interlace_tff, or interlace_bff"
            ),
            "crop"                : (
                "SPS crop window in visible luma pixels as "
                "left:right:top:bottom"
            ),
            "psnr"                : (
                "Minimum per-frame PSNR between input and reference video"
            ),
        }

        self.assertEqual(_METRIC_DESCRIPTIONS, expected_descriptions)
        self.assertEqual(
            tuple(_METRIC_DESCRIPTIONS),
            tuple(METRIC_HANDLERS),
        )

        metric_lines = _format_metrics_help().splitlines()
        name_width = max(len(name) for name in METRIC_HANDLERS)
        description_start = len("  - ") + name_width + len(" : ")
        rendered_descriptions = {}
        rendered_name = None

        for line in metric_lines:
            self.assertEqual(line, line.rstrip())

            if line.startswith("  - "):
                self.assertEqual(line.index(":"), description_start - 2)
                rendered_name = line[len("  - "):description_start - 3].rstrip()
                rendered_descriptions[rendered_name] = []
            else:
                self.assertEqual(
                    line[:description_start],
                    " " * description_start,
                )

            description = line[description_start:]
            self.assertLessEqual(len(description), 60)
            self.assertFalse(description.endswith("-"))
            rendered_descriptions[rendered_name].append(description)

        self.assertEqual(
            tuple(rendered_descriptions),
            tuple(METRIC_HANDLERS),
        )
        self.assertEqual(
            {
                name : " ".join(lines)
                for name, lines in rendered_descriptions.items()
            },
            expected_descriptions,
        )

    def test_help_uses_expected_capitalization_layout_and_metrics(self):
        output = io.StringIO()

        build_parser().print_help(file = output)
        help_text = output.getvalue()

        self.assertIn("Usage: media-check", help_text)
        self.assertIn(
            "Inspect video metadata and stream structure; calculate PSNR",
            help_text,
        )
        self.assertNotIn(
            "Check metadata, stream structure, and PSNR for video",
            help_text,
        )
        self.assertIn("Options:", help_text)
        self.assertIn("-h, --help", help_text)
        self.assertIn("Show this help message and exit", help_text)
        self.assertIn("-i, --input INPUT", help_text)
        self.assertIn("-c, --check [METRIC ...]", help_text)
        self.assertIn("Omit the option or names to calculate all", help_text)
        self.assertIn("[-c [METRIC ...]]", help_text)
        self.assertIn("-e, --effort {light,medium,high}", help_text)
        self.assertIn(
            "Decoder effort: light=1 thread, medium=4 threads,",
            help_text,
        )
        self.assertIn(
            "high=FFmpeg automatic (default: medium)",
            help_text,
        )
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
