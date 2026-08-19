#!/usr/bin/env python3
"""Generator for the wayfinder ticket-02 program-contract prototype.

Reads Specification.xlsx (dependency-free) and emits the real generated tree:

    runner.sh
    out/<suite>/<case>/script.sh
    out/<suite>/<case>/case.conf

Throwaway prototype: light validation only, single suite, mock board/check layer.
Run from this directory:

    python gen.py
    MOCK_BOARD=1 bash runner.sh          # on Linux / Git Bash
"""
import os
import re
import shlex
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import xlsxmini

SPEC = os.path.join(HERE, "Specification.xlsx")
DEFAULT_TIMEOUT = "120"

KNOWN_METRICS = {
    "return", "width", "height", "framerate", "level", "profile", "psnr",
    "codec", "bitrate", "gop", "interval-intraframe", "pframes", "bframes",
    "refframes", "frame_count", "scan_type", "crop",
}
NO_FILE_METRICS = {"return", "fps"}


def is_number(value):
    try:
        float(value)
        return True
    except ValueError:
        return False


def sanitize(name):
    name = re.sub(r'[\\/:"*?<>|]+', "_", str(name).strip())
    name = re.sub(r"\s+", "_", name)
    return name or "unknown"


def cell_text(value):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def parse_check_metrics(text):
    tokens = re.split(r"[;\n]+", cell_text(text))
    out = []
    for token in tokens:
        name = token.strip()
        if name and name not in out:
            out.append(name)
    return out


def compile_criteria(criteria):
    c = cell_text(criteria).strip()
    if not c:
        raise ValueError("empty criteria")

    interval = re.match(r"^([\[\(])\s*(.+?)\s*;\s*(.+?)\s*([\]\)])$", c)
    if interval:
        lower = interval.group(2)
        upper = interval.group(3)
        if not is_number(lower) or not is_number(upper):
            raise ValueError("interval bound not numeric: %r" % c)
        lop = ">=" if interval.group(1) == "[" else ">"
        uop = "<=" if interval.group(4) == "]" else "<"
        return "awk -v v=\"$value\" 'BEGIN{exit !(v%s%s && v%s%s)}'" % (lop, lower, uop, upper)

    comparison = re.match(r"^(>=|<=|>|<)\s*(.+)$", c)
    if comparison:
        bound = comparison.group(2)
        if not is_number(bound):
            raise ValueError("comparison bound not numeric: %r" % c)
        return "awk -v v=\"$value\" 'BEGIN{exit !(v%s%s)}'" % (comparison.group(1), bound)

    if is_number(c):
        return "awk -v v=\"$value\" 'BEGIN{exit !(v==%s)}'" % c

    return '[[ "$value" == "%s" ]]' % c.replace('"', '\\"')


def info_comments(columns, row, test_idx, check_idx):
    lines = []
    for col in columns[test_idx + 1:check_idx]:
        if col in ("Timeout", "Skip"):
            continue
        value = cell_text(row.get(col, "")).strip()
        if value == "":
            continue
        lines.append("#  %-18s %s" % (col, value))
    return "\n".join(lines)


