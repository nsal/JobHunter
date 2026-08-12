# Task 9 Fix 4: Prevent Orphaned Supervised Processes

## Overview

This bug fix closes the three remaining Task 9 lifecycle defects found during
review. It prevents a live lease-reclaimed worker from being replaced in the
dispatcher's child registry, hard-stops a dispatcher that survives launcher
termination, and prevents the launcher from reporting clean shutdown while the
web server remains alive.

The change preserves the existing single launcher, single dispatcher,
maximum-three worker design, SQLite ownership tokens, stop-aware dispatcher
polling, and bounded cleanup phases. It strengthens supervision guarantees
without adding distributed workers, process groups, scheduling changes, or UI
scope.

## Context (from discovery)

- `app/work/dispatcher.py` retains a reclaimed child when it survives
  `terminate()`, but `_fill_slots()` can claim the same queued work ID and
  overwrite the live child in `_children`.
- `app/cli.py::_stop_dispatcher()` stops after one `terminate()` attempt and can
  return while the dispatcher remains alive.
- Requested launcher shutdown terminates the web server and waits one second
  without checking whether it actually exited.
- `tests/test_dispatcher.py` covers shutdown-time termination resistance, but
  not lease recovery followed by a replacement claim for the same work ID.
- `tests/test_cli.py` assumes `terminate()` always stops a child and does not
  exercise kill escalation or cleanup exhaustion for either supervised
  process.
- The current full suite passes with 391 tests, so the missing cases require
  focused regressions rather than broad architectural changes.

## Development Approach

- **Testing approach**: TDD, consistent with Task 9 Fixes 1 through 3.
- Add a focused failing regression before each production change and confirm it
  fails for the reviewed reason.
- Complete each task fully before moving to the next task.
- Keep changes small and limited to dispatcher registry identity and launcher
  cleanup guarantees.
- Every task that changes code must add or update tests for success and error or
  edge paths.
- All focused tests must pass before starting the next task.
- Update this plan immediately if implementation scope or interfaces change.
- Preserve configured concurrency, token-bound work ownership, safe diagnostics,
  and initiating-child exit codes.
- Use `uv` for execution, Ruff for linting and formatting, and mypy for static
  verification.

## Testing Strategy

- **Dispatcher unit tests**: cover an expired child that survives termination,
  retention of its registry entry, exclusion of its work ID from new claims,
  and continued filling of genuinely available slots with other work.
- **Launcher unit tests**: cover terminate-phase exit, kill-phase exit, cleanup
  exhaustion, clean requested shutdown, startup failure, and sibling failure
  for both dispatcher and server processes.
- **Status tests**: verify clean shutdown returns zero, forced or incomplete
  cleanup returns non-zero, and an initiating non-zero child status remains the
  result when sibling cleanup also fails.
- **No browser E2E tests**: this change affects only process supervision and has
  no route, form, template, or browser behavior.
- **Process smoke test**: retain the existing bounded spawned-process test; add
  another real-spawn test only if unit doubles cannot verify the selected
  escalation contract safely and deterministically.
- **Acceptance verification**: run the complete Python suite, Ruff check and
  format check, mypy, generated-schema drift check, lockfile check, and Git
  whitespace check.

## Progress Tracking

- Mark completed items with `[x]` immediately when done.
- Add newly discovered tasks with a `➕` prefix.
- Record blockers with a `⚠️` prefix.
- Update this plan if implementation deviates from the selected design.
- Do not move to the next task while focused tests are failing.
- Keep recorded verification counts aligned with actual command output.

## Solution Overview

Use durable registry reservations and one bounded launcher escalation contract:

1. Treat every work ID in `Dispatcher._children` as reserved until its process
   is confirmed stopped and removed. Over-fetch available work by the bounded
   number of supervised children, skip reserved IDs before claiming, and still
   fill open capacity with unrelated queued work.
2. Extend the launcher's process boundary with `kill()` and add one small helper
   that terminates, joins, escalates to kill when needed, joins again, and
   reports whether cleanup was forced and confirmed.
