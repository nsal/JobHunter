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
- Make the main dashboard application table use the available viewport width
  within reasonable page margins rather than a narrow central frame. Render
  every application as one 11-column desktop row, merge Stage and Stage note
  into one Stage cell, clamp every cell to two lines, rename the Post header to
  URL, make the CV preview control smaller, and use one consistent typeface
  and size for all table text.
- Rename the user-facing Recruiter flag to Agency without changing the stored
  `is_recruiter` field or existing application data. Refine the New application
  upload control by reducing its guidance/status text and removing its
  redundant Close action while retaining Cancel and the native file chooser.
- Correct the responsive table and file-picker accessibility regressions: full
  dates must remain visible at narrow widths, and keyboard users must receive a
  visible, operable file-selection focus target.
- Prevent accidental and direct-request insertion of a second Submitted history
  record by requiring an explicit non-placeholder New stage selection and
  rejecting Submitted through the stage-addition domain operation.

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
- The latest dashboard change correctly widens only the applications page, but
  regressed its desktop table into six slash-paired headers and two rows per
  application. The corrective layout must use the agreed 11-column order and
  retain two-line truncation rather than expanding rows for long values.

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
- On the main dashboard at desktop widths, render one replaceable `<tr>` per
  application in one shared `<tbody>`. Use the agreed 11-column order, merge
  Stage and its note into one editable cell, constrain every desktop value to
  two lines with an ellipsis, and retain the labelled stacked presentation for
  narrow screens.
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
- Add route/template and stylesheet assertions for the one-row, 11-column
  dashboard, full-width page layout, two-line truncation, merged Stage cell,
  URL header, smaller CV preview control, Agency labels, the New application
  file-status text, and the absence of the New-form Close action. No browser
  E2E suite is configured, so manually verify desktop and narrow-screen table
  behavior after implementation.
- Add dashboard regression tests with long Role, Company, Stage note, and
  Notes values, asserting they render safely, expose full values through
  existing titles or editors, and cannot expand a desktop row beyond two lines.
- Add repository and route tests for placeholder-stage rejection and a direct
  attempt to append Submitted after application creation. Add responsive CSS
  assertions for full date visibility and a keyboard-focused upload-control
  test or browser-level check when an E2E harness becomes available.
- Exercise repository tests for current-stage replacement versus note-only
  edits, ordering, invalid stages, missing applications, and notes clearing.
- After each task run `uv run pytest`, `uv run ruff check .`,
  `uv run ruff format --check .`, and `uv run mypy app`; all must pass before
  continuing. Browser E2E tests are not currently available.

## Progress Tracking

- Mark completed items with `[x]` immediately when done.
- Add newly discovered tasks with a `➕` prefix and blockers with a `⚠️` prefix.
- Keep this plan synchronized with implementation and verification results.
- ⚠️ Code review found that Task 15's two-row/six-column redesign conflicts
  with the required desktop table. Task 17 replaces it with the agreed
  one-row, 11-column, two-line-clamped layout while preserving its full-width
  dashboard improvement.
- ⚠️ Follow-up review found that long Notes content can contribute intrinsic
  button width and expand the desktop table. Task 18 constrains the existing
  cell content to its assigned column without changing editor behavior.
- ⚠️ The first Task 18 constraint did not cap the button's block size in the
  browser. The completed corrective implementation uses an inner Notes preview
  plus explicit two-line button and preview caps on desktop.

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
- Scope dashboard layout rules to the applications dashboard: retain the
  full-width dashboard gutter, restore one shared table body with one row per
  application, and show the agreed 11 columns in their documented order. Merge
  Stage and its note into a single editor-triggering cell, clamp desktop cells
  to exactly two lines with ellipses, and retain the existing narrow-screen
  labelled layout. Do not change stored data or remove any application field.
- Set one shared family and size for all application-table headers, cells,
  links, and editor triggers. Give only the dashboard PDF Preview CV action a
  smaller compact-control class without changing its accessible name or the
  DOC/DOCX download action.
- Render dashboard Notes directly from the stored value rather than through
  `preview_text`; this dashboard-only change preserves every Notes character
  while retaining the helper for any other future compact-preview use.
