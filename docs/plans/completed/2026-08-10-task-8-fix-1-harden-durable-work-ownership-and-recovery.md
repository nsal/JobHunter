# Task 8 Fix 1: Harden Durable Work Ownership and Recovery

## Overview

- Correct the Task 8 durable-work implementation before dispatcher and worker
  orchestration is built on top of it.
- Ensure only the worker holding the current token can persist assessment or
  CV results and finalize the corresponding work item.
- Bound stale-worker and timeout retries to two total attempts without leaving
  an application blocked by permanently unclaimable queued work.
- Prevent private values from entering persisted operational errors, make
  automatic and override generation enqueue behavior explicit and atomic, and
  compare only canonical UTC work timestamps.
- Use test-driven development: every behavioral correction starts with a
  focused failing regression test, followed by the smallest implementation
  needed to pass it.

## Context (from discovery)

- JobHunter is a Python 3.14 FastAPI application using direct `sqlite3`
  repositories, immutable assessment/CV records, and a fresh-schema approach.
- `app/work/repository.py` owns the new queued/running/succeeded/failed state
  machine, leases, retries, checkpoints, and operational errors.
- `app/assessments.py` currently persists an assessment, finalizes any active
  assessment work by application ID without checking its token, and uses
  `INSERT OR IGNORE` for matched-generation enqueue.
- `app/cv_generations.py` validates mismatch override eligibility and enqueues
  work through separate connections, leaving a time-of-check/time-of-use race.
- `app/repository.py` creates an application, initial stage, and assessment work
  atomically, while web-created timestamps are UTC values without an explicit
  offset.
- The existing focused work tests cover ordinary transitions but do not cover
  stale-worker completion, exhausted stale recovery, arbitrary-error
  redaction, built-in process timeouts, enqueue conflicts, override races, or
  offset-equivalent timestamps.
- No dependency or migration is needed. There is no production data, and the
  project deliberately initializes a fresh schema.

## Development Approach

- **Testing approach:** TDD. For each task, add a regression test and run it to
  confirm the expected failure before changing implementation code.
- Complete each task fully and mark its checklist immediately before moving to
  the next task.
- Make small changes at repository transaction boundaries; avoid introducing
  dispatcher or process-supervision behavior assigned to Task 9.
- Every modified path requires success, ownership/error, and concurrency or
  boundary tests as applicable.
- Run the focused tests after each red/green cycle, then run `uv run pytest`
  before beginning the next task.
- Use `uv run ruff check .`, `uv run ruff format --check .`, and
  `uv run mypy app tests` for the final engineering gate.
- Keep this plan synchronized if file scope, transaction boundaries, or public
  method signatures change during implementation.

## Testing Strategy

- **Work repository unit tests:** failure classification, safe messages,
  built-in process timeout retry, canonical timestamps, token rejection,
  attempt exhaustion, terminal state, and idempotency.
- **Assessment service/repository tests:** current-token completion, stale-token
  rollback, automatic generation enqueue, explicit active-work conflict, and
  no partial immutable assessment or lifecycle writes.
- **CV generation tests:** token-bound completion after atomic DOCX persistence
  and stale-token rollback without lifecycle or immutable-generation changes.
- **Override repository tests:** mismatch eligibility and work insertion in one
  transaction, duplicate-active-work rejection, and a deterministic concurrent
  stage-change test.
- **Schema/repository tests:** canonical stored timestamps and bounded terminal
  recovery without violating `attempt_count <= 2`.
- **No browser E2E tests:** this fix changes no route or rendered UI. Existing
  route tests remain part of the full regression suite.

## Progress Tracking

- Mark completed items with `[x]` immediately when done.
- Add newly discovered work with a `➕` prefix.
- Add blockers with a `⚠️` prefix and identify the affected review finding.
- Record focused and full-suite counts after each task.
- Do not archive this plan until all automated gates pass.

## Solution Overview

