import os
import re
import threading
from abc import ABC, abstractmethod
from contextlib import nullcontext
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import (
    Callable,
    Dict,
    FrozenSet,
    Iterable,
    Iterator,
    List,
    Optional,
    Pattern,
    Protocol,
    Tuple,
)

import av

from media_checker.errors import ConfigurationError, MediaError
from media_checker.models import MediaDescriptor, VideoMetadata


RAW_VIDEO_EXTENSIONS        = (".raw", ".yuv")
ENCODED_INPUT_FORMATS: Dict[str, Optional[str]] = {
    ".264"  : "h264",
    ".26l"  : "h264",
    ".h264" : "h264",
    ".265"  : "hevc",
    ".h265" : "hevc",
    ".mp4"  : None,
}
ENCODED_VIDEO_EXTENSIONS = tuple(ENCODED_INPUT_FORMATS)
SUPPORTED_EXTENSIONS = RAW_VIDEO_EXTENSIONS + ENCODED_VIDEO_EXTENSIONS

SUPPORTED_ENCODED_CODECS = frozenset(("h264", "hevc"))
H264_MACROBLOCK_SIZE = 16

TRACE_FIELD_PATTERN = re.compile(
    r"^\s*\d+\s+(?P<name>[A-Za-z0-9_\[\]]+)\s+"
    r"[-.01]+\s+=\s+(?P<value>-?\d+)\s*$"
)
TRACE_UNIT_PATTERN = re.compile(r"nal_unit_type:\s*(?P<value>\d+)\(")

HEADER_TRACE_LOCK = threading.RLock()

H264_LEVEL_1B     = 9
H264_LEVEL_1B_IDC = 11


@dataclass(frozen = True)
class _PacketSummary:
    bitrate           : Optional[int] = None
    level             : Optional[int] = None
    refframes         : Optional[int] = None
    crop              : Optional[str] = None
    field_order       : Optional[str] = None
    field_order_count : int = 0


@dataclass(frozen = True)
class _FrameSummary:
    frame_count         : Optional[int] = None
    gop                 : Optional[int] = None
    interval_intraframe : Optional[int] = None
    pframes             : Optional[int] = None
    bframes             : Optional[int] = None
    scan_mode           : Optional[str] = None


@dataclass(frozen = True)
class _EncodedAnalysis:
    """Cached whole-stream values used by encoded-only metrics."""

    bitrate             : Optional[int] = None
    level               : Optional[int] = None
    gop                 : Optional[int] = None
    interval_intraframe : Optional[int] = None
    pframes             : Optional[int] = None
    bframes             : Optional[int] = None
    refframes           : Optional[int] = None
    frame_count         : Optional[int] = None
    scan_type           : Optional[str] = None
    crop                : Optional[str] = None


@dataclass(frozen = True)
class _TraceCodec:
    sps_unit           : int
    level_field        : str
    reference_field    : Pattern[str]
    crop_parser        : Callable[
        [Dict[str, int]],
        Optional[Tuple[int, int, int, int]],
    ]
    top_field_first    : FrozenSet[int]
    bottom_field_first : FrozenSet[int]
    field_sequence     : Optional[Tuple[int, int]] = None


class _TraceHeaderParser:
    """Reduce FFmpeg trace_headers messages to stable codec observations."""

    def __init__(self, codec_name: Optional[str]):
        self.codec_name = codec_name
        self.parameter_sets = []  # type: List[Dict[str, int]]
        self.access_unit_structures = []  # type: List[List[int]]
        self._parameter_set = None  # type: Optional[Dict[str, int]]

    def feed(
        self,
        logs: Iterable[Tuple[int, str, str]],
        access_unit: bool = False,
    ) -> None:
        codec = _TRACE_CODECS.get(self.codec_name or "")

        if codec is None:
            return

        picture_structures = []  # type: List[int]

        for _, component, message in logs:
            if component != "trace_headers":
                continue

            if TRACE_UNIT_PATTERN.search(message) is not None:
                continue

            field_match = TRACE_FIELD_PATTERN.match(message)

            if field_match is None:
                continue

            name  = field_match.group("name")
            value = int(field_match.group("value"))

            if name == "nal_unit_type":
                self._finish_parameter_set()

                if value == codec.sps_unit:
                    self._parameter_set = {}

                continue

            if name == "pic_struct":
                picture_structures.append(value)

            if self._parameter_set is not None:
                self._parameter_set[name] = value

        if access_unit:
            self.access_unit_structures.append(picture_structures)

    def values(
        self,
    ) -> Tuple[Optional[int], Optional[int], Optional[str], Optional[str], int]:
        self._finish_parameter_set()

        return (
            _codec_level(self.codec_name, self.parameter_sets),
            _reference_frames(self.codec_name, self.parameter_sets),
            _crop_text(self.codec_name, self.parameter_sets),
            _field_order(self.codec_name, self.access_unit_structures),
            len(self.access_unit_structures),
        )

    def _finish_parameter_set(self) -> None:
        if self._parameter_set is not None:
            self.parameter_sets.append(self._parameter_set)
            self._parameter_set = None


