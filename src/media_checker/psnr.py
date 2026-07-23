import gc
import math
import mmap
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
from typing import Iterable, Optional, Tuple, cast

import av
import numpy as np

from media_checker.errors import MediaError, MetricError
from media_checker.media import RAW_FORMATS, RawLayout, VideoSource
from media_checker.models import VideoMetadata


INFINITE_PSNR_VALUE = 1000.0
PSNR_DECIMAL_PLACES = 6
MAX_RAW_WORKERS = 8
RAW_ACTIVE_MEMORY_BUDGET = 256 * 1024 * 1024

PSNR_FILTER_LOCK = threading.RLock()
PSNR_MINIMUM_PATTERN = re.compile(
    r"\bmin:(?P<value>inf|[0-9]+(?:\.[0-9]+)?)\b"
)

RAW_COMPARISON_FORMATS = {
    raw_format.av_format : raw_format.comparison_format
    for raw_format in RAW_FORMATS.values()
}

_YUV420_FORMATS = frozenset(("I420", "NV12"))
_YUV422_FORMATS = frozenset(("YUY2", "UYVY", "YVYU"))
_RGB_FORMATS = frozenset((
    "RGB",
    "BGR",
    "RGB16",
    "ARGB",
    "RGBA",
    "ABGR",
    "BGRA",
))


class _NativePsnrSetupError(Exception):
    pass


class _NativeFrameComparator:
    """Own one native PSNR graph and its process-wide logging capture."""

    def __init__(
        self,
        input_metadata: VideoMetadata,
        reference_metadata: VideoMetadata,
        target_format: str,
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
            input_buffer = graph.add_buffer(template = input_template)
            reference_buffer = graph.add_buffer(template = reference_template)
            input_format_filter = graph.add(
                "format",
                args = "pix_fmts={}".format(target_format),
            )
            reference_format_filter = graph.add(
                "format",
                args = "pix_fmts={}".format(target_format),
            )
            psnr_filter = graph.add("psnr")
            sink = graph.add("buffersink")

            input_buffer.link_to(input_format_filter)
            input_format_filter.link_to(psnr_filter, 0, 0)
            reference_buffer.link_to(reference_format_filter)
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
        except (ValueError, OSError, av.FFmpegError) as error:
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


class _NumpyFrameComparator:
    """Exact compatibility path used when native graph setup is unavailable."""

    def __init__(self, target_format: str):
        self.target_format = target_format
        self.minimum = math.inf

    def compare(
        self,
        input_frame: av.VideoFrame,
        reference_frame: av.VideoFrame,
        frame_index: int,
    ) -> None:
        del frame_index
        self.minimum = min(
            self.minimum,
            frame_psnr(input_frame, reference_frame, self.target_format),
        )

    def finish(self) -> float:
        return _normalize_minimum(self.minimum)

    def close(self) -> None:
        pass


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
            reader_factory = getattr(
                self.reference_source,
                "frame_reader",
                None,
            )

            if reader_factory is None:
                reference_metadata = self.reference_source.metadata()
            else:
                self._reference_reader = reader_factory()
                reference_metadata = self._reference_reader.open()
                self._reference_frames = self._reference_reader

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
                    reference_metadata,
                    target_format,
                )
            except _NativePsnrSetupError:
                self._comparator = _NumpyFrameComparator(target_format)

            if self._reference_frames is None:
                self._reference_frames = self.reference_source.frames()
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
    input_metadata = input_source.metadata()

    if _can_compare_raw_direct(input_source, reference_source):
        reference_metadata = reference_source.metadata()
        _validate_metadata(
            input_metadata,
            reference_metadata,
            input_is_raw = True,
            reference_is_raw = True,
        )
        return _compare_raw_direct(
            input_source,
            reference_source,
            frame_limit,
        )

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


