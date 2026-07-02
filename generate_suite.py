#!/usr/bin/env python3
"""generate_suite.py - build the GStreamer QA test suite from the spec.

This is the ONE source of truth for the suite. It:

  1. Reads ``specs/test_spec.xlsx`` (one suite per sheet).
  2. Writes ``config/tests.json`` - one object per test (used by the runners and
     the report).
  3. Emits, for every test, two **fully self-contained** scripts:
       - ``tests/<suite>/<test_case>/run_test.sh``     - real pipeline + timeout
         inlined; runs it on the remote over SSH.
       - ``tests/<suite>/<test_case>/check_output.py`` - only the checks this test
         needs, with the expected values inlined; validates the output.
     Open any one of those files and you can read exactly what it does - there is
     no template indirection, no shared library import, and no config lookup at
     run time. The values live in the file.

  4. Is idempotent / re-runnable: re-running with the same spec produces the same
     bytes, and test directories that are no longer in the spec are removed.

HOW TO CHANGE A TEST: edit ``specs/test_spec.xlsx`` and re-run this script.
HOW TO ADD A NEW METRIC CHECK: add one entry to ``SNIPPET_BUILDERS`` below (a
small function that returns the Python source for the check) and re-run. That is
the single edit point - the per-test scripts are regenerated from it.

Paths are anchored to this file's own directory, so the script works from
anywhere. Only ``load_spec`` needs pandas; the rest is the standard library.
"""

import os
import re
import sys
import json
import shlex
import shutil
import argparse
import datetime


# ROOT is the directory containing this script (gst_test_suite/).
ROOT = os.path.dirname(os.path.abspath(__file__))
SPEC_PATH = os.path.join(ROOT, "specs", "test_spec.xlsx")
CONFIG_PATH = os.path.join(ROOT, "config", "tests.json")
TESTS_DIR = os.path.join(ROOT, "tests")


# ===========================================================================
# Logging (timestamped, colored only when stderr is a terminal)
# ===========================================================================

_COLORS = {"INFO": "\033[32m", "WARN": "\033[33m", "ERROR": "\033[31m"}
_RESET = "\033[0m"


def _emit(level, msg):
    line = "[{ts}] [{lvl}] {msg}".format(
        ts=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        lvl=level,
        msg=msg,
    )
    if sys.stderr.isatty():
        line = _COLORS.get(level, "") + line + _RESET
    print(line, file=sys.stderr)


def log_info(msg, quiet=False):
    if not quiet:
        _emit("INFO", msg)


def log_warn(msg, quiet=False):
    if not quiet:
        _emit("WARN", msg)


def log_error(msg):
    _emit("ERROR", msg)


# ===========================================================================
# Spec loading (one suite per sheet) - pandas imported lazily
# ===========================================================================

def _normalize_header(name):
    """Lower-case + strip a header for case-insensitive matching."""
    return str(name).strip().lower()


def _is_blank(value):
    """True for None, NaN, or an empty/whitespace-only string."""
    if value is None:
        return True
    if isinstance(value, float) and value != value:  # NaN != NaN
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    return False


def _coerce_int(value, default):
    """Coerce to int, returning default on blank/non-numeric input."""
    if _is_blank(value):
        return default
    try:
        return int(float(value))  # handles Excel floats like 30.0
    except (TypeError, ValueError):
        return default


def _coerce_bool(value, default=True):
    """Parse a truthy/falsy spec cell into a bool (default when unknown)."""
    if _is_blank(value):
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if value == 1:
            return True
        if value == 0:
            return False
        return default
    text = str(value).strip().lower()
    if text in ("true", "1", "yes"):
        return True
    if text in ("false", "0", "no"):
        return False
    return default


def _coerce_optional_str(value):
    """Stripped string, or None when blank/NaN."""
    if _is_blank(value):
        return None
    return str(value).strip()


def _coerce_text(value):
    """Stripped string, or '' when blank/NaN."""
    if _is_blank(value):
        return ""
    return str(value).strip()


