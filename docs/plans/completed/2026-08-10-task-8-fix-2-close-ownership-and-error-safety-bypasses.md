# Task 8 Fix 2: Close Ownership and Error-Safety Bypasses

## Overview

- Correct the remaining Task 8 ownership and privacy defects found after Fix 1.
- Require the current work ID and worker token at every assessment and CV
  success-persistence boundary; callers must never obtain ownership implicitly
  or bypass verification by omitting credentials.
- Ensure every matched assessment atomically queues CV-generation work,
  including the ordinary service path used by current callers.
- Accept idempotent completion only from the original finalizer and persist
  provider metadata only from the trusted sanitized provider exception type.
- Use test-driven development: add each focused regression first, confirm it
  fails for the reviewed defect, then make the smallest implementation change
  that passes it.

## Tracking

- GitHub issue: [#37](https://github.com/nsal/JobHunter/issues/37)

## Context (from discovery)

- JobHunter is a Python 3.14 FastAPI application using direct `sqlite3`
  repositories, immutable assessment/CV records, and Ruff, mypy, and pytest.
- `app/assessments.py` and `app/cv_generations.py` only verify ownership when
  optional work credentials are present, so direct persistence can omit both
  values and bypass the token boundary.
- `app/assessment/service.py` claims work for callers that omit credentials but
  disables matched generation enqueue through `enqueue_generation=False`,
  leaving successful matched applications without active follow-up work.
- `app/cv/generator.py` and the assessment service invent a token when they find
  implicit active work; if that work is already running, they execute provider
  and artefact work without ever claiming it.
- `app/work/repository.py` retains `finalizer_token` but accepts any token after
  success, and trusts `code`, `retryable`, and `safe_message` attributes on any
  exception passed to `fail()`.
- Existing tests pass but explicitly accept a wrong-token completion and do not
  cover missing credentials, stale CV persistence, forged safe metadata, or an
  already-running implicit execution path.
- The project uses a fresh-schema policy. No dependency, migration, route, UI,
  or dispatcher implementation is required for this correction.

## Development Approach

- **Testing approach:** TDD. For every task, write the focused regression tests
  first and run them to confirm the expected failure before editing production
  code.
- Complete one task fully, run its focused tests and the full suite, and mark
  its checklist before moving to the next task.
- Make ownership enforcement explicit at public service and repository
  boundaries; do not retain an unsafe compatibility mode.
- The required work credentials are an intentional API hardening change.
  Update all in-repository callers and tests rather than preserving the
  credential-omitting behavior.
- Every modified path requires success, stale/error, and idempotency coverage
  where applicable.
- Keep transaction boundaries small and avoid dispatcher/process-supervision
  behavior assigned to Task 9.
- Keep this plan synchronized if signatures, file scope, or transaction
  boundaries change during implementation.

## Testing Strategy

- **Work repository tests:** same-owner and wrong-owner completion idempotency,
  trusted provider failure classification, forged-metadata redaction, process
  timeout retry, and unchanged retry exhaustion behavior.
- **Assessment tests:** credentials required before provider execution, exact
  token-bound persistence, stale matched/mismatched rollback, unconditional
  matched generation enqueue, enqueue-conflict rollback, and finalizer-owner
  idempotency.
- **CV tests:** explicit queued/claimed work setup, credentials required before
  generation, stale-worker rollback and artefact cleanup, same-owner
  idempotency, wrong-owner rejection, and matched/mismatch override success.
- **Call-site regression tests:** update service and repository test helpers to
  enqueue and claim work explicitly; no tests may persist successful domain
  records through a credential-free shortcut.
- **No new browser E2E tests:** no route or rendered UI behavior changes.
  Existing route tests remain part of every full-suite gate.
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

Make ownership mandatory instead of inferential. Assessment and CV services
will require keyword-only `work_id` and `worker_token` values supplied by the
worker orchestration layer. Their immutable completion values will carry
non-optional credentials, and repositories will always verify the exact
running row under the same immediate transaction as domain persistence and
work finalization. Remove implicit enqueue/claim behavior from execution
services; initial assessment enqueue, automatic matched generation enqueue,
and explicit mismatch override enqueue remain the only work-creation paths.

Matched assessment persistence will always insert its CV-generation work item
after owner-qualified assessment finalization in the same transaction. There
will be no `enqueue_generation` escape hatch. Tests that need repeated
assessments or generations will explicitly complete the active workflow before
creating the next item, preserving the one-active-work invariant.

Use `finalizer_token` for terminal idempotency: a repeated completion from the
same token is harmless, while any other token raises `StaleWorkerError`.
Failure classification will recognize only built-in `TimeoutError` and the
project's sanitized `StructuredGenerationError`; every other exception uses
the stable generic code/message even if it exposes similarly named attributes.

## Technical Details

### Mandatory ownership contract

- Make `work_id` and `worker_token` required keyword-only parameters on
  `AssessmentService.execute()`/`assess()` and
  `CvGenerationService.execute()`.
- Make the corresponding fields non-optional on `CompletedAssessment` and
  `CompletedCvGeneration`, or pass them as required repository parameters if
  that produces a clearer boundary during implementation.
- Remove service-side implicit active-work lookup, enqueue, claim, and random
  token generation.
- Always select and verify work ID, application ID, work type, `running` state,
  current token, and CV assessment ID before immutable persistence.
- Reject blank credentials at the repository boundary as well as stale or
  mismatched credentials.

### Assessment-to-generation handoff

- Remove `enqueue_generation` from completed assessment state.
- For every matched owned assessment, insert exactly one queued generation
  item in the same transaction as assessment persistence and assessment-work
  finalization.
- Keep mismatch lifecycle transition behavior atomic and do not enqueue
  generation without an explicit override.
- Preserve rollback of assessment, lifecycle, finalization, and generation
  writes when the one-active-work constraint fails.

### Finalization and failure safety

- On a succeeded work row, compare the supplied token with `finalizer_token`.
  Return only for the original finalizer; raise `StaleWorkerError` otherwise.
- Preserve token-qualified updates for running completion, heartbeat, and
  checkpoint operations.
- Preserve safe provider codes/messages only for
  `StructuredGenerationError`; retain built-in `TimeoutError` as the generic
  retryable process-timeout case.
- Ignore `code`, `retryable`, and `safe_message` attributes on every other
  exception and persist only `unknown_error` / `Work failed.`.
- Keep the two-claim retry and terminal stale-recovery policy unchanged.

## What Goes Where

- `app/work/repository.py`: trusted failure classification and
  finalizer-token-qualified terminal idempotency.
- `app/assessment/service.py`: mandatory worker credentials and removal of
  implicit work creation/claiming.
- `app/assessments.py`: mandatory ownership verification and unconditional
  atomic generation enqueue for matched results.
- `app/cv/generator.py`: mandatory worker credentials and removal of implicit
  enqueue/claim behavior.
- `app/cv_generations.py`: mandatory token-bound CV persistence and
  owner-qualified idempotency.
- `tests/test_work_repository.py`: failure privacy and finalizer ownership
  regressions.
- `tests/test_assessment_service.py`: mandatory ownership and automatic enqueue
  regressions.
- `tests/test_cv_generator.py`: explicit work setup, stale CV ownership,
  idempotency, and cleanup regressions.
- `tests/test_repository.py`: update initial-work assertions or shared work
  setup only if signature changes require it.
- `docs/plans/2026-08-06-deliver-ai-assessment-and-one-page-cv-v1.md`: record
  Fix 2 results without marking Task 9 or later work complete.

## Implementation Steps

### Task 1: Trust only typed failures and enforce finalizer ownership

**Files:**
- Modify: `app/work/repository.py`
- Modify: `tests/test_work_repository.py`

- [x] Replace the existing wrong-token success assertion with a failing test
  proving only the retained `finalizer_token` receives an idempotent return.
- [x] Write a failing test proving a different token receives
  `StaleWorkerError` after successful completion without changing the terminal
  row.
- [x] Write failing tests where an arbitrary exception forges transient
  `code`, `retryable`, and private `safe_message` attributes; assert generic
  non-retryable persisted metadata and no original text.
- [x] Write success tests proving trusted `StructuredGenerationError` codes and
  bounded safe messages are retained and built-in `TimeoutError` still gets
  one retry.
- [x] Run `uv run pytest tests/test_work_repository.py` and confirm the new
  regressions fail for wrong-token acceptance and forged metadata trust.
- [x] Restrict failure classification to trusted typed provider failures plus
  the built-in process-timeout case.
- [x] Compare `finalizer_token` before the succeeded-state idempotent return and
  reject all other tokens.
- [x] Run `uv run pytest tests/test_work_repository.py` and `uv run pytest`;
  both must pass before Task 2.

### Task 2: Require assessment ownership and always queue matched generation

**Files:**
- Modify: `app/assessment/service.py`
- Modify: `app/assessments.py`
- Modify: `tests/test_assessment_service.py`
- Modify: `tests/test_repository.py` (only if shared setup changes)

- [x] Write failing service tests proving missing or blank work credentials are
  rejected before profile reads or provider execution.
- [x] Write failing repository tests proving omitted, partial, stale, wrong
  application, and wrong work-type credentials cannot persist matched or
  mismatched assessments or alter lifecycle/work rows.
- [x] Write a failing regression proving the ordinary matched service path
  leaves one succeeded assessment item and one queued generation item.
- [x] Write success and error tests for same-owner repeated persistence,
  wrong-owner repetition, and automatic-generation active-work rollback.
- [x] Run focused assessment/repository tests and confirm failures correspond
  to optional ownership and suppressed generation enqueue.
- [x] Make service and completed-assessment ownership values mandatory and
  remove implicit work lookup/enqueue/claim behavior.
- [x] Remove `enqueue_generation` and always perform the matched generation
  insertion inside the owned completion transaction.
- [x] Update every assessment test helper/call site to claim exact work and pass
  its ID/token explicitly.
- [x] Run `uv run pytest tests/test_assessment_service.py
  tests/test_repository.py tests/test_work_repository.py` and
  `uv run pytest`; all must pass before Task 3.

### Task 3: Require CV ownership and reject unclaimed execution

**Files:**
- Modify: `app/cv/generator.py`
- Modify: `app/cv_generations.py`
- Modify: `tests/test_cv_generator.py`
- Modify: `tests/test_work_repository.py` (only for shared ownership helpers)

- [x] Write failing tests proving missing or blank CV work credentials are
  rejected before provider execution and before content/DOCX writes.
- [x] Write failing tests where worker A loses a CV lease, worker B reclaims it,
  and worker A attempts repository persistence and full service execution.
- [x] Write a failing test proving a credential-free invocation cannot discover
  running work, invent a token, or issue a duplicate provider request.
- [x] Write success tests for explicitly queued and claimed matched generation
  and mismatch override generation, including exact assessment/hash binding.
- [x] Write same-owner idempotency, wrong-owner rejection, database-finalization
  cleanup, and unchanged-lifecycle tests for both generation paths.
- [x] Run focused CV/work tests and confirm failures correspond to optional
  ownership and implicit running-work execution.
- [x] Make service and completed-generation ownership values mandatory and
  remove implicit active-work lookup, enqueue, claim, and random token logic.
- [x] Keep mismatch override enqueue as a separate explicit transaction; claim
  its returned work ID before calling the generation service.
- [x] Update every CV test helper/direct repository call to create, claim, and
  pass the exact work item instead of using credential-free persistence.
- [x] Run `uv run pytest tests/test_cv_generator.py
  tests/test_work_repository.py` and `uv run pytest`; all must pass before
  Task 4.

### Task 4: Verify Task 8 Fix 2 acceptance criteria

**Files:**
- Modify: `docs/plans/2026-08-10-task-8-fix-2-close-ownership-and-error-safety-bypasses.md`

- [x] Verify no assessment or CV success-persistence API accepts missing,
  partial, blank, stale, wrong-application, wrong-type, or wrong-assessment
  ownership credentials.
- [x] Verify matched assessment completion always queues generation atomically
  and mismatch completion never does so without an explicit override.
- [x] Verify no execution service looks up, creates, or claims work implicitly,
  and no missing-token path reaches provider or artefact operations.
- [x] Verify only the original finalizer receives idempotent completion and all
  other tokens are rejected.
- [x] Verify arbitrary exception attributes cannot enter persisted errors while
  trusted provider failures and process timeouts preserve intended retry
  behavior.
- [x] Run `uv run pytest` and record the complete passing count: 280 passed.
- [x] Run `uv run ruff check .` and `uv run ruff format --check .`.
- [x] Run `uv run mypy app tests`,
  `uv run python scripts/generate_ai_schemas.py --check`, and
  `uv lock --check`.
- [x] Run `git diff --check` and review the final diff against all five Fix 2
  findings.

### Task 5: Finalize documentation and archive Fix 2

**Files:**
- Modify: `docs/plans/2026-08-06-deliver-ai-assessment-and-one-page-cv-v1.md`
- Modify: `README.md` (only if operator-visible behavior changes)
- Modify: `AGENTS.md` (only if a reusable project-wide rule is discovered)
- Move: `docs/plans/2026-08-10-task-8-fix-2-close-ownership-and-error-safety-bypasses.md`
  to `docs/plans/completed/`

- [x] Record Task 8 Fix 2 behavior and final test/gate counts in the parent
  implementation plan without marking Task 9 or later tasks complete.
- [x] Update README only if mandatory ownership changes operator-facing usage;
  otherwise record that no README change is required.
- [x] Update AGENTS only for a reusable project-wide convention; otherwise
  record that no AGENTS change is required.
- [x] Confirm every checklist item is complete and all automated gates remain
  passing after documentation changes.
- [x] Move this plan to `docs/plans/completed/`.

## Post-Completion

No manual UI, deployment, external-provider, or production-data verification
is required. Task 9 must consume the mandatory work ID/token service APIs and
must not reintroduce implicit ownership acquisition inside assessment or CV
execution.
