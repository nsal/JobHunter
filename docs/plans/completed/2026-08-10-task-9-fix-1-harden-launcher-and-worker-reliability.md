# Task 9 Fix 1: Harden Launcher and Worker Reliability

## Overview

This bug-fix closes the six reliability defects found in the Task 9 launcher,
dispatcher, and spawned-worker implementation. It makes the `jobhunter`
console command installable, removes the fresh-database startup race, ensures
long-running AI work can finalize after heartbeats, honors the configured work
lease, resumes validated checkpoints, and shuts down without orphaning worker
processes.

The changes retain the existing one-launcher, one-dispatcher, maximum-three-
worker architecture. They strengthen its process and durability boundaries
without adding distributed coordination, desktop automation, or new UI scope.

## Context (from discovery)

- Relevant components are `pyproject.toml`, `app/cli.py`,
  `app/work/dispatcher.py`, `app/work/runner.py`,
  `app/work/repository.py`, `app/assessment/service.py`, and
  `app/cv/generator.py`.
- The project uses Python 3.14, FastAPI/Uvicorn, spawned multiprocessing
  children, SQLite-backed durable work, Pydantic models, and `uv` tooling.
- Work ownership is token-bound. Heartbeats, checkpoints, failure, and domain
  completion each open independent SQLite connections and enforce canonical
  timestamp ordering.
- Assessment and CV services already validate provider output and write
  artifacts atomically, but the new runner does not consume durable
  checkpoints and currently supplies completion time before doing the work.
- Existing Task 9 tests use frozen clocks and process doubles. They pass, but
  do not exercise installed entry points, fresh startup, heartbeat/finalizer
  ordering, checkpoint recovery, or launcher shutdown with worker descendants.

## Development Approach

- **Testing approach**: TDD. For every behavior, add a focused failing test,
  run it to confirm the intended failure, implement the smallest correction,
  and refactor only after the focused tests pass.
- Complete each task fully before moving to the next task.
- Keep changes small and focused on the six reviewed defects.
- Every task that changes code must add or update tests for its success and
  error/edge paths.
- All focused tests must pass before starting the next task.
- Update this plan immediately if implementation scope or interfaces change.
- Preserve existing synchronous service call sites and token-bound repository
  invariants.
- Use `uv` for all execution and dependency operations, Ruff for formatting
  and linting, and mypy for static type verification.

## Testing Strategy

- **Unit tests**: extend `tests/test_cli.py`, `tests/test_dispatcher.py`, and
  `tests/test_runner.py` for orchestration, clocks, leases, and process
  lifecycle behavior.
- **Service tests**: extend `tests/test_assessment_service.py` and
  `tests/test_cv_generator.py` for valid checkpoint reuse, invalidation,
  validation, deterministic downstream work, and cleanup behavior.
- **Repository tests**: update `tests/test_work_repository.py` only if the
  checkpoint or lease repository contract must change.
- **Spawn smoke tests**: keep tests bounded and deterministic. Do not call
  OpenAI, Uvicorn sockets, Microsoft Word, or desktop automation.
- **CLI acceptance test**: verify the installed `jobhunter` entry point can
  display help from the project environment.
- **No browser E2E tests**: this fix does not change UI routes or rendered
  behavior.
- Run the focused test file after each red/green cycle and `uv run pytest`
  before acceptance verification.

## Progress Tracking

- Mark completed items with `[x]` immediately when done.
- Add newly discovered work with a `➕` prefix.
- Record blockers with a `⚠️` prefix.
- Keep this plan aligned with the implementation and actual verification
  results.
- Do not begin a later task while the current task's focused tests fail.

## Solution Overview

Use explicit lifecycle boundaries instead of timing assumptions:

1. Make JobHunter a packaged uv project so `[project.scripts]` installs the
   `jobhunter` executable.
2. Initialize the SQLite schema synchronously before either supervised child
   can poll it; no live parent SQLite connection crosses a spawn boundary.
3. Separate work-start time from domain-completion time. Resolve completion
   immediately before repository finalization, after the final heartbeat that
   can affect the attempt.
4. Pass `queue.work_lease_seconds` into every heartbeat rather than relying on
   the repository's default.