SCRIPT_TEMPLATE = r'''#!/usr/bin/env bash
# PROTOTYPE - throwaway generated program (wayfinder ticket 02).
# Suite: %%SUITE%%   Case: %%CASE%%
%%INFO_COMMENTS%%

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CASE_DIR="$SCRIPT_DIR"
SUITE="$(basename "$(dirname "$CASE_DIR")")"
CASE="$(basename "$CASE_DIR")"
WORK_DIR="$(pwd)"

CONF="$CASE_DIR/case.conf"
TIMEOUT=120
SKIP_REASON=""
if [ -f "$CONF" ]; then
  # shellcheck disable=SC1090
  . "$CONF"
fi
TIMEOUT="${TIMEOUT:-120}"

DEBUG="${DEBUG:-0}"
MOCK_BOARD="${MOCK_BOARD:-0}"

# ---------------------------------------------------------------------------
# prerun(): board-side user snippet, embedded verbatim (decision Q19).
# ---------------------------------------------------------------------------
prerun() {
%%PRERUN_BODY%%
}

# ---------------------------------------------------------------------------
# precheck(): PC-side user snippet, embedded verbatim (decision Q19).
# ---------------------------------------------------------------------------
precheck() {
%%PRECHECK_BODY%%
}

# ---------------------------------------------------------------------------
# run(): executes on the board. Returns pipeline exit code in return.txt.
# ---------------------------------------------------------------------------
PIPELINE='%%PIPELINE%%'

run() {
  cd "$CASE_DIR" || return 2
  export WORK_DIR CASE_DIR DEBUG MOCK_BOARD
  export XDG_RUNTIME_DIR=/run
  export GST_DEBUG_NO_COLOR=1
  if [ "$DEBUG" = "1" ]; then export GST_DEBUG=1; fi

  echo "== prerun =="
  prerun
  rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "[PRERUN] failed rc=$rc" >&2
    echo "$rc" > return.txt
    echo "$rc"
    return 0
  fi

  echo "== pipeline (timeout=$TIMEOUT) =="
  if [ "$MOCK_BOARD" = "1" ]; then
    timeout --kill-after=5 "$TIMEOUT" bash "$WORK_DIR/mock/board.sh" "$SUITE" "$CASE"
    rc=$?
  else
    timeout --kill-after=5 "$TIMEOUT" bash -c "$PIPELINE"
    rc=$?
  fi
  echo "$rc" > return.txt
  echo "return.txt=$rc"
  return 0
}

# ---------------------------------------------------------------------------
# check(): executes on the LabPC. Prints OK/NG lines and final PASSED/FAILED.
# ---------------------------------------------------------------------------
check() {
  cd "$CASE_DIR" || return 2
  export WORK_DIR CASE_DIR DEBUG MOCK_BOARD

  echo "== precheck =="
  precheck
  rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "[PRECHECK] failed rc=$rc" >&2
    echo "FAILED"
    return 0
  fi

  # Per-case metric list from case.conf (CHECK_METRICS).
  check_list=()
  old_IFS="$IFS"; IFS=";"
  read -r -a check_list <<< "${CHECK_METRICS:-}"
  IFS="$old_IFS"

  media_metrics=()
  for m in "${check_list[@]}"; do
    m="${m// /}"
    [ -z "$m" ] && continue
    case "$m" in
      return|fps) ;;
      *) media_metrics+=("$m") ;;
    esac
  done

  if [ "${#media_metrics[@]}" -gt 0 ]; then
    output_file=""
    output_file=$(find "$CASE_DIR" -maxdepth 1 -type f -name 'output.*' 2>/dev/null)
    if [ "$(printf '%s\n' "$output_file" | grep -c .)" -ne 1 ]; then
      echo "[ERR] expected exactly one output.* in $CASE_DIR" >&2
      echo "FAILED"
      return 0
    fi

    reference_file=""
    for m in "${media_metrics[@]}"; do
      if [ "$m" = "psnr" ]; then
        reference_file=$(find "$CASE_DIR" -maxdepth 1 -type f -name 'reference.*' 2>/dev/null)
        if [ "$(printf '%s\n' "$reference_file" | grep -c .)" -ne 1 ]; then
          echo "[ERR] expected exactly one reference.* in $CASE_DIR" >&2
          echo "FAILED"
          return 0
        fi
      fi
    done

    if [ -n "$reference_file" ]; then
      cat > "$CASE_DIR/metrics.yaml" <<YAML
input:
  path: $output_file
reference:
  path: $reference_file
YAML
    else
      cat > "$CASE_DIR/metrics.yaml" <<YAML
input:
  path: $output_file
YAML
    fi

    if [ "$MOCK_BOARD" = "1" ]; then
      bash "$WORK_DIR/mock/media_check.sh" "$SUITE" "$CASE"
    else
      media-check --input "$CASE_DIR/metrics.yaml" --check "${media_metrics[@]}" --output "$CASE_DIR/result.yaml"
    fi
  fi

  result=PASSED
  for m in "${check_list[@]}"; do
    m="${m// /}"
    [ -z "$m" ] && continue
    check_one "$m"
  done

  echo "$result"
}

%%CHECK_ONE%%

"$1"
'''


