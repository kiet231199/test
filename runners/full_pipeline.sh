#!/usr/bin/env bash
set -euo pipefail
# full_pipeline.sh - one-shot orchestration for the whole suite.
#
# Usage:
#   bash full_pipeline.sh <IP> [--suite <suite>] [--id <test_case>] [--quiet] [--jobs N]
#
# What it does, for every ENABLED test:
#   1. Runs the GStreamer pipeline on the remote (run_test.sh), STRICTLY ONE AT
#      A TIME - the remote can only run one pipeline at a time.
#   2. As soon as a pipeline finishes, launches that test's check_output.py in
#      the BACKGROUND so checking overlaps with the next pipeline run. Up to
#      --jobs checks (default 8) run at once.
# After every pipeline has run and every check has finished, it generates the
# Excel report (report/generate_report.py).
#
# This is the only stage that runs pipelines and checks together; run the
# pipelines and the checks separately with run_all_tests.sh / check_all_outputs.sh
# if you prefer.

# --- Resolve paths from this script's own location (never hardcode) ----------
SCRIPT_DIR="$(cd "$(dirname "$0")" ; pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." ; pwd)"
TESTS_DIR="${ROOT_DIR}/tests"
CONFIG_JSON="${ROOT_DIR}/config/tests.json"
REPORT_PY="${ROOT_DIR}/report/generate_report.py"

# --- Logging (timestamped; colored only when stderr is a terminal) -----------
if [[ -t 2 ]]; then
    _C_G=$'\033[32m'; _C_Y=$'\033[33m'; _C_R=$'\033[31m'; _C_0=$'\033[0m'
else
    _C_G=""; _C_Y=""; _C_R=""; _C_0=""
fi
_ts() { date "+%Y-%m-%d %H:%M:%S"; }
log_info()  { printf '%s[%s] [INFO] %s%s\n'  "${_C_G}" "$(_ts)" "$*" "${_C_0}" >&2; }
log_warn()  { printf '%s[%s] [WARN] %s%s\n'  "${_C_Y}" "$(_ts)" "$*" "${_C_0}" >&2; }
log_error() { printf '%s[%s] [ERROR] %s%s\n' "${_C_R}" "$(_ts)" "$*" "${_C_0}" >&2; }

# --- Usage -------------------------------------------------------------------
usage() {
    cat >&2 <<EOF
Usage: bash $(basename "$0") <IP> [--suite <suite>] [--id <test_case>] [--quiet] [--jobs N]

  <IP>            Last octet of the remote target (root@192.168.5.<IP>). Required.
  --suite <name>  Only process tests belonging to this suite.
  --id <test_case>  Only process this single test id.
  --quiet         Suppress per-test progress logging (summary still printed).
  --jobs N        Max output checks to run in parallel (default 8).
EOF
    exit 2
}

# --- Argument parsing --------------------------------------------------------
IP=""
FILTER_SUITE=""
FILTER_ID=""
QUIET=0
MAX_JOBS=8

while [[ $# -gt 0 ]]; do
    case "$1" in
        --suite)
            FILTER_SUITE="${2:-}"
            if [[ -z "${FILTER_SUITE}" ]]; then
                usage
            fi
            shift 2
            ;;
        --id)
            FILTER_ID="${2:-}"
            if [[ -z "${FILTER_ID}" ]]; then
                usage
            fi
            shift 2
            ;;
        --quiet)
            QUIET=1
            shift
            ;;
        --jobs)
            MAX_JOBS="${2:-}"
            if [[ -z "${MAX_JOBS}" ]]; then
                usage
            fi
            shift 2
            ;;
        -h|--help)
            usage
            ;;
        -*)
            log_error "unknown option: $1"
            usage
            ;;
        *)
            if [[ -z "${IP}" ]]; then
                IP="$1"
                shift
            else
                log_error "unexpected positional argument: $1"
                usage
            fi
            ;;
    esac
done

if [[ -z "${IP}" ]]; then
    log_error "missing required <IP> argument"
    usage
fi

if ! [[ "${MAX_JOBS}" =~ ^[0-9]+$ ]]; then
    log_error "--jobs must be a positive integer (got: ${MAX_JOBS})"
    usage
fi
if [[ "${MAX_JOBS}" -lt 1 ]]; then
    log_error "--jobs must be at least 1"
    usage
fi

if [[ ! -f "${CONFIG_JSON}" ]]; then
    log_error "config not found: ${CONFIG_JSON} (run generate_suite.py first)"
    exit 1
fi

# say - print a progress line only when not in quiet mode.
say() {
    if [[ "${QUIET}" -eq 0 ]]; then
        log_info "$@"
    fi
}

