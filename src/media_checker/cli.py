import argparse
import sys
from pathlib import Path
from typing import List, Optional

from media_checker.checker import check, normalize_metrics
from media_checker.descriptors import load_descriptor
from media_checker.errors import ConfigurationError
from media_checker.models import (
    STATUS_SUCCESS,
    CheckRequest,
    CheckResult,
)
from media_checker.yaml_io import write_result


DEFAULT_OUTPUT = "metrics-result.yaml"

EXIT_SUCCESS       = 0
EXIT_METRIC_FAILED = 1
EXIT_CONFIGURATION = 2


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line interface without reading process arguments."""

    parser = argparse.ArgumentParser(
        description = "Check metadata and PSNR for raw or encoded video"
    )

    parser.add_argument(
        "-i",
        "--input",
        required = True,
        help     = "YAML descriptor for the media being checked",
    )
    parser.add_argument(
        "-r",
        "--reference",
        help = "Optional YAML descriptor for PSNR reference media",
    )
    parser.add_argument(
        "-c",
        "--check",
        required = True,
        nargs    = "+",
        metavar  = "METRIC",
        help     = "Metrics to calculate",
    )
    parser.add_argument(
        "-o",
        "--output",
        default = DEFAULT_OUTPUT,
        help    = "Result YAML path (default: {})".format(DEFAULT_OUTPUT),
    )

    return parser


def run(arguments: Optional[List[str]] = None) -> int:
    """Execute one CLI request and return its process status."""

    parser = build_parser()
    args   = parser.parse_args(arguments)
    output = Path(args.output)

    try:
        metrics = normalize_metrics(args.check)
        input_descriptor = load_descriptor(Path(args.input))
        reference_descriptor = None

        if args.reference:
            reference_descriptor = load_descriptor(Path(args.reference))

        result = check(CheckRequest(
            input     = input_descriptor,
            reference = reference_descriptor,
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
