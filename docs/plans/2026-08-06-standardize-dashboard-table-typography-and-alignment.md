# Standardize dashboard table typography and alignment

## Overview

- Make all text in the desktop applications dashboard table use the same
  system font and `.8125rem` size.
- Correct the Notes action so it inherits the table typography instead of the
  global button size.
- Left-align Role, Company, and Notes; center all other desktop table columns.
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

## Development Approach

- **Testing approach:** Regular (implement the styling change, then add
  regression coverage).
- Complete each task fully before moving to the next, keeping changes focused
  to desktop dashboard table presentation.
- Add regression tests alongside the UI change and run them before continuing.
- Update this plan if scope changes during implementation.
- Keep the mobile card presentation unchanged and retain existing truncation,
  editing, and accessibility behavior.

## Testing Strategy

- Add a focused regression test that checks the dashboard row exposes stable
  semantic hooks for the Role and Company cells and that the table stylesheet
  contains the intended typography and alignment rules.
- Include an edge-case assertion that the Notes control retains its edit hook
  and its inherited table font rule, so its interactive behavior and text size
  do not regress independently.
- Run `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`,
  and `uv run mypy app tests` after the implementation.
- Manually inspect the dashboard at a desktop viewport and a viewport below
  `40rem` because this project has no browser-level visual test runner.

## Progress Tracking

- Mark completed items with `[x]` immediately when done.
- Add newly discovered work with a `➕` prefix and blockers with a `⚠️` prefix.
- Keep this file synchronized with the implementation and test results.

## Solution Overview

Add semantic CSS classes to the Role and Company body cells, matching the
existing Notes-cell hook. Use those hooks to apply left alignment only to the
three requested text-heavy columns. Apply center alignment to desktop table
body cells by default, and explicitly make the Notes edit button inherit the
table font and surrounding text alignment. Scope the rules so the responsive
card media query continues to render as it does today.

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

### Task 3: Verify dashboard table acceptance criteria

**Files:**
- Modify: `docs/plans/2026-08-06-standardize-dashboard-table-typography-and-alignment.md`

- [ ] Verify at a desktop viewport that Role, Company, and Notes are
  left-aligned and all remaining body columns are centered.
- [ ] Verify that Notes text has the same typeface and size as every other
  dashboard body cell.
- [ ] Verify below `40rem` that the card layout, labels, editing controls, and
  content readability are unchanged.
- [x] Run `uv run pytest` and confirm the complete suite passes.
- [x] Run `uv run ruff check .`, `uv run ruff format --check .`, and
  `uv run mypy app tests`.
- [x] Record the completed checklist and verification results in this plan.

### Task 4: Update documentation

- [x] Update README.md only if the dashboard behavior documentation needs to
  describe the new alignment convention.
- [x] Update AGENTS.md only if the work establishes a reusable project pattern.
- [ ] Move this plan to `docs/plans/completed/` once implementation and all
  verification steps are complete.

➕ Stabilized two unrelated route tests that had hard-coded the prior calendar
date. Their expectations now use the current UTC date, matching application
behavior and allowing the full test suite to run reliably.

## Verification Results

- Focused dashboard regression test: `2 passed`.
- Full suite: `48 passed`.
- `uv run ruff check .`: passed.
- `uv run ruff format --check .`: passed (23 files already formatted).
- `uv run mypy app tests`: passed (13 source files).
- Desktop and sub-`40rem` CSS behavior was reviewed from the final cascade.
  Browser-based manual visual inspection remains pending because no browser is
  available in this environment; the plan has therefore not been archived.

## Post-Completion

**Manual verification:**

- Review a populated dashboard in the supported desktop browser widths.
- Confirm the visual hierarchy remains readable in both light and dark color
  schemes.

**External system updates:**

- None expected.
