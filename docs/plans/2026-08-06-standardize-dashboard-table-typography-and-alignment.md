# Standardize dashboard table typography, alignment, and asset freshness

## Overview

- Make all text in the desktop applications dashboard table use the same
  system font and `.8125rem` size.
- Correct the Notes action so it inherits the table typography instead of the
  global button size.
- Left-align Role, Company, and Notes; center all other desktop table columns.
- Vertically center desktop dashboard cell content.
- Revalidate local static assets on every page refresh so browser caches cannot
  retain an out-of-date dashboard stylesheet.
- Preserve the existing narrow-screen card layout and its current alignment.

## Context (from discovery)

- The project is a FastAPI/Jinja application, with dashboard presentation in
  `app/templates/applications/` and styles in `app/static/app.css`.
- `index.html` defines the 11-column applications table, and
  `_application_row.html` renders each body row, including the Notes edit
  button.
- `app/static/app.css` sets `.applications-table` text to `.8125rem`, but the
  Notes button still receives the global `button` font size of `1rem`.
- The desktop header is already centered. Body cells default to left alignment;
  the responsive breakpoint converts the table into a label/value card layout.
- Existing dashboard tests use FastAPI integration tests in `tests/`; no
  browser-driven CSS test suite is configured.
- `app/main.py` mounts FastAPI's `StaticFiles` at `/static`, while
  `base.html` references the unversioned `app.css` URL. Brave therefore reused
  a cached stylesheet until a hard reload, whereas Safari had the newer rules.

## Development Approach

- **Testing approach:** Regular (implement the styling change, then add
  regression coverage).
- Complete each task fully before moving to the next, keeping changes focused
  to desktop dashboard table presentation.
- Add regression tests alongside the UI change and run them before continuing.
- Update this plan if scope changes during implementation.
- Keep the mobile card presentation unchanged and retain existing truncation,
  editing, and accessibility behavior.
- Use `Cache-Control: no-cache` for local static files. This preserves efficient
  conditional reuse of unchanged assets while requiring the browser to
  revalidate CSS and JavaScript after a normal refresh.

## Testing Strategy

- Add a focused regression test that checks the dashboard row exposes stable
  semantic hooks for the Role and Company cells and that the table stylesheet
  contains the intended typography and alignment rules.
- Include an edge-case assertion that the Notes control retains its edit hook
  and its inherited table font rule, so its interactive behavior and text size
  do not regress independently.
- Add an integration assertion that `/static/app.css` sends the selected
  `Cache-Control: no-cache` policy.
- Add a stylesheet regression assertion that desktop application cells use
  `vertical-align: middle` without changing the narrow-screen card rules.
- Run `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`,
  and `uv run mypy app tests` after the implementation.
- Manually inspect the dashboard at a desktop viewport and a viewport below
  `40rem` because this project has no browser-level visual test runner.

## Progress Tracking

- Mark completed items with `[x]` immediately when done.
- Add newly discovered work with a `➕` prefix and blockers with a `⚠️` prefix.
- Keep this file synchronized with the implementation and test results.

## Solution Overview

Retain the semantic CSS classes on the Role and Company body cells, matching
the existing Notes-cell hook. Use those hooks to apply left alignment only to
the three requested text-heavy columns. Apply center alignment and vertical
centering to desktop table body cells by default, and explicitly make the
Notes edit button inherit the table font and surrounding text alignment. Scope
the rules so the responsive card media query continues to render as it does
today. Configure the static mount to send `Cache-Control: no-cache`, ensuring
each browser refresh revalidates the small local stylesheet and script while
allowing unchanged assets to use a conditional response.

## Technical Details

- Modify the Role and Company `<td>` elements in
  `app/templates/applications/_application_row.html` with semantic CSS classes;
  do not alter their links, titles, or data labels.
- In `app/static/app.css`, set desktop `.applications-table td` content to
  centered alignment, then override Role, Company, and Notes cells to left
  alignment.
- Add `font: inherit` (and inherited text alignment where needed) to the Notes
  edit button so it matches `.applications-table` typography despite the global
  `button` rule.
- Add `vertical-align: middle` to the desktop `.applications-table td` rule;
  the mobile breakpoint continues to use a grid layout and is unaffected.
- Extend the static-file response behavior in `app/main.py` so only `/static`
  responses include `Cache-Control: no-cache`; do not change cache behavior for
  HTML pages, application data, or uploaded CV responses.
- Keep all changes outside the existing `@media (max-width: 40rem)` behavior,
  except for an explicit safeguard if required to preserve card alignment.

## What Goes Where

- **Implementation Steps** contain the template, stylesheet, test, and
  verification work that can be completed in this repository.
- **Post-Completion** records the small amount of manual visual verification
  needed because CSS layout is not exercised by an end-to-end test suite.

## Implementation Steps

### Task 1: Add semantic hooks for left-aligned desktop text columns

