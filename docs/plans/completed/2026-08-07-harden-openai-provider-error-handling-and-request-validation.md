# Harden OpenAI Provider Error Handling and Request Validation

## Overview

This plan remediates four findings in the new structured-generation provider
boundary. It prevents private request and response data from remaining
reachable through exception chains, preserves durable retry behavior for HTTP
408 and 409 responses, and validates schema names against the exact character
set accepted by OpenAI.

The work stays limited to the provider contracts, OpenAI adapter, and mocked
provider tests. It does not change consent persistence, application services,
the parent delivery plan, or user-facing behavior.

## Context (from discovery)

- **Files involved:** `app/ai/providers/base.py`,
  `app/ai/providers/openai.py`, and `tests/test_ai_providers.py`.
- `StructuredGenerationError` exposes safe codes, messages, and retry flags,
  but current raises occur while sensitive SDK or validation exceptions remain
  active.
- `max_retries=0` delegates retry decisions to the future durable work layer,
  so the adapter's `retryable` classification must preserve SDK semantics.
- `StructuredGenerationRequest` validates schema names before provider calls,
  but `str.isalnum()` does not match OpenAI's ASCII-only contract.
- Existing tests use deterministic fakes and mocked OpenAI exceptions; no live
  provider call is required for this remediation.

## Development Approach

- **Testing approach:** Regular: implement each bounded fix, then add its
  regression tests before moving to the next task.
- Complete each task fully before starting the next one.
- Make small, focused changes and avoid new abstractions unless they are needed
  to keep exception construction separate from exception handling.
- Every implementation task must add or update tests for its modified paths.
- All focused tests must pass before the next task begins.
- Update this plan immediately if remediation scope changes.
- Preserve the existing provider protocol and public result/error shapes.

## Testing Strategy

- Extend `tests/test_ai_providers.py` for every changed behavior.
- Inspect both `__context__` and `__cause__` recursively so tests detect hidden
  SDK errors, Pydantic validation errors, raw provider output, credentials, and
  private request data.
- Cover SDK failure on both the initial request and the repair request.
- Add explicit HTTP 408 and 409 classification cases alongside the existing
  timeout, rate-limit, server, authentication, and invalid-request cases.
- Add schema-name acceptance and rejection cases at the request boundary,
  including valid dashes and invalid non-ASCII alphanumeric characters.
- Run the focused provider tests after each implementation task and the full
  project verification suite at the end.
- No browser or UI end-to-end tests are needed because this change does not
  modify routes, templates, or frontend behavior.

## Progress Tracking

- Mark completed items with `[x]` immediately when done.
- Add newly discovered work with a `➕` prefix.
- Record blockers with a `⚠️` prefix.
- Keep this plan synchronized with implementation and verification results.
- Do not proceed to the next task while the current task's tests fail.

## Solution Overview

Refactor exception normalization so sensitive source exceptions are inspected
only long enough to derive safe domain data. Construct or record the sanitized
failure inside the handler, leave the handler, and raise the domain exception
only when no SDK or Pydantic exception is active. Apply the same rule between
the first invalid response, the repair request, and repair exhaustion.

Extend status normalization without expanding the public error-code enum:
classify HTTP 408 as a retryable timeout and HTTP 409 as a retryable provider
server failure. Replace permissive Unicode-aware schema-name validation with a
bounded ASCII expression matching OpenAI's documented
`[A-Za-z0-9_-]+` contract.

## Technical Details

- `raise ... from None` suppresses traceback rendering but does not clear an
  exception object's `__context__`; sanitized errors must be raised after the
  corresponding `except` block has ended.
- The repair flow must also leave the first `_InvalidOutput` handler before it
  issues the second provider request. Otherwise a repair-time provider failure
  can inherit the first validation failure as context.
- `_validate_output` should derive only the bounded validation summary while
  handling `ValidationError`, then raise `_InvalidOutput` outside that handler.
- Repair exhaustion should similarly raise `StructuredGenerationError` only
  after the second `_InvalidOutput` handler has ended.
- Existing safe messages, error codes, retry flags, one-repair limit, consent
  recheck, metadata, and usage aggregation must remain unchanged except for the
  intentional 408/409 classifications.
- Schema names remain limited to 64 characters. ASCII letters, digits,
  underscores, and dashes are accepted; whitespace, punctuation outside that
  set, and Unicode letters or digits are rejected before any provider call.

## What Goes Where

- **Implementation Steps:** provider contract validation, adapter exception
  flow, status classification, regression tests, and local verification.
- **Post-Completion:** optional external confirmation against a live OpenAI
  account; this is not required for automated acceptance.

## Implementation Steps

### Task 1: Remove sensitive data from exception chains

**Files:**

- Modify: `app/ai/providers/openai.py`
- Modify: `tests/test_ai_providers.py`

- [x] Refactor SDK error normalization in `openai.py` so each `except` branch
  derives a sanitized `StructuredGenerationError`, exits the handler, and only
  then raises the domain error.
- [x] Restructure first-response validation so repair instructions and the
  repair provider call execute after the first `_InvalidOutput` handler ends.