Use SQLite transactions as the ownership boundary. Assessment and CV
completion APIs will receive the work ID and worker token, verify that the
exact work item is running and belongs to the expected application/work type,
persist the immutable domain record, and finalize that same work row in one
transaction. A stale worker therefore cannot win after lease recovery.

Matched assessment completion will first finalize its owned assessment item,
then insert the generation item normally. Any active-work conflict will abort
the complete transaction instead of silently dropping generation. Mismatch
override validation and enqueue will likewise execute under one immediate
transaction.

The work repository will normalize every incoming timestamp to one UTC text
representation before storage or comparison. Failure classification will use
an allowlist: provider-safe errors retain bounded safe metadata, built-in
`TimeoutError` becomes a retryable process timeout, and unknown exceptions use
a generic message without interpolating exception text. Stale recovery will
requeue only first-attempt work and terminally fail expired second-attempt work.

## Technical Details

### Ownership and atomic completion

- Require `work_id` and `worker_token` when assessment or CV execution persists
  a successful result.
- Verify work ID, application ID, work type, `running` state, token, and—for CV
  work—the assessment ID under the same transaction as immutable persistence.
- Update work completion with a token-qualified `UPDATE`; treat zero affected
  rows as stale ownership and roll back all domain/lifecycle changes.
- Preserve idempotency for a repeated call from the worker that originally
  finalized the item. If this requires retaining a finalizer token, store only
  the opaque token needed to distinguish the owner; arbitrary stale tokens
  must not be accepted.
- Finalize CV work only after `cv-content.json` and the DOCX candidate have been
  atomically persisted and their hashes have been validated by the existing
  generation service.

### Retry and error policy

- Allow two claims total: the initial attempt and one retry.
- Requeue an expired running lease only when `attempt_count < 2`.
- Convert an expired second attempt to `failed`, set a canonical terminal
  timestamp, clear its token/lease, and release the active-work uniqueness
  constraint.
- Treat `TimeoutError` and the existing retryable provider timeout/rate-limit/
  connection/server codes as transient.
- Persist only allowlisted error codes and bounded safe messages. Unknown
  exceptions use stable generic code/message values and never `str(error)`.

### Timestamp contract

- Parse every repository timestamp as ISO-8601, interpret existing naive web
  values as UTC, convert aware values to UTC, and serialize one fixed format.
- Canonicalize enqueue, claim, heartbeat, retry availability, completion, and
  stale-recovery values before issuing SQL.
- Reject blank, malformed, or non-positive lease values at the repository
  boundary.
- Continue using SQLite text ordering only after canonicalization makes lexical
  and chronological order equivalent.

### Generation enqueue behavior

- Remove `INSERT OR IGNORE` from automatic generation enqueue.
- Roll back assessment persistence if generation enqueue cannot satisfy the
  one-active-work invariant.
- Validate mismatch stage, assessment ownership/outcome, and duplicate active
  work under one `BEGIN IMMEDIATE` transaction before inserting override work.
- Preserve the original mismatch assessment score and outcome.

## What Goes Where

- `app/work/models.py`: canonical work timestamp parsing/serialization and any
  typed failure/finalization values shared by repositories.
- `app/work/repository.py`: safe failure classification, bounded retry and
  stale recovery, ownership helpers, and canonical timestamp use.
- `app/assessments.py`: token-bound assessment persistence and automatic
  generation enqueue in one transaction.
- `app/assessment/service.py`: propagate the claimed work ID/token to
  persistence after artefact validation.
- `app/cv_generations.py`: atomic override enqueue and token-bound CV
  persistence/finalization.
- `app/cv/generator.py`: propagate the claimed work ID/token only after content
  and candidate writes have succeeded.
- `app/database.py`: only the minimal fresh-schema adjustment needed to retain
  finalizer ownership or terminal timing, if tests demonstrate it is required.
- `tests/`: TDD regressions adjacent to each affected repository/service.

## Implementation Steps

### Task 1: Canonicalize work timestamps and sanitize failure classification

