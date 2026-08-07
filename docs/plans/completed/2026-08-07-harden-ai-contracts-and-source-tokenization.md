# Harden AI contracts and source tokenization

## Overview

- Address the three actionable findings from the read-only review of the
  uncommitted Task 3 implementation.
- Reject coercible malformed AI output, require evidence for positive
  supporting-category alignment, and preserve valid Unicode separator
  characters during source tokenization.
- Retain the existing v1 models, identifiers, JSON Schema paths, and source
  block format so valid assessment and CV payloads remain compatible.

## Context (from discovery)

- `app/ai/schema_models.py` defines the immutable Pydantic contracts and
  cross-record validation used by future provider and scoring services.
- `ContractModel` currently forbids extra fields but permits Pydantic scalar
  coercion, including converting `"false"` to `False` for mandatory flags.
- `SupportingCategoryAssessment` validates duplicate references but permits a
  positive alignment without requirement or profile citations.
- `app/ai/source_blocks.py` normalizes CRLF/CR to LF, then uses
  `str.splitlines()`. That method disagrees with markdown-it line maps for
  U+0085, U+2028, and U+2029 and can silently discard text after a separator.
- The relevant regression suites are `tests/test_ai_schemas.py` and
  `tests/test_source_blocks.py`. Generated schemas are maintained by
  `scripts/generate_ai_schemas.py`.

## Development Approach

- **Testing approach:** Regular, matching the parent delivery plan: make each
  focused implementation change, add its success and error-path regression
  tests, then run the relevant and full suites.
- Complete each task fully and mark its checklist before moving to the next.
- Keep changes narrow; do not redesign the taxonomy, scoring model, hard-gate
  semantics, source block IDs, or provider integration.
- Every code task must update tests for every modified behavior.
- Do not proceed while the focused or full test suite is failing.
- Update this plan immediately if a fix requires different files, schema
  behavior, or compatibility constraints.
- Preserve compatibility for valid v1 JSON payloads. Rejection of values that
  relied on Pydantic coercion is the intended behavior change.

## Testing Strategy

- **Contract unit tests:** Validate strict JSON parsing with native booleans and
  reject string/integer substitutes that previously coerced successfully.
- **Grounding unit tests:** Cover all positive category alignments with missing
  requirement citations, missing profile citations, and valid citations.
  Confirm gap, unknown, and not-applicable states retain their intended empty
  citation behavior.
- **Tokenizer unit tests:** Cover U+0085, U+2028, and U+2029 inside valid UTF-8
  source and prove no surrounding text is lost or reordered.
- **Schema tests:** Regenerate both committed v1 JSON Schemas and run drift
  checking even if strict runtime configuration does not alter their shape.
- **Final gate:** Run pytest, Ruff linting and formatting, mypy, schema drift,
  lockfile consistency, and Git whitespace checks.
- No browser or external-provider testing is needed because these changes are
  isolated to local validation and deterministic tokenization.

## Progress Tracking

- Mark completed items with `[x]` immediately when done.
- Add newly discovered work with a `➕` prefix.
- Add blockers with a `⚠️` prefix and identify the affected finding.
- Keep this plan and the parent Task 3 verification record synchronized with
  actual files and test counts.

## Solution Overview

Use the narrowest fix at each existing validation boundary:

1. Enable Pydantic strict mode on the shared AI contract base. Parse provider
   JSON through strict JSON validation so valid enum strings remain supported
   while scalar substitutions are rejected.
2. Extend the supporting-category model validator. `strong`, `matched`, and
   `partial` alignments must cite at least one known requirement and one
   profile block; non-positive states may retain empty citations.
3. Split normalized source text only on literal LF characters, matching
   markdown-it's line-map behavior and preserving other valid Unicode
   separators as source content.

The model definitions remain the source of truth for committed JSON Schemas.
No new dependency or schema version is required.

## Technical Details

### Strict contract validation

- Add `strict=True` to `ContractModel.model_config` while retaining
  `extra="forbid"` and `frozen=True`.
- Exercise provider-shaped payloads through `model_validate_json`; JSON-native
  enum strings and booleans must validate, while `"false"`, `"true"`, `0`, and
  `1` must fail when supplied for boolean fields.
- Review direct Python construction in tests and use enum instances where
  strict Python-mode validation is intentional.

### Positive category grounding

- Define the positive set as `CategoryAlignment.STRONG`,
  `CategoryAlignment.MATCHED`, and `CategoryAlignment.PARTIAL`.
- Require both `requirement_ids` and `profile_block_ids` for those alignments.
- Continue resolving those IDs through `AssessmentResult` and
  `validate_assessment_references`, so the new non-empty rule composes with the
  existing dangling-reference checks.
- Do not require citations for `gap`, `unknown`, or `not_applicable`; these
  states do not contribute positive evidence.

### Unicode-safe line mapping

