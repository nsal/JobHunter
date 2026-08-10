# Deliver AI assessment and evidence-cited one-page CV draft v1

## Overview

- Deliver one complete local workflow from immutable job description through
  evidence-cited assessment, deterministic scoring, tailored draft CV content,
  and deterministic DOCX generation targeted at one A4 page.
- Keep FastAPI responsive by placing durable work in SQLite and running it
  through a separately supervised dispatcher with up to three spawned workers.
- Start new applications before submission, show assessment/work progress, and
  generate automatically for matches or explicitly after a mismatch override.
- Replace the current CV upload/preview/download behavior with generated
  private artefacts and a safe Finder handoff.
- Keep v1 intentionally narrow: OpenAI only, the current raw Markdown profile,
  one private DOCX template, small private YAML layout settings, no profile
  indexing, and no automated CV refitting.
- Treat generated CV content and DOCX output as an editable AI draft requiring
  human factual, editorial, and pagination review before PDF export and use.
  Defer semantic fact-level grounding and automated final-PDF rendering to
  later features.

## Context (from discovery)

- JobHunter is a local Python 3.14 FastAPI/Jinja/SQLite application. Routes and
  wiring are concentrated in `app/main.py`, schema creation in
  `app/database.py`, and persistence in `app/repository.py`.
- The fresh schema has `applications`, immutable stage history, assessments,
  and CV generations. New applications begin at `Assessing`; `Submitted` is a
  later manual transition.
- The current application supports CV uploads through `app/cv_uploads.py`,
  path validation/delivery through `app/cv_files.py`, and dashboard/detail CV
  actions. These features and their tests will be removed, not preserved.
- There is no production data. The schema may be rebuilt directly; ordered
  migrations and legacy-data compatibility are out of scope for v1.
- The project has no AI SDK, document library, queue, process supervisor,
  configuration layer, or browser E2E suite. Tests use pytest, httpx2,
  temporary SQLite databases, FastAPI lifespan fixtures, and Jinja response
  assertions.
- `private/*` is already ignored. Private profile content, consent state,
  templates, settings, and generated artefacts must remain under that tree.
- `pyproject.toml` requires Ruff at 80 columns. Project instructions require
  `uv`, complete type hints, mypy, and a passing pytest suite.
- The reviewed source design is retained as the staged roadmap at
  `docs/roadmaps/2026-08-06-ai-assessment-and-tailored-cv-generation.md`.

## Development Approach

- **Testing approach:** Regular: implement each focused unit, add its success
  and error-path tests, then run the full suite before beginning the next task.
- Complete each task fully and mark its checklist immediately before moving to
  the next task.
- Every task that changes code must add or update tests for every new and
  modified path. Do not defer tests to a later task.
- Run `uv run pytest` after each task and do not continue while it is failing.
- Use deterministic fakes for OpenAI, clocks, and process launching in the
  ordinary test suite.
- Keep provider-specific code behind a narrow structured-generation protocol.
  Domain services must depend on the protocol, not the OpenAI adapter.
- Keep scoring, lifecycle decisions, path validation, citation integrity, and
  DOCX layout configuration deterministic after structured model output is
  returned.
- Use atomic file replacement and short SQLite write transactions. Child
  processes open their own database/provider resources and receive IDs/tokens
  only.
- Use `uv add` for dependencies, update `uv.lock`, and use Ruff only for Python
  linting and formatting.
- Update this plan immediately if implementation changes a file name, schema,
  workflow boundary, or acceptance criterion.
- Backward compatibility is intentionally not required for the development
  database or legacy CV behavior because no production data exists.

## Testing Strategy

- **Schema/repository tests:** Create only fresh temporary databases. Verify
  constraints, nullable submission dates, immutable history, atomic work
  creation, active-work uniqueness, worker tokens, retries, and
  lifecycle transitions.
- **Unit tests:** Cover settings, path containment, Markdown block IDs, schema
  validation, evidence references, score arithmetic, hard gates, safe
  filenames, DOCX structure, checkpoint hashes, and retry classification.
- **Provider contract tests:** Mock OpenAI calls. Verify structured success,
  one repair attempt, timeouts, rate limits, metadata, consent enforcement, and
  secret/private-text redaction.
- **Service integration tests:** Use fake providers and private temporary roots
  to run assessment and generation services through success, mismatch,
  override, technical failure, and resume paths.
- **Process tests:** Inject fake clocks and process launchers for normal tests.
  Add one bounded real-spawn smoke test proving child SQLite isolation.
- **Document tests:** Inspect DOCX structure, layout settings, safe metadata,
  and atomic output in the normal suite. Tests do not claim to render or count
  pages; one-page suitability is reviewed by the user in their editor.
- **Route/UI tests:** Use httpx2/Jinja assertions for setup readiness, consent,
  immediate redirects, polling, statuses, immutable JDs, mismatch override,
  retries, dashboard badges, and Finder actions.
- **Synthetic model cases:** Commit non-sensitive profile/JD fixtures covering
  mandatory requirements, evidence matches, hard gates, invalid citations,
  exact cited identity values, confidentiality rejection, and score boundaries.
- **No browser E2E framework:** Do not add Playwright or similar solely for v1;
  record focused manual browser checks under Post-Completion.
