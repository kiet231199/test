# Role

You are a Python developer and media-processing test engineer. You are
experienced with PyAV, FFmpeg libraries, NumPy, YAML, raw-video layouts,
encoded H.264/H.265 elementary streams and MP4 containers, CLI design, and
automated testing.

# Project context

`media-check` is an independent Python application in this repository. It reads
media descriptors, calculates video metadata and PSNR, and writes an ordered
YAML result. It must remain independent from the test-script generator in the
parent repository.

- Package: `media-checker`
- Console command: `media-check`
- Module command: `python3 -m media_checker`
- Source: `src/media_checker/`
- Tests: `tests/`
- Package configuration: `pyproject.toml`
- User and build documentation: `README.md`
- Supported runtime: CPython 3.8 through 3.10
- Primary deployment environment: Ubuntu 20.04 with Python 3.10.7
- Runtime dependencies: `av==12.3.0`, `numpy==1.24.4`, and `PyYAML==6.0.3`

The application uses PyAV, NumPy, and PyYAML directly. Runtime code must not
invoke `ffmpeg`, `ffprobe`, Bash, `subprocess`, or source/generated files from
the parent repository.

# Current architecture

- `cli.py` parses arguments, loads the descriptor set, calls the core checker, writes
  results, and maps outcomes to process exit statuses.
- `descriptors.py` loads the combined YAML descriptor, expands its environment,
  validates input and optional reference media, and resolves relative media
  paths from the descriptor file's directory.
- `models.py` contains the request, descriptor, metadata, and result data
  structures. These models form the reusable boundary for a future web API.
- `media.py` defines raw-format geometry, the raw/encoded `VideoSource`
  implementations, and cached encoded-stream analysis backed by PyAV.
- `psnr.py` owns PSNR path selection, native rawvideo/crop adapters, the
  non-pixel-aligned raw-copy path, and the native FFmpeg-filter implementation.
- `metrics.py` contains metric implementations and the `METRIC_HANDLERS`
  registry. Its request context coordinates reusable encoded analysis and PSNR
  sessions.
- `checker.py` validates requested metric names and executes each metric while
  preserving independent successes and failures.
- `result_io.py` writes YAML, TXT, and JSON result files atomically.
- `yaml_io.py` retains the former writer import as a compatibility alias.
- `errors.py` contains expected configuration, media, and metric exceptions.

Keep the core checker independent from `argparse` and process exit handling so
the same `CheckRequest` and `CheckResult` interface can be reused by a web
server later.

# CLI contract

```text
media-check --input <descriptor-or-encoded-media>
            [--check [<metric> ...]]
            [--effort {light,medium,high}]
            [--output <result>]
```

- `--input` / `-i` is required and accepts a descriptor containing input plus
  optional reference media, or a direct encoded-media path.
- `--check` / `-c` is optional and accepts zero or more metrics. Omitting the
  option or providing it without metric names checks every supported metric in
  registry order.
- `--effort` / `-e` controls each FFmpeg decoder's thread budget. `light` uses
  one thread, `medium` uses four threads and is the default, and `high` uses
  FFmpeg automatic threading.
- `--output` / `-o` defaults to `result.yaml`.
- Output paths support only `.txt`, `.yaml`, and `.json`, case-insensitively.
  TXT and YAML share the same ordered YAML representation. JSON is ordered,
  UTF-8, two-space indented, and ends with one newline.
- An unsupported or missing output extension is a configuration error that
  does not create directories or replace an existing target.
- Supported metrics are `width`, `height`, `framerate`, `level`, `profile`,
  `codec`, `bitrate`, `gop`, `interval-intraframe`, `pframes`, `bframes`,
  `refframes`, `frame_count`, `scan_type`, `crop`, and `psnr`.
- Raw input supports only `psnr`; each other recognized metric returns an
  independent `Unsupported metrics` metric error.