RUNNER_TEMPLATE = r'''#!/usr/bin/env bash
# PROTOTYPE - throwaway. Generated runner for wayfinder ticket 02.
# Do not treat as production. Mock mode: MOCK_BOARD=1 bash runner.sh
set -u

PROGRAM_ROOT="$(cd "$(dirname "$0")" && pwd)"
OUT_DIR="$PROGRAM_ROOT/out"
LAB_WORK_DIR="$PROGRAM_ROOT"

# Generator -> runner: metric columns in workbook criteria-column order.
# This prototype has one suite, so it is a single program-level list.
METRICS=(%%METRIC_COLUMNS%%)

MOCK_BOARD="${MOCK_BOARD:-0}"

IP=0
SUITE_PICK=false
CASE_PICK=false
DEBUG=false
VERBOSE=false
RERUN=false
FORCE=false
LOG=false
TIDY=false

NUM_PASSED=0
NUM_FAILED=0
NUM_SKIPPED=0
LIST_FAILED=()
SELECTED_SUITES=()
SELECTED_CASES=()

# INT bookkeeping (prototype-grade process handling)
STOP=0
PHASE=none
CURRENT_SUITE=""
CURRENT_CASE=""

red=$'\e[0;31m'
yellow=$'\e[0;33m'
cyan=$'\e[0;36m'
nocolor=$'\e[0m'

die_usage() { printf '%s\n' "$1" >&2; exit 2; }

show_help() {
  cat <<HELP
Usage: runner.sh [options]

Mock demo on this machine:
  MOCK_BOARD=1 bash runner.sh

Options (legacy flag set):
  -h, --help             Show this help.
  -i, --ip <n>           Last octet of the target board (real mode only).
  -s, --suite <name...>  Run selected suite(s).
  -c, --case <name...>   Run selected case(s).
  -r, --rerun            Rerun FAILED cases.
  -f, --force            Rerun PASSED cases too.
  -d, --debug            GST_DEBUG=1 on board; keep output/reference files.
  -v, --verbose          Echo the full log/result streams.
  -l, --log [case...]    Show logs/results (do not run).
  -t, --tidy [case...]   Remove runtime artifacts and reset NOT RUN (do not run).
HELP
}

csv_quote() {
  local s="$1"
  case "$s" in
    *','*|*'"'*) printf '"%s"' "$(printf '%s' "$s" | sed 's/"/""/g')" ;;
    *) printf '%s' "$s" ;;
  esac
}

discover_suites() {
  local d
  SELECTED_SUITES=()
  for d in "$OUT_DIR"/*/; do
    [ -d "$d" ] || continue
    SELECTED_SUITES+=("$(basename "$d")")
  done
}

cases_for_suite() {
  local suite="$1" d
  for d in "$OUT_DIR/$suite"/*/; do
    [ -d "$d" ] && printf '%s\n' "$(basename "$d")"
  done
}

# case.conf interface (decision Q2): TIMEOUT= and SKIP_REASON=
get_conf() {
  local suite="$1" case="$2" conf="$OUT_DIR/$suite/$case/case.conf"
  TIMEOUT=120
  SKIP_REASON=""
  [ -f "$conf" ] || return 0
  # shellcheck disable=SC1090
  . "$conf"
  TIMEOUT="${TIMEOUT:-120}"
  SKIP_REASON="${SKIP_REASON:-}"
}

metric_value() {
  local suite="$1" case="$2" metric="$3" dir="$OUT_DIR/$suite/$case"
  case "$metric" in
    return)
      [ -f "$dir/return.txt" ] && cat "$dir/return.txt" 2>/dev/null || true
      ;;
    *)
      [ -f "$dir/result.yaml" ] && awk -F': ' -v m="$metric" '$1==m {print $2; exit}' "$dir/result.yaml" 2>/dev/null || true
      ;;
  esac
}

status_from_artifacts() {
  local suite="$1" case="$2" dir="$OUT_DIR/$suite/$case" last
  [ -f "$dir/.int" ] && { printf 'INT'; return 0; }
  if [ -f "$dir/result.txt" ]; then
    last="$(tr -d '\r' < "$dir/result.txt" | tail -n 1)"
    case "$last" in
      *PASSED*) printf 'PASSED' ;;
      *FAILED*) printf 'FAILED' ;;
      *) printf 'DONE' ;;
    esac
  elif [ -f "$dir/log.txt" ]; then
    printf 'DONE'
  else
    printf 'NOT RUN'
  fi
}

render_case_line() {
  local suite="$1" case="$2" status note="" v line="$case"
  get_conf "$suite" "$case"
  status="$(status_from_artifacts "$suite" "$case")"
  if [ -n "$SKIP_REASON" ]; then
    status="NOT RUN"
    note="SKIPPED: $SKIP_REASON"
  fi
  for m in "${METRICS[@]}"; do
    v="$(metric_value "$suite" "$case" "$m")"
    line="${line},$(csv_quote "$v")"
  done
  line="${line},${status},$(csv_quote "$note")"
  printf '%s\n' "$line"
}

rebuild_csv() {
  local suite="$1" f="$OUT_DIR/${suite}_result.csv" header="test case" m d case
  for m in "${METRICS[@]}"; do header="${header},${m}"; done
  header="${header},status,note"
  {
    printf '%s\n' "$header"
    for d in "$OUT_DIR/$suite"/*/; do
      [ -d "$d" ] || continue
      case="$(basename "$d")"
      render_case_line "$suite" "$case"
    done
  } > "$f"
}

update_case_line() {
  local suite="$1" case="$2"
  rebuild_csv "$suite"
}

clean_case_runtime() {
  local dir="$1"
  find "$dir" -mindepth 1 -maxdepth 1 \( -name 'log.txt' -o -name 'result.txt' \
    -o -name 'return.txt' -o -name 'metrics.yaml' -o -name 'result.yaml' \
    -o -name 'output.*' -o -name 'reference.*' -o -name '.int' \) -delete 2>/dev/null
}

clean_media() {
  # Default check keeps records, deletes only output.* / reference.*.
  [ "$DEBUG" = true ] && return 0
  find "$1" -mindepth 1 -maxdepth 1 \( -name 'output.*' -o -name 'reference.*' \) -delete 2>/dev/null
}

resolve_board_work_dir() {
  if [ "$MOCK_BOARD" = "1" ]; then
    BOARD_WORK_DIR="$LAB_WORK_DIR"
    BOARD_HOST=""
    return 0
  fi

  if [ "$IP" = "0" ]; then die_usage "real mode requires -i <last-octet>"; fi
  if ! ping -c 1 -W 2 "192.168.5.${IP}" >/dev/null 2>&1; then
    printf '%s\n' "${red}[ERRO] cannot reach board 192.168.5.${IP}${nocolor}" >&2
    exit 2
  fi

  local rootfs
  rootfs="$(ssh "root@192.168.5.${IP}" "LC_ALL=C df -P / | awk 'END{print \$1}'")" || {
    printf '%s\n' "${red}[ERRO] failed to resolve NFS root on board${nocolor}" >&2
    exit 2
  }

  rootfs="${rootfs#*:}"
  rootfs="${rootfs%/}"
  case "$LAB_WORK_DIR" in
    "$rootfs") BOARD_WORK_DIR="/" ;;
    "$rootfs"/*) BOARD_WORK_DIR="/${LAB_WORK_DIR#"$rootfs"/}" ;;
    *) printf '%s\n' "${red}[ERRO] working dir is not inside the board NFS root${nocolor}" >&2; exit 2 ;;
  esac
  BOARD_HOST="root@192.168.5.${IP}"
}

on_int() {
  STOP=1
  if [ "$PHASE" = "run" ] && [ -n "$CURRENT_SUITE" ]; then
    touch "$OUT_DIR/$CURRENT_SUITE/$CURRENT_CASE/.int" 2>/dev/null || true
  fi
}
trap on_int INT

run_phase() {
  local suite="$1" case="$2" dir="$OUT_DIR/$suite/$case" d=0
  [ "$DEBUG" = true ] && d=1
  if [ "$MOCK_BOARD" = "1" ]; then
    ( cd "$LAB_WORK_DIR" && MOCK_BOARD=1 DEBUG="$d" bash "out/$suite/$case/script.sh" run ) > "$dir/log.txt" 2>&1
  else
    ssh "$BOARD_HOST" "cd '$BOARD_WORK_DIR' && DEBUG=$d bash out/$suite/$case/script.sh run" > "$dir/log.txt" 2>&1
  fi
}

check_phase() {
  local suite="$1" case="$2" dir="$OUT_DIR/$suite/$case" d=0
  [ "$DEBUG" = true ] && d=1
  ( cd "$LAB_WORK_DIR" && MOCK_BOARD="${MOCK_BOARD}" DEBUG="$d" bash "out/$suite/$case/script.sh" check ) > "$dir/result.txt" 2>&1
}

run_case() {
  local suite="$1" case="$2" dir="$OUT_DIR/$suite/$case" status final
  get_conf "$suite" "$case"

  if [ -n "$SKIP_REASON" ]; then
    printf '%s\n' "$case"
    printf '  --> NOT RUN (SKIPPED: %s)\n' "$SKIP_REASON"
    update_case_line "$suite" "$case"
    NUM_SKIPPED=$((NUM_SKIPPED + 1))
    return 0
  fi

  status="$(status_from_artifacts "$suite" "$case")"
  case "$status" in
    PASSED)
      if [ "$FORCE" = true ]; then clean_case_runtime "$dir"; else NUM_SKIPPED=$((NUM_SKIPPED + 1)); return 0; fi
      ;;
    FAILED)
      if [ "$FORCE" = true ] || [ "$RERUN" = true ]; then clean_case_runtime "$dir"; else NUM_SKIPPED=$((NUM_SKIPPED + 1)); return 0; fi
      ;;
    INT)
      clean_case_runtime "$dir"
      ;;
    NOT\ RUN|DONE) : ;;
  esac

  printf '%s\n' "$case"

  CURRENT_SUITE="$suite"
  CURRENT_CASE="$case"

  PHASE=run
  run_phase "$suite" "$case"
  PHASE=none
  update_case_line "$suite" "$case"

  if [ -f "$dir/.int" ]; then
    printf '  --> INT (will re-run next time)\n'
    CURRENT_SUITE=""; CURRENT_CASE=""
    return 0
  fi

  PHASE=check
  check_phase "$suite" "$case"
  PHASE=none
  update_case_line "$suite" "$case"

  final="$(tr -d '\r' < "$dir/result.txt" | tail -n 1)"
  case "$final" in *PASSED*) final=PASSED ;; *FAILED*) final=FAILED ;; *) final=DONE ;; esac
  printf '  --> %s\n' "$final"

  if [ "$final" = "PASSED" ]; then
    NUM_PASSED=$((NUM_PASSED + 1))
  elif [ "$final" = "FAILED" ]; then
    NUM_FAILED=$((NUM_FAILED + 1))
    LIST_FAILED+=("$case")
  fi

  clean_media "$dir"
  CURRENT_SUITE=""; CURRENT_CASE=""
}

summary() {
  local total=$((NUM_PASSED + NUM_FAILED + NUM_SKIPPED)) c
  printf '\n+-----------------------------------------------+\n'
  printf '| Total=%-3d Passed=%-3d Failed=%-3d Skipped=%-3d |\n' "$total" "$NUM_PASSED" "$NUM_FAILED" "$NUM_SKIPPED"
  printf '+-----------------------------------------------+\n'
  if [ "$NUM_FAILED" -gt 0 ]; then
    printf 'Failed cases:\n'
    for c in "${LIST_FAILED[@]}"; do printf '  %s\n' "$c"; done
  fi
}

log_mode() {
  local suite case dir cases=() sel is
  for suite in "${SELECTED_SUITES[@]}"; do
    mapfile -t cases < <(cases_for_suite "$suite")
    for case in "${cases[@]}"; do
      if [ "${#SELECTED_CASES[@]}" -gt 0 ]; then
        is=0
        for sel in "${SELECTED_CASES[@]}"; do [ "$sel" = "$case" ] && is=1; done
        [ "$is" = "1" ] || continue
      fi
      dir="$OUT_DIR/$suite/$case"
      [ -d "$dir" ] || continue
      printf '\n%s\n' "$case"
      if [ -f "$dir/result.txt" ]; then
        tr -d '\r' < "$dir/result.txt" | tail -n 1
      elif [ -f "$dir/log.txt" ]; then
        printf 'DONE (run finished, no check result yet)\n'
      else
        printf 'NOT RUN\n'
      fi
      if [ "$VERBOSE" = true ] && [ -f "$dir/log.txt" ]; then
        cat "$dir/log.txt"
      fi
    done
  done
}

tidy_mode() {
  local suite case dir cases=() sel is
  for suite in "${SELECTED_SUITES[@]}"; do
    mapfile -t cases < <(cases_for_suite "$suite")
    for case in "${cases[@]}"; do
      if [ "${#SELECTED_CASES[@]}" -gt 0 ]; then
        is=0
        for sel in "${SELECTED_CASES[@]}"; do [ "$sel" = "$case" ] && is=1; done
        [ "$is" = "1" ] || continue
      fi
      dir="$OUT_DIR/$suite/$case"
      [ -d "$dir" ] || continue
      find "$dir" -mindepth 1 -maxdepth 1 ! -name 'script.sh' ! -name 'case.conf' -delete 2>/dev/null
    done
  done
}

parse_args() {
  while [ $# -gt 0 ]; do
    case "$1" in
      -h|--help) show_help; exit 0 ;;
      -i|--ip) IP="$2"; shift ;;
      -d|--debug) DEBUG=true ;;
      -v|--verbose) VERBOSE=true ;;
      -r|--rerun) RERUN=true ;;
      -f|--force) FORCE=true ;;
      -l|--log) LOG=true; CASE_PICK=true ;;
      -t|--tidy) TIDY=true; CASE_PICK=true ;;
      -s|--suite) SUITE_PICK=true; SELECTED_SUITES=() ;;
      -c|--case) CASE_PICK=true ;;
      *)
        if [ "$CASE_PICK" = true ]; then
          SELECTED_CASES+=("$1")
        elif [ "$SUITE_PICK" = true ]; then
          if [ -d "$OUT_DIR/$1" ]; then SELECTED_SUITES+=("$1"); else die_usage "unknown suite: $1"; fi
        else
          die_usage "unexpected argument: $1"
        fi
        ;;
    esac
    shift
  done
}

main() {
  cd "$PROGRAM_ROOT" || exit 2
  discover_suites
  parse_args "$@"

  if [ "$LOG" = true ] || [ "$TIDY" = true ]; then
    local suite
    for suite in "${SELECTED_SUITES[@]}"; do rebuild_csv "$suite"; done
    [ "$LOG" = true ] && log_mode
    [ "$TIDY" = true ] && tidy_mode
    for suite in "${SELECTED_SUITES[@]}"; do rebuild_csv "$suite"; done
    exit 0
  fi

  resolve_board_work_dir

  local suite case cases=() sel is
  for suite in "${SELECTED_SUITES[@]}"; do
    rebuild_csv "$suite"
    mapfile -t cases < <(cases_for_suite "$suite")
    for case in "${cases[@]}"; do
      if [ "${#SELECTED_CASES[@]}" -gt 0 ]; then
        is=0
        for sel in "${SELECTED_CASES[@]}"; do [ "$sel" = "$case" ] && is=1; done
        [ "$is" = "1" ] || continue
      fi
      run_case "$suite" "$case"
    done
  done
  summary
}

main "$@"
'''


