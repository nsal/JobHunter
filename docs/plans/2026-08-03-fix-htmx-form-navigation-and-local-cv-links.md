# Fix HTMX Form Navigation and Local CV Links

## Overview

- Fix create and edit form submissions when HTMX is enabled so successful
  submissions navigate to the canonical application detail page.
- Preserve validation feedback without swapping full HTML documents into the
  document body.
- Keep job URLs optional for applications whose job description arrived by
  email, while preventing executable link schemes.
- Support a clickable, local on-disk CV location through validated absolute
  paths and `file:` URLs.

## Context (from discovery)

- JobHunter is a local FastAPI application with SQLite persistence and Jinja
  templates.
- `app/main.py` owns form routes and currently returns a table-row fragment
  for HTMX create requests, while both relevant templates target `body`.
- `app/templates/applications/form.html` and `detail.html` contain the create
  and edit forms; `tests/test_routes.py` covers normal redirects and only the
  stage-history HTMX path.
- `job_url` and `cv_path` are persisted by `app/repository.py` and rendered as
  anchors in the application detail and list-row templates.
- The workspace has no Git metadata, so this plan is based on the current
  source snapshot rather than a branch diff.

## Development Approach

- **Testing approach:** Regular — implement each focused change, then add or
  update its tests before continuing.
- Retain HTMX for create and edit requests. On success, send a successful
  response carrying `HX-Redirect` to the application detail URL; retain the
  existing 303 redirect for non-HTMX requests.
- Extract create and edit form fragments with stable, dedicated HTMX targets.
  Return those fragments for HTMX validation errors; return normal full-page
  templates for non-HTMX validation errors.
- Return HTMX validation fragments with status 200 so HTMX swaps them by
  default; preserve 422 for non-HTMX validation errors.
- Centralize link normalization and validation before persistence. A blank job
  URL is valid; non-blank job URLs must use `http` or `https`. CV locations
  must be either absolute filesystem paths (normalized to `file:` URLs) or
  `file:` URLs. Reject relative paths and all other schemes.
- Complete a task, write its success and error-path tests, and run its relevant
  tests before starting the next task. Update this plan if scope changes.
- Maintain existing non-HTMX PRG behavior and current stage-history HTMX
  behavior.

## Testing Strategy

- Add repository tests for empty and valid job URLs, rejected unsafe job URL
  schemes, absolute-path CV normalization, accepted `file:` URLs, and rejected
  relative or executable CV values.
- Add route tests for HTMX create/edit success (`HX-Redirect`) and validation
  failures that return the matching form fragment rather than a document.
- Retain coverage of standard POST/303/GET flows and stage-history HTMX
  updates.
- No browser E2E framework exists in this project. Perform manual browser
  verification for HTMX navigation and local-file link behavior, because
  browsers may restrict `file:` navigation from `http://localhost`.

## Progress Tracking

- Mark completed tasks immediately with `[x]`.
- Add newly discovered work with a `➕` prefix and record blockers with a
  `⚠️` prefix.
- Keep this plan synchronized with implementation changes.

## Solution Overview

The solution separates full document rendering from HTMX fragment rendering.
Each form receives an element-specific target for validation responses, while
successful HTMX submissions instruct the client to perform a normal navigation
to the detail page. Link validation is performed at the repository boundary so
both create and update operations store only safe, normalized link values.

## Technical Details

- Add a typed normalization helper in `app/repository.py`, using standard URL
  parsing and `pathlib` rather than template-side checks.
- Treat an empty `job_url` as `None`; accept only absolute `http`/`https` URLs
  otherwise.
- Convert an absolute `cv_path` to a `file:` URI before storing it. Accept a
  `file:` URI as supplied, but reject relative paths and non-`file` schemes.
- Sanitize persisted links on read so legacy unsafe values are never rendered
  as anchors.
- Give create and edit forms distinct wrapper IDs and return their matching
  partial templates for HTMX errors.
- For successful HTMX create/update requests, return an empty successful
  response with an `HX-Redirect` header to `/applications/{id}`. The existing
  303 responses remain for regular form posts.