- **Final automated gate:** Run `uv run pytest`, `uv run ruff check .`,
  `uv run ruff format --check .`, `uv run mypy app tests`, the schema drift
  check, and `uv lock --check`.

## Progress Tracking

- Mark completed items with `[x]` immediately when done.
- Add newly discovered work with a `➕` prefix.
- Add blockers with a `⚠️` prefix and identify the affected acceptance
  criterion.
- Keep this file synchronized with actual paths, schema, commands, and results.
- Record focused and full-suite results after every task.
- Do not archive the plan until all automated gates pass and the manual DOCX
  review/PDF handoff boundary is recorded.

## Solution Overview

JobHunter v1 uses a fixed two-work-item workflow:

```text
Current private profile.md + immutable application JD
                         |
                         v
                  assessment work
                         |
          structured requirements/evidence
                         |
                         v
             deterministic validation/scoring
                    |              |
                 mismatch        matched
                    |              |
          explicit override        v
                    +------> CV generation work
                                      |
                            evidence-cited CV draft
                                      |
                            template + layout YAML
                                      |
                                  DOCX writer
                                      |
                         Ready for review
                                      |
                         human review/amendment
                                      |
                         manual PDF export
```

One `jobhunter` launcher supervises FastAPI/Uvicorn and a dispatcher as
separate long-lived processes. FastAPI writes durable work and returns
immediately. The dispatcher claims SQLite work and runs at most three spawned
children. Workers open independent resources. Document generation ends after
the editable DOCX is written and persisted; there is no renderer or desktop
automation boundary in v1.

The assessment worker makes one structured OpenAI call over numbered profile
and JD blocks. Application code validates every reference and computes the
score and outcome. The CV worker makes one structured call for evidence-cited
draft `CvContent` and applies it deterministically to a private template. The
writer targets one A4 page through bounded content and layout settings, but the
candidate remains an editable draft requiring human factual, editorial, and
pagination review. There is no automatic fit-revision or PDF-rendering call in
v1.

## Technical Details

### Scope and private inputs

- Read `private/profile/profile.md` when a worker starts. Record its hash, but
  do not retain a snapshot or create a Profile Index.
- Send the raw Markdown as bounded source blocks without inferring a typed
  customer profile. Structured Markdown/JSON/YAML profile parsing and semantic
  fact IDs belong to the later customer-profile feature.
- Read `private/profile/cv-template.docx` and
  `private/profile/cv-layout.yaml` for generation. The YAML contains bounded,
  machine-readable page, margin, style, font, spacing, and output settings; it
  does not contain API credentials or prompt text.
- Keep OpenAI capability/model routing, timeouts, queue timings, concurrency,
  scoring threshold, and taxonomy version in tracked `config/ai.yaml`.
- Read `OPENAI_API_KEY` from the environment only.
- Store one-time OpenAI profile-sharing acknowledgement in the private SQLite
  database and require it before queueing profile-bearing OpenAI work.
- Do not persist complete prompts or raw OpenAI responses by default.

### Fresh persistence model

- Rebuild the development schema around `applications`,
  `application_stage_history`, `consents`, `work_items`, `assessments`,
  and `cv_generations`.
- Add `applications.created_at`, required immutable `full_jd`, and a unique,
  stable, relative `artefact_directory`. Remove `cv_path`.
- Add lifecycle stages `Assessing`, `Mismatch`, and `Ready for review` to the
  existing post-submission stages. New applications begin at `Assessing`.
- Derive `submitted_date` from the first real `Submitted` transition and show
  an em dash when it does not exist.
- Keep assessment and generation records immutable after successful
  completion. Retries update their associated work/checkpoint state rather
  than overwriting a successful historical record.
- Enforce at most one queued/running work item per application with a partial
  unique index.

### Structured outputs and scoring

- Deterministically split Markdown/JD headings, paragraphs, list groups, and
  tables into bounded blocks with stable IDs and content hashes.
- Define strict versioned models and committed JSON Schemas for
  `AssessmentResult` and `CvContent`; reject extra fields and unbounded text or
  collections.
- `AssessmentResult` contains cited JD requirements, mandatory flags, cited
  profile evidence, match/gap classifications, supporting categories, hard
  gates, and a concise analysis. Model output never supplies the final score or
  lifecycle transition.
- Use fixed v1 supporting categories and weights in application code. Validate
  that weights total 100 and calculate:

  ```text
  mandatory coverage = matched mandatory / total mandatory
  final score = supporting alignment * mandatory coverage
  ```

- Treat explicit salary/location, remote-policy, work-authorization,
  clearance, and excluded-business contradictions as hard gates. Unknown or
  ambiguous facts are visible gaps, not failures.
- Generate automatically only when the threshold is met, all mandatory
  requirements match, and no hard gate fails.
- Require every CV identity value and claim to cite validated profile blocks,
  and require claim citations to remain within assessment-allowed evidence.
- Require each identity/contact value to occur exactly in at least one cited
  block without attempting to classify arbitrary Markdown as a phone, URL,
  organization, title, date, credential, skill, or metric.
- Reject configured confidential language, but do not claim deterministic
  semantic validation of generated prose. The provider instructions and
  mandatory human review own factual completeness in v1.

### Work, recovery, and lifecycle

