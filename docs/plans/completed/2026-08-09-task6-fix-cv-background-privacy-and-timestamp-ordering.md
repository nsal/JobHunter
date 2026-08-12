# Task6: Fix CV background privacy and timestamp ordering

## Overview

- Close the two remaining actionable findings from the Task 6 code review.
- Prevent document-level backgrounds and their relationships or linked assets
  from surviving from a private DOCX template into a generated CV candidate.
- Return completed CV generations in chronological order even when stored
  ISO-8601 timestamps use different UTC offsets.
- Clean the active Task 6 record after an intentional rollback: retain one
  concise rollback note and the authoritative issue #31 history, but do not
  restore or recreate discarded plan files.
- Preserve the current CV schema, database schema, stored timestamp text,
  visible generated content, filename behavior, and public service APIs.

## Context (from discovery)

- `app/documents/word_writer.py` clears body, header, footer, and section
  content, but it does not clear `w:background` from the document root. The
  package sanitizer consequently retains live background relationships and
  internal private image bytes.
- `app/cv_generations.py` validates chronology as normalized UTC instants, then
  stores the original ISO-8601 text and orders that text lexicographically.
  Differing offsets can therefore produce the wrong newest-first order.
- Earlier implementation and remediation iterations failed and were
  intentionally rolled back before Task 6 restarted from a clean baseline.
  Their staged archive deletions are expected and must not be reversed.
- The active Task 6 plan still contains verbose entries and seven broken links
  for those discarded iterations. Those references, rather than the deleted
  files, are the documentation defect.
- The issue #31 remediation file remains authoritative because its behavior,
  implementation, and tests are present in the current working tree.
- The project uses Python 3.14, `uv`, Ruff at 80 columns, mypy, pytest, SQLite,
  python-docx, and deterministic DOCX package sanitization.
