# GStreamer QA Automation Suite

Automated test suite that runs GStreamer pipelines on a **remote target over SSH**, validates the
encoded output (width / height / bitrate, plus file / stdout / fps / exit-code checks), and produces
an Excel report. The whole suite is generated from an Excel specification: every test gets its own
**self-contained** `run_test.sh` and `check_output.py` with the pipeline and expected values inlined
— no shared library, no template, no runtime config lookup. Adding or changing tests is a spec edit
plus a re-generate, never hand-editing scripts.

---

## Execution model (read first)

- **Pipelines and all validation run on the remote**, not locally. Every command is executed as:
  ```bash
  ssh root@192.168.5.<IP> "<cmd>"
  ```
  (Plain `ssh` — host-key / batch-mode options are intentionally not passed; your local
  `~/.ssh/config` and `known_hosts` govern the connection.)
- `<IP>` is the **last octet** of the remote address (e.g. `42` → `192.168.5.42`). It is a required
  command-line argument to the runners and to each `run_test.sh`.
- Output files (e.g. `.264`) are produced **on the remote** at the verbatim `Output_File` path.
  They are *not* copied back; `ffprobe`/`ffmpeg` validation targets that remote path over SSH.

---

## Prerequisites

**Local machine (runs the runners and the report):**
- Python 3.8+
- Install Python dependencies:
  ```bash
  pip install -r requirements.txt
  ```
- A Bash shell (tested with **bash 5.2**) providing `timeout`, `ssh`, `python3`, and `wait -n`.
- **SSH access** to `root@192.168.5.<IP>`. Key-based (non-interactive) access is recommended so
  the runners do not block on a password prompt; the suite passes plain `ssh`, so your
  `~/.ssh/config` / `known_hosts` decide how the connection is made.

**Remote target:**
- **aarch64 GNU/Linux** (e.g. Arch Linux ARM), GNU coreutils (`stat -c %s`, `timeout`),
  **bash 5.2**.
- `gst-launch-1.0` (tested against **GStreamer 1.22.12**) plus the plugins your pipelines need
  (e.g. `omxh264enc`, `v4l2src`).
- `ffprobe` and `ffmpeg` (used for metric extraction and PSNR).
- A real capture device (e.g. `/dev/video0`) for the `Camera_H264` tests.

---

## Directory layout

```
gst_test_suite/
├── specs/test_spec.xlsx        # The Excel spec (one sheet per suite)
├── config/tests.json           # Generated from the spec; drives runners & report
├── generate_suite.py           # Spec -> config/tests.json + self-contained per-test scripts
├── tests/<suite>/<test_case>/    # run_test.sh + check_output.py (standalone; generated)
├── runners/                    # run_all_tests.sh, check_all_outputs.sh, full_pipeline.sh
├── results/<suite>/<test_case>/  # Runtime artifacts (logs, exit code, check_result.json)
├── report/generate_report.py   # Aggregates results -> report/test_report.xlsx
├── requirements.txt
└── README.md
```

Each per-test `run_test.sh` / `check_output.py` is standalone — no `lib/`, no `templates/`, no
config read at run time. `generate_suite.py` is the single source that emits them; open any test
file to see exactly what it runs and checks.

---

## Quick start

```bash
# 1. (Re)generate config/tests.json and the per-test scripts from the spec
python generate_suite.py

# 2. Run everything end-to-end against remote 192.168.5.42
bash runners/full_pipeline.sh 42
```

---

## 1. Run all tests

`full_pipeline.sh` runs **every enabled test's pipeline → checks each output → report** in one
shot. Run it from the project root:

```bash
bash runners/full_pipeline.sh <IP>
```

Example (remote `192.168.5.42`):

```bash
bash runners/full_pipeline.sh 42
```

**Concurrency model:** the remote can only run one GStreamer pipeline at a time, so pipelines run
**strictly sequentially**. The moment a pipeline finishes, that test's output check (lightweight
`ffprobe`/`ffmpeg`, not a pipeline) is launched in the **background**, so checking overlaps with the
next pipeline run. Up to `--jobs` checks run at once (default 8).

Useful flags:

| Flag              | Effect                                             |
|-------------------|----------------------------------------------------|
| `--suite <suite>` | Restrict to one suite                              |
| `--id <test_case>`  | Restrict to one test                               |
| `--jobs N`        | Max output checks to run in parallel (default 8)   |
| `--quiet`         | Suppress per-action timestamped logging            |