- Work states are `queued`, `running`, `succeeded`, and `failed`; work types are
  `assessment` and `cv_generation`.
- Store attempt count, availability, current step, worker token, profile/JD and
  checkpoint hashes, timestamps, heartbeat/lease data, and a sanitized error.
- Claim work transactionally. Only the current worker token may heartbeat,
  checkpoint, or finalize it.
- Retry transient provider/process timeouts once. Do not retry invalid
  configuration, unsafe paths, invalid citations or cited identity values,
  exhausted schema repair, or a deterministic over-page result.
- Validate and atomically replace checkpoint files before marking their step
  complete. Reuse assessment output before scoring and `cv-content.json`
  before rendering when all relevant hashes still match.
- Because profile snapshots are excluded, a changed profile hash invalidates a
  checkpoint and requires a new assessment.
- Initial mismatch moves `Assessing` to `Mismatch`. A passing assessment stays
  `Assessing` until deterministic DOCX generation moves it to `Ready for
  review`.
- `Ready for review` means the editable draft artefact was generated
  successfully and targeted at one A4 page; it does not claim factual,
  editorial, pagination, PDF-export, or submission completion.
- Successful override generation moves `Mismatch` to `Ready for review`
  without changing the original score/outcome. Technical failures leave the
  current lifecycle unchanged.

### Artefacts and DOCX generation

- Store each application under a safe stable relative directory beneath
  `private/artefacts/<company>/<yyyy-mm-dd_role>/`, appending the application
  ID on collision.
- Store assessment files beneath `assessments/<assessment-id>/` and generation
  files beneath `cv-generations/<generation-id>/`.
- Use atomic sibling writes, containment checks, symlink rejection, and safe
  portable path segments for every file operation.
- Generate `<First Name> <Last Name> - <Job Title>.docx` from exactly cited
  profile identity and the user-entered application role.
- Apply the private template/YAML deterministically; the writer never rewrites
  model content.
- Target one A4 portrait page through the configured dimensions, margins,
  fonts, spacing, and bounded content. Persist the editable DOCX atomically.
- Do not render the DOCX, count PDF pages, or record renderer metadata in v1.
  The user opens and amends the DOCX, confirms pagination manually, exports the
  final PDF, submits it, and records `Submitted` through the stage editor.

### User interface

- Add a Setup page showing private-file readiness, selected OpenAI model, and
  the one-time remote-profile acknowledgement.
- Require a ready private profile/template/layout, acknowledgement, role,
  company, and full JD before atomically creating an application, assessment,
  and queued work item.
- Remove JD editing after creation while keeping it visible; keep ordinary
  application metadata and notes editable.
- Show lifecycle separately from queue/current-step status. Poll only while
  work is queued/running and stop on terminal state.
- Show score, mandatory coverage, outcome, concise analysis, gaps, model, and
  timestamps after assessment.
- Offer `Generate CV anyway` only for a completed mismatch and Retry only for a
  failed work item. A successful generation exposes the editable draft as
  `Ready for review`.
- Replace the dashboard CV column and all old CV controls with assessment/work
  status and `Open artefacts`.
- Implement Finder opening as a POST-only, origin-checked endpoint that resolves
  the persisted relative directory, revalidates containment, and invokes
  macOS `open` with an argument array and no shell.

## What Goes Where

- **Tracked behavior:** Python modules, migrations-free fresh schema,
  provider-neutral interfaces, OpenAI adapter, schemas, prompts, tracked
  routing, public examples, tests, and documentation.
- **Private behavior/data:** active profile, layout settings, DOCX template,
  SQLite database/consent, assessment artefacts, CV content, candidate DOCX
  files, and temporary verification output under `private/`.
- **Implementation Steps:** repository changes, tests, generated schemas,
  public fixtures/examples, and automated verification achievable here.
- **Post-Completion:** real credentials/models, private profile/template setup,
  live model verification, manual browser/editor inspection, and GitHub
  issue/PR coordination.

## Implementation Steps

### Task 1: Rebuild lifecycle schema and remove legacy CV behavior

**Files:**
- Modify: `app/database.py`
- Modify: `app/repository.py`
- Modify: `app/main.py`
- Delete: `app/cv_files.py`
- Delete: `app/cv_uploads.py`
- Modify: `app/templates/applications/_application_form.html`
- Modify: `app/templates/applications/_application_edit_form.html`
- Modify: `app/templates/applications/_application_row.html`
- Modify: `app/templates/applications/index.html`
- Modify: `app/templates/applications/detail.html`
- Modify: `tests/test_database.py`
- Modify: `tests/test_repository.py`
- Modify: `tests/test_routes.py`
- Rename: `tests/test_dashboard_cv_actions.py` to
  `tests/test_dashboard_actions.py`
- Delete: `tests/test_cv_uploads.py`

- [x] Replace the fresh schema with `applications` and
  `application_stage_history`, add pre-submission stages, require `full_jd`,
  add `created_at`, and remove `cv_path`.
- [x] Create applications at `Assessing`; permit a later explicit `Submitted`
  transition and derive nullable `submitted_date` from its first occurrence.
- [x] Make the JD immutable after creation while preserving edits for supported
  application metadata and notes.
- [x] Remove CV upload/storage/delivery code, routes, form fields, dashboard
  controls, detail controls, and obsolete tests.