def _codec_level(
    codec_name: Optional[str],
    parameter_sets: List[Dict[str, int]],
) -> Optional[int]:
    codec = _TRACE_CODECS.get(codec_name or "")

    if codec is None or not parameter_sets:
        return None

    levels = []

    for fields in parameter_sets:
        level = fields.get(codec.level_field)

        if level is None or level < 0:
            return None

        if codec_name == "h264" and level == H264_LEVEL_1B_IDC:
            constraint_set3 = fields.get("constraint_set3_flag")

            if constraint_set3 not in (0, 1):
                return None

            if constraint_set3 == 1:
                level = H264_LEVEL_1B

        levels.append(level)

    return levels[0] if len(set(levels)) == 1 else None


def _reference_frames(
    codec_name: Optional[str],
    parameter_sets: List[Dict[str, int]],
) -> Optional[int]:
    codec = _TRACE_CODECS.get(codec_name or "")

    if codec is None:
        return None

    values = [
        value
        for fields in parameter_sets
        for name, value in fields.items()
        if codec.reference_field.fullmatch(name) is not None and value >= 0
    ]

    return max(values) if values else None


def _crop_text(
    codec_name: Optional[str],
    parameter_sets: List[Dict[str, int]],
) -> Optional[str]:
    codec = _TRACE_CODECS.get(codec_name or "")

    if codec is None or not parameter_sets:
        return None

    crops = []  # type: List[Tuple[int, int, int, int]]

    for fields in parameter_sets:
        crop = codec.crop_parser(fields)

        if crop is None:
            return None

        crops.append(crop)

    if len(set(crops)) != 1:
        return None

    return ":".join(str(value) for value in crops[0])


def _h264_crop(fields: Dict[str, int]) -> Optional[Tuple[int, int, int, int]]:
    cropping_flag = fields.get("frame_cropping_flag")
    frame_only    = fields.get("frame_mbs_only_flag")

    if cropping_flag not in (0, 1) or frame_only not in (0, 1):
        return None

    chroma_format = fields.get("chroma_format_idc", 1)

    if fields.get("separate_colour_plane_flag", 0) == 1:
        chroma_format = 0

    width_in_mbs_minus_one = fields.get("pic_width_in_mbs_minus1")
    height_in_maps_minus_one = fields.get("pic_height_in_map_units_minus1")

    if (
        width_in_mbs_minus_one is None
        or width_in_mbs_minus_one < 0
        or height_in_maps_minus_one is None
        or height_in_maps_minus_one < 0
    ):
        coded_size = (None, None)
    else:
        coded_width  = (width_in_mbs_minus_one + 1) * H264_MACROBLOCK_SIZE
        coded_height = (
            (height_in_maps_minus_one + 1)
            * H264_MACROBLOCK_SIZE
            * (2 - frame_only)
        )
        coded_size   = (coded_width, coded_height)

    return _scaled_crop(
        fields,
        cropping_flag,
        chroma_format,
        (
            "frame_crop_left_offset",
            "frame_crop_right_offset",
            "frame_crop_top_offset",
            "frame_crop_bottom_offset",
        ),
        coded_size,
        vertical_multiplier = 2 - frame_only,
    )


def _h265_crop(fields: Dict[str, int]) -> Optional[Tuple[int, int, int, int]]:
    cropping_flag = fields.get("conformance_window_flag")

    if cropping_flag not in (0, 1):
        return None

    return _scaled_crop(
        fields,
        cropping_flag,
        fields.get("chroma_format_idc", 1),
        (
            "conf_win_left_offset",
            "conf_win_right_offset",
            "conf_win_top_offset",
            "conf_win_bottom_offset",
        ),
        (
            fields.get("pic_width_in_luma_samples"),
            fields.get("pic_height_in_luma_samples"),
        ),
    )


def _scaled_crop(
    fields: Dict[str, int],
    cropping_flag: int,
    chroma_format: int,
    offset_names: Tuple[str, str, str, str],
    coded_size: Tuple[Optional[int], Optional[int]],
    vertical_multiplier: int = 1,
) -> Optional[Tuple[int, int, int, int]]:
    if cropping_flag == 0:
        return (0, 0, 0, 0)

    units = _chroma_crop_units(chroma_format)

    if units is None:
        return None

    unit_x, unit_y = units
    unit_y *= vertical_multiplier
    offsets = tuple(fields.get(name) for name in offset_names)

    if any(value is None or value < 0 for value in offsets):
        return None

    left, right, top, bottom = offsets
    crop = (left * unit_x, right * unit_x, top * unit_y, bottom * unit_y)
    coded_width, coded_height = coded_size

    if (
        coded_width is None
        or coded_width <= crop[0] + crop[1]
        or coded_height is None
        or coded_height <= crop[2] + crop[3]
    ):
        return None

    return crop


