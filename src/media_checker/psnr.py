import gc
import mmap
import re
import threading
from dataclasses import dataclass
from fractions import Fraction
from typing import Iterable, Optional, Tuple, cast

import av

from media_checker.errors import MediaError, MetricError
from media_checker.media import RAW_FORMATS, VideoSource
from media_checker.models import VideoMetadata
from media_checker.native_runtime import configure_filter_graph
from media_checker.raw_copy import RawFrameCopier


INFINITE_PSNR_VALUE = 1000.0
PSNR_DECIMAL_PLACES = 6

PSNR_FILTER_LOCK = threading.RLock()
PSNR_MINIMUM_PATTERN = re.compile(
    r"\bmin:(?P<value>inf|[0-9]+(?:\.[0-9]+)?)\b"
)

RAW_COMPARISON_FORMATS = {
    raw_format.av_format : raw_format.comparison_format
    for raw_format in RAW_FORMATS.values()
}

class _NativePsnrSetupError(Exception):
    pass


def _add_input_filters(
    graph,
    source,
    crop: Optional[Tuple[int, int]],
    target_format: str,
):
    current = source

    if crop is not None:
        crop_filter = graph.add(
            "crop",
            args = "w={}:h={}:x=0:y=0".format(crop[0], crop[1]),
        )
        current.link_to(crop_filter)
        current = crop_filter

    format_filter = graph.add(
        "format",
        args = "pix_fmts={}".format(target_format),
    )
    current.link_to(format_filter)
    return format_filter


@dataclass(frozen = True)
class _FrameSpec:
    visible_metadata : VideoMetadata
    frame_metadata   : VideoMetadata
    crop             : Optional[Tuple[int, int]] = None


