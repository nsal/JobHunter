# Task 8 Fix 4: Enforce Exact Completion Replay Integrity

## Overview

- Correct the two terminal-idempotency defects found after Task 8 Fix 3.
- Accept a repeated assessment or CV completion only when the original
  finalizer supplies the exact payload already stored in the immutable domain
  row.
- Reject altered same-finalizer replays without changing the immutable row,
  lifecycle, terminal work history, or generated artefacts.
- Close the remaining assessment preflight and post-preflight ownership-race
  test gaps identified during review.
- Use test-driven development: add each focused regression first, confirm the
  reviewed defect fails, and then make the smallest repository change that
  passes it.

## Tracking

- GitHub issue: [#39](https://github.com/nsal/JobHunter/issues/39)

## Context (from discovery)

- JobHunter is a Python 3.14 FastAPI application with direct `sqlite3`
  repositories, immutable assessment/CV records, and durable token-owned work.
- `AssessmentRepository.add_completed()` returns idempotently when it finds
  only the assessment ID after confirming the succeeded work's finalizer.
  Altered score, outcome, metadata, hash, path, or timestamp values are not
  compared with the immutable assessment row.
- `CvGenerationRepository.add_completed()` has the same early-return pattern.
  It returns before the required and optional hash-binding checks added in Fix
  3, so a changed replay is acknowledged as successful.
- The correct comparison representation already exists at each repository's
  insert boundary: JSON fields, booleans, enums, decimals, and token usage are
  converted to SQLite values immediately before insertion.
- Assessment preflight tests cover missing work and stale tokens but not wrong
  applications or work types. Neither service currently has an explicit test
  for ownership loss after preflight and before final persistence.
- The correction requires no schema, dependency, route, UI, dispatcher,
  migration, or public service-signature change.

## Development Approach

- **Testing approach:** TDD. Write the exact-replay regressions before changing
  each repository and record the expected failure.
- Complete each numbered task fully and run its focused tests before starting
  the next task.
- Keep replay validation repository-local because assessment and CV rows have
  different persisted fields, normalization rules, and domain errors.
- Derive inserts and replay comparisons from the same canonical persisted
  payload representation so the two paths cannot drift.
- Preserve the existing ownership order: wrong finalizers remain
  `StaleWorkerError`; only the original finalizer is eligible for exact replay
  comparison.
- Every modified production method must have exact-success, altered-payload,
  stale-owner, and atomic no-mutation coverage.
- Update this plan immediately if payload representation, file scope, or
  transaction boundaries change during implementation.
- Do not proceed to the next task until the current task's focused tests and
  the full suite pass.

## Testing Strategy

- **Assessment repository tests:** exact same-finalizer replay, parameterized
  mutations across persisted score/outcome, model/schema/instruction/taxonomy,
  source hashes, provider/usage, artefact, and completion metadata, plus wrong
  finalizer behavior.
- **CV repository tests:** exact same-finalizer replay and parameterized
  mutations across persisted model/schema/instruction, required and optional
  hashes, provider/usage, artefact, and completion metadata.
- **Atomicity assertions:** rejected replays leave the existing immutable row,
  succeeded work row, finalizer token, lifecycle, other records, and artefacts
  byte-for-byte unchanged.
- **Assessment preflight tests:** wrong application and wrong work type fail
  before profile, provider, or artefact access.
- **Post-preflight race tests:** reclaim assessment and CV work during the
  provider call, then prove final persistence rejects worker A, worker B keeps
  ownership, domain/lifecycle rows do not change, and newly written artefacts
  are removed.
- **No browser E2E tests:** the change affects only repository and service
  ownership contracts; existing route tests remain part of the full suite.
- **Final gates:** `uv run pytest`, `uv run ruff check .`,
  `uv run ruff format --check .`, `uv run mypy app tests`,
  `uv run python scripts/generate_ai_schemas.py --check`, `uv lock --check`,
  and `git diff --check`.

## Progress Tracking

- Mark completed items with `[x]` immediately when done.
- Add newly discovered work with a `➕` prefix.
- Add blockers with a `⚠️` prefix and identify the affected review finding.
- Record the failing TDD command before each production change and the focused
  and full-suite passing counts after each task.
- Keep this plan synchronized with implementation scope and behavior.
- Do not archive this plan until every automated gate passes.

## Solution Overview

Build one repository-local canonical persisted payload for each completed
domain object. Use that representation both for the initial `INSERT` and for
terminal replay comparison. After confirming that the work row succeeded under
the supplied finalizer token, fetch the full immutable row by its ID and compare
every persisted field. Return only for an exact match. Raise the repository's
stable state error when the row is absent or any persisted value differs.

Keep wrong-finalizer handling unchanged: a token that did not finalize the work
must receive `StaleWorkerError` without learning whether a supplied domain
payload matches. Perform replay checks under the existing `BEGIN IMMEDIATE`
transaction so terminal work and immutable records are observed consistently.

Add the missing ownership tests without widening production scope. The final
transaction-qualified work checks already provide the intended post-preflight
race boundary; the new regressions will make that requirement explicit and
protect it from future refactoring.

## Technical Details

### Canonical persisted payloads

- Represent each immutable row as an ordered tuple or field-to-value mapping
  matching its SQLite columns exactly.
- Normalize enums to `.value`, score decimals to stored floats, booleans to
  integers, and tuple metadata to the same deterministic JSON strings used by
  insertion.
- Include identifiers, application/assessment relationships, all model and
  provenance metadata, all hashes, token counts, paths, and the original stored
  completion timestamp.
- Exclude `work_id` and `worker_token` from immutable-row equality because they
  are verified independently against `work_items` and are not persisted in the
  domain tables.
- Reuse the canonical representation in both insertion and comparison rather
  than maintaining duplicate field lists with different conversions.

### Terminal replay decision

- If the work is `succeeded` and `finalizer_token` differs from the supplied
  token, raise `StaleWorkerError` as today.
- If the work is `succeeded` and the finalizer matches, fetch the complete
  immutable row for the supplied domain ID.
- Return only when the immutable row exactly equals the canonical completion
  payload.
- Raise `AssessmentStateError` or `CvGenerationStateError` when the immutable
  row is absent or differs. Use a stable message that does not include payload
  values, paths, hashes, or private content.
- Do not rerun lifecycle transitions, enqueue generation work, insert another
  domain row, or update terminal work during an accepted replay.

### Ownership regression coverage

- Create a second application or use its assessment work to prove an
  assessment work ID cannot be paired with the wrong application or work type.
- Delete or guard the profile input in preflight tests so any private read
  would fail the test before the expected domain error.
- Use the fake generator's existing side-effect hook to expire worker A's
  lease, recover the row, and claim it for worker B after preflight.
- Verify assessment final persistence and CV final persistence both reject
  worker A under the existing immediate transaction.
- For CV, assert that content and DOCX files created before final persistence
  are cleaned up when the ownership check fails.

## What Goes Where

- `app/assessments.py`: canonical assessment persistence representation and
  exact terminal replay comparison.
- `app/cv_generations.py`: canonical CV-generation persistence representation
  and exact terminal replay comparison before any idempotent return.
- `tests/test_assessment_service.py`: assessment replay mutations, wrong
  application/type preflight, and post-preflight lease-loss regression.
- `tests/test_cv_generator.py`: CV replay mutations and post-preflight
  lease-loss/artefact-cleanup regression.
- `docs/plans/2026-08-06-deliver-ai-assessment-and-one-page-cv-v1.md`: record
  Fix 4 results without marking Task 9 or later tasks complete.

## Implementation Steps

### Task 1: Require exact assessment completion replays

**Files:**

- Modify: `app/assessments.py`
- Modify: `tests/test_assessment_service.py`

- [x] Add a direct repository helper that creates and persists one completed
  assessment with explicit claimed-work credentials for replay testing.
- [x] Add a success test proving the original finalizer can repeat the exact
  completed assessment without duplicating the assessment, lifecycle, or
  automatic CV-generation work.
- [x] Add parameterized failing regressions that change each category of
  persisted assessment payload while retaining the assessment ID, work ID,
  and original finalizer token.
- [x] Run the changed-replay tests and confirm their expected state-error
  assertions fail because the repository currently returns successfully.
- [x] Add wrong-finalizer and missing-immutable-row assertions proving those
  paths remain rejected without changing the terminal work row.
- [x] Implement a repository-local canonical assessment payload representation
  shared by insertion and replay comparison.
- [x] Replace the ID-only idempotent return with exact full-row comparison and
  stable `AssessmentStateError` behavior for absent or changed payloads.
- [x] Assert rejected replays leave the immutable assessment, lifecycle,
  terminal assessment work, queued generation work, and artefacts unchanged.
- [x] Run `uv run pytest tests/test_assessment_service.py` and then
  `uv run pytest`; both must pass before Task 2.

### Task 2: Require exact CV-generation completion replays

**Files:**

- Modify: `app/cv_generations.py`
- Modify: `tests/test_cv_generator.py`

- [x] Extend the completed-generation test helper so exact and mutated replays
  use hashes derived from the claimed work and referenced assessment.
- [x] Preserve the existing success test proving an exact same-finalizer replay
  is harmless and a wrong-finalizer replay raises `StaleWorkerError`.
- [x] Add parameterized failing regressions for altered required hashes,
  optional hashes, model/schema/instruction metadata, provider/usage metadata,
  paths/content hashes, repair status, and completion timestamp after success.
- [x] Run the altered-replay tests and confirm their expected state-error
  assertions fail because the repository returns before Fix 3 binding checks.
- [x] Add missing-immutable-row coverage for succeeded work and confirm it
  cannot be acknowledged as idempotent.
- [x] Implement a repository-local canonical CV persistence representation
  shared by insertion and terminal replay comparison.
- [x] Return idempotently only when every persisted CV-generation field is an
  exact match; otherwise raise stable `CvGenerationStateError`.
- [x] Assert rejected replays preserve the immutable generation, terminal work
  ownership/history, lifecycle, assessment, and existing artefacts unchanged.
- [x] Run `uv run pytest tests/test_cv_generator.py` and then `uv run pytest`;
  both must pass before Task 3.

### Task 3: Complete preflight and ownership-race regression coverage

**Files:**

- Modify: `tests/test_assessment_service.py`
- Modify: `tests/test_cv_generator.py`

- [x] Extend assessment preflight parameterization with wrong-application and
  wrong-work-type credentials and assert no profile, provider, database, or
  artefact mutation occurs.
- [x] Add an assessment service race test that transfers ownership to worker B
  during generation and expects worker A's final persistence to raise
  `StaleWorkerError`.
- [x] Assert the assessment race leaves worker B running, creates no immutable
  assessment or generation work, preserves the lifecycle, and removes worker
  A's new artefacts.
- [x] Add the equivalent CV service race test using the fake generator effect
  between preflight and content/DOCX persistence.
- [x] Assert the CV race leaves worker B running, creates no immutable
  generation, preserves assessment/lifecycle data, and removes worker A's
  content and DOCX files.
- [x] Run `uv run pytest tests/test_assessment_service.py
  tests/test_cv_generator.py` and then `uv run pytest`; both must pass before
  Task 4.

### Task 4: Verify Task 8 Fix 4 acceptance criteria

**Files:**

- Modify:
  `docs/plans/2026-08-10-task-8-fix-4-enforce-exact-completion-replays.md`

- [x] Verify only exact same-finalizer assessment payloads receive an
  idempotent return.
- [x] Verify only exact same-finalizer CV-generation payloads receive an
  idempotent return and no terminal path bypasses Fix 3 hash binding.
- [x] Verify altered replays and wrong finalizers leave immutable records,
  work history, lifecycle, queued follow-up work, and artefacts unchanged.
- [x] Verify wrong assessment application/type ownership is rejected before
  private reads and both services retain their final ownership race boundary.
- [x] Run `uv run pytest` and record the complete passing count.
- [x] Run `uv run ruff check .` and `uv run ruff format --check .`.
- [x] Run `uv run mypy app tests`,
  `uv run python scripts/generate_ai_schemas.py --check`, and
  `uv lock --check`.
- [x] Run `git diff --check` and review the final diff against both Fix 4
  findings and all recorded test gaps.

### Task 5: Finalize documentation and archive Fix 4

**Files:**

- Modify: `docs/plans/2026-08-06-deliver-ai-assessment-and-one-page-cv-v1.md`
- Modify: `README.md` only if operator-visible behavior changes
- Modify: `AGENTS.md` only if a reusable project-wide rule is discovered
- Move:
  `docs/plans/2026-08-10-task-8-fix-4-enforce-exact-completion-replays.md`
  to `docs/plans/completed/`

- [x] Record Task 8 Fix 4 behavior, issue link, TDD evidence, and final gate
  counts in the parent plan without marking Task 9 or later tasks complete.
- [x] Update README only if behavior is operator-visible; otherwise record that
  no README change is required.
- [x] Update AGENTS only for a reusable project-wide convention; otherwise
  record that no AGENTS change is required.
- [x] Confirm every checklist item is complete and rerun `uv run pytest` after
  final documentation changes.
- [x] Move this plan to `docs/plans/completed/` only after every automated gate
  passes.

## Post-Completion

No manual UI, live-provider, deployment, migration, or production-data
verification is required. Task 9 must preserve exact terminal replay semantics
when it adds dispatcher retries and must continue supplying the exact claimed
work ID and worker token to both service APIs.

## Completion Evidence

- Implemented repository-local canonical SQLite payloads for assessments and
  CV generations and reused them for insertion and terminal replay comparison.
- Exact same-finalizer replays return without changing immutable rows, work,
  lifecycle, or follow-up work. Altered payloads, missing rows, and wrong
  finalizers are rejected with stable state or stale-worker errors.
- Added assessment wrong-application/work-type preflight coverage and
  assessment/CV post-preflight lease-reclaim tests with artefact cleanup.
- Focused suite: 123 passed. Full suite: 350 passed.
- Final gates passed: Ruff check, Ruff format check, mypy, AI schema drift,
  lockfile, and `git diff --check`.
- No README or AGENTS change was required; the behavior is an internal worker
  and repository integrity contract.