def _chroma_crop_units(chroma_format: int) -> Optional[Tuple[int, int]]:
    return {
        0 : (1, 1),
        1 : (2, 2),
        2 : (2, 1),
        3 : (1, 1),
    }.get(chroma_format)


_TRACE_CODECS = {
    "h264" : _TraceCodec(
        sps_unit           = 7,
        level_field        = "level_idc",
        reference_field    = re.compile(r"max_num_ref_frames"),
        crop_parser        = _h264_crop,
        top_field_first    = frozenset((3, 5)),
        bottom_field_first = frozenset((4, 6)),
    ),
    "hevc" : _TraceCodec(
        sps_unit           = 33,
        level_field        = "general_level_idc",
        reference_field    = re.compile(
            r"sps_max_dec_pic_buffering_minus1\[\d+\]"
        ),
        crop_parser        = _h265_crop,
        top_field_first    = frozenset((3, 5, 10, 11)),
        bottom_field_first = frozenset((4, 6, 9, 12)),
        field_sequence     = (1, 2),
    ),
}


def _field_order(
    codec_name: Optional[str],
    picture_structure_groups: List[List[int]],
) -> Optional[str]:
    codec = _TRACE_CODECS.get(codec_name or "")

    if codec is None:
        return None

    if any(len(group) != 1 for group in picture_structure_groups):
        return None

    picture_structures = [group[0] for group in picture_structure_groups]
    orders = []

    for value in picture_structures:
        if value in codec.top_field_first:
            orders.append("interlace_tff")
        elif value in codec.bottom_field_first:
            orders.append("interlace_bff")
        else:
            orders.append(None)

    distinct_orders = set(order for order in orders if order is not None)

    if len(distinct_orders) == 1 and None not in orders:
        return orders[0]

    if codec.field_sequence is not None and picture_structures:
        first_field, second_field = codec.field_sequence

        if all(
            value in codec.field_sequence
            for value in picture_structures
        ):
            # HEVC field pictures arrive in decode order, so B pictures can
            # group like fields. A legal sequence still has paired counts.
            first_signal = picture_structures[0]
            expected_first_count = (len(picture_structures) + 1) // 2
            expected_second_count = len(picture_structures) // 2

            if (
                picture_structures.count(first_signal) == expected_first_count
                and picture_structures.count(
                    second_field if first_signal == first_field else first_field
                ) == expected_second_count
            ):
                return (
                    "interlace_tff"
                    if first_signal == first_field
                    else "interlace_bff"
                )

    return None


@dataclass(frozen = True)
class SourcePlane:
    offset            : int
    stride            : int
    visible_row_bytes : int
    visible_rows      : int


@dataclass(frozen = True)
class RawLayout:
    width       : int
    height      : int
    stride      : int
    sliceheight : int
    planes      : Tuple[SourcePlane, ...]
    frame_size  : int

    @property
    def visible_sample_count(self) -> int:
        return sum(
            plane.visible_row_bytes * plane.visible_rows
            for plane in self.planes
        )


@dataclass(frozen = True)
class RawPlane:
    """Derive one stored plane from the first-plane raw geometry."""

    row_bytes_numerator    : int = 1
    row_bytes_denominator  : int = 1
    stride_divisor         : int = 1
    height_divisor         : int = 1