5. Treat checkpoints as typed, version-bound provider outputs. Validate their
   path, digest, complete input hashes, and Pydantic content before reuse, then
   repeat deterministic validation/scoring/rendering as appropriate.
6. Give the dispatcher its full drain budget on every launcher exit path and
   terminate it only after that bounded cooperative shutdown fails.

## Technical Details

### Packaging and startup

- Enable package installation using the minimal uv-supported project setting
  or build-system declaration.
- Keep `jobhunter = "app.cli:main"` as the console-script target.
- Initialize the selected database path before starting Uvicorn and the
  dispatcher. Initialization must finish and close its connection before
  `Process.start()` is called.
- Return a non-zero launcher status for initialization failures without
  starting either child.

### Time and lease handling

- Preserve deterministic timestamps supplied by existing service tests and
  synchronous call sites.
- Add an explicit finalization-time mechanism for spawned workers so each
  service resolves `completed_at` after provider and deterministic artifact
  work, just before constructing its immutable completion record.
- Ensure the heartbeat cannot advance durable activity beyond the chosen
  completion timestamp during finalization.
- Give `_Heartbeat` an explicit lease duration and pass
  `settings.queue.work_lease_seconds` through `WorkRepository.heartbeat`.

### Checkpoint contract

- An assessment checkpoint contains validated `AssessmentResult` JSON. A
  resumed attempt must parse it, revalidate citations, and recompute scoring
  locally without making another provider call.
- A CV checkpoint contains validated `CvContent` JSON and is recorded before
  DOCX rendering. A resumed attempt must parse and revalidate the content, then
  rerun deterministic DOCX rendering without making another provider call.
- Bind checkpoints to every relevant current input: profile, JD,
  instruction/prompt, schema, assessment result where applicable, template,
  and layout.
- Verify the artifact's current SHA-256 digest and safe contained path before
  parsing it. A missing, changed, malformed, wrong-step, or hash-mismatched
  checkpoint is invalid and must not be trusted.
- Decide invalid-checkpoint behavior explicitly in tests: discard it and run
  the ordinary provider path while preserving token ownership and safe error
  handling.
- Preserve a valid checkpoint across retryable failure or stale-worker
  recovery. Do not delete a checkpoint merely because later deterministic
  work was interrupted.

### Shutdown contract

- Share or explicitly pass the dispatcher's drain timeout so the launcher
  waits longer than the complete dispatcher drain budget.
- On server failure, signal dispatcher shutdown, wait for bounded draining,
  and only then force termination.
- On user-requested shutdown, apply the same ordering before terminating the
  web process and returning.
- Preserve child exit-code propagation when either long-lived process fails.

## What Goes Where

- **Implementation Steps** contain repository changes, tests, configuration,
  and in-repository documentation.
- **Post-Completion** contains manual process inspection and GitHub workflow
  actions that cannot be fully proven by unit tests alone.

## Implementation Steps

### Task 1: Install and exercise the `jobhunter` console command

**Files:**
- Modify: `pyproject.toml`
- Modify: `tests/test_cli.py`
- Modify: `uv.lock` only if uv reports a lockfile change

- [x] Add a failing CLI acceptance test proving the `jobhunter` executable is
  discoverable and `jobhunter --help` exits successfully.
- [x] Run `uv run pytest tests/test_cli.py` and confirm failure is caused by
  the missing installed entry point.
- [x] Enable minimal project packaging so uv installs `[project.scripts]`.
- [x] Add or update the error-path test so an invalid CLI option still exits
  non-zero through the installed command.
- [x] Run `uv sync`, `uv lock --check`, and
  `uv run pytest tests/test_cli.py`; all must pass before Task 2.

### Task 2: Initialize SQLite before supervised children start

**Files:**
- Modify: `app/cli.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_dispatcher.py`

- [x] Add a failing test that launches against a nonexistent database and
  asserts the schema exists before either process factory starts a child.
- [x] Add a failing error-path test proving schema initialization failure
  starts neither Uvicorn nor the dispatcher and returns a non-zero status.
- [x] Run the focused CLI/dispatcher tests and confirm the fresh-database race
  is reproduced.
- [x] Initialize the database synchronously at the launcher boundary and close
  the connection before spawning either process.
- [x] Retain an independent dispatcher startup regression test so direct
  dispatcher execution cannot poll an uninitialized schema if that remains a
  supported entry boundary.