- Associated GitHub issue:
  [#32](https://github.com/nsal/JobHunter/issues/32).

## Development Approach

- **Testing approach:** TDD. Add a failing regression before each behavioral
  correction, implement the smallest fix, and rerun the focused suite.
- Complete each task fully and mark its checklist immediately before moving to
  the next task.
- Make small, focused changes and avoid schema, dependency, or API expansion.
- Every code change must include focused success and edge-case tests in the
  same task.
- Do not proceed to the next task while its focused tests are failing.
- Update this plan immediately if implementation changes a file, public API,
  schema, workflow boundary, or acceptance criterion.
- Preserve unrelated user changes in the dirty working tree, including the
  intentional deletions from the rolled-back documentation set.

## Testing Strategy

- **DOCX privacy tests:** Build synthetic templates containing a standards-
  compliant document-level background with an internal image relationship, an
  external relationship, and no relationship. Prove the current leak, then
  assert all background-only content is absent after the fix.
- **Timestamp ordering tests:** Persist multiple generations for one
  application across distinct assessments using timestamps whose lexical and
  chronological orders differ. Assert newest-first instant ordering and stable
  ID ordering for equal instants.
- **Documentation validation:** Run a focused audit of remaining relative plan
  links after removing discarded-iteration references. Do not add a permanent
  Markdown parser or documentation-test framework.
- **Regression tests:** Run the focused writer and generator suites after their
  respective tasks, followed by the complete project verification matrix.
- **No browser E2E:** These changes affect DOCX generation, repository ordering,
  and engineering documentation only; existing pytest coverage is the correct
  automated level.

## Progress Tracking

- Mark completed items with `[x]` immediately when done.
- Add newly discovered tasks with a `➕` prefix.
- Add blockers with a `⚠️` prefix and identify the affected acceptance
  criterion.
- Record focused and full-suite command results in this file.
- Keep this plan and the parent Task 6 plan synchronized with actual behavior.

## Solution Overview

Use the targeted approach selected and validated during brainstorming:

1. Remove direct `w:background` children while clearing the private template.
   Existing relationship reachability pruning will then remove internal or
   external relationships used only by the background and any linked internal
   package asset.
2. Preserve accepted and stored timestamp text, but order rows by the same
   Python-normalized UTC instants used for validation and retain `id DESC` as
   the deterministic tie-breaker.
3. Keep the intentional archive deletions. Replace the discarded Task 6
   remediation sequence with one concise rollback and shipping-baseline note,
   then retain the existing issue #31 entry as authoritative history.
4. Verify remaining local plan links with a focused repository audit instead
   of adding a reusable Markdown-link parser.

This approach fixes demonstrated runtime defects and stale documentation
without changing persisted values, adding a migration, broadening DOCX
sanitization, or reconstructing failed historical work.

## Rejected Alternatives

- **Restore the deleted plans:** Rejected because the files belong to an
  intentionally discarded implementation history and are not authoritative.
- **Create an abandoned-plan archive:** Rejected because it conflicts with the
  clean Task 6 restart and adds maintenance without product value.
- **Delete history without a rollback note:** Rejected because a short marker
  prevents future reviewers from mistaking the intentional cleanup for data
  loss.
- **Allowlist the entire DOCX document root:** Rejected because removing only
  `w:background` is sufficient and carries less compatibility risk.
- **Normalize timestamps on write or migrate the schema:** Rejected because
  ordering by normalized instant fixes the contract while preserving stored
  text and existing APIs.

## Technical Details

- A WordprocessingML background is a `w:background` child of `w:document`, not
  `w:body`. Remove every direct background child before saving generated
  content.
- For an internal background image, the sanitized candidate must omit the
  background XML, relationship, image part, private sentinel bytes, and stale
  content-type override.
- For an external background, remove both the background XML and its external
  relationship. A background with no relationship must also disappear.
- Preserve configured page geometry, styles, visible CV content, hyperlinks,
  package determinism, and all existing sanitization behavior.
- Use Python-normalized UTC instants for ordering so every accepted
  `datetime.fromisoformat` value, including sub-millisecond precision, follows
  the same chronology contract. Existing naive timestamps retain the project's
  UTC compatibility convention.
- Continue returning original stored `completed_at` values through repository
  reads; only list ordering changes.
- Use `id DESC` after the normalized timestamp expression so equivalent
  instants have deterministic order.
- Existing repository validation remains responsible for rejecting malformed
  or backdated timestamps before insertion; no new error type is needed.
- In the parent plan, replace the discarded-iteration sequence between the
  initial Task 6 verification and issue #31 with one authoritative closure
  note. Keep the issue #31 link and completed file unchanged.
- Git history remains the recovery mechanism for intentionally discarded plan
  content.

## What Goes Where

- **Implementation Steps:** Regression tests, focused production fixes,
  rollback-aware documentation cleanup, verification, and plan synchronization
  are tracked below with checkboxes.
- **Post-Completion:** Manual Microsoft Word inspection and GitHub commit, PR,
  and issue updates require external actions and are listed separately.

## Implementation Steps

### Task 1: Remove private document-level backgrounds

**Files:**

- Modify: `tests/test_word_writer.py`
- Modify: `app/documents/word_writer.py`

- [x] Add a failing writer regression using a synthetic `w:background` with a
  linked internal private image and content-type entry.
- [x] Add failing edge regressions for a background without a relationship and
  one with an external relationship.
- [x] Assert candidates remove background XML, background-only relationships,
  internal image parts, stale content-type references, and sentinel bytes.
- [x] Update `_clear_template` to remove direct document-root background
  content before the package sanitizer computes relationship reachability.
- [x] Assert configured page geometry, styles, visible CV content, hyperlinks,
  and deterministic output remain intact.
- [x] Run `uv run pytest tests/test_word_writer.py`; it must pass before Task 2.

### Task 2: Order generation timestamps by normalized instant

**Files:**

- Modify: `tests/test_cv_generator.py`
- Modify: `app/cv_generations.py`

- [x] Add a failing repository regression with two assessments and generations
  whose raw timestamp strings sort opposite to their actual instants.
- [x] Add a failing regression for equivalent instants expressed with
  different offsets and verify deterministic `id DESC` tie-breaking.
- [x] Cover the existing naive-timestamp-as-UTC compatibility behavior in list
  ordering.
- [x] Change `list_for_application` to order by normalized Python instant and
  then
  generation ID without rewriting stored timestamps.
- [x] Assert `get` and list results continue returning original timestamp text
  and all existing metadata.
- [x] Run `uv run pytest tests/test_cv_generator.py`; it must pass before
  Task 3.

### Task 3: Record the intentional Task 6 rollback cleanly

**Files:**

- Modify: `docs/plans/2026-08-06-deliver-ai-assessment-and-one-page-cv-v1.md`
- Modify: this plan file

- [x] Keep the staged deletions from the rolled-back plan set; do not restore,
  recreate, or move those files.
- [x] Remove the verbose failed-remediation sequence and its seven broken links
  from the active Task 6 plan.
- [x] Add one concise authoritative note covering the intentional rollback,
  clean Task 6 restart, shipping evidence-cited draft contract, and retained
  DOCX privacy, determinism, and persistence behavior.
- [x] Preserve the existing issue #31 entry and completed-plan link unchanged.
- [x] Run a focused read-only audit proving every remaining local Markdown plan
  link resolves to an existing file.
- [x] Run `git diff --check`; it must pass before Task 4.

### Task 4: Verify remediation acceptance criteria

**Files:**

- Modify: this plan file

- [x] Verify a candidate built from a background-bearing template contains no
  background XML, relationships, linked assets, or sentinel bytes.
- [x] Verify mixed-offset, equivalent-instant, and naive UTC-compatible
  generation timestamps list in the documented deterministic order.
- [x] Verify the parent Task 6 plan contains the concise rollback note, retains
  issue #31, and has no references to discarded remediation plans.
- [x] Confirm no intentionally deleted plan was restored or recreated.
- [x] Run `uv run pytest tests/test_word_writer.py`.
- [x] Run `uv run pytest tests/test_cv_generator.py`.
- [x] Run `uv run pytest` and record the passing count.
- [x] Run `uv run ruff check .` and `uv run ruff format --check .`.
- [x] Run `uv run mypy app tests scripts`.
- [x] Run `uv run python scripts/generate_ai_schemas.py --check`.
- [x] Run `uv lock --check` and `git diff --check`.

### Task 5: Finalize remediation documentation

**Files:**

- Modify: `docs/plans/2026-08-06-deliver-ai-assessment-and-one-page-cv-v1.md`
- Modify: this plan file
- Move: this plan file to `docs/plans/completed/`

- [x] Record the final background-privacy, timestamp-ordering, and rollback-note
  behavior plus verification results in the parent Task 6 progress notes.
- [x] Confirm README and AGENTS require no changes, or update them if a reusable
  convention was introduced.
- [x] Mark every checklist item complete and move this plan to
  `docs/plans/completed/`.
- [x] Run the focused local-link audit after the documentation move.
- [x] Run `git diff --check` after final documentation updates.

## Verification Results

- `uv run pytest tests/test_word_writer.py` — 35 passed.
- `uv run pytest tests/test_cv_generator.py` — 35 passed.
- `uv run pytest` — 265 passed.
- `uv run ruff check .`, `uv run ruff format --check .`,
  `uv run mypy app tests scripts`,
  `uv run python scripts/generate_ai_schemas.py --check`, `uv lock --check`,
  local Markdown-plan-link audits, and `git diff --check` passed.

## Post-Completion

*Items requiring manual intervention or external systems; no checkboxes.*

**Manual verification:**

- Optionally open a candidate generated from a public synthetic background-
  bearing template in Microsoft Word and confirm no background or watermark is
  visible or reported by Document Inspector.
- Confirm a candidate without a background remains visually unchanged.

**External system updates:**

- Commit on the current feature branch using Conventional Commits and issue
  #32; do not push or merge directly to `master`.
- Open or update a pull request for the remediation.
- Comment on issue #32 with the commit or PR link and final verification
  results after implementation.
