# Task 8 Fix 3: Enforce Preflight Ownership and Generation Integrity

## Overview

- Correct the three remaining Task 8 defects found after Fix 2.
- Reject missing, wrong, or stale assessment and CV work ownership before any
  private input is read or any provider request is issued, while retaining the
  final transactional ownership check needed to close races.
- Prevent a completed mismatch assessment from queuing another generation that
  the immutable CV-generation constraint can never persist.
- Bind immutable CV-generation hashes to the claimed work item and referenced
  assessment so unrelated output metadata cannot be finalized.
- Use test-driven development: add each focused regression first, confirm it
  fails for the reviewed defect, and then make the smallest implementation
  change that passes it.

## Tracking

- GitHub issue: [#38](https://github.com/nsal/JobHunter/issues/38)

## Context (from discovery)

- JobHunter is a Python 3.14 FastAPI application with direct `sqlite3`
  repositories, private AI inputs, immutable assessment/CV records, and a
  durable work state machine.
- `app/assessment/service.py` and `app/cv/generator.py` reject blank
  credentials but do not verify current ownership until successful output is
  persisted. A stale worker therefore still reads private inputs and makes a
  paid provider request before receiving `StaleWorkerError`.
- `app/assessments.py` and `app/cv_generations.py` already perform the required
  final token-qualified verification under `BEGIN IMMEDIATE`; this check must
  remain after adding a read-only preflight check.
- `CvGenerationRepository.enqueue_override()` validates mismatch eligibility
  and active-work uniqueness but does not reject an assessment that already
  has an immutable `cv_generations` row.
- `CvGenerationRepository.add_completed()` verifies work identity and token but
  persists caller-supplied hashes without comparing them to the work item or
  assessment. Existing repository test helpers currently rely on that bypass.
- The correction requires no schema, dependency, route, UI, dispatcher, or
  migration change. The project continues to use its fresh-schema policy.

## Development Approach

- **Testing approach:** TDD. Write the focused failing regression before each
  production change and record the expected failure.
- Complete each numbered task fully and run its focused tests before starting
  the next task.
- Keep the preflight check small and read-only. It reduces avoidable private
  data/provider work but cannot replace the locked persistence check because
  ownership can change during generation.
- Reuse repository-local ownership helpers where that keeps preflight and final
  validation consistent without adding a new service dependency.
- Make invalid override and hash-binding failures stable domain errors and
  verify that every rejected transaction leaves work, lifecycle, and immutable
  records unchanged.
- Add or update tests for every modified method, covering success, stale/error,
  rollback, and idempotency behavior where applicable.
- Keep this plan synchronized if signatures, file scope, or transaction
  boundaries change during implementation.

## Testing Strategy

- **Assessment service tests:** valid owner preflight, wrong ID/application/type
  and stale-token rejection before profile reads or provider calls, plus final
  persistence rejection when ownership changes after preflight.
- **CV service tests:** valid owner preflight, wrong ID/application/assessment/
  type and stale-token rejection before private input, provider, content, or
  DOCX work, plus the existing post-generation race boundary.
- **Override repository tests:** reject a second override after a generation is
  already complete, preserve the completed generation and mismatch result, and
  leave no queued/running work behind.
- **CV persistence tests:** accept hashes that match the work and assessment;
  reject mismatched profile, JD, assessment-result, and populated optional work
  hashes atomically; preserve same-owner idempotency for the exact completion.
- **Test helpers:** derive completion hashes from the real assessment/work rows
  instead of using unrelated fixed 64-character values.
- **No browser E2E tests:** this change has no route or rendered UI behavior.
  Existing route tests remain part of the full-suite gate.
- **Final gates:** `uv run pytest`, `uv run ruff check .`,
  `uv run ruff format --check .`, `uv run mypy app tests`,
  `uv run python scripts/generate_ai_schemas.py --check`, `uv lock --check`,
  and `git diff --check`.

## Progress Tracking

- Mark completed items with `[x]` immediately when done.
- Add newly discovered work with a `➕` prefix.
- Add blockers with a `⚠️` prefix and identify the affected review finding.
- Record focused and full-suite counts after each task.
- Do not archive this plan until every automated gate passes.

## Solution Overview

Add explicit ownership preflight methods to the assessment and CV repositories.
Each service will call its repository preflight before loading application,
assessment, profile, template, or layout data. The preflight will require the
exact work ID, application ID, work type, running state, worker token, and, for
CV work, assessment ID. The existing `BEGIN IMMEDIATE` validation in
`add_completed()` remains authoritative if a lease changes after preflight.

Extend mismatch override eligibility inside its existing immediate transaction
to reject any assessment with a completed generation. Extend CV completion
validation in its existing immediate transaction to compare required
profile/JD hashes with both the work row and assessment, compare the
assessment-result hash with the assessment, and compare populated optional
prompt/schema/template/layout work hashes with their completion equivalents.
No domain row or work finalization may survive a mismatch.

## Technical Details

### Ownership preflight

- Add narrow repository methods or shared repository-local helpers that verify
  claimed ownership without changing state.
- Assessment preflight must match work ID, application ID, `assessment` type,
  `running` state, and worker token.
- CV preflight must additionally match `cv_generation` type and assessment ID.
- Use the same error categories as final persistence: missing or mismatched work
  identity is a domain state error; a non-current token is
  `StaleWorkerError`.
- Call preflight before application/JD/profile/template/layout reads and before
  provider or artefact operations.
- Keep the final transaction-qualified `UPDATE` and token check unchanged in
  purpose so a worker that loses ownership during execution cannot persist.

### Completed override rejection

- Under the existing `BEGIN IMMEDIATE`, check `cv_generations` for the exact
  application/assessment before inserting override work.
- Raise a stable `CvGenerationStateError` when an immutable generation already
  exists.
- Do not alter the mismatch assessment, lifecycle, completed generation, or
  terminal work history, and do not create an active work item.

### CV hash binding

- Require the completion profile and JD hashes to equal the owned work row and
  the referenced assessment row.
- Require the completion assessment-result hash to equal the referenced
  assessment result hash.
- When prompt/schema/template/layout hashes are populated on the work row,
  require them to match the corresponding instruction/schema/template/layout
  completion hashes. Treat null optional work hashes as not yet bound.
- Perform all comparisons before inserting `cv_generations` or finalizing work.
- On mismatch, raise a stable state/input error and roll back every database
  change; the running work remains owned so the runner can classify the failure.
- Preserve exact-payload, same-finalizer idempotency and wrong-finalizer
  rejection.

## What Goes Where

- `app/assessments.py`: assessment ownership preflight and shared exact-owner
  query logic.
- `app/assessment/service.py`: invoke assessment preflight before private input
  or provider work.
- `app/cv_generations.py`: CV ownership preflight, completed-override rejection,
  and work/assessment hash binding during completion.
- `app/cv/generator.py`: invoke CV preflight before private input, provider, or
  artefact work.
- `tests/test_assessment_service.py`: preflight success, stale/wrong ownership,
  zero-provider-call, and post-preflight race regressions.
- `tests/test_cv_generator.py`: CV preflight, duplicate completed override,
  exact hash binding, rollback, idempotency, and corrected helper coverage.
- `docs/plans/2026-08-06-deliver-ai-assessment-and-one-page-cv-v1.md`: record
  Fix 3 results without marking Task 9 or later tasks complete.

## Implementation Steps

### Task 1: Reject unowned assessment execution before private work

**Files:**
- Modify: `app/assessments.py`
- Modify: `app/assessment/service.py`
- Modify: `tests/test_assessment_service.py`

- [x] Add failing parameterized tests for wrong work ID, application, work type,
  and worker token before profile reads and provider execution.
- [x] Strengthen the stale-lease regression to assert zero provider requests,
  zero artefact writes, and no assessment/lifecycle/work mutation by worker A.
- [x] Add a success test proving an exactly claimed assessment reaches the
  provider and final transactional persistence.
- [x] Add or preserve a race test where ownership changes after preflight and
  final persistence still rolls back with `StaleWorkerError`.
- [x] Run `uv run pytest tests/test_assessment_service.py` and confirm the new
  preflight regressions fail for the reviewed behavior.
- [x] Add repository-local exact assessment ownership preflight and invoke it
  before application, JD, profile, provider, or artefact access.
- [x] Run `uv run pytest tests/test_assessment_service.py` and `uv run pytest`;
  both must pass before Task 2.

### Task 2: Reject unowned CV execution before private work

**Files:**
- Modify: `app/cv_generations.py`
- Modify: `app/cv/generator.py`
- Modify: `tests/test_cv_generator.py`

- [x] Add failing parameterized tests for wrong work ID, application,
  assessment, work type, and worker token before generation input/private-file
  reads.
- [x] Add a stale-lease regression where worker B reclaims the item and worker
  A produces zero provider requests, content writes, and DOCX writes.
- [x] Add a success test proving exact CV work ownership permits the ordinary
  matched and explicit mismatch-override paths.
- [x] Add or preserve a race test where ownership changes after preflight and
  final persistence rejects worker A and cleans its newly written artefacts.
- [x] Run `uv run pytest tests/test_cv_generator.py` and confirm the new
  preflight regressions fail for the reviewed behavior.
- [x] Add repository-local exact CV ownership preflight and invoke it before
  application, assessment, profile, template, layout, provider, or artefact
  access.
- [x] Run `uv run pytest tests/test_cv_generator.py` and `uv run pytest`; both
  must pass before Task 3.

### Task 3: Reject duplicate completed mismatch overrides

**Files:**
- Modify: `app/cv_generations.py`
- Modify: `tests/test_cv_generator.py`

- [x] Add a failing regression that completes mismatch override generation and
  then attempts to enqueue another override for the same assessment.
- [x] Assert rejection leaves the original immutable generation, mismatch
  assessment, lifecycle, and terminal work row unchanged with no active work.
- [x] Add a success test proving an eligible mismatch with no completed
  generation still queues exactly one correctly bound work item.
- [x] Run `uv run pytest tests/test_cv_generator.py` and confirm the completed
  duplicate currently queues.
- [x] Extend the locked override eligibility check to reject an existing
  `cv_generations` row with stable `CvGenerationStateError` behavior.
- [x] Run `uv run pytest tests/test_cv_generator.py` and `uv run pytest`; both
  must pass before Task 4.

### Task 4: Enforce exact CV work and assessment hash binding

**Files:**
- Modify: `app/cv_generations.py`
- Modify: `tests/test_cv_generator.py`

- [x] Replace fixed unrelated completion hashes in CV repository helpers with
  hashes derived from the actual assessment and claimed work item.
- [x] Add a success test proving exact profile, JD, and assessment-result hashes
  persist and finalize the owned work item.
- [x] Add failing parameterized tests for profile, JD, and assessment-result
  mismatches and for each populated optional prompt/schema/template/layout work
  hash mismatch.
- [x] Assert every mismatch rolls back the immutable generation and work
  finalization, keeps lifecycle unchanged, and does not disturb existing
  artefacts or generations.
- [x] Add same-owner exact-completion idempotency and wrong-owner rejection
  coverage after successful hash-bound persistence.
- [x] Run `uv run pytest tests/test_cv_generator.py` and confirm the mismatched
  hash regressions currently persist.
- [x] Compare completion hashes with the work and assessment rows before insert
  or finalization, using one stable domain error for binding failures.
- [x] Run `uv run pytest tests/test_cv_generator.py
  tests/test_work_repository.py` and `uv run pytest`; all must pass before
  Task 5.

### Task 5: Verify Task 8 Fix 3 acceptance criteria

**Files:**
- Modify:
  `docs/plans/2026-08-10-task-8-fix-3-preflight-ownership-and-cv-integrity.md`

- [x] Verify stale, wrong-application, wrong-type, and wrong-assessment
  credentials cannot reach private reads, provider calls, or artefact writes.
- [x] Verify ownership remains checked again under the final persistence
  transaction and a post-preflight lease loss cannot persist.
- [x] Verify an assessment with an immutable CV generation cannot enqueue
  another mismatch override or leave active work behind.
- [x] Verify CV completion requires exact work/assessment hash binding and every
  binding failure rolls back domain and work writes.
- [x] Run `uv run pytest` and record the complete passing count.
- [x] Run `uv run ruff check .` and `uv run ruff format --check .`.
- [x] Run `uv run mypy app tests`,
  `uv run python scripts/generate_ai_schemas.py --check`, and
  `uv lock --check`.
- [x] Run `git diff --check` and review the final diff against all three Fix 3
  findings.

### Task 6: Finalize documentation and archive Fix 3

**Files:**
- Modify: `docs/plans/2026-08-06-deliver-ai-assessment-and-one-page-cv-v1.md`
- Modify: `README.md` (only if operator-visible behavior changes)
- Modify: `AGENTS.md` (only if a reusable project-wide rule is discovered)
- Move:
  `docs/plans/2026-08-10-task-8-fix-3-preflight-ownership-and-cv-integrity.md`
  to `docs/plans/completed/`

- [x] Record Task 8 Fix 3 behavior, issue link, and final test/gate counts in
  the parent plan without marking Task 9 or later tasks complete.
- [x] Update README only if behavior is operator-visible; otherwise record that
  no README change is required.
- [x] Update AGENTS only for a reusable project-wide convention; otherwise
  record that no AGENTS change is required.
- [x] Confirm every checklist item is complete and rerun `uv run pytest` after
  final documentation changes.
- [x] Move this plan to `docs/plans/completed/` only after every gate passes.

## Fix 3 Results

Issue [#38](https://github.com/nsal/JobHunter/issues/38) is resolved. Exact
assessment and CV work ownership is now checked before private input, provider,
or artefact access, while the locked final ownership checks remain
authoritative. Completed mismatch assessments cannot enqueue another override,
and CV completion hashes are bound to both the claimed work item and its
assessment.

Focused tests: 68 passed. Full suite: 295 passed. Final gates passed:
`ruff check`, `ruff format --check`, mypy, AI schema drift, `uv lock --check`,
and `git diff --check`. No README or AGENTS change is required because the
behavior is an internal worker API contract.

## Post-Completion

No manual UI, live-provider, deployment, migration, or production-data
verification is required. Task 9 must call the preflight-protected service APIs
with exact claimed work credentials and must preserve the final transactional
ownership checks.