- [x] Run `uv run pytest tests/test_cli.py tests/test_dispatcher.py`; all must
  pass before Task 3.

### Task 3: Finalize work with a post-execution timestamp

**Files:**
- Modify: `app/work/runner.py`
- Modify: `app/assessment/service.py`
- Modify: `app/cv/generator.py`
- Modify: `tests/test_runner.py`
- Modify: `tests/test_assessment_service.py`
- Modify: `tests/test_cv_generator.py`

- [x] Add failing tests where the completion clock advances beyond at least
  one heartbeat during assessment and CV execution.
- [x] Assert both services persist a completion timestamp at or after the
  latest durable heartbeat, not the timestamp captured before provider work.
- [x] Run the focused runner and service tests and confirm both work types fail
  under the old ordering.
- [x] Introduce the smallest explicit finalization-time interface that keeps
  existing deterministic synchronous service callers backward compatible.
- [x] Coordinate final heartbeat shutdown/finalization so no heartbeat can
  race ahead of the resolved completion timestamp.
- [x] Add edge tests for provider/service failure and stale ownership so the
  new clock interface does not bypass durable failure handling.
- [x] Run `uv run pytest tests/test_runner.py tests/test_assessment_service.py
  tests/test_cv_generator.py`; all must pass before Task 4.

### Task 4: Renew heartbeats with the configured work lease

**Files:**
- Modify: `app/work/runner.py`
- Modify: `tests/test_runner.py`

- [x] Add a failing heartbeat test using a non-default valid work lease and
  assert that exact duration reaches `WorkRepository.heartbeat`.
- [x] Add an edge test proving heartbeat ownership loss still stops the
  heartbeat thread without exposing exception details.
- [x] Run `uv run pytest tests/test_runner.py` and confirm the current
  hard-coded 60-second renewal causes the first test to fail.
- [x] Pass `settings.queue.work_lease_seconds` explicitly through `_Heartbeat`
  and preserve its existing bounded stop/join behavior.
- [x] Run `uv run pytest tests/test_runner.py tests/test_work_repository.py`;
  all must pass before Task 5.

### Task 5: Resume validated assessment checkpoints

**Files:**
- Modify: `app/work/runner.py`
- Modify: `app/assessment/service.py`
- Modify: `app/work/repository.py` if checkpoint validation needs a narrower
  repository contract
- Modify: `tests/test_runner.py`
- Modify: `tests/test_assessment_service.py`
- Modify: `tests/test_work_repository.py` if the repository contract changes

- [x] Add a failing test that recovers assessment work with a valid checkpoint
  and proves the provider generator is not called.
- [x] Assert resumed content is parsed as `AssessmentResult`, citations are
  revalidated, scoring is recomputed, and immutable completion succeeds.
- [x] Add failing invalidation tests for changed profile, JD, instruction,
  schema, artifact digest, malformed JSON, missing file, and wrong checkpoint
  step.
- [x] Run the focused tests and confirm the runner currently ignores every
  checkpoint.
- [x] Record complete assessment checkpoint hashes and implement safe typed
  reuse while retaining the ordinary provider path for invalid checkpoints.
- [x] Preserve valid assessment checkpoint artifacts across retryable/stale
  recovery and avoid deleting artifacts owned by an earlier attempt.
- [x] Run `uv run pytest tests/test_runner.py tests/test_assessment_service.py
  tests/test_work_repository.py`; all must pass before Task 6.

### Task 6: Resume CV content before deterministic DOCX rendering

**Files:**
- Modify: `app/work/runner.py`
- Modify: `app/cv/generator.py`
- Modify: `app/work/repository.py` if shared checkpoint validation changes
- Modify: `tests/test_runner.py`
- Modify: `tests/test_cv_generator.py`
- Modify: `tests/test_work_repository.py` if the repository contract changes

- [x] Add a failing test that recovers CV work with valid `CvContent` JSON,
  skips the provider generator, and still invokes the deterministic writer.
- [x] Move checkpoint creation to the validated content boundary before DOCX
  rendering, rather than checkpointing the final candidate.
- [x] Add failing invalidation tests for profile, JD, assessment result,
  instruction, schema, template, layout, artifact digest, malformed JSON,
  missing file, and wrong checkpoint step.
