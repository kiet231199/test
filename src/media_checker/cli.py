import argparse
import signal
import sys
import threading
from pathlib import Path
from textwrap import wrap
from typing import Any, Dict, List, NoReturn, Optional

from media_checker.checker import CheckInterrupted, check, normalize_metrics
from media_checker.descriptors import load_input
from media_checker.errors import ConfigurationError
from media_checker.metrics import METRIC_HANDLERS
from media_checker.models import (
    DEFAULT_EFFORT,
    EFFORT_LEVELS,
    STATUS_ERROR,
    STATUS_NOT_CHECKED,
    STATUS_SUCCESS,
    CheckRequest,
    CheckResult,
)
from media_checker.result_io import output_format, write_result


DEFAULT_OUTPUT = "result.yaml"

EXIT_SUCCESS       = 0
EXIT_METRIC_FAILED = 1
EXIT_CONFIGURATION = 2
SIGNAL_EXIT_STATUS_BASE = 128
EXIT_INTERRUPTED = SIGNAL_EXIT_STATUS_BASE + int(signal.SIGINT)

ANSI_GREEN  = "\033[32m"
ANSI_RED    = "\033[31m"
ANSI_YELLOW = "\033[33m"
ANSI_RESET  = "\033[0m"
HELP_MAX_POSITION = 16
_METRIC_DESCRIPTION_WIDTH = 60

_METRIC_DESCRIPTIONS = {
    "width"               : "Encoded video width in pixels",
    "height"              : "Encoded video height in pixels",
    "framerate"           : (
        "Estimated displayed frame rate as numerator/denominator"
    ),
    "level"               : "H.264 or H.265 level signaled by the codec SPS",
    "profile"             : "Codec profile reported by the selected video stream",
    "codec"               : "Selected video codec normalized to h264 or h265",
    "bitrate"             : (
        "Selected-video bitrate in bits per second, excluding audio and "
        "container overhead"
    ),
    "gop"                 : "Largest observed I-picture group size in frames",
    "interval-intraframe" : (
        "Largest presentation-order frame distance between adjacent I pictures"
    ),
    "pframes"             : (
        "P-picture count strictly inside the earliest longest I-picture interval"
    ),
    "bframes"             : (
        "B-picture count strictly inside the earliest longest I-picture interval"
    ),
    "refframes"           : "Maximum SPS-signaled reference-picture capacity",
    "frame_count"         : (
        "Number of completely decoded selected-video frames"
    ),
    "scan_type"           : (
        "Scan type: progressive, interlace_tff, or interlace_bff"
    ),
    "crop"                : (
        "SPS crop window in visible luma pixels as left:right:top:bottom"
    ),
    "psnr"                : (
        "Minimum per-frame PSNR between input and reference video"
    ),
}


class _InterruptSignals:
    """Convert catchable process termination into Python cancellation."""

    def __init__(self):
        self.signal_number = None  # type: Optional[int]
        self._previous = {}  # type: Dict[int, Any]

    @property
    def exit_status(self) -> int:
        signal_number = self.signal_number or int(signal.SIGINT)
        return SIGNAL_EXIT_STATUS_BASE + signal_number

    def __enter__(self) -> "_InterruptSignals":
        if threading.current_thread() is not threading.main_thread():
            return self

        for signal_number in (signal.SIGINT, signal.SIGTERM):
            number = int(signal_number)

            try:
                previous = signal.getsignal(signal_number)
                signal.signal(signal_number, self._handle)
            except (OSError, ValueError):
                continue

            self._previous[number] = previous

        return self

    def __exit__(self, exception_type, exception, traceback) -> None:
        for signal_number, previous in self._previous.items():
            signal.signal(signal_number, previous)

        self._previous.clear()

    def _handle(self, signal_number, frame) -> NoReturn:
        self.signal_number = int(signal_number)
        raise KeyboardInterrupt


class HelpFormatter(argparse.RawTextHelpFormatter):
    """Render option aliases before one shared metavar."""

    def __init__(self, prog: str):
        super().__init__(prog, max_help_position = HELP_MAX_POSITION)

    def _format_action_invocation(self, action: argparse.Action) -> str:
        if not action.option_strings:
            return super()._format_action_invocation(action)

        invocation = ", ".join(action.option_strings)

        if action.nargs == 0:
            return invocation

        default = self._get_default_metavar_for_optional(action)
        arguments = self._format_args(action, default)
        return "{} {}".format(invocation, arguments)

    def _format_usage(self, *args, **kwargs) -> str:
        usage = super()._format_usage(*args, **kwargs)

        if usage.startswith("usage:"):
            return "Usage:" + usage[len("usage:"):]

        return usage


class ArgumentParser(argparse.ArgumentParser):
    """Capitalized help and complete guidance for argument errors."""

    def print_help(self, file = None) -> None:
        if file is None:
            file = sys.stdout

        help_text = self.format_help()

        if _supports_color(file):
            help_text = _color_usage_command(help_text)

        self._print_message(help_text, file)

    def error(self, message: str) -> NoReturn:
        self.print_help(sys.stderr)
        capitalized_message = message[:1].upper() + message[1:]
        self.exit(
            EXIT_CONFIGURATION,
            "\nError: {}\n".format(capitalized_message),
        )


def _supports_color(stream) -> bool:
    isatty = getattr(stream, "isatty", None)
    return bool(isatty and isatty())