- [x] Write/update schema and repository tests for fresh constraints,
  `Assessing` creation, nullable submission, explicit submission, immutable JD,
  stage ordering, and success paths.
- [x] Write/update route/template tests for required JD, removed CV behavior,
  edit behavior, invalid transitions, and missing applications.
- [x] Run `uv run pytest`; record the passing count before task 2.

Task 1 verification: `uv run pytest` — 46 passed.

### Task 2: Add typed settings, private input validation, and artefact storage

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `config/ai.yaml`
- Create: `app/settings.py`
- Create: `app/artefacts.py`
- Modify: `app/database.py`
- Modify: `app/repository.py`
- Create: `examples/profile/README.md`
- Create: `examples/profile/profile.example.md`
- Create: `examples/profile/cv-layout.example.yaml`
- Create: `examples/profile/cv-template.example.docx`
- Create: `tests/test_settings.py`
- Create: `tests/test_artefacts.py`
- Modify: `tests/test_repository.py`

- [x] Add direct bounded dependencies with `uv add` for OpenAI, Pydantic, YAML,
  Markdown tokenization, and DOCX generation; update the lockfile.
- [x] Define typed tracked settings for OpenAI routing/timeouts, scoring
  threshold/taxonomy version, queue polling, heartbeat, lease, and concurrency
  three; reject secrets in tracked YAML.
- [x] Validate the active private `profile.md`, `cv-template.docx`, and
  `cv-layout.yaml` paths, size/type limits, and bounded layout values without
  parsing the profile into a normalized index.
- [x] Implement stable application artefact-directory allocation, safe segment
  normalization, relative storage, containment checks, symlink rejection,
  hashing, and atomic JSON/text/binary writes.
- [x] Add the unique relative artefact directory to application persistence and
  keep it stable after role/company edits.
- [x] Publish non-private setup/profile/layout/template examples and confirm
  `private/*` remains ignored.
- [x] Write settings/input tests for defaults, overrides, missing/corrupt
  files, unknown providers, invalid timing/layout relationships, and secret-like
  tracked keys.
- [x] Write artefact/repository tests for ordinary paths, collisions, Unicode,
  reserved names, stable edits, traversal, unsafe symlinks, atomic-write
  failure, and round trips.
- [x] Run `uv run pytest`; record the passing count before task 3.

Task 2 verification: `uv run pytest` — 74 passed. `uv run ruff check .`,
`uv run ruff format --check .`, `uv run mypy app tests`, and
`uv lock --check` also passed.

### Task 3: Define strict AI contracts and deterministic source blocks

**Files:**
- Create: `app/ai/__init__.py`
- Create: `app/ai/schema_models.py`
- Create: `app/ai/source_blocks.py`
- Create: `app/ai/schemas/v1/assessment-result.json`
- Create: `app/ai/schemas/v1/cv-content.json`
- Create: `scripts/__init__.py`
- Create: `scripts/generate_ai_schemas.py`
- Create: `tests/test_ai_schemas.py`
- Create: `tests/test_source_blocks.py`

- [x] Define strict, versioned Pydantic models for cited requirements,
  evidence matches, supporting categories, hard gates, `AssessmentResult`, CV
  sections/claims, and `CvContent`.
- [x] Forbid extra fields, bound all text/collections, use stable enums, and
  model JD/profile references explicitly.
- [x] Deterministically tokenize Markdown and plain JD text into bounded blocks
  with stable IDs, order, source kind, and content hashes.
- [x] Generate and commit versioned JSON Schemas and add a `--check` mode that
  fails on model/schema drift.
- [x] Implement cross-record validation for duplicate/dangling requirement,
  block, evidence, and CV-claim references.
- [x] Write schema/block tests for representative valid results, stable block
  IDs/hashes, headings, tables, lists, long content, and round trips.
- [x] Write error tests for extra fields, invalid enums, duplicate/dangling
  references, malformed UTF-8, and oversized inputs/outputs.
- [x] Run the schema drift check and `uv run pytest`; record results before
  task 4.

Task 3 verification: `uv run pytest` — 100 passed. `uv run python
scripts/generate_ai_schemas.py --check`, `uv run ruff check .`, `uv run ruff
format --check .`, `uv run mypy app tests scripts`, and `uv lock --check` also
passed.

Post-review hardening verification: `uv run pytest` — 120 passed. `uv run
pytest tests/test_ai_schemas.py`, `uv run pytest tests/test_source_blocks.py`,
`uv run python scripts/generate_ai_schemas.py --check`, `uv run ruff check .`,
`uv run ruff format --check .`, `uv run mypy app tests scripts`, `uv lock
--check`, and `git diff --check` also passed.

Follow-up review verification: Unicode separators are also preserved at bounded
source-block split positions. `uv run pytest` — 127 passed; the 24-test focused
source-block suite, schema drift check, Ruff, mypy, lockfile, and Git whitespace
checks also passed.

### Task 4: Implement the OpenAI structured-generation adapter and consent

**Files:**
- Create: `app/ai/providers/__init__.py`
- Create: `app/ai/providers/base.py`
- Create: `app/ai/providers/openai.py`
- Create: `app/ai/providers/factory.py`
- Create: `app/consent.py`
- Modify: `app/database.py`
- Create: `tests/test_ai_providers.py`
- Create: `tests/test_consent.py`
- ➕ Modify: `tests/test_database.py`

