# Task 9 Fix 2: Preserve Worker Shutdown and Generation Provenance

## Overview

This bug-fix closes the three remaining Task 9 reliability defects found in
review. It gives the dispatcher enough supervised time to drain and finalize
workers, preserves the original provider provenance when a validated
checkpoint is resumed, and honors fractional work-lease configuration without
truncation.

The change also fills the missing service-level regression coverage for
checkpoint reuse, checkpoint invalidation, completion-clock ordering, and
full-budget launcher shutdown. It keeps the existing spawned-process,
SQLite-backed architecture and does not expand into UI or provider features.

## Context (from discovery)

- Associated issue: `https://github.com/nsal/JobHunter/issues/43`.
- The launcher in `app/cli.py` currently waits exactly the dispatcher's
  internal drain duration, although dispatcher polling, worker termination,
  joining, and durable failure recording can extend beyond that duration.
- `app/assessment/service.py` and `app/cv/generator.py` reuse validated output
  checkpoints but replace immutable provider metadata with `"checkpoint"`,
  empty response IDs, and zero token usage.
- `app/work/models.py`, `app/work/repository.py`, and `app/database.py` already
  provide the typed and durable checkpoint boundary where allowlisted
  provenance can be stored atomically with checkpoint hashes.
- `app/work/dispatcher.py` casts the configured floating-point work lease to
  `int` for the initial claim, while heartbeat renewals retain the exact value.
- Existing CLI, dispatcher, and runner tests cover basic lifecycle behavior,
  but assessment/CV checkpoint and completion-clock tests claimed by Fix 1 are
  absent from their service test files.

## Development Approach

- **Testing approach**: TDD. Add focused failing tests before each production
  change, verify that they fail for the intended reason, then implement the
  smallest correction.
- Complete each task fully before moving to the next task.
- Keep changes small and focused on the reviewed defects and missing
  regression coverage.
- Every task that changes code must add or update tests for its success and
  error or edge paths.
- All focused tests must pass before starting the next task.
- Update this plan immediately if implementation scope or interfaces change.
- Preserve existing synchronous service call sites, token-bound ownership,
  safe error redaction, and checkpoint content validation.
- Use `uv` for execution, Ruff for formatting and linting, and mypy for static
  verification.

## Testing Strategy

- **Repository tests**: verify typed checkpoint provenance serialization,
  migration behavior, exact replay, malformed-data rejection, and safe
  handling of legacy checkpoints without provenance.
- **Assessment service tests**: prove a valid checkpoint avoids the provider,
  preserves exact provider metadata, recomputes scoring, finalizes after the
  last heartbeat, and rejects every invalid checkpoint dimension.
- **CV service tests**: prove a valid content checkpoint avoids the provider,
  preserves exact provider metadata, reruns deterministic rendering, finalizes
  after the last heartbeat, and rejects every invalid checkpoint dimension.
- **Runner tests**: verify typed provenance reaches the service across the
  spawned-worker boundary without passing provider objects or private content.
- **Launcher/dispatcher tests**: model the complete drain budget, requested
  shutdown, sibling failure, forced timeout, and worker cleanup without real
  Uvicorn or provider calls.
- **Lease tests**: use a valid fractional work lease and assert exact claim and
  heartbeat expiry behavior.
- **No browser E2E tests**: this fix changes no route, template, or user
  interaction. The bounded real-spawn SQLite smoke test remains the relevant
  process-level acceptance test.

## Progress Tracking

- Mark completed items with `[x]` immediately when done.
- Add newly discovered tasks with a `➕` prefix.
- Record blockers with a `⚠️` prefix.
- Update this plan if implementation deviates from the selected design.
- Do not move to the next task while focused tests are failing.
- Keep recorded verification counts aligned with actual command output.

## Solution Overview

Use explicit durable contracts for both checkpoint provenance and process
shutdown:

1. Add a typed, allowlisted generation-provenance value containing only the
   model, provider, response IDs, token counts, and repair flag already allowed
   in immutable completion records.
