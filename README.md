# Media Checker

`media-check` is a standalone Ubuntu 20.04 application for reading video
metadata and encoded-stream structure, calculating PSNR, and writing ordered
results. It uses PyAV and NumPy directly and does not invoke `ffmpeg`,
`ffprobe`, Bash, or subprocesses at runtime.

## Automated setup

The application supports CPython 3.10. From the project root, run:

```bash
source ./setup.sh
```

The script checks `python3`, creates or reuses a compatible `.venv`, builds and
installs the wheel, removes generated build files, and leaves the virtual
environment active. Successful installation keeps pip and build output quiet
and prints only the final confirmation; failure diagnostics remain visible. It
never uses `sudo`.

If the script is executed instead of sourced, installation still completes,
but activation cannot remain in the parent terminal:

```bash
./setup.sh
source .venv/bin/activate
```

An existing `.venv` must contain a working Python 3.10. The script
stops without deleting an incompatible environment.

## Descriptor

A YAML, JSON, or TXT descriptor contains the input and an optional PSNR
reference:

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

`input` is required. `reference` is optional and is needed only for PSNR.
`reference: null` is treated as no reference. The former flat descriptor format
is not supported.

Encoded H.264/H.265 elementary streams and `.mp4` containers need only a path.
MP4 input uses the first H.264 or H.265 video stream; audio and other stream
formats are ignored. An MP4 file without H.264 or H.265 video is a media error.
Raw `.raw` and `.yuv` media require `path`, `width`, `height`, and `format`.
`framerate`, `frame_count`, `stride`, and `sliceheight` are optional.

Supported media extensions are `.raw`, `.yuv`, `.264`, `.26l`, `.h264`,
`.265`, `.h265`, and `.mp4`. H.26L and H.264 extensions use the H.264 demuxer;
`.265` and `.h265` use the HEVC demuxer. Extension matching is
case-insensitive.

Supported raw formats are `I444`, `I420`, `YUY2`, `UYVY`, `YVYU`, `NV12`,
`GRAY8`, `RGB`, `BGR`, `ARGB`, `RGBA`, `ABGR`, `BGRA`, and `RGB16`. `stride`
is the first plane's stored bytes per row, and `sliceheight` is the first
plane's stored row count. I444 uses the same geometry for all three planes.
I420 uses half stride and half slice height for its U and V planes. NV12 uses
the first-plane stride and half slice height for its interleaved UV plane.
When omitted, the values default to tightly packed rows and the visible
height. Supplied padding is removed before PSNR is calculated.

A raw file must contain only complete frames for its effective storage
geometry. When `input.frame_count` is supplied, PSNR compares that many frames
and ignores additional frames. Without it, PSNR compares every frame and reports
an error when the input and reference frame counts differ. Raw framerate is not
needed for PSNR.

Absolute media paths are recommended. Relative paths are resolved from the
directory containing the YAML descriptor.

For encoded media, `--input/-i` can point directly to an H.264/H.265 elementary
stream or supported MP4 file. This is equivalent to a descriptor containing
only `input.path`, so no reference is available for PSNR. Direct raw input is
not accepted because its width, height, and format must come from a descriptor.

### Environment variables

`${NAME}` values may use letters, numbers, and underscores; the first character
must be a letter or underscore.

1. Values inside `env` expand once from system environment variables.
2. Descriptor `env` values then override system values.
3. The merged values expand recognized fields in `input` and `reference`.

For example, if the system has `MEDIA_ROOT=/srv/media`:

```yaml
env:
  INPUT_FILE: ${MEDIA_ROOT}/input.yuv
  FRAME_COUNT: 300

input:
  path: ${INPUT_FILE}
  frame_count: ${FRAME_COUNT}
  # The required width, height, and format are omitted from this short example.
```

A full replacement keeps its scalar type or is converted to the field's
required type. A replacement inside a longer string is converted to text.
`null` unsets a variable. Missing variables, invalid variable names, collection
values, and malformed `${NAME}` expressions are configuration errors. Expansion
does not change the running process environment and is not recursive.

Unknown top-level and media fields are ignored. Recognized fields that are used
for the selected media type are validated. A supplied reference is always
validated, even when PSNR is not requested.

## Usage

```bash
media-check \
    --input media.yaml \
    --check codec bitrate gop interval-intraframe pframes bframes \
            refframes frame_count scan_type crop psnr \
    --effort medium \
    --output result.yaml
```

`--input/-i` is a combined descriptor or direct encoded-media path.
`--check/-c` is optional and accepts zero or more metric names. Omitting the
option or providing it without names checks every supported metric in the order
shown by `--help`. Help lists each metric with an aligned description and wraps
description text after 60 characters. `--effort/-e` controls the native FFmpeg
thread budget for encoded decoders and PSNR filters: `light` uses one thread,
`medium` uses four threads and is the default, and `high` lets FFmpeg choose
automatically. The limit is applied to each native stage rather than acting as
a hard process-wide thread cap. `--output/-o`
accepts only `.txt`, `.yaml`, or `.json` paths and defaults to `result.yaml` in
the current directory. Extension matching is case-insensitive. TXT and YAML
use the same ordered YAML representation; JSON uses an equivalent ordered,
indented representation.

