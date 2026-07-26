import threading
from typing import Any

from media_checker.errors import MediaError
from media_checker.native_runtime import load_numpy


_NUMPY = None
_NUMPY_LOCK = threading.Lock()


class RawFrameCopier:
    """Reuse mapped source views while copying exceptional raw storage."""

    def __init__(
        self,
        mapping: Any,
        layout: Any,
    ):
        self._numpy = _load_numpy()
        self._sources = tuple(
            self._numpy.ndarray(
                shape = (
                    len(mapping) // layout.frame_size,
                    source_plane.visible_rows,
                    source_plane.visible_row_bytes,
                ),
                dtype = self._numpy.uint8,
                buffer = mapping,
                offset = source_plane.offset,
                strides = (
                    layout.frame_size,
                    source_plane.stride,
                    1,
                ),
            )
            for source_plane in layout.planes
        )

    def copy(self, frame_index: int, frame: Any) -> None:
        for source, destination_plane in zip(
            self._sources,
            frame.planes,
        ):
            visible_rows = source.shape[1]
            visible_row_bytes = source.shape[2]

            if destination_plane.line_size < visible_row_bytes:
                raise MediaError(
                    "PyAV plane stride is smaller than the visible raw row"
                )

            destination = self._numpy.ndarray(
                shape = (visible_rows, visible_row_bytes),
                dtype = self._numpy.uint8,
                buffer = destination_plane,
                strides = (destination_plane.line_size, 1),
            )
            self._numpy.copyto(destination, source[frame_index])

    def close(self) -> None:
        self._sources = ()


def _load_numpy():
    global _NUMPY

    if _NUMPY is not None:
        return _NUMPY

    with _NUMPY_LOCK:
        if _NUMPY is None:
            _NUMPY = load_numpy()

    return _NUMPY
