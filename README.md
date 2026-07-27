# 🎬 Media Checker

`media-check` inspects video files and compares video quality.

- It reads metadata and stream structure.
- It also calculates PSNR against a reference video.
- Results are written as ordered YAML for every output file name.

## ✨ Key features

- 🔍 Inspect H.264, H.265, and MP4 video.
- 📏 Read size, frame rate, codec, and bitrate.
- 🧩 Inspect GOPs, frame types, crop, and scan type.
- 🖼️ Compare raw or encoded video with PSNR.
- 📄 Load simple YAML descriptors.
- 🧾 Write ordered YAML results to any output file name.
- 🧱 Keep successful metrics after another metric fails.
- 🧵 Use one native thread per decoder or filter.

## 🏗️ How it works

```text
📄 Descriptor or encoded video
            ↓
🧭 Command-line interface
            ↓
🧾 Descriptor loader and validator
            ↓
🎞️ Raw or encoded video source
            ↓
📐 Metric checker and PSNR engine
            ↓
🗂️ Ordered result file and console summary
```

- 🧭 The CLI accepts media and metric choices.
- 🧾 Descriptors define input and optional reference video.
- 🎞️ PyAV reads encoded video and raw frames.
- 📐 Metrics share decoded data when possible.
- 🗂️ Each metric returns its own status.

## ⚡ Quick installation

Media Checker supports CPython 3.10 on Ubuntu 20.04.

From the project root, run:

```bash
source ./setup.sh
```

The script creates or checks `.venv`.
It builds and installs `media-check`.
It leaves the environment active.

If you execute the script, activate manually:

```bash
./setup.sh
source .venv/bin/activate
```

For a manual installation:

```bash
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install .
```

## 🚀 Usage

```bash
media-check --input <descriptor-or-video> \
  [--check [<metric> ...]] \
  [--output <result-file>]
```

- 📥 `--input` or `-i` is required.
- ✅ `--check` or `-c` chooses metrics.
- 🧮 Omit metrics to run every metric.
- 📤 `--output` or `-o` defaults to `result.yaml`.
- 🗃️ Output always uses YAML, regardless of its file name or extension.
- 🔁 Duplicate metric names run once.

Available metrics:

- 📐 `width`, `height`, `framerate`, `level`, and `profile`
- 🎞️ `codec`, `bitrate`, `gop`, and `interval-intraframe`
- 🖼️ `pframes`, `bframes`, `refframes`, and `frame_count`
- 🔎 `scan_type`, `crop`, and `psnr`

Raw video supports only `psnr`.
Other raw metrics return independent errors.

### 🧪 Example: inspect encoded video

```bash
media-check \
  --input media/output.265 \
  --check codec bitrate gop frame_count \
  --output result.report
```

Encoded input accepts `.264`, `.26l`, `.h264`, `.265`, `.h265`, and `.mp4`.
MP4 uses its first H.264 or H.265 video stream.

### 🖼️ Example: calculate PSNR

Create `media.yaml` beside your media files:

```yaml
input:
  path: output.yuv
  width: 224
  height: 96
  format: NV12
  framerate: "24/1"

reference:
  path: reference.265
```

Then run:

```bash
media-check --input media.yaml --check psnr --output result.yaml
```

Raw `.raw` and `.yuv` files need a descriptor.
They require `path`, `width`, `height`, and `format`.
Optional fields are `framerate`, `frame_count`, `stride`, and `sliceheight`.

Supported raw formats are `I444`, `I420`, `YUY2`, `UYVY`, `YVYU`, `NV12`,
`GRAY8`, `RGB`, `BGR`, `ARGB`, `RGBA`, `ABGR`, `BGRA`, and `RGB16`.

## 🧾 Results and exit codes

Each requested metric has its own ordered result value. Failed or unfinished
metrics are `null`.

```yaml
width: 224
psnr: null
```

Only failed metrics are printed to the console:

```text
[ERRO] [psnr] PSNR requires a reference descriptor
```

The tags and metric name are colored when printed to a terminal. Redirected
output has no color.

- ✅ Exit `0`: every metric succeeded.
- ⚠️ Exit `1`: one or more metrics failed.
- 🛑 Exit `2`: command or configuration error.
- ⌨️ Exit `130`: interrupted with Ctrl+C.
- 📴 Exit `143`: stopped with SIGTERM.

## 🛠️ Development

Run the full test suite:

```bash
PYTHONPATH=src:. python3 -m unittest discover -s tests -v
```

Build a wheel manually:

```bash
python3 -m pip install --upgrade build
python3 -m build
python3 -m pip install --force-reinstall --no-deps dist/media_checker-*.whl
```
