#!/usr/bin/env bash
set -euo pipefail
# check_all_outputs.sh - run every per-test check_output.py and tally verdicts.
#
# Usage:
#   bash check_all_outputs.sh [<IP>] [--suite <suite>] [--id <test_case>] \
#       [--quiet] [--jobs N]
#
# A check only reads the already-captured logs and runs lightweight
# ffprobe/ffmpeg probes on the remote (it does NOT start a GStreamer pipeline),
# so checks are safe to run concurrently. Up to N checks (default 8) run in
# parallel on this host; use --jobs 1 to force sequential checking.
#
# check_output.py exits 0 for PASS (TIMEOUT/SKIPPED also exit 0) and non-zero
# for FAIL. The remote octet is optional here but, when given, is forwarded to
# each check_output.py (file-based metrics need it).

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
Usage: bash $(basename "$0") [<IP>] [--suite <suite>] [--id <test_case>] [--quiet] [--jobs N]

  <IP>            Last octet of the remote target (root@192.168.5.<IP>). Optional
                  here, but required for file-based metric checks; forwarded to
                  each check_output.py when supplied.
  --suite <name>  Only check tests belonging to this suite.
  --id <test_case>  Only check this single test id.
  --quiet         Suppress per-test progress logging (summary still printed).
  --jobs N        Max checks to run in parallel (default 8; use 1 for sequential).
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

# Validate --jobs is a positive integer.
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

# --- Build the worklist (ALL tests, enabled or not) --------------------------
# Checks apply to every test directory; honor only the optional filters.
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
    log_warn "no tests matched the given filters; nothing to check"
    echo "[0/0] Checks done — 0 PASS, 0 FAIL"
    exit 0
fi

# Scratch directory to collect one verdict file per test (PASS|FAIL).
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT

# wait_for_slot - block until fewer than MAX_JOBS background checks are running.
wait_for_slot() {
    while [[ "$(jobs -rp | wc -l)" -ge "${MAX_JOBS}" ]]; do
        wait -n
    done
}

# run_check INDEX SUITE TEST_CASE - run one check_output.py and write its verdict
# (PASS|FAIL) to TMP_DIR/INDEX. Designed to be launched in the background.
run_check() {
    local index="$1"
    local suite="$2"
    local test_case="$3"
    local check_script="${TESTS_DIR}/${suite}/${test_case}/check_output.py"
    local rc=0

    if [[ ! -f "${check_script}" ]]; then
        log_error "missing check_output.py for ${suite}/${test_case}"
        echo "FAIL" > "${TMP_DIR}/${index}"
        return 0
    fi

    set +e
    if [[ -n "${IP}" ]]; then
        python3 "${check_script}" "${IP}" >/dev/null 2>&1
    else
        python3 "${check_script}" >/dev/null 2>&1
    fi
    rc=$?
    set -e

    if [[ "${rc}" -eq 0 ]]; then
        echo "PASS" > "${TMP_DIR}/${index}"
    else
        echo "FAIL" > "${TMP_DIR}/${index}"
    fi
    return 0
}

# --- Launch checks (up to MAX_JOBS in parallel) ------------------------------
INDEX=0
for entry in "${WORKLIST[@]}"; do
    INDEX=$((INDEX + 1))
    suite="${entry%%$'\t'*}"
    test_case="${entry##*$'\t'}"
    say "[${INDEX}/${TOTAL}] Checking ${test_case}..."
    wait_for_slot
    run_check "${INDEX}" "${suite}" "${test_case}" &
done

# Wait for all background checks to finish.
wait

# --- Tally verdicts ----------------------------------------------------------
PASS=0
FAIL=0
for verdict_file in "${TMP_DIR}"/*; do
    if [[ ! -f "${verdict_file}" ]]; then
        continue
    fi
    verdict="$(cat "${verdict_file}")"
    if [[ "${verdict}" == "PASS" ]]; then
        PASS=$((PASS + 1))
    else
        FAIL=$((FAIL + 1))
    fi
done

echo "[${TOTAL}/${TOTAL}] Checks done — ${PASS} PASS, ${FAIL} FAIL"

if [[ "${FAIL}" -gt 0 ]]; then
    exit 1
fi
exit 0