class _NativeFrameComparator:
    """Own one native PSNR graph and its process-wide logging capture."""

    def __init__(
        self,
        input_metadata: VideoMetadata,
        reference_metadata: VideoMetadata,
        target_format: str,
        input_crop: Optional[Tuple[int, int]] = None,
        reference_crop: Optional[Tuple[int, int]] = None,
    ):
        self._closed = False
        self._lock_acquired = False
        self._capture = None
        self._logs = []
        self._previous_log_level = None
        self._previous_skip_repeated = None
        self._graph = None
        self._input_buffer = None
        self._reference_buffer = None
        self._sink = None

        if "psnr" not in av.filter.filters_available:
            raise _NativePsnrSetupError("FFmpeg psnr filter is unavailable")

        input_format = input_metadata.format
        reference_format = reference_metadata.format

        if input_format is None or reference_format is None:
            raise _NativePsnrSetupError("PSNR source format is unavailable")

        try:
            PSNR_FILTER_LOCK.acquire()
            self._lock_acquired = True
            self._capture = av.logging.Capture(local = True)
            self._logs = self._capture.__enter__()
            self._previous_log_level = av.logging.get_level()
            self._previous_skip_repeated = av.logging.get_skip_repeated()
            av.logging.set_level(av.logging.INFO)
            av.logging.set_skip_repeated(False)

            input_template = _template_frame(input_metadata, input_format)
            reference_template = _template_frame(
                reference_metadata,
                reference_format,
            )
            graph = av.filter.Graph()
            configure_filter_graph(graph)
            input_buffer = graph.add_buffer(template = input_template)
            reference_buffer = graph.add_buffer(template = reference_template)
            input_format_filter = _add_input_filters(
                graph,
                input_buffer,
                input_crop,
                target_format,
            )
            reference_format_filter = _add_input_filters(
                graph,
                reference_buffer,
                reference_crop,
                target_format,
            )
            psnr_filter = graph.add("psnr")
            sink = graph.add("buffersink")

            input_format_filter.link_to(psnr_filter, 0, 0)
            reference_format_filter.link_to(psnr_filter, 0, 1)
            psnr_filter.link_to(sink)
            graph.configure()

            self._graph = graph
            self._input_buffer = input_buffer
            self._reference_buffer = reference_buffer
            self._sink = sink
        except KeyboardInterrupt:
            self.close()
            raise
        except (
            AttributeError,
            RuntimeError,
            ValueError,
            OSError,
            av.FFmpegError,
        ) as error:
            self.close()
            raise _NativePsnrSetupError(str(error)) from error

    def compare(
        self,
        input_frame: av.VideoFrame,
        reference_frame: av.VideoFrame,
        frame_index: int,
    ) -> None:
        input_frame.pts = frame_index
        input_frame.time_base = Fraction(1, 1)
        reference_frame.pts = frame_index
        reference_frame.time_base = Fraction(1, 1)

        try:
            self._input_buffer.push(input_frame)
            self._reference_buffer.push(reference_frame)
            self._sink.pull()
        except (ValueError, OSError, av.FFmpegError) as error:
            raise MetricError(
                "PSNR frame conversion failed: {}".format(error)
            ) from error

    def finish(self) -> float:
        try:
            self._input_buffer.push(None)
            self._reference_buffer.push(None)

            while True:
                try:
                    self._sink.pull()
                except (StopIteration, av.EOFError):
                    break
        except (ValueError, OSError, av.FFmpegError) as error:
            raise MetricError(
                "PSNR frame conversion failed: {}".format(error)
            ) from error
        finally:
            self._destroy_graph()
            logs = list(self._logs)
            self._end_capture()

        minimum = _minimum_from_logs(logs)

        if minimum is None:
            raise MetricError("PSNR filter did not report a result")

        return minimum

    def close(self) -> None:
        if self._closed:
            return

        self._destroy_graph()
        self._end_capture()

    def _destroy_graph(self) -> None:
        self._sink = None
        self._reference_buffer = None
        self._input_buffer = None
        self._graph = None
        gc.collect()

    def _end_capture(self) -> None:
        if self._closed:
            return

        self._closed = True

        try:
            if self._previous_log_level is not None:
                av.logging.set_level(self._previous_log_level)

            if self._previous_skip_repeated is not None:
                av.logging.set_skip_repeated(
                    self._previous_skip_repeated
                )
        finally:
            try:
                if self._capture is not None:
                    self._capture.__exit__(None, None, None)
            finally:
                if self._lock_acquired:
                    self._lock_acquired = False
                    PSNR_FILTER_LOCK.release()


class _SourceFrameReader:
    """Adapt a VideoSource frame iterator to the PSNR reader interface."""

    def __init__(self, source: VideoSource):
        self.source = source
        self.spec = None  # type: Optional[_FrameSpec]
        self._reader = None
        self._frames = None

    def open(self) -> _FrameSpec:
        reader_factory = getattr(self.source, "frame_reader", None)

        if reader_factory is None:
            metadata = self.source.metadata()
            self._frames = self.source.frames()
        else:
            self._reader = reader_factory()
            metadata = self._reader.open()
            self._frames = self._reader

        self.spec = _FrameSpec(
            visible_metadata = metadata,
            frame_metadata   = metadata,
        )
        return self.spec

    def __iter__(self) -> "_SourceFrameReader":
        return self

    def __next__(self) -> av.VideoFrame:
        return next(self._frames)

    def close(self) -> None:
        _close_frame_iterators(self._frames)

        if self._reader is not None:
            self._reader.close()