2. Persist that value atomically on the owned work row whenever provider output
   is checkpointed. A checkpoint without valid provenance is not resumable and
   falls back to ordinary provider generation.
3. Rehydrate the provenance with the checkpoint in `WorkerRunner`; assessment
   and CV services use it unchanged when constructing immutable completion
   records.
4. Define separate dispatcher drain and launcher supervision budgets. The
   launcher must cover signal observation, the complete cooperative drain, and
   bounded forced cleanup before it may terminate the dispatcher.
5. Pass `queue.work_lease_seconds` as a float through both claim and heartbeat
   paths so their lease calculations agree exactly.

Prefer one shared provenance model because assessment and CV completion tables
store the same provider metadata fields. Keep service-specific content and
validation separate to avoid coupling the two domain pipelines.

## Technical Details

### Checkpoint provenance

- Add a frozen `WorkCheckpointProvenance` model in `app/work/models.py` with:
  `model`, `provider`, `response_ids`, `input_tokens`, `output_tokens`,
  `total_tokens`, and `repair_attempted`.
- Validate non-empty bounded model/provider values, bounded response IDs,
  non-negative token counts, and internally consistent token totals using the
  same expectations as provider results and completion records.
- Add a nullable checkpoint-provenance JSON column to `work_items`. Fresh
  databases receive the complete schema constraint; existing databases receive
  an idempotent migration in `initialize_database`.
- Serialize and deserialize only the allowlisted fields. Malformed, incomplete,
  or invalid stored provenance must not be trusted.
- Extend `WorkCheckpoint` and `WorkRepository.checkpoint` so the content path,
  content digest, input hashes, and provenance commit in one owned transaction.
- Treat legacy checkpoints without provenance as invalid for reuse. Preserve
  their artifacts, generate fresh provider output, and replace their durable
  checkpoint metadata when the current worker still owns the work.

### Service resume behavior

- On ordinary generation, derive checkpoint provenance directly from the
  sanitized `StructuredGenerationResult` returned by the provider boundary.
- On valid resume, retain the checkpoint's model, provider, response IDs,
  token usage, and repair flag exactly; do not synthesize a checkpoint model or
  zero usage.
- Continue validating path containment, content SHA-256, every relevant input
  hash, strict Pydantic content, citations, scoring, and deterministic output.
- Keep completion timestamps resolved after heartbeat shutdown and immediately
  before transactional finalization.

### Shutdown budgets

- Give the dispatcher an explicit cooperative drain budget and a bounded
  forced-cleanup phase for its supervised workers.
- Give the launcher a distinct supervision timeout that is strictly greater
  than the dispatcher's complete shutdown contract, including signal
  observation and forced cleanup.
- Use named constants or a small budget helper rather than duplicating timeout
  arithmetic across startup failure, server failure, dispatcher failure, and
  requested shutdown paths.
- Preserve child exit-code propagation and return non-zero when a long-lived
  child fails.

### Lease precision

- Change `WorkRepository.claim` to accept a positive `float`, matching
  `QueueSettings.work_lease_seconds` and `heartbeat`.
- Remove the integer cast in the dispatcher and calculate the initial expiry
  from the exact configured duration.
- Retain canonical microsecond UTC timestamps and existing timing validation.

## What Goes Where

- **Implementation Steps** contain all repository code, tests, migrations, and
  in-repository documentation changes.
- **Post-Completion** contains manual process inspection and GitHub workflow
  actions that require a real operating-system process tree or external state.

## Implementation Steps

### Task 1: Persist typed checkpoint provenance

**Files:**

- Modify: `app/work/models.py`
- Modify: `app/database.py`
- Modify: `app/work/repository.py`
- Modify: `app/work/runner.py`
- Modify: `tests/test_database.py`
- Modify: `tests/test_work_repository.py`
- Modify: `tests/test_runner.py`

- [x] Add failing model tests for valid provenance and invalid blank, oversized,
  negative-token, inconsistent-total, and malformed response-ID values.
