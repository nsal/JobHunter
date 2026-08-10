# Task 8 Fix 6: Close Work Result, Enqueue, and Timestamp Invariants

## Overview

- Correct the three remaining durable-work defects found in the Task 8 review.
- Bind each succeeded assessment work item to the immutable assessment it
  created so terminal replay cannot substitute another job's result.
- Prevent generic CV enqueue from creating work for an assessment that already
  has an immutable CV generation.
- Require schema-validated work transition timestamps to represent real UTC
  calendar instants while retaining exact microsecond ordering.
- Use test-driven development (TDD): add each regression first, confirm it
  fails for the reviewed behavior, then implement the smallest complete fix.

## Tracking

- GitHub issue: [#41](https://github.com/nsal/JobHunter/issues/41).

## Context (from discovery)

- JobHunter is a Python 3.14 FastAPI application using direct `sqlite3`
  repositories and a fresh-schema policy.
- `app/assessments.py` verifies a succeeded work row and an immutable
  assessment independently during replay. Reusing a worker token across two
  assessment jobs therefore permits a cross-work result substitution.
- `app/work/repository.py` binds generic CV work to an assessment and its
  profile/JD hashes, but it does not reject an assessment already present in
  `cv_generations`. `CvGenerationRepository.enqueue_override()` already has
  the required completed-generation guard.
- `app/database.py` uses a fixed-width UTC `GLOB` plus lexical comparison for
  exact work timestamp ordering. The shape check still accepts impossible
  calendar and clock values such as month or hour `99`.
- The affected tests are `tests/test_assessment_service.py`,
  `tests/test_work_repository.py`, and `tests/test_database.py`. No dependency,
  route, UI, provider, migration, or Task 9 dispatcher change is required.

## Development Approach

- **Testing approach:** TDD (tests first).
- For every task, add the focused regression and record its expected failure
  before changing production code.
- Complete each task fully and run its focused tests plus the full suite before
  moving to the next task.
- Make small, repository-local changes and preserve the current immediate
  SQLite transaction boundaries.
- Add or update tests for every modified production method, including success,
  replay/error, ownership, and rollback behavior.
- Keep the result association explicit in durable state rather than inferring
  it from worker-token uniqueness or completion timestamps.
- Preserve exact microsecond ordering with canonical UTC text comparisons;
  semantic timestamp validation must be a separate schema predicate and must
  not return to lossy Julian-day ordering.
- Update this plan immediately if implementation scope, schema semantics, or
  transaction boundaries change.
- Do not begin the next task while any focused or full-suite test is failing.

## Testing Strategy

- **Assessment repository tests:** two assessment jobs for one application may
  reuse a token, but work A must reject assessment B's exact immutable payload;
  the original work/result replay must remain idempotent.
- **Assessment schema tests:** queued, running, and failed assessment work must
  not claim a result; succeeded assessment work must reference its immutable
  assessment. Corrupt historical replay fixtures must bypass constraints only
  within the narrow fixture setup.
- **CV enqueue tests:** generic enqueue must reject an assessment with an
  existing immutable generation and leave work/domain state unchanged; an
  eligible assessment must still derive and validate profile/JD hashes.
- **Timestamp schema tests:** reject invalid month, day, hour, minute, and
  second values; accept valid leap-day and microsecond-forward transitions;
  continue rejecting offset-shaped and one-microsecond-backdated values.
- **Atomicity assertions:** every rejected replay or enqueue preserves
  immutable results, work ownership/history, lifecycle state, and active-work
  availability byte-for-byte.
- **No browser E2E tests:** these corrections change repository and fresh-schema
  invariants only; the route suite remains part of the full regression gate.
- **Final gates:** `uv run pytest`, `uv run ruff check .`,
  `uv run ruff format --check .`, `uv run mypy app tests`,
  `uv run python scripts/generate_ai_schemas.py --check`, `uv lock --check`,
  and `git diff --check`.

## Progress Tracking

- Mark completed items with `[x]` immediately after their tests pass.
- Record each initial failing TDD command and the focused/full passing counts.
- Add newly discovered work with a `➕` prefix.
- Add blockers with a `⚠️` prefix and identify the affected review finding.
- Keep this plan synchronized with actual file scope and behavior.
- Do not archive the plan until every checklist item and automated gate passes.

### Fix 6 implementation evidence

- Initial TDD command `uv run pytest tests/test_assessment_service.py::test_assessment_completion_replay_cannot_cross_work_results tests/test_database.py::test_succeeded_assessment_work_requires_result_association tests/test_database.py::test_work_schema_rejects_semantically_invalid_transition_timestamps -q` failed 3 tests: cross-work replay was accepted, succeeded assessment work without a result was accepted, and impossible timestamps were accepted.
- The initial generic-enqueue regression run also exposed a fixture binding-count error; after correcting that fixture, the reviewed duplicate-enqueue behavior was covered by the passing regression and by an actual completed-generation workflow regression in `tests/test_cv_generator.py`.
- Focused assessment/database/work suite: 75 passed. CV/work repository suite: 87 passed. Full suite: 361 passed.
- All final Ruff, formatting, mypy, generated-schema, lockfile, and whitespace gates passed.

## Solution Overview

Use `work_items.assessment_id` as the durable result association for succeeded
assessment work as well as the input association for CV work. Keep it null for
assessment work before success and for failed assessment work. Set it in the
same token-qualified transaction that inserts the immutable assessment and
marks the work succeeded. Terminal assessment replay must require that stored
association before comparing the immutable payload.

Extend generic CV enqueue's existing immediate transaction with a lookup for an
immutable generation owned by the same application and assessment. Reject the
enqueue with a stable `WorkStateError` before inserting work. Keep ordinary
assessment lookup and profile/JD hash derivation unchanged.

Retain fixed-width canonical UTC timestamp strings for precise lexical
ordering. Strengthen the schema predicate so each non-null transition timestamp
also round-trips through SQLite's calendar/date functions for its date and
clock components. This rejects impossible values without using `julianday()`
for ordering and losing microseconds.

## Technical Details

### Durable assessment result association

- Make the work-type/assessment check state-aware: CV work always requires an
  assessment input; assessment work requires a result only when succeeded and
  must otherwise keep `assessment_id` null.
- Insert the immutable assessment first, then set the assessment work row's
  `assessment_id`, success state, completion timestamp, and finalizer token in
  the existing token-qualified update.
- On replay, require the stored work association to equal the supplied
  assessment ID before comparing the full canonical immutable payload.
- Preserve wrong-owner `StaleWorkerError` behavior and stable
  `AssessmentStateError` behavior for missing or substituted results.
- Update the deliberately corrupt missing-result fixture narrowly; do not
  weaken production constraints or restore a generic finalization path.

### Completed-generation enqueue guard

- Under `BEGIN IMMEDIATE`, check `cv_generations` for the supplied
  application/assessment before inserting generic CV work.
- Raise a stable `WorkStateError` when an immutable generation already exists.
- Perform the check before work insertion and retain the existing assessment
  ownership and hash-binding validations.
- Prove rejection does not create active work or mutate the immutable
  assessment/generation, lifecycle, or prior terminal work.

### Semantic and precise work timestamps

- Keep the exact `YYYY-MM-DDTHH:MM:SS.ffffff+00:00` shape requirement.
- Add semantic checks for valid calendar date and clock components using
  SQLite normalization/round-trip functions.
- Continue comparing canonical UTC strings lexically so all six fractional
  digits participate in ordering.
- Apply the predicate consistently to `started_at`, `heartbeat_at`, and
  `completed_at` whenever those values participate in a transition check.
- Test leap years, impossible calendar/clock values, equality, forward
  microseconds, backdated microseconds, and noncanonical offsets directly.

## What Goes Where

- `app/assessments.py`: persist and require the assessment work/result link.
- `app/work/repository.py`: reject generic CV enqueue after immutable success.
- `app/database.py`: state-aware assessment association and semantic timestamp
  constraints.
- `tests/test_assessment_service.py`: cross-work replay, exact replay,
  wrong-owner, corruption fixture, and atomicity regressions.
- `tests/test_work_repository.py`: completed-generation enqueue rejection and
  valid hash-derived enqueue coverage.
- `tests/test_database.py`: direct state/result association and semantic,
  microsecond-precise timestamp constraints.
- `docs/plans/2026-08-06-deliver-ai-assessment-and-one-page-cv-v1.md`: record
  final Fix 6 evidence without marking Task 9 or later work complete.

## Implementation Steps

### Task 1: Bind succeeded assessment work to its immutable result

**Files:**

- Modify: `app/assessments.py`
- Modify: `app/database.py`
- Modify: `tests/test_assessment_service.py`
- Modify: `tests/test_database.py`

- [x] Add a failing repository regression that completes two assessment jobs
  for one application with the same worker token, then replays work A with
  assessment B's exact payload and expects `AssessmentStateError`.
- [x] Add failing schema regressions proving succeeded assessment work requires
  an assessment result association while queued, running, and failed
  assessment work reject one.
- [x] Run the focused regressions and record the expected cross-work replay and
  schema failures before changing production code.
- [x] Make the schema's assessment association constraint state-aware without
  weakening the always-associated CV work requirement.
- [x] Set `assessment_id` in the token-qualified assessment success update and
  require the stored association during terminal replay.
- [x] Update the narrow missing-result corruption fixture without adding a
  production bypass.
- [x] Add exact same-work replay, wrong-owner, changed-result, and transaction
  snapshot assertions for all success and rejection paths.
- [x] Run `uv run pytest tests/test_assessment_service.py
  tests/test_database.py`, then `uv run pytest`; all must pass before Task 2.

### Task 2: Reject generic CV work after immutable generation

**Files:**

- Modify: `app/work/repository.py`
- Modify: `tests/test_work_repository.py`
- Modify: `tests/test_cv_generator.py` only if shared completion setup is needed

- [x] Add a failing regression that completes a CV generation, calls generic
  `WorkRepository.enqueue()` for the same assessment, and expects
  `WorkStateError`.
- [x] Assert the rejected enqueue preserves assessments, generations,
  lifecycle, prior terminal work, and active-work count byte-for-byte.
- [x] Add or retain success coverage proving eligible CV work derives omitted
  profile/JD hashes and accepts exact supplied hashes.
- [x] Run the focused regressions and record the expected duplicate enqueue
  before changing production code.
- [x] Add the completed-generation eligibility check inside the existing
  immediate enqueue transaction, before `work_items` insertion.
- [x] Preserve stable wrong-application, missing-assessment, hash-mismatch, and
  active-work error behavior with focused tests.
- [x] Run `uv run pytest tests/test_work_repository.py
  tests/test_cv_generator.py`, then `uv run pytest`; all must pass before
  Task 3.

### Task 3: Reject semantically invalid work timestamps exactly

**Files:**

- Modify: `app/database.py`
- Modify: `tests/test_database.py`

- [x] Add failing direct-SQL regressions for invalid month, day, hour, minute,
  and second values that match the canonical timestamp shape.
- [x] Add success regressions for a valid leap day, equal transitions, and a
  one-microsecond-forward transition.
- [x] Retain regressions for offset-shaped input and one-microsecond-backdated
  heartbeat/completion values.
- [x] Run `uv run pytest tests/test_database.py` and record the expected
  semantic-validation failures before changing the schema.
- [x] Add reusable semantic date/clock checks while retaining the exact GLOB
  and fixed-width lexical ordering predicates.
- [x] Verify malformed timestamp rejection raises `sqlite3.IntegrityError` and
  leaves no partial work row.
- [x] Run `uv run pytest tests/test_database.py`, then `uv run pytest`; all
  must pass before Task 4.

### Task 4: Verify Task 8 Fix 6 acceptance criteria

**Files:**

- Modify:
  `docs/plans/2026-08-10-task-8-fix-6-close-work-result-enqueue-and-timestamp-invariants.md`

- [x] Verify assessment replay can succeed only for the immutable result bound
  to that exact work item and original finalizer.
- [x] Verify generic CV enqueue rejects every assessment with an immutable
  generation and preserves all domain/work state atomically.
- [x] Verify direct SQL rejects impossible calendar/clock values and backdated
  microseconds while valid canonical microsecond transitions succeed.
- [x] Run `uv run pytest` and record the complete passing count.
- [x] Run `uv run ruff check .` and `uv run ruff format --check .`.
- [x] Run `uv run mypy app tests`,
  `uv run python scripts/generate_ai_schemas.py --check`, and
  `uv lock --check`.
- [x] Run `git diff --check` and review the final diff against all three Fix 6
  findings.

### Task 5: Finalize documentation and archive Fix 6

**Files:**

- Modify: `docs/plans/2026-08-06-deliver-ai-assessment-and-one-page-cv-v1.md`
- Modify:
  `docs/plans/2026-08-10-task-8-fix-6-close-work-result-enqueue-and-timestamp-invariants.md`
- Modify: `README.md` only if operator-visible behavior changes
- Modify: `AGENTS.md` only if a reusable project-wide rule is discovered
- Move:
  `docs/plans/2026-08-10-task-8-fix-6-close-work-result-enqueue-and-timestamp-invariants.md`
  to `docs/plans/completed/`

- [x] Record the GitHub issue, failing TDD evidence, focused/full test counts,
  and final gate results in this plan.
- [x] Update the parent implementation plan with Fix 6 behavior and evidence
  without marking Task 9 or later tasks complete.
- [x] Update README only for operator-visible behavior; otherwise record that
  no README change is required.
- [x] Update AGENTS only for a reusable project-wide convention; otherwise
  record that no AGENTS change is required.
- [x] Run `uv run pytest` after final documentation changes.
- [x] Confirm every checklist item is complete and move this plan to
  `docs/plans/completed/` only after every automated gate passes.

## Post-Completion

No manual UI, live-provider, deployment, migration, or production-data
verification is required. Task 9 must preserve the exact work/result binding,
must not enqueue generation for an assessment with immutable CV output, and
must continue writing only canonical, semantically valid UTC work timestamps.

After implementation is committed or a PR is opened, add a comment to the
associated GitHub issue summarizing the changes and linking the commit or PR,
as required by the project workflow.
