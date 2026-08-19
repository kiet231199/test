# Prototype 02 - "Generated program contract" (real framework artifacts)

Throwaway prototype for wayfinder ticket 02. This replaces the rejected HTML
state-model demo with the real generated tree the user asked for:

- `runner.sh` (generated)
- `out/<suite>/<case>/script.sh` (generated, `run` / `check` subcommands)
- `out/<suite>/<case>/case.conf` (generated, `TIMEOUT=` / `SKIP_REASON=` / `CHECK_METRICS=`)

The whole path is visible: Excel spec -> generator -> generated tree -> run/check
-> `<suite>_result.csv`.

## Run it

On Linux (or this Windows box's Git Bash):

```bash
python gen.py                    # read Specification.xlsx, emit runner.sh + out/
MOCK_BOARD=1 bash runner.sh      # simulate the board and media-check, write CSV
```

Without a board this is the mock path. For a real board on the LabPC:

```bash
python gen.py
bash runner.sh -i <last-octet>   # ssh root@192.168.5.<n>, NFS WORK_DIR discovery
```

`python gen.py` uses only the standard library (`xlsxmini.py` reads/writes the
workbook); no openpyxl/pandas.

## Demo cases (suite `omx_filesink`)

| Case | Timeout | Skip | Expected status |
| --- | --- | --- | --- |
| `omx_filesink_0001_normal_psnr` | 120 | - | PASSED (return=0, psnr=31.42) |
| `omx_filesink_0002_timeout` | 2 | - | FAILED (return=124) |
| `omx_filesink_0003_skip` | 120 | RZ/G3E does not support this resolution | NOT RUN + SKIPPED note |
| `omx_filesink_0004_prerun_fail` | 120 | - | FAILED (return=9) |
| `omx_filesink_0005_precheck_fail` | 120 | - | FAILED in precheck |
| `omx_filesink_0006_stream_props` | 120 | - | PASSED (width/height/framerate/level/profile) |

## What is being reviewed

- **Q2 (PROVISIONAL)** - `case.conf` as the generator->runner interface.
  It carries `TIMEOUT=`, `SKIP_REASON=`, and `CHECK_METRICS=` (the per-case
  metric names). `runner.sh` sources `TIMEOUT` and `SKIP_REASON` at scheduling;
  `script.sh run` sources `TIMEOUT` for the board-side `timeout N` wrapper;
  `script.sh check` sources `CHECK_METRICS` and passes those names to
  `media-check`.
- **Q11 (PROVISIONAL)** - criteria stay compiled into `script.sh` at generation
  time. `script.sh check` reads the metric names from `case.conf`, then runs
  each metric's compiled OK/NG check (numeric checks use `awk`, present on both
  Git Bash and the Linux target).
- **Q19** - `prerun()` / `precheck()` are embedded verbatim as function bodies
  in `script.sh`. `prerun()` runs on the board before the pipeline and aborts
  the run on non-zero; `precheck()` runs on the PC before metrics and aborts
  the check on non-zero.

## Mock assumption (sign-off needed)

No board exists in this workspace, so the demo introduces an environment flag,
not a new CLI flag:

- `MOCK_BOARD=1` makes `script.sh` call `mock/board.sh` (fake output files +
  return codes) and `mock/media_check.sh` (fake `result.yaml` values) instead
  of `gst-launch-1.0` / `media-check`.
- The real branches stay in the generated `script.sh`; the mock only bypasses
  hardware and third-party tools. The Excel spec, `case.conf`, CSV, and runner
  state machine are all the real artifacts.

## CSV header order (resolved)

Ticket 01 already defines the metric criteria columns at workbook level: one
column per metric used anywhere in the workbook. So the criteria-column order is
a single program-level order, and the generated `runner.sh` embeds it once:

```bash
METRICS=(return width height framerate level profile psnr)
```

The Suite result file uses that order. The header is now lowercase:

```csv
test case,return,width,height,framerate,level,profile,psnr,status,note
```

## Throwaway notes

- Prototype only: light validation, one suite, mock scenarios hard-coded per
  case name in `mock/`. No tests, no production error handling.
- `make_spec.py` rebuilds the committed `Specification.xlsx`; `xlsxmini.py` is
  the tiny dependency-free xlsx reader/writer shared by both scripts.
- Reminder: review this before Q2/Q11 are closed.