- [x] Restructure Pydantic validation and repair exhaustion so neither the raw
  `ValidationError` nor `_InvalidOutput` remains attached to the final domain
  exception.
- [x] Preserve safe messages, public error attributes, one repair attempt,
  consent rechecking, and successful response metadata and usage behavior.
- [x] Update provider-error tests to traverse `__context__` and `__cause__` and
  prove SDK secrets and private request content are absent.
- [x] Add a repair-call provider-error test proving the first raw response and
  the repair SDK error are absent from the complete exception chain.
- [x] Update repair-exhaustion tests to prove both raw responses and Pydantic
  validation objects are absent from the complete exception chain.
- [x] Run `uv run pytest tests/test_ai_providers.py`; all tests must pass before
  Task 2.

### Task 2: Preserve retry semantics for HTTP 408 and 409

**Files:**

- Modify: `app/ai/providers/openai.py`
- Modify: `tests/test_ai_providers.py`

- [x] Map HTTP 408 responses to `ProviderErrorCode.TIMEOUT` with
  `retryable=True` and a sanitized timeout message.
- [x] Map HTTP 409 responses to `ProviderErrorCode.SERVER` with
  `retryable=True` and a sanitized transient-provider message.
- [x] Preserve the existing classifications for 400, 401, 403, 429, and 5xx
  responses.
- [x] Add explicit mocked HTTP 408 and 409 cases that assert error codes, retry
  flags, safe messages, and clean exception chains.
- [x] Retain coverage proving ordinary 4xx request errors remain deterministic
  and non-retryable.
- [x] Run `uv run pytest tests/test_ai_providers.py`; all tests must pass before
  Task 3.

### Task 3: Enforce OpenAI-compatible schema names before requests

**Files:**

- Modify: `app/ai/providers/base.py`
- Modify: `tests/test_ai_providers.py`

- [x] Replace Unicode-aware `isalnum()` validation with a full-match ASCII
  schema-name rule allowing only letters, digits, underscores, and dashes.
- [x] Keep the existing non-empty and 64-character bounds and provide a safe,
  accurate validation message.
- [x] Add success tests for representative names containing underscores and
  dashes at the request boundary.
- [x] Add rejection tests for non-ASCII letters and digits, spaces, unsupported
  punctuation, empty names, and overlong names.
- [x] Confirm rejected names fail during request construction, before consent
  access or provider invocation can occur.
- [x] Run `uv run pytest tests/test_ai_providers.py`; all tests must pass before
  Task 4.

### Task 4: Verify acceptance criteria

**Files:**

- Verify: `app/ai/providers/base.py`
- Verify: `app/ai/providers/openai.py`
- Verify: `tests/test_ai_providers.py`

- [x] Verify normalized provider failures have no adapter-created exception
  context or cause containing credentials, request bodies, prompts, or raw
  responses.
- [x] Verify initial failures, repair-time failures, and repair exhaustion all
  satisfy the same redaction invariant.
- [x] Verify HTTP 408 and 409 are retryable while deterministic 4xx failures
  remain non-retryable.
- [x] Verify schema-name validation exactly accepts `[A-Za-z0-9_-]+` within the
  64-character bound.
- [x] Run `uv run pytest tests/test_ai_providers.py`.
- [x] Run the full test suite with `uv run pytest`.
- [x] Run `uv run ruff check .` and `uv run ruff format --check .`.
- [x] Run `uv run mypy app tests scripts`.
- [x] Run `uv run python scripts/generate_ai_schemas.py --check`.
- [x] Run `uv lock --check` and `git diff --check`.

### Task 5: Finalize remediation documentation

**Files:**

- Modify: this remediation plan in `docs/plans/`.
- Move to: `docs/plans/completed/` with the same filename.

- [x] Record focused and full verification results in this plan.
- [x] Confirm no README or AGENTS guidance changed and leave those files
  untouched.
- [x] Confirm the parent AI delivery plan remains unchanged, as required by the
  selected scope.
- [x] Mark every completed checklist item and document any deviations.
- [x] Move this plan to `docs/plans/completed/` after all acceptance criteria
  pass.

## Verification Results

Completed on 2026-08-07 with no scope deviations.

- `uv run pytest tests/test_ai_providers.py` — 35 passed.
- `uv run pytest` — 169 passed.
- `uv run ruff check .` — passed.
- `uv run ruff format --check .` — 44 files already formatted.
- `uv run mypy app tests scripts` — no issues in 28 source files.
- `uv run python scripts/generate_ai_schemas.py --check` — passed.
- `uv lock --check` and `git diff --check` — passed.

README and AGENTS guidance were not modified. The parent AI delivery plan had
an existing local modification and was not changed by this remediation.

## Post-Completion

**Manual verification**

- No manual UI verification is required.
- Optionally make a non-profile-bearing request against a configured OpenAI
  account to confirm the installed SDK accepts dashed schema names and returns
  the expected structured response. Do not use private profile or JD content
  for this optional check.

**External system updates**

- No deployment, configuration, dependency, or consuming-project changes are
  required.
- The associated GitHub issue should receive a completion comment linking the
  eventual conventional commit or pull request, following repository rules.