- [x] Define a narrow typed structured-generation request/result protocol with
  model, schema, timeout, usage, response identifiers, and sanitized metadata.
- [x] Implement the OpenAI adapter using environment-only credentials and the
  configured model/options without leaking provider code into domain services.
- [x] Support schema-enforced output, local validation, and exactly one repair
  request containing bounded validation errors.
- [x] Normalize timeout, rate-limit, connection, server, authentication, and
  invalid-output failures into retryable or deterministic domain errors.
- [x] Add private-database consent storage and enforce the current one-time
  OpenAI profile-sharing acknowledgement before any profile-bearing request.
- [x] Redact credentials, profile/JD bodies, complete prompts, and raw provider
  responses from logs, exceptions, and persisted errors.
- [x] Write mocked provider tests for native success, repaired success,
  metadata, timeout/rate/server errors, missing credentials, one repair only,
  and redaction.
- [x] Write consent tests for absent, granted, revoked, and repeated
  acknowledgement behavior.
- [x] Run `uv run pytest`; record the passing count before task 5.

Task 4 verification: `uv run pytest` — 157 passed. `uv run ruff check .`,
`uv run ruff format --check .`, `uv run mypy app tests scripts`, `uv run python
scripts/generate_ai_schemas.py --check`, `uv lock --check`, and `git diff
--check` also passed.

### Task 5: Implement deterministic scoring and assessment execution

**Files:**
- Create: `app/assessment/__init__.py`
- Create: `app/assessment/taxonomy.py`
- Create: `app/assessment/scoring.py`
- Create: `app/assessment/service.py`
- Create: `app/ai/instructions/assessment.md`
- Create: `app/assessments.py`
- Modify: `app/database.py`
- Create: `tests/fixtures/assessment_cases.py`
- Create: `tests/test_scoring.py`
- Create: `tests/test_assessment_service.py`

- [x] Define versioned fixed supporting categories/weights that total 100,
  mandatory coverage, deterministic rounding, threshold comparison, and stable
  outcome precedence.
- [x] Evaluate cited salary/location, remote, authorization, clearance, and
  excluded-business contradictions as hard gates; retain ambiguity as a gap.
- [x] Build one bounded assessment request from current profile/JD blocks and
  validate every model-returned block, requirement, category, and gate
  reference before scoring.
- [x] Persist immutable assessment metadata plus atomic
  `assessment-result.json` and `match-analysis.json` artefacts without storing
  complete prompts or raw responses.
- [x] Record the profile/JD/model/schema/instruction/taxonomy hashes and keep
  technical failures distinct from mismatch outcomes.
- [x] Write scoring tests for no mandatory requirements, partial/full mandatory
  coverage, threshold boundaries, category arithmetic, deterministic rounding,
  simultaneous gates, ambiguity, and outcome precedence.
- [x] Write fake-provider service tests for matched, skill mismatch,
  salary/location mismatch, other mismatch, unsupported references, changed
  inputs, provider errors, repair exhaustion, and atomic-write failure.
- [x] Run `uv run pytest`; record the passing count before task 6.

Task 5 verification: `uv run pytest` — 195 passed. `uv run ruff check .`,
`uv run ruff format --check .`, and `uv run mypy app tests scripts` also
passed.

### Task 6: Generate evidence-cited CV drafts and deterministic DOCX candidates

**Files:**
- Create: `app/cv/__init__.py`
- Create: `app/cv/generator.py`
- Create: `app/documents/__init__.py`
- Create: `app/documents/word_writer.py`
- Create: `app/ai/instructions/cv-generator.md`
- Create: `app/cv_generations.py`
- Modify: `app/database.py`
- ➕ Modify: `app/settings.py`
- ➕ Modify: `examples/profile/cv-layout.example.yaml`
- Create: `tests/test_cv_generator.py`
- Create: `tests/test_word_writer.py`
- ➕ Modify: `tests/test_database.py`
- ➕ Create: `tests/fixtures/profile_master.md`

- [x] Build one bounded CV request from the validated assessment, current
  profile blocks, application role, and allowed evidence only.
- [x] Limit deterministic draft validation to valid/allowed block references,
  exact cited identity/contact occurrence, configured confidential wording,
  and unchanged-input hashes.
- [x] Remove semantic phone, organization, proper-name, title, credential,
  date, metric, and claim-boundary inference from the v1 trust boundary.
- [x] Keep the current raw Markdown block pipeline and public `CvContent`
  schema; do not add a profile index, structured profile parser, fact IDs,
  sidecar JSON/YAML, migration adapter, or new dependency.
- [x] Update the CV-generation instructions to require faithful copying,
  complete citations, preserved attribution/disclosure limits, and explicit
  treatment of the result as a human-reviewed draft.
- [x] Persist immutable generation metadata and atomic `cv-content.json` under
  a generation-specific directory.
- [x] Parse and validate the private template/layout settings, then render
  CvContent deterministically without rewriting its text.
- [x] Apply configured page size, margins, styles, fonts, spacing, bullets,
  links, section order, and safe document metadata.
- [x] Produce an atomic candidate named
  `<First Name> <Last Name> - <Job Title>.docx` from exactly cited profile
  identity and the application role.