- Keep the native file input as the source of the upload. Wrap it in an
  accessible label-triggered control with a separate, JavaScript-updated
  status element so both the initial “No file selected” status and the format
  guidance can be reduced to roughly 60–70% of normal form text; do not
  reintroduce a path picker or expose a client filesystem path.
- Treat “Agency” as a presentation-only name for `is_recruiter`: update create,
  edit, dashboard, and detail labels while preserving request names,
  repository mappings, and database columns for compatibility.
- At the dashboard mobile breakpoint, replace the constrained table-row layout
  with a labelled stacked presentation rather than clipping cells or restoring
  a horizontal scrollbar. Keep full `YYYY-MM-DD` dates, all edit controls, and
  all application fields reachable in that presentation.
- Make the native file input fill the visible Choose CV control with transparent
  styling rather than visually hiding the focus target. Use a focus-within
  outline on the control, preserve label association/status updates, and test
  keyboard activation manually until browser E2E coverage exists.
- In `Repository.add_stage`, reject an empty/placeholder stage and Submitted,
  because creation already owns the immutable initial Submitted record. The
  stage-history select will begin on a disabled placeholder and omit Submitted;
  route-level validation must return the existing error fragment without
  changing history for forged values.

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
- Modify: `tests/test_dashboard_cv_actions.py`

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
- [x] Verify the dashboard workflow through the available route/static checks;
  native browser file selection, Finder invocation on macOS, dialog focus,
  responsive visual layout, and PDF viewing remain documented as manual
  Post-Completion checks below.

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

### Task 9: ➕ Fit the applications table within dashboard margins

**Files:**
- Modify: `app/templates/applications/index.html`
- Modify: `app/templates/applications/_application_row.html`
- Modify: `app/static/app.css`
- Modify: `tests/test_routes.py`

- [x] Replace verbose dashboard headers with concise one-line labels and
  accessible explanatory titles, keeping all twelve application columns.
- [x] Define an applications-table-specific fixed column layout that removes
  its `60rem` minimum width and fits the normal dashboard content area without
  a horizontal scrollbar or over-wide page margins.
- [x] Reduce dashboard table text and cell padding to a readable compact scale;
  apply no-wrap and ellipsis handling to long body values so one application
  remains one visual row while preserving the full values in accessible titles
  or existing detail navigation.
- [x] Retain responsive usability: table controls must remain operable, date
  values must stay intact, and narrow viewports must not cause page-level
  horizontal overflow.
- [x] Write route/template tests for concise accessible headings and row
  truncation hooks, plus stylesheet assertions for the scoped fixed layout and
  compact table metrics.
- [x] Write edge-case tests for long Role, Company, Stage note, and Notes
  values, proving their rendered row remains a single-line table row and their
  full data remains reachable.
- [x] Run `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`,
  and `uv run mypy app` — all must pass before Task 10.

### Task 10: ➕ Rename the Recruiter UI flag to Agency

**Files:**
- Modify: `app/templates/applications/_application_form.html`
- Modify: `app/templates/applications/_application_edit_form.html`
- Modify: `app/templates/applications/index.html`
- Modify: `app/templates/applications/detail.html`
- Modify: `tests/test_routes.py`

- [x] Replace every user-facing Recruiter label and dashboard heading with
  Agency in create, edit, list, and detail renderings.
- [x] Preserve the existing `is_recruiter` checkbox name, request parsing,
  repository mapping, database column, and stored values; no migration or API
  contract change is part of this task.
- [x] Write route/template tests for Agency labels across all application views
  and for preselected Agency checkboxes on existing applications.
- [x] Write compatibility tests proving form submission using `is_recruiter`
  still persists and renders the Agency state correctly.
- [x] Run `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`,
  and `uv run mypy app` — all must pass before Task 11.

### Task 11: ➕ Refine New application upload text and actions

**Files:**
- Modify: `app/templates/applications/_application_form.html`
- Modify: `app/static/app.css`
- Modify: `app/static/app.js`
- Modify: `tests/test_routes.py`

- [x] Remove only the Close button from the New application dialog header;
  retain its Escape behavior and the non-submitting Cancel action in the form
  footer.