**Files:**
- Modify: `app/work/models.py`
- Modify: `app/work/repository.py`
- Modify: `tests/test_work_repository.py`

- [x] Write failing tests for equivalent timestamps with different offsets,
  naive UTC input, malformed timestamps, and non-positive lease durations.
- [x] Write failing tests proving `TimeoutError` receives one retry and unknown
  exception text containing profile/API-key-like values is not persisted.
- [x] Run `uv run pytest tests/test_work_repository.py` and confirm the new
  tests fail for the expected reasons.
- [x] Implement one canonical UTC timestamp helper and apply it at every work
  repository write and comparison boundary.
- [x] Replace open-ended failure metadata with allowlisted codes/messages while
  retaining safe provider error details.
- [x] Update success and error tests for canonical stored values and bounded
  messages.
- [x] Run `uv run pytest tests/test_work_repository.py` and `uv run pytest`;
  both must pass before Task 2.

### Task 2: Bind assessment persistence and generation enqueue to work ownership

**Files:**
- Modify: `app/assessments.py`
- Modify: `app/assessment/service.py`
- Modify: `app/database.py` (only if owner-qualified idempotency requires it)
- Modify: `tests/test_assessment_service.py`
- Modify: `tests/test_repository.py`
- Modify: `tests/test_work_repository.py`

- [x] Write failing tests where worker A loses its lease, worker B reclaims the
  assessment, and worker A attempts to persist matched and mismatched results.
- [x] Write a failing test proving an automatic generation active-work conflict
  rolls back the assessment, work finalization, and lifecycle changes.
- [x] Run the focused assessment/work tests and confirm the new tests fail for
  stale ownership and silently ignored enqueue conflicts.
- [x] Require and propagate the claimed work ID/token through assessment
  execution and verify exact ownership inside `add_completed()`.
- [x] Finalize only the owned assessment row and replace `INSERT OR IGNORE` with
  an ordinary, explicit automatic-generation insert in the same transaction.
- [x] Add success tests for matched automatic enqueue and mismatch lifecycle
  transition, plus idempotency tests for the actual finalizing worker.
- [x] Run `uv run pytest tests/test_assessment_service.py
  tests/test_repository.py tests/test_work_repository.py` and
  `uv run pytest`; all must pass before Task 3.

### Task 3: Bound stale recovery and retry exhaustion

**Files:**
- Modify: `app/work/repository.py`
- Modify: `tests/test_work_repository.py`

- [x] Write a failing test for an expired second-attempt lease that currently
  becomes queued and then violates the schema on its next claim.
- [x] Write failing tests for first-attempt stale recovery, second-attempt
  terminal failure, active-work release, and repeated recovery idempotency.
- [x] Run `uv run pytest tests/test_work_repository.py` and confirm the retry
  exhaustion regression fails for the expected reason.
- [x] Requeue only stale work with one remaining attempt and terminally fail
  exhausted work atomically with safe error metadata and a canonical timestamp.
- [x] Preserve checkpoints needed by a valid retry while clearing worker lease
  ownership consistently.
- [x] Run `uv run pytest tests/test_work_repository.py` and `uv run pytest`;
  both must pass before Task 4.

### Task 4: Make mismatch override enqueue transactional

**Files:**
- Modify: `app/cv_generations.py`
- Modify: `tests/test_cv_generator.py`
- Modify: `tests/test_work_repository.py`

- [x] Write failing tests for duplicate active override work and for a current
  stage transition racing mismatch validation.
- [x] Write failing tests for missing assessment, matched assessment, and an
  assessment belonging to a different application.
- [x] Run the focused CV/work tests and confirm the new atomicity tests fail for
  the expected reason.
- [x] Execute mismatch eligibility lookup and work insertion under one
  immediate transaction using canonical timestamps.
- [x] Convert the active-work uniqueness failure to the stable work-state error
  without changing the original assessment or lifecycle.
- [x] Add success coverage proving one eligible mismatch override is queued
  with the correct assessment/profile/JD hashes.