3. Keep the dispatcher's stop-event and full internal supervision budget as its
   cooperative phase, then use the shared escalation helper if it remains
   alive.
4. Use the same helper for the web server after its normal SIGTERM request and
   require confirmed exit before returning a clean launcher status.
5. Apply the contract to requested shutdown, startup failure, server failure,
   and dispatcher failure while preserving the status of the process that
   initiated shutdown when it is already non-zero.

## Technical Details

### Dispatcher registry identity

- `_children` remains keyed by work ID because only one current work owner may
  be launched while an earlier process for that work is alive.
- `_fill_slots()` must not call `claim()` for a candidate whose ID is already in
  `_children`, even if stale recovery has returned its durable row to `queued`.
- Query enough candidates to compensate for at most `len(_children)` reserved
  IDs, then stop after filling the actual number of open slots.
- A termination-resistant reclaimed child continues to consume one concurrency
  slot and remains eligible for repeated bounded stop attempts; it must never
  be replaced or discarded while alive.
- Other queued work may fill remaining slots, so one retained child does not
  unnecessarily stall unrelated capacity.

### Launcher cleanup result

- Extend `app.cli.ProcessLike` with `kill()` to match the multiprocessing
  process API and existing dispatcher process protocol.
- Represent cleanup with the smallest explicit result needed by callers:
  whether escalation was forced and whether the process was confirmed stopped.
  A tuple is acceptable if its meaning remains clear; a small frozen dataclass
  is preferred if more than two states are required.
- Keep bounded terminate and kill joins as named shared constants or derive
  them from the existing dispatcher lifecycle constants when that avoids
  duplicated timing contracts.
- Catch only portable unsupported-operation cases around `kill()` and fall back
  safely; do not hide unexpected process-control failures as successful exit.
- Recheck `is_alive()` after every join. Never infer exit solely from a signal
  method returning.

### Launcher status rules

- Requested shutdown returns zero only when dispatcher cleanup is cooperative,
  server cleanup does not require hard kill, both children are confirmed
  stopped, and the dispatcher exits zero.
- A dispatcher that exceeds its complete internal cleanup budget is already a
  forced launcher shutdown, even if launcher termination later succeeds.
- A server that survives SIGTERM and requires kill, or survives all cleanup,
  makes requested shutdown non-zero.
- When a child exits non-zero and initiates sibling cleanup, preserve that
  non-zero status. If the initiating status is zero but sibling cleanup is
  forced or incomplete, return the launcher's generic non-zero failure status.
- Startup cleanup uses the same bounded helper and always returns non-zero; it
  must not leave either successfully started child alive.

## What Goes Where

- **Implementation Steps** contain code, tests, and in-repository documentation
  achievable in this repository.
- **Post-Completion** contains manual process-tree verification and GitHub
  workflow actions that require external state.

## Implementation Steps

### Task 1: Reserve work IDs until reclaimed children exit

**Files:**

- Modify: `app/work/dispatcher.py`
- Modify: `tests/test_dispatcher.py`

- [x] Add a failing regression where lease recovery requeues work while its old
  process survives `terminate()`, then prove no replacement process is spawned
  for the same work ID.
- [x] Add a success test proving unrelated queued work still fills the remaining
  capacity while the reclaimed child stays supervised.
- [x] Update `_fill_slots()` to skip every candidate whose work ID remains in
  `_children` and over-fetch only enough candidates to preserve available
  capacity.
- [x] Add edge coverage proving the original child entry remains intact until
  `is_alive()` becomes false and ordinary reclaimed-child exit still frees the
  slot.
- [x] Run `uv run pytest tests/test_dispatcher.py`; all tests must pass before
  Task 2.

### Task 2: Add bounded launcher hard-stop escalation

**Files:**

- Modify: `app/cli.py`
- Modify: `tests/test_cli.py`

- [x] Extend the launcher process protocol and process double with a portable
  hard-stop operation.
- [x] Add failing helper-level tests for terminate-phase exit, kill-phase exit,
  and survival after both bounded phases.