- [x] Keep the native multipart file input and operating-system chooser, but
  expose a label-triggered visual control with a separate initial “No file
  selected” status that updates to the selected filename without showing a
  filesystem path.
- [x] Render the `PDF, DOC, or DOCX up to 10 MiB` guidance and file-status text
  at approximately 60–70% of normal form text, with readable contrast and
  sufficient spacing from the file-selection control.
- [x] Keep validation rerenders and selected-file feedback accessible through
  correct label association and a status/live-region pattern; do not change
  upload size/type validation or edit-form behavior.
- [x] Write route/template tests for the removed Close control, retained
  Cancel control, native upload markup, guidance, and initial status element.
- [x] Write client-behavior tests or focused JavaScript assertions for status
  replacement after a file is chosen and clearing/resetting after form rerender.
- [x] Run `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`,
  and `uv run mypy app` — all must pass before Task 12.

### Task 12: ➕ Preserve dashboard dates and controls on narrow screens

**Files:**
- Modify: `app/templates/applications/index.html`
- Modify: `app/templates/applications/_application_row.html`
- Modify: `app/static/app.css`
- Modify: `tests/test_routes.py`

- [x] Add responsive cell labels or equivalent semantic hooks to every
  dashboard field so rows can switch from the compact desktop table to a
  labelled stacked presentation at a documented narrow-screen breakpoint.
- [x] Replace the dashboard-only `overflow-x: hidden` behavior with responsive
  styling that keeps all fields, edit controls, and complete `YYYY-MM-DD`
  Submitted/Updated dates visible rather than clipping them.
- [x] Preserve the compact fixed desktop table, concise headers, ellipsis
  behavior, and stage-history table behavior outside the mobile breakpoint.
- [x] Write route/template tests for the responsive field-label hooks and CSS
  assertions that date cells override truncation in the stacked layout.
- [x] Add focused long-content tests that cover both date fields and dashboard
  editor controls, proving the responsive markup retains their full values.
- [x] Run `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`,
  and `uv run mypy app` — all must pass before Task 13.

### Task 13: ➕ Restore keyboard-operable New application file selection

**Files:**
- Modify: `app/templates/applications/_application_form.html`
- Modify: `app/static/app.css`
- Modify: `app/static/app.js`
- Modify: `tests/test_routes.py`

- [x] Make the actual New-application file input a transparent, focusable
  overlay within the visible Choose CV control, instead of a visually hidden
  standalone input.
- [x] Add a clear `:focus-within` focus indicator to the visible control and
  retain pointer, keyboard, screen-reader, native-chooser, and live-status
  behavior without exposing a local filesystem path.
- [x] Preserve the smaller status/guidance text, initial No file selected
  message, upload validation, and Cancel/Escape dialog behavior.
- [x] Write route/template and stylesheet assertions for the focusable input,
  focus-within control, and accessible status association.
- [x] Add a manual keyboard acceptance scenario (Tab, visible focus, Space or
  Enter, native chooser, selected filename) to Post-Completion; add browser
  E2E coverage if the project later adopts a browser test harness.
- [x] Run `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`,
  and `uv run mypy app` — all must pass before Task 14.

### Task 14: ➕ Prevent duplicate Submitted stage history

**Files:**
- Modify: `app/repository.py`
- Modify: `app/main.py`
- Modify: `app/templates/applications/_stage_history.html`
- Modify: `tests/test_repository.py`
- Modify: `tests/test_routes.py`

- [x] Render New stage with a disabled, selected placeholder and omit Submitted
  from its append-only stage choices; preserve the separate current-stage
  editor's existing behavior.
- [x] Reject blank/placeholder stage values and a Submitted stage in
  `Repository.add_stage`, with a precise validation message and no history
  mutation; retain valid transitions and ordering checks.
- [x] Ensure the route rerenders the history fragment with the placeholder and
  submitted errors for HTMX and direct requests without closing the current
  history record.
- [x] Write repository tests for blank, placeholder, and Submitted rejection,
  including history-count/current-stage invariants and a valid non-Submitted
  transition.
- [x] Write route/template tests for the initially selected disabled placeholder,
  omitted Submitted option, forged Submitted/blank submissions, validation
  feedback, and successful valid additions.
- [x] Run `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`,
  and `uv run mypy app` — all must pass before Task 15.

