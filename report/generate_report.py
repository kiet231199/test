"""generate_report.py - aggregate check results into an Excel report.

Reads every results/<suite>/<test_case>/check_result.json, merges it with the
canonical test list in config/tests.json (sourced from the spec), and writes
report/test_report.xlsx with:
  * a "Statistics" sheet (totals, PASS/FAIL/TIMEOUT/SKIPPED %, durations), and
  * one sheet per suite (Test_case, Test purpose, Status, Expected_Value,
    Actual_Value, Duration_Sec, Pipeline).

Status taxonomy per test (PROMPT.md 3.6):
  * SKIPPED  - the test is disabled in the spec (enabled == false).
  * NOT_RUN  - enabled but no check_result.json exists (never executed/checked).
  * PASS/FAIL/TIMEOUT - taken from the test's check_result.json status.

All paths are resolved from this file's own location (never hardcoded). openpyxl
is imported lazily inside the functions that need it.
"""

import os
import sys
import json
import datetime
from typing import Optional, List, Dict, Any

# report/generate_report.py -> ROOT is the parent directory of report/.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_THIS_DIR)


# --- Logging (timestamped; colored only when stderr is a terminal) ----------
_COLORS = {"INFO": "\033[32m", "WARN": "\033[33m", "ERROR": "\033[31m"}
_RESET = "\033[0m"


def _emit(level, msg):
    line = "[{ts}] [{lvl}] {msg}".format(
        ts=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        lvl=level, msg=msg)
    if sys.stderr.isatty():
        line = _COLORS.get(level, "") + line + _RESET
    print(line, file=sys.stderr)


def log_info(msg):
    _emit("INFO", msg)


def log_warn(msg):
    _emit("WARN", msg)


def log_error(msg):
    _emit("ERROR", msg)


# --- Canonical paths (anchored to ROOT) -------------------------------------
CONFIG_PATH = os.path.join(ROOT, "config", "tests.json")
RESULTS_DIR = os.path.join(ROOT, "results")
REPORT_PATH = os.path.join(_THIS_DIR, "test_report.xlsx")

# --- Status fill colors (ARGB hex, no leading '#') --------------------------
FILL_PASS = "C6EFCE"      # light green
FILL_FAIL = "FFC7CE"      # light red
FILL_TIMEOUT = "FFEB9C"   # light orange
FILL_SKIPPED = "EFEFEF"   # light gray (SKIPPED and NOT_RUN)

# Suite-sheet column headers, in order.
SUITE_HEADERS = [
    "Test_case",
    "Test purpose",
    "Status",
    "Expected_Value",
    "Actual_Value",
    "Duration_Sec",
    "Pipeline",
]

PIPELINE_TRUNCATE = 80  # characters before appending "..."


def load_tests(config_path: str) -> List[Dict[str, Any]]:
    """Load config/tests.json (the canonical, spec-sourced test list).

    Returns an empty list (with a warning) if the file is missing or invalid,
    so the report can still render whatever results exist.
    """
    try:
        with open(config_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, IOError):
        log_warn(
            "config/tests.json not found at {0}; report will be empty.".format(
                config_path
            )
        )
        return []
    except (ValueError, json.JSONDecodeError):
        log_error(
            "config/tests.json at {0} is not valid JSON.".format(config_path)
        )
        return []
    if not isinstance(data, list):
        log_error("config/tests.json must be a JSON array.")
        return []
    return data


def load_check_result(suite: str, test_case: str) -> Optional[Dict[str, Any]]:
    """Load results/<suite>/<test_case>/check_result.json, or None if absent.

    A missing file means the test was never run/checked (NOT_RUN). A malformed
    file is reported and treated as absent.
    """
    path = os.path.join(RESULTS_DIR, suite, test_case, "check_result.json")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, IOError):
        return None
    except (ValueError, json.JSONDecodeError):
        log_warn(
            "Malformed check_result.json for {0}/{1}; treating as NOT_RUN.".format(
                suite, test_case
            )
        )
        return None


def _truncate_pipeline(pipeline: str) -> str:
    """Truncate a pipeline string to PIPELINE_TRUNCATE chars + '...'."""
    pipeline = pipeline or ""
    if len(pipeline) > PIPELINE_TRUNCATE:
        return pipeline[:PIPELINE_TRUNCATE] + "..."
    return pipeline


