import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from media_checker.descriptors import load_descriptor, load_input
from media_checker.errors import ConfigurationError
from media_checker.media import RAW_FORMATS


def _raw_lines(path: str = "input.yuv"):
    return [
        "  path: {}".format(path),
        "  width: 4",
        "  height: 2",
        '  framerate: "24/1"',
        "  format: NV12",
        "  frame_count: 1",
        "  stride: 4",
        "  sliceheight: 2",
    ]


def _minimal_raw_lines(
    path: str = "input.yuv",
    raw_format: str = "NV12",
):
    return [
        "  path: {}".format(path),
        "  width: 4",
        "  height: 2",
        "  format: {}".format(raw_format),
    ]


class DescriptorTests(unittest.TestCase):
    def test_direct_encoded_input_builds_an_input_only_descriptor(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)

            for extension in (".264", ".26L", ".H264", ".265", ".H265", ".MP4"):
                with self.subTest(extension = extension):
                    media_path = root / ("input" + extension)
                    media_path.write_bytes(b"encoded")

                    descriptors = load_input(media_path)

                    self.assertEqual(descriptors.input.path, media_path.resolve())
                    self.assertEqual(
                        descriptors.input.extension,
                        extension.lower(),
                    )
                    self.assertFalse(descriptors.input.is_raw)
                    self.assertIsNone(descriptors.reference)

    def test_direct_raw_input_requires_a_descriptor(self):
        with tempfile.TemporaryDirectory() as folder:
            raw_path = Path(folder) / "input.YUV"
            raw_path.write_bytes(bytes(12))

            with self.assertRaisesRegex(
                ConfigurationError,
                "Raw media input requires a descriptor",
            ):
                load_input(raw_path)

    def test_direct_encoded_input_resolves_from_the_working_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            media_path = root / "input.264"
            media_path.write_bytes(b"encoded")
            previous_directory = Path.cwd()

            try:
                os.chdir(root)
                descriptors = load_input(Path("input.264"))
            finally:
                os.chdir(previous_directory)

            self.assertEqual(descriptors.input.path, media_path.resolve())

    def test_missing_direct_encoded_input_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            missing_path = Path(folder) / "missing.H264"

            with self.assertRaisesRegex(
                ConfigurationError,
                "Media file does not exist",
            ):
                load_input(missing_path)

    def test_non_media_input_suffixes_remain_descriptor_compatible(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            media_path = root / "input.264"
            media_path.write_bytes(b"encoded")

            for name in ("input.yaml", "input.json", "input.txt", "input.yml", "input"):
                with self.subTest(name = name):
                    descriptor_path = root / name
                    descriptor_path.write_text(
                        "input:\n  path: input.264\n",
                        encoding = "utf-8",
                    )

                    descriptors = load_input(descriptor_path)

                    self.assertEqual(descriptors.input.path, media_path.resolve())

    def test_load_raw_descriptor_resolves_relative_path(self):
        with tempfile.TemporaryDirectory() as folder:
            root       = Path(folder)
            media_dir  = root / "media"
            media_dir.mkdir()
            media_path = media_dir / "input.YUV"
            media_path.write_bytes(bytes(12))
            descriptor_path = root / "input.yaml"
            descriptor_path.write_text(
                "\n".join(["input:"] + _raw_lines("media/input.YUV")),
                encoding = "utf-8",
            )

            descriptor_set = load_descriptor(descriptor_path)
            descriptor     = descriptor_set.input

            self.assertEqual(descriptor.path, media_path.resolve())
            self.assertEqual(descriptor.extension, ".yuv")
            self.assertEqual(descriptor.format, "NV12")
            self.assertEqual(str(descriptor.framerate), "24")
            self.assertIsNone(descriptor_set.reference)

    def test_minimal_raw_descriptor_infers_tight_storage(self):
        for name, raw_format in RAW_FORMATS.items():
            with self.subTest(raw_format = name):
                with tempfile.TemporaryDirectory() as folder:
                    root       = Path(folder)
                    media_path = root / "input.raw"
                    luma_size  = 4 * raw_format.bytes_per_pixel * 2
                    frame_size = (
                        luma_size
                        * raw_format.stored_height_numerator
                        // raw_format.stored_height_denominator
                    )
                    media_path.write_bytes(bytes(frame_size * 2))
                    descriptor_path = root / "input.yaml"
                    descriptor_path.write_text(
                        "\n".join([
                            "input:",
                        ] + _minimal_raw_lines("input.raw", name)),
                        encoding = "utf-8",
                    )

                    descriptor = load_descriptor(descriptor_path).input

                    self.assertIsNone(descriptor.framerate)
                    self.assertIsNone(descriptor.frame_count)
                    self.assertIsNone(descriptor.stride)
                    self.assertIsNone(descriptor.sliceheight)

    def test_old_flat_descriptor_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "input.yuv").write_bytes(bytes(12))
            descriptor_path = root / "input.yaml"
            descriptor_path.write_text(
                "\n".join(line.strip() for line in _raw_lines()),
                encoding = "utf-8",
            )

            with self.assertRaisesRegex(ConfigurationError, "'input'"):
                load_descriptor(descriptor_path)

    def test_encoded_descriptor_ignores_raw_and_unknown_fields(self):
        with tempfile.TemporaryDirectory() as folder:
            root       = Path(folder)
            media_path = root / "output.265"
            media_path.write_bytes(b"encoded")
            descriptor_path = root / "output.yaml"
            descriptor_path.write_text(
                "\n".join([
                    "ignored_root: ${MISSING}",
                    "input:",
                    "  path: output.265",
                    "  width: ${MISSING}",
                    "  stride: invalid",
                    "  ignored_media: ${MISSING}",
                ]),
                encoding = "utf-8",
            )

            descriptor = load_descriptor(descriptor_path).input

            self.assertFalse(descriptor.is_raw)
            self.assertIsNone(descriptor.width)
            self.assertIsNone(descriptor.stride)

    def test_mp4_descriptor_is_supported_case_insensitively(self):
        with tempfile.TemporaryDirectory() as folder:
            root       = Path(folder)
            media_path = root / "output.MP4"
            media_path.write_bytes(b"container")
            descriptor_path = root / "output.yaml"
            descriptor_path.write_text(
                "\n".join([
                    "input:",
                    "  path: output.MP4",
                ]),
                encoding = "utf-8",
            )

            descriptor = load_descriptor(descriptor_path).input

            self.assertFalse(descriptor.is_raw)
            self.assertEqual(descriptor.extension, ".mp4")

    def test_reference_is_loaded_and_null_is_treated_as_absent(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "input.264").write_bytes(b"input")
            (root / "reference.265").write_bytes(b"reference")
            pair_path = root / "pair.yaml"
            pair_path.write_text(
                "\n".join([
                    "input:",
                    "  path: input.264",
                    "reference:",
                    "  path: reference.265",
                ]),
                encoding = "utf-8",
            )
            single_path = root / "single.yaml"
            single_path.write_text(
                "\n".join([
                    "input:",
                    "  path: input.264",
                    "reference: null",
                ]),
                encoding = "utf-8",
            )

            pair   = load_descriptor(pair_path)
            single = load_descriptor(single_path)

            self.assertEqual(pair.input.path, (root / "input.264").resolve())
            self.assertEqual(
                pair.reference.path if pair.reference else None,
                (root / "reference.265").resolve(),
            )
            self.assertIsNone(single.reference)

    def test_invalid_reference_is_rejected_even_when_it_may_be_unused(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "input.264").write_bytes(b"input")
            descriptor_path = root / "input.yaml"
            descriptor_path.write_text(
                "\n".join([
                    "input:",
                    "  path: input.264",
                    "reference:",
                    "  path: missing.264",
                ]),
                encoding = "utf-8",
            )

            with self.assertRaisesRegex(ConfigurationError, "does not exist"):
                load_descriptor(descriptor_path)

    def test_all_raw_formats_validate_their_exact_file_size(self):
        for name, raw_format in RAW_FORMATS.items():
            with self.subTest(raw_format = name):
                with tempfile.TemporaryDirectory() as folder:
                    root   = Path(folder)
                    width  = 4
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
                            "input:",
                            "  path: input.raw",
                            "  width: {}".format(width),
                            "  height: {}".format(height),
                            '  framerate: "24/1"',
                            "  format: {}".format(name),
                            "  frame_count: 1",
                            "  stride: {}".format(stride),
                            "  sliceheight: {}".format(height),
                        ]),
                        encoding = "utf-8",
                    )

                    descriptor = load_descriptor(descriptor_path).input
                    self.assertEqual(descriptor.format, name)

    def test_invalid_descriptors_report_configuration_errors(self):
        cases = {
            "missing raw field" : None,
            "noncanonical rate" : '  framerate: "48/2"',
            "null rate" : "  framerate: null",
            "null frame count" : "  frame_count: null",
            "null stride" : "  stride: null",
            "null sliceheight" : "  sliceheight: null",
            "short stride" : "  stride: 2",
            "short sliceheight" : "  sliceheight: 1",
        }

        for name, replacement in cases.items():
            with self.subTest(case = name):
                with tempfile.TemporaryDirectory() as folder:
                    root = Path(folder)
                    (root / "input.yuv").write_bytes(bytes(12))
                    lines = ["input:"] + _raw_lines()

                    if name == "missing raw field":
                        lines.remove("  width: 4")
                    else:
                        if replacement is None:
                            raise AssertionError("Replacement is required for this case")
                        field_name = replacement.strip().split(":", 1)[0]
                        lines = [
                            replacement if line.strip().startswith(field_name + ":") else line
                            for line in lines
                        ]

                    descriptor_path = root / "input.yaml"
                    descriptor_path.write_text("\n".join(lines), encoding = "utf-8")

                    with self.assertRaises(ConfigurationError):
                        load_descriptor(descriptor_path)

    def test_explicit_frame_count_allows_additional_complete_frames(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "input.yuv").write_bytes(bytes(24))
            descriptor_path = root / "input.yaml"
            descriptor_path.write_text(
                "\n".join(["input:"] + _raw_lines()),
                encoding = "utf-8",
            )

            descriptor = load_descriptor(descriptor_path).input

            self.assertEqual(descriptor.frame_count, 1)

    def test_raw_file_must_contain_declared_complete_frames(self):
        cases = (
            (12, 2),
            (13, 1),
        )

        for file_size, frame_count in cases:
            with self.subTest(file_size = file_size, frame_count = frame_count):
                with tempfile.TemporaryDirectory() as folder:
                    root = Path(folder)
                    (root / "input.yuv").write_bytes(bytes(file_size))
                    lines = ["input:"] + _raw_lines()
                    lines = [
                        "  frame_count: {}".format(frame_count)
                        if line == "  frame_count: 1"
                        else line
                        for line in lines
                    ]
                    descriptor_path = root / "input.yaml"
                    descriptor_path.write_text("\n".join(lines), encoding = "utf-8")

                    with self.assertRaisesRegex(ConfigurationError, "file size"):
                        load_descriptor(descriptor_path)

    def test_raw_file_size_must_contain_only_complete_frames(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "input.yuv").write_bytes(bytes(11))
            descriptor_path = root / "input.yaml"
            descriptor_path.write_text(
                "\n".join(["input:"] + _raw_lines()),
                encoding = "utf-8",
            )

            with self.assertRaisesRegex(ConfigurationError, "file size"):
                load_descriptor(descriptor_path)


