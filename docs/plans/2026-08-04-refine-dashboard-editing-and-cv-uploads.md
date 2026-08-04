# Refine Dashboard Editing and CV Uploads

## Overview

- Remove the dead-end from the New application overlay by providing explicit
  Close and Cancel actions that never submit the form.
- Make dashboard summary fields consistently readable: keep dates unbroken,
  normalise the Search and Clear controls, and open dashboard CV links in the
  existing preview overlay when the document is a PDF.
- Replace the server-rendered CV directory browser with the browser's native
  file chooser (Finder on macOS) and store the uploaded document privately at
  `private/cv/artefacts/<company>/<YYYY-MM-DD> <role>/`.
- Add dashboard overlays for focused updates to the current stage/stage note
  and application notes, avoiding a trip to the full edit page.
- Apply a final dashboard visual-consistency pass: use one shared button
  typography, make all application-table text consistent, keep table headers
  centred and on one line where the viewport permits, open job-post links in a
  new tab, and compact the stage-history display to match its update form.

## Context (from discovery)

- JobHunter is a local FastAPI application with Jinja templates, HTMX, a small
  static JavaScript file, and SQLite persistence. Existing route tests use
  `httpx2`; no browser E2E suite is configured.
- `app/templates/base.html` already provides native dialogs for creation and
  PDF preview. `app/static/app.js` centralises dialog open/close behaviour.
- The dashboard row currently links every CV to the detail page and renders
  stage, stage note, notes, and dates as static text. `Repository.add_stage`
  creates the current-stage history record.
- CVs are currently selected by an application-managed directory picker and
  stored as validated `file:` URIs. The requested system file manager requires
  a multipart native file input and private server-side storage instead.
- The data model has a required Company field and only a recruiter boolean,
  not a recruiter-name field. Per the confirmed decision, Company supplies the
  artefact-directory segment for all applications.

## Development Approach

- **Testing approach:** Regular — implement one focused unit, add focused
  success and error tests, and run all required checks before the next task.
- Use `<input type="file">` with `multipart/form-data`; this invokes the
  operating system's chooser while keeping browser security boundaries intact.
  Do not attempt to recover or store the client machine's original path.
- Store only validated PDF, DOC, and DOCX uploads beneath the application
  private directory. Sanitise Company and Role for safe portable path segments,
  add a deterministic collision suffix, stream into a temporary file, and
  atomically move it into the final directory only after a successful upload.
  Store the resulting canonical local `file:` URI in the existing `cv_path`
  column; no database migration is needed.
- Preserve an existing CV when an edit form submits without a replacement
  file. Do not automatically delete a replaced artefact in this change; that
  avoids accidental data loss and can be addressed by a separate retention
  workflow.
- A stage-overlay submit with a different stage appends a new immutable history
  item at the current time. A submit with the unchanged stage updates only the
  current stage note, preventing duplicate same-stage records while allowing a
  correction. Notes use a dedicated narrow repository operation.
- Use HTMX fragments to replace the relevant dashboard row after a successful
  stage or notes update, and close the relevant overlay through a named client
  event. Keep ordinary full-page POST/redirect fallbacks for direct navigation.
- Update this plan if implementation discovers material scope changes.

## Testing Strategy

- Add unit tests for safe upload-path derivation, supported and rejected file
  types, collision handling, and storage-root containment.
- Extend route tests for multipart creation/editing, validation failures with
  no persisted upload, replacement/preservation semantics, and private PDF
  preview/download delivery.
- Add route/template tests for cancel/close controls, non-wrapping dates,
  equal-height search controls, dashboard CV preview actions, and the two
  dashboard editor overlays.
- Add route/template tests for the job-post external-link attributes and the
  shared classes used by dashboard table cells, headers, and stage history.
  Add focused CSS assertions or snapshots, consistent with existing tests, for
  the shared button typography and compact stage-history presentation.
- Exercise repository tests for current-stage replacement versus note-only
  edits, ordering, invalid stages, missing applications, and notes clearing.
- After each task run `uv run pytest`, `uv run ruff check .`,
  `uv run ruff format --check .`, and `uv run mypy app`; all must pass before
  continuing. Browser E2E tests are not currently available.

## Progress Tracking

- Mark completed items with `[x]` immediately when done.
- Add newly discovered tasks with a `➕` prefix and blockers with a `⚠️` prefix.
- Keep this plan synchronized with implementation and verification results.

## Solution Overview

The shared shell will gain reusable editor dialogs alongside the existing
application and preview dialogs. The create form will render a regular Cancel
link for full-page navigation and a Close/Cancel dialog action when rendered
through HTMX. The form's old picker button will become a native file control,
so the browser asks the operating system to choose the CV.

