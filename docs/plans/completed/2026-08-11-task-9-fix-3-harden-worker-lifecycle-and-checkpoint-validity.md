# Task 9 Fix 3: Harden Worker Lifecycle and Checkpoint Validity

## Overview

This bug fix closes the five remaining Task 9 defects found in review. It makes
dispatcher shutdown budgets cover configured poll latency, prevents surviving
workers from being discarded, reports forced or failed shutdown accurately,
invalidates CV checkpoints when the editable target role changes, and retries
transient heartbeat database failures without abandoning a live lease.

The changes preserve the existing launcher, single dispatcher, maximum-three
spawned workers, SQLite ownership tokens, and typed checkpoint architecture.
They strengthen lifecycle and checkpoint correctness without adding UI,
provider, scheduling, or distributed-worker scope.

## Context (from discovery)

- `app/cli.py` derives one dispatcher supervision timeout and currently returns
  success after requested shutdown even when it must terminate the dispatcher.
- `app/work/dispatcher.py` can sleep for the configured poll interval before
  seeing a stop event, but its supervision helper allows only a fixed one-second
  observation margin. Its final cleanup also removes children without proving
  they exited.
- `app/work/runner.py` stops heartbeats after every repository exception, so a
  transient SQLite `OperationalError` can eventually expire otherwise live
  work.
- `app/cv/generator.py` uses the application role in provider input and DOCX
  rendering, while its checkpoint hash set omits that editable value.
- Work checkpoint hashes flow through `app/database.py`, `app/work/models.py`,
  `app/work/repository.py`, and `app/work/runner.py`; role binding must follow
  that same durable, allowlisted path.
- Existing Task 9 tests use process, clock, sleeper, repository, and service
  doubles. The current full suite passes, but it lacks the reviewed failure
  scenarios.

## Development Approach

- **Testing approach**: TDD, consistent with Task 9 Fixes 1 and 2.
- Add a focused failing regression before each production change and confirm it
  fails for the reviewed reason.
- Complete each task fully before moving to the next task.
- Keep changes small and limited to the five reviewed defects.
- Every task that changes code must add or update tests for its success and
  error or edge paths.
- All focused tests must pass before starting the next task.
- Update this plan immediately when implementation scope or interfaces change.
- Preserve synchronous service callers, token-bound ownership, safe error
  redaction, checkpoint provenance, and existing retry limits.
- Use `uv` for execution, Ruff for linting and formatting, and mypy for static
  verification.

## Testing Strategy

- **Launcher tests**: cover requested shutdown, dispatcher failure during
  shutdown, forced termination, exit-status propagation, and the full timeout
  derived from a legal long poll interval.
- **Dispatcher tests**: cover interruptible idle waits, cooperative completion,
  forced termination, delayed termination, confirmed child exit, durable
  failure recording, and no discarded live child.
- **Runner tests**: cover a transient SQLite heartbeat failure followed by a
  successful renewal, plus terminal ownership-loss behavior.
- **CV service tests**: cover role-preserving checkpoint replay and role-change
  invalidation with ordinary provider fallback.
- **Repository and database tests**: cover fresh and migrated `role_sha256`
  storage, checkpoint persistence, malformed values, and legacy null values.
- **No browser E2E tests**: this fix changes no route, form, template, or user
  interaction. The bounded spawned-process smoke test remains the relevant
  process-level regression.
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

Use explicit lifecycle signals and durable input hashes:

1. Replace uninterruptible dispatcher idle sleeping with a stop-aware wait, or
   include the maximum possible observation delay in the shared shutdown budget.
   The preferred implementation is stop-aware waiting because it shortens real
   shutdown and removes timing arithmetic from correctness.
2. Keep each worker in the dispatcher's child registry until `is_alive()` is
   false. After cooperative grace, call `terminate()`; after the forced grace,
   escalate with `kill()` where supported and join within a final bounded phase.
3. Return a non-zero launcher status when dispatcher shutdown is forced or the
   dispatcher exits non-zero while the launcher is stopping.
4. Add an allowlisted `role_sha256` work/checkpoint field. Hash the exact target
   role used in CV request construction and rendering, and require it to match
   before checkpoint reuse.
5. Treat SQLite operational contention during a heartbeat as retryable on the
   next interval. Continue stopping immediately for stale ownership or invalid
   work state, and keep exception details out of durable or user-visible data.

## Technical Details

### Shutdown observation and budgets

- Extend the stop-signal protocol with a bounded `wait(timeout)` operation, or
  provide an equivalent injected wait function for deterministic tests.
- Use the stop signal for idle polling so a shutdown request wakes the
  dispatcher immediately even when `poll_interval_seconds` is large.
- Keep one shared helper for cooperative, terminate, kill, and parent
  supervision budgets. The launcher timeout must be strictly greater than the
  dispatcher's complete internal cleanup contract.
- Apply the same contract on startup failure, server failure, requested
  shutdown, and dispatcher failure paths.