> There is no parallel-pipeline mode: running multiple GStreamer pipelines on the target at once is
> not supported by the hardware.

---

## 2. Run all tests in a single suite

Suite names match the Excel sheet names (e.g. `Video_H264`, `Camera_H264`):

```bash
bash runners/run_all_tests.sh <IP> --suite <suite>
```

Example:

```bash
bash runners/run_all_tests.sh 42 --suite Video_H264
```

Then validate and/or report as needed:

```bash
bash runners/check_all_outputs.sh <IP>      # checks run in parallel (--jobs N, default 8)
python report/generate_report.py
```

`check_all_outputs.sh` runs the per-test `check_output.py` scripts concurrently (checks are safe to
parallelize because they do not start GStreamer pipelines). Pass `--jobs 1` to force sequential
checking.

---

## 3. Run a single test

Use `--id` with the `Test_case` value from the spec:

```bash
bash runners/run_all_tests.sh <IP> --id video_0001
```

Example:

```bash
bash runners/run_all_tests.sh 42 --id video_0001
```

A single test can also be driven directly:

```bash
bash tests/Video_H264/video_0001/run_test.sh 42        # run the pipeline on the remote
python tests/Video_H264/video_0001/check_output.py 42  # validate (IP optional; falls back to ip.txt)
```

---

## 4. Where to find results and reports

**Per-test runtime artifacts** — `results/<suite>/<test_case>/`:

| File                | Contents                                              |
|---------------------|-------------------------------------------------------|
| `stdout.log`        | SSH stdout from the pipeline                          |
| `stderr.log`        | SSH stderr from the pipeline                          |
| `exit_code.txt`     | Pipeline exit code (**124 = timeout**)                |
| `ip.txt`            | Remote octet used for this run                        |
| `duration.txt`      | Wall-clock duration in seconds                        |
| `check_result.json` | Per-check verdict written by `check_output.py`        |

`check_result.json` has `status` of **PASS / FAIL / TIMEOUT / SKIPPED**, the expected vs. actual
values, and a `checks` array (one entry per check key). Example:

```json
{
  "test_case": "video_0001",
  "suite": "Video_H264",
  "status": "PASS",
  "output_type": "width;height;bitrate",
  "expected": "width=1920; height=1080; bitrate=900000:1100000",
  "actual": "width=1920, height=1080, bitrate=1001233",
  "duration_sec": 4.21,
  "checks": [
    {"key": "width",   "status": "PASS", "expected": "1920",           "actual": "1920"},
    {"key": "height",  "status": "PASS", "expected": "1080",           "actual": "1080"},
    {"key": "bitrate", "status": "PASS", "expected": "900000:1100000", "actual": "1001233"}
  ]
}
```

**Aggregated report** — `report/test_report.xlsx`, generated by `report/generate_report.py`. It
contains a **Statistics** sheet (totals, PASS/FAIL/TIMEOUT/SKIPPED counts and %, durations,
timestamp) plus **one sheet per suite** with a color-coded per-test breakdown
(green = PASS, red = FAIL, orange = TIMEOUT, gray = SKIPPED/NOT_RUN).

---

## 5. How to add a new test case

Tests are **driven by the spec**, never hand-edited:

1. Open `specs/test_spec.xlsx` and go to the sheet for the target suite (each sheet is one suite;
   the sheet name is the suite name). To create a new suite, add a new sheet using the same column
   layout.
2. Add a row and fill the columns:
   - `Test_case` — unique id, e.g. `video_0017`.
   - `Test purpose` — human-readable description (becomes `description` in `config/tests.json`).
   - `Pipeline` — full `gst-launch-1.0` command (runs on the remote). Any `filesink location=...`
     **must** match the `Output_File` column.
   - `Output_Type` — one or more check keys separated by `;`. Built-in keys:
     `width`, `height`, `bitrate`, `exit_code`, `no_error`, `file_exists`, `file_size`,
     `stdout_match`, `fps`. (To use `psnr`, `gop_size`, `p_frames`, or `b_frames`, add a snippet
     first — see §6.)
   - `Expected_Value` — single bare scalar for a single key (e.g. `0`, `>=24.0`); for multiple keys
     use `;`-separated `key=expr` pairs (e.g. `width=1920; height=1080; bitrate=900000:1100000`).
     Expr formats: width/height = exact int; bitrate/file_size = `min:max` (bitrate also accepts
     `>=value`); fps = `>=value`; exit_code = int; stdout_match = regex; `no_error`/`file_exists`
     ignore the expr.
   - `Timeout_Sec` — integer (default `30` if blank).
   - `Enabled` — `TRUE`/`FALSE` (default `TRUE` if blank); disabled tests are still generated but
     skipped by the runners.
   - `Input_File` — remote reference path; needed only by a full-reference `psnr` check (not built
     in by default — see §6). Leave blank otherwise.
   - `Output_File` — remote output path; required for `file_exists`/`file_size`/`width`/`height`/
     `bitrate`/`psnr`.
