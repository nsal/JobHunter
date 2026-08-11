# Task 11 Fix 5: Stabilize Workflow Polling and CV Actions

Associated issue: [#49](https://github.com/nsal/JobHunter/issues/49)

## Overview

This bug fix addresses two defects found while reviewing the Task 11
asynchronous workflow UI. It prevents the self-replacing HTMX work-status
fragment from immediately requesting itself after every swap, and it ensures a
failed mismatch CV attempt offers only the dedicated retry action instead of
also redisplaying the initial `Generate CV anyway` action.

The change stays within the existing FastAPI, Jinja, HTMX, SQLite, and durable
work boundaries. It does not change polling frequency, workflow lifecycle
states, retry eligibility, queue behavior, assessment results, or CV generation
semantics.

## Context (from discovery)

- `app/templates/applications/_work_status.html` uses
  `hx-trigger="load, every 2s"` together with `hx-swap="outerHTML"`. Each active
  response inserts a new element whose `load` trigger fires immediately, so
  the intended two-second polling interval is bypassed.
- `tests/test_assessment_routes.py` verifies that active work polls and terminal
  work stops polling, but it does not assert the exact trigger contract that
  prevents immediate self-replacement loops.
- `app/templates/applications/_assessment.html` shows the mismatch override
  whenever there is no active or completed generation. After a mismatch CV
  failure, those conditions are true even though generation work already
  exists and `_work_status.html` also renders `Retry CV generation`.
- `app/routes/__init__.py::application_workflow()` already exposes
  `generation_work`, so the template can distinguish a first generation from
  a failed attempt without adding another state model or repository query.
- `tests/test_cv_generation_routes.py` covers fresh mismatch overrides, active
  matched generation, and CV retry guards, but not the controls rendered after
  a mismatch override fails.
- No browser E2E framework is configured. Existing route-level HTTP and
  rendered-HTML assertions are the appropriate automated regression boundary.

## Development Approach

- **Testing approach**: TDD. Add each focused regression first and confirm it
  fails for the reviewed reason before changing production templates.
- Complete each task fully before moving to the next task.
- Keep changes small and reuse the existing `generation_work` workflow
  projection.
- Every production change must have new or updated success and edge-case tests.
- All focused tests must pass before starting the next task.
- Update this plan immediately if implementation scope or interfaces change.
- Preserve backward compatibility for workflow routes, lifecycle stages, and
  durable work records.
- Use `uv` for execution, Ruff at 80 columns, and mypy for static verification.

## Testing Strategy

- **Polling contract tests**: assert active detail and work-status responses use
  `hx-trigger="every 2s"`, do not include the `load` trigger, and continue to
  remove polling attributes once work becomes terminal.
- **CV action precedence tests**: create a completed mismatch, queue and fail
  its override generation, and prove the detail page renders
  `Retry CV generation` without `Generate CV anyway`.
- **Success preservation**: retain or strengthen the assertion that a fresh
  completed mismatch still offers `Generate CV anyway`, and that active work
  continues to hide the initial override.
- **No browser dependency**: do not add Playwright, Cypress, or another E2E
  package. Manually inspect the browser network cadence after implementation.
- **Acceptance verification**: run the focused route tests, full Python suite,
  Ruff check and format check, mypy, generated-schema drift check, lockfile
  check, and Git whitespace check.

## Progress Tracking

- Mark completed items with `[x]` immediately when done.
- Add newly discovered tasks with a `➕` prefix.
- Record blockers with a `⚠️` prefix.
- Update this plan if implementation deviates from the selected design.
- Do not move to the next task while focused tests are failing.
- Keep recorded verification counts aligned with actual command output.

## Solution Overview

Keep the server-rendered active status as the initial view and use only HTMX's
periodic trigger for subsequent refreshes. Because the fragment already
contains current status, an immediate `load` request provides no user-visible
benefit. Removing it lets each newly swapped element wait the full two-second
interval before polling again.

Use the existing `generation_work` projection to separate an untouched
mismatch from one that has already attempted generation. The initial override
is available only before any generation work exists. Once a generation fails,
the failed-work branch remains the single source of retry controls.

## Technical Details

### Polling lifecycle

For queued or running work, `#work-status` will retain:

- `hx-get="/applications/{id}/work-status"`;
- `hx-trigger="every 2s"`;
- `hx-swap="outerHTML"`.

The initial page already renders the latest durable state, so no `load` trigger
is needed. A terminal response continues omitting all polling attributes,
which stops future requests after the final swap.

### CV action precedence

The mismatch override will render only when all of these remain true:

1. a completed assessment exists and its outcome is not `matched`;
2. the current lifecycle stage is `Mismatch`;
3. there is no active work;
4. there is no completed generation;
5. there is no prior CV-generation work.

The fifth condition uses the existing `generation_work` context value. A failed
CV attempt therefore leaves `Retry CV generation` as the sole generation
action. Queue and endpoint guards remain unchanged.

## What Goes Where

- **Implementation Steps** contain template, route-test, verification, and
  in-repository documentation work achievable in this checkout.
- **Post-Completion** contains browser observation and GitHub actions requiring
  external state.

## Implementation Steps

### Task 1: Enforce interval-only HTMX status polling

**Files:**

- Modify: `tests/test_assessment_routes.py`
- Modify: `app/templates/applications/_work_status.html`

- [x] Add failing rendered-HTML assertions proving active detail and focused
  status responses currently include the immediate `load` trigger.
- [x] Assert the required active contract is exactly periodic polling with
  `hx-trigger="every 2s"` and `hx-swap="outerHTML"`.
- [x] Retain the terminal-work assertion proving `hx-get` and polling triggers
  disappear when work is no longer queued or running.
- [x] Run `uv run pytest tests/test_assessment_routes.py` and confirm the new
  regression fails for the immediate-load reason.
- [x] Remove the `load` trigger from the active work-status template without
  changing the two-second interval, URL, swap target, or status content.
- [x] Re-run `uv run pytest tests/test_assessment_routes.py`; all tests must pass
  before Task 2.

### Task 2: Make failed mismatch generation expose retry only

**Files:**

- Modify: `tests/test_cv_generation_routes.py`
- Modify: `app/templates/applications/_assessment.html`

- [x] Add a focused helper or setup path that completes a mismatch assessment,
  queues its override generation, and records a terminal generation failure.
- [x] Add a failing detail-page test asserting the failed mismatch renders
  `Retry CV generation` and does not render `Generate CV anyway`.
- [x] Retain or strengthen the success test proving a fresh completed mismatch
  still renders `Generate CV anyway` before generation work exists.
- [x] Retain the active-work assertion proving an in-progress generation hides
  the initial mismatch override.
- [x] Run `uv run pytest tests/test_cv_generation_routes.py` and confirm the new
  regression fails because both generation actions are currently rendered.
- [x] Add the existing `generation_work` projection to the mismatch override
  condition; do not add repository queries, route state, or endpoint changes.
- [x] Re-run `uv run pytest tests/test_cv_generation_routes.py`; all tests must
  pass before Task 3.

### Task 3: Verify Task 11 Fix 5 acceptance criteria

**Files:**

- Modify: this plan only to record actual verification results

- [x] Verify an active status fragment waits for the periodic trigger and does
  not request immediately after each outer swap.
- [x] Verify terminal status fragments still stop polling.
- [x] Verify fresh mismatches offer the initial override, active generations
  suppress it, and failed mismatch generations expose only retry.
- [x] Run `uv run pytest tests/test_assessment_routes.py
  tests/test_cv_generation_routes.py` and record the passing count.
- [x] Run `uv run pytest` and record the complete passing count: 477 tests
  passed; the focused route suites passed 19 tests.
- [x] Run `uv run ruff check .`.
- [x] Run `uv run ruff format --check .`.
- [x] Run `uv run mypy app tests`.
- [x] Run `uv run python scripts/generate_ai_schemas.py --check`.
- [x] Run `uv lock --check` and `git diff --check`.

### Task 4: [Final] Update documentation and archive the plan

**Files:**

- Modify:
  `docs/plans/2026-08-06-deliver-ai-assessment-and-one-page-cv-v1.md`
- Modify: `README.md` only if user-facing workflow guidance changes
- Modify: `AGENTS.md` only if a reusable project convention is discovered
- Move: this plan to `docs/plans/completed/`

- [x] Record Task 11 Fix 5 behavior and verified command results in the main
  delivery plan.
- [x] Update README or AGENTS only when implementation creates a lasting user
  or contributor requirement.
- [x] Confirm every Task 11 Fix 5 implementation checkbox is complete and no
  blocker remains.
- [x] Move this plan to `docs/plans/completed/`.
- [x] Re-run `git diff --check` after documentation and plan updates.

## Post-Completion

*Items requiring manual intervention or external systems; no implementation
checkboxes.*

**Manual verification:**

- Open an application with queued or running work and use browser developer
  tools to confirm `/work-status` requests occur approximately every two
  seconds, with no immediate request chain after each response.
- Let the work become terminal and confirm polling stops after the terminal
  fragment is swapped.
- Open a fresh mismatch and confirm `Generate CV anyway` is present, then fail
  its CV attempt and confirm only `Retry CV generation` remains.

**External workflow:**

- Commit with a Conventional Commit message including the associated issue
  number.
- Open or update the feature-branch pull request; never push or merge directly
  to `master`.
- Comment on the associated GitHub issue with the commit or pull-request link
  after implementation is complete.