def _color_usage_command(help_text: str) -> str:
    usage_prefix = "Usage: "
    command_start = help_text.find(usage_prefix)

    if command_start < 0:
        return help_text

    command_start += len(usage_prefix)
    command_end = help_text.find("\n\n", command_start)

    if command_end < 0:
        command_end = len(help_text)

    return "{}{}{}{}{}".format(
        help_text[:command_start],
        ANSI_YELLOW,
        help_text[command_start:command_end],
        ANSI_RESET,
        help_text[command_end:],
    )


def _status_text(status: str, stream) -> str:
    if not _supports_color(stream):
        return status

    color = {
        STATUS_SUCCESS : ANSI_GREEN,
        STATUS_ERROR   : ANSI_RED,
    }.get(status)

    if color is None:
        return status

    return "{}{}{}".format(color, status, ANSI_RESET)


def _print_short_result(result: CheckResult, file = None) -> None:
    if file is None:
        file = sys.stdout

    for name, metric in result.metrics.items():
        if metric.status == STATUS_NOT_CHECKED:
            continue

        print(
            "{}: {}".format(name, _status_text(metric.status, file)),
            file = file,
        )

        if metric.status == STATUS_ERROR:
            lines = str(metric.value or "").splitlines() or [""]

            for line in lines:
                print("  {}".format(line), file = file)


def _format_metrics_help() -> str:
    """Render registered metrics with aligned, word-wrapped descriptions."""

    name_width = max(len(name) for name in METRIC_HANDLERS)
    lines = []  # type: List[str]

    for name in METRIC_HANDLERS:
        prefix = "  - {} : ".format(name.ljust(name_width))
        description_lines = wrap(
            _METRIC_DESCRIPTIONS[name],
            width                = _METRIC_DESCRIPTION_WIDTH,
            break_long_words     = False,
            break_on_hyphens     = False,
        )
        lines.append("{}{}".format(prefix, description_lines[0]))
        lines.extend(
            "{}{}".format(" " * len(prefix), description)
            for description in description_lines[1:]
        )

    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line interface without reading process arguments."""

    parser = ArgumentParser(
        prog            = "media-check",
        description     = (
            "Inspect video metadata and stream structure; calculate PSNR"
        ),
        add_help        = False,
        formatter_class = HelpFormatter,
    )
    parser._optionals.title = "Options"

    parser.add_argument(
        "-h",
        "--help",
        action = "help",
        help   = "Show this help message and exit",
    )

    parser.add_argument(
        "-i",
        "--input",
        required = True,
        help     = "Descriptor or encoded media file to check",
    )
    metrics_help = (
        "Metrics to calculate. Omit the option or names to calculate all.\n"
        "Supported metrics:\n{}"
    ).format(_format_metrics_help())
    parser.add_argument(
        "-c",
        "--check",
        nargs    = "*",
        metavar  = "METRIC",
        help     = metrics_help,
    )
    parser.add_argument(
        "-e",
        "--effort",
        choices = EFFORT_LEVELS,
        default = DEFAULT_EFFORT,
        help    = (
            "Decoder effort: light=1 thread, medium=4 threads, "
            "high=FFmpeg automatic (default: {})"
        ).format(DEFAULT_EFFORT),
    )
    parser.add_argument(
        "-o",
        "--output",
        default = DEFAULT_OUTPUT,
        help    = (
            "Result .txt, .yaml, or .json path (default: {})"
        ).format(DEFAULT_OUTPUT),
    )

    return parser


def run(arguments: Optional[List[str]] = None) -> int:
    """Execute one CLI request and return its process status."""

    interrupts = _InterruptSignals()

    try:
        with interrupts:
            parser = build_parser()
            args   = parser.parse_args(arguments)
            output = Path(args.output)

            try:
                output_format(output)
            except ConfigurationError as error:
                print(str(error), file = sys.stderr)
                return EXIT_CONFIGURATION

            return _run_request(args, output, interrupts)
    except KeyboardInterrupt:
        return interrupts.exit_status


def _run_request(
    args: argparse.Namespace,
    output: Path,
    interrupts: _InterruptSignals,
) -> int:
    interrupted = False

    try:
        requested_metrics = args.check or tuple(METRIC_HANDLERS)
        metrics = normalize_metrics(requested_metrics)
        descriptors = load_input(Path(args.input))

        try:
            result = check(CheckRequest(
                input     = descriptors.input,
                reference = descriptors.reference,
                metrics   = metrics,
                effort    = args.effort,
            ))
        except CheckInterrupted as interruption:
            result = interruption.result
            interrupted = True

        write_result(result, output)
    except ConfigurationError as error:
        result = CheckResult.configuration_failure(str(error))

        try:
            write_result(result, output)
        except OSError as write_error:
            print(
                "Cannot write result '{}': {}".format(output, write_error),
                file = sys.stderr,
            )

        print(str(error), file = sys.stderr)
        return EXIT_CONFIGURATION
    except OSError as error:
        print("Cannot write result '{}': {}".format(output, error), file = sys.stderr)
        return EXIT_CONFIGURATION

    try:
        _print_short_result(result)
    except OSError as error:
        print("Cannot display result: {}".format(error), file = sys.stderr)
        return EXIT_CONFIGURATION

    if interrupted:
        return interrupts.exit_status

    if result.status == STATUS_SUCCESS:
        return EXIT_SUCCESS

    return EXIT_METRIC_FAILED


def main() -> None:
    """Run the installed console command."""

    try:
        sys.exit(run())
    except KeyboardInterrupt:
        sys.exit(EXIT_INTERRUPTED)