- [x] Add failing database tests for fresh-schema provenance storage and an
  idempotent upgrade from the pre-Fix-2 `work_items` schema.
- [x] Add failing repository tests proving checkpoint content, hashes, and
  provenance persist atomically under the current worker token.
- [x] Add failing repository tests for stale ownership, malformed stored JSON,
  and legacy checkpoints with no provenance.
- [x] Implement `WorkCheckpointProvenance`, schema migration, allowlisted JSON
  serialization, and typed row hydration.
- [x] Extend the checkpoint repository and runner boundaries without passing
  provider clients, raw prompts, profile text, or exception details.
- [x] Run `uv run pytest tests/test_database.py
  tests/test_work_repository.py tests/test_runner.py`; all tests must pass
  before Task 2.

### Task 2: Preserve assessment provenance on checkpoint resume

**Files:**

- Modify: `app/assessment/service.py`
- Modify: `tests/test_assessment_service.py`

- [x] Add a failing valid-resume test proving the provider is not called,
  citations are revalidated, scoring is recomputed, and the exact original
  model, provider, response IDs, usage, and repair flag are persisted.
- [x] Add failing invalidation tests for missing provenance, wrong step,
  changed profile, JD, instruction, or schema hashes, changed artifact digest,
  missing artifact, malformed JSON, and invalid citations.
- [x] Add a failing cleanup/recovery test proving a checkpoint survives an
  interrupted attempt and is reusable by the next owner.
- [x] Add a failing completion-clock test proving finalization occurs at or
  after the latest heartbeat and the callback is not used before provider and
  deterministic work finish.
- [x] Implement provenance creation on ordinary generation and exact
  provenance reuse only after all checkpoint validations succeed.
- [x] Preserve the provider fallback path and ordinary synchronous service
  behavior for invalid or absent checkpoints.
- [x] Run `uv run pytest tests/test_assessment_service.py`; all tests must pass
  before Task 3.

### Task 3: Preserve CV provenance on checkpoint resume

**Files:**

- Modify: `app/cv/generator.py`
- Modify: `tests/test_cv_generator.py`

- [x] Add a failing valid-resume test proving the provider is not called, cited
  content is revalidated, deterministic DOCX rendering reruns, and the exact
  original provider provenance is persisted.
- [x] Add failing invalidation tests for missing provenance, wrong step,
  changed profile, JD, assessment result, instruction, schema, template, or
  layout hashes, changed artifact digest, missing artifact, malformed JSON,
  and invalid citations.
- [x] Add a failing writer-interruption test proving content and provenance
  survive together and remain reusable by the next owner.
- [x] Add a failing completion-clock test proving finalization occurs at or
  after the latest heartbeat and only after deterministic rendering.
- [x] Implement provenance creation on ordinary generation and exact
  provenance reuse only after all checkpoint validations succeed.
- [x] Preserve cleanup of attempt-owned candidates and provider fallback for
  invalid or absent checkpoints.
- [x] Run `uv run pytest tests/test_cv_generator.py`; all tests must pass before
  Task 4.

### Task 4: Supervise the complete dispatcher shutdown contract

**Files:**

- Modify: `app/cli.py`
- Modify: `app/work/dispatcher.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_dispatcher.py`

- [x] Add failing launcher tests for requested shutdown and server failure
  where the dispatcher consumes its entire cooperative drain budget before
  completing worker cleanup.
- [x] Add a failing lifecycle-double test proving the launcher does not
  terminate the dispatcher while it is recording terminated-worker failures.
- [x] Add a failing forced-timeout test proving the launcher eventually
  terminates a dispatcher that exceeds the complete supervision budget.
- [x] Add dispatcher tests for worker completion during grace, worker
  termination after grace, durable requeue/failure recording, and no retained
  child entries after shutdown.
- [x] Introduce one explicit shutdown-budget contract shared by launcher and
  dispatcher and use it on startup failure, sibling failure, and requested
  shutdown paths.
