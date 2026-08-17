# Unified Excel schema - v1 (DRAFT, pending owner confirmation)

Asset of ticket 01 "Unified Excel schema". Deliverable: the single column schema used by
every test program workbook, plus one fully worked example row per program.

## Workbook shape

- One workbook per test program. Sheet = suite; the sheet name becomes the suite name.
- Sheets whose name starts with `_` are documentation; the generator ignores them.
- Row 1 is the header row. Every row with a non-empty `Test case` cell is a case; other rows are ignored.
- A case row must select at least one check metric.

## Skeleton

Fixed columns, in this order, present in every sheet:

```text
Test case | <information region> | Checklist | Note | Timeout | Skip | Check metrics | <one criteria column per metric> | Prerun | Precheck | Pipeline
```

### Test case (required)

Case name. Free text, unique within the sheet. Becomes the case directory name after sanitizing.
Migration keeps legacy names (`omx_filesink_0001`, `video_h264_0001`, Allegro stream file names).

### Information region

All columns between `Test case` and `Check metrics` are information columns, rendered as bash
comments in the generated case script. Multi-line cells become multiple comment lines. The
generator never parses them. `Checklist` and `Note` are the standard human-readable columns
and never drive automation. Programs add their own fields here (`Width`, `Height`, `Framerate`,
`Level`, `Profile`, `Control rate`, `Target bitrate`, `Interval intraframe`, `Decoder`,
`Buffer mode`, `Format`, `ScanType`, `Crop`, ...). The region may differ per program; the
skeleton does not.

### Timeout (optional)

Positive integer, seconds, enforced by the runner (mechanism: ticket 02). Empty = runner default.

### Skip (optional)

Empty or `FALSE` = run. Any other content = the case is skipped, and the content is the skip
reason recorded in the report. This absorbs Allegro's `list_unsupport` entries (with reasons)
and the draft's boolean skips.

### Check metrics (required, at least one)

The metrics this case checks. Names separated by `;` and/or newlines, in check order. Names
must exactly match registered metric names (case-sensitive).

### Metric criteria columns

One workbook column per metric used anywhere in the workbook; the header is exactly the
lowercase metric name (`return`, `width`, `psnr`, ...). The cell holds the criteria for that
metric in this row, in the criteria DSL:

- Comparison: `>=29`, `<=300`, `>0`, `<300`
- Interval: `[200; 300)`, `(200; 300]` (square = inclusive, round = exclusive)
- Bare number: `224` (numeric equality)
- Bare text: `Baseline` (string equality)

Rows that do not select a metric leave its cell empty. Selecting a metric with an empty
criteria cell, naming a metric with no criteria column, or naming an unknown metric is a
generation error.

### Prerun (optional)

Small bash snippet invoked on the board before the pipeline runs (before `run`).
Example: `~/v4l2-init.sh -d /dev/video0 -r 1920x1080`.

### Precheck (optional)

Small bash snippet invoked on the PC before `check`. Typical use: create `reference.*`
or post-process `output.*` before the metrics run.

### Pipeline (required)

Bash command sequence executed on the board. Usually one `gst-launch-1.0` command; more
commands are allowed. Paths use `$WORK_DIR` placeholders. The checked output must be written
as `output.<ext>` in the case directory; a reference produced by the pipeline must be written
as `reference.<ext>`.

## Metric registry

- `return` - pipeline exit code, recorded by the runner. The only metric that is not media-check.
- `psnr` - min-frame PSNR of `output.*` against `reference.*`, computed by media-check.
  Media preparation (crop/alignment) is ticket 03; raw metadata is ticket 06; the phase-2
  Allegro reference decoder swaps the reference creator (ticket 04).
- media-check metrics measured on `output.*`: `width`, `height`, `framerate`, `level`,
  `profile`, `codec`, `bitrate`, `gop`, `interval-intraframe`, `pframes`, `bframes`,
  `refframes`, `frame_count`, `scan_type`, `crop`.
- Reserved for phase 2: `fps` (Avg. FPS parsed from the pipeline log). Rows may fill the
  `fps` criteria cell now, but must not select `fps` until phase 2 implements it; such rows
  check `return` meanwhile.

v1 rows use the charted set: `return`, `width`, `height`, `framerate`, `level`, `profile`,
`psnr`. Every other media-check metric becomes selectable with no schema change.

## output / reference convention

- The framework searches the case directory for `output.*` and `reference.*` before checking
  metrics. It parses nothing else. Exactly one match of each, or error.
- Metrics without a reference (`width`, `height`, ...) work on `output.*`. `psnr` needs both.
  `return` and `fps` need neither, so display-sink and expect-failure rows define no files.
- The files are created some how before checking: by the pipeline (filesink), by `Prerun` or
  `Precheck` snippets, or by the check recipe the generator emits (ticket 03).
- A selected metric whose file is missing at check time fails the check at run time; the
  generator cannot see files that the run creates.

## Worked example rows

In every example below, `$CASE_DIR` is the case directory (layout and environment names are
fixed by ticket 02).

### Encode - omxh264enc (suite `Video_h264`)