@dataclass(frozen = True)
class RawFormat:
    """Stored plane geometry and comparison format for one raw layout."""

    name                       : str
    av_format                  : str
    comparison_format          : str
    bytes_per_pixel            : int
    width_alignment            : int = 1
    height_alignment           : int = 1
    stride_alignment           : int = 1
    sliceheight_alignment      : int = 1
    stored_height_numerator    : int = 1
    stored_height_denominator : int = 1
    planes                     : Tuple[RawPlane, ...] = ()

    @property
    def has_chroma_plane(self) -> bool:
        return len(self._planes()) > 1

    def _planes(self) -> Tuple[RawPlane, ...]:
        if self.planes:
            return self.planes

        return (RawPlane(row_bytes_numerator = self.bytes_per_pixel),)

    def validate(self, descriptor: MediaDescriptor) -> None:
        self.layout(descriptor)

        if descriptor.frame_count is not None and descriptor.frame_count <= 0:
            raise ConfigurationError("Raw frame_count must be a positive integer")

        if descriptor.framerate is not None and descriptor.framerate <= 0:
            raise ConfigurationError("Raw framerate must be positive")

    def layout(self, descriptor: MediaDescriptor) -> RawLayout:
        width  = _required(descriptor.width, "width")
        height = _required(descriptor.height, "height")
        stride = descriptor.stride
        sliceheight = descriptor.sliceheight

        if stride is None:
            stride = width * self.bytes_per_pixel

        if sliceheight is None:
            sliceheight = height

        numeric_fields = {
            "width"       : width,
            "height"      : height,
            "stride"      : stride,
            "sliceheight" : sliceheight,
        }

        for field_name, value in numeric_fields.items():
            if value <= 0:
                raise ConfigurationError(
                    "Raw {} must be a positive integer".format(field_name)
                )

        if width % self.width_alignment != 0:
            raise ConfigurationError(
                "Raw format {} requires width aligned to {}".format(
                    self.name,
                    self.width_alignment,
                )
            )

        if height % self.height_alignment != 0:
            raise ConfigurationError(
                "Raw format {} requires height aligned to {}".format(
                    self.name,
                    self.height_alignment,
                )
            )

        if sliceheight < height:
            raise ConfigurationError("Raw sliceheight must be at least height")

        if sliceheight % self.sliceheight_alignment != 0:
            raise ConfigurationError(
                "Raw format {} requires sliceheight aligned to {}".format(
                    self.name,
                    self.sliceheight_alignment,
                )
            )

        minimum_stride = width * self.bytes_per_pixel

        if stride < minimum_stride:
            raise ConfigurationError(
                "Raw stride must be at least {} bytes for {}".format(
                    minimum_stride,
                    self.name,
                )
            )

        if stride % self.stride_alignment != 0:
            raise ConfigurationError(
                "Raw format {} requires stride aligned to {}".format(
                    self.name,
                    self.stride_alignment,
                )
            )

        source_planes = []
        offset = 0

        for plane in self._planes():
            row_bytes_product = width * plane.row_bytes_numerator

            if row_bytes_product % plane.row_bytes_denominator != 0:
                raise ConfigurationError(
                    "Raw format {} has invalid plane row geometry".format(self.name)
                )

            if (
                stride % plane.stride_divisor != 0
                or height % plane.height_divisor != 0
                or sliceheight % plane.height_divisor != 0
            ):
                raise ConfigurationError(
                    "Raw format {} has invalid plane storage geometry".format(
                        self.name
                    )
                )

            plane_stride = stride // plane.stride_divisor
            visible_rows = height // plane.height_divisor
            stored_rows = sliceheight // plane.height_divisor
            source_planes.append(SourcePlane(
                offset            = offset,
                stride            = plane_stride,
                visible_row_bytes = (
                    row_bytes_product // plane.row_bytes_denominator
                ),
                visible_rows      = visible_rows,
            ))
            offset += plane_stride * stored_rows

        return RawLayout(
            width       = width,
            height      = height,
            stride      = stride,
            sliceheight = sliceheight,
            planes      = tuple(source_planes),
            frame_size  = offset,
        )

    def source_planes(self, descriptor: MediaDescriptor) -> List[SourcePlane]:
        return list(self.layout(descriptor).planes)

    def frame_size(self, descriptor: MediaDescriptor) -> int:
        return self.layout(descriptor).frame_size


RAW_FORMATS: Dict[str, RawFormat] = {
    "I444" : RawFormat(
        name              = "I444",
        av_format         = "yuv444p",
        comparison_format = "yuv444p",
        bytes_per_pixel   = 1,
        stored_height_numerator = 3,
        planes = (
            RawPlane(),
            RawPlane(),
            RawPlane(),
        ),
    ),
    "I420" : RawFormat(
        name              = "I420",
        av_format         = "yuv420p",
        comparison_format = "yuv420p",
        bytes_per_pixel   = 1,
        width_alignment   = 2,
        height_alignment  = 2,
        stride_alignment  = 2,
        sliceheight_alignment = 2,
        stored_height_numerator    = 3,
        stored_height_denominator = 2,
        planes = (
            RawPlane(),
            RawPlane(
                row_bytes_denominator = 2,
                stride_divisor        = 2,
                height_divisor        = 2,
            ),
            RawPlane(
                row_bytes_denominator = 2,
                stride_divisor        = 2,
                height_divisor        = 2,
            ),
        ),
    ),
    "YUY2" : RawFormat(
        name              = "YUY2",
        av_format         = "yuyv422",
        comparison_format = "yuyv422",
        bytes_per_pixel   = 2,
        width_alignment   = 2,
    ),
    "UYVY" : RawFormat(
        name              = "UYVY",
        av_format         = "uyvy422",
        comparison_format = "yuyv422",
        bytes_per_pixel   = 2,
        width_alignment   = 2,
    ),
    "YVYU" : RawFormat(
        name              = "YVYU",
        av_format         = "yvyu422",
        comparison_format = "yuyv422",
        bytes_per_pixel   = 2,
        width_alignment   = 2,
    ),
    "NV12" : RawFormat(
        name              = "NV12",
        av_format         = "nv12",
        comparison_format = "yuv420p",
        bytes_per_pixel   = 1,
        width_alignment   = 2,
        height_alignment  = 2,
        stride_alignment  = 2,
        sliceheight_alignment = 2,
        stored_height_numerator    = 3,
        stored_height_denominator = 2,
        planes = (
            RawPlane(),
            RawPlane(height_divisor = 2),
        ),
    ),
    "GRAY8" : RawFormat(
        name              = "GRAY8",
        av_format         = "gray",
        comparison_format = "gray",
        bytes_per_pixel   = 1,
    ),
    "RGB" : RawFormat(
        name              = "RGB",
        av_format         = "rgb24",
        comparison_format = "rgb24",
        bytes_per_pixel   = 3,
    ),
    "BGR" : RawFormat(
        name              = "BGR",
        av_format         = "bgr24",
        comparison_format = "rgb24",
        bytes_per_pixel   = 3,
    ),
    "ARGB" : RawFormat(
        name              = "ARGB",
        av_format         = "argb",
        comparison_format = "rgba",
        bytes_per_pixel   = 4,
    ),
    "RGBA" : RawFormat(
        name              = "RGBA",
        av_format         = "rgba",
        comparison_format = "rgba",
        bytes_per_pixel   = 4,
    ),
    "ABGR" : RawFormat(
        name              = "ABGR",
        av_format         = "abgr",
        comparison_format = "rgba",
        bytes_per_pixel   = 4,
    ),
    "BGRA" : RawFormat(
        name              = "BGRA",
        av_format         = "bgra",
        comparison_format = "rgba",
        bytes_per_pixel   = 4,
    ),
    "RGB16" : RawFormat(
        name              = "RGB16",
        av_format         = "rgb565le",
        comparison_format = "rgb24",
        bytes_per_pixel   = 2,
    ),
}


