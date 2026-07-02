import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Dict, Iterator, List, Optional

import av

from media_checker.errors import ConfigurationError, MediaError
from media_checker.models import MediaDescriptor, VideoMetadata


RAW_VIDEO_EXTENSIONS     = (".raw", ".yuv")
ENCODED_VIDEO_EXTENSIONS = (".264", ".265")
SUPPORTED_EXTENSIONS     = RAW_VIDEO_EXTENSIONS + ENCODED_VIDEO_EXTENSIONS

ENCODED_INPUT_FORMATS = {
    ".264" : "h264",
    ".265" : "hevc",
}


@dataclass(frozen = True)
class SourcePlane:
    offset            : int
    stride            : int
    visible_row_bytes : int
    visible_rows      : int


@dataclass(frozen = True)
class RawFormat:
    """Stored plane geometry and comparison format for one raw layout."""

    name                       : str
    av_format                  : str
    comparison_format          : str
    bytes_per_pixel            : int
    width_alignment            : int = 1
    height_alignment           : int = 1
    chroma_height_divisor      : Optional[int] = None
    stored_height_numerator    : int = 1
    stored_height_denominator : int = 1

    @property
    def has_chroma_plane(self) -> bool:
        return self.chroma_height_divisor is not None

    def validate(self, descriptor: MediaDescriptor) -> None:
        width       = _required(descriptor.width, "width")
        height      = _required(descriptor.height, "height")
        frame_count = _required(descriptor.frame_count, "frame_count")
        stride      = _required(descriptor.stride, "stride")
        sliceheight = _required(descriptor.sliceheight, "sliceheight")

        numeric_fields = {
            "width"       : width,
            "height"      : height,
            "frame_count" : frame_count,
            "stride"      : stride,
            "sliceheight" : sliceheight,
        }

        for field_name, value in numeric_fields.items():
            if value <= 0:
                raise ConfigurationError(
                    "Raw {} must be a positive integer".format(field_name)
                )

        if descriptor.framerate is None or descriptor.framerate <= 0:
            raise ConfigurationError("Raw framerate must be positive")

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

        if self.has_chroma_plane and sliceheight % self.height_alignment != 0:
            raise ConfigurationError(
                "Raw format {} requires sliceheight aligned to {}".format(
                    self.name,
                    self.height_alignment,
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

        if self.has_chroma_plane and stride % self.width_alignment != 0:
            raise ConfigurationError(
                "Raw format {} requires stride aligned to {}".format(
                    self.name,
                    self.width_alignment,
                )
            )

    def source_planes(self, descriptor: MediaDescriptor) -> List[SourcePlane]:
        width       = _required(descriptor.width, "width")
        height      = _required(descriptor.height, "height")
        stride      = _required(descriptor.stride, "stride")
        sliceheight = _required(descriptor.sliceheight, "sliceheight")

        if not self.has_chroma_plane:
            return [
                SourcePlane(
                    offset            = 0,
                    stride            = stride,
                    visible_row_bytes = width * self.bytes_per_pixel,
                    visible_rows      = height,
                )
            ]

        luma_size = stride * sliceheight
        chroma_height_divisor = self.chroma_height_divisor

        if chroma_height_divisor is None:
            raise ConfigurationError(
                "Raw format {} has no chroma-plane geometry".format(self.name)
            )

        return [
            SourcePlane(
                offset            = 0,
                stride            = stride,
                visible_row_bytes = width,
                visible_rows      = height,
            ),
            SourcePlane(
                offset            = luma_size,
                stride            = stride,
                visible_row_bytes = width,
                visible_rows      = height // chroma_height_divisor,
            ),
        ]

    def frame_size(self, descriptor: MediaDescriptor) -> int:
        stride      = _required(descriptor.stride, "stride")
        sliceheight = _required(descriptor.sliceheight, "sliceheight")

        stored_luma_size = stride * sliceheight
        return (
            stored_luma_size
            * self.stored_height_numerator
            // self.stored_height_denominator
        )


RAW_FORMATS: Dict[str, RawFormat] = {
    "NV12" : RawFormat(
        name              = "NV12",
        av_format         = "nv12",
        comparison_format = "yuv420p",
        bytes_per_pixel   = 1,
        width_alignment   = 2,
        height_alignment  = 2,
        chroma_height_divisor      = 2,
        stored_height_numerator    = 3,
        stored_height_denominator = 2,
    ),
    "YUY2" : RawFormat(
        name              = "YUY2",
        av_format         = "yuyv422",
        comparison_format = "yuyv422",
        bytes_per_pixel   = 2,
        width_alignment   = 2,
    ),
    "RGB16" : RawFormat(
        name              = "RGB16",
        av_format         = "rgb565le",
        comparison_format = "rgb24",
        bytes_per_pixel   = 2,
    ),
    "RGB" : RawFormat(
        name              = "RGB",
        av_format         = "rgb24",
        comparison_format = "rgb24",
        bytes_per_pixel   = 3,
    ),
    "RGBA" : RawFormat(
        name              = "RGBA",
        av_format         = "rgba",
        comparison_format = "rgba",
        bytes_per_pixel   = 4,
    ),
    "GRAY8" : RawFormat(
        name              = "GRAY8",
        av_format         = "gray",
        comparison_format = "gray",
        bytes_per_pixel   = 1,
    ),
}


def _required(value: Optional[int], field_name: str) -> int:
    if value is None:
        raise ConfigurationError("Raw descriptor is missing '{}'".format(field_name))

    return value


def validate_raw_file(descriptor: MediaDescriptor) -> None:
    """Validate raw geometry and exact stored file size."""

    raw_format = RAW_FORMATS.get(descriptor.format or "")

    if raw_format is None:
        raise ConfigurationError(
            "Unsupported raw format '{}'".format(descriptor.format or "")
        )

    raw_format.validate(descriptor)

    frame_count = _required(descriptor.frame_count, "frame_count")
    expected_size = raw_format.frame_size(descriptor) * frame_count
    actual_size   = descriptor.path.stat().st_size

    if actual_size != expected_size:
        raise ConfigurationError(
            "Raw file size is {} bytes; expected {} bytes from its descriptor".format(
                actual_size,
                expected_size,
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
        frame_count = _required(self.descriptor.frame_count, "frame_count")
        frame_size  = self.raw_format.frame_size(self.descriptor)
        time_base   = _time_base(self.descriptor.framerate)

        try:
            with self.descriptor.path.open("rb") as media_file:
                for frame_index in range(frame_count):
                    data = media_file.read(frame_size)

                    if len(data) != frame_size:
                        raise MediaError(
                            "Raw file ended while reading frame {}".format(frame_index)
                        )

                    frame           = av.VideoFrame(width, height, self.raw_format.av_format)
                    frame.pts       = frame_index
                    frame.time_base = time_base
                    self._copy_visible_planes(data, frame)
                    yield frame
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
    """Read H.264 or H.265 elementary streams through PyAV."""

    def __init__(self, descriptor: MediaDescriptor):
        super().__init__(descriptor)
        self._metadata: Optional[VideoMetadata] = None

    def metadata(self) -> VideoMetadata:
        if self._metadata is not None:
            return self._metadata

        try:
            with self._open() as container:
                stream  = _first_video_stream(container, self.descriptor.path)
                context = stream.codec_context

                codec_name = getattr(context, "name", None)
                if codec_name is None and getattr(context, "codec", None) is not None:
                    codec_name = context.codec.name

                pixel_format = getattr(context, "format", None)

                self._metadata = VideoMetadata(
                    width      = int(context.width),
                    height     = int(context.height),
                    framerate  = getattr(stream, "base_rate", None),
                    format     = getattr(pixel_format, "name", None),
                    profile    = _profile_text(getattr(context, "profile", None)),
                    level      = _optional_int(getattr(context, "level", None)),
                    codec_name = codec_name,
                )
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
                stream = _first_video_stream(container, self.descriptor.path)

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

    def _open(self):
        input_format = ENCODED_INPUT_FORMATS[self.descriptor.extension]
        return av.open(str(self.descriptor.path), mode = "r", format = input_format)


def _first_video_stream(container, path: Path):
    if not container.streams.video:
        raise MediaError("Media '{}' does not contain a video stream".format(path))

    return container.streams.video[0]


def _time_base(framerate: Optional[Fraction]) -> Fraction:
    if framerate is None:
        raise ConfigurationError("Raw descriptor is missing 'framerate'")

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
