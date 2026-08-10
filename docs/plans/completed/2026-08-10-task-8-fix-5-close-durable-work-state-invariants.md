# Task 8 Fix 5: Close Durable Work State Invariants

## Overview

- Correct the remaining durable-work defects found in the Task 8 review.
- Ensure typed assessment and CV work can succeed only through the domain
  repositories that atomically persist their immutable result and related
  lifecycle changes.
- Make failure reporting owner-qualified and idempotent across both queued
  retries and terminal failures.
- Reject structurally incomplete CV work and prevent work timestamps from
  recording transitions before the current attempt began.
- Use test-driven development (TDD): add each regression first, confirm it
  fails for the reviewed behavior, then implement the smallest complete fix.

## Tracking

- GitHub issue: [#40](https://github.com/nsal/JobHunter/issues/40)

## Context (from discovery)

- JobHunter is a Python 3.14 FastAPI application using direct `sqlite3`
  repositories and a fresh-schema policy.
- `app/work/repository.py` owns work enqueue, claim, heartbeat, checkpoint,
  failure, retry, and stale-recovery transitions. Its public `complete()`
  method can currently bypass domain persistence.
- `app/assessments.py` and `app/cv_generations.py` already provide the
  authoritative atomic success boundaries for their corresponding work types.
- `app/database.py` defines the work schema but does not bind work type to a
  required assessment or enforce monotonic attempt timestamps.
- `tests/test_work_repository.py`, `tests/test_assessment_service.py`, and
  `tests/test_cv_generator.py` contain helpers that currently rely on generic
  completion or incomplete CV work and must be corrected with the behavior.
- No dependency, migration, route, UI, dispatcher, or provider change is
  required. Task 9 remains responsible for worker orchestration.

## Development Approach

- **Testing approach:** TDD (tests first).
- For every task, write focused failing regressions and record the expected
  failure before modifying production code.
- Complete one task fully and run its focused tests plus the full suite before
  beginning the next task.
- Keep success persistence type-specific. Do not introduce a new public
  generic finalization path for the two existing domain work types.
- Keep validation and ownership decisions inside existing immediate SQLite
  transactions so concurrent stage, work, and ownership changes remain
  serialized.
- Prefer small schema and repository changes over dispatcher behavior assigned
  to Task 9.
- Add or update tests for every modified production method, covering success,
  ownership errors, invalid state, replay, and rollback behavior.
- Update this plan immediately if implementation scope or transaction
  boundaries change.
- Do not move to the next task while any focused or full-suite test is failing.

## Testing Strategy

- **Work repository tests:** invalid CV enqueue, type-specific association,
  failure replay ownership, new-claim reset behavior, heartbeat chronology,
  failure chronology, and absence of a generic success bypass.
- **Assessment tests:** success remains atomic with immutable persistence,
  lifecycle/follow-up work, and monotonic work completion; corrupt terminal
  fixtures do not require a production completion shortcut.
- **CV tests:** automatic and override work remain processable, completion is
  monotonic, invalid work cannot reach provider execution, and updated helpers
  use legitimate domain finalization.
- **Schema tests:** direct SQL cannot create CV work without an assessment or
  store impossible completed/heartbeat ordering.
- **Atomicity assertions:** every rejected enqueue, failure replay, or
  completion leaves domain rows, lifecycle, work ownership, follow-up work,
  and artefacts unchanged.
- **No browser E2E tests:** this fix changes repository invariants only; the
  existing route suite remains part of the full regression gate.
- **Final gates:** `uv run pytest`, `uv run ruff check .`,
  `uv run ruff format --check .`, `uv run mypy app tests`,
  `uv run python scripts/generate_ai_schemas.py --check`, `uv lock --check`,
  and `git diff --check`.

## Progress Tracking

- Mark completed items with `[x]` immediately after their tests pass.
- Add newly discovered work with a `➕` prefix.
- Add blockers with a `⚠️` prefix and identify the affected review finding.
- Record each failing TDD command and the focused/full passing counts.
- Keep this plan synchronized with actual file scope and behavior.
- Do not archive the plan until all automated gates pass.

## Solution Overview

Treat assessment and CV success as domain transactions, not generic work-state
updates. Remove the public `WorkRepository.complete()` bypass and retain
success transitions only in `AssessmentRepository.add_completed()` and
`CvGenerationRepository.add_completed()`. Replace production-shortcut test
setup with legitimate domain completion; use narrow direct-SQL fixtures only
when a test intentionally models corrupted historical state.

Make CV enqueue type-aware. A CV work item must reference an assessment owned
by the same application and carry the assessment's profile and JD hashes. The
repository will validate or derive those values in the enqueue transaction,
and the fresh schema will reject a CV row with a null assessment ID.

Add a dedicated safe failure-owner token to work state. Both queued retry and
terminal failure transitions retain the token that recorded the classified
failure. An exact same-owner replay returns the stored result without changing
timestamps or retry availability; wrong owners and changed failure payloads
are rejected. A new claim or stale recovery clears obsolete failure ownership.

Canonicalize transition timestamps as today, then compare success, failure,
and heartbeat times with the current attempt's `started_at` and latest
`heartbeat_at` under the same transaction. Store a transition only when its
time is not earlier than the work activity it follows.

## Technical Details

### Domain-only success finalization

- Remove `WorkRepository.complete()` because both supported work types have
  richer atomic completion requirements.
- Keep token-qualified success updates in the assessment and CV repositories
  after immutable result validation and insertion.
- Replace test uses that merely clear active work with legitimate assessment
  or CV completion paths.
- Model a deliberately corrupted `succeeded` row with a local SQL fixture only
  in tests that verify missing immutable-result replay rejection.
- Verify there is no public call path that can release active-work uniqueness
  while omitting the required immutable domain result.

### Processable CV enqueue

- Require a non-empty `assessment_id` for `WorkType.CV_GENERATION` and reject
  an assessment ID for `WorkType.ASSESSMENT`.
- Under `BEGIN IMMEDIATE`, verify the assessment belongs to the supplied
  application before inserting CV work.
- Bind the queued work's profile and JD hashes to the referenced assessment.
  Omitted values may be derived; supplied values must match.
- Add a work-table check requiring `assessment_id IS NOT NULL` for CV work and
  `assessment_id IS NULL` for assessment work.
- Preserve the existing automatic matched and explicit mismatch-override
  enqueue transactions and verify they satisfy the strengthened schema.

### Owner-qualified failure replay

- Add an optional `failure_token` column and corresponding `WorkItem` field.
- On a retry transition, persist the current token with the classified code
  and bounded safe message before clearing `worker_token`.
- On terminal failure, retain the failure token instead of accepting every
  later caller as an idempotent replay.
- Accept a queued-retry or terminal-failure replay only when the failure owner,
  code, and safe message match the stored transition. Preserve the original
  availability/completion timestamps on replay.
- Raise `StaleWorkerError` for a different token and `WorkStateError` for a
  changed failure from the same terminal/retry owner.
- Clear obsolete `failure_token` values on a new claim and in stale-recovery
  transitions, which are system-owned rather than worker-reported failures.

### Monotonic work timestamps

- Compare canonical timestamps, never raw offset representations.
- A heartbeat must not precede `started_at` or the existing heartbeat.
- Worker-reported failure and domain success must not precede the latest of
  `started_at` and `heartbeat_at` for the current attempt.
- Exact terminal replays return before chronology validation because they do
  not write a new timestamp.
- Add fresh-schema checks for completed/heartbeat ordering where SQLite can
  enforce the invariant independently of repository code.
- Keep lease-expiry policy unchanged: Task 9 recovery still decides when an
  expired running item is requeued or terminally failed.

## What Goes Where

- `app/work/models.py`: typed failure-owner state and any small canonical
  timestamp-order helper shared by repositories.
- `app/work/repository.py`: CV enqueue validation, failure replay ownership,
  monotonic heartbeat/failure transitions, and removal of generic success.
- `app/database.py`: fresh-schema work-type association, failure-owner, and
  timestamp-order constraints.
- `app/assessments.py`: monotonic assessment-work completion validation.
- `app/cv_generations.py`: monotonic CV-work completion validation.
- `tests/test_work_repository.py`: primary state-machine regressions.
- `tests/test_assessment_service.py`: assessment atomicity, timestamp, and
  corrected setup coverage.
- `tests/test_cv_generator.py`: CV enqueue/completion and corrected setup
  coverage.
- `tests/test_database.py`: direct schema-invariant coverage.
- `docs/plans/2026-08-06-deliver-ai-assessment-and-one-page-cv-v1.md`: record
  final Fix 5 evidence without marking Task 9 or later work complete.

## Implementation Steps

### Task 1: Remove the generic success-finalization bypass

**Files:**

- Modify: `app/work/repository.py`
- Modify: `tests/test_work_repository.py`
- Modify: `tests/test_assessment_service.py`
- Modify: `tests/test_cv_generator.py`

- [x] Add failing regressions proving no public work-repository operation can
  mark assessment or CV work `succeeded` without its immutable domain row.
- [x] Run the focused regressions and record the expected generic-completion
  failure before changing production code.
- [x] Remove `WorkRepository.complete()` and retain success transitions only
  in the assessment and CV domain repositories.
- [x] Replace test-helper uses of generic completion with legitimate domain
  completion when exercising valid workflows.
- [x] Add narrow direct-SQL corruption fixtures for missing-result replay tests
  without restoring a production bypass.
- [x] Update success tests to prove assessment/CV result persistence, work
  success, lifecycle changes, and follow-up enqueue remain atomic.
- [x] Run `uv run pytest tests/test_work_repository.py
  tests/test_assessment_service.py tests/test_cv_generator.py` and then
  `uv run pytest`; all must pass before Task 2.

### Task 2: Require structurally complete CV work at enqueue time

**Files:**

- Modify: `app/work/repository.py`
- Modify: `app/database.py`
- Modify: `tests/test_work_repository.py`
- Modify: `tests/test_database.py`
- Modify: `tests/test_cv_generator.py`

- [x] Add failing repository tests for missing/blank assessment IDs, an
  assessment owned by another application, mismatched profile/JD hashes, and
  successful hash derivation from a valid assessment.
- [x] Add failing direct-SQL schema tests proving CV work currently accepts a
  null assessment ID and assessment work accepts a non-null assessment ID.
- [x] Run the new tests and record the expected validation/schema failures.
- [x] Validate work-type-specific assessment identity under an immediate
  transaction in `WorkRepository.enqueue()`.
- [x] Derive omitted CV profile/JD hashes from the referenced assessment and
  reject supplied hashes that do not match it.
- [x] Add the fresh-schema type/assessment check while preserving automatic
  matched and mismatch-override enqueue behavior.
- [x] Update existing work/CV fixtures so every CV work item is processable and
  bound to its application's assessment.
- [x] Add success and rollback tests for ordinary CV enqueue plus both domain
  enqueue paths.
- [x] Run `uv run pytest tests/test_work_repository.py tests/test_database.py
  tests/test_cv_generator.py` and then `uv run pytest`; all must pass before
  Task 3.

### Task 3: Make worker-reported failure exactly owner-idempotent

**Files:**

- Modify: `app/work/models.py`
- Modify: `app/work/repository.py`
- Modify: `app/database.py`
- Modify: `tests/test_work_repository.py`
- Modify: `tests/test_database.py`

- [x] Add failing tests for an exact same-owner retry replay, exact terminal
  failure replay, wrong-owner retry/terminal replays, and altered same-owner
  failure metadata.
- [x] Add failing tests proving a new claim and stale recovery do not retain an
  obsolete worker's failure ownership.
- [x] Run the focused tests and record the current queued-replay
  `StaleWorkerError` and wrong-owner terminal acceptance.
- [x] Add `failure_token` to the fresh schema and typed `WorkItem` projection.
- [x] Persist failure ownership for queued retry and terminal worker-failure
  transitions without exposing arbitrary exception data.
- [x] Accept only exact owner/code/message replays without moving the original
  retry availability or terminal completion timestamp.
- [x] Clear obsolete failure ownership on claim and system stale recovery.
- [x] Add atomicity assertions proving rejected replays preserve attempts,
  ownership, error metadata, and timestamps byte-for-byte.
- [x] Run `uv run pytest tests/test_work_repository.py tests/test_database.py`
  and then `uv run pytest`; all must pass before Task 4.

### Task 4: Enforce monotonic attempt timestamps

**Files:**

- Modify: `app/work/models.py`
- Modify: `app/work/repository.py`
- Modify: `app/database.py`
- Modify: `app/assessments.py`
- Modify: `app/cv_generations.py`
- Modify: `tests/test_work_repository.py`
- Modify: `tests/test_assessment_service.py`
- Modify: `tests/test_cv_generator.py`
- Modify: `tests/test_database.py`

- [x] Add failing tests for a heartbeat before claim/current heartbeat, failure
  before latest activity, and assessment/CV completion before latest activity.
- [x] Add boundary success tests where transition time equals the latest work
  activity and offset-equivalent timestamps compare canonically.
- [x] Add direct-SQL tests for the fresh-schema completed/heartbeat ordering
  constraints.
- [x] Run the focused tests and record the impossible timestamp states that
  are currently accepted.
- [x] Add one small canonical timestamp-order helper and use it within each
  affected immediate transaction.
- [x] Reject backdated heartbeat and worker-failure transitions before updating
  work state.
- [x] Reject backdated assessment/CV completion before immutable insertion or
  work finalization, preserving exact terminal replay behavior.
- [x] Correct test helper clocks so valid workflows claim before they complete.
- [x] Add rollback assertions for domain rows, lifecycle, work ownership,
  follow-up work, and newly written artefacts.
- [x] Run `uv run pytest tests/test_work_repository.py
  tests/test_assessment_service.py tests/test_cv_generator.py
  tests/test_database.py` and then `uv run pytest`; all must pass before
  Task 5.

### Task 5: Verify Task 8 Fix 5 acceptance criteria

**Files:**

- Modify:
  `docs/plans/2026-08-10-task-8-fix-5-close-durable-work-state-invariants.md`

- [x] Verify assessment/CV work cannot succeed without the corresponding
  immutable result and domain transaction.
- [x] Verify every queued CV work item references an assessment owned by the
  same application and carries matching profile/JD hashes.
- [x] Verify worker failure is idempotent only for the exact failure owner and
  classified payload across retry and terminal states.
- [x] Verify heartbeat, failure, and domain completion timestamps never precede
  the latest current-attempt activity.
- [x] Verify all rejected operations are atomic and reveal no private data.
- [x] Run `uv run pytest` and record the complete passing count.
- [x] Run `uv run ruff check .` and `uv run ruff format --check .`.
- [x] Run `uv run mypy app tests`,
  `uv run python scripts/generate_ai_schemas.py --check`, and
  `uv lock --check`.
- [x] Run `git diff --check` and review the final diff against all four Fix 5
  findings.

### Task 6: Finalize documentation and archive Fix 5

**Files:**

- Modify: `docs/plans/2026-08-06-deliver-ai-assessment-and-one-page-cv-v1.md`
- Modify:
  `docs/plans/2026-08-10-task-8-fix-5-close-durable-work-state-invariants.md`
- Modify: `README.md` only if operator-visible behavior changes
- Modify: `AGENTS.md` only if a reusable project-wide rule is discovered
- Move:
  `docs/plans/2026-08-10-task-8-fix-5-close-durable-work-state-invariants.md`
  to `docs/plans/completed/`

- [x] Record the GitHub issue, TDD failure evidence, focused/full test counts,
  and final gate results in this plan.
- [x] Update the parent implementation plan with Fix 5 behavior and evidence
  without marking Task 9 or later tasks complete.
- [x] Update README only for operator-visible behavior; otherwise record that
  no README change is required.
- [x] Update AGENTS only for a reusable project-wide convention; otherwise
  record that no AGENTS change is required.
- [x] Run `uv run pytest` after the final documentation changes.
- [x] Confirm every checklist item is complete and move this plan to
  `docs/plans/completed/` only after every automated gate passes.

## Fix 5 Completion Evidence

- GitHub issue: [#40](https://github.com/nsal/JobHunter/issues/40).
- TDD evidence: before implementation, the reviewed behavior exposed a
  callable `WorkRepository.complete()` path; the first focused run after its
  removal reported the expected `AttributeError` in legacy generic-finalizer
  fixtures, while the new CV, failure-replay, and timestamp regressions were
  then corrected to the domain-finalizer contracts.
- Focused verification: `uv run pytest tests/test_work_repository.py
  tests/test_assessment_service.py tests/test_cv_generator.py
  tests/test_database.py` — 142 passed.
- Full verification: `uv run pytest` — 354 passed.
- Final gates passed: `uv run ruff check .`, `uv run ruff format --check .`,
  `uv run mypy app tests`, `uv run python scripts/generate_ai_schemas.py
  --check`, `uv lock --check`, and `git diff --check`.
- No README or AGENTS change is required. Task 9 and later tasks remain
  incomplete.

## Post-Completion

No manual UI, live-provider, deployment, migration, or production-data
verification is required. Task 9 must use only the domain success finalizers,
must supply processable assessment-bound CV work, and must preserve the failure
replay and monotonic timestamp contracts.

After implementation is committed or a PR is opened, add a comment to the
associated GitHub issue summarizing the changes and linking the commit or PR,
as required by the project workflow.
