# Task 11 Fix 2: Preserve Dashboard Workflow State and Lifecycle Timestamps

Associated issue: [#48](https://github.com/nsal/JobHunter/issues/48)

## Overview

This bug fix addresses the three defects found during review of the Task 11
asynchronous workflow UI. It makes terminal failures visible on the dashboard,
keeps workflow badges and artefact actions intact after HTMX row updates, and
prevents completed-generation promotion from failing when the current stage
uses an equivalent offset-bearing timestamp.

The work stays within the existing FastAPI, Jinja, SQLite, and durable-work
boundaries. It does not change lifecycle names, retry eligibility, assessment
or CV generation behavior, or the immutable result schemas.

## Context (from discovery)

- `app/templates/applications/_application_row.html` renders active work and
  assessment outcomes but does not render terminal work failures.
- `app/main.py` renders the same row after HTMX stage and notes updates using a
  plain `Repository.get_application()` result without workflow projection.
- `app/routes/__init__.py` already exposes `latest_work`, `active_work`,
  assessment metadata, and artefact availability for dashboard rows.
- `app/repository.py::promote_completed_generation()` compares canonical UTC
  instants but writes a canonical `effective_to` beside an offset-bearing raw
  `effective_from`, while SQLite validates the pair lexically.
- Existing route-level HTTP tests cover dashboard editing and workflow pages;
  no browser E2E framework is configured.

## Development Approach

- **Testing approach**: TDD. Add focused failing regressions before each
  production change and confirm each test fails for the reviewed reason.
- Complete each task fully before moving to the next task.
- Keep changes small and use the existing workflow projection rather than
  introducing a second dashboard-specific state model.
- Every production change must have new or updated success and edge-case tests.
- All focused tests must pass before starting the next task.
- Update this plan immediately if implementation scope or interfaces change.
- Preserve backward compatibility for existing application and stage records.
- Use `uv` for execution, Ruff at 80 columns, and mypy for static verification.

## Testing Strategy

- **Dashboard status tests**: prove failed assessment and CV work are visible
  on a full dashboard response while active and completed assessment summaries
  retain their existing behavior.
- **HTMX route tests**: prove successful stage and notes row replacements retain
  the current workflow badge and `Open artefacts` action.
- **Repository timestamp tests**: seed a current stage with a positive UTC
  offset, persist a chronologically later generation, and prove promotion
  closes the stage without an SQLite constraint failure. Also prove an older
  generation remains a no-op.
- **No browser E2E framework**: retain the project's HTTP and rendered-HTML
  assertions; do not add Playwright, Cypress, or another browser dependency.
- **Acceptance verification**: run the full Python suite, Ruff check and format
  check, mypy, generated-schema drift check, lockfile check, and Git whitespace
  check.

## Progress Tracking

- Mark completed items with `[x]` immediately when done.
- Add newly discovered tasks with a `➕` prefix.
- Record blockers with a `⚠️` prefix.
- Update this plan if implementation deviates from the selected design.
- Do not move to the next task while focused tests are failing.
- Keep recorded verification counts aligned with actual command output.

## Solution Overview

Use the existing workflow projection consistently at every dashboard rendering
boundary. The row template will prioritize an active work item, then a failed
latest work item, then the existing assessment summary. Both HTMX update paths
will attach the same workflow data used by the full dashboard before rendering
the replacement row.

Lifecycle promotion will continue comparing canonical UTC instants. When it
closes the current stage, it will write the already-computed canonical current
start and generation completion together in the same immediate transaction.
This preserves the represented instant while ensuring SQLite compares two
compatible timestamp strings, including for existing offset-bearing rows.

## Technical Details

### Dashboard status precedence

The compact row status will follow this order:

1. queued or running `active_work`;
2. failed `latest_work`, with the correct assessment or CV-generation label;
3. latest completed assessment outcome;
4. no compact workflow line when none of the above exists.

Failure details and retry controls remain on the application detail page. The
dashboard will expose only the safe work type and failed state.

### HTMX row projection

The successful stage-editor and notes-editor POST paths will call the existing
`application_workflow()` projection after reloading the application and before
rendering `_application_row.html`. Ordinary non-HTMX redirects continue to
reload the complete dashboard and require no extra projection.

### Offset-safe lifecycle promotion

`promote_completed_generation()` already derives canonical `completed_at` and
`current_from` values. Its closing update will persist both canonical values so
the `effective_to >= effective_from` schema check compares the same format.
The update and insertion remain atomic and idempotent. A generation that
chronologically precedes the current stage must still leave history unchanged.

## What Goes Where

- **Implementation Steps** contain repository, route, template, test, and
  in-repository documentation work achievable in this checkout.
- **Post-Completion** contains manual browser checks and GitHub actions that
  require external state.

## Implementation Steps

### Task 1: Show terminal workflow failures on dashboard rows

**Files:**

- Modify: `app/templates/applications/_application_row.html`
- Modify: `tests/test_dashboard_actions.py`

- [x] Add failing dashboard tests for a terminal assessment failure and a
  terminal CV-generation failure.
- [x] Assert the assessment failure renders `Assessment: failed` instead of no
  workflow summary.
- [x] Assert the CV failure renders `CV generation: failed` instead of the
  earlier assessment outcome.
- [x] Update row status precedence to render failed `latest_work` after active
  work and before the assessment fallback.
- [x] Add or retain success assertions for active work and completed assessment
  summaries so the new branch does not regress existing badges.
- [x] Run `uv run pytest tests/test_dashboard_actions.py`; all tests must pass
  before Task 2.

### Task 2: Retain workflow data after HTMX dashboard edits

**Files:**

- Modify: `app/main.py`
- Modify: `tests/test_routes.py`

- [x] Add failing route tests showing that stage-editor and notes-editor HTMX
  responses currently lose the workflow badge and artefact action.
- [x] Prepare an existing application artefact directory in the tests and
  assert `Open artefacts` survives each successful row swap.
- [x] Assert active assessment or CV status survives both successful row swaps.
- [x] Attach `application_workflow()` data before rendering each successful
  `_application_row.html` HTMX response.
- [x] Preserve ordinary redirect behavior, editor-close triggers, updated
  stage/notes values, and existing 404/422 responses.
- [x] Run `uv run pytest tests/test_routes.py`; all tests must pass before
  Task 3.

### Task 3: Make generation promotion safe for offset stage timestamps

**Files:**

- Modify: `app/repository.py`
- Modify: `tests/test_repository.py`

- [x] Add a failing repository regression with a current stage beginning at a
  positive UTC offset and a generation completing later by UTC instant.
- [x] Assert promotion returns true, creates one `Ready for review` stage, and
  closes the prior stage without an `IntegrityError`.
- [x] Assert the closed stage stores canonical, chronologically ordered start
  and end timestamps without changing the represented start instant.
- [x] Update the closing history write to persist canonical `current_from` and
  `completed_at` together inside the existing immediate transaction.
- [x] Add an edge-case test proving a generation that precedes the current
  offset stage remains a no-op and leaves history unchanged.
- [x] Retain or add an idempotency assertion proving repeated promotion does not
  append another `Ready for review` stage.
- [x] Run `uv run pytest tests/test_repository.py`; all tests must pass before
  Task 4.

### Task 4: Verify Task 11 Fix 2 acceptance criteria

**Files:**

- Modify: this plan only to record actual verification results

- [x] Verify failed assessment and CV work are distinguishable on the full
  dashboard without exposing failure details.
- [x] Verify HTMX stage and notes updates retain workflow and artefact controls.
- [x] Verify offset-bearing current stages promote successfully while older
  generations remain ignored.
- [x] Run `uv run pytest` and record the passing count.
- [x] Run `uv run ruff check .`.
- [x] Run `uv run ruff format --check .`.
- [x] Run `uv run mypy app tests`.
- [x] Run `uv run python scripts/generate_ai_schemas.py --check`.
- [x] Run `uv lock --check` and `git diff --check`.

Verification result: focused dashboard, route, and repository coverage passed
44 tests; the full suite passed 476 tests. Ruff check and format check, mypy,
generated-schema drift, lockfile, and whitespace checks all passed.

### Task 5: [Final] Update documentation and archive the plan

**Files:**

- Modify:
  `docs/plans/2026-08-06-deliver-ai-assessment-and-one-page-cv-v1.md`
- Modify: `README.md` only if user-facing workflow guidance changes
- Modify: `AGENTS.md` only if a reusable project convention is discovered
- Move: this plan to `docs/plans/completed/`

- [x] Record Task 11 Fix 2 behavior and verified command results in the main
  delivery plan.
- [x] Update README or AGENTS only when the implementation creates a lasting
  user or contributor requirement.
- [x] Confirm all Task 11 Fix 2 checkboxes are complete and no blocker remains.
- [x] Move this plan to `docs/plans/completed/`.
- [x] Re-run `git diff --check` after documentation and plan updates.

## Post-Completion

**Manual verification:**

- Open the dashboard with a failed assessment and a failed CV generation and
  verify the compact labels identify the correct failed work type.
- Edit stage and notes through their dashboard dialogs and verify the swapped
  row retains its workflow label and `Open artefacts` action.
- Exercise a copied development database containing an offset-bearing current
  stage and verify detail/dashboard reads promote a completed draft without a
  server error.

**External workflow:**

- Commit with a Conventional Commit message that includes the associated issue
  number.
- Open or update the feature-branch pull request; never push or merge directly
  to `master`.
- Comment on the associated GitHub issue with the commit or pull-request link
  after implementation is complete.