def load_spec(xlsx_path):
    """Load every sheet of the spec workbook into a list of test dicts.

    Each sheet is one suite (suite = sheet name). Column lookup is trimmed and
    case-insensitive; rows without a Test_case are skipped; unknown columns are
    ignored. pandas is imported here so importing this module stays light.
    """
    import pandas as pd  # lazy import

    sheets = pd.read_excel(xlsx_path, sheet_name=None, engine="openpyxl")
    results = []

    for sheet_name, frame in sheets.items():
        suite = str(sheet_name).strip()

        # normalized header -> actual column label, for this sheet.
        col_lookup = {}
        for col in frame.columns:
            col_lookup[_normalize_header(col)] = col

        def get_cell(row, header):
            actual = col_lookup.get(header)
            if actual is None:
                return None
            return row.get(actual)

        for _, row in frame.iterrows():
            test_case_raw = get_cell(row, "test_case")
            if _is_blank(test_case_raw):
                continue

            # description: prefer 'Test purpose', fall back to 'Description'.
            description_raw = get_cell(row, "test purpose")
            if _is_blank(description_raw):
                description_raw = get_cell(row, "description")

            results.append({
                "suite": suite,
                "test_case": str(test_case_raw).strip(),
                "description": _coerce_text(description_raw),
                "pipeline": _coerce_text(get_cell(row, "pipeline")),
                "output_type": _coerce_text(get_cell(row, "output_type")),
                "expected_value": _coerce_text(get_cell(row, "expected_value")),
                "timeout_sec": _coerce_int(get_cell(row, "timeout_sec"), 30),
                "enabled": _coerce_bool(get_cell(row, "enabled"), True),
                "input_file": _coerce_optional_str(get_cell(row, "input_file")),
                "output_file": _coerce_optional_str(get_cell(row, "output_file")),
            })

    return results


# ===========================================================================
# Expected-value parsing (resolves each test's literal targets)
# ===========================================================================

def parse_expected(output_type, expected_value):
    """Turn Output_Type + Expected_Value into an ordered {key: expr} dict.

    Single-key Output_Type -> the WHOLE Expected_Value is that key's expr (so a
    threshold like '>=24.0' is not split on its '='). Multi-key -> each
    ';'-separated token of Expected_Value is split on its FIRST '=' into key=expr.
    """
    output_type = output_type or ""
    expected_value = expected_value or ""

    keys = [k.strip() for k in output_type.split(";") if k.strip()]
    if len(keys) == 1:
        return {keys[0]: expected_value.strip()}

    result = {}
    for token in expected_value.split(";"):
        token = token.strip()
        if not token:
            continue
        key, sep, expr = token.partition("=")
        if sep == "":
            continue
        result[key.strip()] = expr.strip()
    return result


def _parse_range(expr):
    """Parse 'lo:hi' into (lo, hi) floats."""
    lo_str, _, hi_str = expr.partition(":")
    return (float(lo_str.strip()), float(hi_str.strip()))


def _parse_threshold(expr):
    """Parse '>=x'/'>x'/'<=x'/'<x'/'==x' (bare number == equality) -> (op, x)."""
    expr = expr.strip()
    for op in (">=", "<=", "==", ">", "<"):
        if expr.startswith(op):
            return (op, float(expr[len(op):].strip()))
    return ("==", float(expr))


def _emit_numeric_compare(expr, var="actual"):
    """Return a Python boolean expression comparing `var` per a spec expr.

    'min:max' -> 'lo <= var <= hi'; otherwise a threshold like 'var >= 24.0'.
    The numbers are baked in as literals so the emitted check has no parsing.
    """
    expr = (expr or "").strip()
    if ":" in expr:
        lo, hi = _parse_range(expr)
        return "{lo} <= {var} <= {hi}".format(lo=lo, hi=hi, var=var)
    op, value = _parse_threshold(expr)
    return "{var} {op} {value}".format(var=var, op=op, value=value)


