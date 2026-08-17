# Unified Excel schema

Type: grilling
Status: closed
Assignee: Codex

## Question

Design the single column schema used by every test program workbook. Every case the three programs have today must fit this schema, and nothing else.

Requirements:

- Suite = sheet. One row per test case, hand-written (including Allegro stream cases).
- Free information columns rendered as comments into the generated case script (today: Test purpose, Width, Height, ... Note).
- Machine-readable check selection: which metrics this case checks, and one criteria cell per metric. The draft criteria DSL (`>=29`, `[200; 300)`, numeric/string equality) is the starting language.
- Media specification for file-comparison metrics: Input and Output cells that accept `<path>` or `<path>|<width>|<height>|<format>` with `$WORK_DIR` placeholders.
- Pipeline column holding the full pipeline string executed on the board.
- Timeout and Skip columns the runner can enforce.
- Human-readable Checklist/Note columns that do not drive automation.

The schema must concretely accommodate:

1. An Encode row (omxh264enc: control rate, target bitrate, interval intraframe, profile/level caps).
2. A Decode row (decoder, buffer mode, PSNR expectation for filesink, Avg. FPS expectation for display sinks â€” phase-2 metrics must be expressible).
3. An Allegro row (one per stream file, suite = stream family).

Deliverable: the schema definition (ordered column list with semantics) plus one fully worked example row per program.


## Resolution

Resolved 2026-08-18 by grilling (three rounds) plus one prototype.

Schema definition and worked example rows: [assets/01-unified-excel-schema.md](../assets/01-unified-excel-schema.md)

Key decisions:

- Skeleton plus free information region: `Test case` -> information region (rendered as comments; `Checklist`/`Note` standard) -> `Timeout` -> `Skip` -> `Check metrics` -> one criteria column per metric -> `Prerun` -> `Precheck` -> `Pipeline`. Programs keep their own info fields; no universal column union.
- Input/Output columns abolished. The check searches the case directory for `output.*` (the file metrics run on) and `reference.*` (the psnr counterpart); exactly one match of each, or error. The files are created by the pipeline, by Prerun/Precheck snippets, or by the emitted check recipe - the framework parses nothing to find them.
- Prerun = board-side bash snippet invoked before `run`; Precheck = PC-side bash snippet invoked before `check` (e.g. FFmpeg-decode `reference.*` for Allegro).
- Check metrics: explicit selector cell, at least one metric per row; criteria columns named exactly the lowercase metric name; criteria DSL unchanged (`>=29`, `[200; 300)`, numeric/string equality).
- Metric vocabulary = media-check metric names plus `return` (runner-recorded exit code). `fps` reserved for phase 2: its criteria cell may be filled, but it is not selectable until implemented.
- Timeout = seconds, empty = runner default; Skip = text reason (empty/FALSE runs), absorbing Allegro's list_unsupport entries.
- Sheets starting with `_` are ignored (documentation). Case names are free text, unique per sheet; migration keeps legacy names.
- Worked reference patterns: Encode psnr reference = source yuv copied by Precheck (omx rows) or the v4l2 raw-capture sink (v4l2 rows); Decode reference = symlinked input mp4; Allegro reference = FFmpeg decode in Precheck (exact recipe is ticket 03).

Deferred to other tickets: raw media metadata -> 06 (created from this resolution); psnr preparation/crop semantics -> 03; invocation, environment, CSV mechanics -> 02; swappable reference + fps -> 04; row export -> 05.

Prototype: branch `prototype/media-detect` (commit 8e923c9) - the pipeline-parsing detection demo; parsing was retired in favor of the naming convention above.