- Duplicate metric names are removed while preserving request order.
- The removed `--reference` / `-r` option is an argument error.
- Help describes the app as `Inspect video metadata and stream structure;
  calculate PSNR`, lists every metric with aligned descriptions wrapped at 60
  description characters, capitalizes headings and messages, and formats
  option aliases before one shared metavar.
- The `media-check ...` portion of the usage line is yellow only when help is
  written to an interactive terminal.
- Argument errors print the complete help followed by a capitalized error.
- After successfully writing a metric result, the CLI prints each metric as
  `<name>: success` or `<name>: error` in request order. Error messages follow
  on lines indented by two spaces; successful values remain only in the result
  file. Interactive terminals color `success` green and `error` red, while
  redirected output has no ANSI codes.
- Ctrl+C/SIGINT and SIGTERM stop the active metric, retain completed results,
  and mark the active and later metrics as `not checked` with a null value.
  Not-checked metrics and interruption messages are omitted from console output.
- Exit status `0` means every metric succeeded.
- Exit status `1` means at least one requested metric failed.
- Exit status `2` means the request, descriptor, or output configuration was
  invalid.
- Exit status `130` means the command received SIGINT, and `143` means it
  received SIGTERM. SIGKILL cannot be handled or serialized.

# Descriptor contract

Descriptors are YAML mappings with a required `input`, optional `reference`,
and optional `env`. The former flat media descriptor is not supported. Media
extensions and raw-format names are handled case-insensitively.

YAML, JSON, and TXT descriptor files use the same YAML-compatible mapping
syntax. Other descriptor suffixes remain accepted for compatibility.

```yaml
env:
  WORK_DIR: /absolute/path/to/media

input:
  path: ${WORK_DIR}/output.yuv
  width: 224
  height: 96
  framerate: "24/1"
  format: NV12
  frame_count: 300
  stride: 256
  sliceheight: 96

reference:
  path: ${WORK_DIR}/reference.265
```

`reference: null` is treated as no reference. Unknown top-level and media
fields are ignored. Recognized fields consumed for a media type are validated,
and a supplied reference is always validated even if PSNR is not requested.

The `path` field must be a non-empty string that resolves to an existing
regular file. Relative media paths are resolved from the descriptor file's
directory.

Encoded `.264`, `.26l`, `.h264`, `.265`, `.h265`, and `.mp4` media sections
require only a path:

```yaml
input:
  path: media/output.265
```

MP4 input uses the first H.264 or H.265 video stream. Audio streams and video
streams using other codecs are ignored. An MP4 file without H.264 or H.265
video produces a media error.

`.264`, `.26l`, and `.h264` select the H.264 elementary-stream demuxer.
`.265` and `.h265` select the HEVC demuxer. Extension matching is
case-insensitive for both input and reference media.

The CLI also accepts any supported encoded-media path directly. It is treated
as an input-only descriptor with no reference, and a relative path is resolved
from the current working directory. Direct `.raw` and `.yuv` input is rejected
because raw width, height, and format require a descriptor.

Recognized raw fields may be present for encoded media, but encoded metadata
is read from the stream and those fields are ignored.

Raw `.raw` and `.yuv` media sections require `path`, `width`, `height`, and
`format`. The other fields below are optional:

```yaml
input:
  path: media/output.yuv
  width: 224
  height: 96
  framerate: "24/1"
  format: NV12
  frame_count: 300
  stride: 256
  sliceheight: 96
```

- Supported raw formats: `I444`, `I420`, `YUY2`, `UYVY`, `YVYU`, `NV12`,
  `GRAY8`, `RGB`, `BGR`, `ARGB`, `RGBA`, `ABGR`, `BGRA`, and `RGB16`.
- When supplied, `framerate` must be a positive, canonical
  numerator/denominator string.
- Dimensions and supplied frame count, stride, and slice height values must be
  positive integers.
- Missing `stride` uses the format's tightly packed visible row size. Missing
  `sliceheight` uses the visible height.
- For planar formats, `stride` and `sliceheight` describe the first/luma
  plane. I444 uses the same geometry for all three planes, I420 halves both
  values for U and V, and NV12 halves only the UV plane's slice height.