# ===========================================================================
# Check snippets - the SINGLE EDIT POINT for metrics
# ===========================================================================
# Each builder takes the test's expected expr for its key and returns a dict:
#   {"name": "<emitted function name>",
#    "func": "<def name(ctx): ... source emitted into check_output.py>",
#    "needs_ssh": bool,   # function calls _ssh_capture (-> import subprocess/shlex)
#    "needs_re":  bool}   # function uses re (-> import re)
# The emitted function has the uniform signature  check_<key>(ctx) -> (bool, str)
# where ctx exposes .ip, .exit_code, .stdout, .stderr and the module-level
# OUTPUT_FILE / INPUT_FILE constants. To add a metric: write one builder and add
# it to SNIPPET_BUILDERS.


def _snip_dimension(dim, expr):
    """width / height: probe the stream dimension and compare to an int target."""
    target = int(expr)
    func = (
        'def check_{dim}(ctx):\n'
        '    """Probe {dim} via remote ffprobe; PASS iff it equals {target}."""\n'
        '    rc, out, _ = _ssh_capture(\n'
        '        ctx.ip,\n'
        '        "ffprobe -v error -select_streams v:0 -show_entries stream={dim} "\n'
        '        "-of default=nw=1:nk=1 " + shlex.quote(OUTPUT_FILE),\n'
        '    )\n'
        '    if rc != 0 or not out.strip():\n'
        '        return (False, "unavailable")\n'
        '    try:\n'
        '        actual = int(float(out.splitlines()[0].strip()))\n'
        '    except (ValueError, IndexError):\n'
        '        return (False, "unavailable")\n'
        '    return (actual == {target}, str(actual))'
    ).format(dim=dim, target=target)
    return {"name": "check_" + dim, "func": func,
            "needs_ssh": True, "needs_re": False}


def _snip_width(expr):
    return _snip_dimension("width", expr)


def _snip_height(expr):
    return _snip_dimension("height", expr)


def _snip_bitrate(expr):
    """bitrate: average bitrate via ffprobe (stream, then format fallback)."""
    compare = _emit_numeric_compare(expr, "actual")
    func = (
        'def check_bitrate(ctx):\n'
        '    """Probe average bitrate via remote ffprobe and compare."""\n'
        '    def probe(entry):\n'
        '        rc, out, _ = _ssh_capture(\n'
        '            ctx.ip,\n'
        '            "ffprobe -v error " + entry + " -of default=nw=1:nk=1 "\n'
        '            + shlex.quote(OUTPUT_FILE),\n'
        '        )\n'
        '        if rc != 0 or not out.strip():\n'
        '            return None\n'
        '        line = out.strip().splitlines()[0].strip()\n'
        '        if not line or line.upper() == "N/A":\n'
        '            return None\n'
        '        return line\n'
        '    raw = (probe("-select_streams v:0 -show_entries stream=bit_rate")\n'
        '           or probe("-show_entries format=bit_rate"))\n'
        '    if raw is None:\n'
        '        return (False, "unavailable")\n'
        '    try:\n'
        '        actual = float(raw)\n'
        '    except ValueError:\n'
        '        return (False, "unavailable")\n'
        '    return ({compare}, str(int(actual)))'
    ).format(compare=compare)
    return {"name": "check_bitrate", "func": func,
            "needs_ssh": True, "needs_re": False}


def _snip_exit_code(expr):
    """exit_code: the pipeline's recorded exit code equals an int target."""
    target = int(expr)
    func = (
        'def check_exit_code(ctx):\n'
        '    """PASS iff the pipeline exit code equals {target}."""\n'
        '    return (ctx.exit_code == {target}, str(ctx.exit_code))'
    ).format(target=target)
    return {"name": "check_exit_code", "func": func,
            "needs_ssh": False, "needs_re": False}