**Files:**
- Modify: `app/templates/applications/_application_row.html`
- Modify: `tests/test_dashboard_cv_actions.py`

- [x] Add semantic Role and Company cell classes that match the existing Notes
  cell styling pattern.
- [x] Preserve every current role/company link, title, data-label, and rendered
  value while adding the classes.
- [x] Write regression assertions for the Role and Company cell hooks in a
  rendered dashboard row.
- [x] Write an edge-case assertion that a Notes cell retains its editor hook
  after the markup update.
- [x] Run the focused dashboard test with `uv run pytest
  tests/test_dashboard_cv_actions.py` before task 2.

### Task 2: Standardize desktop table typography and alignment

**Files:**
- Modify: `app/static/app.css`
- Modify: `tests/test_dashboard_cv_actions.py`

- [x] Center desktop application-table body cells by default.
- [x] Override Role, Company, and Notes cells to left alignment.
- [x] Make the Notes edit button inherit the dashboard table font and text
  alignment, retaining its current sizing and two-line truncation behavior.
- [x] Write stylesheet regression assertions for the default centered rule and
  the three left-aligned exceptions.
- [x] Write an edge-case assertion for the Notes inherited-font rule so the
  global button size cannot make it larger again.
- [x] Run the focused dashboard test with `uv run pytest
  tests/test_dashboard_cv_actions.py` before task 3.

### Task 3: Revalidate static assets and vertically center desktop cells

**Files:**
- Modify: `app/main.py`
- Modify: `app/static/app.css`
- Modify: `tests/test_routes.py`
- Modify: `tests/test_dashboard_cv_actions.py`

- [x] Configure the `/static` mount to return `Cache-Control: no-cache` for
  static asset responses, without altering response behavior for other routes.
- [x] Add `vertical-align: middle` to the desktop
  `.applications-table td` rule, preserving its existing mobile grid override.
- [x] Write a route integration test confirming `/static/app.css` has the
  `no-cache` directive and still returns the stylesheet successfully.
- [x] Write stylesheet regression assertions for desktop vertical centering and
  unchanged narrow-screen card alignment.
- [x] Run the focused route and dashboard tests with `uv run pytest
  tests/test_routes.py tests/test_dashboard_cv_actions.py` before task 4.

### Task 4: Verify dashboard table acceptance criteria

**Files:**
- Modify: `docs/plans/2026-08-06-standardize-dashboard-table-typography-and-alignment.md`

- [ ] Verify at a desktop viewport that Role, Company, and Notes are
  left-aligned and all remaining body columns are centered.
- [ ] Verify at a desktop viewport that each body cell's content is vertically
  centered, including multi-line and button content.
- [ ] Verify that Notes text has the same typeface and size as every other
  dashboard body cell.
- [ ] Verify in Safari and a Chromium-based browser that a standard reload
  receives the current stylesheet without requiring a hard reload.
- [ ] Verify below `40rem` that the card layout, labels, editing controls, and
  content readability are unchanged.
- [x] Run `uv run pytest` and confirm the complete suite passes.
- [x] Run `uv run ruff check .`, `uv run ruff format --check .`, and
  `uv run mypy app tests`.
- [x] Record the completed checklist and verification results in this plan.

### Task 5: Update documentation

- [x] Update README.md only if the dashboard behavior documentation needs to
  describe the new alignment convention.
- [x] Update AGENTS.md only if the work establishes a reusable project pattern.
- [ ] Move this plan to `docs/plans/completed/` once implementation and all
  verification steps are complete.

➕ Stabilized two unrelated route tests that had hard-coded the prior calendar
date. Their expectations now use the current UTC date, matching application
behavior and allowing the full test suite to run reliably.

➕ Investigation in Brave confirmed that the dashboard was using an older
cached `/static/app.css`: the newer `text-align: center` rule was absent from
the browser's applied stylesheet. A hard reload loaded the current CSS and
made Brave match Safari. The selected fix is static-asset revalidation instead
of versioned URLs or relying on manual hard reloads.

## Verification Results

- Original focused dashboard regression test: `2 passed`.
- Task 3 focused suite: `25 passed`.
- Full suite after Task 3: `49 passed`.
- `uv run ruff check .`: passed.
- `uv run ruff format --check .`: passed (23 files already formatted).
- `uv run mypy app tests`: passed (13 source files).
- The original typography and horizontal-alignment implementation passed its
  automated checks. Browser-based manual visual inspection remains pending for
  the newly added vertical-centering and static-asset revalidation scope; the
  plan has therefore not been archived.

## Post-Completion

**Manual verification:**

- Review a populated dashboard in the supported desktop browser widths.
- Use a normal refresh in Safari and a Chromium-based browser; confirm the
  current stylesheet is loaded without a hard reload.
- Confirm one-line, two-line, and button content is vertically centered on the
  desktop dashboard.
- Confirm the visual hierarchy remains readable in both light and dark color
  schemes.

**External system updates:**

- None expected.