### Child termination guarantees

- Extend both dispatcher and launcher process protocols only as far as needed
  for a portable hard-stop operation.
- Never remove a child from `_children` while `is_alive()` remains true.
- Persist the safe shutdown diagnostic only after the process is confirmed
  stopped, so no still-running worker can be represented as durably reclaimed.
- If a process survives every bounded cleanup phase, propagate dispatcher
  failure to the launcher rather than returning a clean status.

### Launcher exit status

- Track whether dispatcher shutdown completed cooperatively, required forced
  termination, or produced a non-zero exit code.
- Preserve the original server or dispatcher failure code when one long-lived
  child initiates shutdown.
- Return non-zero for forced or failed dispatcher cleanup during a user-requested
  shutdown; return zero only after both supervised processes are confirmed
  stopped under the clean shutdown contract.

### Role-bound CV checkpoints

- Add nullable `role_sha256` to `work_items`, including the fresh schema,
  idempotent existing-database migration, `WorkItem`, repository allowlist, and
  runner checkpoint hydration.
- Compute the digest from the exact trimmed `generation_input.role` consumed by
  `_request_input` and the word writer.
- Store the digest atomically with content path, content digest, provenance, and
  other checkpoint hashes.
- Require the current role digest to match before loading checkpoint content.
  Legacy checkpoints without it safely fall back to fresh generation and are
  replaced under the current worker token.
- Keep `prompt_sha256` as the instruction digest because completion repositories
  already compare it with immutable `instruction_sha256` metadata.

### Heartbeat retry classification

- Catch `sqlite3.OperationalError` separately and retry after the next normal
  heartbeat interval, with no tight loop and no exception-text persistence.
- Stop renewal for `StaleWorkerError`, `WorkStateError`, and
  `WorkNotFoundError`, because they prove the current process no longer owns
  renewable work.
- Preserve conservative stop behavior for unexpected programming or validation
  failures unless implementation discovery proves a safer bounded policy.

## What Goes Where

- **Implementation Steps** contain code, tests, migrations, and in-repository
  documentation achievable in this repository.
- **Post-Completion** contains manual process-tree verification and GitHub
  workflow actions that require external state.

## Implementation Steps

### Task 1: Make dispatcher idle polling stop-aware

**Files:**

- Modify: `app/work/dispatcher.py`
- Modify: `tests/test_dispatcher.py`

- [x] Add a failing test with a valid long poll interval proving a stop request
  wakes the dispatcher without consuming the full polling delay.
- [x] Add an edge test proving ordinary idle operation still waits for exactly
  the configured interval when no stop is requested.
- [x] Extend the stop-signal boundary with the smallest bounded-wait interface
  needed by threading and multiprocessing events.
- [x] Replace idle `sleep` with stop-aware waiting while preserving immediate
  slot refill after queue or process activity.
- [x] Run `uv run pytest tests/test_dispatcher.py`; all tests must pass before
  Task 2.

### Task 2: Retain and hard-stop surviving worker processes

**Files:**

- Modify: `app/work/dispatcher.py`
- Modify: `tests/test_dispatcher.py`

- [x] Add a failing process-double test whose first `terminate()` does not make
  it exit and prove the dispatcher does not remove it from supervision.
- [x] Add success and edge tests for cooperative exit, terminate-phase exit,
  kill-phase exit, durable failure recording, and bounded cleanup exhaustion.
- [x] Extend `ProcessLike` with a portable hard-stop operation and explicit
  cleanup-result reporting.
- [x] Keep child entries until confirmed exit, then join and record the safe
  token-bound shutdown failure exactly once.
- [x] Make exhausted cleanup fail the dispatcher instead of silently discarding
  a live child.
- [x] Run `uv run pytest tests/test_dispatcher.py`; all tests must pass before
  Task 3.

### Task 3: Propagate complete dispatcher shutdown outcomes

**Files:**

- Modify: `app/cli.py`
- Modify: `app/work/dispatcher.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_dispatcher.py`

- [x] Add failing launcher tests for requested shutdown where the dispatcher
  exits non-zero and where it exceeds the complete cleanup contract.
- [x] Add a long-poll regression proving the launcher allows the complete
  dispatcher contract and does not terminate it during worker finalization.
- [x] Define one shared supervision budget covering stop observation,
  cooperative drain, terminate, kill, joins, and a bounded parent margin.
- [x] Return non-zero when requested shutdown requires dispatcher termination or
  observes a failed dispatcher exit; preserve sibling failure codes elsewhere.
- [x] Add edge tests proving clean requested shutdown still returns zero and all
  supervised processes are confirmed stopped.
- [x] Run `uv run pytest tests/test_cli.py tests/test_dispatcher.py`; all tests
  must pass before Task 4.

### Task 4: Bind CV checkpoints to the target role

**Files:**