def _snip_no_error(expr):
    """no_error: exit code 0 and no 'ERROR' line in stderr."""
    func = (
        'def check_no_error(ctx):\n'
        '    """PASS iff exit code 0 and no \'ERROR\' in stderr."""\n'
        '    has_error = "ERROR" in (ctx.stderr or "")\n'
        '    passed = (ctx.exit_code == 0) and not has_error\n'
        '    return (passed, "exit_code={0}, error_in_stderr={1}".format(\n'
        '        ctx.exit_code, has_error))'
    )
    return {"name": "check_no_error", "func": func,
            "needs_ssh": False, "needs_re": False}


def _snip_file_exists(expr):
    """file_exists: remote `test -f <output>`."""
    func = (
        'def check_file_exists(ctx):\n'
        '    """PASS iff the remote output file exists."""\n'
        '    rc, out, _ = _ssh_capture(\n'
        '        ctx.ip, "test -f " + shlex.quote(OUTPUT_FILE) + " && echo EXISTS")\n'
        '    exists = rc == 0 and "EXISTS" in out\n'
        '    return (exists, "exists" if exists else "missing")'
    )
    return {"name": "check_file_exists", "func": func,
            "needs_ssh": True, "needs_re": False}


def _snip_file_size(expr):
    """file_size: remote byte size within 'min:max' (or a threshold)."""
    compare = _emit_numeric_compare(expr, "size")
    func = (
        'def check_file_size(ctx):\n'
        '    """PASS iff the remote file size (bytes) satisfies the spec range."""\n'
        '    rc, out, _ = _ssh_capture(ctx.ip, "stat -c %s " + shlex.quote(OUTPUT_FILE))\n'
        '    if rc != 0 or not out.strip():\n'
        '        return (False, "unavailable")\n'
        '    try:\n'
        '        size = int(out.strip().splitlines()[0].strip())\n'
        '    except (ValueError, IndexError):\n'
        '        return (False, "unavailable")\n'
        '    return ({compare}, str(size))'
    ).format(compare=compare)
    return {"name": "check_file_size", "func": func,
            "needs_ssh": True, "needs_re": False}


def _snip_stdout_match(expr):
    """stdout_match: a regex is found in stdout."""
    func = (
        'def check_stdout_match(ctx):\n'
        '    """PASS iff the regex is found in stdout."""\n'
        '    found = re.search({pattern}, ctx.stdout or "") is not None\n'
        '    return (found, "matched" if found else "no match")'
    ).format(pattern=repr(expr))
    return {"name": "check_stdout_match", "func": func,
            "needs_ssh": False, "needs_re": True}


def _snip_fps(expr):
    """fps: parse an fps value from the logs and compare to a threshold."""
    compare = _emit_numeric_compare(expr, "actual")
    func = (
        'def check_fps(ctx):\n'
        '    """Parse fps from stdout+stderr and compare to the spec threshold."""\n'
        '    text = (ctx.stdout or "") + "\\n" + (ctx.stderr or "")\n'
        r'    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*fps", text)'
        '\n'
        '    if not match:\n'
        r'        match = re.search(r"(?:current|average|rate)[^0-9]*([0-9]+(?:\.[0-9]+)?)", text)'
        '\n'
        '    if not match:\n'
        '        return (False, "unavailable")\n'
        '    actual = float(match.group(1))\n'
        '    return ({compare}, str(actual))'
    ).format(compare=compare)
    return {"name": "check_fps", "func": func,
            "needs_ssh": False, "needs_re": True}


# key -> builder. THIS is where you add a new metric (see module docstring).
SNIPPET_BUILDERS = {
    "width": _snip_width,
    "height": _snip_height,
    "bitrate": _snip_bitrate,
    "exit_code": _snip_exit_code,
    "no_error": _snip_no_error,
    "file_exists": _snip_file_exists,
    "file_size": _snip_file_size,
    "stdout_match": _snip_stdout_match,
    "fps": _snip_fps,
}


def _snip_unknown(key):
    """Fallback for a spec key with no builder: always FAILs, visibly."""
    name = "check_" + re.sub(r"\W", "_", key)
    func = (
        'def {name}(ctx):\n'
        '    """Unsupported check key {key!r} - add a builder in generate_suite.py."""\n'
        '    return (False, "unsupported check key: {key}")'
    ).format(name=name, key=key)
    return {"name": name, "func": func, "needs_ssh": False, "needs_re": False}