- [x] Replace semantic-parser regressions with tests for the reduced contract:
  exact cited identities, invalid/forbidden citations, confidential wording,
  changed inputs, and arbitrary narrative claims retained as cited drafts.
- [x] Add a realistic public master-style Markdown fixture with an
  international phone, multi-token organizations, projects, achievements,
  disclosure limits, and long narrative sections.
- [x] Add a fixed-provider service test proving that the master-style fixture
  completes assessment-to-draft generation without semantic profile parsing.
- [x] Preserve writer tests for unsafe names, malformed/corrupt template or
  YAML, missing styles, unavailable fonts, conflicting settings, unsafe output
  paths, private-part pruning, and effects-aware styles.
- [x] Run `uv run pytest tests/test_cv_generator.py` and `uv run pytest
  tests/test_word_writer.py`; both focused suites must pass.
- [x] Run `uv run pytest`; record the passing count before task 7.

Task 6 verification: `uv run pytest` — 211 passed. `uv run ruff check .`,
`uv run ruff format --check .`, `uv run mypy app tests scripts`, `uv run
python scripts/generate_ai_schemas.py --check`, `uv lock --check`, and `git
diff --check` also passed.

Task 6 rollback and shipping baseline: the discarded remediation iterations
were intentionally rolled back before a clean Task 6 restart, so their archive
links remain deleted. The authoritative v1 contract is evidence-cited CV drafts
with retained DOCX privacy, reachability, rendering, determinism, and serialized
persistence behavior. The issue #31 record below remains the authoritative
history for its completed remediation.

Task 6 issue #31 remediation: the completed [CV metadata, chronology, and
filename remediation](completed/2026-08-09-fix-cv-metadata-timestamps-and-filenames.md)
now resets all surviving section properties before applying layout, validates
ISO-8601 generation chronology against assessment and stage timestamps inside
the locked persistence transaction, and bounds candidate filenames for both
Unicode characters and atomic temporary-file bytes. Final verification:
`uv run pytest tests/test_cv_generator.py` — 31 passed; `uv run pytest
tests/test_word_writer.py` — 32 passed; `uv run pytest` — 258 passed; `uv run
ruff check .`, `uv run ruff format --check .`, `uv run mypy app tests scripts`,
`uv run python scripts/generate_ai_schemas.py --check`, `uv lock --check`, and
`git diff --check` passed. README and AGENTS require no changes.

Task 6 background privacy and timestamp-ordering remediation: generated
candidates now remove document-level `w:background` content before package
reachability pruning, including private internal assets and external links.
Completed generations now list by normalized UTC instant with `id DESC` ties,
while preserving the original stored timestamp text. The intentional rollback
note replaces discarded-plan links. Verification: Word writer — 35 passed; CV
generator — 35 passed; full suite — 265 passed; Ruff, mypy, schema check,
lockfile check, local-link audit, and diff check passed. README and AGENTS
require no changes.

### Task 7: Generate one-page-targeted DOCX drafts for manual review

**Files:**
- Modify: `app/documents/__init__.py`
- Modify: `app/documents/word_writer.py`
- Modify: `tests/test_word_writer.py`
- Modify: `app/database.py`
- Modify: `tests/test_database.py`
- Modify: `tests/test_repository.py`
- Modify: `tests/test_cv_generator.py`
- Modify: `tests/test_routes.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`

- [x] Rename `Ready to apply` to `Ready for review` in the fresh lifecycle
  allowlist and all generation, repository, and route fixtures.
- [x] Preserve manual `Submitted` transitions and derive the submitted date
  from the first real transition after human review.
- [x] Remove the unused Word verifier, AppleScript adapter, PDF dependency,
  macOS pytest marker, renderer metadata, and Word resource-boundary surface.
- [x] Keep `CandidateDocument`, `WordWriter`, and `WordWriterError` as the
  supported document package exports.
- [x] Keep deterministic A4 portrait DOCX generation, safe paths, atomic
  writes, malformed-input failures, and unavailable-font coverage. These tests
  establish the writer contract and do not claim to prove rendered page count.
- [x] Run focused lifecycle, generation, route, and writer tests before task 8.

Task 7 Fix 3 verification: the focused lifecycle, generation, route, and DOCX
writer suite passed after the manual-review boundary was implemented. Automatic
PDF rendering and page-count verification are deferred until a future feature
selects an authoritative renderer.

### Task 8: Add durable work state, checkpoints, and retries

**Files:**
- Create: `app/work/__init__.py`
- Create: `app/work/models.py`
- Create: `app/work/repository.py`
- Modify: `app/database.py`
- Modify: `app/repository.py`
- Modify: `app/assessments.py`
- Modify: `app/cv_generations.py`
- Create: `tests/test_work_repository.py`
- Modify: `tests/test_repository.py`
- Modify: `tests/test_assessment_service.py`

- [ ] Add `work_items` with typed/check-constrained
  states, work types, attempts, availability, steps, tokens, heartbeats,
  timestamps, checkpoint hashes, and sanitized errors.
- [ ] Enforce one queued/running work item per application and transactional
  claim, heartbeat, checkpoint, completion, failure, delayed retry, and stale
  recovery operations.
