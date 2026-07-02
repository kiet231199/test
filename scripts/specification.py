import re
import stat
from pathlib import Path

import pandas as pd

from scripts import log
from scripts.templates import render_bash_script
from scripts.criteria import parse_criteria
from scripts.metrics import METRIC_HANDLERS, RESULT_FILE_NAME


###############################################################################
#                              GLOBAL VARIABLES                               #
###############################################################################

SCRIPT_NAME       = "script.sh"
TEST_CASE_COL     = "Test case"
CHECK_METRICS_COL = "Check metrics"
PIPELINE_COL      = "Pipeline"
OUTPUT_COL        = "Output"


###############################################################################
#                               LOCAL FUNCTIONS                               #
###############################################################################

def sanitize_name(name: str) -> str:
    """
    Convert sheet name / test case name to a safe directory name.
    """
    name = str(name).strip()

    # Replace path separators and unsafe characters
    name = re.sub(r'[\\/:"*?<>|]+', "_", name)

    # Replace repeated whitespace with single underscore
    name = re.sub(r"\s+", "_", name)

    return name if name else "unknown"


def cell_to_text(value) -> str:
    """
    Convert Excel cell value to string safely.
    Keep newline characters if they exist.
    """
    if pd.isna(value):
        return ""

    # Avoid printing float integer like 24.0
    if isinstance(value, float) and value.is_integer():
        return str(int(value))

    return str(value)


def normalize_multiline_text(value: str) -> str:
    """
    Normalize Windows/Mac newline to Unix newline.
    """
    return value.replace("\r\n", "\n").replace("\r", "\n")


def render_test_information(row, columns_to_write):
    """
    Render specification columns as bash comments.

    If an Excel cell has newline characters, output multiple comment lines.
    """
    lines        = []
    column_width = 24

    for col in columns_to_write:
        value       = cell_to_text(row[col])
        value       = normalize_multiline_text(value)
        value_lines = value.split("\n")

        for i, value_line in enumerate(value_lines):
            if not value_line:
                value_line = "--"

            if i == 0:
                lines.append(f"# {str(col):<{column_width}} {value_line}")
            else:
                lines.append(f"# {'':<{column_width}} {value_line}")

    return "\n".join(lines)


def parse_check_metrics(cell_text: str):
    """
    Parse the 'Check metrics' cell into an ordered list of metric names.

    Metric names are separated by ';' and newline characters. Duplicated
    names are dropped while preserving their first occurrence order.
    """
    cell_text = normalize_multiline_text(cell_text)

    tokens = re.split(r"[;\n]+", cell_text)

    metric_names = []

    for token in tokens:
        name = token.strip()

        if not name:
            continue

        if name not in metric_names:
            metric_names.append(name)

    return metric_names


def build_check_metrics(sheet_name, excel_row_number, row, metric_names, output):
    """
    Build the list of (metric_name, extract_bash, condition_bash) tuples.

    Each requested metric must be supported and must have a non-empty
    criteria in the column whose name equals the metric name. Any missing
    or unsupported metric raises an error immediately.
    """
    check_metrics = []

    for metric_name in metric_names:
        if metric_name not in METRIC_HANDLERS:
            raise ValueError(
                f"Sheet '{sheet_name}', Excel row {excel_row_number}: "
                f"unsupported check metric '{metric_name}'"
            )

        if metric_name not in row.index:
            raise ValueError(
                f"Sheet '{sheet_name}', Excel row {excel_row_number}: "
                f"missing criteria column '{metric_name}'"
            )

        criteria = cell_to_text(row[metric_name]).strip()

        if not criteria:
            raise ValueError(
                f"Sheet '{sheet_name}', Excel row {excel_row_number}: "
                f"empty criteria for metric '{metric_name}'"
            )

        handler_class = METRIC_HANDLERS[metric_name]
        row_values    = {}

        for field_name in handler_class.required_row_fields:
            if field_name not in row.index:
                raise ValueError(
                    f"Sheet '{sheet_name}', Excel row {excel_row_number}: "
                    f"missing required column '{field_name}' for metric "
                    f"'{metric_name}'"
                )

            row_values[field_name] = cell_to_text(row[field_name]).strip()

        try:
            handler = handler_class(
                row_values = row_values,
                output     = output,
            )
        except ValueError as e:
            raise ValueError(
                f"Sheet '{sheet_name}', Excel row {excel_row_number}, "
                f"metric '{metric_name}': {e}"
            ) from e

        extract_bash = handler.render(
            output      = output,
            result_file = RESULT_FILE_NAME,
        )

        condition_bash = parse_criteria(criteria)

        check_metrics.append((metric_name, extract_bash, condition_bash))

    return check_metrics


