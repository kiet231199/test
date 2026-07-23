import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import av

from media_checker.checker import check
from media_checker.descriptors import load_descriptor
from media_checker.media import (
    EncodedVideoSource,
    VideoSource,
    _EncodedAnalysis,
    _FrameSummary,
    _PacketSummary,
    _codec_level,
    _crop_text,
    _field_order,
    _reference_frames,
    _summarize_frames,
)
from media_checker.models import (
    ENCODED_MEDIA_TYPE,
    RAW_MEDIA_TYPE,
    CheckRequest,
    MediaDescriptor,
    VideoMetadata,
)
from tests.helpers import encode_container_video, encode_elementary_video


class FakeAnalyzedSource(VideoSource):
    def __init__(self, descriptor, codec_name, analysis):
        super().__init__(descriptor)
        self.codec_name = codec_name
        self.encoded_analysis = analysis

    def metadata(self):
        return VideoMetadata(
            width      = 16,
            height     = 16,
            framerate  = None,
            format     = "yuv420p",
            codec_name = self.codec_name,
        )

    def analysis(self):
        return self.encoded_analysis

    def frames(self):
        return iter(())


class EncodedAliasTests(unittest.TestCase):
    def test_new_elementary_extensions_are_case_insensitive(self):
        cases = (
            ("sample.26L", "libx264", "h264"),
            ("sample.H264", "libx264", "h264"),
            ("sample.H265", "libx265", "hevc"),
        )

        for file_name, encoder, container_format in cases:
            with self.subTest(file_name = file_name):
                with tempfile.TemporaryDirectory() as folder:
                    root = Path(folder)
                    media_path = root / file_name
                    encode_elementary_video(
                        media_path,
                        encoder,
                        container_format,
                    )
                    descriptor_path = root / "input.yaml"
                    descriptor_path.write_text(
                        "input:\n  path: {}\n".format(file_name),
                        encoding = "utf-8",
                    )

                    descriptor = load_descriptor(descriptor_path).input
                    frames = list(EncodedVideoSource(descriptor).frames())
                    psnr = check(CheckRequest(
                        input     = descriptor,
                        reference = descriptor,
                        metrics   = ("psnr",),
                    ))

                    self.assertEqual(descriptor.extension, media_path.suffix.lower())
                    self.assertEqual(len(frames), 2)
                    self.assertEqual(psnr.status, "success")
                    self.assertEqual(psnr.metrics["psnr"].value, 1000.0)