- [ ] Implement one transient retry and deterministic failure classification;
  reject stale tokens and make completion/failure idempotent.
- [ ] Keep document generation independent of desktop applications and
  finalize work after atomic DOCX persistence.
- [ ] Atomically create application, initial assessment, and queued assessment
  work; atomically enqueue automatic generation after a matched assessment.
- [ ] Implement mismatch override generation while preserving the original
  assessment and rejecting duplicate active work.
- [ ] Reuse validated assessment/CvContent checkpoints only when profile, JD,
  prompt, schema, template, and layout hashes still match.
- [ ] Write work/repository tests for ordinary state transitions, concurrency
  constraints, duplicate work, stale tokens, heartbeat expiry, retry
  exhaustion, checkpoint reuse/invalidation, atomic initial work, automatic
  generation, and override generation.
- [ ] Write error tests for interrupted writes, deterministic failures,
  idempotent finalization, and unchanged lifecycle after technical failure.
- [ ] Run `uv run pytest`; record the passing count before task 9.

### Task 9: Build the supervised launcher, dispatcher, and spawned workers

**Files:**
- Create: `app/work/dispatcher.py`
- Create: `app/work/runner.py`
- Create: `app/cli.py`
- Modify: `app/main.py`
- Modify: `pyproject.toml`
- Create: `tests/test_dispatcher.py`
- Create: `tests/test_runner.py`
- Create: `tests/test_cli.py`

- [ ] Add a `jobhunter` CLI that supervises FastAPI/Uvicorn and one dispatcher
  as separate long-lived processes and propagates startup/shutdown failures.
- [ ] Let the dispatcher claim eligible work, spawn up to three children using
  `spawn` semantics, refill slots immediately after exits, and use the
  configured idle poll interval only when no slot/work event occurs.
- [ ] Pass work IDs/tokens only; create SQLite, settings, provider, artefact,
  assessment, and CV resources inside each worker.
- [ ] Dispatch assessment and CV-generation step pipelines with heartbeat,
  checkpoint, retry, and lifecycle integration.
- [ ] Recover abandoned running work after bounded expiry and persist safe
  spawn/crash/exit diagnostics.
- [ ] Support graceful termination without accepting new work, orphaning
  children, or sharing inherited SQLite connections.
- [ ] Write fake-clock/launcher tests for idle polling, burst work, concurrency
  three, immediate reuse, mixed work types, crash/retry, restart recovery, and
  graceful shutdown without desktop automation.
- [ ] Write runner tests for independent resource construction, step resume,
  matched/mismatch/override lifecycle, deterministic failure, and redaction.
- [ ] Add a bounded real-spawn smoke test proving child SQLite isolation and
  completion; keep it deterministic and free of OpenAI/desktop calls.
- [ ] Run `uv run pytest`; record the passing count before task 10.

### Task 10: Add setup readiness and OpenAI acknowledgement UI

**Files:**
- Create: `app/routes/__init__.py`
- Create: `app/routes/setup.py`
- Create: `app/templates/setup/detail.html`
- Create: `app/templates/setup/_status.html`
- Modify: `app/templates/base.html`
- Modify: `app/main.py`
- Create: `tests/test_setup_routes.py`

- [ ] Add Setup navigation and a page showing profile/template/layout
  readiness, selected OpenAI model, safe validation errors, and current consent
  state without rendering private paths or content.
- [ ] Add explicit acknowledge/revoke actions for remote profile transmission
  and require origin-safe POST requests.
- [ ] Revalidate private files/config on page load and application submission;
  do not add a filesystem watcher.
- [ ] Block new application work with a clear action when setup or consent is
  incomplete, while keeping existing application pages readable.
- [ ] Write route/rendering tests for ready setup, acknowledgement, revocation,
  repeated actions, and application preflight success.
- [ ] Write tests for missing/corrupt private inputs, missing credentials,
  forged origin, safe error rendering, and no private content/path disclosure.
- [ ] Run `uv run pytest`; record the passing count before task 11.

### Task 11: Integrate asynchronous assessment, CV generation, and artefacts UI

**Files:**
- Create: `app/routes/assessments.py`
- Create: `app/routes/cv_generations.py`
- Create: `app/routes/artefacts.py`
- Create: `app/templates/applications/_work_status.html`
- Create: `app/templates/applications/_assessment.html`
- Modify: `app/templates/applications/_application_form.html`
- Modify: `app/templates/applications/_application_edit_form.html`
- Modify: `app/templates/applications/_application_row.html`
- Modify: `app/templates/applications/index.html`
- Modify: `app/templates/applications/detail.html`
- Modify: `app/static/app.js`
- Modify: `app/static/app.css`
- Modify: `app/main.py`
- Modify: `app/repository.py`
- Modify: `tests/test_routes.py`
- Modify: `tests/test_dashboard_actions.py`
- Create: `tests/test_assessment_routes.py`
- Create: `tests/test_cv_generation_routes.py`
- Create: `tests/test_artefact_routes.py`

- [ ] Create applications by atomically persisting the application, initial
  assessment, and queued work, then redirect immediately to detail.
- [ ] Render lifecycle separately from queued/running step status and poll the
  focused status fragment only while work is active.
- [ ] Show score, mandatory coverage, outcome, analysis, gaps, model,
  timestamps, generation state, and safe failure/retry guidance.
