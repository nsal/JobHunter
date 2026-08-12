# Task 12 Fix 1: Correct Acceptance Records and Operator Guidance

## Overview

- Correct the Task 12 completion record so it does not claim that v1 verifies
  deterministic over-page failures when pagination is intentionally manual.
- Repair documentation links made stale or invalid when the parent delivery
  plan moved into `docs/plans/completed/`.
- Remove the README claim that `resource_lease_seconds` provides a private-
  resource lease, because no runtime component consumes that setting.
- Keep the remediation documentation-only. Do not add a renderer, change queue
  behavior, remove tracked settings, or expand the v1 acceptance boundary.

## Context (from discovery)

- Associated GitHub issue:
  [#50](https://github.com/nsal/JobHunter/issues/50).
- The parent delivery plan is currently an uncommitted rename from
  `docs/plans/` to `docs/plans/completed/`, with Task 12 and Task 13 completion
  updates in the same diff.
- Task 7 explicitly removed automated rendering and page-count verification;
  the writer targets A4 but leaves pagination confirmation to the user.
- The archived parent plan nevertheless marks deterministic over-page failure
  verification complete, then immediately states that v1 does not render or
  count pages.
- The move breaks a relative `completed/...` link inside the parent plan, and
  the roadmap still identifies the parent's former active-plan path.
- `resource_lease_seconds` is present in `config/ai.yaml`, `QueueSettings`, and
  settings tests, but dispatcher, runner, provider, and repository code do not
  use it. The newly expanded README incorrectly presents it as active runtime
  behavior.
- The project is Python 3.14 with FastAPI, SQLite, Ruff at 80 columns, mypy,
  pytest, `uv`, and a 477-test baseline.

## Development Approach

- **Testing approach:** Regular, inherited from the parent delivery plan.
- Complete each documentation correction fully before moving to the next
  task, and keep the fix limited to the reviewed defects.
- Preserve the existing uncommitted README and parent-plan work while editing
  only the affected statements and references.
- Do not create production or test code for documentation-only changes. Use
  focused path/link and source-reference checks in each task, then run the
  complete project verification gate before completion.
- Update this plan immediately if implementation reveals a runtime defect that
  requires code or test changes; every such code change must add or update
  success and error-path tests in the same task.
- Do not change `AGENTS.md` unless the remediation establishes a reusable
  project-wide rule.

## Testing Strategy

- **Acceptance-record checks:** search the completed parent plan for over-page,
  pagination, rendering, and page-count statements and confirm they describe
  one consistent manual-review boundary.
- **Link checks:** resolve every local Markdown link in the moved parent plan,
  verify the roadmap points to the completed location, and search for active
  canonical references to the obsolete parent-plan path.
- **Operator-documentation checks:** compare every queue timing/recovery claim
  retained in the README with its runtime call site. Do not describe validated
  but unused settings as operational behavior.
- **Regression gate:** run `uv run pytest`, Ruff check and format check, mypy,
  generated-schema drift, lockfile sync, and Git whitespace checks even though
  this fix changes documentation only.
- **No browser E2E or live provider work:** the remediation does not change UI
  or provider behavior, so no new browser or OpenAI verification is required.

## Progress Tracking

- Mark completed items with `[x]` immediately when done.
- Add newly discovered tasks with a `➕` prefix.
- Record blockers with a `⚠️` prefix and identify the affected criterion.
- Keep paths, commands, and verification results synchronized with the actual
  diff.
- Do not archive this fix plan until every automated gate passes.

## Solution Overview

Treat the review findings as documentation truthfulness and navigation defects:

1. Reword the parent acceptance criterion to record automated layout/package
   checks separately from manual pagination review. Do not represent a
   deferred renderer outcome as verified.
2. Make links location-correct after archival: links between files already in
   `completed/` use same-directory targets, while roadmap and README references
   use the completed parent-plan path.
3. Describe only queue behavior backed by runtime call sites. Remove the
   private-resource lease claim without changing the unused configuration field
   as part of this narrowly scoped fix.

## Technical Details

### Pagination acceptance boundary

- The writer applies deterministic A4 portrait layout settings and validates
  DOCX structure, paths, metadata, and atomic output.
- It does not render the DOCX, count pages, or produce a deterministic
  over-page result.
- The corrected acceptance record must say that page fit remains a manual
  editor check and that automatic rendering/page-count verification is
  deferred.
- Remove or qualify any older parent-plan retry language that still identifies
  a deterministic over-page result as a current v1 failure class.

### Archived documentation links

- The parent plan now lives at
  `docs/plans/completed/2026-08-06-deliver-ai-assessment-and-one-page-cv-v1.md`.
- Its link to the CV metadata remediation is now same-directory and must not
  retain a second `completed/` segment.
- The roadmap's Stage 1 reference must point to the completed parent plan.
- The README's Post-Completion reference should be a direct relative Markdown
  link to the archived parent plan so operators can reach the listed manual
  checks.
- Historical `Files: Modify` entries in already completed fix plans describe
  their implementation-time paths and do not need mechanical rewriting unless
  they are live Markdown links or canonical navigation references.

### Queue documentation boundary

- Runtime-backed defaults include dispatcher concurrency, idle polling,
  heartbeat interval, and work-lease duration.
- Retry behavior is currently fixed by repository state transitions at two
  attempts; avoid implying that an unused configuration field controls it.
- `resource_lease_seconds` remains a validated tracked field for now, but the
  README must not claim that it establishes a lease until a runtime owner and
  behavior are implemented.
- Removing or wiring unused settings is a separate runtime/configuration change
  and is outside this documentation fix.

## What Goes Where

- **Implementation Steps:** parent-plan corrections, README corrections,
  roadmap/link updates, targeted documentation checks, full automated gates,
  verification records, and plan archival achievable in this repository.
- **Post-Completion:** GitHub commit, pull request, and issue coordination that
  depends on repository workflow state after implementation.

## Implementation Steps

### Task 1: Correct the pagination acceptance record

**Files:**

- Modify:
  `docs/plans/completed/2026-08-06-deliver-ai-assessment-and-one-page-cv-v1.md`

- [x] Replace the completed deterministic over-page verification claim with an
  accurate split between automated DOCX/layout checks and manual pagination.
- [x] Align retry/failure language with Task 7's removal of rendering and
  page-count verification.
- [x] Preserve `Ready for review` as an editable-draft state that does not
  assert factual, editorial, pagination, PDF, or submission completion.
- [x] Run focused `rg` checks for `over-page`, `pagination`, `render`, and
  `page-count`; review every match for consistency before Task 2.
- [x] Record the focused documentation verification result in this plan.

Task 1 focused verification: the parent plan now describes deterministic DOCX
structure/layout checks separately from manual pagination. Its retry policy no
longer classifies a deterministic over-page result, and its remaining render,
page-count, and pagination matches consistently defer fit confirmation to the
editor or a future authoritative renderer.

### Task 2: Repair links affected by parent-plan archival

**Files:**

- Modify:
  `docs/plans/completed/2026-08-06-deliver-ai-assessment-and-one-page-cv-v1.md`
- Modify:
  `docs/roadmaps/2026-08-06-ai-assessment-and-tailored-cv-generation.md`
- Modify: `README.md`

- [x] Change the parent plan's nested `completed/...` link to a valid
  same-directory link.
- [x] Point the roadmap's Stage 1 reference to the completed parent-plan path.
- [x] Link the README's Post-Completion guidance directly to the archived
  parent plan.
- [x] Validate every local Markdown link in the moved parent plan resolves to
  an existing file and search canonical navigation docs for the obsolete path.
- [x] Record the focused link verification result in this plan before Task 3.

Task 2 focused verification: the moved parent plan's local Markdown link
resolves to `docs/plans/completed/2026-08-09-fix-cv-metadata-timestamps-and-
filenames.md`. The roadmap and README use the archived parent-plan path; no
canonical navigation document retains the obsolete active-plan link.

### Task 3: Remove unsupported queue-behavior guidance

**Files:**

- Modify: `README.md`

- [x] Remove the claim that `resource_lease_seconds` implements a 180-second
  private-resource lease.
- [x] Retain only timing, concurrency, retry, and recovery statements supported
  by dispatcher, runner, and work-repository call sites.
- [x] Keep the change documentation-only; do not remove or wire
  `resource_lease_seconds` in this fix.
- [x] Search runtime call sites for each retained queue-behavior statement and
  record the focused source-reference verification result in this plan.
- [x] Confirm the README remains formatted at no more than 80 columns before
  Task 4.

Task 3 focused verification: dispatcher call sites support the three-worker
limit, one-second polling, lease assignment, and lease recovery; the runner
supports ten-second heartbeats; and `WorkRepository.fail`/`recover_stale`
support the bounded two-attempt retry behavior. The README contains no
`resource_lease_seconds`, private-resource lease, or 180-second lease claim.
All prose lines in `README.md` are at most 80 columns; the required direct
archived-plan URL is retained as a single Markdown link destination.

### Task 4: Verify acceptance criteria and engineering standards

**Files:**

- Modify: `docs/plans/2026-08-11-task-12-fix-1-correct-docs.md`

- [x] Verify the parent plan no longer claims automated over-page detection or
  failure handling in v1.
- [x] Verify all parent-plan local Markdown links resolve and canonical roadmap
  and README references use the archived path.
- [x] Verify README queue behavior is backed by runtime call sites and contains
  no private-resource lease claim.
- [x] Run `uv run pytest` and record the complete passing count.
- [x] Run `uv run ruff check .` and `uv run ruff format --check .`.
- [x] Run `uv run mypy app tests scripts` and
  `uv run python scripts/generate_ai_schemas.py --check`.
- [x] Run `uv lock --check` and `git diff --check`.

Task 4 verification: focused acceptance, link, and source-reference checks
passed. The complete repository gate passed: `uv run pytest` reported 477
passed; Ruff check and format check passed; mypy reported no issues in 64
source files; generated-schema drift, `uv lock --check`, and `git diff
--check` passed.

### Task 5: Record the remediation and archive the fix plan

**Files:**

- Modify:
  `docs/plans/completed/2026-08-06-deliver-ai-assessment-and-one-page-cv-v1.md`
- Modify: `README.md` only if final verification changes operator guidance
- Modify: `AGENTS.md` only if a reusable project-wide rule is discovered
- Move: `docs/plans/2026-08-11-task-12-fix-1-correct-docs.md` to
  `docs/plans/completed/`

- [x] Add the Task 12 Fix 1 focused and full verification results to the parent
  plan without reintroducing the inaccurate acceptance claims.
- [x] Confirm no README or AGENTS change beyond the scoped corrections is
  required.
- [x] Confirm every checklist item is complete and move this plan to
  `docs/plans/completed/`.
- [x] Keep the associated issue, commit, and pull-request references
  synchronized through the repository workflow.

Task 5 completion: the parent plan records the documentation remediation and
the full verification results. No runtime or configuration changes were
needed. This fix plan is ready to archive; commit, pull request, and issue
comment links remain repository workflow actions for the associated issue.

## Post-Completion

*These items require external repository state and are informational here.*

**External system and repository actions:**

- Use the associated GitHub issue number in the Conventional Commit message.
- Push only the dedicated feature/fix branch and open a pull request; do not
  push or merge directly to `master`.
- Comment on the issue with the completed commit or pull-request link.
