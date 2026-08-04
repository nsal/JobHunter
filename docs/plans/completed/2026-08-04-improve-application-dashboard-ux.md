# Improve Application Dashboard UX

## Overview

- Deliver one cohesive usability release covering all six requested
  improvements: local CV selection and viewing, date-only summary fields,
  concise dashboard notes, a compact create form, expandable job descriptions,
  and dashboard search.
- Keep CVs in their original location. JobHunter will store a validated
  reference to the selected local file rather than upload or copy it.
- Use a server-side picker because browser file inputs intentionally do not
  disclose the source file's absolute path to web applications.
- Make the dashboard faster to scan without changing the underlying application
  or immutable stage-history model.

## Context (from discovery)

- JobHunter is a local FastAPI application using SQLite, Jinja templates, and
  HTMX. Application persistence and validation are in `app/repository.py`; HTTP
  routes are in `app/main.py`.
- `cv_path` is already persisted as a validated `file:` URI. It is presently
  entered manually and rendered as a direct link, which browsers may block from
  an `http://localhost` page.
- The dashboard is a single table in `app/templates/applications/index.html`.
  It currently has no notes column, no search control, and renders summary
  timestamps without formatting.
- Create and edit forms duplicate a vertical list of fields. HTMX redirects
  after successful submissions, and validation failures swap form fragments.
- No browser E2E framework is present. Existing async route tests use `httpx2`;
  repository tests use temporary SQLite databases.

## Development Approach

- **Testing approach:** Regular — implement one focused unit, add success and
  error-path tests, and run the required checks before starting the next task.
- Retain the existing database column and canonical `file:` URI representation
  for CV locations; no schema migration is necessary.
- Add an explicitly bounded local file-picker root. Use
  `JOBHUNTER_CV_ROOT` when set and the current user's home directory otherwise.
  Resolve paths and reject traversal, symlinks escaping the root, directories,
  missing files, and unsupported extensions.
- Allow only `.pdf`, `.doc`, and `.docx` CVs. Preview PDFs through an
  application-owned endpoint and an HTML dialog. Offer Word files as an
  application-served download/open action, because browsers cannot reliably
  embed DOC or DOCX files without a converter or external service.
- Use 300-character previews for dashboard notes and job descriptions. This is
  within the requested 200–500-character range and is long enough to be useful
  while keeping the table compact.
- Search case-insensitively across role, company, and notes using the existing
  SQLite repository boundary. Keep empty searches equivalent to the current
  unfiltered list and preserve the query in the rendered search control.
- Format only the Submitted and Last updated summary fields as ISO dates. Keep
  complete timestamp values in persistence and in stage history, where the time
  remains useful for sequencing.
- Preserve non-HTMX full-page fallbacks and current HTMX create/edit behaviour.
  Update this plan if implementation discovers a material scope change.

## Testing Strategy

- Add repository tests for CV-root containment, accepted PDF/Word files,
  rejected extensions and invalid paths, plus empty and cross-field searches.
- Add route tests for picker navigation, validation failures, PDF preview,
  Word download handling, and not-found/forbidden CV files.
- Add route-rendering tests for the compact create dialog, date-only summary
  fields, truncated dashboard/detail content, expand controls, and query
  retention/search results.
- Run `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`,
  and `uv run mypy app` after every task and for final verification.
- Manually verify the dialog interactions, PDF overlay, a DOCX download/open
  action, narrow-screen layout, and case-insensitive search in a browser.

## Progress Tracking

- Mark completed items with `[x]` as work is finished.
- Add newly discovered work with a `➕` prefix and blockers with a `⚠️` prefix.
- Keep this plan synchronized with implementation and verification results.

## Solution Overview

The dashboard will stay table-based, augmented with a search bar and concise
preview columns. The New application action will open a compact modal form via
HTMX, while its normal page remains a non-JavaScript fallback. Both create and
edit forms will use a reusable CV-picker control that selects an existing local
PDF, DOC, or DOCX under an approved root.

The application will serve selected CV files through validated endpoints. PDF
responses will render in an overlay for recruiter calls; Word documents will
use an explicit download/open action. The preview endpoint will resolve and
revalidate every stored path so legacy or manually altered database values
cannot expose files outside the permitted root.

## Technical Details

- Create a typed CV-file service that derives the permitted root, converts
  safe canonical file URIs to resolved `Path` instances, checks extension and
  regular-file status, and enumerates safe picker-directory contents.
- Replace free-form CV entry with an HTMX-loaded picker dialog and a hidden
  `cv_path` form value. Selecting a file writes its canonical file URI and a
  readable filename/path summary into the parent form. Keep the existing value
  visible when editing or after validation errors.
- Add application routes for picker fragments and CV delivery. Delivery sets a
  safe content type and `inline` disposition for PDFs; Word responses use an
  attachment disposition. Missing, out-of-root, malformed, and unsupported
  files must return an appropriate non-leaking error.