- [x] Preserve clean exit-code propagation and bounded shutdown behavior.
- [x] Run `uv run pytest tests/test_cli.py tests/test_dispatcher.py`; all tests
  must pass before Task 5.

### Task 5: Preserve fractional work-lease precision

**Files:**

- Modify: `app/work/dispatcher.py`
- Modify: `app/work/repository.py`
- Modify: `tests/test_dispatcher.py`
- Modify: `tests/test_work_repository.py`

- [x] Add a failing repository test asserting that a fractional claim lease
  produces the exact canonical expiry timestamp.
- [x] Add a failing dispatcher test asserting the configured fractional lease
  reaches `WorkRepository.claim` without conversion.
- [x] Add an edge test at the minimum valid timing relationship to prove the
  worker is not recoverable before the configured fractional expiry.
- [x] Change the claim contract to accept a positive float and remove the
  dispatcher integer cast.
- [x] Confirm heartbeat renewal and initial claim now use the same exact lease
  duration and timestamp precision.
- [x] Run `uv run pytest tests/test_dispatcher.py
  tests/test_work_repository.py tests/test_runner.py`; all tests must pass
  before Task 6.

### Task 6: Verify acceptance criteria and regression safety

**Files:**

- Modify: `docs/plans/2026-08-06-deliver-ai-assessment-and-one-page-cv-v1.md`
  only to record verified Task 9 Fix 2 results

- [x] Verify valid assessment and CV checkpoints skip provider calls while
  preserving exact immutable generation provenance.
- [x] Verify every invalid checkpoint dimension safely falls back to ordinary
  generation without weakening ownership or artifact path checks.
- [x] Verify completion timestamps remain at or after the latest heartbeat.
- [x] Verify requested shutdown and sibling failure allow the full dispatcher
  cleanup contract before bounded forced termination.
- [x] Verify fractional initial claims and heartbeat renewals use the exact
  configured lease.
- [x] Run `uv run pytest` and record the passing count.
- [x] Run `uv run ruff check .` and `uv run ruff format --check .`.
- [x] Run `uv run mypy app tests scripts`.
- [x] Run the generated AI schema drift check used by the main Task 9 plan.
- [x] Run `uv lock --check` and `git diff --check`.

### Task 7: [Final] Update documentation and close the fix plan

**Files:**

- Modify: `README.md` only if the launcher shutdown contract is documented
- Modify: `AGENTS.md` only if a durable project-wide convention emerges
- Move: this plan to `docs/plans/completed/`

- [x] Update existing launcher or durability documentation only where Fix 2
  changes a user-facing or maintainer-facing contract.
- [x] Confirm the main Task 9 plan records actual, not anticipated,
  verification results and test counts.
- [x] Confirm no Task 10 UI scope, new provider capability, or external service
  integration was added.
- [x] Mark every completed checklist item and document approved deviations.
- [x] Move this plan to `docs/plans/completed/`.
- [x] Run `git diff --check` after the move.

## Post-Completion

## Implementation Record

Implementation is complete for the repository and service scope described in
this plan. Focused regressions cover typed provenance validation, atomic
checkpoint persistence, exact assessment/CV provenance replay, and fractional
claim expiry. The complete verification suite passed with 383 tests. Manual
process-tree inspection and GitHub publication remain post-completion actions.

### Manual verification

- Start `jobhunter` against a temporary database, enqueue deterministic local
  work, send SIGTERM near the drain deadline, and confirm no dispatcher or
  worker process remains.
- Inspect completed assessment and CV rows after checkpoint recovery and
  confirm model, provider, response IDs, token usage, and repair metadata match
  the original provider result.
- Inspect a fractional-lease work row and confirm the stored initial expiry
  retains microsecond precision.

### External system updates

- Use a Conventional Commit containing the associated issue number when the
  implementation is ready to publish.
- Open a pull request from the feature branch; never push or merge directly to
  `master`.
- Comment on the associated GitHub issue with the commit or pull-request link
  after implementation is complete.
