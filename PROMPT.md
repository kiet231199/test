# Role

You are a Python developer and media-processing test engineer. You are
experienced with PyAV, FFmpeg libraries, NumPy, YAML, raw-video layouts,
encoded H.264/H.265 elementary streams, CLI design, and automated testing.

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
- `media.py` defines raw-format geometry and the raw/encoded `VideoSource`
  implementations backed by PyAV.
- `metrics.py` contains metric implementations and the `METRIC_HANDLERS`
  registry.
- `checker.py` validates requested metric names and executes each metric while
  preserving independent successes and failures.
- `yaml_io.py` writes result files atomically.
- `errors.py` contains expected configuration, media, and metric exceptions.

Keep the core checker independent from `argparse` and process exit handling so
the same `CheckRequest` and `CheckResult` interface can be reused by a web
server later.

# CLI contract

```text
media-check --input <descriptor> --check <metric> [<metric> ...]
            [--output <result>]
```

- `--input` / `-i` is required and contains input plus optional reference media.
- `--check` / `-c` accepts one or more metrics.
- `--output` / `-o` defaults to `metrics-result.yaml`.
- Supported metrics are `width`, `height`, `framerate`, `level`, `profile`,
  and `psnr`.
- Duplicate metric names are removed while preserving request order.
- The removed `--reference` / `-r` option is an argument error.
- Help lists every metric, capitalizes headings and messages, and formats option
  aliases before one shared metavar.
- The `media-check ...` portion of the usage line is yellow only when help is
  written to an interactive terminal.
- Argument errors print the complete help followed by a capitalized error.
- Exit status `0` means every metric succeeded.
- Exit status `1` means at least one requested metric failed.
- Exit status `2` means the request, descriptor, or output configuration was
  invalid.

# Descriptor contract

Descriptors are YAML mappings with a required `input`, optional `reference`,
and optional `env`. The former flat media descriptor is not supported. Media
extensions and raw-format names are handled case-insensitively.

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

Encoded `.264` and `.265` media sections require only a path:

```yaml
input:
  path: media/output.265
```

Recognized raw fields may be present for encoded media, but encoded metadata
is read from the stream and those fields are ignored.

Raw `.raw` and `.yuv` media sections require every field below:

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

- Supported raw formats: `NV12`, `YUY2`, `RGB16`, `RGB`, `RGBA`, and `GRAY8`.
- `framerate` must be a positive, canonical numerator/denominator string.
- Dimensions, frame count, stride, and slice height must be positive integers.
- Raw dimensions and storage geometry must satisfy format alignment rules.
- Raw file size must exactly match the descriptor geometry and frame count.

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

- Metadata metrics return stream metadata for encoded media and descriptor
  metadata for raw media.
- `level` and `profile` return metric errors for raw media.
- PSNR compares visible pixels after removing raw stride and slice-height
  padding.
- PSNR requires matching resolutions and frame counts.
- Two encoded inputs must provide equal framerates. A raw-media framerate
  mismatch does not by itself block PSNR.
- PSNR is the minimum value across all compared frames, rounded to six decimal
  places.
- Identical frames return `1000.0` instead of infinity.
- Each metric fails independently so valid metric values remain available when
  another metric fails.

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
    error: PSNR requires a reference descriptor
```

The overall status is `success`, `partial`, or `failed`. A request-level
configuration failure uses `status: failed`, a top-level `error`, and an empty
`metrics` mapping.

# Development workflow

The user has no sudo permission and may have only the `python3` command. From
the project root, the automated setup is:

```bash
source ./setup.sh
```

`setup.sh` requires Python 3.8 through 3.10, creates or validates `.venv`,
builds and installs the wheel, removes generated build output, and leaves the
environment active when sourced. When executed normally, it installs the app
and prints the activation command. It never deletes an incompatible `.venv`.

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



# Note:

After any update, remember to update @README.md and @PROMPT.md to match with the
lastest implementation