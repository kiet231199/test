# Media Checker

`media-check` is a standalone Ubuntu 20.04 application for reading video
metadata and calculating PSNR. It uses PyAV and NumPy directly and does not
invoke `ffmpeg`, `ffprobe`, Bash, or subprocesses.

## Installation

The application supports CPython 3.8 through 3.10. This includes Ubuntu
20.04's default Python 3.8 and Python 3.10.7 installed separately.

After exporting Python 3.10.7, verify that `python3` resolves to that
interpreter before creating the virtual environment. Ubuntu's default Python
3.8 needs `sudo apt install python3-venv`; a separately installed interpreter
must include its matching `venv` module.

```bash
python3 --version
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install .
```

The dependencies are pinned to versions that provide Linux wheels for the
supported Python versions. A system FFmpeg installation is not required.

## Build

From the `check_app` directory, activate the virtual environment created above
and build both the source archive and wheel:

```bash
python3 -m pip install --upgrade build
python3 -m build
```

The packages are written to `dist/`. Install the wheel to expose the
`media-check` command:

```bash
python3 -m pip install dist/media_checker-*.whl
```

The `build/`, `dist/`, cache, and package metadata directories are generated
outputs and can be deleted after building or testing.

## Descriptors

Encoded H.264/H.265 elementary streams need only a path:

```yaml
path: media/output.265
```

Raw media requires its complete stored and visible layout:

```yaml
path: media/output.yuv
width: 224
height: 96
framerate: "24/1"
format: NV12
frame_count: 300
stride: 256
sliceheight: 96
```

`stride` is the first plane's stored bytes per row. `sliceheight` is the first
plane's stored row count. Relative media paths are resolved from the descriptor
file, not from the process working directory.

Supported media extensions are `.raw`, `.yuv`, `.264`, and `.265`. Supported
raw formats are `NV12`, `YUY2`, `RGB16`, `RGB`, `RGBA`, and `GRAY8`.

## Usage

```bash
media-check \
    --input output.yaml \
    --reference reference.yaml \
    --check width height framerate level profile psnr \
    --output metrics-result.yaml
```

`--input/-i` describes the subject being measured. `--reference/-r` describes
the golden media and is required only for PSNR. `--output/-o` defaults to
`metrics-result.yaml` in the current directory.

## Results

Each requested metric has an independent result so successful values remain
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
