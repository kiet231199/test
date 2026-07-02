#!/usr/bin/env bash
set -euo pipefail
# run_all_tests.sh - discover and execute every enabled test's run_test.sh on a
# remote target over SSH, ONE AT A TIME (sequential).
#
# Usage:
#   bash run_all_tests.sh <IP> [--suite <suite>] [--id <test_case>] [--quiet]
#
# <IP> is the last octet of the remote address (root@192.168.5.<IP>); it is
# forwarded verbatim to each tests/<suite>/<id>/run_test.sh. Only tests whose
# `enabled` flag in config/tests.json is true are run.
#
# Pipelines are ALWAYS run sequentially: the remote target can only run one
# GStreamer pipeline at a time, so there is intentionally no parallel mode.

# --- Resolve paths from this script's own location (never hardcode) ----------
SCRIPT_DIR="$(cd "$(dirname "$0")" ; pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." ; pwd)"
TESTS_DIR="${ROOT_DIR}/tests"
CONFIG_JSON="${ROOT_DIR}/config/tests.json"

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
Usage: bash $(basename "$0") <IP> [--suite <suite>] [--id <test_case>] [--quiet]

  <IP>            Last octet of the remote target (root@192.168.5.<IP>). Required.
  --suite <name>  Only run tests belonging to this suite.
  --id <test_case>  Only run this single test id.
  --quiet         Suppress per-test progress logging (summary still printed).
EOF
    exit 2
}

# --- Argument parsing --------------------------------------------------------
IP=""
FILTER_SUITE=""
FILTER_ID=""
QUIET=0

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
# Python (stdlib only) emits one TAB-separated "suite<TAB>test_case" line per
# enabled test that matches the optional --suite / --id filters.
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
    log_warn "no enabled tests matched the given filters; nothing to run"
    echo "Completed: 0 tests — 0 PASS, 0 FAIL, 0 TIMEOUT"
    exit 0
fi

say "running ${TOTAL} enabled test(s) sequentially against remote 192.168.5.${IP}"

# classify_run SUITE TEST_CASE - echo PASS|FAIL|TIMEOUT from the recorded
# exit_code.txt (124 == timeout). FAIL when the file is missing.
classify_run() {
    local suite="$1"
    local test_case="$2"
    local exit_file="${ROOT_DIR}/results/${suite}/${test_case}/exit_code.txt"

    if [[ ! -f "${exit_file}" ]]; then
        echo "FAIL"
        return 0
    fi

    local pipeline_rc
    pipeline_rc="$(tr -d '[:space:]' < "${exit_file}")"

    if [[ "${pipeline_rc}" == "124" ]]; then
        echo "TIMEOUT"
    elif [[ "${pipeline_rc}" == "0" ]]; then
        echo "PASS"
    else
        echo "FAIL"
    fi
    return 0
}

# --- Execute every test sequentially -----------------------------------------
PASS=0
FAIL=0
TIMEOUT=0
INDEX=0

for entry in "${WORKLIST[@]}"; do
    INDEX=$((INDEX + 1))
    suite="${entry%%$'\t'*}"
    test_case="${entry##*$'\t'}"
    run_script="${TESTS_DIR}/${suite}/${test_case}/run_test.sh"

    say "[${INDEX}/${TOTAL}] Running ${test_case}..."

    if [[ ! -f "${run_script}" ]]; then
        log_error "missing run_test.sh for ${suite}/${test_case}"
        FAIL=$((FAIL + 1))
        continue
    fi

    # run_test.sh records the pipeline's exit code itself and returns 0; a
    # non-zero pipeline outcome is expected, so disable `set -e` just around the
    # call and re-enable it immediately after.
    set +e
    bash "${run_script}" "${IP}"
    set -e

    verdict="$(classify_run "${suite}" "${test_case}")"
    case "${verdict}" in
        PASS)
            PASS=$((PASS + 1))
            ;;
        TIMEOUT)
            TIMEOUT=$((TIMEOUT + 1))
            ;;
        *)
            FAIL=$((FAIL + 1))
            ;;
    esac
done

echo "Completed: ${TOTAL} tests — ${PASS} PASS, ${FAIL} FAIL, ${TIMEOUT} TIMEOUT"

# Non-zero overall exit if any test did not pass cleanly, so CI can react.
if [[ "${FAIL}" -gt 0 || "${TIMEOUT}" -gt 0 ]]; then
    exit 1
fi
exit 0
