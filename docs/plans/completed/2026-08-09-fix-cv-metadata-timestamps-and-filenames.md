# Fix CV metadata privacy, generation chronology, and Unicode filenames

## Overview

- Close the three actionable findings from the Task 6 code review.
- Prevent private section revision data and related template parts from
  surviving in generated DOCX candidates.
- Reject malformed or backdated CV-generation completion timestamps before an
  immutable record is inserted.
- Keep generated candidate basenames portable when names or roles contain
  multibyte Unicode characters.
- Preserve Task 6's evidence-cited draft contract, deterministic DOCX bytes,
  current database schema, and public APIs unless a small compatibility-safe
  helper is required.

## Context (from discovery)

- `app/documents/word_writer.py` clears body and story content but preserves the
  template's final `w:sectPr` node. A tracked `w:sectPrChange` author was
  reproduced in the generated `word/document.xml`; referenced printer settings
  can survive through the same path.
- `app/cv_generations.py` serializes eligibility checks with `BEGIN IMMEDIATE`,
  but it selects only stage/outcome and accepts any non-empty `completed_at`.
- `candidate_filename` limits Python characters, while the artefact store adds
  a 21-byte temporary suffix and common filesystems limit each encoded basename
  to 255 bytes. A generated 295-byte UTF-8 basename was reproduced failing with
  `ENAMETOOLONG`.
- Existing focused coverage belongs in `tests/test_word_writer.py` and
  `tests/test_cv_generator.py`; there is no browser or route behavior in scope.
- The project uses Python 3.14, `uv`, Ruff at 80 columns, mypy, pytest, SQLite,
  python-docx, and deterministic ZIP/package sanitization.
