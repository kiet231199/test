import math
from abc import ABC, abstractmethod
from itertools import islice, zip_longest
from typing import Dict, Optional, Tuple, cast

import av
import numpy as np

from media_checker.errors import MediaError, MetricError
from media_checker.media import RAW_FORMATS, VideoSource, _EncodedAnalysisSource


INFINITE_PSNR_VALUE = 1000.0
PSNR_DECIMAL_PLACES = 6

H264_LEVEL_DIVISOR = 10
H265_LEVELS = {
    30  : "1.0",
    60  : "2.0",
    63  : "2.1",
    90  : "3.0",
    93  : "3.1",
    120 : "4.0",
    123 : "4.1",
    150 : "5.0",
    153 : "5.1",
    156 : "5.2",
    180 : "6.0",
    183 : "6.1",
    186 : "6.2",
}

RAW_COMPARISON_FORMATS = {
    raw_format.av_format : raw_format.comparison_format
    for raw_format in RAW_FORMATS.values()
}


class MetricContext:
    def __init__(
        self,
        input_source: VideoSource,
        reference_source: Optional[VideoSource],
        input_frame_limit: Optional[int],
        requested_metrics: Tuple[str, ...] = (),
    ):
        self.input_source      = input_source
        self.reference_source  = reference_source
        self.input_frame_limit = input_frame_limit
        self.requested_metrics = requested_metrics

    def input_metadata(self):
        if (
            not self.input_source.is_raw
            and any(
                name in _ENCODED_ANALYSIS_METRICS
                for name in self.requested_metrics
            )
        ):
            source = cast(_EncodedAnalysisSource, self.input_source)
            inspect = getattr(source, "inspect", None)

            if inspect is not None:
                inspect(self.requested_metrics)

        return self.input_source.metadata()

    def input_analysis(self):
        source = cast(_EncodedAnalysisSource, self.input_source)
        inspect = getattr(source, "inspect", None)

        if inspect is not None:
            return inspect(self.requested_metrics)

        return source.analysis()


class Metric(ABC):
    """Calculate one result from a subject and optional reference."""

    supports_raw_input = False

    def supports(self, context: MetricContext) -> bool:
        return self.supports_raw_input or not context.input_source.is_raw

    @abstractmethod
    def calculate(self, context: MetricContext):
        raise NotImplementedError


class MetadataMetric(Metric):
    field_name = ""

    def calculate(self, context: MetricContext):
        metadata = context.input_metadata()
        value    = getattr(metadata, self.field_name)

        if value is None:
            raise MetricError(
                "Metric '{}' is unavailable for the input media".format(
                    self.field_name
                )
            )

        return value


class WidthMetric(MetadataMetric):
    field_name = "width"


class HeightMetric(MetadataMetric):
    field_name = "height"


class FramerateMetric(MetadataMetric):
    field_name = "framerate"

    def calculate(self, context: MetricContext) -> str:
        value = super().calculate(context)
        return "{}/{}".format(value.numerator, value.denominator)


class ProfileMetric(MetadataMetric):
    field_name = "profile"

    def calculate(self, context: MetricContext) -> str:
        return str(super().calculate(context))


class CodecMetric(MetadataMetric):
    field_name = "codec_name"

    def calculate(self, context: MetricContext) -> str:
        value = str(super().calculate(context)).lower()

        if value == "h264":
            return "h264"

        if value in ("hevc", "h265"):
            return "h265"

        raise MetricError("Metric 'codec' is unavailable for the input media")


class EncodedAnalysisMetric(Metric):
    field_name  = ""
    metric_name = ""

    def calculate(self, context: MetricContext):
        analysis = context.input_analysis()
        value    = getattr(analysis, self.field_name)

        if value is None:
            raise MetricError(
                "Metric '{}' is unavailable for the input media".format(
                    self.metric_name
                )
            )

        return value


class BitrateMetric(EncodedAnalysisMetric):
    field_name  = "bitrate"
    metric_name = "bitrate"


class GopMetric(EncodedAnalysisMetric):
    field_name  = "gop"
    metric_name = "gop"


class IntraFrameIntervalMetric(EncodedAnalysisMetric):
    field_name  = "interval_intraframe"
    metric_name = "interval-intraframe"


class PFramesMetric(EncodedAnalysisMetric):
    field_name  = "pframes"
    metric_name = "pframes"


class BFramesMetric(EncodedAnalysisMetric):
    field_name  = "bframes"
    metric_name = "bframes"


class ReferenceFramesMetric(EncodedAnalysisMetric):
    field_name  = "refframes"
    metric_name = "refframes"


class FrameCountMetric(EncodedAnalysisMetric):
    field_name  = "frame_count"
    metric_name = "frame_count"


class ScanTypeMetric(EncodedAnalysisMetric):
    field_name  = "scan_type"
    metric_name = "scan_type"


class CropMetric(EncodedAnalysisMetric):
    field_name  = "crop"
    metric_name = "crop"


class LevelMetric(Metric):
    def calculate(self, context: MetricContext) -> str:
        metadata = context.input_metadata()
        level    = metadata.level

        if level is None:
            level = context.input_analysis().level

        if level is None:
            raise MetricError("Metric 'level' is unavailable for the input media")

        return _level_text(metadata.codec_name, level)