# ===========================================================================
# Inlined SSH helper (emitted into check_output.py only when a check needs it)
# ===========================================================================

_SSH_HELPER = (
    'def _ssh_capture(ip, cmd, timeout=30):\n'
    '    """Run a command on root@192.168.5.<ip> over SSH.\n'
    '\n'
    '    Returns (returncode, stdout, stderr). Never raises: a missing ssh binary\n'
    '    yields 127, a timeout yields 124.\n'
    '    """\n'
    '    args = ["ssh", "root@192.168.5.{0}".format(ip), cmd]\n'
    '    try:\n'
    '        proc = subprocess.run(args, capture_output=True, text=True,\n'
    '                              timeout=timeout)\n'
    '        return (proc.returncode, proc.stdout or "", proc.stderr or "")\n'
    '    except FileNotFoundError:\n'
    '        return (127, "", "ssh not found")\n'
    '    except subprocess.TimeoutExpired:\n'
    '        return (124, "", "ssh timeout")\n'
    '\n'
    '\n'
)


# ===========================================================================
# Per-test script templates (sentinel @@TOKEN@@ substitution, never .format)
# ===========================================================================

RUN_TEST_TEMPLATE = r'''#!/usr/bin/env bash
# run_test.sh - @@SUITE@@/@@TEST_CASE@@
#
# GENERATED by generate_suite.py - do not edit by hand.
# To change it, edit specs/test_spec.xlsx and re-run generate_suite.py.
#
# Runs this test's GStreamer pipeline on the remote target over SSH and records
# the outcome under results/@@SUITE@@/@@TEST_CASE@@/.
set -euo pipefail

# Resolve paths from this script's own location (never hardcode).
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"     # tests/<suite>/<test_case> -> ROOT
RESULT_DIR="${ROOT}/results/@@SUITE@@/@@TEST_CASE@@"

# Require the remote octet as the first argument: root@192.168.5.<IP>.
if [[ $# -lt 1 || -z "${1:-}" ]]; then
    echo "Usage: $0 <IP>   (last octet of 192.168.5.<IP>)" >&2
    exit 2
fi
IP="$1"

# The pipeline and timeout for THIS test, inlined from the spec.
TIMEOUT_SEC=@@TIMEOUT@@
PIPELINE=@@PIPELINE_QUOTED@@

mkdir -p "${RESULT_DIR}"

# Run the pipeline on the remote with a timeout wrapper. 'timeout' exits 124 on
# timeout; disable 'set -e' around the call so we can record the exit code.
START="${SECONDS}"
set +e
timeout "${TIMEOUT_SEC}" ssh "root@192.168.5.${IP}" "${PIPELINE}" \
    > "${RESULT_DIR}/stdout.log" \
    2> "${RESULT_DIR}/stderr.log"
EXIT_CODE="$?"
set -e
DURATION=$(( SECONDS - START ))

# Record runtime artifacts for check_output.py.
echo "${EXIT_CODE}" > "${RESULT_DIR}/exit_code.txt"   # 124 == timeout
echo "${IP}"        > "${RESULT_DIR}/ip.txt"
echo "${DURATION}"  > "${RESULT_DIR}/duration.txt"

# One-line status (whether the pipeline ran; correctness is check_output.py's job).
if   [[ "${EXIT_CODE}" -eq 124 ]]; then STATUS="TIMEOUT"
elif [[ "${EXIT_CODE}" -eq 0   ]]; then STATUS="PASS"
else                                     STATUS="FAIL"
fi
echo "[${STATUS}] @@TEST_CASE@@ (${DURATION}s)"
exit 0
'''