- Keep the existing NFC and CRLF/CR normalization.
- Replace `splitlines()` with splitting on literal `"\n"` so the line array
  uses the same boundaries as markdown-it token maps.
- Preserve U+0085, U+2028, and U+2029 in block content and therefore in the
  deterministic content hash.

## What Goes Where

- **Runtime behavior:** `app/ai/schema_models.py` and
  `app/ai/source_blocks.py`.
- **Generated contracts:** `app/ai/schemas/v1/assessment-result.json` and
  `app/ai/schemas/v1/cv-content.json`.
- **Regression tests:** `tests/test_ai_schemas.py` and
  `tests/test_source_blocks.py`.
- **Tracking:** this plan and the Task 3 verification note in
  `docs/plans/2026-08-06-deliver-ai-assessment-and-one-page-cv-v1.md`.

## Implementation Steps

### Task 1: Enforce strict and grounded assessment contracts

**Files:**

- Modify: `app/ai/schema_models.py`
- Modify: `app/ai/schemas/v1/assessment-result.json`
- Modify: `app/ai/schemas/v1/cv-content.json`
- Modify: `tests/test_ai_schemas.py`

- [x] Enable strict Pydantic validation on the shared AI contract base without
  changing valid v1 JSON field names, enums, or collection shapes.
- [x] Require requirement and profile citations for strong, matched, and
  partial supporting-category alignment.
- [x] Regenerate the assessment and CV JSON Schemas from the model source of
  truth.
- [x] Add success tests for strict JSON-native booleans/enums and cited positive
  category alignments.
- [x] Add error tests for coercible string/integer booleans and for every
  positive alignment missing either citation type.
- [x] Add compatibility tests proving gap, unknown, and not-applicable
  categories may retain empty citations.
- [x] Run `uv run pytest tests/test_ai_schemas.py` and the schema drift check;
  both must pass before Task 2.
- [x] Run `uv run pytest`; the full suite must pass before Task 2.

### Task 2: Preserve Unicode separators in deterministic source blocks

**Files:**

- Modify: `app/ai/source_blocks.py`
- Modify: `tests/test_source_blocks.py`

- [x] Align source line splitting with markdown-it token-map LF boundaries.
- [x] Preserve U+0085, U+2028, and U+2029 within source block content and
  content hashes.
- [x] Add success tests proving text on both sides of each Unicode separator is
  retained in order.
- [x] Add regression tests proving existing LF, CRLF, and CR normalization and
  stable block hashes remain unchanged.
- [x] Run `uv run pytest tests/test_source_blocks.py`; it must pass before
  Task 3.
- [x] Run `uv run pytest`; the full suite must pass before Task 3.

### Task 3: Verify review acceptance criteria

**Files:**

- Modify only if required by verification failures.

- [x] Verify malformed coercible booleans fail instead of changing mandatory
  semantics.
- [x] Verify every positive supporting alignment requires both citation types
  and all citations still resolve against known records and blocks.
- [x] Verify the three Unicode separators cannot cause source text loss.
- [x] Run `uv run pytest` and record the complete passing count.
- [x] Run `uv run ruff check .` and `uv run ruff format --check .`.
- [x] Run `uv run mypy app tests scripts`.
- [x] Run `uv run python scripts/generate_ai_schemas.py --check`.
- [x] Run `uv lock --check` and `git diff --check`.

### Task 4: Update and archive remediation documentation

**Files:**

- Modify:
  `docs/plans/2026-08-06-deliver-ai-assessment-and-one-page-cv-v1.md`
- Move: `docs/plans/2026-08-07-harden-ai-contracts-and-source-tokenization.md`
  to `docs/plans/completed/`

- [x] Add the post-review regression count and completed verification commands
  to the parent Task 3 record without erasing its original result.
- [x] Confirm no README or reusable `AGENTS.md` rule is needed for this local
  contract/tokenizer correction.
- [x] Confirm every checklist item and recorded path matches the implementation.
- [x] Move this completed plan to `docs/plans/completed/`.

## Post-Completion

No manual, UI, provider, deployment, or external-system verification is
required. The future OpenAI adapter should consume provider output through the
strict JSON-validation path established here.

Completion verification: `uv run pytest` — 120 passed. Focused contract and
source-block suites, Ruff linting and formatting, mypy, schema drift checking,
lockfile checking, and Git whitespace checking all passed.

Follow-up review remediation:

- [x] ➕ Preserve U+0085, U+2028, and U+2029 when they fall immediately before
  or after a bounded source-block split.
- [x] ➕ Retain existing whitespace-only source rejection while exempting only
  the three supported separators from block-padding removal.
- [x] ➕ Add boundary tests for all three separators at both split positions.

Follow-up verification: `uv run pytest` — 127 passed. The focused source-block
suite passed with 24 tests. Ruff linting and formatting, mypy, schema drift
checking, lockfile checking, and Git whitespace checking also passed.