def _required(value: Optional[int], field_name: str) -> int:
    if value is None:
        raise ConfigurationError("Raw descriptor is missing '{}'".format(field_name))

    return value


def validate_raw_file(descriptor: MediaDescriptor) -> None:
    """Validate raw geometry and complete stored frames."""

    raw_format = RAW_FORMATS.get(descriptor.format or "")

    if raw_format is None:
        raise ConfigurationError(
            "Unsupported raw format '{}'".format(descriptor.format or "")
        )

    raw_format.validate(descriptor)

    frame_size  = raw_format.frame_size(descriptor)
    actual_size = descriptor.path.stat().st_size

    if actual_size % frame_size != 0:
        raise ConfigurationError(
            "Raw file size is {} bytes; expected a whole number of {}-byte frames".format(
                actual_size,
                frame_size,
            )
        )

    available_frames = actual_size // frame_size
    frame_count      = descriptor.frame_count

    if frame_count is not None and available_frames < frame_count:
        raise ConfigurationError(
            "Raw file size is {} bytes; expected at least {} frames of {} bytes".format(
                actual_size,
                frame_count,
                frame_size,
            )
        )


class VideoSource(ABC):
    """Read normalized metadata and visible frames from one media file."""

    def __init__(self, descriptor: MediaDescriptor):
        self.descriptor = descriptor

    @property
    def is_raw(self) -> bool:
        return self.descriptor.is_raw

    @abstractmethod
    def metadata(self) -> VideoMetadata:
        raise NotImplementedError

    @abstractmethod
    def frames(self) -> Iterator[av.VideoFrame]:
        raise NotImplementedError



class _EncodedAnalysisSource(Protocol):
    """Structural interface for sources that expose whole-stream metrics."""

    def analysis(self) -> _EncodedAnalysis:
        ...


class RawVideoSource(VideoSource):
    """Read headerless raw frames while removing stored row padding."""

    def __init__(self, descriptor: MediaDescriptor):
        super().__init__(descriptor)
        self.raw_format = RAW_FORMATS[descriptor.format or ""]

    def metadata(self) -> VideoMetadata:
        return VideoMetadata(
            width     = _required(self.descriptor.width, "width"),
            height    = _required(self.descriptor.height, "height"),
            framerate = self.descriptor.framerate,
            format    = self.raw_format.av_format,
        )

    def frames(self) -> Iterator[av.VideoFrame]:
        width       = _required(self.descriptor.width, "width")
        height      = _required(self.descriptor.height, "height")
        frame_size  = self.raw_format.frame_size(self.descriptor)
        time_base   = _time_base(self.descriptor.framerate)

        try:
            with self.descriptor.path.open("rb") as media_file:
                frame_index = 0

                while True:
                    data = media_file.read(frame_size)

                    if not data:
                        break

                    if len(data) != frame_size:
                        raise MediaError(
                            "Raw file ended while reading frame {}".format(frame_index)
                        )

                    frame     = av.VideoFrame(width, height, self.raw_format.av_format)
                    frame.pts = frame_index

                    if time_base is not None:
                        frame.time_base = time_base

                    self._copy_visible_planes(data, frame)
                    yield frame
                    frame_index += 1
        except OSError as error:
            raise MediaError(
                "Cannot read raw media '{}': {}".format(self.descriptor.path, error)
            ) from error

    def _copy_visible_planes(self, data: bytes, frame: av.VideoFrame) -> None:
        source_planes = self.raw_format.source_planes(self.descriptor)

        if len(source_planes) != len(frame.planes):
            raise MediaError(
                "Raw format {} produced an unexpected plane layout".format(
                    self.raw_format.name
                )
            )

        for source, destination in zip(source_planes, frame.planes):
            destination_data = bytearray(destination.buffer_size)

            if destination.line_size < source.visible_row_bytes:
                raise MediaError(
                    "PyAV plane stride is smaller than the visible raw row"
                )

            for row in range(source.visible_rows):
                source_start      = source.offset + row * source.stride
                source_end        = source_start + source.visible_row_bytes
                destination_start = row * destination.line_size
                destination_end   = destination_start + source.visible_row_bytes
                destination_data[destination_start:destination_end] = data[
                    source_start:source_end
                ]

            destination.update(bytes(destination_data))


