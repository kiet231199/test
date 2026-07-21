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
    _crop_text,
    _field_order,
    _reference_frames,
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


if __name__ == "__main__":
    unittest.main()
