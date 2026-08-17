# Generated program contract

Type: grilling
Status: open
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