## Implementation Steps

### Task 1: Validate and normalize stored job and CV links

**Files:**
- Modify: `app/repository.py`
- Modify: `tests/test_repository.py`

- [x] Add typed helpers that normalize optional job URLs and CV locations
  before insert or update.
- [x] Allow blank job URLs and restrict non-empty values to absolute `http` or
  `https` URLs.
- [x] Accept absolute CV paths by converting them to `file:` URIs and accept
  existing `file:` URIs; reject relative and unsafe-scheme values.
- [x] Apply validation consistently in `create_application` and
  `update_application` without changing unrelated persisted fields.
- [x] Sanitize legacy stored links before applications reach templates.
- [x] Write repository tests for accepted empty/HTTP(S)/local-file inputs and
  for rejected malformed, relative, and executable-scheme values.
- [ ] ⚠️ Run `uv run pytest tests/test_repository.py` and fix all failures
  before Task 2. Blocked: `uv` is unavailable and `.venv/bin/python` points to
  a missing interpreter.

### Task 2: Make HTMX create and edit responses navigation-safe

**Files:**
- Modify: `app/main.py`
- Modify: `app/templates/applications/form.html`
- Create: `app/templates/applications/_application_form.html`
- Modify: `app/templates/applications/detail.html`
- Create: `app/templates/applications/_application_edit_form.html`
- Modify: `tests/test_routes.py`

- [x] Extract create and edit forms into partials with stable wrapper IDs and
  element-specific HTMX targets for validation responses.
- [x] Render full pages for normal GET and non-HTMX validation errors, and
  render only the corresponding form partial for HTMX validation errors.
- [x] Return successful `HX-Redirect` responses for HTMX create and edit
  requests while preserving the existing 303 redirects for ordinary posts.
- [x] Ensure form errors retain submitted values, including checkboxes and
  valid local CV locations.
- [x] Add route tests for HTMX create/edit redirects, HTMX validation fragments,
  normal redirects, and link-validation 422 responses.
- [ ] ⚠️ Run `uv run pytest tests/test_routes.py` and fix all failures
  before Task 3.
  Blocked: `uv` is unavailable and `.venv/bin/python` points to a
  missing interpreter.

### Task 3: Verify the complete regression suite and acceptance criteria

**Files:**
- Modify: `tests/test_routes.py` (only if test gaps are found)
- Modify: `tests/test_repository.py` (only if test gaps are found)

- [ ] Confirm non-HTMX create and edit still use POST/Redirect/GET behavior.
- [ ] Confirm HTMX create and edit never return a document or table row for a
  `body` swap, and instead direct the client to the application detail page.
- [ ] Confirm blank job URLs save successfully and accepted CV paths are stored
  and rendered as `file:` links.
- [ ] Add any missing success and error-path regression tests discovered during
  verification.
- [ ] Run `uv run pytest`, `uv run ruff check .`, and `uv run mypy app`; fix all
  failures before Task 4.

### Task 4: Update user-facing local-file guidance

**Files:**
- Modify: `README.md`
- Modify: `docs/plans/2026-08-03-fix-htmx-form-navigation-and-local-cv-links.md`

- [x] Document that job URLs are optional and are restricted to web URLs.
- [x] Document the accepted CV-location formats and the possibility that a
  browser blocks `file:` navigation from `http://localhost`.
- [x] Record the HTMX validation-status adjustment and verification blocker in
  this plan.
- [ ] ⚠️ Re-run `uv run pytest` after documentation-adjacent test updates,
  if any.
  Blocked: `uv` is unavailable and `.venv/bin/python` points to a missing
  interpreter.
- [ ] Move this plan to `docs/plans/completed/` only after implementation and
  all verification steps are complete.

## Post-Completion

**Manual verification:**

- Submit valid and invalid create/edit forms in a browser with HTMX loaded;
  verify successful submissions navigate to the detail page and validation
  messages remain inside the affected form.
- Open a stored `file:` CV link in the supported target browsers and document
  any browser-specific restriction observed.

**External system updates:**

- None expected. The application remains a local FastAPI service.