CHECK_OUTPUT_TEMPLATE = r'''#!/usr/bin/env python3
"""check_output.py - @@SUITE@@/@@TEST_CASE@@

GENERATED by generate_suite.py - do not edit by hand.
To change it, edit specs/test_spec.xlsx and re-run generate_suite.py.

Validates this test's output against its expected value(s) and writes
results/@@SUITE@@/@@TEST_CASE@@/check_result.json (status PASS / FAIL / TIMEOUT).
Run it standalone (``python3 check_output.py [IP]``) or from check_all_outputs.sh.
"""
@@IMPORTS@@

# tests/<suite>/<test_case>/check_output.py -> ROOT is four directory levels up.
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
SUITE = @@SUITE_REPR@@
TEST_CASE = @@TEST_CASE_REPR@@
RESULT_DIR = os.path.join(ROOT, "results", SUITE, TEST_CASE)

# Everything below is inlined from the spec for THIS test.
OUTPUT_FILE = @@OUTPUT_FILE@@
INPUT_FILE = @@INPUT_FILE@@
OUTPUT_TYPE = @@OUTPUT_TYPE@@
EXPECTED_VALUE = @@EXPECTED_VAL@@
EXPECTED_FOR = @@EXPECTED_MAP@@


def _read(name, default=""):
    """Read results/<suite>/<test_case>/<name>, or return default."""
    try:
        with open(os.path.join(RESULT_DIR, name), encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return default


def _exit_code():
    """The pipeline's recorded exit code as int, or None if unavailable."""
    try:
        return int(_read("exit_code.txt").strip())
    except ValueError:
        return None


def _duration():
    """The recorded wall-clock duration in seconds, or 0.0."""
    try:
        return float(_read("duration.txt").strip())
    except ValueError:
        return 0.0


def _resolve_ip():
    """Remote octet: a command-line arg wins, else the ip.txt run_test.sh wrote."""
    if len(sys.argv) > 1 and sys.argv[1].strip():
        return sys.argv[1].strip()
    return _read("ip.txt").strip()


@@SSH_HELPER@@class Ctx:
    """Everything a checker might need, gathered once."""

    def __init__(self, ip, exit_code, stdout, stderr):
        self.ip = ip
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr


@@CHECK_FUNCS@@


# (key, checker) pairs for THIS test, in spec order.
CHECKS = [
@@CHECK_LIST@@
]


def main():
    ctx = Ctx(
        ip=_resolve_ip(),
        exit_code=_exit_code(),
        stdout=_read("stdout.log"),
        stderr=_read("stderr.log"),
    )

    # exit_code 124 means run_test.sh's `timeout` killed the pipeline.
    if ctx.exit_code == 124:
        status = "TIMEOUT"
        checks = []
        actual_summary = "timeout"
    else:
        checks = []
        parts = []
        all_passed = True
        for key, checker in CHECKS:
            passed, actual = checker(ctx)
            checks.append({
                "key": key,
                "status": "PASS" if passed else "FAIL",
                "expected": EXPECTED_FOR.get(key, ""),
                "actual": actual,
            })
            parts.append("{0}={1}".format(key, actual))
            all_passed = all_passed and passed
        status = "PASS" if all_passed else "FAIL"
        actual_summary = ", ".join(parts)

    result = {
        "test_case": TEST_CASE,
        "suite": SUITE,
        "status": status,
        "output_type": OUTPUT_TYPE,
        "expected": EXPECTED_VALUE,
        "actual": actual_summary,
        "duration_sec": _duration(),
        "checks": checks,
    }

    os.makedirs(RESULT_DIR, exist_ok=True)
    with open(os.path.join(RESULT_DIR, "check_result.json"), "w",
              encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, ensure_ascii=False)

    print("{0}/{1} -> {2}".format(SUITE, TEST_CASE, status), file=sys.stderr)
    return 0 if status in ("PASS", "TIMEOUT", "SKIPPED") else 1


if __name__ == "__main__":
    sys.exit(main())
'''


# ===========================================================================
# Assemblers
# ===========================================================================