- Associated GitHub issue:
  [#31](https://github.com/nsal/JobHunter/issues/31).

## Development Approach

- **Testing approach:** Regular. Implement each focused correction, add its
  success and error-path tests, and run the focused suite before continuing.
- Complete each task fully and mark its checklist immediately before moving to
  the next task.
- Every task that changes code must add or update tests for every modified path.
- Keep changes small and local; add no dependency or schema migration.
- Preserve backward compatibility for ordinary ASCII filenames, valid existing
  timestamps, public CV models, generated visible content, and layout behavior.
- Keep privacy, chronology, filename acceptance, and output determinism under
  application control rather than relying on Microsoft Word or the filesystem.
- Update this plan immediately if implementation changes a file, public API,
  schema, workflow boundary, or acceptance criterion.
- Run the required focused test after each task and do not continue while it is
  failing.

## Testing Strategy

- **DOCX privacy tests:** Inject tracked section-change metadata, relationship-
  backed printer settings, private bytes, and content-type entries into a
  synthetic template. Assert the candidate contains none of them while its
  page geometry, styles, visible content, and deterministic hash remain valid.
- **Timestamp tests:** Cover malformed text, completion before the assessment,
  completion before the current stage, equivalent instants with differing UTC
  offsets, existing naive UTC values, successful valid persistence, rollback,
  and artefact cleanup through the service path.
- **Filename tests:** Preserve current ASCII/reserved-name cases and cover
  multibyte BMP and supplementary-plane characters, minimum role retention,
  UTF-8 boundary behavior, atomic temporary-name headroom, and a successful
  writer round trip for a formerly overlong candidate name.
- **Regression tests:** Re-run the focused generator/writer suites after each
  task and the full project verification matrix before completion.
- **No browser E2E:** The fixes have no route or UI behavior, so the existing
  pytest suites are the correct acceptance level.

## Progress Tracking

- Mark completed items with `[x]` immediately when done.
- Add newly discovered work with a `➕` prefix.
- Add blockers with a `⚠️` prefix and identify the affected acceptance
  criterion.
- Record focused and full-suite command results in this file.
- Keep this plan and the parent delivery plan synchronized with actual paths,
  behavior, and test counts.

## Solution Overview

Use three narrow corrections that preserve the existing architecture:

1. Clear headers and footers, then reset the one surviving section-properties
   element to a minimal, attribute-free baseline before applying configured page
   geometry. This allowlist-style reset removes revision metadata, printer
   settings, stale header/footer references, and unknown section descendants.
   Removing only the two observed elements is rejected because future private
   section metadata could bypass a blacklist.
2. Parse stored and proposed timestamps as ISO-8601 instants, treating existing
   naive datetime-local values as UTC for compatibility. Inside the same
   `BEGIN IMMEDIATE` eligibility transaction, require completion to be no
   earlier than both the assessment and current stage. A SQLite schema check is
   rejected because it cannot reliably compare mixed ISO offsets or rows in
   related tables.
3. Retain the current safe-character and reserved-name rules, but apply both the
   80-character bound and a conservative UTF-8 byte bound. Reserve the exact
   atomic temporary suffix before allocating bytes between name, separator,
   role, and `.docx`, and truncate only at Unicode code-point boundaries. Using
   platform `pathconf` is rejected because filenames and hashes must remain
   deterministic across supported systems.

## Technical Details

- The final candidate basename must remain at most 80 characters and at most
  234 UTF-8 bytes. The byte limit reserves 21 bytes for `.tmp-` plus the current
  16-hex-character token under a 255-byte filesystem component limit.
- Preserve the full sanitized candidate name when possible, then allocate the
  remaining character and byte budget to the target role. If necessary, shorten
  the name while reserving at least one safe role character; retain the existing
  ASCII fallbacks.
- Reset the final `w:sectPr` children and attributes after any referenced story
  parts have been cleared. `_apply_page_layout` must then recreate only portrait
  page size and configured margins; Word defaults provide the remaining section
  behavior.
- Package reachability pruning remains authoritative for deleting relationships,
  printer-setting binaries, orphan headers/footers, and content-type overrides
  after their section references disappear.
- Parse timestamps with `datetime.fromisoformat`. Normalize aware values to UTC
  and attach UTC to existing naive values before comparison. Reject invalid,
  empty, or chronologically earlier completion values with safe `ValueError`
  messages.
- Select `assessment.completed_at` and `history.effective_from` in the locked
  eligibility query. Perform validation before inserting `cv_generations`, so
  any failure rolls back and the service's existing cleanup removes candidate
  artefacts.
- Do not change tables, JSON contracts, YAML settings, provider calls,
  lifecycle stages, or visible generated CV prose.

## What Goes Where

- **Implementation Steps:** Repository code, DOCX sanitization, filename logic,
  focused tests, verification, and plan synchronization are tracked below.
- **Post-Completion:** GitHub commit, PR, and issue updates require external
  version-control actions and are listed separately.

## Implementation Steps

### Task 1: Rebuild safe DOCX section properties

**Files:**
- Modify: `app/documents/word_writer.py`
- Modify: `tests/test_word_writer.py`

- [x] Clear every surviving `w:sectPr` child and attribute after clearing
  referenced header/footer stories and before applying layout settings.
- [x] Preserve configured portrait page size and margins, required styles,
  visible CV content, relationship reachability, and deterministic ZIP output.
- [x] Add a success test proving ordinary synthetic templates still render with
  the configured layout after the section reset.
- [x] Add privacy regressions for `w:sectPrChange`, author/date/revision values,
  `w:printerSettings`, its relationship, binary part, and content-type entry.
- [x] Run `uv run pytest tests/test_word_writer.py`; 28 passed before Task 2.

### Task 2: Enforce CV-generation timestamp chronology

**Files:**
- Modify: `app/cv_generations.py`
- Modify: `tests/test_cv_generator.py`

- [x] Add a small typed ISO-8601 normalization helper that treats naive stored
  datetime-local values as UTC and normalizes aware timestamps to UTC.
- [x] Extend the locked eligibility query with assessment completion and current
  stage effective timestamps.
- [x] Reject malformed completion timestamps and values earlier than either
  source timestamp before inserting the immutable generation row.
- [x] Add repository tests for valid naive/aware values, equivalent UTC offsets,
  malformed values, assessment backdating, and stage backdating.
- [x] Add a service-path regression proving chronology failure leaves no
  `cv-content.json`, candidate DOCX, or generation row.
- [x] Run `uv run pytest tests/test_cv_generator.py`; 31 passed before Task 3.

### Task 3: Make candidate filenames UTF-8 byte safe

**Files:**
- Modify: `app/documents/word_writer.py`
- Modify: `tests/test_word_writer.py`

- [x] Introduce deterministic UTF-8-safe truncation that never splits a Unicode
  code point and retains the existing safe-character normalization.
- [x] Enforce both the current 80-character limit and the 234-byte atomic target
  limit while preserving the separator, `.docx`, and at least one role
  character or fallback.
- [x] Preserve current output for existing ASCII and reserved-name cases.
- [x] Add boundary tests for BMP and supplementary Unicode, role/name budget
  allocation, byte limits, and stable output.
- [x] Add a writer round-trip test proving a formerly overlong multibyte name is
  atomically stored and reopened as a valid DOCX.
- [x] Run `uv run pytest tests/test_word_writer.py`; 32 passed before Task 4.

### Task 4: Verify remediation acceptance criteria

**Files:**
- Modify: `docs/plans/2026-08-09-fix-cv-metadata-timestamps-and-filenames.md`

- [x] Verify generated packages contain no tracked section author/date metadata,
  printer-settings relationship, binary, or content-type override.
- [x] Verify malformed/backdated timestamps fail without persisted rows or
  artefacts and valid mixed-format timestamps retain correct ordering.
- [x] Verify every candidate and atomic temporary basename remains within the
  documented character and UTF-8 byte budgets.
- [x] Run `uv run pytest tests/test_cv_generator.py` — 31 passed — and
  `uv run pytest tests/test_word_writer.py` — 32 passed.
- [x] Run `uv run pytest` — 258 passed.
- [x] Run `uv run ruff check .`, `uv run ruff format --check .`, and
  `uv run mypy app tests scripts` — all passed.
- [x] Run `uv run python scripts/generate_ai_schemas.py --check`,
  `uv lock --check`, and `git diff --check` — all passed.

### Task 5: Finalize remediation documentation

**Files:**
- Modify: `docs/plans/2026-08-06-deliver-ai-assessment-and-one-page-cv-v1.md`
- Move: `docs/plans/2026-08-09-fix-cv-metadata-timestamps-and-filenames.md`
  to `docs/plans/completed/`

- [x] Record the remediation behavior and final verification results in the
  parent Task 6 progress notes.
- [x] Confirm README and AGENTS require no changes, or update them if a reusable
  timestamp or filename convention was introduced.
- [x] Mark every checklist item complete and move this plan to
  `docs/plans/completed/`.
- [x] Run `git diff --check` after the documentation move.

## Post-Completion

**Manual verification:**
- On macOS, optionally open a candidate generated from a public synthetic
  metadata-bearing template in Microsoft Word and inspect Document Inspector.
- Confirm the candidate displays the configured portrait layout and expected
  Unicode filename in Finder. Authoritative Word pagination remains Task 7.

**External system updates:**
- Commit on the current feature branch using Conventional Commits and the
  associated issue number; do not push or merge directly to `master`.
- Open or update a pull request for the remediation.
- Comment on the associated GitHub issue with the commit or PR link and final
  verification results after implementation.