class EnvironmentDescriptorTests(unittest.TestCase):
    def test_descriptor_values_override_system_values(self):
        with tempfile.TemporaryDirectory() as folder:
            root       = Path(folder)
            system_dir = root / "system"
            user_dir   = root / "user"
            system_dir.mkdir()
            user_dir.mkdir()
            (user_dir / "input.264").write_bytes(b"encoded")
            descriptor_path = root / "input.yaml"
            descriptor_path.write_text(
                "\n".join([
                    "env:",
                    "  MEDIA_DIR: user",
                    "input:",
                    "  path: ${MEDIA_DIR}/input.264",
                ]),
                encoding = "utf-8",
            )

            with patch.dict(os.environ, {"MEDIA_DIR" : str(system_dir)}, clear = False):
                descriptor = load_descriptor(descriptor_path).input
                self.assertEqual(os.environ["MEDIA_DIR"], str(system_dir))

            self.assertEqual(descriptor.path, (user_dir / "input.264").resolve())

    def test_env_values_expand_from_system_not_sibling_values(self):
        with tempfile.TemporaryDirectory() as folder:
            root       = Path(folder)
            system_dir = root / "system"
            system_dir.mkdir()
            (system_dir / "input.264").write_bytes(b"encoded")
            descriptor_path = root / "input.yaml"
            descriptor_path.write_text(
                "\n".join([
                    "env:",
                    "  ROOT: user",
                    "  INPUT_FILE: ${ROOT}/input.264",
                    "input:",
                    "  path: ${INPUT_FILE}",
                ]),
                encoding = "utf-8",
            )

            with patch.dict(os.environ, {"ROOT" : str(system_dir)}, clear = False):
                descriptor = load_descriptor(descriptor_path).input

            self.assertEqual(descriptor.path, (system_dir / "input.264").resolve())

    def test_system_text_is_converted_for_integer_fields(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "input.yuv").write_bytes(bytes(12))
            descriptor_path = root / "input.yaml"
            descriptor_path.write_text(
                "\n".join([
                    "env:",
                    "  COUNT: ${SYSTEM_COUNT}",
                    "input:",
                    "  path: input.yuv",
                    "  width: 4",
                    "  height: 2",
                    '  framerate: "24/1"',
                    "  format: NV12",
                    "  frame_count: ${COUNT}",
                    "  stride: 4",
                    "  sliceheight: 2",
                ]),
                encoding = "utf-8",
            )

            with patch.dict(os.environ, {"SYSTEM_COUNT" : "1"}, clear = False):
                descriptor = load_descriptor(descriptor_path).input

            self.assertEqual(descriptor.frame_count, 1)
            self.assertIsInstance(descriptor.frame_count, int)

    def test_expansion_is_not_recursive(self):
        with tempfile.TemporaryDirectory() as folder:
            root          = Path(folder)
            literal_root  = root / "${HOME}" / "media"
            literal_root.mkdir(parents = True)
            media_path = literal_root / "input.264"
            media_path.write_bytes(b"encoded")
            descriptor_path = root / "input.yaml"
            descriptor_path.write_text(
                "\n".join([
                    "env:",
                    "  INPUT_DIR: ${ROOT}",
                    "input:",
                    "  path: ${INPUT_DIR}/input.264",
                ]),
                encoding = "utf-8",
            )

            system_values = {
                "ROOT" : "${HOME}/media",
                "HOME" : "expanded-home",
            }
            with patch.dict(os.environ, system_values, clear = False):
                descriptor = load_descriptor(descriptor_path).input

            self.assertEqual(descriptor.path, media_path.resolve())

    def test_scalar_env_value_keeps_its_type_for_full_replacement(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "input.yuv").write_bytes(bytes(12))
            descriptor_path = root / "input.yaml"
            descriptor_path.write_text(
                "\n".join([
                    "env:",
                    "  COUNT: 1",
                    "input:",
                    "  path: input.yuv",
                    "  width: 4",
                    "  height: 2",
                    '  framerate: "24/1"',
                    "  format: NV12",
                    "  frame_count: ${COUNT}",
                    "  stride: 4",
                    "  sliceheight: 2",
                ]),
                encoding = "utf-8",
            )

            descriptor = load_descriptor(descriptor_path).input

            self.assertEqual(descriptor.frame_count, 1)
            self.assertIsInstance(descriptor.frame_count, int)

    def test_missing_invalid_and_unset_variables_are_errors(self):
        cases = (
            "env: null\ninput:\n  path: input.264",
            "env:\n  BAD-NAME: value\ninput:\n  path: input.264",
            "env:\n  ITEMS: [one, two]\ninput:\n  path: input.264",
            "env:\n  UNUSED: ${MISSING}\ninput:\n  path: input.264",
            "input:\n  path: ${MISSING}/input.264",
            "env:\n  ROOT: null\ninput:\n  path: ${ROOT}/input.264",
            "input:\n  path: ${BAD-NAME}/input.264",
        )

        for content in cases:
            with self.subTest(content = content):
                with tempfile.TemporaryDirectory() as folder:
                    descriptor_path = Path(folder) / "input.yaml"
                    descriptor_path.write_text(content, encoding = "utf-8")

                    with patch.dict(os.environ, {"ROOT" : "system"}, clear = False):
                        with self.assertRaises(ConfigurationError):
                            load_descriptor(descriptor_path)

    def test_descriptor_scalar_types_are_not_coerced(self):
        cases = (
            "\n".join([
                "env:",
                '  COUNT: "1"',
                "input:",
                "  path: input.yuv",
                "  width: 4",
                "  height: 2",
                '  framerate: "24/1"',
                "  format: NV12",
                "  frame_count: ${COUNT}",
                "  stride: 4",
                "  sliceheight: 2",
            ]),
            "\n".join([
                "env:",
                "  MEDIA_PATH: 10",
                "input:",
                "  path: ${MEDIA_PATH}",
            ]),
        )

        for content in cases:
            with self.subTest(content = content):
                with tempfile.TemporaryDirectory() as folder:
                    root = Path(folder)
                    (root / "input.yuv").write_bytes(bytes(12))
                    descriptor_path = root / "input.yaml"
                    descriptor_path.write_text(content, encoding = "utf-8")

                    with self.assertRaises(ConfigurationError):
                        load_descriptor(descriptor_path)

    def test_unknown_media_field_does_not_expand_missing_variable(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "input.264").write_bytes(b"encoded")
            descriptor_path = root / "input.yaml"
            descriptor_path.write_text(
                "\n".join([
                    "input:",
                    "  path: input.264",
                    "  note: ${MISSING}",
                ]),
                encoding = "utf-8",
            )

            descriptor = load_descriptor(descriptor_path).input

            self.assertEqual(descriptor.path, (root / "input.264").resolve())


if __name__ == "__main__":
    unittest.main()