- [x] Implement one bounded terminate/kill/join helper that reports forced and
  confirmed cleanup without treating signal delivery as proof of exit.
- [x] Update `_stop_dispatcher()` to retain its stop-event cooperative phase,
  then use the shared escalation helper when the dispatcher remains alive.
- [x] Add edge tests for dispatcher kill escalation, confirmed dispatcher exit,
  and cleanup exhaustion with a still-live dispatcher.
- [x] Run `uv run pytest tests/test_cli.py`; all tests must pass before Task 3.

### Task 3: Require confirmed web-server cleanup on every launcher path

**Files:**

- Modify: `app/cli.py`
- Modify: `tests/test_cli.py`

- [x] Add a failing requested-shutdown test where the server survives
  `terminate()` and exits only after `kill()`.
- [x] Add an exhaustion test proving a server that survives both phases cannot
  produce a zero launcher status.
- [x] Route requested shutdown, startup failure, and dispatcher-failure server
  cleanup through the shared bounded helper.
- [x] Update server-failure dispatcher cleanup assertions so the initiating
  non-zero child status is preserved while cleanup remains fully bounded.
- [x] Add success tests proving ordinary server termination still returns the
  expected clean or initiating-child status without unnecessary kill calls.
- [x] Run `uv run pytest tests/test_cli.py tests/test_dispatcher.py`; all tests
  must pass before Task 4.

### Task 4: Verify acceptance criteria and regression safety

**Files:**

- Modify:
  `docs/plans/2026-08-06-deliver-ai-assessment-and-one-page-cv-v1.md`
  only to record verified Task 9 Fix 4 results

- [x] Verify no live child registry entry can be overwritten after stale lease
  recovery.
- [x] Verify unrelated queued work continues to use genuinely open dispatcher
  slots.
- [x] Verify dispatcher and server cleanup both escalate within bounded phases
  and no clean status is returned while either process remains alive.
- [x] Verify existing clean shutdown and child-failure status propagation remain
  backward compatible.
- [x] Run `uv run pytest` and record the passing count: 401 passed.
- [x] Run `uv run ruff check .` and `uv run ruff format --check .`.
- [x] Run `uv run mypy app tests scripts`.
- [x] Run `uv run python scripts/generate_ai_schemas.py --check`.
- [x] Run `uv lock --check` and `git diff --check`.

Verification results: `uv run pytest tests/test_dispatcher.py` — 8 passed;
`uv run pytest tests/test_cli.py` — 16 passed; combined dispatcher/CLI
regressions — 24 passed; full suite — 401 passed. Ruff check and format check,
mypy, generated-schema drift, lockfile, and Git whitespace checks all passed.

### Task 5: [Final] Update documentation and close the fix plan

**Files:**

- Modify: `README.md` only if shutdown status behavior needs user guidance
- Modify: `AGENTS.md` only if a reusable supervision invariant is discovered
- Move: this plan to `docs/plans/completed/`

- [x] Record final focused and full verification results in this plan and the
  main Task 9 plan.
- [x] Confirm README changes are unnecessary or document the observable launcher
  shutdown contract.
- [x] Confirm AGENTS changes are unnecessary or document the reusable rule that
  live child registry entries cannot be replaced before confirmed exit.
- [x] Confirm every checklist item is complete and no blocker remains.
- [x] Move this plan to `docs/plans/completed/` after all checks pass.

## Post-Completion

**Manual verification**

- Run `jobhunter`, request shutdown while the dispatcher and server are idle,
  and confirm the launcher exits zero with no remaining descendants.
- Repeat with controlled termination-resistant dispatcher and server processes;
  confirm bounded kill escalation and a non-zero status when cleanup is forced
  or incomplete.
- Recover an expired work lease while its old process is deliberately held
  alive and confirm no second worker starts for that work ID; release the old
  process and confirm the queued retry then runs normally.

**External system updates**

- Add a completion comment to the associated GitHub issue with the commit or PR
  link after implementation.
- Publish through a dedicated branch and pull request; never push or merge this
  work directly to `master`.
