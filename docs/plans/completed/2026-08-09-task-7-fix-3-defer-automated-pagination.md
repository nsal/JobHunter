# Task 7 Fix 3: Defer Automated Pagination Verification

GitHub issue: [#35](https://github.com/nsal/JobHunter/issues/35)

## Overview

- Remove the premature Microsoft Word pagination-verification implementation
  and its macOS-only automation boundary from the first JobHunter release.
- Generate an editable DOCX that is deliberately targeted at one A4 page, then
  stop at explicit human review. The user opens and amends the DOCX, confirms
  pagination, converts it to PDF, submits it, and records `Submitted` manually.
- Rename `Ready to apply` to `Ready for review` so lifecycle state reflects only
  what JobHunter has actually proved: deterministic DOCX generation completed,
  but factual review, pagination review, PDF conversion, and submission remain
  outstanding.
- Defer automatic PDF rendering and page-count verification to a later feature
  where the final PDF renderer can be selected deliberately. Do not add
  LibreOffice, another renderer, or a speculative verifier interface now.

## Context (from discovery)

- **Files/components involved:** `app/database.py`,
  `app/cv_generations.py`, `app/documents/__init__.py`, the Word verifier and
  AppleScript files, lifecycle/repository/route/document tests,
  `pyproject.toml`, `uv.lock`, and the parent delivery plan.
- **Related patterns:** the existing `WordWriter` applies a private DOCX
  template and typed layout settings deterministically; lifecycle stages come
  from `app.database.STAGES`; `Submitted` is already a manual transition and
  establishes `submitted_date`.
- **Current implementation state:** Word verification is not called from
  production services yet. Its Python, AppleScript, tests, marker, dependency,
  and two remediation plans are uncommitted work that can be removed without a
  runtime migration.
- **Dependencies:** `pypdf` is used only by the verifier tests/implementation.
  `python-docx` remains required for editable DOCX generation.
- **Data constraint:** this is a fresh start with no application records, so the
  stage allowlist can be renamed directly. No `Ready to apply` data migration or
  compatibility alias is required.
- **Product decision:** the initial deliverable is an editable DOCX. The user
  manually reviews and amends it, confirms it is one page in their editor,
  exports the final PDF, submits it, and then changes the application stage to
  `Submitted`.

## Development Approach

- **Testing approach:** TDD, following the existing delivery-plan convention.
  Add failing lifecycle/public-boundary regressions before removing or renaming
  implementation code.
- Complete each task fully before moving to the next and keep changes small and
  reviewable.
- Every task that changes code must add or update separate success and
  error/edge-case tests in the same task.
- All tests for the active task must pass before the next task starts; no
  failing regression may be deferred.
- Update this plan immediately if the manual-review boundary, lifecycle, or
  renderer deferral changes during implementation.
- Preserve all existing stages other than the intentional `Ready to apply` to
  `Ready for review` rename, immutable generation records, safe paths, atomic
  DOCX writes, and manual `Submitted` behavior.
- Use Ruff for formatting and linting, type all Python changes, and run Python
  tools through `uv run`.

## Testing Strategy

- **Lifecycle tests:** prove a fresh schema accepts `Ready for review`, rejects
  the removed `Ready to apply` value, exposes the new value through stage UI,
  and preserves manual transition from review to `Submitted` with the expected
  submitted date.
- **Generation tests:** update matched and mismatch-override fixtures to use
  `Ready for review`; retain eligibility, immutable metadata, chronology,
  concurrency, and safe-failure coverage.
- **Document package tests:** prove the supported public document boundary
  contains DOCX writer types and no automatic renderer/verifier types.
- **Writer regression tests:** retain template/layout, A4 portrait, margins,
  fonts, spacing, bullets, links, metadata, atomic output, and malformed-input
  coverage. These tests must not claim to prove rendered page count.
- **Dependency/reference audit:** prove no Python, test, marker, dependency, or
  delivery-plan path still requires Microsoft Word, AppleScript, `pypdf`, font
  inventory, PDF page counting, or a Word resource lease.
- **Route/UI tests:** update existing HTTP/Jinja assertions for the lifecycle
  label and manual `Submitted` transition. No browser E2E framework is needed
  because the existing route suite covers the stage selector and transition.
- **Manual verification:** open a public synthetic generated DOCX in the user's
  editor, review/amend it, confirm one-page layout manually, export a PDF, and
  record `Submitted` through the existing stage editor.

## Progress Tracking

- Mark completed items with `[x]` immediately when done.
- Add newly discovered tasks with a `➕` prefix.
- Document blockers with a `⚠️` prefix and identify the affected acceptance
  criterion.
- Record focused and final command results in this plan.
- Keep this plan synchronized with implementation and the parent Task 7 record.

## Solution Overview

The initial generation pipeline ends after deterministic DOCX creation and
persistence:

```text
assessment + cited CV content
              |
       template + layout
              |
        deterministic DOCX
              |
       Ready for review
              |
   user reviews and amends
              |
 user confirms one page and exports PDF
              |
    user records Submitted
```

`WordWriter` remains the only document implementation. It targets a one-page
A4 result by applying the configured dimensions, margins, fonts, spacing, and
bounded content, but neither the writer nor lifecycle claims that pagination
has been rendered or verified. Generation success means the editable draft is
available for review.

The Word verifier, AppleScript adapter, macOS font inventory, temporary PDF,
page-count classifications, renderer metadata, and Word serialization lease
are removed. No replacement renderer abstraction is retained because there is
no production consumer and the later PDF automation requirements are not yet
settled. A renderer boundary can be designed when automatic PDF generation is
scheduled; LibreOffice is a candidate, not a dependency or decision in this
task.

## Technical Details

- Replace `Ready to apply` with `Ready for review` in `STAGES`, SQL fixtures,
  tests, UI expectations, generation scenarios, and the parent delivery plan.
- Do not add a migration: there are no existing records or compatibility
  requirements for the removed stage value.
- Keep `Submitted` as the exact post-review stage. JobHunter performs no
  automatic review-complete, PDF-exported, or submission transition.
- Preserve the current `submitted_date` derivation from the first real
  `Submitted` stage transition.
- Delete `app/documents/word_verifier.py`,
  `app/documents/word_export.applescript`, and
  `tests/test_word_verifier.py`.
- Remove verifier exports from `app/documents/__init__.py`, remove the
  `macos_integration` pytest marker, and remove `pypdf` with `uv remove pypdf`
  so `pyproject.toml` and `uv.lock` stay synchronized.
- Do not replace deleted verifier failure values, process protocols, font
  inventory types, or renderer metadata; no production caller consumes them.
- Keep `CandidateDocument`, `WordWriter`, and `WordWriterError` as the supported
  document package exports.
- Describe generated output consistently as an editable AI draft targeted at
  one page and requiring factual, editorial, and pagination review.
- Remove Microsoft Word automation, Word lease, exact page-count acceptance,
  and macOS permission work from later delivery tasks. Preserve the manual
  Finder/download handoff and manual `Submitted` transition.
- Remove the two superseded completed Word-remediation plans. Record the design
  reversal and this completed Fix 3 plan in the parent delivery plan instead of
  retaining documentation for code that no longer exists.

## What Goes Where

- **Implementation Steps:** lifecycle rename, verifier/dependency removal,
  focused/full tests, delivery-plan realignment, and repository documentation
  are completed here.
- **Post-Completion:** manual DOCX review/PDF export and GitHub issue linkage
  are external actions. Automatic PDF rendering remains a future product
  feature.

## Implementation Steps

### Task 1: Rename the review lifecycle boundary

**Files:**
- Modify: `app/database.py`
- Modify: `tests/test_database.py`
- Modify: `tests/test_repository.py`
- Modify: `tests/test_cv_generator.py`
- Modify: `tests/test_routes.py`

- [x] Add failing schema and route regressions proving `Ready for review` is an
  allowed/selectable stage and `Ready to apply` is no longer accepted.
- [x] Add a failing repository regression proving the user can transition
  manually from `Ready for review` to `Submitted` and that `submitted_date`
  comes from that transition.
- [x] Replace `Ready to apply` with `Ready for review` in the fresh-schema stage
  allowlist; do not add a data migration or compatibility alias.
- [x] Update matched, mismatch-override, chronology, concurrency, repository,
  and route fixtures to use the new lifecycle value.
- [x] Retain separate error coverage for invalid stages, stale/ineligible
  generation state, and backdated manual transitions.
- [x] Run `uv run pytest tests/test_database.py tests/test_repository.py
  tests/test_cv_generator.py tests/test_routes.py`; all tests must pass before
  Task 2.

### Task 2: Remove automatic Word pagination verification

**Files:**
- Delete: `app/documents/word_verifier.py`
- Delete: `app/documents/word_export.applescript`
- Delete: `tests/test_word_verifier.py`
- Modify: `app/documents/__init__.py`
- Modify: `tests/test_word_writer.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`

- [x] Add a failing public-boundary regression proving `app.documents` exposes
  the DOCX writer contract without renderer, process, font-inventory, or Word
  verifier types.
- [x] Delete the Python verifier, AppleScript adapter, and verifier test suite;
  confirm no production or test import remains.
- [x] Reduce `app.documents` exports to `CandidateDocument`, `WordWriter`, and
  `WordWriterError`.
- [x] Remove the unused `macos_integration` marker and remove `pypdf` from
  both dependency metadata and the lockfile.
- [x] Retain success tests for deterministic DOCX creation and error tests for
  malformed templates/layouts, unavailable configured fonts, unsafe paths,
  and interrupted/atomic output behavior.
- [x] Run `uv run pytest tests/test_word_writer.py` and `uv run mypy app tests
  scripts`; both must pass before Task 3.

### Task 3: Realign the delivery plan with manual review

**Files:**
- Modify: `docs/plans/2026-08-06-deliver-ai-assessment-and-one-page-cv-v1.md`
- Delete:
  docs/plans/completed/2026-08-09-task-7-harden-word-pagination-verification.md
- Delete: `docs/plans/completed/2026-08-09-task-7-fix-2-macos-verification.md`
- Modify: `docs/plans/2026-08-09-task-7-fix-3-defer-automated-pagination.md`

- [x] Rewrite the overview, architecture flow, lifecycle, artefact contract,
  acceptance criteria, and manual-verification notes so DOCX generation ends
  at `Ready for review` without automatic pagination claims.
- [x] Replace Task 7 with deterministic one-page-targeted DOCX delivery for
  manual review; remove its Word, AppleScript, font inventory, renderer, PDF
  page-count, and macOS integration requirements/results.
- [x] Remove Word resource leases and renderer construction from Tasks 8 and 9,
  exact Word-page acceptance from Task 11, and macOS/Word prerequisites from
  Task 12 while retaining manual review, PDF export, and `Submitted` handoff.
- [x] Record automatic final-PDF rendering as deferred future work without
  selecting or adding LibreOffice, Aspose, ONLYOFFICE, or another renderer.
- [x] Remove the two superseded Word-remediation plans and add a concise Fix 3
  decision record/link to the parent plan.
- [x] Run a local Markdown-link/reference audit and `uv run pytest`; both must
  pass before Task 4.

### Task 4: Verify the revised acceptance criteria

**Files:**
- Modify: `docs/plans/2026-08-09-task-7-fix-3-defer-automated-pagination.md`

- [x] Confirm no code or parent active-plan requirement imports or invokes
  Microsoft Word, AppleScript, a font inventory, PDF page counting, or a Word
  lease.
- [x] Confirm no code or active-plan lifecycle accepts `Ready to apply`; the
  `Ready for review` to manual `Submitted` behavior is covered.
- [x] Confirm the DOCX writer still has focused success and error coverage and
  no automated test claims to prove rendered page count.
- [x] Run `uv run pytest` and record the passing count.
- [x] Run `uv run ruff check .` and `uv run ruff format --check .`.
- [x] Run `uv run mypy app tests scripts`.
- [x] Run `uv run python scripts/generate_ai_schemas.py --check`, `uv lock
  --check`, and `git diff --check`.
- [x] Record all final verification results in this plan before Task 5.

Final verification: focused lifecycle/generation/route/DOCX tests — 116 passed;
`uv run pytest` — 266 passed; `uv run ruff check .`, `uv run ruff format
--check .`, `uv run mypy app tests scripts`, `uv run python
scripts/generate_ai_schemas.py --check`, `uv lock --check`, and `git diff
--check` all passed. README and AGENTS required no changes because the current
operator workflow does not yet expose generated DOCX review; Task 12 owns that
documentation. The manual DOCX review, pagination confirmation, PDF export,
and `Submitted` handoff remain post-completion actions.

### Task 5: [Final] Update documentation and archive the plan

**Files:**
- Modify: `docs/plans/2026-08-09-task-7-fix-3-defer-automated-pagination.md`
- Review: `README.md`
- Review: `AGENTS.md`
- Move: `docs/plans/2026-08-09-task-7-fix-3-defer-automated-pagination.md`
  to `docs/plans/completed/`

- [x] Update README only if the currently implemented operator workflow exposes
  generated DOCX review; otherwise record that Task 12 owns user documentation.
- [x] Update AGENTS only if the work establishes a reusable project-wide
  convention; otherwise record that no AGENTS change was needed.
- [x] Re-run `uv run pytest` after documentation changes and confirm it passes.
- [x] Mark every checkbox complete and move this plan to
  `docs/plans/completed/`.

## Post-Completion

*Items requiring manual intervention or external systems; no checkboxes.*

**Manual verification:**

- Generate a public synthetic candidate through the normal DOCX writer path.
- Open it in the user's preferred editor, inspect factual content and layout,
  make a visible amendment, and confirm pagination manually.
- Export the reviewed DOCX to PDF outside JobHunter and inspect the final PDF.
- Use the existing stage editor to move the application from
  `Ready for review` to `Submitted`; confirm the submitted date is recorded.

**Deferred automation:**

- When automatic final-PDF generation becomes a product requirement, evaluate
  local headless renderers against the actual template/font corpus and define
  the PDF as the authoritative paginated artefact.
- Add a renderer interface only with a concrete production consumer and chosen
  renderer. LibreOffice is a candidate but is not selected by this plan.

**External system updates:**

- Link the implementation commit or pull request to the associated GitHub
  issue and add the required completion summary after the revision ships.
- Comment on issues #33 and #34 that Microsoft Word pagination automation was
  superseded by the manual-review decision; close them if no independent work
  remains.
