from fractions import Fraction
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import av
import numpy as np

from media_checker.media import RAW_FORMATS
from media_checker.models import MediaDescriptor, RAW_MEDIA_TYPE


def raw_descriptor(
    path: Path,
    raw_format: str = "NV12",
    width: int = 4,
    height: int = 2,
    framerate: Optional[Fraction] = Fraction(24, 1),
    frame_count: Optional[int] = 1,
    stride: Optional[int] = 4,
    sliceheight: Optional[int] = 2,
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
    width: int = 16,
    height: int = 16,
    options: Optional[Dict[str, str]] = None,
    interlaced: bool = False,
) -> None:
    encode_container_video(
        path,
        codec_name,
        container_format,
        rate,
        frame_count,
        width = width,
        height = height,
        options = options,
        interlaced = interlaced,
    )


def encode_container_video(
    path: Path,
    codec_name: str,
    container_format: str,
    rate: Fraction = Fraction(24, 1),
    frame_count: int = 2,
    include_audio: bool = False,
    width: int = 16,
    height: int = 16,
    options: Optional[Dict[str, str]] = None,
    interlaced: bool = False,
) -> None:
    with av.open(str(path), mode = "w", format = container_format) as container:
        stream         = container.add_stream(codec_name, rate = rate)
        stream.width   = width
        stream.height  = height
        stream.pix_fmt = "yuv420p"

        stream_options = dict(options or {})

        if codec_name == "libx265":
            x265_parameters = stream_options.get("x265-params", "")
            x265_defaults = "log-level=error:pools=1"
            stream_options["x265-params"] = ":".join(filter(None, (
                x265_defaults,
                x265_parameters,
            )))

        if stream_options:
            stream.options = stream_options

        if interlaced:
            stream.codec_context.interlaced_dct = True
            stream.codec_context.interlaced_me  = True

        audio_stream = None

        if include_audio:
            audio_stream = container.add_stream("aac", rate = 48000)
            audio_stream.layout = "mono"

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

        if audio_stream is not None:
            for audio_index in range(2):
                samples = np.zeros((1, 1024), dtype = np.float32)
                audio_frame = av.AudioFrame.from_ndarray(
                    samples,
                    format = "fltp",
                    layout = "mono",
                )
                audio_frame.pts         = audio_index * 1024
                audio_frame.sample_rate = 48000
                audio_frame.time_base   = Fraction(1, 48000)

                for packet in audio_stream.encode(audio_frame):
                    container.mux(packet)

            for packet in audio_stream.encode():
                container.mux(packet)
