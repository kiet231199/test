import math
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path
from unittest.mock import patch

from media_checker.checker import check
from media_checker.errors import MediaError
from media_checker.media import EncodedVideoSource, RAW_FORMATS, VideoSource
from media_checker.models import (
    ENCODED_MEDIA_TYPE,
    STATUS_ERROR,
    STATUS_FAILED,
    STATUS_PARTIAL,
    STATUS_SUCCESS,
    CheckRequest,
    MediaDescriptor,
    VideoMetadata,
)

from tests.helpers import (
    encode_container_video,
    encode_elementary_video,
    padded_nv12_frame,
    raw_descriptor,
    write_raw_frames,
)


class RawMetricTests(unittest.TestCase):
    def test_yuy2_calculates_psnr_with_pinned_pyav(self):
        with tempfile.TemporaryDirectory() as folder:
            raw_format = RAW_FORMATS["YUY2"]
            descriptor = raw_descriptor(
                Path(folder) / "input.raw",
                raw_format = raw_format.name,
                stride = 4 * raw_format.bytes_per_pixel,
            )
            write_raw_frames(
                descriptor,
                [bytes(raw_format.frame_size(descriptor))],
            )

            result = check(CheckRequest(
                input     = descriptor,
                reference = descriptor,
                metrics   = ("psnr",),
            ))

            self.assertEqual(result.metrics["psnr"].value, 1000.0)

    def test_all_raw_formats_calculate_identical_psnr(self):
        for name, raw_format in RAW_FORMATS.items():
            with self.subTest(raw_format = name):
                with tempfile.TemporaryDirectory() as folder:
                    path       = Path(folder) / "input.raw"
                    stride     = 4 * raw_format.bytes_per_pixel
                    descriptor = raw_descriptor(
                        path,
                        raw_format = name,
                        stride     = stride,
                    )
                    frame_size = raw_format.frame_size(descriptor)
                    write_raw_frames(descriptor, [bytes(frame_size)])

                    result = check(CheckRequest(
                        input     = descriptor,
                        reference = descriptor,
                        metrics   = ("psnr",),
                    ))

                    self.assertEqual(result.metrics["psnr"].value, 1000.0)

    def test_padding_is_cropped_before_psnr(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            input_descriptor = raw_descriptor(
                root / "input.yuv",
                stride      = 8,
                sliceheight = 4,
            )
            reference_descriptor = raw_descriptor(
                root / "reference.yuv",
                stride      = 8,
                sliceheight = 4,
            )
            visible_y  = [bytes((10, 20, 30, 40)), bytes((50, 60, 70, 80))]
            visible_uv = [bytes((90, 100, 110, 120))]

            write_raw_frames(input_descriptor, [
                padded_nv12_frame(
                    input_descriptor,
                    visible_y,
                    visible_uv,
                    padding = 1,
                )
            ])
            write_raw_frames(reference_descriptor, [
                padded_nv12_frame(
                    reference_descriptor,
                    visible_y,
                    visible_uv,
                    padding = 255,
                )
            ])

            result = check(CheckRequest(
                input     = input_descriptor,
                reference = reference_descriptor,
                metrics   = ("psnr",),
            ))

            self.assertEqual(result.status, STATUS_SUCCESS)
            self.assertEqual(result.metrics["psnr"].value, 1000.0)

    def test_optional_padding_dimensions_are_applied_independently(self):
        cases = (
            (
                "stride",
                {"stride" : 6, "sliceheight" : None},
                bytes((1, 2, 3, 4, 99, 99, 5, 6, 7, 8, 99, 99)),
            ),
            (
                "sliceheight",
                {"stride" : None, "sliceheight" : 4},
                bytes((1, 2, 3, 4, 5, 6, 7, 8) + (99,) * 8),
            ),
        )

        for name, storage, input_data in cases:
            with self.subTest(padding = name):
                with tempfile.TemporaryDirectory() as folder:
                    root = Path(folder)
                    input_descriptor = raw_descriptor(
                        root / "input.raw",
                        "GRAY8",
                        stride      = storage["stride"],
                        sliceheight = storage["sliceheight"],
                    )
                    reference_descriptor = raw_descriptor(
                        root / "reference.raw",
                        "GRAY8",
                        stride      = None,
                        sliceheight = None,
                    )
                    input_descriptor.path.write_bytes(input_data)
                    reference_descriptor.path.write_bytes(
                        bytes((1, 2, 3, 4, 5, 6, 7, 8))
                    )

                    result = check(CheckRequest(
                        input     = input_descriptor,
                        reference = reference_descriptor,
                        metrics   = ("psnr",),
                    ))

                    self.assertEqual(result.metrics["psnr"].value, 1000.0)

    def test_psnr_returns_six_decimal_minimum(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            input_descriptor = raw_descriptor(
                root / "input.raw",
                raw_format = "GRAY8",
                stride = 4,
            )
            reference_descriptor = raw_descriptor(
                root / "reference.raw",
                raw_format = "GRAY8",
                stride = 4,
            )
            write_raw_frames(input_descriptor, [bytes((0, 0, 0, 0, 0, 0, 0, 0))])
            write_raw_frames(reference_descriptor, [bytes((1, 1, 1, 1, 1, 1, 1, 1))])

            result = check(CheckRequest(
                input     = input_descriptor,
                reference = reference_descriptor,
                metrics   = ("psnr",),
            ))

            expected = round(10.0 * math.log10(255.0 ** 2), 6)
            self.assertEqual(result.metrics["psnr"].value, expected)

    def test_missing_reference_is_a_metric_error(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            descriptor = raw_descriptor(root / "input.raw", "GRAY8", stride = 4)
            write_raw_frames(descriptor, [bytes(8)])

            result = check(CheckRequest(
                input     = descriptor,
                reference = None,
                metrics   = ("psnr",),
            ))

            self.assertEqual(result.status, STATUS_FAILED)
            self.assertEqual(result.metrics["psnr"].status, STATUS_ERROR)
            self.assertIn("reference", result.metrics["psnr"].value)
            self.assertNotIn("code", result.metrics["psnr"].to_dict())

    def test_raw_input_rejects_non_psnr_metrics_independently(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            descriptor = raw_descriptor(root / "input.raw", "GRAY8", stride = 4)
            write_raw_frames(descriptor, [bytes(8)])

            result = check(CheckRequest(
                input     = descriptor,
                reference = descriptor,
                metrics   = ("width", "psnr"),
            ))

            self.assertEqual(result.status, STATUS_PARTIAL)
            self.assertEqual(result.metrics["width"].status, STATUS_ERROR)
            self.assertEqual(result.metrics["width"].value, "Unsupported metrics")
            self.assertEqual(result.metrics["psnr"].value, 1000.0)

    def test_raw_level_and_profile_are_metric_errors(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            descriptor = raw_descriptor(root / "input.raw", "GRAY8", stride = 4)
            write_raw_frames(descriptor, [bytes(8)])

            result = check(CheckRequest(
                input     = descriptor,
                reference = None,
                metrics   = ("level", "profile"),
            ))

            self.assertEqual(result.status, STATUS_FAILED)
            self.assertEqual(result.metrics["level"].status, STATUS_ERROR)
            self.assertEqual(result.metrics["profile"].status, STATUS_ERROR)
            self.assertEqual(result.metrics["level"].value, "Unsupported metrics")
            self.assertEqual(result.metrics["profile"].value, "Unsupported metrics")

    def test_psnr_rejects_frame_count_and_resolution_mismatches(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            input_descriptor = raw_descriptor(
                root / "input.raw",
                "GRAY8",
                frame_count = 2,
                stride = 4,
            )
            short_reference = raw_descriptor(
                root / "short.raw",
                "GRAY8",
                frame_count = 1,
                stride = 4,
            )
            wide_reference = raw_descriptor(
                root / "wide.raw",
                "GRAY8",
                width  = 6,
                stride = 6,
            )
            write_raw_frames(input_descriptor, [bytes(8), bytes(8)])
            write_raw_frames(short_reference, [bytes(8)])
            write_raw_frames(wide_reference, [bytes(12)])

            frame_result = check(CheckRequest(
                input     = input_descriptor,
                reference = short_reference,
                metrics   = ("psnr",),
            ))
            size_result = check(CheckRequest(
                input     = input_descriptor,
                reference = wide_reference,
                metrics   = ("psnr",),
            ))

            self.assertIn("frame counts", frame_result.metrics["psnr"].value)
            self.assertIn("resolutions", size_result.metrics["psnr"].value)

    def test_raw_framerate_mismatch_does_not_block_psnr(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            input_descriptor = raw_descriptor(
                root / "input.raw",
                "GRAY8",
                framerate = Fraction(24, 1),
                stride = 4,
            )
            reference_descriptor = raw_descriptor(
                root / "reference.raw",
                "GRAY8",
                framerate = Fraction(30, 1),
                stride = 4,
            )
            write_raw_frames(input_descriptor, [bytes(8)])
            write_raw_frames(reference_descriptor, [bytes(8)])

            result = check(CheckRequest(
                input     = input_descriptor,
                reference = reference_descriptor,
                metrics   = ("psnr",),
            ))

            self.assertEqual(result.metrics["psnr"].value, 1000.0)

    def test_missing_raw_frame_count_compares_every_complete_frame(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            input_descriptor = raw_descriptor(
                root / "input.raw",
                "GRAY8",
                framerate   = None,
                frame_count = None,
                stride      = None,
                sliceheight = None,
            )
            reference_descriptor = raw_descriptor(
                root / "reference.raw",
                "GRAY8",
                framerate   = None,
                frame_count = None,
                stride      = None,
                sliceheight = None,
            )
            write_raw_frames(input_descriptor, [bytes(8), bytes([20] * 8)])
            write_raw_frames(reference_descriptor, [bytes(8), bytes([20] * 8)])

            result = check(CheckRequest(
                input     = input_descriptor,
                reference = reference_descriptor,
                metrics   = ("psnr",),
            ))

            self.assertEqual(result.metrics["psnr"].value, 1000.0)

            reference_descriptor.path.write_bytes(bytes(8))
            mismatch = check(CheckRequest(
                input     = input_descriptor,
                reference = reference_descriptor,
                metrics   = ("psnr",),
            ))

            self.assertIn("frame counts", mismatch.metrics["psnr"].value)

    def test_input_frame_count_limits_psnr_to_the_requested_prefix(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            input_descriptor = raw_descriptor(
                root / "input.raw",
                "GRAY8",
                frame_count = 1,
                stride = None,
                sliceheight = None,
            )
            reference_descriptor = raw_descriptor(
                root / "reference.raw",
                "GRAY8",
                frame_count = None,
                stride = None,
                sliceheight = None,
            )
            write_raw_frames(input_descriptor, [bytes(8), bytes([10] * 8)])
            write_raw_frames(reference_descriptor, [bytes(8), bytes([200] * 8)])

            result = check(CheckRequest(
                input     = input_descriptor,
                reference = reference_descriptor,
                metrics   = ("psnr",),
            ))

            self.assertEqual(result.metrics["psnr"].value, 1000.0)


class EncodedMediaTests(unittest.TestCase):
    def test_h264_and_h265_elementary_streams_decode(self):
        cases = (
            ("sample.264", "libx264", "h264"),
            ("sample.265", "libx265", "hevc"),
        )

        for file_name, codec_name, container_format in cases:
            with self.subTest(file_name = file_name):
                with tempfile.TemporaryDirectory() as folder:
                    path = Path(folder) / file_name
                    encode_elementary_video(path, codec_name, container_format)
                    descriptor = MediaDescriptor(
                        path       = path,
                        media_type = ENCODED_MEDIA_TYPE,
                        extension  = path.suffix,
                    )
                    source   = EncodedVideoSource(descriptor)
                    metadata = source.metadata()
                    frames   = list(source.frames())

                    self.assertEqual(metadata.width, 16)
                    self.assertEqual(metadata.height, 16)
                    self.assertEqual(len(frames), 2)
                    self.assertIsNotNone(metadata.profile)

                    result = check(CheckRequest(
                        input     = descriptor,
                        reference = None,
                        metrics   = ("width", "height", "framerate", "profile"),
                    ))
                    self.assertEqual(result.status, STATUS_SUCCESS)
                    self.assertEqual(result.metrics["width"].value, 16)
                    self.assertEqual(result.metrics["height"].value, 16)
                    self.assertEqual(result.metrics["framerate"].value, "24/1")
                    self.assertIsInstance(result.metrics["profile"].value, str)

    def test_encoded_framerates_use_displayed_frame_cadence(self):
        media_cases = (
            ("sample.264", "libx264", "h264"),
            ("sample.265", "libx265", "hevc"),
            ("sample-h264.mp4", "libx264", "mp4"),
            ("sample-h265.mp4", "libx265", "mp4"),
        )
        rate_cases = (
            (Fraction(24, 1), "24/1"),
            (Fraction(30000, 1001), "30000/1001"),
        )

        for file_name, encoder_name, container_format in media_cases:
            for rate, expected in rate_cases:
                with self.subTest(file_name = file_name, rate = rate):
                    with tempfile.TemporaryDirectory() as folder:
                        path = Path(folder) / file_name
                        encode_container_video(
                            path,
                            encoder_name,
                            container_format,
                            rate = rate,
                        )
                        descriptor = MediaDescriptor(
                            path       = path,
                            media_type = ENCODED_MEDIA_TYPE,
                            extension  = path.suffix,
                        )

                        result = check(CheckRequest(
                            input     = descriptor,
                            reference = None,
                            metrics   = ("framerate",),
                        ))

                        self.assertEqual(
                            result.metrics["framerate"].value,
                            expected,
                        )

    def test_mp4_h264_and_h265_video_decode_while_audio_is_ignored(self):
        cases = (
            ("libx264", "h264"),
            ("libx265", "hevc"),
        )

        for encoder_name, expected_codec in cases:
            with self.subTest(codec = expected_codec):
                with tempfile.TemporaryDirectory() as folder:
                    path = Path(folder) / "sample.mp4"
                    encode_container_video(
                        path,
                        encoder_name,
                        "mp4",
                        include_audio = True,
                    )
                    descriptor = MediaDescriptor(
                        path       = path,
                        media_type = ENCODED_MEDIA_TYPE,
                        extension  = ".mp4",
                    )
                    source   = EncodedVideoSource(descriptor)
                    metadata = source.metadata()
                    frames   = list(source.frames())

                    self.assertEqual(metadata.codec_name, expected_codec)
                    self.assertEqual(metadata.width, 16)
                    self.assertEqual(metadata.height, 16)
                    self.assertEqual(len(frames), 2)

    def test_mp4_without_h264_or_h265_video_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "unsupported.mp4"
            encode_container_video(path, "mpeg4", "mp4", include_audio = True)
            descriptor = MediaDescriptor(
                path       = path,
                media_type = ENCODED_MEDIA_TYPE,
                extension  = ".mp4",
            )

            with self.assertRaisesRegex(MediaError, "H.264 or H.265"):
                EncodedVideoSource(descriptor).metadata()

    def test_encoded_psnr_supports_equal_and_different_framerates(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first_path  = root / "first.264"
            second_path = root / "second.264"
            encode_elementary_video(first_path, "libx264", "h264")
            encode_elementary_video(
                second_path,
                "libx264",
                "h264",
                rate = Fraction(30, 1),
            )
            first = MediaDescriptor(
                path       = first_path,
                media_type = ENCODED_MEDIA_TYPE,
                extension  = ".264",
            )
            second = MediaDescriptor(
                path       = second_path,
                media_type = ENCODED_MEDIA_TYPE,
                extension  = ".264",
            )

            equal_result = check(CheckRequest(
                input     = first,
                reference = first,
                metrics   = ("psnr",),
            ))
            rate_result = check(CheckRequest(
                input     = first,
                reference = second,
                metrics   = ("psnr",),
            ))

            self.assertEqual(equal_result.metrics["psnr"].value, 1000.0)
            self.assertIn("framerates", rate_result.metrics["psnr"].value)

    def test_encoded_input_can_compare_with_raw_reference(self):
        with tempfile.TemporaryDirectory() as folder:
            root         = Path(folder)
            encoded_path = root / "input.264"
            raw_path     = root / "reference.raw"
            encode_elementary_video(encoded_path, "libx264", "h264")
            encoded = MediaDescriptor(
                path       = encoded_path,
                media_type = ENCODED_MEDIA_TYPE,
                extension  = ".264",
            )
            reference = raw_descriptor(
                raw_path,
                raw_format = "RGB",
                width       = 16,
                height      = 16,
                frame_count = 2,
                stride      = 48,
                sliceheight = 16,
            )
            first_frame  = bytes(16 * 16 * 3)
            second_frame = bytes([24] * (16 * 16 * 3))
            write_raw_frames(reference, [first_frame, second_frame])

            result = check(CheckRequest(
                input     = encoded,
                reference = reference,
                metrics   = ("psnr",),
            ))

            self.assertEqual(result.status, STATUS_SUCCESS)
            self.assertIsInstance(result.metrics["psnr"].value, float)

    def test_codec_levels_are_normalized_for_people(self):
        descriptor = MediaDescriptor(
            path       = Path("unused.264"),
            media_type = ENCODED_MEDIA_TYPE,
            extension  = ".264",
        )
        cases = (
            ("h264", 41, "4.1"),
            ("h264", 9, "1b"),
            ("hevc", 123, "4.1"),
        )

        for codec_name, level, expected in cases:
            with self.subTest(codec_name = codec_name, level = level):
                source = FakeEncodedSource(descriptor, codec_name, level)

                with patch(
                    "media_checker.checker.create_video_source",
                    return_value = source,
                ):
                    result = check(CheckRequest(
                        input     = descriptor,
                        reference = None,
                        metrics   = ("level",),
                    ))

                self.assertEqual(result.metrics["level"].value, expected)


class FakeEncodedSource(VideoSource):
    def __init__(
        self,
        descriptor: MediaDescriptor,
        codec_name: str,
        level: int,
    ):
        super().__init__(descriptor)
        self.codec_name = codec_name
        self.level      = level

    def metadata(self) -> VideoMetadata:
        return VideoMetadata(
            width      = 16,
            height     = 16,
            framerate  = Fraction(24, 1),
            format     = "yuv420p",
            profile    = "Main",
            level      = self.level,
            codec_name = self.codec_name,
        )

    def frames(self):
        return iter(())


if __name__ == "__main__":
    unittest.main()