- [x] Run the focused tests and confirm current CV retries always rerun the
  provider and cannot resume at the rendering boundary.
- [x] Implement safe typed CV checkpoint reuse and preserve ordinary
  generation behavior when a checkpoint is invalid.
- [x] Add cleanup/interruption tests proving a valid content checkpoint
  survives an interrupted writer and can be reused by the next attempt.
- [x] Run `uv run pytest tests/test_runner.py tests/test_cv_generator.py
  tests/test_work_repository.py`; all must pass before Task 7.

### Task 7: Drain dispatcher workers on every launcher exit path

**Files:**
- Modify: `app/cli.py`
- Modify: `app/work/dispatcher.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_dispatcher.py`

- [x] Add failing launcher tests proving server failure and requested shutdown
  signal the dispatcher and wait through its full drain budget before any
  forced termination.
- [x] Add a bounded process test or lifecycle double that represents an active
  dispatcher child with worker descendants and detects eager termination.
- [x] Run the focused CLI/dispatcher tests and confirm the current immediate
  and five-second termination paths fail the new assertions.
- [x] Share or pass an explicit dispatcher drain timeout and make launcher
  shutdown ordering cooperative-first, forceful-second.
- [x] Preserve non-zero child status propagation and add an edge test for a
  dispatcher that exceeds its bounded drain deadline.
- [x] Run `uv run pytest tests/test_cli.py tests/test_dispatcher.py`; all must
  pass before Task 8.

### Task 8: Verify acceptance criteria and regression safety

**Files:**
- Modify: `docs/plans/2026-08-06-deliver-ai-assessment-and-one-page-cv-v1.md`
  only to record final Task 9 Fix 1 verification

- [x] Verify the installed `jobhunter --help` command succeeds.
- [x] Verify a fresh database starts without a dispatcher/schema race.
- [x] Verify assessment and CV work can complete after multiple heartbeats.
- [x] Verify configured leases, valid checkpoint resume, invalid checkpoint
  fallback, and bounded shutdown for both normal and sibling-failure paths.
- [x] Run `uv run pytest` and record the passing count.
- [x] Run `uv run ruff check .` and `uv run ruff format --check .`.
- [x] Run `uv run mypy app tests scripts`.
- [x] Run the generated-schema drift check used by the main Task 9 plan.
- [x] Run `uv lock --check` and `git diff --check`.
- [x] Record all verification results in the main Task 9 plan before Task 9.

Verification results: `uv run pytest` — 375 passed; focused launcher,
dispatcher, runner, repository, assessment, and CV tests — 152 passed;
`uv run ruff check .`, `uv run ruff format --check .`, and
`uv run mypy app tests scripts` passed; the generated AI schema drift check,
`uv lock --check`, and `git diff --check` passed. The installed
`jobhunter --help` acceptance test passed. No external provider, Uvicorn
socket, desktop automation, commit, push, or issue comment was performed in
this workspace session.

### Task 9: [Final] Update documentation and close the fix plan

**Files:**
- Modify: `README.md` only if launcher installation or invocation is already
  documented there
- Modify: `AGENTS.md` only if a durable new project-wide convention emerged
- Move: this plan to `docs/plans/completed/`

- [x] Update existing launcher documentation only where the corrected package,
  startup, checkpoint, lease, or shutdown behavior is user-facing.
- [x] Confirm no premature Task 10 or later scope was added.
- [x] Mark every completed checklist item and record any approved deviations.
- [x] Move this plan to `docs/plans/completed/`.
- [x] Run `git diff --check` after the move.

## Post-Completion

### Manual verification

- Start `jobhunter` against a temporary fresh database, enqueue deterministic
  local work, send SIGTERM, and confirm no dispatcher or worker process remains.
- Inspect SQLite work rows after checkpoint recovery and verify attempt,
  checkpoint, heartbeat, completion, and sanitized error fields remain
  consistent.
- Confirm no real OpenAI request or private profile content is used during
  automated verification.

### External system updates

- Commit with a Conventional Commit message containing the associated issue
  number.
- Push only to the dedicated feature/fix branch and open or update a pull
  request; never push or merge directly to `master`.
- After implementation, comment on the associated GitHub issue with the commit
  or pull-request link and the final verification results.