### Task 15: ➕ Rebuild the main dashboard table as a full-width two-row layout

**Files:**
- Modify: `app/templates/base.html`
- Modify: `app/templates/applications/index.html`
- Modify: `app/templates/applications/_application_row.html`
- Modify: `app/templates/applications/_stage_editor.html`
- Modify: `app/templates/applications/_notes_editor.html`
- Modify: `app/static/app.css`
- Modify: `tests/test_routes.py`

- [x] Add a dashboard-specific main-content hook in the shared layout, then
  expand only the applications index to the viewport's usable width with
  responsive, reasonable side margins; keep other pages at their present
  reading width.
- [x] Replace each single-row 12-column application fragment with one valid,
  independently replaceable application table-body containing exactly two
  desktop rows. Distribute all twelve fields across the two rows, preserving
  clear accessible field labels and the Stage/Notes editor controls.
- [x] Update the dashboard HTMX stage and notes editor targets and successful
  response fragments so each action replaces the complete two-row application
  group, without breaking the non-HTMX redirect fallback or empty/search
  states.
- [x] Remove dashboard ellipsis, clipping, and desktop no-wrap rules; use the
  expanded width, sensible six-field allocation, wrapping, and compact
  spacing so every stored value remains rendered in full. Preserve the
  existing labelled narrow-screen presentation.
- [x] Rename the dashboard’s visible Post header/field label to URL, apply one
  explicit shared font family and size to headers, values, links, and editor
  controls, and introduce a smaller dashboard-only PDF Preview CV button
  without changing its preview behavior or accessible name.
- [x] Write route/template tests for the full-width dashboard hook, the exact
  two-row-per-application markup, complete untruncated long values, URL label,
  compact preview class, and HTMX response containing both replacement rows.
- [x] Write stylesheet assertions and edge-case tests for responsive page
  margins, removal of ellipsis/clipping, wrapping values, uniform dashboard
  typography, absent URLs/CVs, and long content in every field grouping.
- [x] Run `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`,
  and `uv run mypy app` — all must pass before Task 16.

### Task 16: ➕ Render dashboard Notes without truncation

**Files:**
- Modify: `app/templates/applications/_application_row.html`
- Modify: `tests/test_routes.py`

> **Superseded layout assumption:** Task 17 will keep direct Notes rendering
> but visually clamp it to two lines, consistent with the corrected table
> design. This completed task remains as the historical record of removing the
> server-side 300-character preview limit.

- [x] Replace the dashboard-only `preview_text` filter with direct Notes
  rendering, preserving the existing empty marker, escaping, Notes editor
  trigger, and two-row application layout.
- [x] Keep the `preview_text` helper unchanged for non-dashboard or future
  compact-preview consumers; do not alter stored Notes, repository behavior,
  or full-detail rendering.
- [x] Write a route/template regression test for Notes longer than 300
  characters that asserts the entire value is present and no generated
  ellipsis is rendered.
- [x] Write edge-case tests for empty and HTML-sensitive Notes values to prove
  the existing marker and escaping behavior remain correct.
- [x] Run `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`,
  and `uv run mypy app` — all must pass before Task 17.

### Task 17: ➕ Restore the full-width dashboard to one 11-column row

**Files:**
- Modify: `app/templates/applications/index.html`
- Modify: `app/templates/applications/_application_row.html`
- Modify: `app/static/app.css`
- Modify: `tests/test_routes.py`

- [x] Retain `dashboard-main` and its responsive page gutters, but replace the
  slash-paired six-header desktop table with these 11 visible columns: Role,
  Company, Pay, URL, CV, Agency, Remote, Stage, Notes, Submitted, and Updated.
- [x] Restore one shared `#applications` table body and one replaceable `<tr>`
  per application; retain valid HTMX Stage and Notes row replacement and the
  existing non-HTMX redirects and empty/search results.
- [x] Merge the Stage name and Stage note into one Stage cell with the existing
  Stage editor trigger. Keep the stage name and note distinguishable, expose
  their full values through the editor and accessible title, and do not create
  a separate Stage note header or cell.
- [x] Allocate fixed desktop column widths with compact URL/CV/Agency/Remote
  and date columns, then apply a two-line ellipsis clamp to every desktop cell
  so each application remains exactly one visual row of two text lines.
