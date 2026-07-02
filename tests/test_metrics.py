import unittest

import pandas as pd

from scripts.criteria import parse_criteria
from scripts.metrics import (
    GSTREAMER_TO_FFMPEG_FORMAT,
    INFINITE_PSNR_VALUE,
    PsnrMetric,
    WidthMetric,
    parse_media_spec,
    quote_expandable_path,
)
from scripts.specification import build_check_metrics
from scripts.templates import render_bash_script


class MediaSpecTests(unittest.TestCase):
    def test_raw_formats_use_centralized_mapping(self):
        for gst_format, ffmpeg_format in GSTREAMER_TO_FFMPEG_FORMAT.items():
            with self.subTest(gst_format = gst_format):
                media = parse_media_spec(
                    f"$WORK_DIR/input.yuv|224|96|{gst_format}",
                    "Input",
                )

                self.assertTrue(media.is_raw)
                self.assertEqual(media.ffmpeg_pixel_format, ffmpeg_format)

    def test_encoded_files_accept_path_or_optional_metadata(self):
        for cell_text in (
            "$WORK_DIR/output.265",
            "$WORK_DIR/output.264|224|96|NV12",
        ):
            with self.subTest(cell_text = cell_text):
                media = parse_media_spec(cell_text, "Output")

                self.assertFalse(media.is_raw)
                self.assertIsNone(media.width)
                self.assertIsNone(media.ffmpeg_pixel_format)

    def test_invalid_media_cells_raise_errors(self):
        invalid_cells = (
            "input.raw",
            "input.raw|224|96|UNKNOWN",
            "input.yuv|0|96|NV12",
            "input.yuv|224|height|NV12",
            "input.yuv|224|96",
            "input.mp4",
        )

        for cell_text in invalid_cells:
            with self.subTest(cell_text = cell_text):
                with self.assertRaises(ValueError):
                    parse_media_spec(cell_text, "Input")


class PsnrMetricTests(unittest.TestCase):
    def test_psnr_renders_all_raw_and_encoded_combinations(self):
        raw_input    = "$WORK_DIR/input.yuv|224|96|NV12"
        encoded_input = "$WORK_DIR/input.264"
        raw_output    = "$WORK_DIR/output.raw|224|96|NV12"
        encoded_output = "$WORK_DIR/output.265"

        cases = (
            (raw_input, raw_output, 2),
            (raw_input, encoded_output, 1),
            (encoded_input, raw_output, 1),
            (encoded_input, encoded_output, 0),
        )

        for input_value, output_value, raw_input_count in cases:
            with self.subTest(input_value = input_value, output_value = output_value):
                metric = PsnrMetric(
                    row_values = {"Input": input_value},
                    output     = output_value,
                )

                rendered = metric.render(
                    output      = metric.output,
                    result_file = "result.txt",
                )

                self.assertEqual(rendered.count("-f rawvideo"), raw_input_count)
                self.assertIn('-lavfi "[0:v][1:v]psnr"', rendered)
                self.assertIn(
                    f'if [[ "${{value}}" == "inf" ]]; then '
                    f'value={INFINITE_PSNR_VALUE}; fi',
                    rendered,
                )
                self.assertIn('value=""', rendered)

    def test_metric_generation_requires_input_column(self):
        row = pd.Series({
            "psnr" : ">=29",
        })

        with self.assertRaisesRegex(ValueError, "missing required column 'Input'"):
            build_check_metrics(
                sheet_name       = "Video_H264",
                excel_row_number = 2,
                row              = row,
                metric_names     = ["psnr"],
                output           = "$WORK_DIR/output.265",
            )


class BashRenderTests(unittest.TestCase):
    def test_expandable_path_protects_non_work_dir_expansion(self):
        rendered = quote_expandable_path("$WORK_DIR/$HOME/$(whoami)/file.265")

        self.assertEqual(
            rendered,
            '"$WORK_DIR/\\$HOME/\\$(whoami)/file.265"',
        )

    def test_generated_check_exports_pc_work_dir_and_keeps_metric_order(self):
        row = pd.Series({
            "return" : "0",
            "psnr"   : ">=29",
            "Input"  : "${WORK_DIR}/input.yuv|224|96|NV12",
        })

        check_metrics = build_check_metrics(
            sheet_name       = "Video_H264",
            excel_row_number = 2,
            row              = row,
            metric_names     = ["return", "psnr"],
            output           = "$WORK_DIR/output.265",
        )

        script = render_bash_script(
            test_information = "# test",
            pipeline         = "true",
            output           = "$WORK_DIR/output.265",
            work_dir         = "/test_program",
            pc_work_dir      = "/nfs/test_program",
            check_metrics    = check_metrics,
        )

        self.assertIn("export WORK_DIR=/nfs/test_program", script)
        self.assertIn('# Check return', script)
        self.assertIn('# Check psnr', script)
        self.assertLess(script.index('# Check return'), script.index('# Check psnr'))
        self.assertIn('-i "$WORK_DIR/output.265"', script)

    def test_existing_output_metrics_expand_work_dir(self):
        rendered = WidthMetric().render(
            output      = "$WORK_DIR/output.265",
            result_file = "result.txt",
        )

        self.assertIn('[[ -f "$WORK_DIR/output.265" ]]', rendered)


if __name__ == "__main__":
    unittest.main()