def make_executable(path: Path):
    """
    chmod +x script.sh
    """
    current_mode = path.stat().st_mode
    path.chmod(current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def validate_required_columns(sheet_name: str, columns):
    """
    Validate required columns.
    """
    required_columns = [
        TEST_CASE_COL,
        CHECK_METRICS_COL,
        PIPELINE_COL,
    ]

    for col in required_columns:
        if col not in columns:
            raise ValueError(
                f"Sheet '{sheet_name}' does not contain required column '{col}'"
            )


def create_test_from_excel(
    spec_file: Path,
    out_dir: Path,
    work_dir: str,
    pc_work_dir: str,
):
    """
    Main generator:
    - Each sheet becomes a test suite directory.
    - Each row with a valid 'Test case' becomes a test case directory.
    - script.sh is created from template.
    - Pipeline is required.
    - Output is optional. If Output is defined, chmod command is generated.
    - pc_work_dir is exported by check() for PC-side file paths.
    """
    if not spec_file.exists():
        raise FileNotFoundError(f"Specification file not found: {spec_file}")

    sheets = pd.read_excel(
        spec_file,
        sheet_name = None,
        dtype      = object,
        engine     = "openpyxl",
    )

    out_dir.mkdir(parents=True, exist_ok=True)

    for sheet_name, df in sheets.items():
        if df.empty:
            log.warning(f"Sheet '{sheet_name}' is empty")
            continue

        columns = list(df.columns)

        validate_required_columns(sheet_name, columns)

        test_case_idx     = columns.index(TEST_CASE_COL)
        check_metrics_idx = columns.index(CHECK_METRICS_COL)

        if check_metrics_idx <= test_case_idx:
            raise ValueError(
                f"Sheet '{sheet_name}': "
                f"'{CHECK_METRICS_COL}' column must be after '{TEST_CASE_COL}' column"
            )

        # Start after 'Test case', stop before 'Check metrics'
        columns_to_write = columns[test_case_idx + 1 : check_metrics_idx]

        suite_dir = out_dir / sanitize_name(sheet_name)
        suite_dir.mkdir(parents=True, exist_ok=True)

        generated_count = 0

        for row_index, row in df.iterrows():
            test_case_value = cell_to_text(row[TEST_CASE_COL]).strip()

            # Skip rows without test case name
            if not test_case_value:
                continue

            pipeline = cell_to_text(row[PIPELINE_COL]).strip()

            if not pipeline:
                raise ValueError(
                    f"Sheet '{sheet_name}', Excel row {row_index + 2}, "
                    f"test case '{test_case_value}' has empty '{PIPELINE_COL}'"
                )

            output = ""
            if OUTPUT_COL in columns:
                output = cell_to_text(row[OUTPUT_COL]).strip()

            metric_names  = parse_check_metrics(cell_to_text(row[CHECK_METRICS_COL]))
            check_metrics = build_check_metrics(
                sheet_name       = sheet_name,
                excel_row_number = row_index + 2,
                row              = row,
                metric_names     = metric_names,
                output           = output,
            )

            test_case_dir = suite_dir / sanitize_name(test_case_value)
            test_case_dir.mkdir(parents=True, exist_ok=True)

            script_path = test_case_dir / SCRIPT_NAME

            test_information = render_test_information(
                row              = row,
                columns_to_write = columns_to_write,
            )

            script_content = render_bash_script(
                test_information = test_information,
                pipeline         = pipeline,
                output           = output,
                work_dir         = work_dir,
                pc_work_dir      = pc_work_dir,
                check_metrics    = check_metrics,
            )

            script_path.write_text(script_content, encoding="utf-8")
            make_executable(script_path)

            generated_count += 1

        log.ok(
            f"Sheet '{sheet_name}' -> "
            f"{generated_count} test case(s) generated in {suite_dir}"
        )
