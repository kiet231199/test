from typing import Dict, Iterable, Tuple

from media_checker.errors import CheckerError, ConfigurationError
from media_checker.media import create_video_source
from media_checker.metrics import METRIC_HANDLERS, MetricContext
from media_checker.models import (
    STATUS_FAILED,
    STATUS_PARTIAL,
    STATUS_SUCCESS,
    CheckRequest,
    CheckResult,
    MetricResult,
)


UNSUPPORTED_METRICS_MESSAGE = "Unsupported metrics"


class CheckInterrupted(KeyboardInterrupt):
    """A cancelled check with completed and unfinished metric results."""

    def __init__(self, result: CheckResult):
        super().__init__()
        self.result = result


def normalize_metrics(metric_names: Iterable[str]) -> Tuple[str, ...]:
    """Validate metric names and remove duplicates without reordering them."""

    metrics = []

    for name in metric_names:
        if name not in METRIC_HANDLERS:
            raise ConfigurationError("Unsupported metric '{}'".format(name))

        if name not in metrics:
            metrics.append(name)

    if not metrics:
        raise ConfigurationError("At least one metric must be requested")

    return tuple(metrics)


def check(request: CheckRequest) -> CheckResult:
    """Calculate all requested metrics and retain independent failures."""

    metric_names = normalize_metrics(request.metrics)
    results = {
        metric_name : MetricResult.not_checked()
        for metric_name in metric_names
    }

    try:
        input_source = create_video_source(request.input)
        reference_source = None

        if request.reference is not None:
            reference_source = create_video_source(request.reference)

        context = MetricContext(
            input_source      = input_source,
            reference_source  = reference_source,
            input_frame_limit = (
                request.input.frame_count
                if request.input.is_raw
                else None
            ),
            requested_metrics = metric_names,
        )

        for metric_name in metric_names:
            handler = METRIC_HANDLERS[metric_name]

            if not handler.supports(context):
                results[metric_name] = MetricResult.failure(
                    UNSUPPORTED_METRICS_MESSAGE
                )
                continue

            try:
                value = handler.calculate(context)
                results[metric_name] = MetricResult.success(value)
            except CheckerError as error:
                results[metric_name] = MetricResult.failure(str(error))
    except KeyboardInterrupt as error:
        raise CheckInterrupted(_check_result(results)) from error

    return _check_result(results)


def _check_result(results: Dict[str, MetricResult]) -> CheckResult:
    success_count = sum(
        result.status == STATUS_SUCCESS
        for result in results.values()
    )

    if success_count == len(results):
        status = STATUS_SUCCESS
    elif success_count == 0:
        status = STATUS_FAILED
    else:
        status = STATUS_PARTIAL

    return CheckResult(status = status, metrics = results)