- Modify: `app/database.py`
- Modify: `app/work/models.py`
- Modify: `app/work/repository.py`
- Modify: `app/work/runner.py`
- Modify: `app/cv/generator.py`
- Modify: `tests/test_database.py`
- Modify: `tests/test_work_repository.py`
- Modify: `tests/test_runner.py`
- Modify: `tests/test_cv_generator.py`

- [x] Add failing fresh-schema and migration tests for nullable, length-checked
  `role_sha256`, including idempotent initialization of an existing database.
- [x] Add failing model/repository/runner tests proving the role digest persists
  atomically and reaches `WorkCheckpoint.hashes` without raw role text.
- [x] Add a valid-resume CV test proving an unchanged role still skips the
  provider and preserves exact checkpoint provenance.
- [x] Add a changed-role regression proving an old checkpoint is rejected,
  ordinary provider generation runs with the new role, and checkpoint metadata
  is replaced under current ownership.
- [x] Implement the schema, migration, typed model, repository allowlist, runner
  hydration, role hashing, and checkpoint validation changes.
- [x] Add edge tests for legacy null role hashes, malformed role digests, stale
  worker writes, and role changes during deterministic rendering.
- [x] Run `uv run pytest tests/test_database.py tests/test_work_repository.py
  tests/test_runner.py tests/test_cv_generator.py`; all tests must pass before
  Task 5.

### Task 5: Retry transient heartbeat database failures

**Files:**

- Modify: `app/work/runner.py`
- Modify: `tests/test_runner.py`

- [x] Add a failing heartbeat test where the first renewal raises
  `sqlite3.OperationalError` and the next scheduled renewal succeeds.
- [x] Add ownership-loss tests for stale token, invalid work state, and missing
  work proving renewal still stops immediately.
- [x] Classify transient SQLite operational failures separately and retry only
  after the configured interval without a tight loop.
- [x] Preserve safe handling for unexpected exceptions and ensure no database,
  provider, path, or private-input details escape the heartbeat thread.
- [x] Run `uv run pytest tests/test_runner.py tests/test_work_repository.py`;
  all tests must pass before Task 6.

### Task 6: Verify acceptance criteria and regression safety

**Files:**

- Modify: `docs/plans/2026-08-06-deliver-ai-assessment-and-one-page-cv-v1.md`
  only to record verified Task 9 Fix 3 results

- [x] Verify legal long polling cannot consume dispatcher drain time after a
  stop request.
- [x] Verify no worker is removed from supervision before confirmed exit and
  cleanup exhaustion reaches the launcher as failure.
- [x] Verify clean shutdown returns zero while forced or failed dispatcher
  shutdown returns non-zero.
- [x] Verify unchanged-role CV checkpoints replay exactly and changed-role or
  legacy checkpoints safely use fresh provider generation.
- [x] Verify transient SQLite heartbeat contention retries while ownership loss
  stops renewal.
- [x] Run `uv run pytest` and record the passing count: 391 passed.
- [x] Run `uv run ruff check .` and `uv run ruff format --check .`.
- [x] Run `uv run mypy app tests scripts`.
- [x] Run `uv run python scripts/generate_ai_schemas.py --check`.
- [x] Run `uv lock --check` and `git diff --check`.

### Task 7: [Final] Update documentation and close the fix plan

**Files:**

- Modify: `README.md` only if launcher shutdown behavior needs user guidance
- Modify: `AGENTS.md` only if a reusable worker-lifecycle rule is discovered
- Move: this plan to `docs/plans/completed/`

- [x] Record final focused and full verification results in this plan and the
  main Task 9 plan.
- [x] Confirm README changes are unnecessary or document the observable CLI
  shutdown exit-status contract.
- [x] Confirm AGENTS changes are unnecessary or document any new durable worker
  invariant that future tasks must preserve.
- [x] Confirm every checklist item is complete and no blocker remains.
- [x] Move this plan to `docs/plans/completed/` after all checks pass.

### Verification record

- Focused lifecycle/checkpoint suite: 127 passed.
- Full suite: 391 passed.
- Ruff check and format check: passed.
- Mypy (`app tests scripts`): passed with no issues in 55 source files.
- Generated-schema drift, lockfile, and Git whitespace checks: passed.
- README and AGENTS updates were not needed; no new user-facing command is
  required for the corrected shutdown status behavior.

## Post-Completion

**Manual verification**

- Run `jobhunter` with a legal non-default long poll interval, start at least one
  bounded fake-provider work item, request shutdown during idle supervision, and
  inspect the process tree until every worker exits.
- Repeat with a controlled termination-resistant worker and verify bounded hard
  cleanup plus a non-zero launcher status.
- Resume CV work after editing the target role and confirm the provider receives
  the new role and the resulting DOCX uses only the new target context.

**External system updates**

- Add a completion comment to the associated GitHub issue with the commit or PR
  link after implementation.
- Publish through a dedicated branch and pull request; never push or merge this
  work directly to `master`.
