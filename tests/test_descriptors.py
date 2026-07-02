import tempfile
import unittest
from pathlib import Path

from media_checker.descriptors import load_descriptor
from media_checker.errors import ConfigurationError
from media_checker.media import RAW_FORMATS


class DescriptorTests(unittest.TestCase):
    def test_load_raw_descriptor_resolves_relative_path(self):
        with tempfile.TemporaryDirectory() as folder:
            root       = Path(folder)
            media_dir  = root / "media"
            media_dir.mkdir()
            media_path = media_dir / "input.YUV"
            media_path.write_bytes(bytes(12))
            descriptor_path = root / "input.yaml"
            descriptor_path.write_text(
                "\n".join([
                    "path: media/input.YUV",
                    "width: 4",
                    "height: 2",
                    'framerate: "24/1"',
                    "format: nv12",
                    "frame_count: 1",
                    "stride: 4",
                    "sliceheight: 2",
                ]),
                encoding = "utf-8",
            )

            descriptor = load_descriptor(descriptor_path)

            self.assertEqual(descriptor.path, media_path.resolve())
            self.assertEqual(descriptor.extension, ".yuv")
            self.assertEqual(descriptor.format, "NV12")
            self.assertEqual(str(descriptor.framerate), "24")

    def test_encoded_descriptor_ignores_recognized_raw_fields(self):
        with tempfile.TemporaryDirectory() as folder:
            root       = Path(folder)
            media_path = root / "output.265"
            media_path.write_bytes(b"encoded")
            descriptor_path = root / "output.yaml"
            descriptor_path.write_text(
                "\n".join([
                    "path: output.265",
                    "width: 1920",
                    "stride: 2048",
                ]),
                encoding = "utf-8",
            )

            descriptor = load_descriptor(descriptor_path)

            self.assertFalse(descriptor.is_raw)
            self.assertIsNone(descriptor.width)
            self.assertIsNone(descriptor.stride)

    def test_all_raw_formats_validate_their_exact_file_size(self):
        for name, raw_format in RAW_FORMATS.items():
            with self.subTest(raw_format = name):
                with tempfile.TemporaryDirectory() as folder:
                    root = Path(folder)
                    width = 4
                    height = 2
                    stride = width * raw_format.bytes_per_pixel
                    media_path = root / "input.raw"

                    if raw_format.has_chroma_plane:
                        size = stride * height * 3 // 2
                    else:
                        size = stride * height

                    media_path.write_bytes(bytes(size))
                    descriptor_path = root / "input.yaml"
                    descriptor_path.write_text(
                        "\n".join([
                            "path: input.raw",
                            "width: {}".format(width),
                            "height: {}".format(height),
                            'framerate: "24/1"',
                            "format: {}".format(name),
                            "frame_count: 1",
                            "stride: {}".format(stride),
                            "sliceheight: {}".format(height),
                        ]),
                        encoding = "utf-8",
                    )

                    descriptor = load_descriptor(descriptor_path)
                    self.assertEqual(descriptor.format, name)

    def test_invalid_descriptors_report_configuration_errors(self):
        cases = {
            "unknown field" : "unknown: value",
            "missing raw field" : "",
            "noncanonical rate" : 'framerate: "48/2"',
            "short stride" : "stride: 2",
            "short sliceheight" : "sliceheight: 1",
        }

        for name, replacement in cases.items():
            with self.subTest(case = name):
                with tempfile.TemporaryDirectory() as folder:
                    root = Path(folder)
                    (root / "input.yuv").write_bytes(bytes(12))
                    lines = [
                        "path: input.yuv",
                        "width: 4",
                        "height: 2",
                        'framerate: "24/1"',
                        "format: NV12",
                        "frame_count: 1",
                        "stride: 4",
                        "sliceheight: 2",
                    ]

                    if name == "missing raw field":
                        lines.remove("frame_count: 1")
                    elif name == "unknown field":
                        lines.append(replacement)
                    else:
                        field_name = replacement.split(":", 1)[0]
                        lines = [
                            replacement if line.startswith(field_name + ":") else line
                            for line in lines
                        ]

                    descriptor_path = root / "input.yaml"
                    descriptor_path.write_text("\n".join(lines), encoding = "utf-8")

                    with self.assertRaises(ConfigurationError):
                        load_descriptor(descriptor_path)

    def test_raw_file_size_must_match_frame_count(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "input.yuv").write_bytes(bytes(11))
            descriptor_path = root / "input.yaml"
            descriptor_path.write_text(
                "\n".join([
                    "path: input.yuv",
                    "width: 4",
                    "height: 2",
                    'framerate: "24/1"',
                    "format: NV12",
                    "frame_count: 1",
                    "stride: 4",
                    "sliceheight: 2",
                ]),
                encoding = "utf-8",
            )

            with self.assertRaisesRegex(ConfigurationError, "file size"):
                load_descriptor(descriptor_path)


if __name__ == "__main__":
    unittest.main()