- [x] Run `uv run pytest tests/test_cv_generator.py
  tests/test_work_repository.py` and `uv run pytest`; all must pass before
  Task 5.

### Task 5: Finalize CV work with the current worker token

**Files:**
- Modify: `app/cv_generations.py`
- Modify: `app/cv/generator.py`
- Modify: `app/database.py` (only if owner-qualified idempotency requires it)
- Modify: `tests/test_cv_generator.py`
- Modify: `tests/test_work_repository.py`

- [x] Write failing tests where a stale CV worker attempts persistence after a
  new worker has claimed the lease.
- [x] Write a failing atomic-write regression proving content/DOCX failure does
  not finalize work and database finalization failure removes newly written
  artefacts through the existing cleanup boundary.
- [x] Run the focused CV/work tests and confirm the new ownership/finalization
  tests fail for the expected reasons.
- [x] Require and propagate work ID/token through CV execution and atomically
  verify ownership, insert `cv_generations`, and finalize that exact work item.
- [x] Keep lifecycle unchanged on technical failure and preserve the existing
  Task 11 boundary for moving successful applications to `Ready for review`.
- [x] Add success and owner-qualified idempotency tests for matched and mismatch
  override generation.
- [x] Run `uv run pytest tests/test_cv_generator.py
  tests/test_work_repository.py` and `uv run pytest`; all must pass before
  Task 6.

### Task 6: Verify Task 8 Fix 1 acceptance criteria

**Files:**
- Modify: `docs/plans/2026-08-10-task-8-fix-1-harden-durable-work-ownership-and-recovery.md`

- [x] Verify stale assessment and CV workers cannot persist or finalize after
  lease loss.
- [x] Verify matched assessment persistence and automatic generation enqueue
  are atomic and never silently ignored.
- [x] Verify mismatch override eligibility and enqueue are atomic.
- [x] Verify provider and process timeouts receive at most one retry and stale
  second attempts become terminal without blocking new work.
- [x] Verify persisted unknown errors contain no original exception text and
  all work timestamps use the canonical UTC representation.
- [x] Run `uv run pytest` and record the complete passing count.
- [x] Run `uv run ruff check .` and `uv run ruff format --check .`.
- [x] Run `uv run mypy app tests`, the committed schema drift check, and
  `uv lock --check`.
- [x] Run `git diff --check` and review the final diff against all seven review
  findings.

### Task 7: Finalize documentation and archive the fix plan

**Files:**
- Modify: `docs/plans/2026-08-06-deliver-ai-assessment-and-one-page-cv-v1.md`
- Modify: `README.md` (only if operator-visible behavior changed)
- Modify: `AGENTS.md` (only if a reusable project-wide rule was discovered)
- Move: `docs/plans/2026-08-10-task-8-fix-1-harden-durable-work-ownership-and-recovery.md`
  to `docs/plans/completed/`

- [x] Record Task 8 Fix 1 results and test counts in the parent implementation
  plan without marking later dispatcher/UI tasks complete.
- [x] Update README only if retry/error behavior needs operator documentation;
  otherwise record that no README change is required.
- [x] Update AGENTS only for a genuinely reusable project-wide convention;
  otherwise record that no AGENTS change is required.
- [x] Confirm every checklist item is complete and all automated gates remain
  passing.
- [x] Move this plan to `docs/plans/completed/`.

Verification results: focused durable-work, assessment, and CV tests passed
(59 tests). The complete suite passed with 277 tests. `uv run ruff check .`,
`uv run ruff format --check .`, `uv run mypy app tests`,
`uv run python scripts/generate_ai_schemas.py --check`, `uv lock --check`,
and `git diff --check` all passed. README and AGENTS.md required no changes.

## Post-Completion

No manual UI, external provider, or deployment verification is required for
this repository-layer correction. The subsequent Task 9 dispatcher/runner
implementation should consume only the corrected token-bound APIs and should
add real spawned-process coverage without weakening these ownership rules.
