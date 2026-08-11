# Task 11 Fix 1: Harden Workflow Actions and Assessment Integrity

## Overview

This bug fix closes the four Task 11 defects found during review of the
asynchronous assessment and CV workflow UI. It prevents cross-origin or
setup-ineligible requests from queueing profile-bearing AI work, rejects
retries after an application leaves the required lifecycle stage, and refuses
to render an assessment analysis whose persisted artefact no longer matches
its immutable hash.

The change preserves the existing FastAPI, Jinja, SQLite, durable-work, and
macOS Finder boundaries. It adds focused validation around the new workflow
routes and view projection without changing scoring, generation, worker
ownership, lifecycle names, or the one-active-work invariant.

## Context (from discovery)

- `app/routes/assessments.py` and `app/routes/cv_generations.py` enqueue retry
  and override work without the same-origin guard used by setup and Finder
  actions.
- The same handlers bypass `require_setup_ready`, although assessment and CV
  requests carry private profile content and the project requires consent and
  readiness before queue insertion.
- Retry eligibility currently depends only on the newest work item's type and
  failed state; a manual lifecycle transition can therefore leave a visible
  action that queues work the domain service will reject.
- `app/routes/__init__.py::_assessment_view()` reads `analysis_path` without
  comparing the bytes with the immutable `analysis_sha256` stored in SQLite.
- Existing route tests cover normal retry, override, duplicate-work, escaped
  failure text, and Finder origin checks, but not the four reviewed failure
  scenarios.

## Development Approach

- **Testing approach**: TDD. Add a focused failing regression before each
  production change and confirm it fails for the reviewed reason.
- Complete each task fully before moving to the next task.
- Keep route guards shared and narrow; do not introduce a general middleware
  or authentication system for this local application.
- Enforce lifecycle eligibility transactionally at the repository boundary in
  addition to hiding unavailable actions in the rendered view.
- Read assessment analysis bytes once, verify the stored digest, and only then
  parse and project allowlisted fields.
- Every task that changes code must add or update success and error-path tests.
- All focused tests must pass before starting the next task.
- Update this plan immediately if implementation scope or interfaces change.
- Use `uv` for execution, Ruff at 80 columns, and mypy for static verification.

## Testing Strategy

- **Route security tests**: submit retry and override requests with missing,
  forged, and trusted origins; prove rejected requests create no work rows.
- **Setup-readiness tests**: revoke consent or invalidate readiness before an
  otherwise valid workflow action and prove queue state remains unchanged.
- **Lifecycle tests**: move failed assessment and CV applications away from
  their eligible stage, verify Retry is hidden, and verify direct POSTs are
  rejected transactionally.
- **Artefact-integrity tests**: verify valid analysis still renders, then
  modify its bytes without changing SQLite metadata and prove the altered
  analysis and gaps are not displayed.
- **No browser E2E framework**: retain the project's route-level HTTP and
  rendered-HTML assertions; do not add Playwright or another browser stack.
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

Use defense in depth at the HTTP, repository, and artefact boundaries:

1. Apply the configured same-origin check before loading workflow context or
   mutating queue state for assessment retry, CV override, CV retry, and the
   legacy CV-generation alias.
2. Revalidate setup immediately before queue insertion so revoked consent,
   missing credentials, or invalid private inputs produce a safe response and
   no durable work item.
3. Derive whether Retry is available from both terminal work state and current
   lifecycle, and repeat the lifecycle check inside the enqueue transaction to
   close direct-request and concurrent-transition gaps.
4. Read analysis bytes through `ArtefactStore`, compare their SHA-256 digest
   with `analysis_sha256`, and parse JSON only after integrity succeeds.
5. Preserve current safe error rendering, HTMX behavior, redirects, polling,
   and successful workflow actions.

## Technical Details

### Workflow request guards

- Reuse `_require_same_origin()` and `require_setup_ready()` rather than
  creating a second origin parser or consent query.
- Run the origin check first so an untrusted request cannot use detailed setup
  or application state as an oracle.
- Convert setup failures into the existing safe workflow error response for
  trusted requests while preserving HTTP 403 for an unsafe origin.
- Ensure the `/generate-cv` alias reaches exactly the same guarded code path as
  `/cv-generations` and cannot bypass validation.
- Add trusted `Origin` headers to existing successful action tests to model
  browser form behavior explicitly.

### Transactional lifecycle eligibility

- Assessment retry is eligible only while the current stage is `Assessing`.
- Matched CV retry is eligible only while the current stage is `Assessing`;
  mismatch/override CV retry is eligible only while the current stage is
  `Mismatch`.
- Perform the stage and assessment-outcome lookup in the same immediate SQLite
  transaction that inserts the replacement work row.
- Raise the existing safe work-state exception for an ineligible lifecycle so
  route behavior remains a conflict rather than a worker-side failure.
- Expose or compute narrow retry-availability values for the template so it
  never offers an action the repository will reject.

### Assessment analysis integrity

- Read the analysis artefact once as bytes to avoid a hash/read time-of-check
  race.
- Compare the lowercase SHA-256 digest with the immutable database value before
  decoding or parsing JSON.
- Continue treating missing, unsafe, malformed, non-object, or hash-mismatched
  analysis as unavailable and render no unverified analysis or gaps.
- Do not expose paths, hashes, parser details, or private content in errors.

## What Goes Where

- **Implementation Steps** contain code, tests, and in-repository documentation
  achievable in this repository.
- **Post-Completion** contains manual browser verification and GitHub workflow
  actions that require external state.

## Implementation Steps

### Task 1: Guard all workflow action requests

**Files:**

- Modify: `app/routes/assessments.py`
- Modify: `app/routes/cv_generations.py`
- Modify: `tests/test_assessment_routes.py`
- Modify: `tests/test_cv_generation_routes.py`