class _NativeRawFrameReader:
    """Decode representable stored raw geometry with FFmpeg rawvideo."""

    def __init__(
        self,
        source: VideoSource,
        stored_width: int,
        stored_height: int,
    ):
        self.source = source
        self.raw_format = RAW_FORMATS[source.descriptor.format or ""]
        self.stored_width = stored_width
        self.stored_height = stored_height
        self.spec = None  # type: Optional[_FrameSpec]
        self._container = None
        self._frames = None

    def open(self) -> _FrameSpec:
        visible_metadata = self.source.metadata()
        frame_metadata = VideoMetadata(
            width     = self.stored_width,
            height    = self.stored_height,
            framerate = visible_metadata.framerate,
            format    = self.raw_format.av_format,
        )
        crop = None

        if (
            self.stored_width != visible_metadata.width
            or self.stored_height != visible_metadata.height
        ):
            crop = (
                visible_metadata.width,
                visible_metadata.height,
            )

        options = {
            "video_size" : "{}x{}".format(
                self.stored_width,
                self.stored_height,
            ),
            "pixel_format" : self.raw_format.av_format,
        }

        if visible_metadata.framerate is not None:
            options["framerate"] = "{}/{}".format(
                visible_metadata.framerate.numerator,
                visible_metadata.framerate.denominator,
            )

        try:
            with av.logging.Capture(local = True):
                self._container = av.open(
                    str(self.source.descriptor.path),
                    mode    = "r",
                    format  = "rawvideo",
                    options = options,
                )
            stream = self._container.streams.video[0]
            self._frames = iter(self._container.decode(stream))
        except (OSError, av.FFmpegError) as error:
            self.close()
            raise MediaError(
                "Cannot read raw media '{}': {}".format(
                    self.source.descriptor.path,
                    error,
                )
            ) from error

        self.spec = _FrameSpec(
            visible_metadata = visible_metadata,
            frame_metadata   = frame_metadata,
            crop             = crop,
        )
        return self.spec

    def __iter__(self) -> "_NativeRawFrameReader":
        return self

    def __next__(self) -> av.VideoFrame:
        try:
            return next(self._frames)
        except StopIteration:
            raise
        except (OSError, av.FFmpegError) as error:
            raise MediaError(
                "Cannot read raw media '{}': {}".format(
                    self.source.descriptor.path,
                    error,
                )
            ) from error

    def close(self) -> None:
        _close_frame_iterators(self._frames)
        self._frames = None

        if self._container is not None:
            self._container.close()
            self._container = None


class _CopiedRawFrameReader:
    """Copy visible raw rows into AVFrames when storage is not pixel-aligned."""

    def __init__(self, source: VideoSource):
        self.source = source
        self.raw_format = RAW_FORMATS[source.descriptor.format or ""]
        self.layout = self.raw_format.layout(source.descriptor)
        self.spec = None  # type: Optional[_FrameSpec]
        self._file = None
        self._mapping = None
        self._copier = None
        self._frame_index = 0

    def open(self) -> _FrameSpec:
        metadata = self.source.metadata()

        try:
            self._file = self.source.descriptor.path.open("rb")
            self._mapping = mmap.mmap(
                self._file.fileno(),
                0,
                access = mmap.ACCESS_READ,
            )
            self._copier = RawFrameCopier(
                self._mapping,
                self.layout,
            )
        except (OSError, ValueError) as error:
            self.close()
            raise MediaError(
                "Cannot read raw media '{}': {}".format(
                    self.source.descriptor.path,
                    error,
                )
            ) from error

        self.spec = _FrameSpec(
            visible_metadata = metadata,
            frame_metadata   = metadata,
        )
        return self.spec

    def __iter__(self) -> "_CopiedRawFrameReader":
        return self

    def __next__(self) -> av.VideoFrame:
        frame_offset = self._frame_index * self.layout.frame_size

        if frame_offset >= len(self._mapping):
            raise StopIteration

        frame = av.VideoFrame(
            self.layout.width,
            self.layout.height,
            self.raw_format.av_format,
        )
        frame.pts = self._frame_index
        time_base = _time_base(
            self.spec.visible_metadata.framerate
        )

        if time_base is not None:
            frame.time_base = time_base

        self._copier.copy(self._frame_index, frame)

        self._frame_index += 1
        return frame

    def close(self) -> None:
        if self._copier is not None:
            self._copier.close()
            self._copier = None

        if self._mapping is not None:
            self._mapping.close()
            self._mapping = None

        if self._file is not None:
            self._file.close()
            self._file = None