- [x] Preserve the current labelled narrow-screen presentation, complete
  Submitted/Updated dates, safe URL/CV actions, and keyboard-operable editor
  controls; do not change application data, routes, or database schema.
- [x] Write route/template tests for the exact 11 headers and column order,
  one-row fragment/HTMX replacement, merged Stage markup, absent values, and
  full long-value access through titles or editors.
- [x] Write stylesheet and long-content regression tests for full-width
  margins, fixed width allocation, two-line clamping/ellipsis, uniform row
  height, and the retained narrow-screen labelled layout.
- [x] Run `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`,
  and `uv run mypy app` — all must pass before Task 18.

### Task 18: ➕ Constrain long Notes content to its dashboard column

**Files:**
- Modify: `app/static/app.css`
- Modify: `tests/test_routes.py`

- [x] Add `min-width: 0` to dashboard table cells and apply
  `box-sizing: border-box`, `min-width: 0`, and `width: 100%` to the existing
  `.table-cell-content` elements so intrinsic button text cannot expand a
  fixed column.
- [x] Add a dedicated inner Notes preview and cap both it and its editor button
  at two desktop lines; reset those caps at the mobile breakpoint so full Notes
  content remains readable in the stacked presentation.
- [x] Preserve the two-line desktop clamp, ellipsis, fixed row height, Notes
  title, and Notes editor trigger while keeping the mobile stacked layout
  unrestricted and fully readable.
- [x] Write stylesheet assertions for the shrink constraints and strengthen
  long-Notes route coverage to prove the full value remains available through
  the existing title/editor path.
- [x] Run `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`,
  and `uv run mypy app` — all must pass before Task 19.

### Task 19: Verify dashboard and stage-history acceptance criteria

**Files:**
- Modify: `tests/test_routes.py` (only if verification identifies a gap)
- Modify: `tests/test_repository.py` (only if verification identifies a gap)
- Modify: `app/static/app.css` (only if verification identifies a layout defect)

- [x] Verify all dashboard desktop and narrow-screen criteria together:
  full-width dashboard margins, one 11-column desktop row per application,
  two-line-clamped values whose Notes cell cannot expand the table, merged
  Stage/Stage note editing, URL labels,
  compact PDF previews, Agency labels, safe job links, and New application
  upload/focus behavior.
- [x] Verify stage history cannot accept blank, placeholder, or duplicate
  Submitted additions while valid transitions still append correctly.
- [x] Run the full test suite: `uv run pytest`.
- [x] Run static checks: `uv run ruff check .`, `uv run ruff format --check .`,
  and `uv run mypy app`.
- [x] Verify test coverage meets the project standard before Task 20.

### Task 20: Update documentation and close the plan

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
- [x] Confirm every acceptance criterion and verification check above is
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
- At normal desktop width, verify the applications dashboard uses the available
  viewport width while retaining reasonable page margins, every application is
  rendered as one 11-column row of exactly two text lines, long values are
  ellipsized without changing the row height, and full Stage/Notes data remains
  available through its editor or title. Confirm the URL label, smaller PDF
  Preview CV control, and consistent table typography.
- Verify Agency appears in every user-facing application view while existing
  Agency selections remain saved after create and edit submissions.
- Open the New application dialog and verify it has no Close button, retains
  Cancel and Escape handling, opens the system file chooser, and displays the
  smaller format guidance and no-file/selected-file status legibly.
- At a narrow viewport, verify each dashboard application uses the responsive
  labelled layout, all edit controls remain reachable, and Submitted/Updated
  dates show their full `YYYY-MM-DD` values without a clipped column or
  horizontal scrollbar.
- Navigate to Choose CV with the keyboard, confirm a visible focus indicator,
  activate the native chooser with the keyboard, and confirm the selected
  filename replaces the initial status without showing its local path.
- In Stage history, confirm New stage starts at its placeholder, Submitted is
  unavailable, blank/forged Submitted submissions show validation feedback,
  and a valid transition appends exactly one new history record.
- Preview a PDF from the dashboard, download a DOC/DOCX, and verify a missing
  file fails without revealing an internal path.