class EncodedVideoSource(VideoSource):
    """Read H.264 or H.265 video from elementary streams or MP4."""

    def __init__(self, descriptor: MediaDescriptor):
        super().__init__(descriptor)
        self._metadata: Optional[VideoMetadata] = None
        self._analysis: Optional[_EncodedAnalysis] = None
        self._analysis_metrics = frozenset()  # type: FrozenSet[str]

    def metadata(self) -> VideoMetadata:
        if self._metadata is not None:
            return self._metadata

        try:
            with self._open() as container:
                stream  = _supported_video_stream(container, self.descriptor.path)
                self._metadata = _stream_metadata(stream)
                return self._metadata
        except MediaError:
            raise
        except (OSError, av.FFmpegError) as error:
            raise MediaError(
                "Cannot inspect encoded media '{}': {}".format(
                    self.descriptor.path,
                    error,
                )
            ) from error

    def frames(self) -> Iterator[av.VideoFrame]:
        try:
            with self._open() as container:
                stream = _supported_video_stream(container, self.descriptor.path)

                for frame in container.decode(stream):
                    yield frame
        except MediaError:
            raise
        except (OSError, av.FFmpegError) as error:
            raise MediaError(
                "Cannot decode encoded media '{}': {}".format(
                    self.descriptor.path,
                    error,
                )
            ) from error

    def analysis(self) -> _EncodedAnalysis:
        if self._analysis is not None:
            return self._analysis

        packets = self._packet_summary()
        frames  = self._frame_summary()

        if frames.scan_mode == "progressive":
            scan_type = "progressive"
        elif (
            frames.scan_mode == "interlaced"
            and packets.field_order_count == frames.frame_count
        ):
            scan_type = packets.field_order
        else:
            scan_type = None

        self._analysis = _EncodedAnalysis(
            bitrate             = packets.bitrate,
            level               = packets.level,
            gop                 = frames.gop,
            interval_intraframe = frames.interval_intraframe,
            pframes             = frames.pframes,
            bframes             = frames.bframes,
            refframes           = packets.refframes,
            frame_count         = frames.frame_count,
            scan_type           = scan_type,
            crop                = packets.crop,
        )
        return self._analysis

    def inspect(self, metric_names: Iterable[str]) -> _EncodedAnalysis:
        """Inspect all requested packet and frame values in at most one pass."""

        requested = frozenset(metric_names)

        if (
            self._analysis is not None
            and requested.issubset(self._analysis_metrics)
        ):
            return self._analysis

        trace_metrics = frozenset((
            "refframes",
            "scan_type",
            "crop",
        ))
        frame_metrics = frozenset((
            "gop",
            "interval-intraframe",
            "pframes",
            "bframes",
            "frame_count",
            "scan_type",
        ))
        inspect_frames = bool(requested & frame_metrics)

        try:
            with self._open() as container:
                stream     = _supported_video_stream(container, self.descriptor.path)
                codec_name = _codec_name(stream.codec_context)
                self._metadata = _stream_metadata(stream)
                stream_bitrate = _stream_bitrate(stream)
                trace_headers = bool(
                    requested & trace_metrics
                    or (
                        "level" in requested
                        and self._metadata.level is None
                    )
                )
                inspect_packets = bool(
                    trace_headers
                    or (
                        "bitrate" in requested
                        and stream_bitrate is None
                    )
                )

                if inspect_packets or inspect_frames:
                    packets, frames = _inspect_stream(
                        container,
                        stream,
                        codec_name,
                        inspect_packets = inspect_packets,
                        inspect_frames  = inspect_frames,
                        trace_headers   = trace_headers,
                    )
                else:
                    packets = _PacketSummary(
                        bitrate = stream_bitrate,
                        level   = self._metadata.level,
                    )
                    frames  = _FrameSummary()
        except MediaError:
            raise
        except (OSError, av.FFmpegError):
            packets = _PacketSummary()
            frames  = _FrameSummary()

        self._analysis = _analysis_from_summaries(packets, frames)
        self._analysis_metrics = requested
        return self._analysis

    def _packet_summary(self) -> _PacketSummary:
        try:
            with self._open() as container:
                stream     = _supported_video_stream(container, self.descriptor.path)
                codec_name = _codec_name(stream.codec_context)
                return _inspect_packets(container, stream, codec_name)
        except (MediaError, OSError, av.FFmpegError):
            return _PacketSummary()

    def _frame_summary(self) -> _FrameSummary:
        try:
            with self._open() as container:
                stream = _supported_video_stream(container, self.descriptor.path)
                frame_types = []
                interlaced  = []

                for frame in container.decode(stream):
                    picture_type = getattr(frame.pict_type, "name", None)
                    frame_types.append(str(picture_type or frame.pict_type))
                    interlaced.append(bool(frame.interlaced_frame))

                return _summarize_frames(frame_types, interlaced)
        except (MediaError, OSError, av.FFmpegError):
            return _FrameSummary()

    def _open(self):
        input_format = ENCODED_INPUT_FORMATS.get(self.descriptor.extension)

        if input_format is None:
            return av.open(str(self.descriptor.path), mode = "r")

        return av.open(
            str(self.descriptor.path),
            mode   = "r",
            format = input_format,
        )