On upload, the server derives a private artefact directory from the validated
Company, current date, and validated Role. It saves a copy there and retains
the existing canonical URI model, so the protected preview and download routes
continue to own access to the file. The dashboard's PDF CV action will use the
existing iframe preview dialog; Word documents retain their download action,
because they cannot be reliably rendered in that iframe without conversion.

Each editable dashboard cell opens a focused dialog. Stage changes retain their
history semantics and immediately refresh the row. Notes changes update only
the notes field and likewise refresh the row, while a cancelled dialog leaves
the dashboard untouched.

## Technical Details

- Add an upload-storage helper, rooted at `ROOT / "private" / "cv" /
  "artefacts"`, that receives an `UploadFile`, checks extension and content
  metadata as appropriate, imposes a documented size limit, writes safely, and
  returns a canonical file URI. Its resolved paths must never escape the
  private root.
- Change create and edit form routes to accept `UploadFile | None` and use an
  `enctype="multipart/form-data"` form. Keep the stored current `cv_path` in a
  hidden value for edit preservation, but never trust browser-supplied file
  paths.
- Remove the `/cv-picker` user flow and its dialog/template/script handlers.
  Reuse or simplify `app/cv_files.py` so its stored-path validation and serving
  checks use the managed artefact root.
- Render dates as semantic `<time>` elements with a dedicated no-wrap class and
  an adequate date column width. Normalise `.search-form` action controls to a
  shared box sizing, height, and padding so Clear cannot exceed Search.
- Add GET fragments and POST actions for stage and notes editors. Successful
  HTMX responses return the rendered `_application_row.html` and trigger an
  event that closes the matching dialog. Server errors return the editor
  fragment with an accessible validation message and preserved input.
- Render a dashboard CV action with `data-preview-url` for PDFs and preserve a
  protected download link for supported Word files. Missing or invalid stored
  CVs render as unavailable rather than exposing a raw file URI.
- Define dashboard typography at the shared table/button selectors rather than
  per column: all buttons inherit one explicit family and size, while header
  and body cells share the same explicit family and size. Centre header text,
  prevent header wrapping at normal desktop widths, and retain horizontal
  scrolling for constrained viewports.
- Render external job-post URLs as safe, clearly labelled links with
  `target="_blank"` and `rel="noopener noreferrer"`. Reduce stage-history
  padding, gaps, and action-control sizing so it visually matches the compact
  stage-update form without making the history controls harder to operate.
- Apply those external-link attributes consistently to both dashboard and
  detail-page job-post renderings; a route test must prevent the two templates
  from drifting apart again.

## What Goes Where

- **Implementation Steps** are repository-local application, template, CSS,
  JavaScript, and test changes.
- **Post-Completion** lists manual browser checks, especially those involving
  the operating-system file chooser and native dialogs.

## Implementation Steps

### Task 1: Replace the CV browser with private native-upload storage

**Files:**
- Create: `app/cv_uploads.py`
- Modify: `app/cv_files.py`
- Modify: `app/main.py`
- Modify: `app/repository.py`
- Modify: `tests/test_routes.py`
- Modify: `tests/test_repository.py`

- [x] Implement typed, path-safe private artefact storage rooted at
  `private/cv/artefacts`, deriving sanitised Company and dated Role folders and
  collision-safe filenames.
- [x] Stream valid PDF, DOC, and DOCX uploads via a temporary file and atomic
  move; reject empty, unsupported, oversized, malformed, and escaping inputs
  without retaining an artefact.
- [x] Adapt CV URI validation and the existing preview/download resolver to
  accept only managed artefacts while handling safe legacy values deliberately.
- [x] Change create and full-edit routes to handle multipart uploads, preserve
  the current CV when no replacement is supplied, and clean up a new artefact
  if persistence fails.
- [x] Write unit and repository tests for path derivation, validation,
  collisions, containment, and CV preservation/replacement.
- [x] Write route tests for successful multipart create/edit, rejected uploads,
  storage/persistence failure cleanup, and protected document delivery.
- [x] Run `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`,
  and `uv run mypy app` — all must pass before Task 2.

### Task 2: Make application-form exit and upload controls explicit

**Files:**
- Modify: `app/templates/applications/_application_form.html`
- Modify: `app/templates/applications/_application_edit_form.html`
- Modify: `app/templates/applications/form.html`
- Modify: `app/templates/base.html`
- Modify: `app/static/app.js`
- Modify: `app/static/app.css`
- Modify: `tests/test_routes.py`

- [x] Replace the custom CV picker control with an accessible native CV input,
  accepted extensions, selected-file guidance, and multipart form encoding in
  create and edit forms.
