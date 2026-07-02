from fractions import Fraction
from pathlib import Path
from typing import Iterable, List

import av
import numpy as np

from media_checker.media import RAW_FORMATS
from media_checker.models import MediaDescriptor, RAW_MEDIA_TYPE


def raw_descriptor(
    path: Path,
    raw_format: str = "NV12",
    width: int = 4,
    height: int = 2,
    framerate: Fraction = Fraction(24, 1),
    frame_count: int = 1,
    stride: int = 4,
    sliceheight: int = 2,
) -> MediaDescriptor:
    return MediaDescriptor(
        path        = path,
        media_type  = RAW_MEDIA_TYPE,
        extension   = path.suffix.lower(),
        width       = width,
        height      = height,
        framerate   = framerate,
        format      = raw_format,
        frame_count = frame_count,
        stride      = stride,
        sliceheight = sliceheight,
    )


def write_raw_frames(
    descriptor: MediaDescriptor,
    frames: Iterable[bytes],
) -> None:
    raw_format = RAW_FORMATS[descriptor.format or ""]
    frame_size = raw_format.frame_size(descriptor)
    data        = bytearray()

    for frame in frames:
        if len(frame) != frame_size:
            raise ValueError(
                "Raw frame is {} bytes; expected {}".format(len(frame), frame_size)
            )
        data.extend(frame)

    descriptor.path.write_bytes(bytes(data))


def padded_nv12_frame(
    descriptor: MediaDescriptor,
    visible_y: List[bytes],
    visible_uv: List[bytes],
    padding: int,
) -> bytes:
    raw_format = RAW_FORMATS["NV12"]
    frame       = bytearray([padding] * raw_format.frame_size(descriptor))
    stride      = descriptor.stride or 0
    sliceheight = descriptor.sliceheight or 0
    width       = descriptor.width or 0

    for row, values in enumerate(visible_y):
        frame[row * stride:row * stride + width] = values

    chroma_offset = stride * sliceheight
    for row, values in enumerate(visible_uv):
        start = chroma_offset + row * stride
        frame[start:start + width] = values

    return bytes(frame)


def encode_elementary_video(
    path: Path,
    codec_name: str,
    container_format: str,
    rate: Fraction = Fraction(24, 1),
    frame_count: int = 2,
) -> None:
    width  = 16
    height = 16

    with av.open(str(path), mode = "w", format = container_format) as container:
        stream         = container.add_stream(codec_name, rate = rate)
        stream.width   = width
        stream.height  = height
        stream.pix_fmt = "yuv420p"

        if codec_name == "libx265":
            stream.options = {"x265-params" : "log-level=error:pools=1"}

        for frame_index in range(frame_count):
            pixels = np.full(
                (height, width, 3),
                frame_index * 24,
                dtype = np.uint8,
            )
            frame     = av.VideoFrame.from_ndarray(pixels, format = "rgb24")
            frame.pts = frame_index

            for packet in stream.encode(frame):
                container.mux(packet)

        for packet in stream.encode():
            container.mux(packet)