def build_script(suite, case_name, columns, row, test_idx, check_idx, prerun_idx, check_metrics):
    pipeline = cell_text(row.get("Pipeline", "")).strip()

    if not pipeline:
        raise ValueError("case %r has empty Pipeline" % case_name)
    if not check_metrics:
        raise ValueError("case %r selects no Check metrics" % case_name)

    prerun = cell_text(row.get("Prerun", "")).strip()
    precheck = cell_text(row.get("Precheck", "")).strip()

    # Metric criteria columns live strictly between "Check metrics" and "Prerun".
    metric_columns = [c for c in columns[check_idx + 1:prerun_idx] if c]

    # Validate selections and criteria.
    criteria = {}
    for name in check_metrics:
        if name not in KNOWN_METRICS:
            raise ValueError("case %r: unknown metric %r" % (case_name, name))
        if name not in metric_columns:
            raise ValueError("case %r: no criteria column for %r" % (case_name, name))
        value = cell_text(row.get(name, "")).strip()
        if not value:
            raise ValueError("case %r: empty criteria for %r" % (case_name, name))
        criteria[name] = compile_criteria(value)

    # Compile each metric into a check_one() case branch. Criteria stay in bash
    # (Q11); the metric names arrive at runtime from case.conf CHECK_METRICS.
    branches = []
    for name in check_metrics:
        if name == "return":
            branches.append('''    return)
      value=$(cat "$CASE_DIR/return.txt" 2>/dev/null)
      if %s; then
        echo "[  OK   ] return = $value"
      else
        echo "[  NG   ] return = $value"
        result=FAILED
      fi
      ;;''' % criteria[name])
        else:
            branches.append('''    %s)
      value=$(awk -F': ' -v m="%s" '$1==m {print $2; exit}' "$CASE_DIR/result.yaml" 2>/dev/null)
      if %s; then
        echo "[  OK   ] %s = $value"
      else
        echo "[  NG   ] %s = $value"
        result=FAILED
      fi
      ;;''' % (name, name, criteria[name], name, name))

    check_one = '''check_one() {
  local m="$1" value=""
  case "$m" in
%s
    *)
      echo "[ERR] no compiled criteria for metric: $m" >&2
      result=FAILED
      ;;
  esac
}''' % "\n".join(branches)

    prerun_body = "\n".join("    " + line for line in prerun.splitlines()) if prerun else "    :"
    precheck_body = "\n".join("    " + line for line in precheck.splitlines()) if precheck else "    :"

    pipeline_escaped = pipeline.replace("'", "'\"'\"'")

    return (SCRIPT_TEMPLATE
            .replace("%%SUITE%%", suite)
            .replace("%%CASE%%", case_name)
            .replace("%%INFO_COMMENTS%%", info_comments(columns, row, test_idx, check_idx))
            .replace("%%PRERUN_BODY%%", prerun_body)
            .replace("%%PRECHECK_BODY%%", precheck_body)
            .replace("%%PIPELINE%%", pipeline_escaped)
            .replace("%%CHECK_ONE%%", check_one))