# --- Build the worklist from config/tests.json (enabled tests only) ----------
mapfile -t WORKLIST < <(
    python3 - "${CONFIG_JSON}" "${FILTER_SUITE}" "${FILTER_ID}" <<'PY'
import json
import sys

# Force LF endings so a trailing CR never becomes part of a test_case.
try:
    sys.stdout.reconfigure(newline="\n")
except AttributeError:
    pass

config_path, filter_suite, filter_id = sys.argv[1], sys.argv[2], sys.argv[3]
with open(config_path, "r", encoding="utf-8") as handle:
    tests = json.load(handle)

for test in tests:
    if not test.get("enabled", True):
        continue
    suite = str(test.get("suite", "")).strip()
    test_case = str(test.get("test_case", "")).strip()
    if not suite or not test_case:
        continue
    if filter_suite and suite != filter_suite:
        continue
    if filter_id and test_case != filter_id:
        continue
    print("{}\t{}".format(suite, test_case))
PY
)

TOTAL="${#WORKLIST[@]}"
if [[ "${TOTAL}" -eq 0 ]]; then
    log_warn "no enabled tests matched the given filters; nothing to do"
    exit 0
fi

# wait_for_slot - block until fewer than MAX_JOBS background checks are running.
wait_for_slot() {
    while [[ "$(jobs -rp | wc -l)" -ge "${MAX_JOBS}" ]]; do
        wait -n
    done
}

# launch_check SUITE TEST_CASE - run check_output.py in the background. Its own
# check_result.json is the source of truth for the final tally, so we do not
# need to capture anything here.
launch_check() {
    local suite="$1"
    local test_case="$2"
    local check_script="${TESTS_DIR}/${suite}/${test_case}/check_output.py"

    if [[ ! -f "${check_script}" ]]; then
        log_error "missing check_output.py for ${suite}/${test_case}"
        return 0
    fi

    set +e
    python3 "${check_script}" "${IP}" >/dev/null 2>&1
    set -e
    return 0
}

# --- Stage 1+2: run pipelines sequentially, check outputs in the background --
say "stage 1+2: running ${TOTAL} pipeline(s) sequentially; checks overlap in background"
INDEX=0
for entry in "${WORKLIST[@]}"; do
    INDEX=$((INDEX + 1))
    suite="${entry%%$'\t'*}"
    test_case="${entry##*$'\t'}"
    run_script="${TESTS_DIR}/${suite}/${test_case}/run_test.sh"

    say "[${INDEX}/${TOTAL}] Running ${test_case}..."

    if [[ ! -f "${run_script}" ]]; then
        log_error "missing run_test.sh for ${suite}/${test_case}"
        continue
    fi

    # Run this pipeline (blocking - only one pipeline runs at a time).
    set +e
    bash "${run_script}" "${IP}"
    set -e

    # Launch this test's check in the background so it overlaps with the next
    # pipeline run, respecting the parallel-check cap.
    wait_for_slot
    launch_check "${suite}" "${test_case}" &
done

# Wait for all background checks to finish before reporting.
wait

# --- Stage 3: generate the report --------------------------------------------
say "stage 3: generating report"
report_rc=0
if [[ -f "${REPORT_PY}" ]]; then
    set +e
    python3 "${REPORT_PY}"
    report_rc=$?
    set -e
    if [[ "${report_rc}" -ne 0 ]]; then
        log_error "report generation failed (rc=${report_rc})"
    fi
else
    log_warn "report generator not found: ${REPORT_PY} (skipping report stage)"
fi

# --- Final one-line summary (read from each test's check_result.json) --------
python3 - "${CONFIG_JSON}" "${ROOT_DIR}" <<'PY'
import json
import os
import sys

config_path, root_dir = sys.argv[1], sys.argv[2]
with open(config_path, "r", encoding="utf-8") as handle:
    tests = json.load(handle)

counts = {"PASS": 0, "FAIL": 0, "TIMEOUT": 0, "SKIPPED": 0, "NOT_RUN": 0}
for test in tests:
    if not test.get("enabled", True):
        counts["SKIPPED"] += 1
        continue
    suite = test.get("suite", "")
    test_case = test.get("test_case", "")
    path = os.path.join(root_dir, "results", suite, test_case, "check_result.json")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            status = json.load(handle).get("status", "NOT_RUN")
    except (OSError, ValueError):
        status = "NOT_RUN"
    if status not in counts:
        status = "NOT_RUN"
    counts[status] += 1

print(
    "Summary — {PASS} PASS, {FAIL} FAIL, {TIMEOUT} TIMEOUT, "
    "{SKIPPED} SKIPPED, {NOT_RUN} NOT_RUN".format(**counts)
)
PY

if [[ "${report_rc}" -ne 0 ]]; then
    exit 1
fi
exit 0
