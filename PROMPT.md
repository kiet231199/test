# media-check: Engineering Context

## Purpose and boundaries

`media-checker` is a standalone Python 3.10 application for inspecting video
metadata and stream structure, and calculating PSNR. It is independent of the
parent repository's test-script generator; do not read, modify, execute, or
depend on parent-repository source or generated scripts.

| Item | Value |
| --- | --- |
| Package / command | `media-checker` / `media-check` |
| Module entry point | `python3 -m media_checker` |
| Source / tests | `src/media_checker/` / `tests/` |
| Configuration / docs | `pyproject.toml` / `README.md` |
| Runtime | CPython 3.10; Ubuntu 20.04 target |
| Pinned dependencies | `av==17.1.0`, `numpy==1.24.4`, `PyYAML==6.0.3` |

Runtime code uses PyAV, NumPy, and PyYAML directly. Never invoke `ffmpeg`,
`ffprobe`, Bash, or `subprocess` from the application.

## Architecture

```text
CLI -> descriptors -> CheckRequest -> checker -> metrics registry
                                  |              |         |
                                  |              |         +-> media / psnr
                                  |              +-> CheckResult
                                  +-> result_io -> ordered YAML
```

| Module | Responsibility |
| --- | --- |
| `cli.py` | Parse arguments, load input, invoke checking, serialize results, map exit status. |
| `descriptors.py` | Load YAML-compatible descriptors, expand environment, validate media, resolve relative paths. |
| `models.py` | Immutable request, descriptor, metadata, metric, and result boundary; retain as the future web-API seam. |
| `checker.py` | Normalize and validate metrics; preserve independent metric outcomes and interruptions. |
| `metrics.py` | Metric classes, `METRIC_HANDLERS`, and shared request context. |
| `media.py` | Raw layout validation, raw/encoded sources, selected-stream inspection, cached encoded analysis. |
| `psnr.py` / `raw_copy.py` | Native FFmpeg-filter PSNR; raw adapters; lazy NumPy copy path for non-pixel-aligned stride. |
| `native_runtime.py` | Enforce one native worker for decoders, filters, and exceptional copy work. |
| `result_io.py` | Atomic ordered YAML output for every result path. |
| `errors.py` | Expected configuration, media, and metric exceptions. |
| `yaml_io.py` | Compatibility alias for the legacy writer import. |

Keep `checker` independent of `argparse` and process exits. Reuse
`CheckRequest` and `CheckResult` for future non-CLI clients.

## Current feature baseline

### Invocation and output

```text
media-check --input <descriptor-or-encoded-media>
            [--check [<metric> ...]] [--output <result>]
```

- `--input/-i` is required. A direct input supports encoded media only; raw media requires a descriptor.
- `--check/-c` defaults to every registry metric, in registry order; deduplicate explicit metrics while preserving order.
- `--output/-o` defaults to `result.yaml`; every output path is valid and always receives ordered YAML.
- `--reference/-r` is removed and remains an argument error.
- Exit codes: `0` all metrics successful; `1` any metric failure; `2` configuration failure; `130` SIGINT; `143` SIGTERM.
- Interactive metric-error logs use bold colored tags and cyan metric names; redirected output has no color. Completed metrics survive interruption; active and later metrics become `not checked`.

### Descriptor and media model

- Descriptor: YAML-compatible mapping with required `input`, optional `reference`, and optional `env`; `reference: null` means absent.
- Resolve relative media paths from the descriptor directory. Direct encoded paths resolve from the working directory. Paths must resolve to existing regular files.
- `env` merges a one-pass, non-recursive expansion snapshot with descriptor overrides; validate names and scalar values, and do not mutate process environment.
- Encoded extensions: `.264`, `.26l`, `.h264`, `.265`, `.h265`, `.mp4` (case-insensitive). MP4 selects the first H.264/H.265 video stream.
- Raw extensions: `.raw`, `.yuv`. Require `path`, positive `width` and `height`, and `format`; optional `framerate`, `frame_count`, `stride`, and `sliceheight` must be valid when supplied.
- Raw formats: `I444`, `I420`, `YUY2`, `UYVY`, `YVYU`, `NV12`, `GRAY8`, `RGB`, `BGR`, `ARGB`, `RGBA`, `ABGR`, `BGRA`, `RGB16`.
- Enforce raw alignment, effective storage geometry, complete-frame file size, and declared-frame availability. Ignore raw fields on encoded descriptors.

### Metrics and PSNR

Registry metrics: `width`, `height`, `framerate`, `level`, `profile`, `codec`,
`bitrate`, `gop`, `interval-intraframe`, `pframes`, `bframes`, `refframes`,
`frame_count`, `scan_type`, `crop`, `psnr`.

- Encoded inputs expose stream metadata, packet/frame analysis, SPS-derived fields, and structure metrics.
- Raw inputs support only `psnr`; other recognized metrics fail independently as unsupported.
- Normalize HEVC codec output to `h265`; use decoded/display frame cadence for framerate.
- PSNR supports every raw/encoded pairing, compares visible pixels only, requires matching resolutions, and uses native PyAV/FFmpeg `psnr` filtering.
- Two encoded sources require matching framerate. Raw input `frame_count` limits comparison; otherwise frame counts must match. Return the six-decimal minimum frame PSNR; identical frames return `1000.0`.
- A failed metric must never discard other metric results.

### Performance invariants

- Native decoders and filter graphs use one thread.
- Plan encoded work from the complete metric set: share one source open and, where possible, one packet/decode pass across metadata, structure, and input-side PSNR.
- Avoid packet scans or header tracing unless a requested metric needs them.
- Use native rawvideo input when stride is pixel-aligned; otherwise lazily load NumPy, memory-map source frames, and copy visible pixels into AVFrames. NumPy never computes PSNR.
- Configure the PSNR filter before consuming source frames. Timing is not a test contract.

### Result contract

```yaml
width: 224
psnr: null
```

- Preserve requested metric order.
- Successful metrics use their value; failed and unfinished metrics use `null`. Request-level failures use an empty mapping.
- No status, metric-wrapper, schema-version, or machine-readable error-code fields are written.
- Every output file name uses ordered UTF-8 YAML and is written atomically.
- Only failed metrics are printed: `[ERRO] [<metric-name>] <message>`. `[INFO]`, `[WARN]`, and `[ERRO]` have bold terminal colors; only `[ERRO]` is currently emitted.

## Development and delivery

```bash
source ./setup.sh
PYTHONPATH=src:. python3 -m unittest discover -s tests -v
python3 -m build
python3 -m pip install --force-reinstall --no-deps dist/media_checker-*.whl
media-check --help
```

- `setup.sh` requires Python 3.10, creates or validates `.venv`, installs the wheel, and never deletes an incompatible environment.
- Add or update tests for behavior changes. Before completion, run relevant tests, the complete suite, package build, and installed CLI verification.
- Remove generated build output, package metadata, bytecode, and tool caches after validation.
- Preserve unrelated working-tree changes, including `PROMPT.md`, `Specification.xlsx`, and `DIAGRAM.md` when outside the requested edit.
- Keep dependencies compatible with Ubuntu 20.04/Python 3.10.7. Follow local style; favor small reusable interfaces, registries, and data structures over duplicated conditionals.
- Keep `README.md` and this file aligned with implemented behavior after every change. The tone and formatting rule must be as same as with the current content

## Next requirement

<!-- Add the next feature or change here. Include expected behavior, inputs and
outputs, validation rules, compatibility constraints, and result-schema changes. -->
