#!/usr/bin/env bash
# PROTOTYPE - throwaway. Generated runner for wayfinder ticket 02.
# Do not treat as production. Mock mode: MOCK_BOARD=1 bash runner.sh
set -u

PROGRAM_ROOT="$(cd "$(dirname "$0")" && pwd)"
OUT_DIR="$PROGRAM_ROOT/out"
LAB_WORK_DIR="$PROGRAM_ROOT"

# Generator -> runner: metric columns in workbook criteria-column order.
# This prototype has one suite, so it is a single program-level list.
METRICS=(return width height framerate level profile psnr)

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