- [x] Add a Cancel link to the full-page form and non-submitting Close and
  Cancel controls to application-dialog fragments; each must return the user
  to the dashboard without creating or changing an application.
- [x] Remove the obsolete CV-picker dialog, HTMX request, and template use;
  retain the shared PDF preview dialog.
- [x] Update dialog JavaScript to close the form reliably after Cancel, Close,
  Escape, and successful HTMX completion without affecting the full-page
  fallback.
- [x] Write route/template tests for native upload markup, direct-page cancel,
  overlay close/cancel controls, and form validation re-renders.
- [x] Write edge-case tests that prove no CV is selected by default and that an
  existing edit CV remains visible when validation fails.
- [x] Run `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`,
  and `uv run mypy app` — all must pass before Task 3.

### Task 3: Correct dashboard dates, CV actions, and search-control sizing

**Files:**
- Modify: `app/templates/applications/index.html`
- Modify: `app/templates/applications/_application_row.html`
- Modify: `app/templates/base.html`
- Modify: `app/static/app.css`
- Modify: `app/static/app.js`
- Modify: `tests/test_routes.py`

- [x] Render Submitted and Last updated in semantic date cells with no-wrap
  behaviour and minimum sizing so `YYYY-MM-DD` never splits across lines.
- [x] Give Search and Clear a shared compact action-control class so their box
  height, padding, and alignment match; preserve the existing clear URL and
  no-JavaScript search behaviour.
- [x] Replace the dashboard's CV detail-page link with a PDF preview trigger
  targeting the existing overlay, while retaining a safe download action for
  supported non-PDF documents and an unavailable state for absent CVs.
- [x] Ensure preview overlay controls clear the iframe source on close and
  remain keyboard-accessible.
- [x] Write route/template tests for intact date output, search/clear control
  classes, PDF preview markup, Word download markup, and unavailable CVs.
- [x] Write edge-case tests for dates with missing values and CVs whose stored
  location is invalid, missing, or no longer permitted.
- [x] Run `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`,
  and `uv run mypy app` — all must pass before Task 4.

### Task 4: Add a focused dashboard current-stage editor

**Files:**
- Modify: `app/main.py`
- Modify: `app/repository.py`
- Modify: `app/templates/base.html`
- Create: `app/templates/applications/_stage_editor.html`
- Modify: `app/templates/applications/_application_row.html`
- Modify: `app/static/app.js`
- Modify: `app/static/app.css`
- Modify: `tests/test_routes.py`
- Modify: `tests/test_repository.py`

- [x] Add a dashboard stage-edit GET fragment with the current stage selected,
  all `STAGES` options, a free-text stage-note field, and accessible close and
  cancel actions.
- [x] Add a narrowly scoped POST action: a changed stage closes the current
  history record and adds a new one with the current timestamp; an unchanged
  stage updates the current record's note only.
- [x] Return the refreshed dashboard row after a successful HTMX submit and
  trigger closure of only the stage editor; preserve a redirect fallback for
  non-HTMX requests.
- [x] Make Current stage and Stage note cells expose clear edit triggers
  without nesting interactive controls inside an invalid table structure.
- [x] Write repository tests for changed-stage history sequencing, note-only
  current-record edits, invalid stage values, and missing applications.
- [x] Write route/template tests for prefilled editor data, success-row swaps,
  validation errors, cancellation, and standard non-HTMX redirects.
- [x] Run `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`,
  and `uv run mypy app` — all must pass before Task 5.

### Task 5: Add a focused dashboard notes editor

**Files:**
- Modify: `app/main.py`
- Modify: `app/repository.py`
- Modify: `app/templates/base.html`
- Create: `app/templates/applications/_notes_editor.html`
- Modify: `app/templates/applications/_application_row.html`
- Modify: `app/static/app.js`
- Modify: `app/static/app.css`
- Modify: `tests/test_routes.py`
- Modify: `tests/test_repository.py`

- [x] Add a notes-only repository update method that supports replacing or
  clearing notes without modifying unrelated application fields or stage data.
- [x] Add a GET editor fragment and POST update endpoint with a prefilled
  textarea, validation feedback, and a full-page fallback.
- [x] Add a dashboard Notes edit trigger that opens the overlay and swaps the
  refreshed row on success, including an updated preview or the empty marker.
- [x] Reuse the common dialog close-event pattern while keeping the stage and
  notes dialogs independently addressable.
- [x] Write repository tests for replacing, clearing, and rejecting updates to
  missing applications.
- [x] Write route/template tests for prefilled multiline text, HTML-escaped
  content, empty-note saves, error handling, and successful row refreshes.
- [x] Run `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`,
  and `uv run mypy app` — all must pass before Task 6.