def _resolve_status(test: Dict[str, Any],
                    result: Optional[Dict[str, Any]]) -> str:
    """Resolve a test's report status from its spec row + check result.

    SKIPPED if disabled in the spec; NOT_RUN if enabled but no result file;
    otherwise the status recorded in check_result.json (PASS/FAIL/TIMEOUT,
    falling back to FAIL for any unexpected value).
    """
    if not test.get("enabled", True):
        return "SKIPPED"
    if result is None:
        return "NOT_RUN"
    status = str(result.get("status", "")).strip().upper()
    if status in ("PASS", "FAIL", "TIMEOUT", "SKIPPED"):
        return status
    # Unknown/blank status from a result file is conservatively a FAIL.
    return "FAIL"


def _fill_for_status(status: str) -> Optional[str]:
    """Map a status to its fill hex, or None when no fill applies."""
    if status == "PASS":
        return FILL_PASS
    if status == "FAIL":
        return FILL_FAIL
    if status == "TIMEOUT":
        return FILL_TIMEOUT
    if status in ("SKIPPED", "NOT_RUN"):
        return FILL_SKIPPED
    return None


def _pct(count: int, total: int) -> str:
    """Format count as 'N (XX.X%)', guarding against division by zero."""
    if total <= 0:
        return "{0} (0.0%)".format(count)
    return "{0} ({1:.1f}%)".format(count, (count / total) * 100.0)