- [x] Add failing tests proving missing and forged origins cannot queue
  assessment retries, CV overrides, CV retries, or alias requests.
- [x] Add failing tests proving revoked consent or otherwise incomplete setup
  cannot queue an otherwise valid retry or override.
- [x] Add a shared narrow workflow-action guard that checks origin before setup
  readiness and returns only safe errors.
- [x] Apply the guard to every new workflow POST path without changing normal
  redirects or HTMX responses.
- [x] Update existing successful action tests to send the configured trusted
  origin and verify queue insertion remains unchanged.
- [x] Run `uv run pytest tests/test_assessment_routes.py
  tests/test_cv_generation_routes.py`; all tests must pass before Task 2.

### Task 2: Enforce retry lifecycle eligibility

**Files:**

- Modify: `app/work/repository.py`
- Modify: `app/routes/__init__.py`
- Modify: `app/templates/applications/_work_status.html`
- Modify: `tests/test_work_repository.py`
- Modify: `tests/test_assessment_routes.py`
- Modify: `tests/test_cv_generation_routes.py`

- [x] Add failing repository tests for assessment retry outside `Assessing`,
  matched CV retry outside `Assessing`, and mismatch CV retry outside
  `Mismatch`.
- [x] Add failing route/rendering tests proving a manual lifecycle transition
  hides Retry and a direct trusted POST creates no replacement work.
- [x] Extend transactional enqueue validation to couple retry work type,
  assessment outcome, and current application stage without weakening the
  one-active-work constraint.
- [x] Derive explicit retry availability for the workflow view and use it in
  the template instead of terminal work state alone.
- [x] Preserve successful assessment and CV retry behavior in their eligible
  stages, including safe 409 responses for invalid actions.
- [x] Run `uv run pytest tests/test_work_repository.py
  tests/test_assessment_routes.py tests/test_cv_generation_routes.py`; all
  tests must pass before Task 3.

### Task 3: Verify assessment analysis before rendering

**Files:**

- Modify: `app/routes/__init__.py`
- Modify: `tests/test_assessment_routes.py`

- [x] Add a failing test that changes the persisted analysis bytes after
  completion and proves the altered text and gaps are not rendered.
- [x] Add success coverage proving an analysis whose bytes match
  `analysis_sha256` continues to render normally.
- [x] Change `_assessment_view()` to read bytes once, verify SHA-256, decode,
  parse, and allowlist the analysis fields in that order.
- [x] Preserve safe empty rendering for missing, unsafe, malformed,
  non-object, and digest-mismatched artefacts.
- [x] Add error-path coverage proving no private path, digest, or tampered
  content appears in the response.
- [x] Run `uv run pytest tests/test_assessment_routes.py`; all tests must pass
  before Task 4.

### Task 4: Verify Task 11 Fix 1 acceptance criteria

**Files:**

- Modify:
  `docs/plans/2026-08-06-deliver-ai-assessment-and-one-page-cv-v1.md`
  only to record verified Task 11 Fix 1 results

- [x] Verify every workflow action rejects missing or forged origins before
  reading sensitive state or changing the queue.
- [x] Verify consent and setup readiness are enforced before every new
  profile-bearing work item.
- [x] Verify lifecycle-ineligible retries are hidden and rejected
  transactionally while eligible retries remain backward compatible.
- [x] Verify only hash-matching assessment analysis is rendered.
- [x] Run `uv run pytest` and record the passing count: 469 passed.
- [x] Run `uv run ruff check .` and `uv run ruff format --check .`.
- [x] Run `uv run mypy app tests`.
- [x] Run `uv run python scripts/generate_ai_schemas.py --check`.
- [x] Run `uv lock --check` and `git diff --check`.

### Task 5: [Final] Update documentation and archive the plan

**Files:**

- Modify: `README.md` only if workflow security or recovery guidance changes
- Modify: `AGENTS.md` only if a reusable project convention is discovered
- Move:
  `docs/plans/2026-08-11-task-11-fix-1-harden-workflow-actions-and-assessment-integrity.md`
  to `docs/plans/completed/` after every preceding checkbox is complete

- [x] Update user or contributor documentation only where externally visible
  behavior or a reusable convention changed.
- [x] Confirm the main delivery plan records the final commands and results.
- [x] Confirm all Task 11 Fix 1 checkboxes are complete and no blocker remains.
- [x] Move this plan to `docs/plans/completed/`.
- [x] Re-run `git diff --check` after documentation and plan updates.

Task 11 Fix 1 verification: workflow retry, override, and legacy alias POSTs
now require the configured same origin and complete setup before queueing;
retry visibility and transactional enqueue validation follow the current
lifecycle and assessment outcome; and analysis bytes are hash-verified before
projection. The focused route/repository suite passed 42 tests. Full
acceptance passed: `uv run pytest` — 472 passed; Ruff check and format check,
mypy, generated-schema drift, `uv lock --check`, and `git diff --check` also
passed.

## Post-Completion

**Manual verification:**

- In a browser, verify trusted retry and override forms still redirect and
  update status normally.
- From a separate origin, verify workflow form posts are rejected without an
  OpenAI request or durable work item.
- Revoke consent on Setup, revisit an eligible failed workflow, and verify the
  action reports setup guidance without queueing work.
- Move a failed application to another lifecycle stage and verify Retry is no
  longer offered.
- Tamper with a copied development analysis artefact and verify its altered
  contents are not displayed.

**External workflow:**

- Commit with a Conventional Commit message that includes the associated issue
  number.
- Open or update the feature-branch pull request; never push or merge directly
  to `master`.
- Comment on the associated GitHub issue with the commit or pull-request link
  after implementation is complete.