def build_run_test(test):
    """Render a self-contained run_test.sh for one test."""
    body = RUN_TEST_TEMPLATE
    replacements = {
        "@@SUITE@@": test["suite"],
        "@@TEST_CASE@@": test["test_case"],
        "@@TIMEOUT@@": str(int(test["timeout_sec"])),
        # shlex.quote yields a safe single-quoted bash literal (handles quotes,
        # spaces, ';', '$', etc.) without altering the pipeline's content.
        "@@PIPELINE_QUOTED@@": shlex.quote(test["pipeline"]),
    }
    for token, value in replacements.items():
        body = body.replace(token, value)
    return body


def build_check_output(test):
    """Render a self-contained check_output.py for one test."""
    expected_map = parse_expected(test["output_type"], test["expected_value"])

    funcs = []
    call_lines = []
    needs_ssh = False
    needs_re = False
    for key, expr in expected_map.items():
        builder = SNIPPET_BUILDERS.get(key)
        snippet = builder(expr) if builder is not None else _snip_unknown(key)
        funcs.append(snippet["func"])
        call_lines.append('    ("{0}", {1}),'.format(key, snippet["name"]))
        needs_ssh = needs_ssh or snippet["needs_ssh"]
        needs_re = needs_re or snippet["needs_re"]

    imports = ["import os", "import sys", "import json"]
    if needs_re:
        imports.append("import re")
    if needs_ssh:
        imports.append("import shlex")
        imports.append("import subprocess")

    body = CHECK_OUTPUT_TEMPLATE
    replacements = {
        "@@SUITE_REPR@@": repr(test["suite"]),
        "@@TEST_CASE_REPR@@": repr(test["test_case"]),
        "@@SUITE@@": test["suite"],
        "@@TEST_CASE@@": test["test_case"],
        "@@OUTPUT_FILE@@": repr(test["output_file"]),
        "@@INPUT_FILE@@": repr(test["input_file"]),
        "@@OUTPUT_TYPE@@": repr(test["output_type"]),
        "@@EXPECTED_VAL@@": repr(test["expected_value"]),
        "@@EXPECTED_MAP@@": repr(expected_map),
        "@@IMPORTS@@": "\n".join(imports),
        "@@SSH_HELPER@@": _SSH_HELPER if needs_ssh else "",
        "@@CHECK_FUNCS@@": "\n\n\n".join(funcs),
        "@@CHECK_LIST@@": "\n".join(call_lines),
    }
    for token, value in replacements.items():
        body = body.replace(token, value)
    return body


# ===========================================================================
# Writing
# ===========================================================================