def _inspect_packets(container, stream, codec_name: Optional[str]) -> _PacketSummary:
    packets, _ = _inspect_stream(
        container,
        stream,
        codec_name,
        inspect_packets = True,
        inspect_frames  = False,
        trace_headers   = True,
    )
    return packets


def _inspect_stream(
    container,
    stream,
    codec_name: Optional[str],
    inspect_packets: bool,
    inspect_frames: bool,
    trace_headers: bool,
) -> Tuple[_PacketSummary, _FrameSummary]:
    stream_bitrate = _stream_bitrate(stream)

    stream_duration = _duration(
        getattr(stream, "duration", None),
        getattr(stream, "time_base", None),
    )
    payload_size     = 0
    payload_count    = 0
    packet_duration  = Fraction(0, 1)
    complete_timing  = True
    parser           = _TraceHeaderParser(codec_name)
    trace_valid      = (
        trace_headers
        and "trace_headers" in av.bitstream_filters_available
    )
    trace_filter     = None
    frame_types = []
    interlaced  = []
    frame_valid = True
    log_context = HEADER_TRACE_LOCK if trace_headers else nullcontext()

    with log_context:
        previous_level = av.logging.get_level() if trace_headers else None

        try:
            if trace_valid:
                av.logging.set_level(av.logging.TRACE)

                try:
                    with av.logging.Capture(local = True) as logs:
                        trace_filter = av.BitStreamFilterContext(
                            "trace_headers",
                            stream,
                        )
                    parser.feed(logs)
                except (ValueError, OSError, av.FFmpegError):
                    trace_valid = False

            for packet in container.demux(stream):
                if inspect_packets and packet.size > 0:
                    payload_size  += packet.size
                    payload_count += 1
                    duration = _duration(
                        getattr(packet, "duration", None),
                        getattr(packet, "time_base", None)
                        or getattr(stream, "time_base", None),
                    )

                    if duration is None:
                        complete_timing = False
                    else:
                        packet_duration += duration

                if inspect_frames and frame_valid:
                    try:
                        decoded_frames = packet.decode()
                    except (ValueError, OSError, av.FFmpegError):
                        frame_valid = False
                        frame_types = []
                        interlaced  = []
                        continue

                    for frame in decoded_frames:
                        picture_type = getattr(frame.pict_type, "name", None)
                        frame_types.append(str(picture_type or frame.pict_type))
                        interlaced.append(bool(frame.interlaced_frame))

                if (
                    inspect_packets
                    and packet.size > 0
                    and trace_valid
                    and trace_filter is not None
                ):
                    try:
                        with av.logging.Capture(local = True) as logs:
                            trace_filter.filter(packet)
                        parser.feed(logs, access_unit = True)
                    except (ValueError, OSError, av.FFmpegError):
                        trace_valid = False
        finally:
            if trace_headers:
                av.logging.set_level(previous_level)

    bitrate = stream_bitrate

    if bitrate is None and payload_size > 0:
        media_duration = stream_duration

        if media_duration is None and payload_count > 0 and complete_timing:
            media_duration = packet_duration

        if media_duration is not None and media_duration > 0:
            average = Fraction(payload_size * 8, 1) / media_duration
            bitrate = int(average + Fraction(1, 2))

    if trace_valid:
        level, refframes, crop, field_order, field_order_count = parser.values()
    else:
        level             = None
        refframes         = None
        crop              = None
        field_order       = None
        field_order_count = 0

    packet_summary = _PacketSummary(
        bitrate           = bitrate,
        level             = level,
        refframes         = refframes,
        crop              = crop,
        field_order       = field_order,
        field_order_count = field_order_count,
    )
    frame_summary = (
        _summarize_frames(frame_types, interlaced)
        if inspect_frames and frame_valid
        else _FrameSummary()
    )
    return packet_summary, frame_summary


def _stream_metadata(stream) -> VideoMetadata:
    context      = stream.codec_context
    pixel_format = getattr(context, "format", None)

    return VideoMetadata(
        width      = int(context.width),
        height     = int(context.height),
        framerate  = _encoded_framerate(stream, context),
        format     = getattr(pixel_format, "name", None),
        profile    = _profile_text(getattr(context, "profile", None)),
        level      = _optional_int(getattr(context, "level", None)),
        codec_name = _codec_name(context),
    )