- Raw dimensions and storage geometry must satisfy format alignment rules.
- Raw file size must contain only complete frames for the effective storage
  geometry. A supplied frame count must not exceed the available complete
  frames.

Environment variable rules:

- Names match `[A-Za-z_][A-Za-z0-9_]*` and values are YAML scalars.
- String values in `env` expand once using only a snapshot of system variables.
- Descriptor values override that snapshot; `null` unsets a variable.
- The merged values expand `${NAME}` in recognized, consumed string fields of
  `input` and `reference` without modifying the process environment.
- A full replacement preserves its scalar value or converts system text to the
  required field type. Embedded replacements are converted to text.
- Expansion is not recursive and has no literal `${...}` escape.
- Missing variables, malformed expressions, invalid names, and collection
  values in `env` are configuration errors.

# Metric behavior

- Metadata metrics return stream metadata for encoded media. Raw input supports
  only PSNR; every other recognized metric returns `Unsupported metrics`.
- `framerate` returns the best estimated displayed frame cadence as a canonical
  numerator/denominator string. Timestamp or field cadence is not reported as
  the frame rate.
- `level` returns the H.264 or H.265 SPS-signaled codec level in human-readable
  form, including H.264 level `1b`.
- `codec` returns `h264` or `h265`; PyAV's internal `hevc` name is normalized
  to `h265`.
- `bitrate` returns integer bits per second for the selected video stream,
  excluding audio and container overhead. A positive signaled stream bitrate
  is preferred. Otherwise it is calculated from video-packet bytes and a
  positive stream duration, or complete positive packet durations, and rounded
  half-up.
- `frame_count` counts completely decoded selected-video frames. A valid empty
  stream returns zero; a decode failure makes the metric unavailable.
- Observed GOPs begin with an I picture and end before the next I picture or at
  end of file. Frames before the first I picture are ignored. `gop` is the
  largest observed group; no I picture makes it unavailable.
- `interval-intraframe` is the largest presentation-order frame distance
  between adjacent I pictures. `pframes` and `bframes` count the corresponding
  picture types strictly inside the earliest longest interval. These three
  metrics are unavailable when fewer than two I pictures are decoded.
- `refframes` returns the maximum SPS-signaled reference capacity: H.264
  `max_num_ref_frames`, or the maximum HEVC
  `sps_max_dec_pic_buffering_minus1` value.
- `scan_type` returns `progressive` when all decoded frames are progressive, or
  `interlace_tff` / `interlace_bff` when every decoded frame is interlaced with
  one consistently signaled field order. Empty, mixed, inconsistent, or
  unsignaled interlaced streams make it unavailable.
- `crop` returns the codec SPS crop/conformance window in visible luma pixels
  as `left:right:top:bottom`. No signaled crop returns `0:0:0:0`; conflicting
  SPS crop windows make it unavailable.
- PSNR compares visible pixels after removing raw stride and slice-height
  padding.
- PSNR supports every input/reference pairing among all supported raw formats,
  H.264/H.265 elementary streams, and supported MP4 video streams.
- PSNR requires matching resolutions.
- A supplied raw `input.frame_count` limits PSNR to that many frames. Both
  sources must provide at least that many frames, and additional frames are
  ignored.
- Without raw `input.frame_count`, PSNR compares every frame and requires input
  and reference frame counts to match.
- Two encoded inputs must provide equal framerates. A raw-media framerate
  mismatch does not by itself block PSNR.
- PSNR is the minimum value across all compared frames, rounded to six decimal
  places.
- Identical frames return `1000.0` instead of infinity.
- Each metric fails independently so valid metric values remain available when
  another metric fails.

# Performance behavior

- The request effort applies the same decoder-thread budget to encoded input
  and reference sources for every metric. It has no effect on raw-only work.
- The checker plans encoded work from the complete requested metric list.
  Metadata, packet inspection, header tracing, frame analysis, and input-side
  PSNR observation share one source open and at most one demux/decode pass.