def _write_file(path, content, executable=False, quiet=False):
    """Write content as UTF-8 with LF newlines; chmod +x on POSIX if asked.

    newline='\n' forces LF even on Windows so the generated .sh files stay
    POSIX-clean on the Linux target.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
    if executable and os.name == "posix":
        try:
            os.chmod(path, 0o755)
        except OSError:
            pass
    log_info("wrote {0}".format(path), quiet=quiet)


def write_config(tests, quiet=False):
    """Write config/tests.json (the index the runners and report consume)."""
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    payload = [{
        "suite": t["suite"],
        "test_case": t["test_case"],
        "description": t["description"],
        "pipeline": t["pipeline"],
        "output_type": t["output_type"],
        "expected_value": t["expected_value"],
        "timeout_sec": t["timeout_sec"],
        "enabled": t["enabled"],
        "input_file": t["input_file"],
        "output_file": t["output_file"],
    } for t in tests]
    with open(CONFIG_PATH, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    log_info("wrote {0} ({1} tests)".format(CONFIG_PATH, len(payload)),
             quiet=quiet)


def _clean_stale_dirs(tests, quiet=False):
    """Remove generated test dirs no longer present in the spec.

    Only directories that look generated (contain run_test.sh or check_output.py)
    are removed, so an unrelated folder under tests/ is never touched. Empty
    suite directories are pruned afterwards.
    """
    if not os.path.isdir(TESTS_DIR):
        return
    wanted = {(t["suite"], t["test_case"]) for t in tests}
    for suite in sorted(os.listdir(TESTS_DIR)):
        suite_dir = os.path.join(TESTS_DIR, suite)
        if not os.path.isdir(suite_dir):
            continue
        for test_case in sorted(os.listdir(suite_dir)):
            test_dir = os.path.join(suite_dir, test_case)
            if not os.path.isdir(test_dir):
                continue
            looks_generated = os.path.exists(
                os.path.join(test_dir, "run_test.sh")
            ) or os.path.exists(os.path.join(test_dir, "check_output.py"))
            if (suite, test_case) not in wanted and looks_generated:
                shutil.rmtree(test_dir)
                log_warn("removed stale test dir {0}".format(test_dir),
                         quiet=quiet)
        # Prune a now-empty suite directory.
        if os.path.isdir(suite_dir) and not os.listdir(suite_dir):
            os.rmdir(suite_dir)


def emit_per_test_files(tests, quiet=False):
    """Emit run_test.sh + check_output.py for every test, then drop stale dirs."""
    for test in tests:
        test_dir = os.path.join(TESTS_DIR, test["suite"], test["test_case"])
        _write_file(os.path.join(test_dir, "run_test.sh"),
                    build_run_test(test), executable=True, quiet=quiet)
        _write_file(os.path.join(test_dir, "check_output.py"),
                    build_check_output(test), executable=True, quiet=quiet)
    _clean_stale_dirs(tests, quiet=quiet)


# ===========================================================================
# Main
# ===========================================================================

def generate(spec_path=SPEC_PATH, quiet=False):
    """Load the spec, write config/tests.json, emit all per-test scripts."""
    log_info("loading spec {0}".format(spec_path), quiet=quiet)
    tests = load_spec(spec_path)
    if not tests:
        log_warn("spec yielded zero tests", quiet=quiet)
    write_config(tests, quiet=quiet)
    emit_per_test_files(tests, quiet=quiet)
    suite_count = len({t["suite"] for t in tests})
    log_info(
        "generation complete: {0} tests across {1} suite(s)".format(
            len(tests), suite_count),
        quiet=quiet,
    )
    return tests


def _self_check():
    """Quick offline sanity checks on the parsing + emission logic."""
    assert parse_expected("fps", ">=24.0") == {"fps": ">=24.0"}
    assert parse_expected("exit_code", "0") == {"exit_code": "0"}
    assert parse_expected(
        "width;height;bitrate",
        "width=1920; height=1080; bitrate=900000:1100000",
    ) == {"width": "1920", "height": "1080", "bitrate": "900000:1100000"}
    assert _emit_numeric_compare("900000:1100000") == "900000.0 <= actual <= 1100000.0"
    assert _emit_numeric_compare(">=24.0") == "actual >= 24.0"

    sample = {
        "suite": "S", "test_case": "t1",
        "pipeline": "gst-launch-1.0 videotestsrc ! filesink location=o.264",
        "output_type": "width;height;bitrate",
        "expected_value": "width=1920; height=1080; bitrate=900000:1100000",
        "timeout_sec": 30, "enabled": True,
        "input_file": None, "output_file": "o.264",
    }
    run_sh = build_run_test(sample)
    assert "PIPELINE='gst-launch-1.0 videotestsrc ! filesink location=o.264'" in run_sh
    chk = build_check_output(sample)
    compile(chk, "<emitted check_output.py>", "exec")  # must be valid Python
    assert "actual == 1920" in chk and "900000.0 <= actual <= 1100000.0" in chk
    assert "import gst_utils" not in chk
    print("self-check OK")


def _parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Generate the GStreamer QA test suite from the spec.")
    parser.add_argument(
        "--spec", default=SPEC_PATH,
        help="path to the spec workbook (default: specs/test_spec.xlsx)")
    parser.add_argument(
        "--quiet", action="store_true", help="suppress informational logging")
    parser.add_argument(
        "--self-check", action="store_true",
        help="run offline sanity checks and exit (no spec needed)")
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args(sys.argv[1:])
    if args.self_check:
        _self_check()
        sys.exit(0)
    generate(spec_path=args.spec, quiet=args.quiet)