class CodecTraceTests(unittest.TestCase):
    def test_codec_levels_use_matching_sps_values(self):
        self.assertEqual(
            _codec_level("h264", [
                {"level_idc" : 41},
                {"level_idc" : 41},
            ]),
            41,
        )
        self.assertEqual(
            _codec_level("h264", [{
                "level_idc"            : 11,
                "constraint_set3_flag" : 1,
            }]),
            9,
        )
        self.assertEqual(
            _codec_level("h264", [{
                "level_idc"            : 11,
                "constraint_set3_flag" : 0,
            }]),
            11,
        )
        self.assertEqual(
            _codec_level("hevc", [{"general_level_idc" : 123}]),
            123,
        )
        self.assertIsNone(_codec_level("h264", [{}]))
        self.assertIsNone(_codec_level("h264", [
            {"level_idc" : 40},
            {"level_idc" : 41},
        ]))

    def test_crop_handles_uncropped_conflicting_and_malformed_parameter_sets(self):
        uncropped = {
            "frame_cropping_flag" : 0,
            "frame_mbs_only_flag" : 1,
        }
        cropped = {
            "frame_cropping_flag"     : 1,
            "frame_mbs_only_flag"     : 1,
            "chroma_format_idc"       : 1,
            "pic_width_in_mbs_minus1" : 1,
            "pic_height_in_map_units_minus1" : 1,
            "frame_crop_left_offset"  : 0,
            "frame_crop_right_offset" : 1,
            "frame_crop_top_offset"   : 0,
            "frame_crop_bottom_offset" : 1,
        }

        self.assertEqual(_crop_text("h264", [uncropped]), "0:0:0:0")
        self.assertIsNone(_crop_text("h264", [uncropped, cropped]))
        self.assertIsNone(_crop_text("h264", [{
            "frame_cropping_flag" : 1,
            "frame_mbs_only_flag" : 1,
        }]))
        oversized = dict(cropped)
        oversized["frame_crop_right_offset"] = 100
        oversized["frame_crop_bottom_offset"] = 100
        self.assertIsNone(_crop_text("h264", [oversized]))

        hevc_oversized = {
            "conformance_window_flag"     : 1,
            "chroma_format_idc"           : 1,
            "pic_width_in_luma_samples"   : 16,
            "pic_height_in_luma_samples"  : 16,
            "conf_win_left_offset"        : 0,
            "conf_win_right_offset"       : 8,
            "conf_win_top_offset"         : 0,
            "conf_win_bottom_offset"      : 0,
        }
        self.assertIsNone(_crop_text("hevc", [hevc_oversized]))

    def test_reference_capacity_uses_codec_registry_field_patterns(self):
        self.assertEqual(
            _reference_frames("h264", [{"max_num_ref_frames" : 4}]),
            4,
        )
        self.assertEqual(
            _reference_frames("hevc", [{
                "sps_max_dec_pic_buffering_minus1[0]" : 2,
                "sps_max_dec_pic_buffering_minus1[1]" : 5,
            }]),
            5,
        )

    def test_field_sequences_preserve_order_but_mixed_frames_are_rejected(self):
        self.assertEqual(
            _field_order("hevc", [[1], [1], [2], [2]]),
            "interlace_tff",
        )
        self.assertEqual(
            _field_order("hevc", [[2], [2], [1], [1]]),
            "interlace_bff",
        )
        self.assertIsNone(_field_order("h264", [[1], [2]]))
        self.assertIsNone(_field_order("hevc", [[1], [10]]))
        self.assertIsNone(_field_order("hevc", [[1, 2], []]))


