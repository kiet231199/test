#!/usr/bin/env bash
# PROTOTYPE - throwaway generated program (wayfinder ticket 02).
# Suite: omx_filesink   Case: omx_filesink_0001_normal_psnr
#  Input              320x240_h264
#  Decoder            omxh264dec
#  Buffer mode        use-dmabuf=true
#  Width              320
#  Height             240
#  Format             NV12
#  Framerate          30
#  Checklist          Output is correct; PSNR is larger than 29.

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
    :
}

# ---------------------------------------------------------------------------
# precheck(): PC-side user snippet, embedded verbatim (decision Q19).
# ---------------------------------------------------------------------------
precheck() {
    ln -sf "$WORK_DIR/data/common/320x240_h264.mp4" reference.mp4
}

# ---------------------------------------------------------------------------
# run(): executes on the board. Returns pipeline exit code in return.txt.
# ---------------------------------------------------------------------------
PIPELINE='gst-launch-1.0 filesrc location=$WORK_DIR/data/common/320x240_h264.mp4 ! qtdemux ! h264parse ! omxh264dec use-dmabuf=true ! video/x-raw,width=320,height=240,format=NV12 ! filesink location=$CASE_DIR/output.yuv'

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

check_one() {
  local m="$1" value=""
  case "$m" in
    return)
      value=$(cat "$CASE_DIR/return.txt" 2>/dev/null)
      if awk -v v="$value" 'BEGIN{exit !(v==0)}'; then
        echo "[  OK   ] return = $value"
      else
        echo "[  NG   ] return = $value"
        result=FAILED
      fi
      ;;
    psnr)
      value=$(awk -F': ' -v m="psnr" '$1==m {print $2; exit}' "$CASE_DIR/result.yaml" 2>/dev/null)
      if awk -v v="$value" 'BEGIN{exit !(v>=29)}'; then
        echo "[  OK   ] psnr = $value"
      else
        echo "[  NG   ] psnr = $value"
        result=FAILED
      fi
      ;;
    *)
      echo "[ERR] no compiled criteria for metric: $m" >&2
      result=FAILED
      ;;
  esac
}

"$1"
