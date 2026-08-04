# Fix CV Picker Path Encoding and PDF Detection

## Overview

- Correct the two reviewed CV usability defects without expanding routes,
  configuration, storage, or dependencies.
- Make picker navigation work for folder names containing spaces, `&`, `#`, and
  non-ASCII characters by URL-encoding directory query values.
- Recognize PDF CVs case-insensitively in the application detail view so an
  accepted `resume.PDF` opens the in-app preview rather than the Word download
  action.

## Context (from discovery)

- `app/templates/applications/_cv_picker.html` builds `hx-get` URLs by placing
  picker paths directly in the `directory` query value.
- `app/templates/applications/detail.html` selects the PDF preview action with
  a case-sensitive `.endswith('.pdf')` check.
- `app/cv_files.py` already validates extensions case-insensitively, making
  uppercase PDF paths valid stored CVs.
- `tests/test_routes.py` covers ordinary picker navigation and lowercase PDF
  delivery but not reserved-character paths or uppercase PDFs.

## Development Approach

- **Testing approach:** Regular — make each small template change, add its
  success and edge-path tests, and run all checks before the next task.
- Use Jinja's URL-encoding filter at the query-value boundary; do not alter the
  picker route or its path-containment checks.
- Normalize only the suffix comparison in the template. Preserve the existing
  PDF preview and Word download routes and their response behavior.
- Keep filename display text unchanged and escaped by Jinja; encode only values
  used to construct URLs.
- Update this plan if implementation discovers a material scope change.

## Testing Strategy

- Add route-rendering tests that assert encoded picker targets for nested paths
  containing spaces, `&`, `#`, and non-ASCII characters.
- Exercise the resulting encoded URLs against `/cv-picker` to prove the server
  decodes each directory correctly.
- Add an uppercase-PDF application test that asserts the detail view offers
  Preview CV and that the preview endpoint returns inline PDF content.
- Run `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`,
  and `uv run mypy app` after each task and again at final verification.

## Progress Tracking

- Mark each completed item with `[x]` immediately.
- Record scope changes with ➕ and blockers with ⚠️.
- Keep this plan synchronized with implementation and verification results.

## Solution Overview

The picker template will pass parent and child paths through URL encoding before
including them in `hx-get` query strings. The detail template will compare a
lowercased stored CV URI against the `.pdf` suffix. These presentation-only
changes retain the existing Python validation and endpoints while restoring
correct behavior for supported filenames.

## Technical Details

- Apply `|urlencode` to the `parent` and `item.path` values in picker route
  query strings; Jinja continues to HTML-escape the containing attribute.
- Apply `|lower` before the detail template's PDF suffix check.
- Build test fixtures beneath `JOBHUNTER_CV_ROOT` using actual directories and
  a `resume.PDF` fixture to exercise the same containment/normalization path as
  the picker and detail routes.

## What Goes Where

- **Implementation Steps** cover template, regression-test, and documentation
  updates in this repository.
- **Post-Completion** lists optional manual browser verification only.

## Implementation Steps

### Task 1: Encode picker directory query values

**Files:**
- Modify: `app/templates/applications/_cv_picker.html`
- Modify: `tests/test_routes.py`

- [ ] URL-encode parent and child directory values in their `hx-get` query
  strings without changing visible labels or picker target IDs.
- [ ] Retain safe, relative-path picker navigation and the existing root-level
  behavior.
- [ ] Write success tests for navigation through directories with spaces and
  non-ASCII names.
- [ ] Write edge-path tests for directories containing `&` and `#`, including
  assertions against the rendered encoded URLs and route responses.
- [ ] Run `uv run pytest`, `uv run ruff check .`,
  `uv run ruff format --check .`, and `uv run mypy app` — all must pass before
  Task 2.

### Task 2: Detect PDF CVs case-insensitively in the detail view

**Files:**
- Modify: `app/templates/applications/detail.html`
- Modify: `tests/test_routes.py`

- [ ] Normalize the stored CV URI for the PDF suffix check in the detail
  template and retain the existing Word download action for DOC/DOCX files.
- [ ] Ensure an accepted uppercase PDF renders the Preview CV control rather
  than the Download CV link.
- [ ] Write a success-path route test for an uppercase PDF's detail markup and
  inline preview response.
- [ ] Write an edge-path test confirming uppercase DOC/DOCX values retain the
  download action.
- [ ] Run `uv run pytest`, `uv run ruff check .`,
  `uv run ruff format --check .`, and `uv run mypy app` — all must pass before
  Task 3.

### Task 3: Verify acceptance criteria

**Files:**
- Modify: `tests/test_routes.py` (only if verification identifies a gap)

- [ ] Verify every requested special-character folder can be selected through
  the rendered picker links and reaches the expected directory.
- [ ] Verify PDF preview selection is consistent with case-insensitive file
  validation and no existing lowercase-PDF or Word behavior regresses.
- [ ] Add any missing success-path regression coverage discovered during
  verification.
- [ ] Add any missing error/edge-path regression coverage discovered during
  verification.
- [ ] Run the complete checks: `uv run pytest`, `uv run ruff check .`,
  `uv run ruff format --check .`, and `uv run mypy app`.

### Task 4: Update documentation and close the plan

**Files:**
- Modify: `README.md` (only if user-visible behavior changes need explaining)
- Modify: `docs/plans/2026-08-04-fix-cv-picker-path-encoding-and-pdf-detection.md`

- [ ] Confirm existing CV documentation remains accurate; update it only if
  implementation changes a documented user-visible behavior.
- [ ] Record completed verification commands and any deviation from this plan.
- [ ] Re-run `uv run pytest`, `uv run ruff check .`,
  `uv run ruff format --check .`, and `uv run mypy app` after final updates.
- [ ] Move this plan to `docs/plans/completed/` once every implementation and
  verification checkbox is complete.

## Completion Record

- [x] Task 1: URL-encoded picker parent and child query values, with route
  tests covering spaces, `&`, `#`, non-ASCII characters, and nested navigation.
- [x] Task 2: Made PDF detail-action selection case-insensitive, with tests for
  uppercase PDF preview and uppercase DOCX download behavior.
- [x] Task 3: Verified all acceptance criteria and added no further test gaps.
- [x] Task 4: Confirmed README guidance remains accurate. Ran `uv run pytest`,
  `uv run ruff check .`, `uv run ruff format --check .`, and `uv run mypy app`;
  all passed on 2026-08-04.

## Post-Completion

**Manual verification:**

- In a browser, open nested picker folders named with spaces, `&`, `#`, and
  non-ASCII characters, then select a CV from each.
- Confirm `resume.PDF` opens the inline preview dialog and DOC/DOCX still use
  the download action.

**External system updates:**

- None expected.