### Task 6: Verify the dashboard workflow end to end

**Files:**
- Modify: `tests/test_routes.py` (only if verification finds a gap)
- Modify: `tests/test_repository.py` (only if verification finds a gap)
- Modify: `app/static/app.css` (only if verification finds a layout defect)

- [x] Verify every requested behaviour works together: escaping the New form,
  native upload, artifact directory placement, date layout, search sizing,
  stage/note overlays, notes overlay, and dashboard PDF preview.
- [x] Verify error paths leave no accidental database changes or upload files,
  preserve user-entered form data where feasible, and do not disclose private
  file paths.
- [x] Run the full test suite with `uv run pytest`.
- [x] Run static checks with `uv run ruff check .`, `uv run ruff format --check .`,
  and `uv run mypy app`.
- [ ] Manually verify native browser file selection, Finder invocation on macOS,
  dialog focus/keyboard escape, responsive dashboard dates, and PDF viewing.

### Task 7: Normalize dashboard typography, links, and stage-history density

**Files:**
- Modify: `app/templates/applications/index.html`
- Modify: `app/templates/applications/_application_row.html`
- Modify: `app/templates/applications/_stage_history.html`
- Modify: `app/templates/applications/_stage_editor.html`
- Modify: `app/static/app.css`
- Modify: `tests/test_routes.py`

- [x] Establish shared explicit font-family and font-size rules for every
  button and button-style link, including search, dialog, table-edit, and
  stage-history controls.
- [x] Apply one explicit typeface and size to all dashboard application-table
  body cells (Role, Company, Payment, links, status fields, and dates) while
  preserving semantic emphasis such as headings and error messages.
- [x] Centre and align all dashboard table headers; keep each label on one line
  at normal dashboard widths with `white-space: nowrap` and preserve the
  existing horizontal-scroll fallback on narrow viewports.
- [x] Update dashboard job-post URLs to open in a new tab using
  `target="_blank" rel="noopener noreferrer"`, while retaining accessible link
  text and safe handling of absent URLs.
- [x] Make the application-stage history display compact by matching the
  stage-editor form's control sizing, spacing, and visual density without
  changing stage data or editor behavior.
- [x] Write route/template tests for table typography hooks, centred no-wrap
  headers, dashboard job-post link attributes, and absent-URL behaviour.
- [x] Write presentation-focused tests or assertions for the common button
  styling and compact stage-history classes, including initial and multi-entry
  histories (an application always has an initial stage-history record).
- [x] Run `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`,
  and `uv run mypy app` — all must pass before Task 8.

### Task 8: ➕ Open all application job-post URLs in new tabs

**Files:**
- Modify: `app/templates/applications/detail.html`
- Modify: `tests/test_routes.py`

- [x] Add `target="_blank"` and `rel="noopener noreferrer"` to the detail-page
  job-post URL, matching the dashboard's safe external-link behavior.
- [x] Preserve the current absent-URL marker and URL escaping in the detail
  template; do not alter CV-download or internal application navigation links.
- [x] Extend route/template coverage to assert that the same valid job-post URL
  has the safe new-tab attributes in both dashboard and detail responses.
- [x] Add an absent-URL detail-page assertion that proves no external link is
  rendered when an application has no job URL.
- [x] Run `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`,
  and `uv run mypy app` — all must pass before Task 9.

### Task 9: Update documentation and close the plan

**Files:**
- Modify: `README.md` (if user-facing CV storage or supported formats need
  documenting)
- Modify: `AGENTS.md` (only if a durable engineering convention changes)
- Move: `docs/plans/2026-08-04-refine-dashboard-editing-and-cv-uploads.md` to
  `docs/plans/completed/`

- [x] Document private CV artefact location, supported upload formats, and the
  fact that files are copied into JobHunter-managed storage.
- [x] Document any durable upload-validation or dashboard-fragment conventions
  discovered during implementation.
- [ ] Confirm every acceptance criterion and verification check above is
  complete before moving the plan to `docs/plans/completed/`.

## Post-Completion

**Manual verification**

- On macOS, choose a PDF through Finder in both create and edit forms and
  confirm that it is copied under the expected Company/date/Role path.
- Cancel a blank New application dialog, submit invalid data, and use Escape
  to ensure no trapped or accidental-submit state remains.
- Test a narrow dashboard viewport to confirm dates stay whole, buttons align,
  table headers remain single-line where space permits, and editable cells
  remain operable.
- Verify every button uses the same typeface and size, job-post links open in
  a new tab, table body text is visually consistent, and stage history is as
  compact as the stage-update form.
- Preview a PDF from the dashboard, download a DOC/DOCX, and verify a missing
  file fails without revealing an internal path.