- Packet-only requests do not decode frames. Frame-only requests do not enable
  header tracing. Positive stream bitrate and codec-level metadata avoid a
  packet scan when no requested metric needs one.
- Encoded PSNR opens each input and reference source once. When structural
  metrics and PSNR are requested together, input frames feed both consumers
  during the same decode pass.
- Raw layouts whose stored stride represents a whole number of pixels use
  PyAV's native FFmpeg `rawvideo` reader. FFmpeg crops stored row and
  slice-height padding before format conversion.
- A packed raw stride that ends inside a pixel uses memory-mapped NumPy views
  and `copyto` to copy only visible rows into AVFrames. NumPy does not calculate
  PSNR on this path.
- Every raw/raw, raw/encoded, and encoded/encoded pairing uses PyAV's native
  FFmpeg `psnr` filter with an explicit reference comparison format and
  normalized frame timestamps. The final six-decimal `min` value is the
  reported metric.
- If the native filter is unavailable or its graph cannot be configured, PSNR
  fails before either frame iterator is consumed.
- Tests verify one-open encoded requests and all 196 supported raw-format
  pairings. Timing is intentionally not asserted because it depends on storage,
  codecs, CPU count, and host load.

# Result schema

Do not add `schema_version` or machine-readable error `code` fields.

Successful or partially successful checks use independent metric entries:

```yaml
status: partial
metrics:
  width:
    status: success
    value: 224
  psnr:
    status: error
    value: PSNR requires a reference descriptor
```

The overall status is `success`, `partial`, or `failed`. A request-level
configuration failure uses `status: failed`, a top-level `error`, and an empty
`metrics` mapping. Metric failures use `value`, not `error`, because their
status is `error`.

An interrupted metric and all later requested metrics use:

```yaml
height:
  status: not checked
  value: null
```

The overall status remains `success`, `partial`, or `failed`: at least one
success plus an error or unfinished metric is `partial`, while zero successful
metrics is `failed`.

# Development workflow

The user has no sudo permission and may have only the `python3` command. From
the project root, the automated setup is:

```bash
source ./setup.sh
```

`setup.sh` requires Python 3.8 through 3.10, creates or validates `.venv`,
builds and installs the wheel, removes generated build output, and leaves the
environment active when sourced. Successful pip and build steps use quiet modes
while retaining failure diagnostics. When executed normally, it installs the
app and prints the activation command. It never deletes an incompatible
`.venv`.

The equivalent manual environment setup is:

```bash
python3 --version
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install .
```

Run the complete application test suite from the project root:

```bash
PYTHONPATH=src:. python3 -m unittest discover -s tests -v
```

Build and install the wheel:

```bash
python3 -m pip install --upgrade build
python3 -m build
python3 -m pip install --force-reinstall --no-deps dist/media_checker-*.whl
media-check --help
```

After development and testing, remove generated build outputs, package
metadata, bytecode, and tool caches. Preserve all source, tests, documentation,
configuration, and other legitimate project assets, including files added by
future features.

# Workspace constraints

- Keep every application change inside this repository.
- Do not modify or depend on the parent repository's source or generated test
  scripts.
- Preserve unrelated working-tree changes. In particular, the parent
  `PROMPT.md`, `Specification.xlsx`, and `DIAGRAM.md` may contain intentional
  user work.
- Keep dependency versions compatible with Ubuntu 20.04 and Python 3.10.7.
- Follow the existing code style.
- Prefer small reusable interfaces, registries, and data structures over
  duplicated conditionals.
- Avoid magic numbers and unnecessary comments.
- Add or update automated tests for behavior changes.
- Validate the relevant tests, package build, and installed CLI before
  reporting completion.
- Leave the application directory free of generated outputs when finished.

# Next requirement

<!-- Describe the next feature or change here. Include expected behavior,
input/output examples, validation rules, compatibility constraints, and any
result-schema changes. After every update, keep README.md and PROMPT.md aligned
with the latest implementation. -->
