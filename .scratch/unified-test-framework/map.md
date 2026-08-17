# Wayfinder map: Unified Codec Test Framework

Label: wayfinder:map

## Destination

One unified codec test framework in this repo: one Excel spec format, one Python generator that builds test programs from specs, one common bash runner that runs cases on the board, checks outputs on the PC, and produces per-suite CSV reports. Allegro streams, GST Decode, and GST Encode are all migrated onto it. The map is done when no design decisions remain before implementation starts â€” implementation itself is beyond this map.

## Notes

- Domain: GStreamer/Allegro codec testing on an NFS-booted board (RZ/G3E), driven from a LabPC over `ssh root@192.168.5.<ip>`.
- Skills for resolving sessions: grilling (domain-modeling is not installed); prototype is allowed for the schema ticket.
- References: `Refer/` (gitignored) holds the three legacy programs; `create_test.py` + `scripts/` hold the current draft attempt.
- Standing decisions from the charting session (2026-08-17):
  - Scope: unified framework plus migration of all three programs (Allegro streams, GST Decode, GST Encode).
  - Execution model: codegen â€” a Python generator reads Excel and writes the program tree (suite/case dirs, each case with run/check script); a common bash runner orchestrates run â†’ check â†’ report.
  - Spec: one Excel workbook per test program, sheet = suite, rows hand-written (including Allegro stream cases).
  - Runner: bash, feature parity with GST_Decode.sh (suite/case selection, status resume NOT RUN / DONE / PASSED / FAILED, --rerun/--force/--debug/--verbose/--log/--tidy, Ctrl+C-safe).
  - Report: per-suite `<suite>_result.csv` plus a summary banner.
  - Check metrics v1: the draft set â€” return, width, height, framerate, level, profile, psnr. Allegro reference-decoder PSNR and display-sink Avg. FPS are phase 2.
  - Excel column schema: redesigned from scratch; the draft layout is input to the design, not the answer.

## Decisions so far

<!-- the index â€” one line per closed ticket: enough to judge relevance, then zoom the link for the detail the ticket holds -->

- [Unified Excel schema](issues/01-unified-excel-schema.md) — skeleton + free info region; Input/Output columns abolished: the check searches `output.*`/`reference.*` in the case directory; Prerun (board) / Precheck (PC) user hooks; metric vocabulary = media-check names + `return`, `fps` reserved; criteria DSL kept

## Not yet specified

- Reuse vs rewrite of the draft code (`scripts/`, `create_test.py`, `tests/`) â€” revisit once the schema and program contract are locked.
- Test strategy for the framework itself: unit tests for spec parsing, golden-file tests for generated scripts, dry-run mode.
- Report content beyond the per-suite CSV: failed-case summary shape, which measured values are recorded in the CSV.
- Decode/analyze overlap for Allegro (legacy ran decode.sh and analyze.sh in two parallel shells) â€” revisit if sequential running proves too slow.

## Out of scope

(none ruled out yet)