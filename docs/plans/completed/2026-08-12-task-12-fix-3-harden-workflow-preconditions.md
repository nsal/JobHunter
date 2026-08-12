# Task 12 Fix 3: Harden workflow preconditions

## Overview

- Associated issue: [#53](https://github.com/nsal/JobHunter/issues/53).
- Close the four remaining review findings in the assessment-to-CV workflow.
- Reject unsupported taxonomy and retry configuration before the application
  reports setup readiness.
- Reject assessment inputs that cannot fit the provider request contract before
  creating an immutable application or queued work.
- Make newest-work selection deterministic when multiple work items share a
  queue timestamp.
- Use test-driven development for every behavior change: add a failing focused
  test, implement the smallest fix, then refactor only while tests remain green.

## Context (from discovery)

- `app/settings.py` accepts any syntactically valid taxonomy version and accepts
  `max_attempts=1`, although v1 supports only taxonomy `v1` and exactly two
  claims total.
- `app/repository.py` requires a non-empty job description but queues assessment
  work without proving that the current profile and JD fit the 500,000-character
  structured-request limit in `app/ai/providers/base.py`.
- `app/assessment/service.py` owns the canonical source-block serialization used
  for provider input, so preflight and worker execution must share that logic.
- `app/work/repository.py` orders equal `queued_at` values by random UUID, while
  workflow rendering treats the first item as the latest work.
- The project uses FastAPI, direct SQLite repositories, pytest, Ruff, mypy, and
  a fresh-schema policy. There is no browser E2E suite or live OpenAI call in
  automated tests.

## Development Approach

- **Testing approach:** TDD. Begin each task with a focused test that fails for
  the reviewed regression, confirm the expected failure, then implement the
  smallest production change that makes it pass.
- Complete and verify each task before starting the next task.
- Keep configuration validation at the configuration boundary, payload sizing
  at the shared assessment-input boundary, and ordering guarantees in durable
  work persistence.
- Do not add dependencies or broaden v1 behavior. Preserve the documented fixed
  taxonomy and one-transient-retry policy.
- Add or update tests for every modified code path, including success,
  rejection, boundary, and replay/order cases.
- Run the focused test file after each red/green cycle and `uv run pytest` before
  proceeding to the next task.
- Update this plan immediately if implementation scope or file ownership
  changes.

## Testing Strategy

- **Settings tests:** prove unsupported-but-well-formed taxonomy versions and
  unsupported attempt limits fail during configuration loading, while the
  tracked v1 configuration remains valid.
- **Setup-route tests:** prove unsupported taxonomy configuration is reported as
  incomplete and cannot create an application.
- **Assessment-input tests:** prove the exact serialized payload accepts normal
  inputs, rejects oversized profile/JD combinations at the shared boundary, and
  uses the same serialization during worker execution.
- **Application-route tests:** prove rejected input returns a safe validation
  response and leaves applications, history, and work tables unchanged.
- **Work repository tests:** create work with identical queue timestamps and
  prove insertion order, latest-work selection, and retry UI state are stable.
- **Schema tests:** prove the durable ordering column is generated, unique, and
  does not change across work-state transitions.
- **Regression suite:** run all 489+ tests and every project gate. No browser E2E
  framework or live OpenAI request is added for this bug fix.

## Progress Tracking

- Mark completed items with `[x]` immediately after their tests pass.
- Add newly discovered work with a `➕` prefix.
- Record blockers with a `⚠️` prefix and name the affected acceptance
  criterion.
- Keep the plan synchronized with actual implementation paths and commands.
- Do not move the plan to `docs/plans/completed/` until every automated gate
  passes.

## Solution Overview

Use existing domain boundaries rather than introducing a second validation
system:

1. Resolve the configured taxonomy through the authoritative taxonomy registry
   during settings loading. Constrain `max_attempts` to the only v1-supported
   value, two, rather than pretending it is runtime-configurable.
2. Extract a shared assessment-input builder/validator from the service's
   existing source-block and JSON serialization path. The create route runs it
   against the current validated profile and submitted JD before repository
   insertion; the worker reuses it so the two boundaries cannot drift.
3. Add an explicit SQLite-generated insertion sequence to work items and use it
   as the tie-breaker after `queued_at`. Keep opaque work IDs unchanged and do
   not infer chronology from UUID text.

This approach keeps v1 behavior fixed, rejects impossible work early, and makes
workflow status deterministic without relying on wall-clock precision.

## Technical Details

### Configuration contracts

- After Pydantic shape validation, resolve `scoring.taxonomy_version` through
  `app.assessment.taxonomy.get_taxonomy` and translate unsupported values to a
  safe `SettingsError`.
- Represent `queue.max_attempts` as the fixed supported value `2`, or add an
  equivalent validator that rejects every other value.
- Keep `config/ai.yaml` unchanged at `v1` and two attempts.
- Ensure `inspect_setup` continues to redact configuration details while
  reporting settings as unavailable.

### Assessment payload preflight

- Reuse the current source normalization, block parsing, block document shape,
  JSON separators, key ordering, and `MAX_INPUT_LENGTH` constant.
- Give the shared helper a typed return value containing the profile blocks, JD
  blocks, and serialized input text needed by `AssessmentService.execute`.
- Raise a bounded domain validation error when the exact serialized input is
  empty or exceeds the provider contract.
- Run preflight after setup/consent checks but before
  `Repository.create_application`, so rejection leaves no application, history,
  artefact allocation, or work row.
- Retain worker-side validation because private inputs may change after an
  application is queued.

### Durable work ordering

- Add an SQLite-generated integer sequence to the fresh `work_items` schema.
- Keep `id` as a required unique opaque identifier used by workers and domain
  repositories.
- Add the sequence to `WorkItem` so `SELECT *` remains a fully typed projection.
- Order application work by `queued_at DESC, sequence DESC`; do not use UUIDs as
  chronology.
- Verify retries inserted at the same timestamp sort ahead of earlier failures,
  and verify state transitions never rewrite their insertion sequence.

## What Goes Where

- **Implementation Steps:** settings validation, shared input preflight, schema
  ordering, focused tests, full verification, and documentation updates.
- **Post-Completion:** one manual browser check of safe validation messaging and
  retry status; no deployment, migration, or third-party changes are required.

## Implementation Steps

### Task 1: Fail unsupported v1 configuration during loading

**Files:**

- Modify: `app/settings.py`
- Modify: `app/assessment/taxonomy.py` if a small public resolver contract is
  needed
- Modify: `tests/test_settings.py`
- Modify: `tests/test_setup_routes.py`

- [x] Add failing settings tests for well-formed unsupported taxonomy `v2` and
  unsupported `max_attempts=1`.
- [x] Add a failing setup-route test proving unsupported taxonomy is reported as
  incomplete and blocks application creation without leaking configuration
  details.
- [x] Run `uv run pytest tests/test_settings.py tests/test_setup_routes.py` and
  confirm the new tests fail for the reviewed reasons.
- [x] Validate taxonomy support through the authoritative registry and normalize
  failures to `SettingsError`.
- [x] Constrain `max_attempts` to the fixed v1 value of two.
- [x] Add or update success tests proving the tracked configuration still loads
  and setup remains ready.
- [x] Run `uv run pytest tests/test_settings.py tests/test_setup_routes.py`; all
  tests must pass before Task 2.

### Task 2: Reject unprocessable assessment inputs before persistence

**Files:**

- Modify: `app/assessment/service.py`
- Modify: `app/main.py`
- Modify: `tests/test_assessment_service.py`
- Modify: `tests/test_routes.py`

- [x] Add failing unit tests for normal and oversized combined profile/JD
  payloads using the exact serialized provider-input contract.
- [x] Add failing route tests proving oversized input returns a safe validation
  response and inserts no application, stage-history, or work row.
- [x] Add a boundary test proving an ordinary large-but-valid payload is still
  accepted and queued.
- [x] Run `uv run pytest tests/test_assessment_service.py tests/test_routes.py`
  and confirm the new tests fail for the reviewed reasons.
- [x] Extract a typed shared assessment-input builder that parses both sources,
  serializes the canonical JSON document, and enforces `MAX_INPUT_LENGTH`.
- [x] Reuse the helper from `AssessmentService.execute` and from application
  creation before repository persistence.
- [x] Keep validation messages bounded and free of profile/JD content and private
  paths.
- [x] Run `uv run pytest tests/test_assessment_service.py tests/test_routes.py`;
  all tests must pass before Task 3.

### Task 3: Make equal-timestamp work ordering deterministic

**Files:**

- Modify: `app/database.py`
- Modify: `app/work/models.py`
- Modify: `app/work/repository.py`
- Modify: `tests/test_database.py`
- Modify: `tests/test_work_repository.py`
- Modify: `tests/test_assessment_routes.py`
- Modify: `tests/test_cv_generation_routes.py`

- [x] Add failing schema and repository tests for two work items inserted with an
  identical `queued_at`, asserting the later insertion is listed first.
- [x] Add failing workflow tests proving a same-timestamp retry cannot leave an
  older failed item selected as `latest_work` after retry completion.
- [x] Run the focused database, repository, assessment-route, and CV-route tests
  and confirm the new tests fail for the UUID tie-breaker.
- [x] Add the explicit generated work sequence and preserve unique opaque work
  IDs in the fresh schema.
- [x] Add the sequence to the typed `WorkItem` projection and order work history
  by timestamp followed by sequence.
- [x] Add transition tests proving claim, heartbeat, checkpoint, retry, failure,
  recovery, and completion do not alter the insertion sequence.
- [x] Run `uv run pytest tests/test_database.py tests/test_work_repository.py
  tests/test_assessment_routes.py tests/test_cv_generation_routes.py`; all tests
  must pass before Task 4.

### Task 4: Verify acceptance criteria and regression safety

**Files:**

- Modify: tests touched by Tasks 1-3 only if an uncovered acceptance edge is
  discovered
- Modify: this plan if verification changes scope

- [x] Verify unsupported taxonomy and attempt settings fail before readiness or
  queue insertion.
- [x] Verify oversized combined assessment input cannot create immutable data,
  while normal input still queues successfully.
- [x] Verify equal-timestamp retries always become the latest work item and the
  workflow renders the terminal result rather than stale failure guidance.
- [x] Run `uv run pytest` and confirm the complete suite passes (498 passed).
- [x] Run `uv run ruff check .` and `uv run ruff format --check .`.
- [x] Run `uv run mypy app tests scripts`.
- [x] Run `uv run python scripts/generate_ai_schemas.py --check`.
- [x] Run `uv lock --check` and `git diff --check`.
- [x] Confirm no dependency or lockfile change was introduced.

### Task 5: [Final] Update documentation and archive the plan

**Files:**

- Modify: `README.md` if supported configuration/input behavior needs
  clarification
- Modify:
  `docs/plans/completed/2026-08-06-deliver-ai-assessment-and-one-page-cv-v1.md`
- Move: this plan to `docs/plans/completed/`

- [x] Document that v1 supports taxonomy `v1` and exactly two claims total if
  the existing README wording is insufficient.
- [x] Record Task 12 Fix 3 implementation and final test results in the parent
  completed plan.
- [x] Re-run `uv run pytest`, Ruff, mypy, schema drift, lockfile, and diff checks
  after documentation changes.
- [x] Mark every checklist item complete and move this plan to
  `docs/plans/completed/`.
- [x] Add a completion comment to the associated GitHub issue with the commit or
  PR link, following `AGENTS.md`.

## Post-Completion

### Manual verification

- With a temporary unsupported taxonomy configuration, open `/setup` and
  confirm it reports only a safe invalid-settings message.
- Submit a deliberately oversized synthetic JD and confirm the form remains
  editable, no application is created, and no private content appears in the
  response or logs.
- Exercise one fast synthetic failure/retry cycle and confirm polling ends on
  the newest terminal state rather than the earlier failure.

### External system updates

- No production database migration is planned because this branch explicitly
  uses the documented fresh-schema policy.
- No live OpenAI request is required for acceptance; retain the existing manual
  non-sensitive live-call check for post-merge operator validation.