- Add a Jinja date filter or typed presentation helper for `YYYY-MM-DD` output
  and a typed 300-character preview helper that appends an ellipsis only when
  text is longer than the limit.
- Add a `q` query parameter to the dashboard route and optional repository
  filter. Match role, company, or notes without interpolating user input into
  SQL.
- Use native HTML `dialog` elements plus a small local static script for
  opening/closing HTMX-populated dialogs and assigning the chosen CV value.
  Use native `details` for full-job-description expansion to avoid unnecessary
  client-side state.

## What Goes Where

- **Implementation Steps** are repository-local code, template, styling,
  documentation, and test changes.
- **Post-Completion** lists manual browser checks required for native dialog
  and document-handler behaviour.

## Implementation Steps

### Task 1: Add safe local CV-file selection and delivery services

**Files:**
- Create: `app/cv_files.py`
- Modify: `app/repository.py`
- Modify: `app/main.py`
- Modify: `tests/test_repository.py`
- Modify: `tests/test_routes.py`

- [ ] Implement typed CV-root resolution, canonical URI parsing, containment,
  extension, and regular-file validation in `app/cv_files.py`.
- [ ] Update CV-location validation in `app/repository.py` to use the shared
  validator while preserving the stored canonical `file:` URI value.
- [ ] Add picker-listing, PDF-preview, and Word download routes in
  `app/main.py`, revalidating stored paths immediately before delivery.
- [ ] Write repository tests for allowed PDF/DOC/DOCX paths and empty values.
- [ ] Write repository and route tests for traversal, out-of-root symlinks,
  directories, missing files, unsupported types, and unsafe legacy values.
- [ ] Run `uv run pytest`, `uv run ruff check .`,
  `uv run ruff format --check .`, and `uv run mypy app` — all must pass before
  Task 2.

### Task 2: Replace manual CV entry with an HTMX picker and viewer

**Files:**
- Modify: `app/templates/base.html`
- Modify: `app/templates/applications/_application_form.html`
- Modify: `app/templates/applications/_application_edit_form.html`
- Create: `app/templates/applications/_cv_picker.html`
- Modify: `app/templates/applications/detail.html`
- Create: `app/static/app.js`
- Modify: `app/static/app.css`
- Modify: `tests/test_routes.py`

- [ ] Add accessible picker and viewer dialogs to the shared page shell and
  load the local dialog script without changing the HTMX CDN integration.
- [ ] Group create/edit form fields into compact logical sections and replace
  the editable CV text input with the picker control and selected-file summary.
- [ ] Render a PDF “Preview CV” action that loads an inline overlay; render a
  Word “Download CV” action instead of promising unsupported inline preview.
- [ ] Implement focus-safe dialog opening, closing, and picker selection that
  updates the parent form's hidden canonical `cv_path` value.
- [ ] Write route/template tests for picker fragments, selected-file retention,
  PDF overlay markup, and Word download markup.
- [ ] Write tests for empty CVs, invalid picker requests, and validation-error
  re-renders without a selected-file loss.
- [ ] Run `uv run pytest`, `uv run ruff check .`,
  `uv run ruff format --check .`, and `uv run mypy app` — all must pass before
  Task 3.

### Task 3: Open the compact New application form as a dashboard overlay

**Files:**
- Modify: `app/templates/applications/index.html`
- Modify: `app/templates/applications/form.html`
- Modify: `app/templates/applications/_application_form.html`
- Modify: `app/static/app.js`
- Modify: `app/static/app.css`
- Modify: `tests/test_routes.py`

- [ ] Change the dashboard New application action to load the existing create
  form in a dialog through HTMX, while retaining `/applications/new` as a
  direct full-page fallback.
- [ ] Apply a responsive compact grid to role/company, payment/job URL, CV,
  flags, notes, and job-description groups without reducing input usability.
- [ ] Ensure successful HTMX create redirects to detail as today and cancel or
  close actions do not submit unintended changes.
- [ ] Write route/template tests for the dashboard trigger, dialog form
  fragment, direct fallback page, and successful HTMX redirect.
- [ ] Write tests for failed creation in the overlay, retained input values,
  and correct error-fragment targeting.
- [ ] Run `uv run pytest`, `uv run ruff check .`,
  `uv run ruff format --check .`, and `uv run mypy app` — all must pass before
  Task 4.

### Task 4: Add date-only summary formatting and concise text previews

**Files:**
- Modify: `app/main.py`
- Modify: `app/templates/applications/index.html`
- Modify: `app/templates/applications/_application_row.html`
- Modify: `app/templates/applications/detail.html`
- Modify: `app/templates/applications/_stage_history.html`
- Modify: `app/static/app.css`
- Modify: `tests/test_routes.py`

- [ ] Add typed presentation helpers for ISO date-only values and 300-character
  text previews, including clear behaviour for `None`, short text, and text
  exactly at the limit.
- [ ] Render Submitted and Last updated as dates in dashboard and summary
  views; retain complete timestamps in stage-history controls and records.