def _stream_bitrate(stream) -> Optional[int]:
    bitrate = _positive_int(getattr(stream, "bit_rate", None))

    if bitrate is not None:
        return bitrate

    return _positive_int(getattr(stream.codec_context, "bit_rate", None))


def _analysis_from_summaries(
    packets: _PacketSummary,
    frames: _FrameSummary,
) -> _EncodedAnalysis:
    if frames.scan_mode == "progressive":
        scan_type = "progressive"
    elif (
        frames.scan_mode == "interlaced"
        and packets.field_order_count == frames.frame_count
    ):
        scan_type = packets.field_order
    else:
        scan_type = None

    return _EncodedAnalysis(
        bitrate             = packets.bitrate,
        level               = packets.level,
        gop                 = frames.gop,
        interval_intraframe = frames.interval_intraframe,
        pframes             = frames.pframes,
        bframes             = frames.bframes,
        refframes           = packets.refframes,
        frame_count         = frames.frame_count,
        scan_type           = scan_type,
        crop                = packets.crop,
    )


def _duration(value, time_base) -> Optional[Fraction]:
    if value is None or time_base is None:
        return None

    try:
        duration = Fraction(value) * Fraction(time_base)
    except (TypeError, ValueError, ZeroDivisionError):
        return None

    return duration if duration > 0 else None


def _positive_int(value) -> Optional[int]:
    number = _optional_int(value)
    return number if number is not None and number > 0 else None


def _summarize_frames(
    frame_types: List[str],
    interlaced: List[bool],
) -> _FrameSummary:
    frame_count = len(frame_types)
    intra_indices = [
        index
        for index, picture_type in enumerate(frame_types)
        if picture_type == "I"
    ]
    gop = None
    interval = None
    pframes = None
    bframes = None

    if intra_indices:
        group_lengths = [
            end - start
            for start, end in zip(intra_indices, intra_indices[1:])
        ]
        group_lengths.append(frame_count - intra_indices[-1])
        gop = max(group_lengths)

    if len(intra_indices) >= 2:
        longest_start = intra_indices[0]
        longest_end   = intra_indices[1]

        for start, end in zip(intra_indices, intra_indices[1:]):
            if end - start > longest_end - longest_start:
                longest_start = start
                longest_end   = end

        interval = longest_end - longest_start
        between  = frame_types[longest_start + 1:longest_end]
        pframes  = between.count("P")
        bframes  = between.count("B")

    if frame_count == 0:
        scan_mode = None
    elif all(not value for value in interlaced):
        scan_mode = "progressive"
    elif all(interlaced):
        scan_mode = "interlaced"
    else:
        scan_mode = None

    return _FrameSummary(
        frame_count         = frame_count,
        gop                 = gop,
        interval_intraframe = interval,
        pframes             = pframes,
        bframes             = bframes,
        scan_mode           = scan_mode,
    )


def _supported_video_stream(container, path: Path):
    if not container.streams.video:
        raise MediaError("Media '{}' does not contain a video stream".format(path))

    for stream in container.streams.video:
        if _codec_name(stream.codec_context) in SUPPORTED_ENCODED_CODECS:
            return stream

    raise MediaError(
        "Media '{}' does not contain an H.264 or H.265 video stream".format(path)
    )


def _codec_name(context) -> Optional[str]:
    codec_name = getattr(context, "name", None)

    if codec_name is None and getattr(context, "codec", None) is not None:
        codec_name = context.codec.name

    if codec_name is None:
        return None

    return str(codec_name).lower()


def _encoded_framerate(stream, context) -> Optional[Fraction]:
    for value in (
        getattr(stream, "guessed_rate", None),
        getattr(context, "framerate", None),
        getattr(stream, "average_rate", None),
    ):
        if value is None:
            continue

        try:
            framerate = Fraction(value)
        except (TypeError, ValueError, ZeroDivisionError):
            continue

        if framerate > 0:
            return framerate

    return None


def _time_base(framerate: Optional[Fraction]) -> Optional[Fraction]:
    if framerate is None:
        return None

    return Fraction(framerate.denominator, framerate.numerator)


def _profile_text(profile) -> Optional[str]:
    if profile is None:
        return None

    if hasattr(profile, "name"):
        return str(profile.name)

    return str(profile)


def _optional_int(value) -> Optional[int]:
    if value is None:
        return None

    try:
        number = int(value)
    except (TypeError, ValueError):
        return None

    return number if number >= 0 else None


def create_video_source(descriptor: MediaDescriptor) -> VideoSource:
    """Create the video reader selected by a validated descriptor."""

    if descriptor.is_raw:
        return RawVideoSource(descriptor)

    return EncodedVideoSource(descriptor)


def media_extension(path: Path) -> str:
    return os.path.splitext(path.name)[1].lower()