def _create_frame_reader(
    source: VideoSource,
):
    if not source.is_raw:
        return _SourceFrameReader(source)

    storage = _native_raw_storage(source)

    if storage is None:
        return _CopiedRawFrameReader(source)

    return _NativeRawFrameReader(source, storage[0], storage[1])


def _native_raw_storage(
    source: VideoSource,
) -> Optional[Tuple[int, int]]:
    raw_format = RAW_FORMATS[source.descriptor.format or ""]
    layout = raw_format.layout(source.descriptor)

    if layout.stride % raw_format.bytes_per_pixel != 0:
        return None

    stored_width = layout.stride // raw_format.bytes_per_pixel
    stored_height = layout.sliceheight

    try:
        video_format = av.VideoFormat(raw_format.av_format)
        stored_bits = (
            stored_width
            * stored_height
            * video_format.bits_per_pixel
        )
    except (AttributeError, ValueError):
        return None

    if stored_bits % 8 != 0 or stored_bits // 8 != layout.frame_size:
        return None

    return stored_width, stored_height


class PsnrSession:
    """Consume input frames while comparing one reference stream exactly once."""

    def __init__(
        self,
        reference_source: VideoSource,
        frame_limit: Optional[int],
        input_is_raw: bool = False,
    ):
        self.reference_source = reference_source
        self.frame_limit = frame_limit
        self.input_is_raw = input_is_raw
        self._reference_reader = None
        self._reference_frames = None
        self._comparator = None
        self._compared_frames = 0
        self._started = False
        self._finished = False
        self._value = None  # type: Optional[float]
        self._error = None  # type: Optional[MetricError]

    @property
    def accepts_frames(self) -> bool:
        return (
            self._started
            and not self._finished
            and self._error is None
        )

    def start(self, input_metadata: VideoMetadata) -> None:
        if self._started:
            return

        self._started = True

        try:
            self._reference_reader = _create_frame_reader(
                self.reference_source
            )
            reference_spec = self._reference_reader.open()
            reference_metadata = reference_spec.visible_metadata

            _validate_metadata(
                input_metadata,
                reference_metadata,
                input_is_raw = self.input_is_raw,
                reference_is_raw = self.reference_source.is_raw,
            )
            target_format = comparison_format(
                self.reference_source,
                reference_metadata,
            )

            try:
                self._comparator = _NativeFrameComparator(
                    input_metadata,
                    reference_spec.frame_metadata,
                    target_format,
                    reference_crop = reference_spec.crop,
                )
            except _NativePsnrSetupError as error:
                raise _native_psnr_error(error) from error

            self._reference_frames = self._reference_reader
        except KeyboardInterrupt:
            self.close()
            raise
        except (MediaError, MetricError) as error:
            self._fail(_metric_error(error))

    def consume(self, input_frame: av.VideoFrame) -> None:
        if (
            self._error is not None
            or self._finished
            or (
                self.frame_limit is not None
                and self._compared_frames >= self.frame_limit
            )
        ):
            return

        try:
            reference_frame = next(self._reference_frames)
        except StopIteration:
            self._fail(_frame_count_error())
            return
        except KeyboardInterrupt:
            self.close()
            raise
        except MediaError as error:
            self._fail(_metric_error(error))
            return

        try:
            self._comparator.compare(
                input_frame,
                cast(av.VideoFrame, reference_frame),
                self._compared_frames,
            )
            self._compared_frames += 1
        except KeyboardInterrupt:
            self.close()
            raise
        except MetricError as error:
            self._fail(error)

    def finish(self) -> None:
        if self._finished:
            return

        self._finished = True

        if self._error is not None:
            self.close()
            return

        try:
            if (
                self.frame_limit is not None
                and self._compared_frames < self.frame_limit
            ):
                raise _frame_count_error()

            if self.frame_limit is None:
                try:
                    next(self._reference_frames)
                except StopIteration:
                    pass
                else:
                    raise _frame_count_error()

            if self._compared_frames == 0:
                raise MetricError("PSNR requires at least one decoded frame")

            self._value = self._comparator.finish()
        except KeyboardInterrupt:
            self.close()
            raise
        except (MediaError, MetricError) as error:
            self._fail(_metric_error(error))
        finally:
            _close_frame_iterators(self._reference_frames)

    def abort(self, error: Exception) -> None:
        if self._error is None:
            self._fail(_metric_error(error))

    def result(self) -> float:
        if not self._finished:
            raise MetricError("PSNR comparison did not finish")

        if self._error is not None:
            raise self._error

        if self._value is None:
            raise MetricError("PSNR comparison did not produce a result")

        return self._value

    def close(self) -> None:
        _close_frame_iterators(self._reference_frames)

        if self._reference_reader is not None:
            self._reference_reader.close()

        if self._comparator is not None:
            self._comparator.close()

    def _fail(self, error: MetricError) -> None:
        self._error = error
        self.close()


