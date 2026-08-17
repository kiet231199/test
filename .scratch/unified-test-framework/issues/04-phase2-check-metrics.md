# Phase-2 check metrics

Type: grilling
Status: open
Blocked by: 03

## Question

Design the checks beyond the v1 draft set. This defines the second step of Allegro and GST Decode verdict parity:

- Reference-decoder check: the Allegro decoder executables (`ldecod_Allegro_Linux.exe`, `HEVCDecoder_Allegro_Linux.exe`) run host-side and produce `reference.*` instead of the FFmpeg decode in phase 2 (pass threshold >= 40 per legacy `analyze.sh`). How is the reference creator swapped/declared per suite/case?
- Log-derived metric: Avg. FPS parsed from the pipeline log for display sinks (legacy Decode generator `check_fps`).
- How these metrics plug into the schema (ticket 01) and the contract (ticket 02) without special-casing Allegro.

Deliverable: metric designs that fit the metric registry, plus a worked Allegro row that uses them.