3. Re-generate the suite from the project root:
   ```bash
   python generate_suite.py
   ```
   This rewrites `config/tests.json` and (re)creates each `tests/<suite>/<test_case>/run_test.sh` +
   `check_output.py`. It is idempotent, and it removes generated test directories no longer in the spec.
4. Run the new test:
   ```bash
   bash runners/run_all_tests.sh <IP> --id video_0017
   ```

---

## 6. How to add a new metric check

New metrics are added in **exactly one place** — `generate_suite.py` — and the per-test
`check_output.py` files are regenerated to include only the checks they use.

1. **Write a snippet builder.** Add a function that returns the Python source of the check, with the
   target baked in. The emitted function takes `ctx` (exposing `.ip`, `.exit_code`, `.stdout`,
   `.stderr`, plus the module-level `OUTPUT_FILE` / `INPUT_FILE`) and returns `(passed, actual_str)`:
   ```python
   def _snip_my_metric(expr):
       func = (
           'def check_my_metric(ctx):\n'
           '    rc, out, _ = _ssh_capture(ctx.ip, "ffprobe ... " + shlex.quote(OUTPUT_FILE))\n'
           '    ...\n'
           '    return (passed, str(actual))'
       )
       return {"name": "check_my_metric", "func": func,
               "needs_ssh": True, "needs_re": False}
   ```
   Set `needs_ssh` if the emitted function calls `_ssh_capture` (pulls in the SSH helper +
   `subprocess`/`shlex`); set `needs_re` if it uses `re`. Use `_emit_numeric_compare(expr)` to bake a
   `min:max` / threshold comparison.
2. **Register it.** Add one entry to `SNIPPET_BUILDERS`:
   ```python
   SNIPPET_BUILDERS = {
       # ...existing...
       "my_metric": _snip_my_metric,
   }
   ```
3. **Use it in the spec.** Reference `my_metric` in a row's `Output_Type`, give the matching
   `Expected_Value`, and re-run `python generate_suite.py`.

The runners, the report, and every per-test `run_test.sh` are untouched — `SNIPPET_BUILDERS` is the
single edit point. (The heavier metrics from earlier versions — `psnr`, `gop_size`, `p_frames`,
`b_frames` — are intentionally not shipped; add them here if a spec needs them.)

---

## Notes & caveats

- **Pipelines run one at a time** on the remote (no concurrent GStreamer pipelines); output checks
  run in parallel on the host (`--jobs`, default 8).
- **SSH is invoked plainly** (`ssh root@192.168.5.<IP> "..."`). Use key-based auth and a populated
  `known_hosts` so runs do not block on a prompt; configure this in `~/.ssh/config`.
- **`ffprobe`/`ffmpeg`/`gst-launch-1.0` (and required plugins) must exist on the remote.**
- Built and tested for an **aarch64 GNU/Linux** remote with **bash 5.2** and GNU coreutils.
- **`exit_code.txt == 124` is reported as TIMEOUT, and a TIMEOUT short-circuits the metric checks.**
  ⚠️ A `videotestsrc` / `v4l2src` pipeline with **no `num-buffers`** never ends on its own, so
  `timeout` always kills it (exit 124) — every such test reports TIMEOUT and width/height/bitrate are
  never validated. Add `num-buffers=N` (or send EOS via `gst-launch-1.0 -e`) in the spec's pipeline
  so the run finishes and the metrics are checked.
- **Bitrate** may be unavailable for raw `.264` elementary streams (no container metadata); the check
  then reports `unavailable` (FAIL).
- **`psnr`, `gop_size`, `p_frames`, `b_frames`** are not built in by default — add a snippet (§6).