def calculate_psnr(
    input_source: VideoSource,
    reference_source: VideoSource,
    frame_limit: Optional[int],
) -> float:
    if input_source.is_raw:
        return _calculate_reader_psnr(
            input_source,
            reference_source,
            frame_limit,
        )

    input_metadata = input_source.metadata()
    session = PsnrSession(
        reference_source,
        frame_limit,
        input_is_raw = input_source.is_raw,
    )
    session.start(input_metadata)
    input_frames = input_source.frames()

    try:
        for frame_index, frame in enumerate(input_frames):
            session.consume(frame)

            if (
                frame_limit is not None
                and frame_index + 1 >= frame_limit
            ):
                break

        session.finish()
        return session.result()
    except KeyboardInterrupt:
        session.close()
        raise
    finally:
        _close_frame_iterators(input_frames)


def _calculate_reader_psnr(
    input_source: VideoSource,
    reference_source: VideoSource,
    frame_limit: Optional[int],
) -> float:
    if input_source.is_raw and reference_source.is_raw:
        _validate_metadata(
            input_source.metadata(),
            reference_source.metadata(),
            input_is_raw = True,
            reference_is_raw = True,
        )

    _validate_raw_frame_availability(
        input_source,
        reference_source,
        frame_limit,
    )
    input_reader = _create_frame_reader(input_source)
    reference_reader = _create_frame_reader(reference_source)
    comparator = None

    try:
        input_spec = input_reader.open()
        reference_spec = reference_reader.open()
        _validate_metadata(
            input_spec.visible_metadata,
            reference_spec.visible_metadata,
            input_is_raw = input_source.is_raw,
            reference_is_raw = reference_source.is_raw,
        )
        target_format = comparison_format(
            reference_source,
            reference_spec.visible_metadata,
        )

        try:
            comparator = _NativeFrameComparator(
                input_spec.frame_metadata,
                reference_spec.frame_metadata,
                target_format,
                input_crop     = input_spec.crop,
                reference_crop = reference_spec.crop,
            )
        except _NativePsnrSetupError as error:
            raise _native_psnr_error(error) from error

        compared_frames = _compare_reader_frames(
            input_reader,
            reference_reader,
            comparator,
            frame_limit,
        )

        if compared_frames == 0:
            raise MetricError("PSNR requires at least one decoded frame")

        return comparator.finish()
    except KeyboardInterrupt:
        raise
    except (MediaError, MetricError) as error:
        raise _metric_error(error)
    finally:
        input_reader.close()
        reference_reader.close()

        if comparator is not None:
            comparator.close()