| Column | Value |
| --- | --- |
| `Test case` | `video_h264_0001` |
| `Width` | `224` |
| `Height` | `96` |
| `Framerate` | `24/1` |
| `Level` | `2` |
| `Profile` | `baseline` |
| `Control rate` | `variable` |
| `Target bitrate` | `402554` |
| `Interval intraframe` | `0` |
| `Checklist` | 1) Pipeline runs successfully. 2) Output stream properties match the spec. 3) No visual defect: psnr over 29. |
| `Note` | (empty) |
| `Timeout` | `30` |
| `Skip` | (empty) |
| `Check metrics` | `return; width; height; framerate; level; profile; psnr` |
| `return` | `0` |
| `width` | `224` |
| `height` | `96` |
| `framerate` | `24` |
| `level` | `2` |
| `profile` | `Baseline` |
| `psnr` | `>=29` |
| `Prerun` | (empty) |

`Precheck`:

```bash
cp "$WORK_DIR/input_video/256x96.yuv" reference.yuv
```

`Pipeline`:

```bash
gst-launch-1.0 -e filesrc location=$WORK_DIR/input_video/256x96.yuv num-buffers=300 blocksize=32256 ! videoparse format=nv12 width=256 height=96 framerate=24/1 ! videoscale ! video/x-raw,width=224,height=96 ! omxh264enc control-rate=variable target-bitrate=402554 interval-intraframes=0 ! video/x-h264,profile=\(string\)baseline,level=\(string\)2 ! h264parse ! filesink location=$CASE_DIR/output.h264
```

Note: the PSNR reference here is the source yuv copied by `Precheck`. v4l2 rows instead get
their reference from the pipeline itself: the second filesink writes `reference.yuv` (raw
capture) in `$CASE_DIR`, `Prerun` holds `~/v4l2-init.sh -d /dev/video0 -r WxH`, and `psnr`
is selected.

### Decode - filesink PSNR (suite `omx_filesink`)

| Column | Value |
| --- | --- |
| `Test case` | `omx_filesink_0001` |
| `Input` | `320x240_h264` |
| `Decoder` | `omxh264dec` |
| `Buffer mode` | `use-dmabuf=true` |
| `Width` | `320` |
| `Height` | `240` |
| `Format` | `NV12` |
| `Checklist` | Output is correct; PSNR is larger than 30. |
| `Note` | (empty) |
| `Timeout` | (empty - runner default) |
| `Skip` | (empty) |
| `Check metrics` | `return; psnr` |
| `return` | `0` |
| `psnr` | `>=30` |
| `Prerun` | (empty) |

`Precheck`:

```bash
ln -sf "$WORK_DIR/data/common/320x240_h264.mp4" reference.mp4
```

`Pipeline`:

```bash
gst-launch-1.0 filesrc location=$WORK_DIR/data/common/320x240_h264.mp4 ! qtdemux ! h264parse ! omxh264dec use-dmabuf=true ! video/x-raw,width=320,height=240,format=NV12 ! filesink location=$CASE_DIR/output.yuv
```

The raw output's width/height/format sit in the pipeline caps here; how the check learns them
is ticket 06. Display-sink variants (`waylandsink`, `glimagesink`) have no filesink:
`Check metrics` is `return` only, and the `fps` criteria cell (e.g. `>=28`) is filled but not
selected until phase 2. Expect-failure rows (legacy expected value 0) check `return` with `>0`.

### Allegro - one stream (suite `Allegro_AVC_Syntax_MP_L51`)

| Column | Value |
| --- | --- |
| `Test case` | `Allegro_Inter_Cabac_10_L51_3840x2160@30Hz_6.3.26l` |
| `Standard` | `h264` |
| `Decoder` | `omxh264dec` |
| `Bypass` | `0` |
| `Checklist` | Stream decodes without error; PSNR against the reference is larger than 40. |
| `Note` | (empty) |
| `Timeout` | `360` |
| `Skip` | (empty) |
| `Check metrics` | `return; psnr` |
| `return` | `0` |
| `psnr` | `>=40` |
| `Prerun` | (empty) |

`Precheck`:

```bash
ffmpeg -i "$WORK_DIR/Allegro_streams_h264/Allegro_AVC_Syntax_MP_L51/streams/Allegro_Inter_Cabac_10_L51_3840x2160@30Hz_6.3.26l" -pix_fmt nv12 -f rawvideo reference.yuv
```

`Pipeline`:

```bash
gst-launch-1.0 filesrc location=$WORK_DIR/Allegro_streams_h264/Allegro_AVC_Syntax_MP_L51/streams/Allegro_Inter_Cabac_10_L51_3840x2160@30Hz_6.3.26l ! h264parse ! omxh264dec bypass=0 num-outbufs=30 ! filesink location=$CASE_DIR/output.yuv
```

The stream keeps its real name; only the decoded output is renamed to `output.yuv`, written to
the case directory (not next to the stream). The exact FFmpeg reference recipe is ticket 03;
the phase-2 Allegro reference decoder replaces it (ticket 04). A skipped stream row sets
`Skip` to `RZ/G3E does not support this resolution` and keeps the suite's row shape otherwise.

## Open hooks

- Ticket 02 (Generated program contract): case tree layout, `$CASE_DIR`-style environment,
  `Prerun`/`Precheck` invocation, `Timeout`/`Skip` enforcement, criteria applied to
  media-check's YAML result.
- Ticket 03 (PSNR comparison policy): output/reference preparation and crop rules, min-frame
  PSNR semantics, the Allegro FFmpeg reference recipe.
- Ticket 04 (Phase-2 check metrics): `fps` implementation, swappable reference creator.
- Ticket 05 (Legacy spec migration mapping): field-by-field export into this schema.
- Ticket 06 (Raw media metadata resolution): width/height/format of raw output/reference.
- Prototype: branch `prototype/media-detect` (commit `8e923c9`) - the retired pipeline-parsing
  detection demo.
