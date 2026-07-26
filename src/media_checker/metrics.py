from abc import ABC, abstractmethod
from typing import Dict, Optional, Tuple, cast

from media_checker.errors import MetricError
from media_checker.media import VideoSource, _EncodedAnalysisSource
from media_checker.psnr import PsnrSession, calculate_psnr

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
        self._encoded_prepared = False
        self._psnr_session = None  # type: Optional[PsnrSession]

    def input_metadata(self):
        if (
            not self.input_source.is_raw
            and any(
                name in _ENCODED_PREPARE_METRICS
                for name in self.requested_metrics
            )
        ):
            self._prepare_encoded_request()

        return self.input_source.metadata()

    def input_analysis(self):
        self._prepare_encoded_request()
        source = cast(_EncodedAnalysisSource, self.input_source)
        inspect = getattr(source, "inspect", None)

        if inspect is not None:
            return inspect(self.requested_metrics)

        return source.analysis()

    def input_psnr(self) -> float:
        reference = self.reference_source

        if reference is None:
            raise MetricError("PSNR requires a reference descriptor")

        if self.input_source.is_raw:
            return calculate_psnr(
                self.input_source,
                reference,
                self.input_frame_limit,
            )

        self._prepare_encoded_request()

        if self._psnr_session is not None:
            return self._psnr_session.result()

        return calculate_psnr(
            self.input_source,
            reference,
            self.input_frame_limit,
        )

    def _prepare_encoded_request(self) -> None:
        if self._encoded_prepared or self.input_source.is_raw:
            return

        source = cast(_EncodedAnalysisSource, self.input_source)
        inspect = getattr(source, "inspect", None)

        if inspect is None:
            return

        observer = None

        if (
            "psnr" in self.requested_metrics
            and self.reference_source is not None
        ):
            observer = PsnrSession(
                self.reference_source,
                self.input_frame_limit,
            )
            self._psnr_session = observer

        self._encoded_prepared = True
        inspect(
            self.requested_metrics,
            frame_observer = observer,
        )


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
        return context.input_psnr()

    def _calculate_frames(
        self,
        input_source: VideoSource,
        reference_source: VideoSource,
        frame_limit: Optional[int],
    ) -> float:
        return calculate_psnr(
            input_source,
            reference_source,
            frame_limit,
        )


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

_ENCODED_PREPARE_METRICS = _ENCODED_ANALYSIS_METRICS | {"psnr"}
