import argparse
import sys
from pathlib import Path
from typing import List, NoReturn, Optional

from media_checker.checker import check, normalize_metrics
from media_checker.descriptors import load_descriptor
from media_checker.errors import ConfigurationError
from media_checker.metrics import METRIC_HANDLERS
from media_checker.models import (
    STATUS_SUCCESS,
    CheckRequest,
    CheckResult,
)
from media_checker.result_io import output_format, write_result


DEFAULT_OUTPUT = "result.yaml"

EXIT_SUCCESS       = 0
EXIT_METRIC_FAILED = 1
EXIT_CONFIGURATION = 2

ANSI_YELLOW = "\033[33m"
ANSI_RESET  = "\033[0m"
HELP_MAX_POSITION = 16


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


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line interface without reading process arguments."""

    parser = ArgumentParser(
        prog            = "media-check",
        description     = "Check metadata, stream structure, and PSNR for video",
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
        help     = "YAML descriptor for the media being checked",
    )
    metrics_help = (
        "Metrics to calculate; omit names to calculate all. Supported metrics:\n{}"
    ).format("\n".join(
        "  - {}".format(name)
        for name in METRIC_HANDLERS
    ))
    parser.add_argument(
        "-c",
        "--check",
        required = True,
        nargs    = "*",
        metavar  = "METRIC",
        help     = metrics_help,
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

    parser = build_parser()
    args   = parser.parse_args(arguments)
    output = Path(args.output)

    try:
        output_format(output)
    except ConfigurationError as error:
        print(str(error), file = sys.stderr)
        return EXIT_CONFIGURATION

    try:
        requested_metrics = args.check or tuple(METRIC_HANDLERS)
        metrics = normalize_metrics(requested_metrics)
        descriptors = load_descriptor(Path(args.input))

        result = check(CheckRequest(
            input     = descriptors.input,
            reference = descriptors.reference,
            metrics   = metrics,
        ))
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

    if result.status == STATUS_SUCCESS:
        return EXIT_SUCCESS

    return EXIT_METRIC_FAILED


def main() -> None:
    """Run the installed console command."""

    sys.exit(run())