class PsnrMetric(Metric):
    supports_raw_input = True

    def calculate(self, context: MetricContext) -> float:
        reference = context.reference_source

        if reference is None:
            raise MetricError("PSNR requires a reference descriptor")

        input_metadata     = context.input_source.metadata()
        reference_metadata = reference.metadata()

        if (
            input_metadata.width != reference_metadata.width
            or input_metadata.height != reference_metadata.height
        ):
            raise MetricError(
                "PSNR requires input and reference resolutions to match"
            )

        if not context.input_source.is_raw and not reference.is_raw:
            if (
                input_metadata.framerate is None
                or reference_metadata.framerate is None
            ):
                raise MetricError(
                    "PSNR requires encoded input and reference framerates"
                )

            if input_metadata.framerate != reference_metadata.framerate:
                raise MetricError(
                    "PSNR requires encoded input and reference framerates to match"
                )

        return self._calculate_frames(
            context.input_source,
            reference,
            context.input_frame_limit,
        )

    def _calculate_frames(
        self,
        input_source: VideoSource,
        reference_source: VideoSource,
        frame_limit: Optional[int],
    ) -> float:
        missing = object()
        minimum = math.inf
        target_format = None
        compared_frames = 0
        frame_counts_match = True
        input_frames = input_source.frames()
        reference_frames = reference_source.frames()

        try:
            pairs = zip_longest(
                input_frames,
                reference_frames,
                fillvalue = missing,
            )

            if frame_limit is not None:
                pairs = islice(pairs, frame_limit)

            for input_frame, reference_frame in pairs:
                if input_frame is missing or reference_frame is missing:
                    frame_counts_match = False
                    break

                input_video_frame     = cast(av.VideoFrame, input_frame)
                reference_video_frame = cast(av.VideoFrame, reference_frame)

                if target_format is None:
                    target_format = _comparison_format(
                        reference_source,
                        reference_video_frame,
                    )

                frame_psnr = _frame_psnr(
                    input_video_frame,
                    reference_video_frame,
                    target_format,
                )
                minimum   = min(minimum, frame_psnr)
                compared_frames += 1
        except MetricError:
            raise
        except (ValueError, av.FFmpegError) as error:
            raise MetricError("PSNR frame conversion failed: {}".format(error)) from error
        except MediaError as error:
            raise MetricError(str(error)) from error
        finally:
            _close_frame_iterators(input_frames, reference_frames)

        if (
            not frame_counts_match
            or (
                frame_limit is not None
                and compared_frames < frame_limit
            )
        ):
            raise MetricError(
                "PSNR requires input and reference frame counts to match"
            )

        if compared_frames == 0:
            raise MetricError("PSNR requires at least one decoded frame")

        if math.isinf(minimum):
            return INFINITE_PSNR_VALUE

        return round(minimum, PSNR_DECIMAL_PLACES)


def _close_frame_iterators(*iterators) -> None:
    for iterator in iterators:
        close = getattr(iterator, "close", None)

        if close is None:
            continue

        try:
            close()
        except Exception:
            pass


def _comparison_format(source: VideoSource, frame: av.VideoFrame) -> str:
    if source.is_raw:
        raw_format = RAW_FORMATS[source.descriptor.format or ""]
        return raw_format.comparison_format

    format_name = frame.format.name
    return RAW_COMPARISON_FORMATS.get(format_name, format_name)


def _frame_psnr(
    input_frame: av.VideoFrame,
    reference_frame: av.VideoFrame,
    target_format: str,
) -> float:
    if (
        input_frame.width != reference_frame.width
        or input_frame.height != reference_frame.height
    ):
        raise MetricError("PSNR frame resolutions do not match")

    input_array = input_frame.to_ndarray(format = target_format)
    reference_array = reference_frame.to_ndarray(format = target_format)

    if input_array.shape != reference_array.shape:
        raise MetricError("PSNR converted frame shapes do not match")

    difference = (
        input_array.astype(np.float64)
        - reference_array.astype(np.float64)
    )
    mean_squared_error = float(np.mean(np.square(difference)))

    if mean_squared_error == 0:
        return math.inf

    peak = _sample_peak(target_format, input_array.dtype.itemsize)
    return 10.0 * math.log10((peak * peak) / mean_squared_error)


def _sample_peak(format_name: str, item_size: int) -> int:
    try:
        video_format = av.VideoFormat(format_name)
        components = getattr(video_format, "components", ())
        bit_depth = max(component.bits for component in components)
    except (AttributeError, ValueError):
        bit_depth = item_size * 8

    return (1 << bit_depth) - 1


def _level_text(codec_name: Optional[str], level: int) -> str:
    codec_name = (codec_name or "").lower()

    if codec_name == "h264":
        if level == 9:
            return "1b"

        return "{:.1f}".format(level / H264_LEVEL_DIVISOR)

    if codec_name in ("hevc", "h265"):
        return H265_LEVELS.get(level, str(level))

    return str(level)


METRIC_HANDLERS: Dict[str, Metric] = {
    "width"               : WidthMetric(),
    "height"              : HeightMetric(),
    "framerate"           : FramerateMetric(),
    "level"               : LevelMetric(),
    "profile"             : ProfileMetric(),
    "codec"               : CodecMetric(),
    "bitrate"             : BitrateMetric(),
    "gop"                 : GopMetric(),
    "interval-intraframe" : IntraFrameIntervalMetric(),
    "pframes"             : PFramesMetric(),
    "bframes"             : BFramesMetric(),
    "refframes"           : ReferenceFramesMetric(),
    "frame_count"         : FrameCountMetric(),
    "scan_type"           : ScanTypeMetric(),
    "crop"                : CropMetric(),
    "psnr"                : PsnrMetric(),
}

_ENCODED_ANALYSIS_METRICS = frozenset((
    "level",
    "bitrate",
    "gop",
    "interval-intraframe",
    "pframes",
    "bframes",
    "refframes",
    "frame_count",
    "scan_type",
    "crop",
))