- [ ] Add a 300-character Notes column to the dashboard and render the full
  job description collapsed to 300 characters with a native expand control.
- [ ] Style clipped content and expand controls for readable table and detail
  layouts without breaking whitespace in full descriptions.
- [ ] Write rendering tests for date-only output and under-limit/exact-limit/
  over-limit notes and job-description content.
- [ ] Write rendering tests for missing values, multiline descriptions, and
  the expanded full-content path.
- [ ] Run `uv run pytest`, `uv run ruff check .`,
  `uv run ruff format --check .`, and `uv run mypy app` — all must pass before
  Task 5.

### Task 5: Add cross-field application search to the dashboard

**Files:**
- Modify: `app/main.py`
- Modify: `app/repository.py`
- Modify: `app/templates/applications/index.html`
- Modify: `tests/test_repository.py`
- Modify: `tests/test_routes.py`

- [ ] Add a typed optional search term to `Repository.list_applications` and
  parameterised, case-insensitive matching on role, company, and notes.
- [ ] Accept `q` in the dashboard route, trim blank input, and keep the active
  query in the search field and empty-state message.
- [ ] Add an accessible dashboard search form with a clear action and preserve
  current list ordering for filtered and unfiltered results.
- [ ] Write repository tests for role, company, and notes matches plus blank,
  mixed-case, and no-result searches.
- [ ] Write route/template tests for query retention, HTML escaping, the clear
  action, and filtered empty states.
- [ ] Run `uv run pytest`, `uv run ruff check .`,
  `uv run ruff format --check .`, and `uv run mypy app` — all must pass before
  Task 6.

### Task 6: Verify the complete usability release

**Files:**
- Modify: `tests/test_routes.py` (only if verification finds a gap)
- Modify: `tests/test_repository.py` (only if verification finds a gap)
- Modify: `app/static/app.css` (only if verification finds a layout defect)

- [ ] Verify every Overview requirement works together: picker, PDF preview,
  Word action, compact dialog, date summaries, previews, expansion, and search.
- [ ] Verify security boundaries for stored or legacy CV locations and confirm
  no query interpolation or unsafe link rendering is introduced.
- [ ] Add missing success-path regression tests discovered by integrated
  verification.
- [ ] Add missing error and edge-path regression tests discovered by integrated
  verification.
- [ ] Run the full suite: `uv run pytest`, `uv run ruff check .`,
  `uv run ruff format --check .`, and `uv run mypy app`; fix all failures.

### Task 7: Update documentation and close out the plan

**Files:**
- Modify: `README.md`
- Modify: `docs/plans/2026-08-04-improve-application-dashboard-ux.md`

- [ ] Document `JOBHUNTER_CV_ROOT`, supported CV types, the original-file
  reference model, and the PDF-versus-Word viewer behaviour.
- [ ] Document dashboard search fields, 300-character previews, and the
  date-only summary convention.
- [ ] Record completed verification commands and any implementation deviations
  or browser compatibility observations in this plan.
- [ ] Re-run `uv run pytest`, `uv run ruff check .`,
  `uv run ruff format --check .`, and `uv run mypy app` after final updates.
- [ ] Move this plan to `docs/plans/completed/` after all implementation and
  verification checkboxes are complete.

## Completion Record

- [x] Task 1: Added root-bounded PDF/DOC/DOCX validation, picker listing, and
  secure CV delivery routes with success and error-path tests.
- [x] Task 2: Added reusable picker and preview dialogs, compact grouped forms,
  and PDF/Word-specific CV actions with route/template coverage.
- [x] Task 3: Added the HTMX New application dashboard overlay while retaining
  the direct-page fallback and form validation behaviour.
- [x] Task 4: Added date-only summary fields, 300-character notes and job
  description previews, and native description expansion.
- [x] Task 5: Added parameterised case-insensitive role/company/notes search
  with retained queries and empty-state handling.
- [x] Task 6: Verified the release boundary and complete automated regression
  coverage, including hostile CV paths and filtered searches.
- [x] Task 7: Updated the README and ran `uv run pytest`,
  `uv run ruff check .`, `uv run ruff format --check .`, and `uv run mypy app`.
  All checks passed on 2026-08-04.

## Post-Completion

**Manual verification:**

- Set `JOBHUNTER_CV_ROOT` to a directory containing a PDF, DOC, DOCX, a bad
  extension, and an out-of-root symlink; confirm the picker exposes only safe
  supported files.
- During a recruiter-call simulation, select a PDF, create an application from
  the dashboard overlay, open its preview, keep the dialog open while taking
  notes, and verify the original file remains untouched.
- Verify a DOC/DOCX uses the download/open action and opens with the operating
  system's associated local application after download.
- Verify the compact form and dashboard content at desktop and narrow widths.

**External system updates:**

- None expected. This remains a local-only application; no data migration or
  third-party document-conversion service is planned.