def _compare_reader_frames(
    input_reader,
    reference_reader,
    comparator,
    frame_limit: Optional[int],
) -> int:
    compared_frames = 0

    while frame_limit is None or compared_frames < frame_limit:
        try:
            input_frame = next(input_reader)
        except StopIteration:
            if frame_limit is not None:
                raise _frame_count_error()

            try:
                next(reference_reader)
            except StopIteration:
                break

            raise _frame_count_error()

        try:
            reference_frame = next(reference_reader)
        except StopIteration:
            raise _frame_count_error()

        comparator.compare(
            input_frame,
            reference_frame,
            compared_frames,
        )
        compared_frames += 1

    return compared_frames


def _validate_raw_frame_availability(
    input_source: VideoSource,
    reference_source: VideoSource,
    frame_limit: Optional[int],
) -> None:
    input_count = _raw_frame_count(input_source)
    reference_count = _raw_frame_count(reference_source)

    if input_count == 0 or reference_count == 0:
        raise MetricError("PSNR requires at least one decoded frame")

    if frame_limit is not None:
        if (
            input_count is not None
            and input_count < frame_limit
            or reference_count is not None
            and reference_count < frame_limit
        ):
            raise _frame_count_error()
    elif (
        input_count is not None
        and reference_count is not None
        and input_count != reference_count
    ):
        raise _frame_count_error()


def _raw_frame_count(source: VideoSource) -> Optional[int]:
    if not source.is_raw:
        return None

    raw_format = RAW_FORMATS[source.descriptor.format or ""]
    frame_size = raw_format.frame_size(source.descriptor)
    return source.descriptor.path.stat().st_size // frame_size


def comparison_format(
    source: VideoSource,
    metadata: VideoMetadata,
) -> str:
    if source.is_raw:
        raw_format = RAW_FORMATS[source.descriptor.format or ""]
        return raw_format.comparison_format

    if metadata.format is None:
        raise MetricError("PSNR source format is unavailable")

    return RAW_COMPARISON_FORMATS.get(metadata.format, metadata.format)


def _validate_metadata(
    input_metadata: VideoMetadata,
    reference_metadata: VideoMetadata,
    input_is_raw: bool,
    reference_is_raw: bool,
) -> None:
    if (
        input_metadata.width != reference_metadata.width
        or input_metadata.height != reference_metadata.height
    ):
        raise MetricError(
            "PSNR requires input and reference resolutions to match"
        )

    if not input_is_raw and not reference_is_raw:
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


def _template_frame(metadata: VideoMetadata, format_name: str) -> av.VideoFrame:
    frame = av.VideoFrame(metadata.width, metadata.height, format_name)
    frame.pts = 0
    frame.time_base = Fraction(1, 1)
    return frame


def _time_base(framerate: Optional[Fraction]) -> Optional[Fraction]:
    if framerate is None:
        return None

    return Fraction(framerate.denominator, framerate.numerator)


def _minimum_from_logs(
    logs: Iterable[Tuple[int, str, str]],
) -> Optional[float]:
    values = []

    for _, component, message in logs:
        if component != "psnr":
            continue

        match = PSNR_MINIMUM_PATTERN.search(message)

        if match is not None:
            values.append(match.group("value"))

    if not values:
        return None

    if values[-1] == "inf":
        return INFINITE_PSNR_VALUE

    return round(float(values[-1]), PSNR_DECIMAL_PLACES)


def _frame_count_error() -> MetricError:
    return MetricError(
        "PSNR requires input and reference frame counts to match"
    )


def _metric_error(error: Exception) -> MetricError:
    if isinstance(error, MetricError):
        return error

    return MetricError(str(error))


def _native_psnr_error(error: Exception) -> MetricError:
    return MetricError(
        "Native FFmpeg PSNR is unavailable: {}".format(error)
    )


def _close_frame_iterators(*iterators) -> None:
    for iterator in iterators:
        close = getattr(iterator, "close", None)

        if close is None:
            continue

        try:
            close()
        except Exception:
            pass