def build_case_conf(row, check_metrics):
    timeout = cell_text(row.get("Timeout", "")).strip() or DEFAULT_TIMEOUT
    skip = cell_text(row.get("Skip", "")).strip()
    if skip.lower() == "false":
        skip = ""
    escaped = shlex.quote(skip) if skip else ""
    metrics = shlex.quote("; ".join(check_metrics)) if check_metrics else ""
    return "TIMEOUT=%s\nSKIP_REASON=%s\nCHECK_METRICS=%s\n" % (timeout, escaped, metrics)


def main():
    sheets = xlsxmini.read_sheets(SPEC)
    suites = [(name, rows) for name, rows in sheets.items() if not name.startswith("_")]
    if not suites:
        raise SystemExit("no non-documentation sheets in %s" % SPEC)

    os.makedirs(os.path.join(HERE, "out"), exist_ok=True)

    all_metric_columns = []

    for suite, rows in suites:
        if not rows:
            continue
        header = rows[0]
        try:
            test_idx = header.index("Test case")
            check_idx = header.index("Check metrics")
            prerun_idx = header.index("Prerun")
            header.index("Pipeline")
        except ValueError as e:
            raise SystemExit("sheet %r missing required column: %s" % (suite, e))

        if not (test_idx < check_idx < prerun_idx):
            raise SystemExit("sheet %r has out-of-order skeleton columns" % suite)

        suite_metrics = [c for c in header[check_idx + 1:prerun_idx] if c]
        for m in suite_metrics:
            if m not in all_metric_columns:
                all_metric_columns.append(m)

        suite_dir = os.path.join(HERE, "out", sanitize(suite))
        os.makedirs(suite_dir, exist_ok=True)

        for values in rows[1:]:
            row = {header[i]: (values[i] if i < len(values) else "") for i in range(len(header))}
            test_case = cell_text(row.get("Test case", "")).strip()
            if not test_case:
                continue
            case_name = sanitize(test_case)
            case_dir = os.path.join(suite_dir, case_name)
            os.makedirs(case_dir, exist_ok=True)

            check_metrics = parse_check_metrics(row.get("Check metrics", ""))
            script = build_script(suite, case_name, header, row, test_idx, check_idx, prerun_idx, check_metrics)
            with open(os.path.join(case_dir, "script.sh"), "w", encoding="utf-8", newline="\n") as f:
                f.write(script)
            with open(os.path.join(case_dir, "case.conf"), "w", encoding="utf-8", newline="\n") as f:
                f.write(build_case_conf(row, check_metrics))

    # One suite in this prototype, so the workbook-level metric order = suite order.
    metric_columns = " ".join(all_metric_columns)
    runner = RUNNER_TEMPLATE.replace("%%METRIC_COLUMNS%%", metric_columns)
    runner_path = os.path.join(HERE, "runner.sh")
    with open(runner_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(runner)
    os.chmod(runner_path, 0o755)

    for root, dirs, files in os.walk(os.path.join(HERE, "out")):
        if "script.sh" in files:
            os.chmod(os.path.join(root, "script.sh"), 0o755)

    print("generated runner.sh and out/ (metric columns: %s)" % metric_columns)


if __name__ == "__main__":
    main()