def frame_psnr(
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

    difference = np.subtract(
        input_array,
        reference_array,
        dtype = np.int16,
    )
    squared_error = np.square(difference, dtype = np.int64)
    mean_squared_error = float(
        squared_error.sum(dtype = np.int64)
    ) / input_array.size

    if mean_squared_error == 0:
        return math.inf

    peak = _sample_peak(target_format, input_array.dtype.itemsize)
    return 10.0 * math.log10((peak * peak) / mean_squared_error)


def _compare_raw_direct(
    input_source: VideoSource,
    reference_source: VideoSource,
    frame_limit: Optional[int],
) -> float:
    input_format = RAW_FORMATS[input_source.descriptor.format or ""]
    reference_format = RAW_FORMATS[reference_source.descriptor.format or ""]
    input_layout = input_format.layout(input_source.descriptor)
    reference_layout = reference_format.layout(reference_source.descriptor)
    input_count = (
        input_source.descriptor.path.stat().st_size // input_layout.frame_size
    )
    reference_count = (
        reference_source.descriptor.path.stat().st_size
        // reference_layout.frame_size
    )

    if frame_limit is None:
        if input_count != reference_count:
            raise _frame_count_error()

        compared_frames = input_count
    else:
        if input_count < frame_limit or reference_count < frame_limit:
            raise _frame_count_error()

        compared_frames = frame_limit

    if compared_frames == 0:
        raise MetricError("PSNR requires at least one decoded frame")

    target_format = reference_format.comparison_format
    sample_count = _logical_sample_count(
        reference_layout,
        reference_format.name,
        target_format,
    )
    workers = _raw_worker_count(compared_frames, sample_count)

    try:
        with input_source.descriptor.path.open("rb") as input_file:
            with reference_source.descriptor.path.open("rb") as reference_file:
                with mmap.mmap(
                    input_file.fileno(),
                    0,
                    access = mmap.ACCESS_READ,
                ) as input_map:
                    with mmap.mmap(
                        reference_file.fileno(),
                        0,
                        access = mmap.ACCESS_READ,
                    ) as reference_map:

                        def compare_frame(frame_index: int) -> float:
                            input_array = _logical_raw_frame(
                                input_map,
                                input_layout,
                                input_format.name,
                                target_format,
                                frame_index,
                            )
                            reference_array = _logical_raw_frame(
                                reference_map,
                                reference_layout,
                                reference_format.name,
                                target_format,
                                frame_index,
                            )

                            if input_array.shape != reference_array.shape:
                                raise MetricError(
                                    "PSNR converted frame shapes do not match"
                                )

                            difference = np.subtract(
                                input_array,
                                reference_array,
                                dtype = np.int16,
                            )
                            squared_error = np.square(
                                difference,
                                dtype = np.int64,
                            )
                            error_sum = int(
                                squared_error.sum(dtype = np.int64)
                            )

                            if error_sum == 0:
                                return math.inf

                            mean_squared_error = (
                                error_sum / input_array.size
                            )
                            return 10.0 * math.log10(
                                (255.0 * 255.0) / mean_squared_error
                            )

                        with ThreadPoolExecutor(
                            max_workers = workers,
                        ) as executor:
                            minimum = min(executor.map(
                                compare_frame,
                                range(compared_frames),
                            ))
    except MetricError:
        raise
    except OSError as error:
        raise MetricError(
            "Cannot read raw media: {}".format(error)
        ) from error

    return _normalize_minimum(minimum)


def _logical_raw_frame(
    data,
    layout: RawLayout,
    format_name: str,
    target_format: str,
    frame_index: int,
) -> np.ndarray:
    frame_offset = frame_index * layout.frame_size
    planes = [
        _visible_plane(data, frame_offset, plane)
        for plane in layout.planes
    ]

    if format_name == "I420" or format_name == "I444":
        return np.concatenate([plane.reshape(-1) for plane in planes])

    if format_name == "NV12":
        chroma = planes[1].reshape(-1, 2)
        return np.concatenate((
            planes[0].reshape(-1),
            chroma[:, 0],
            chroma[:, 1],
        ))

    packed = planes[0].reshape(-1)

    if format_name == "YUY2" or format_name == "GRAY8":
        return packed

    if format_name == "UYVY":
        return packed.reshape(-1, 4)[:, (1, 0, 3, 2)].reshape(-1)

    if format_name == "YVYU":
        return packed.reshape(-1, 4)[:, (0, 3, 2, 1)].reshape(-1)

    rgb = _raw_rgb(packed, format_name)

    if target_format == "rgb24":
        return rgb.reshape(-1)

    if target_format == "rgba":
        pixel_count = rgb.size // 3
        rgba = np.empty((pixel_count, 4), dtype = np.uint8)
        rgba[:, :3] = rgb.reshape(-1, 3)
        rgba[:, 3] = _raw_alpha(packed, format_name, pixel_count)
        return rgba.reshape(-1)

    raise MetricError(
        "PSNR direct raw conversion does not support '{}'".format(
            target_format
        )
    )


def _visible_plane(data, frame_offset: int, plane) -> np.ndarray:
    return np.ndarray(
        shape = (plane.visible_rows, plane.visible_row_bytes),
        dtype = np.uint8,
        buffer = data,
        offset = frame_offset + plane.offset,
        strides = (plane.stride, 1),
    )


def _raw_rgb(packed: np.ndarray, format_name: str) -> np.ndarray:
    if format_name == "RGB":
        return packed.reshape(-1, 3)

    if format_name == "BGR":
        return packed.reshape(-1, 3)[:, (2, 1, 0)]

    if format_name == "RGB16":
        values = np.ascontiguousarray(packed).view("<u2")
        red = (values >> 11) & 31
        green = (values >> 5) & 63
        blue = values & 31
        return np.stack((
            (red << 3) | (red >> 2),
            (green << 2) | (green >> 4),
            (blue << 3) | (blue >> 2),
        ), axis = 1).astype(np.uint8)

    pixels = packed.reshape(-1, 4)
    orders = {
        "ARGB" : (1, 2, 3),
        "RGBA" : (0, 1, 2),
        "ABGR" : (3, 2, 1),
        "BGRA" : (2, 1, 0),
    }
    return pixels[:, orders[format_name]]


def _raw_alpha(
    packed: np.ndarray,
    format_name: str,
    pixel_count: int,
) -> np.ndarray:
    alpha_indices = {
        "ARGB" : 0,
        "RGBA" : 3,
        "ABGR" : 0,
        "BGRA" : 3,
    }
    alpha_index = alpha_indices.get(format_name)

    if alpha_index is None:
        return np.full(pixel_count, 255, dtype = np.uint8)

    return packed.reshape(-1, 4)[:, alpha_index]


def _can_compare_raw_direct(
    input_source: VideoSource,
    reference_source: VideoSource,
) -> bool:
    if not input_source.is_raw or not reference_source.is_raw:
        return False

    input_name = input_source.descriptor.format or ""
    reference_name = reference_source.descriptor.format or ""

    return any(
        input_name in family and reference_name in family
        for family in (
            _YUV420_FORMATS,
            _YUV422_FORMATS,
            frozenset(("I444",)),
            frozenset(("GRAY8",)),
            _RGB_FORMATS,
        )
    )


def _logical_sample_count(
    layout: RawLayout,
    format_name: str,
    target_format: str,
) -> int:
    if format_name in _RGB_FORMATS:
        channels = 4 if target_format == "rgba" else 3
        return layout.width * layout.height * channels

    return layout.visible_sample_count


def _raw_worker_count(frame_count: int, sample_count: int) -> int:
    cpu_count = os.cpu_count() or 1
    active_bytes_per_frame = max(sample_count * 12, 1)
    budget_workers = max(
        RAW_ACTIVE_MEMORY_BUDGET // active_bytes_per_frame,
        1,
    )
    return max(min(
        frame_count,
        cpu_count,
        MAX_RAW_WORKERS,
        budget_workers,
    ), 1)


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


def _normalize_minimum(minimum: float) -> float:
    if math.isinf(minimum):
        return INFINITE_PSNR_VALUE

    return round(minimum, PSNR_DECIMAL_PLACES)


def _sample_peak(format_name: str, item_size: int) -> int:
    try:
        video_format = av.VideoFormat(format_name)
        components = getattr(video_format, "components", ())
        bit_depth = max(component.bits for component in components)
    except (AttributeError, ValueError):
        bit_depth = item_size * 8

    return (1 << bit_depth) - 1


def _frame_count_error() -> MetricError:
    return MetricError(
        "PSNR requires input and reference frame counts to match"
    )


def _metric_error(error: Exception) -> MetricError:
    if isinstance(error, MetricError):
        return error

    return MetricError(str(error))


def _close_frame_iterators(*iterators) -> None:
    for iterator in iterators:
        close = getattr(iterator, "close", None)

        if close is None:
            continue

        try:
            close()
        except Exception:
            pass