def build_rows(tests: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Merge each spec test with its check result into a flat row dict.

    Each row carries: suite, test_case, description, expected_value, actual,
    duration_sec, pipeline, status.
    """
    rows: List[Dict[str, Any]] = []
    for test in tests:
        suite = str(test.get("suite", "")).strip()
        test_case = str(test.get("test_case", "")).strip()
        result = load_check_result(suite, test_case)
        status = _resolve_status(test, result)

        # Actual value + duration come from the result file when present.
        actual = ""
        duration = ""
        if result is not None:
            actual = result.get("actual", "") or ""
            dur = result.get("duration_sec", None)
            if isinstance(dur, (int, float)):
                duration = dur

        rows.append({
            "suite": suite,
            "test_case": test_case,
            "description": test.get("description", "") or "",
            "expected_value": test.get("expected_value", "") or "",
            "actual": actual,
            "duration_sec": duration,
            "pipeline": _truncate_pipeline(test.get("pipeline", "")),
            "status": status,
            "enabled": bool(test.get("enabled", True)),
            "has_result": result is not None,
        })
    return rows


def _compute_stats(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute aggregate statistics across all rows.

    Tests Run = rows that have a check_result.json (were executed & checked).
    Percentages are computed against Tests Run and guarded against zero.
    """
    total = len(rows)
    enabled = sum(1 for r in rows if r["enabled"])
    tests_run = sum(1 for r in rows if r["has_result"])

    passed = sum(1 for r in rows if r["status"] == "PASS")
    failed = sum(1 for r in rows if r["status"] == "FAIL")
    timeout = sum(1 for r in rows if r["status"] == "TIMEOUT")
    skipped = sum(1 for r in rows if r["status"] in ("SKIPPED", "NOT_RUN"))

    # Durations only from rows that actually ran and recorded a number.
    durations = [
        float(r["duration_sec"])
        for r in rows
        if isinstance(r["duration_sec"], (int, float))
    ]
    total_duration = sum(durations)
    if durations:
        avg_duration = total_duration / len(durations)
    else:
        avg_duration = 0.0

    return {
        "total": total,
        "enabled": enabled,
        "tests_run": tests_run,
        "passed": passed,
        "failed": failed,
        "timeout": timeout,
        "skipped": skipped,
        "total_duration": total_duration,
        "avg_duration": avg_duration,
    }


def _write_statistics_sheet(workbook, rows: List[Dict[str, Any]]) -> None:
    """Write the 'Statistics' sheet (must be the first sheet)."""
    from openpyxl.styles import Font

    stats = _compute_stats(rows)
    run = stats["tests_run"]  # percentage denominator

    sheet = workbook.active
    sheet.title = "Statistics"

    bold = Font(bold=True)

    # Header row.
    sheet["A1"] = "Metric"
    sheet["B1"] = "Value"
    sheet["A1"].font = bold
    sheet["B1"].font = bold

    metrics = [
        ("Total Tests", stats["total"]),
        ("Enabled Tests", stats["enabled"]),
        ("Tests Run", stats["tests_run"]),
        ("PASS", _pct(stats["passed"], run)),
        ("FAIL", _pct(stats["failed"], run)),
        ("TIMEOUT", _pct(stats["timeout"], run)),
        ("SKIPPED", _pct(stats["skipped"], stats["total"])),
        ("Total Duration", "{0:.2f}".format(stats["total_duration"])),
        ("Average Duration", "{0:.2f}".format(stats["avg_duration"])),
        ("Report Generated",
         datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    ]
    for offset, (metric, value) in enumerate(metrics, start=2):
        sheet.cell(row=offset, column=1, value=metric)
        sheet.cell(row=offset, column=2, value=value)

    # Bold the header row and freeze it.
    sheet.freeze_panes = "A2"

    # Reasonable column widths for readability.
    sheet.column_dimensions["A"].width = 20
    sheet.column_dimensions["B"].width = 28


def _write_suite_sheet(workbook, suite: str,
                       suite_rows: List[Dict[str, Any]]) -> None:
    """Write one suite sheet with status-colored data rows."""
    from openpyxl.styles import Font, PatternFill

    # Excel sheet titles are capped at 31 chars and forbid some characters.
    if suite:
        safe_title = suite[:31]
    else:
        safe_title = "Suite"
    sheet = workbook.create_sheet(title=safe_title)

    bold = Font(bold=True)

    # Header row.
    for col_idx, header in enumerate(SUITE_HEADERS, start=1):
        cell = sheet.cell(row=1, column=col_idx, value=header)
        cell.font = bold

    # Data rows.
    for row_idx, row in enumerate(suite_rows, start=2):
        values = [
            row["test_case"],
            row["description"],
            row["status"],
            row["expected_value"],
            row["actual"],
            row["duration_sec"],
            row["pipeline"],
        ]
        for col_idx, value in enumerate(values, start=1):
            sheet.cell(row=row_idx, column=col_idx, value=value)

        # Conditional fill across the whole row.
        fill_hex = _fill_for_status(row["status"])
        if fill_hex is not None:
            fill = PatternFill(
                start_color=fill_hex, end_color=fill_hex, fill_type="solid"
            )
            for col_idx in range(1, len(SUITE_HEADERS) + 1):
                sheet.cell(row=row_idx, column=col_idx).fill = fill

    # Freeze the header row.
    sheet.freeze_panes = "A2"

    # Column widths tuned for the typical content.
    widths = {"A": 16, "B": 40, "C": 12, "D": 24, "E": 28, "F": 12, "G": 60}
    for col_letter, width in widths.items():
        sheet.column_dimensions[col_letter].width = width


def generate_report(config_path: str = CONFIG_PATH,
                    report_path: str = REPORT_PATH) -> str:
    """Build the Excel report and write it to report_path. Returns the path."""
    # openpyxl is imported here (and in helpers) so module import stays light.
    from openpyxl import Workbook

    log_info("Loading test config from {0}".format(config_path))
    tests = load_tests(config_path)
    rows = build_rows(tests)
    log_info(
        "Merged {0} tests with their check results.".format(len(rows))
    )

    workbook = Workbook()

    # Sheet 1: Statistics (uses the default active sheet).
    _write_statistics_sheet(workbook, rows)

    # One sheet per suite, in first-seen order from the config.
    suite_order: List[str] = []
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        suite = row["suite"] or "Suite"
        if suite not in grouped:
            grouped[suite] = []
            suite_order.append(suite)
        grouped[suite].append(row)

    for suite in suite_order:
        _write_suite_sheet(workbook, suite, grouped[suite])
        log_info(
            "Wrote suite sheet '{0}' ({1} rows).".format(
                suite, len(grouped[suite])
            )
        )

    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    workbook.save(report_path)
    log_info("Report written to {0}".format(report_path))
    return report_path


if __name__ == "__main__":
    # Allow optional overrides: argv[1]=config path, argv[2]=report path.
    if len(sys.argv) > 1:
        cfg = sys.argv[1]
    else:
        cfg = CONFIG_PATH
    if len(sys.argv) > 2:
        out = sys.argv[2]
    else:
        out = REPORT_PATH
    try:
        path = generate_report(cfg, out)
    except Exception as exc:  # noqa: BLE001  (top-level guard for CLI use)
        log_error("Report generation failed: {0}".format(exc))
        sys.exit(1)
    sys.exit(0)