class EncodedMetricTests(unittest.TestCase):
    def test_each_encoded_request_opens_the_input_once(self):
        metric_requests = (
            ("width",),
            ("level",),
            ("bitrate",),
            ("gop",),
            ("scan_type",),
            (
                "width",
                "codec",
                "bitrate",
                "gop",
                "refframes",
                "frame_count",
                "scan_type",
                "crop",
            ),
        )

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sample.264"
            encode_elementary_video(
                path,
                "libx264",
                "h264",
                frame_count = 10,
                width       = 18,
                height      = 18,
            )
            descriptor = MediaDescriptor(
                path       = path,
                media_type = ENCODED_MEDIA_TYPE,
                extension  = ".264",
            )
            original_open = EncodedVideoSource._open

            for metrics in metric_requests:
                with self.subTest(metrics = metrics):
                    opened = []

                    def counting_open(source):
                        opened.append(source.descriptor.path)
                        return original_open(source)

                    with patch.object(
                        EncodedVideoSource,
                        "_open",
                        counting_open,
                    ):
                        check(CheckRequest(
                            input     = descriptor,
                            reference = None,
                            metrics   = metrics,
                        ))

                    self.assertEqual(opened, [path])

    def test_psnr_shares_one_open_for_each_encoded_source(self):
        metric_requests = (
            ("psnr",),
            (
                "width",
                "codec",
                "bitrate",
                "gop",
                "refframes",
                "frame_count",
                "scan_type",
                "crop",
                "psnr",
            ),
        )

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            input_path = root / "input.264"
            reference_path = root / "reference.264"
            encode_elementary_video(
                input_path,
                "libx264",
                "h264",
                frame_count = 10,
                width       = 18,
                height      = 18,
            )
            encode_elementary_video(
                reference_path,
                "libx264",
                "h264",
                frame_count = 10,
                width       = 18,
                height      = 18,
            )
            input_descriptor = MediaDescriptor(
                path       = input_path,
                media_type = ENCODED_MEDIA_TYPE,
                extension  = ".264",
            )
            reference_descriptor = MediaDescriptor(
                path       = reference_path,
                media_type = ENCODED_MEDIA_TYPE,
                extension  = ".264",
            )
            original_open = EncodedVideoSource._open

            for metrics in metric_requests:
                with self.subTest(metrics = metrics):
                    opened = []

                    def counting_open(source):
                        opened.append(source.descriptor.path)
                        return original_open(source)

                    with patch.object(
                        EncodedVideoSource,
                        "_open",
                        counting_open,
                    ):
                        result = check(CheckRequest(
                            input     = input_descriptor,
                            reference = reference_descriptor,
                            metrics   = metrics,
                        ))

                    self.assertEqual(result.status, "success")
                    self.assertEqual(opened.count(input_path), 1)
                    self.assertEqual(opened.count(reference_path), 1)

    def test_registry_exposes_all_new_metrics_with_expected_value_types(self):
        descriptor = MediaDescriptor(
            path       = Path("unused.265"),
            media_type = ENCODED_MEDIA_TYPE,
            extension  = ".265",
        )
        analysis = _EncodedAnalysis(
            bitrate            = 123456,
            gop                = 12,
            interval_intraframe = 12,
            pframes            = 3,
            bframes            = 8,
            refframes          = 4,
            frame_count        = 30,
            scan_type          = "progressive",
            crop               = "0:2:0:2",
        )
        source = FakeAnalyzedSource(descriptor, "hevc", analysis)
        metric_names = (
            "codec",
            "bitrate",
            "gop",
            "interval-intraframe",
            "pframes",
            "bframes",
            "refframes",
            "frame_count",
            "scan_type",
            "crop",
        )

        with patch(
            "media_checker.checker.create_video_source",
            return_value = source,
        ):
            result = check(CheckRequest(
                input     = descriptor,
                reference = None,
                metrics   = metric_names,
            ))

        self.assertEqual(result.status, "success")
        self.assertEqual(result.metrics["codec"].value, "h265")
        self.assertEqual(result.metrics["bitrate"].value, 123456)
        self.assertEqual(result.metrics["interval-intraframe"].value, 12)
        self.assertEqual(result.metrics["crop"].value, "0:2:0:2")

    def test_new_metrics_remain_unsupported_for_raw_input(self):
        descriptor = MediaDescriptor(
            path        = Path("unused.raw"),
            media_type  = RAW_MEDIA_TYPE,
            extension   = ".raw",
            width       = 4,
            height      = 2,
            format      = "GRAY8",
            frame_count = 1,
            stride      = 4,
            sliceheight = 2,
        )
        metrics = (
            "codec",
            "bitrate",
            "gop",
            "interval-intraframe",
            "pframes",
            "bframes",
            "refframes",
            "frame_count",
            "scan_type",
            "crop",
        )

        result = check(CheckRequest(
            input     = descriptor,
            reference = None,
            metrics   = metrics,
        ))

        self.assertEqual(result.status, "failed")
        self.assertTrue(all(
            metric.value == "Unsupported metrics"
            for metric in result.metrics.values()
        ))

    def test_real_h264_and_h265_streams_report_stream_analysis(self):
        cases = (
            ("sample.264", "libx264", "h264", "h264", "0:14:0:14"),
            ("sample.265", "libx265", "hevc", "h265", "0:6:0:6"),
        )

        for (
            file_name,
            encoder,
            container_format,
            expected_codec,
            expected_crop,
        ) in cases:
            with self.subTest(codec = expected_codec):
                with tempfile.TemporaryDirectory() as folder:
                    path = Path(folder) / file_name
                    encode_elementary_video(
                        path,
                        encoder,
                        container_format,
                        frame_count = 10,
                        width       = 18,
                        height      = 18,
                    )
                    descriptor = MediaDescriptor(
                        path       = path,
                        media_type = ENCODED_MEDIA_TYPE,
                        extension  = path.suffix,
                    )

                    result = check(CheckRequest(
                        input     = descriptor,
                        reference = None,
                        metrics   = (
                            "codec",
                            "bitrate",
                            "gop",
                            "refframes",
                            "frame_count",
                            "scan_type",
                            "crop",
                        ),
                    ))

                    self.assertEqual(result.status, "success")
                    self.assertEqual(result.metrics["codec"].value, expected_codec)
                    self.assertGreater(result.metrics["bitrate"].value, 0)
                    self.assertGreater(result.metrics["gop"].value, 0)
                    self.assertGreaterEqual(result.metrics["refframes"].value, 0)
                    self.assertEqual(result.metrics["frame_count"].value, 10)
                    self.assertEqual(result.metrics["scan_type"].value, "progressive")
                    self.assertEqual(result.metrics["crop"].value, expected_crop)

    def test_real_encoded_streams_report_sps_level(self):
        cases = (
            ("sample.264", "libx264", "h264"),
            ("sample.265", "libx265", "hevc"),
            ("sample-h264.mp4", "libx264", "mp4"),
            ("sample-h265.mp4", "libx265", "mp4"),
        )

        for file_name, encoder, container_format in cases:
            with self.subTest(file_name = file_name):
                with tempfile.TemporaryDirectory() as folder:
                    path = Path(folder) / file_name
                    encode_container_video(path, encoder, container_format)
                    descriptor = MediaDescriptor(
                        path       = path,
                        media_type = ENCODED_MEDIA_TYPE,
                        extension  = path.suffix,
                    )

                    result = check(CheckRequest(
                        input     = descriptor,
                        reference = None,
                        metrics   = ("level",),
                    ))

                    self.assertEqual(result.metrics["level"].value, "2.0")

    def test_frame_summary_uses_longest_observed_i_picture_groups(self):
        summary = _summarize_frames(
            ["P", "I", "P", "B", "I", "B", "I", "P", "P"],
            [False] * 9,
        )

        self.assertEqual(summary.frame_count, 9)
        self.assertEqual(summary.gop, 3)
        self.assertEqual(summary.interval_intraframe, 3)
        self.assertEqual(summary.pframes, 1)
        self.assertEqual(summary.bframes, 1)
        self.assertEqual(summary.scan_mode, "progressive")

    def test_h264_picture_timing_distinguishes_field_order(self):
        cases = (
            ("tff", "interlace_tff"),
            ("bff", "interlace_bff"),
        )

        for x264_order, expected in cases:
            with self.subTest(order = x264_order):
                with tempfile.TemporaryDirectory() as folder:
                    path = Path(folder) / "sample.264"
                    encode_elementary_video(
                        path,
                        "libx264",
                        "h264",
                        frame_count = 4,
                        width       = 64,
                        height      = 32,
                        options     = {
                            "x264-params" : (
                                "interlaced=1:{}=1:keyint=20:"
                                "min-keyint=20:scenecut=0"
                            ).format(x264_order),
                        },
                        interlaced = True,
                    )
                    descriptor = MediaDescriptor(
                        path       = path,
                        media_type = ENCODED_MEDIA_TYPE,
                        extension  = ".264",
                    )

                    result = check(CheckRequest(
                        input     = descriptor,
                        reference = None,
                        metrics   = ("scan_type",),
                    ))

                    self.assertEqual(result.metrics["scan_type"].value, expected)

    def test_h265_picture_timing_distinguishes_field_order(self):
        cases = (
            ("tff", "interlace_tff"),
            ("bff", "interlace_bff"),
        )

        for x265_order, expected in cases:
            with self.subTest(order = x265_order):
                with tempfile.TemporaryDirectory() as folder:
                    path = Path(folder) / "sample.265"
                    encode_elementary_video(
                        path,
                        "libx265",
                        "hevc",
                        frame_count = 4,
                        width       = 64,
                        height      = 32,
                        options     = {
                            "x265-params" : (
                                "interlace={}:keyint=20:min-keyint=20:scenecut=0"
                            ).format(x265_order),
                        },
                        interlaced = True,
                    )
                    descriptor = MediaDescriptor(
                        path       = path,
                        media_type = ENCODED_MEDIA_TYPE,
                        extension  = ".265",
                    )

                    result = check(CheckRequest(
                        input     = descriptor,
                        reference = None,
                        metrics   = ("scan_type",),
                    ))

                    self.assertEqual(result.metrics["scan_type"].value, expected)

    def test_interlaced_scan_requires_field_order_for_every_frame(self):
        descriptor = MediaDescriptor(
            path       = Path("unused.264"),
            media_type = ENCODED_MEDIA_TYPE,
            extension  = ".264",
        )
        source = EncodedVideoSource(descriptor)

        with patch.object(
            source,
            "_packet_summary",
            return_value = _PacketSummary(
                field_order       = "interlace_tff",
                field_order_count = 1,
            ),
        ), patch.object(
            source,
            "_frame_summary",
            return_value = _FrameSummary(
                frame_count = 2,
                scan_mode   = "interlaced",
            ),
        ):
            analysis = source.analysis()

        self.assertIsNone(analysis.scan_type)

    def test_header_probe_restores_the_process_log_level(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sample.264"
            encode_elementary_video(path, "libx264", "h264", frame_count = 2)
            descriptor = MediaDescriptor(
                path       = path,
                media_type = ENCODED_MEDIA_TYPE,
                extension  = ".264",
            )
            previous_level = av.logging.get_level()

            try:
                av.logging.set_level(av.logging.WARNING)
                EncodedVideoSource(descriptor).analysis()
                self.assertEqual(av.logging.get_level(), av.logging.WARNING)
            finally:
                av.logging.set_level(previous_level)

    def test_short_stream_keeps_gop_when_complete_interval_is_unavailable(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sample.264"
            encode_elementary_video(path, "libx264", "h264", frame_count = 2)
            descriptor = MediaDescriptor(
                path       = path,
                media_type = ENCODED_MEDIA_TYPE,
                extension  = ".264",
            )

            result = check(CheckRequest(
                input     = descriptor,
                reference = None,
                metrics   = (
                    "gop",
                    "interval-intraframe",
                    "pframes",
                    "bframes",
                ),
            ))

            self.assertEqual(result.status, "partial")
            self.assertEqual(result.metrics["gop"].value, 2)
            self.assertEqual(
                result.metrics["interval-intraframe"].status,
                "error",
            )
            self.assertEqual(result.metrics["pframes"].status, "error")
            self.assertEqual(result.metrics["bframes"].status, "error")

    def test_mp4_audio_does_not_change_selected_video_bitrate(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            values = []

            for include_audio in (False, True):
                path = root / "sample-{}.mp4".format(include_audio)
                encode_container_video(
                    path,
                    "libx264",
                    "mp4",
                    frame_count   = 10,
                    include_audio = include_audio,
                )
                descriptor = MediaDescriptor(
                    path       = path,
                    media_type = ENCODED_MEDIA_TYPE,
                    extension  = ".mp4",
                )
                result = check(CheckRequest(
                    input     = descriptor,
                    reference = None,
                    metrics   = ("bitrate",),
                ))
                values.append(result.metrics["bitrate"].value)

            self.assertEqual(values[0], values[1])

    def test_header_probe_failure_does_not_erase_frame_metrics(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sample.264"
            encode_elementary_video(path, "libx264", "h264", frame_count = 2)
            descriptor = MediaDescriptor(
                path       = path,
                media_type = ENCODED_MEDIA_TYPE,
                extension  = ".264",
            )

            with patch(
                "media_checker.media.av.BitStreamFilterContext",
                side_effect = ValueError("unavailable"),
            ):
                result = check(CheckRequest(
                    input     = descriptor,
                    reference = None,
                    metrics   = ("frame_count", "level", "crop", "refframes"),
                ))

            self.assertEqual(result.status, "partial")
            self.assertEqual(result.metrics["frame_count"].value, 2)
            self.assertEqual(result.metrics["level"].status, "error")
            self.assertEqual(result.metrics["crop"].status, "error")
            self.assertEqual(result.metrics["refframes"].status, "error")


if __name__ == "__main__":
    unittest.main()