Raw input supports only `psnr`. Each other recognized metric produces an
independent error with the value `Unsupported metrics`; this does not prevent a
requested PSNR check from running.

### Encoded metrics

The original encoded metadata metrics are `width`, `height`, `framerate`,
`level`, and `profile`. The stream-analysis metrics are:

- `framerate`: estimated displayed frames per second, written as an exact
  numerator/denominator value such as `24/1` or `30000/1001`.
- `level`: the codec level signaled by the H.264 or H.265 SPS, normalized to a
  readable value such as `4.1` or H.264 `1b`.

- `codec`: `h264` or `h265`.
- `bitrate`: average selected-video bitrate in integer bits per second. Audio
  and container overhead are excluded.
- `gop`: largest observed I-picture group, including a final group ending at
  end of file.
- `interval-intraframe`: largest decoded frame distance between adjacent I
  pictures.
- `pframes` and `bframes`: counts strictly between the I pictures defining the
  longest intra-frame interval; the earliest interval wins ties.
- `refframes`: maximum reference-picture capacity signaled in the codec SPS.
- `frame_count`: number of successfully decoded frames in the selected video
  stream.
- `scan_type`: `progressive`, `interlace_tff`, or `interlace_bff`. Mixed or
  incompletely signaled scan modes produce an unavailable metric error.
- `crop`: codec SPS crop window in visible luma pixels, formatted as
  `left:right:top:bottom`.

Metrics that cannot be determined from a stream fail independently. For
example, a short stream with only one I picture can report `gop` and
`frame_count` while `interval-intraframe`, `pframes`, and `bframes` report
metric errors.

### PSNR and performance

PSNR supports every combination of the listed raw formats, H.264/H.265
elementary streams, and supported MP4 video. It compares visible pixels,
ignores raw storage padding, requires matching resolutions and frame counts,
and reports the minimum frame value rounded to six decimals. Identical frames
return `1000.0`.

Raw layouts whose stored stride represents whole pixels use PyAV's native
FFmpeg rawvideo reader. FFmpeg crops row and slice-height padding, converts both
sources to the reference comparison format, and calculates every pairing with
its native PSNR filter. A packed stride that ends inside a pixel, such as
RGB24 width 210 with stride 640, uses memory-mapped NumPy views only to copy
visible rows into AVFrames before the same native crop/convert/PSNR path. If
the filter cannot be configured, PSNR fails before frame reading begins.

For encoded input, the requested metric list is planned as one inspection
session. Metadata, packet fields, decoded-frame statistics, and PSNR share one
source open and at most one demux/decode pass. Packet-only checks avoid decode;
frame-only checks avoid header tracing; signaled bitrate and level values avoid
unneeded packet scans. Encoded PSNR also opens its reference once.

Automated tests verify the one-open behavior and all 196 raw-format pairings.
Elapsed time is not used as a test assertion because results vary with media,
storage, codec, CPU count, and system load.

Use the included benchmark to compare every effort mode on the same descriptor:

```bash
python scripts/benchmark_effort.py media.yaml \
    --metrics psnr \
    --repetitions 3
```

The JSON report includes wall time, process CPU-seconds, average occupied CPU
cores, and peak resident threads. CPU-seconds and average cores distinguish
active work from sleeping native threads that tools such as htop still display.
The benchmark performs an unmeasured warm-up and rotates effort order between
repetitions. Omit `--metrics` to exercise every supported metric. Use the same
media and metrics when comparing the report with an FFmpeg command.

The removed `--reference/-r` option is an argument error. Argument errors print
the complete help and exit with status `2`.

After writing the result file, the command also prints a short summary in
request order. Successful values remain in the result file and are omitted from
the summary; metric errors include their indented message:

```text
width: success
level: error
  Metric 'level' is unavailable for the input media
```

On an interactive terminal, `success` is green and `error` is red. Redirected
output does not contain ANSI color codes.

If checking is interrupted with Ctrl+C/SIGINT or SIGTERM, completed metric
results are retained. The active metric and every later metric are written with
`status: not checked` and `value: null`; those unfinished metrics are omitted
from the console summary. The command exits without an interruption message or
traceback. SIGKILL cannot be handled or written because the operating system
does not allow process cleanup.

## Results

Each requested metric has an independent result, so successful values remain
available when another metric fails:

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

Metric errors use the `value` field. Request, descriptor, and output
configuration failures retain a top-level `error` field.

Exit status `0` means every metric succeeded, `1` means at least one metric
failed, and `2` means the command or descriptor configuration was invalid.
Ctrl+C/SIGINT exits with status `130`; SIGTERM exits with status `143`. An
unsupported output suffix is a configuration error and does not create or
replace the requested path.

## Development

Run the complete suite from the project root:

```bash
PYTHONPATH=src:. python3 -m unittest discover -s tests -v
```

To build manually:

```bash
python3 -m pip install --upgrade build
python3 -m build
python3 -m pip install --force-reinstall dist/media_checker-*.whl
```

Generated build directories, package metadata, bytecode, and tool caches should
not be committed.
