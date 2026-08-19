# Generated program contract

Type: grilling
Status: closed
Assignee: Codex
Blocked by: 01

## Question

Define what the generator emits and what the bash runner relies on:

- Program tree layout: suite/case directories, per-case script name and subcommands (`run` executes on the board, `check` on the PC).
- Per-case artifacts: log and result file names, formats, and who writes them.
- Per-suite `<suite>_result.csv`: columns and the status state machine (NOT RUN / DONE / PASSED / FAILED), resume semantics, interaction with --rerun/--force.
- Where and how Timeout and Skip (from the schema in ticket 01) are enforced: which side, and which status timed-out or skipped cases record.
- How Prerun/Postrun user functions (from the schema in ticket 01) are invoked: which side, at which moment, with which environment.
- How check searches `output.*` / `reference.*` in the case directory (ticket 01 convention), invokes media-check, and applies criteria to its YAML result.
- Environment conventions: `$WORK_DIR` resolution on board vs PC (NFS-root discovery), DEBUG behavior.

Deliverable: a contract document concrete enough that the runner and the generator can be built independently against it.


## Comments

- 2026-08-18: Claimed by Codex; grilling rounds 1-2 settled the contract except Q2 (case.conf interface) and Q11 (criteria compiled into script.sh), which wait for prototype review. Prototype: branch `prototype/program-contract` (commit bf76091), file `.scratch/unified-test-framework/prototype-02-program-contract.html` (open in a browser; guided walkthroughs cover happy path, timeout, Ctrl+C->INT->resume, skip, prerun failure, missing output, rerun/force/tidy, rebuild healing). REMINDER: user reviews prototype before this ticket resolves.

- 2026-08-18: Decision record, grilling rounds 1-2 (settled unless marked PROVISIONAL).
  - Tree: `runner.sh` at program root + `out/<suite>/<case>/script.sh` (run/check subcommands); workbook stays at program root; nothing else emitted.
  - Generator-runner interface: `case.conf` (TIMEOUT=, SKIP_REASON=) beside script.sh, read by runner and script.sh. PROVISIONAL - prototype review pending.
  - Artifacts: generator: script.sh, case.conf. Runner: log.txt (run-phase stream), result.txt (check-phase stream). script.sh run: return.txt (pipeline exit code). script.sh check: metrics.yaml (media-check descriptor), result.yaml (media-check ordered result). Case: output.*, reference.*
  - Suite result CSV: header row `Test case, <metric columns in sheet criteria-column order>, Status, Note`; no column for metrics absent from the sheet; empty cell when the case does not check a metric; skipped case = NOT RUN + note `SKIPPED: <reason from spec>`; measured values written at check time.
  - Statuses: NOT RUN / DONE / PASSED / FAILED / INT. INT = Ctrl+C during run phase only: runner kills the board pipeline, writes `.int` marker, keeps files; INT re-runs automatically (kept outputs removed at re-run). Ctrl+C during check stays DONE. Rebuild derives everything from artifacts (.int marker included); values re-read from result.yaml/return.txt.
  - Timeout: board-side `timeout --kill-after=5 <N>` around the pipeline inside run(); 124 lands in return.txt; PC-side watchdog N+60 s; default N=120 s; no --timeout CLI flag.
  - Skip: enforced at scheduling from case.conf; --force never overrides skip (edit the spec instead).
  - Prerun/Precheck: embedded verbatim as prerun()/precheck() function bodies in script.sh (PROVISIONAL shape shown in prototype); Prerun on board before pipeline, Precheck on PC before metrics; cwd=CASE_DIR; env WORK_DIR/CASE_DIR/DEBUG. Prerun failure aborts run, its exit code -> return.txt. Precheck failure aborts check -> FAILED.
  - Environment: WORK_DIR resolved at run time (ssh `df -P /` NFS discovery on board; program root on LabPC); CASE_DIR derived from $0; generator takes no --ip.
  - Cleanup/debug: default check deletes only output.* and reference.* (records kept - they feed rebuild); --debug sets GST_DEBUG=1 on board and keeps media files; --tidy removes everything except script.sh + case.conf, removes .int, resets NOT RUN.
  - Runner CLI: legacy flag set unchanged (-i/-s/-c/-r/-f/-d/-v/-l/-t/-h); exit codes 0 no FAILED / 1 any FAILED / 2 usage-env error.
  - Criteria: compiled into script.sh bash at generation (draft criteria.py style). PROVISIONAL - prototype review pending.
  - Check order: Precheck -> find output.*/reference.* (only for metrics that need them, exactly one match each) -> metrics.yaml -> media-check -> result.yaml -> return from return.txt -> per-metric OK/NG lines -> final PASSED/FAILED line.
- 2026-08-18: HTML state-model prototype (branch prototype/program-contract, commit bf76091) REJECTED by user: wrong shape - "we are not building a website, we're creating a test framework". Next prototype must use real framework artifacts (generated tree + runner, readable/runnable), decided in a fresh session.

## Resolution

Resolved 2026-08-20 by real-artifacts prototype review. Prototype: [assets/prototype-02-program-contract/README.md](../assets/prototype-02-program-contract/README.md); generator and generated runner live in the same directory.

Q2 and Q11 are settled:

- `case.conf` carries `TIMEOUT=`, `SKIP_REASON=`, and `CHECK_METRICS=` (this case's `Check metrics` names in authored order; order is not load-bearing). `script.sh check` reads `CHECK_METRICS` and passes those names to `media-check --check ...`.
- Criteria stay compiled into `script.sh` (Q11). `case.conf` holds metric names only; per-metric thresholds and OK/NG branches compile as `check_one()` in `script.sh`.
- Suite result CSV header uses the Specification's criteria-column order, lowercase: `test case,<metric columns in criteria-column order>,status,note`. The runner receives that order once via `METRIC_COLUMNS=(...)` embedded in `runner.sh` (no extra file, no per-suite metadata).
- Generation-time validation: a metric named in `Check metrics` with no criteria column -> error; a metric with an empty criteria cell -> error; a criteria column no case selects is allowed (CSV keeps it, leaving the cell empty); order inside `Check metrics` does not matter.
- Unchanged: `prerun()` / `precheck()` verbatim function bodies (Q19); `MOCK_BOARD=1` prototype convenience; real `gst-launch-1.0` / `media-check` branches remain; per-case artifact conventions (`log.txt`, `result.txt`, `return.txt`, `metrics.yaml`, `result.yaml`, `output.*`, `reference.*`).