- [ ] Add Retry for failed work and `Generate CV anyway` for completed
  mismatches; reject duplicate/invalid actions and preserve mismatch results.
- [ ] Move to `Ready for review` after successful deterministic DOCX
  generation targeted at one page, label the candidate as requiring human
  factual/editorial/pagination review, and retain explicit manual `Submitted`
  transition behavior.
- [ ] Replace the dashboard CV column with compact assessment/work status and
  add `Open artefacts` on dashboard/detail where the directory exists.
- [ ] Implement POST-only, origin-checked, containment-checked Finder opening
  through macOS `open` with an argument array and no shell.
- [ ] Write route/UI tests for immediate redirect, active polling, matched and
  mismatch rendering, automatic generation, override, retries, dashboard
  badges, nullable submission, and polling stop.
- [ ] Write endpoint/error tests for active-work conflict, forged origin,
  traversal/symlink, missing directory, unsupported platform, command failure,
  escaped model/error text, and no private path/content leakage.
- [ ] Run `uv run pytest`; record the passing count before task 12.

### Task 12: Verify v1 acceptance criteria and engineering standards

**Files:**
- Modify:
  `docs/plans/2026-08-06-deliver-ai-assessment-and-one-page-cv-v1.md`

- [ ] Verify setup readiness and acknowledgement gate all profile-bearing
  OpenAI work without exposing private data.
- [ ] Verify application creation returns immediately and the dispatcher runs
  no more than three isolated workers while FastAPI remains responsive.
- [ ] Verify fixed fake results reproduce requirements, evidence, hard gates,
  mandatory coverage, scores, outcomes, evidence-cited draft CV claims,
  metadata, and artefacts.
- [ ] Verify arbitrary master-style Markdown reaches draft generation without
  semantic profile parsing, while invalid citations, uncited identity values,
  confidential wording, and changed inputs still fail deterministically.
- [ ] Verify matched and forced-mismatch flows produce the required filename
  and reach `Ready for review` after deterministic DOCX generation; verify the
  state does not imply completion of human factual/editorial/pagination review.
- [ ] Verify deterministic over-page, provider, process, configuration, and
  path failures remain visible/recoverable without changing lifecycle
  incorrectly or leaking private content.
- [ ] Verify the old CV upload/preview/download behavior is absent and Finder
  actions can open only the persisted application directory.
- [ ] Run `uv run pytest` and record the complete passing count.
- [ ] Run `uv run ruff check .` and `uv run ruff format --check .`.
- [ ] Run `uv run mypy app tests` and the committed-schema drift check.
- [ ] Run `uv lock --check` and confirm `uv.lock` matches `pyproject.toml`.

### Task 13: Update operator documentation and archive the completed plan

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.md` only if implementation establishes a reusable rule
- Modify:
  `docs/plans/2026-08-06-deliver-ai-assessment-and-one-page-cv-v1.md`

- [ ] Document the `jobhunter` launcher, dispatcher/worker behavior, timing,
  concurrency, retry/recovery semantics, and diagnostic commands.
- [ ] Document OpenAI configuration/credentials, private setup, remote-data
  acknowledgement, lifecycle, scoring, generated artefacts, Finder handoff,
  evidence-cited draft status, required human factual/editorial review, and
  manual DOCX/PDF workflow.
- [ ] Document the editable DOCX review, manual pagination confirmation, PDF
  export, and `Submitted` handoff. Record automated PDF rendering as deferred
  without selecting a renderer.
- [ ] Record all automated counts and applicable live OpenAI, browser,
  restart-recovery, and burst-concurrency verification results in this plan.
- [ ] Update `AGENTS.md` only for a genuinely reusable project-wide pattern and
  add/update documentation tests only if executable/documented behavior is
  covered by such tests.
- [ ] Confirm every checklist item and applicable manual verification is
  complete, then move this plan to `docs/plans/completed/`.

## Post-Completion

*These items require private inputs, credentials, or external repository
coordination. Record applicable results before archiving the implementation
plan.*

**Manual verification:**

- Copy/adapt the public examples to `private/profile/profile.md`,
  `private/profile/cv-layout.yaml`, and
  `private/profile/cv-template.docx`.
- Set `OPENAI_API_KEY`, review the configured model/data-handling policy, grant
  the in-app acknowledgement, and run one non-sensitive live assessment and CV
  generation before using the real profile.
- Review matched, mismatch, override, and retry states in a supported
  browser; confirm polling stops and errors expose no private content or paths.
- Submit more than three synthetic applications and observe queueing, FastAPI
  responsiveness, slot reuse, process cleanup, and restart recovery.
- Open an application directory from dashboard/detail, review the editable
  DOCX in the preferred editor, verify every factual claim and disclosure
  boundary against the cited profile evidence, confirm one-page pagination,
  edit if necessary, and manually export the final PDF before recording
  `Submitted`.

**External system and repository actions:**

- Create or identify the associated GitHub issue before implementation
  commits. Use its number in every Conventional Commit message.
- Work on a dedicated feature branch, open a pull request rather than pushing
  or merging directly to `master`, and comment on the issue with the completed
  commit/PR link.
- Confirm any applicable organization-specific OpenAI retention or
  data-handling requirements before sending the private profile.
