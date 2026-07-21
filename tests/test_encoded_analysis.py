import tempfile
import unittest
from pathlib import Path

from media_checker.checker import check
from media_checker.descriptors import load_descriptor
from media_checker.media import EncodedVideoSource
from media_checker.models import CheckRequest
from tests.helpers import encode_elementary_video


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


if __name__ == "__main__":
    unittest.main()
