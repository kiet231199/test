# Media Checker

`media-check` is a standalone Ubuntu 20.04 application for reading video
metadata and calculating PSNR. It uses PyAV and NumPy directly and does not
invoke `ffmpeg`, `ffprobe`, Bash, or subprocesses at runtime.

## Automated setup

The application supports CPython 3.8 through 3.10. From the project root, run:

```bash
source ./setup.sh
```

The script checks `python3`, creates or reuses a compatible `.venv`, builds and
installs the wheel, removes generated build files, and leaves the virtual
environment active. It never uses `sudo`.

If the script is executed instead of sourced, installation still completes,
but activation cannot remain in the parent terminal:

```bash
./setup.sh
source .venv/bin/activate
```

An existing `.venv` must contain a working Python 3.8, 3.9, or 3.10. The script
stops without deleting an incompatible environment.

## Descriptor

One YAML descriptor contains the input and an optional PSNR reference:

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

Encoded H.264/H.265 elementary streams need only a path. Raw `.raw` and `.yuv`
media require `path`, `width`, `height`, `framerate`, `format`, `frame_count`,
`stride`, and `sliceheight`.

Supported raw formats are `NV12`, `YUY2`, `RGB16`, `RGB`, `RGBA`, and `GRAY8`.
`stride` is the first plane's stored bytes per row, and `sliceheight` is the
first plane's stored row count. Raw file size must exactly match this geometry.

Absolute media paths are recommended. Relative paths are resolved from the
directory containing the YAML descriptor.

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
  # Other required raw fields are omitted from this short example.
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
    --check width height framerate level profile psnr \
    --output metrics-result.yaml
```

`--input/-i` is the combined YAML descriptor. `--check/-c` accepts one or more
metrics. `--output/-o` defaults to `metrics-result.yaml` in the current
directory. Run `media-check --help` to see every supported metric.

The removed `--reference/-r` option is an argument error. Argument errors print
the complete help and exit with status `2`.

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
    error: PSNR requires a reference descriptor
```

Exit status `0` means every metric succeeded, `1` means at least one metric
failed, and `2` means the command or descriptor configuration was invalid.

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
