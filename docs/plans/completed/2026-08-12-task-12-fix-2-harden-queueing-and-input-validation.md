# Task 12 Fix 2: Harden Queueing and Input Validation

## Overview

- Close five review findings in the initial assessment workflow: missing
  same-origin enforcement, a consent revocation race, dropped raw HTML source
  blocks, incomplete DOCX template validation, and non-canonical DNS origins.
- Prevent cross-origin requests or concurrently revoked consent from creating
  applications that queue private-profile work.
- Make setup validation and runtime processing agree on accepted Markdown and
  DOCX inputs so an installation reported as ready remains usable.
- Preserve existing application, queue, source-block, and document contracts
  outside these narrowly scoped corrections.

## Context (from discovery)

- Associated GitHub issue:
  [#52](https://github.com/nsal/JobHunter/issues/52).
- `app/main.py` checks setup readiness before `POST /applications`, but the
  endpoint does not invoke the same-origin guard used by retry and override
  actions.
- `app/repository.py` creates the application, stage history, and initial
  assessment work item in one connection, but it does not lock and recheck
  profile-sharing consent in that transaction.
- `app/routes/setup.py` canonicalizes IP literals but preserves DNS hostname
  case, while browser `Origin` values and configured trusted origins are
  compared as exact strings.
- `app/ai/source_blocks.py` recognizes CommonMark headings, paragraphs, lists,
  tables, and code, but ignores `html_block` tokens and can silently omit raw
  HTML from mixed input.
- `app/settings.py` proves that a template is an openable DOCX, whereas
  `app/documents/word_writer.py` later requires the `Normal`, `Title`,
  `Heading 1`, and `List Bullet` paragraph styles.
- Existing regression homes are `tests/test_routes.py`,
  `tests/test_setup_routes.py`, `tests/test_repository.py`,
  `tests/test_source_blocks.py`, `tests/test_settings.py`, and
  `tests/test_word_writer.py`.
- The project is Python 3.14 with FastAPI, SQLite, python-docx, Ruff at 80
  columns, mypy, pytest, and `uv`; the reviewed baseline has 477 tests.

## Development Approach

- **Testing approach:** TDD, as selected by the user.
- For each finding, first add a focused test that reproduces the failure and
  confirm it fails for the intended reason; then implement the smallest fix.
- Complete each task and make its focused tests pass before starting the next
  task. Do not carry a failing test across task boundaries.
- Keep the existing setup-readiness check as an early user-facing precondition,
  while treating the transaction-local consent check as authoritative.
- Reuse one DOCX style-validation contract from setup and writing paths so
  their requirements cannot drift.
- Add no dependency, database migration, new source-block kind, or broader
  authentication mechanism unless implementation proves one is necessary.
- Update this plan immediately if scope or implementation details change.

## Testing Strategy

- **Route integration tests:** verify successful same-origin application
  creation and rejection of missing, malformed, or forged origins without
  creating database records or queued work.
- **Repository tests:** verify consent is checked under the same immediate
  transaction as application and work insertion, including granted, absent,
  and revoked states and complete rollback on rejection.
- **Parser unit tests:** verify all-HTML and mixed Markdown/raw-HTML documents
  retain every source range in deterministic order and continue to obey block
  limits and hashes.
- **Settings and writer tests:** verify setup rejects missing or wrong-type
  required styles, accepts complete templates, and reports requirements
  consistent with the writer.
- **Origin unit tests:** cover mixed-case DNS names alongside canonical IPv4
  and IPv6 behavior and invalid-host rejection.
- **Full regression gate:** run pytest, Ruff lint and format checks, mypy,
  generated-schema drift checks, lockfile validation, and Git whitespace
  checks after all focused suites pass.
- **No browser E2E suite is present:** exercise the HTTP boundary with the
  existing ASGI client and reserve a manual browser smoke test for
  Post-Completion.

## Progress Tracking

- Mark completed items with `[x]` immediately when done.
- Add newly discovered tasks with a `➕` prefix.
- Record blockers with a `⚠️` prefix and identify the affected finding.
- Record the failing and passing focused command for every TDD task.
- Keep file lists, implementation notes, and verification results synchronized
  with the actual diff.
- Do not archive this plan until every automated gate passes.

## Solution Overview

Apply the existing trust and validation rules at every relevant boundary:

1. Guard initial application creation with `_require_same_origin` before
   readiness checks or persistence.
2. Canonicalize DNS hostnames to lowercase when constructing trusted origins.
3. Add an opt-in consent requirement to application creation, acquire a SQLite
   immediate transaction, and recheck consent before any application, history,
   or work row is written.
4. Preserve CommonMark `html_block` ranges as existing paragraph source blocks
   so model input is complete without expanding the public block-kind schema.
5. Extract the writer's required-style checks into a shared document helper
   used by both setup validation and `WordWriter`.

This keeps request authenticity, consent authorization, and durable queueing
as separate defenses while ensuring readiness reflects the same input contract
the worker will enforce.

## Technical Details

### Same-origin application creation

- Call `_require_same_origin(request)` at the start of `POST /applications`,
  before `require_setup_ready` and repository mutation.
- Keep the existing 403 response contract for absent, invalid, and untrusted
  origins.
- Update successful route-test requests or their fixture defaults to send the
  configured trusted origin explicitly; do not weaken the guard for tests or
  non-HTMX form posts.
- Assert rejected requests leave application, stage-history, and work-item
  tables unchanged.

### Origin canonicalization

- Lowercase DNS hostnames in `normalize_http_origin` after validation.
- Continue to normalize IP literals through `ipaddress.ip_address`, preserve
  bracketed IPv6 rendering, reject unspecified addresses, and omit port 80.
- Let `normalize_configured_origin` inherit the same canonicalization so both
  configured state and incoming `Origin` headers compare identically.

### Transactional consent enforcement

- Extend `Repository.create_application` with an explicit keyword-only
  `require_profile_consent` flag, defaulting to `False` for trusted internal
  fixture and seeding paths.
- The web route must pass `require_profile_consent=True` because initial
  assessment work reads the private profile.
- Start `BEGIN IMMEDIATE`, read consent using the existing connection, and
  reject before the first insert when acknowledgement is absent or revoked.
- Surface a safe user-facing precondition response while retaining
  `require_setup_ready` for normal setup guidance.
- Verify a rejected transaction leaves no partial application, stage, or work
  rows and that granted consent still creates all three atomically.

### Raw HTML source preservation

- Recognize CommonMark `html_block` tokens in `_structural_fragments` and map
  them to `BlockKind.PARAGRAPH`; retain their original bounded source text.
- Preserve ordering and `covered_until` behavior when raw HTML appears before,
  between, or after Markdown structures.
- Keep normalization, splitting, IDs, hashes, maximum byte count, maximum
  block size, and maximum block count unchanged.

### Shared DOCX template requirements

- Move required paragraph-style names and style-type validation into a small
  document-level helper that does not import application settings or the
  writer, avoiding a circular dependency.
- Have `validate_template_input` call the helper after opening the DOCX and
  translate validation failures to `SettingsError` with the template path and
  missing or invalid style name.
- Have `WordWriter` use the same helper and retain its `WordWriterError`
  boundary and current message semantics.
- Treat a style with a required name but a non-paragraph type as invalid in
  both paths.

## What Goes Where

- **Implementation Steps:** regression tests, route and repository security
  fixes, parser correction, shared template validation, full verification,
  documentation records, and plan archival achievable in this repository.
- **Post-Completion:** manual browser checks and GitHub commit, pull request,
  and issue coordination performed after implementation.

## Implementation Steps

### Task 1: Enforce same-origin application creation

**Files:**

- Modify: `app/main.py`
- Modify: `tests/test_routes.py`
- Modify: `tests/test_setup_routes.py`

- [x] Add route regression tests for missing and forged `Origin` headers and
  confirm they fail because `POST /applications` currently accepts them.
- [x] Add or update the successful creation case to send the configured trusted
  origin and retain normal redirect/HTMX behavior.
- [x] Call `_require_same_origin` before setup inspection and persistence in the
  application-creation endpoint.
- [x] Assert origin failures return 403 and create no application, stage, or
  work rows; include the valid-origin success scenario separately.
- [x] Run `uv run pytest tests/test_routes.py tests/test_setup_routes.py`; all
  tests must pass before Task 2.

### Task 2: Canonicalize trusted DNS hostnames

**Files:**

- Modify: `app/routes/setup.py`
- Modify: `tests/test_setup_routes.py`

- [x] Add a failing unit test proving a mixed-case DNS launcher host and
  browser-style lowercase origin currently compare differently.
- [x] Add success cases for lowercase canonical output and configured-origin
  normalization without changing IPv4, bracketed IPv6, default-port, or
  invalid-host behavior.
- [x] Lowercase validated DNS hostnames while leaving IP canonicalization to
  `ipaddress.ip_address`.
- [x] Add error and edge cases covering malformed DNS-origin input and ensure
  they continue to fail closed.
- [x] Run `uv run pytest tests/test_setup_routes.py`; all tests must pass before
  Task 3.

### Task 3: Recheck profile-sharing consent in the creation transaction

**Files:**

- Modify: `app/repository.py`
- Modify: `app/main.py`
- Modify: `tests/test_repository.py`
- Modify: `tests/test_routes.py`
- Modify: `tests/test_setup_routes.py`

- [x] Add failing repository tests for required consent that is absent or
  revoked, plus a granted-consent success case.
- [x] Assert each rejection rolls back the application, initial stage, and
  assessment work item, while the success case commits all three.
- [x] Add `require_profile_consent`, acquire `BEGIN IMMEDIATE`, and read consent
  through that same connection before the first insert.
- [x] Make `POST /applications` require transactional profile consent and map
  rejection to the existing safe setup/precondition response contract.
- [x] Add route success and error tests showing the setup precheck is useful but
  the repository check remains authoritative if consent is revoked before the
  transaction.
- [x] Run `uv run pytest tests/test_repository.py tests/test_routes.py \
  tests/test_setup_routes.py`; all tests must pass before Task 4.

### Task 4: Preserve raw HTML source blocks

**Files:**

- Modify: `app/ai/source_blocks.py`
- Modify: `tests/test_source_blocks.py`

- [x] Add a failing all-HTML test that expects a non-empty deterministic source
  block containing the complete raw HTML.
- [x] Add a failing mixed Markdown/raw-HTML test that expects every fragment in
  source order with no duplication or omission.
- [x] Recognize `html_block` tokens and classify them using the existing
  paragraph block kind without changing serialized schemas.
- [x] Add edge cases for adjacent HTML and Markdown blocks and verify block IDs,
  order, content hashes, splitting, and limits remain valid.
- [x] Run `uv run pytest tests/test_source_blocks.py`; all tests must pass
  before Task 5.

### Task 5: Validate required DOCX styles during setup

**Files:**

- Create: `app/template_validation.py`
- Modify: `app/settings.py`
- Modify: `app/documents/word_writer.py`
- Modify: `tests/test_settings.py`
- Modify: `tests/test_word_writer.py`

- [x] Add failing settings tests for a valid DOCX missing each required style
  and for a required name bound to a non-paragraph style.
- [x] Retain a separate success test for a template containing every required
  paragraph style and existing corruption/path error cases.
- [x] Extract the style names and validation logic into an import-safe shared
  document helper and use it from both setup and writer paths.
- [x] Translate helper failures to `SettingsError` during setup and
  `WordWriterError` during generation without hiding the offending style.
- [x] Update writer tests to prove runtime success and error behavior remains
  consistent with setup validation.
- [x] Run `uv run pytest tests/test_settings.py tests/test_word_writer.py`; all
  tests must pass before Task 6.

### Task 6: Verify acceptance criteria and engineering standards

**Files:**

- Modify:
  `docs/plans/2026-08-12-task-12-fix-2-harden-queueing-and-input-validation.md`

- [x] Verify all five review findings have a regression test that failed before
  its fix and passes afterward.
- [x] Verify cross-origin and consent failures cannot leave an application,
  stage-history row, or queued work item.
- [x] Verify mixed-case trusted hosts compare canonically, raw HTML is retained
  in order, and setup rejects every template the writer would reject for
  missing or wrong-type required styles.
- [x] Run `uv run pytest` and record the complete passing count: 489 passed.
- [x] Run `uv run ruff check .` and `uv run ruff format --check .`.
- [x] Run `uv run mypy app tests scripts` and
  `uv run python scripts/generate_ai_schemas.py --check`.
- [x] Run `uv lock --check` and `git diff --check`.
- [x] Record every verification result in this plan before Task 7.

Task 6 verification: the full suite reported 489 passed. Ruff check and
format check passed; mypy reported no issues in 65 source files; generated AI
schema drift, `uv lock --check`, and `git diff --check` passed. Focused tests
also passed for routes/setup (59), repository/routes/setup (78), source
blocks (26), and settings/writer (57).

### Task 7: Record the fixes and archive the plan

**Files:**

- Modify:
  `docs/plans/completed/2026-08-06-deliver-ai-assessment-and-one-page-cv-v1.md`
- Modify: `README.md` only if operator-facing security or input requirements
  changed
- Modify: `AGENTS.md` only if a reusable project-wide rule is discovered
- Move:
  `docs/plans/2026-08-12-task-12-fix-2-harden-queueing-and-input-validation.md`
  to `docs/plans/completed/`

- [x] Record Task 12 Fix 2 behavior and full verification results in the parent
  completion plan.
- [x] Update README only if operators need new origin, consent, Markdown, or
  template-preparation guidance; avoid documenting internal-only refactors.
- [x] Update AGENTS only if implementation establishes a reusable engineering
  rule not already covered there.
- [x] Confirm every checklist item and verification result is complete, then
  move this plan to `docs/plans/completed/`.
- [x] Keep the associated issue, Conventional Commit with issue number, draft
  pull request, and issue completion comment synchronized through the GitHub
  workflow without pushing or merging directly to `master`.

## Post-Completion

*Items requiring manual intervention or external systems; no checkboxes.*

**Manual verification**

- From the configured trusted browser origin, create one application after
  granting consent and confirm it enters `Assessing` with queued assessment
  work.
- Revoke consent and confirm application creation is blocked without a partial
  dashboard record or queued job.
- Load a mixed Markdown/raw-HTML profile through a non-production provider
  fixture or dry run and inspect that all source blocks are present in order.
- Select a DOCX missing a required style and confirm setup reports the exact
  requirement before work can be queued.

**GitHub workflow**

- Commit on the existing feature branch using a Conventional Commit message
  that references the associated issue.
- Push the feature branch, update the draft pull request, and comment on the
  issue with the commit or pull-request link and verification summary.
- Do not push or merge directly to `master`.
