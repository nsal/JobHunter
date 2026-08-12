# Task 10 Fix 1: Harden Setup Security and Readiness

## Overview

This bug fix closes the five Task 10 defects found during review. It makes
consent mutations depend on a configured trusted origin rather than the
request `Host`, propagates the launcher's project root into the web process,
reports each private input's real validation state, applies the provider's
credential rule during setup preflight, and removes the duplicated blocked-form
alert.

The change preserves the local FastAPI/Jinja/SQLite architecture, existing
consent persistence, private-data redaction, durable application creation, and
the default `jobhunter` launcher workflow. It adds no filesystem watcher,
session framework, browser E2E dependency, or remote service.

## Context (from discovery)

- Associated GitHub issue:
  [#46](https://github.com/nsal/JobHunter/issues/46).
- `app/routes/setup.py` compares `Origin` with `request.base_url`, which is
  derived from the request's `Host` header and therefore is not a trusted
  origin boundary.
- `app/main.py::create_app()` accepts a project root, but `app.main.run_server`,
  `app.cli.run_server`, and `Launcher.run()` do not propagate the launcher's
  configured project root into the server child.
- `_private_readiness()` performs complete validation only when every expected
  path is a file, then stops at the first `SettingsError`; unchecked files can
  consequently be displayed as `Ready`.
- Setup accepts any credential with non-whitespace content, while
  `app.ai.providers.factory` rejects credentials with surrounding whitespace.
- A setup precondition failure is assigned to both `error` and `setup_error`,
  and the application form renders both values as separate alerts.
- The current full suite passes with 417 tests, so each defect needs a focused
  regression that demonstrates the reviewed scenario.

## Development Approach

- **Testing approach:** Regular, inherited from the parent delivery plan.
- Complete each task fully before moving to the next task.
- Keep changes focused on setup security, server configuration propagation,
  readiness validation, and blocked-form rendering.
- Add or update tests for every changed code path, including success and error
  cases, before completing each task.
- Run each focused suite after its task and do not continue while it is failing.
- Update this plan immediately if implementation scope, interfaces, or
  acceptance criteria change.
- Preserve private path/content redaction, idempotent consent persistence,
  provider-side consent enforcement, and existing application readability.
- Use `uv` for execution, Ruff for linting and formatting, and mypy for static
  verification.

## Testing Strategy

- **Origin route tests:** verify the configured origin succeeds; missing,
  malformed, wrong-port, forged-origin, and matching forged-Host requests fail
  without changing consent.
- **Launcher tests:** verify the project root reaches both server and dispatcher
  child arguments and that the web process passes it into `create_app()`.
- **Readiness tests:** cover each missing, corrupt, oversized, symlinked, or
  otherwise invalid profile/template/layout independently and in combinations;
  no unchecked input may be labelled ready.
- **Credential tests:** verify missing, blank, whitespace-padded, and valid
  credentials produce the same result in setup and provider construction.
- **Rendering tests:** verify a blocked ordinary or HTMX application submission
  renders one safe alert and retains the Setup action.
- **No browser E2E framework:** use the existing httpx2/Jinja route tests; retain
  focused manual browser checks under Post-Completion.
- **Acceptance verification:** run the complete Python suite, Ruff check and
  format check, mypy, generated-schema drift check, lockfile check, and Git
  whitespace check.

## Progress Tracking

- Mark completed items with `[x]` immediately when done.
- Add newly discovered tasks with a `➕` prefix.
- Record blockers with a `⚠️` prefix and identify the affected criterion.
- Keep this plan synchronized with actual files, interfaces, commands, and test
  counts.
- Do not proceed to the next task while focused tests are failing.
- Move this plan to `docs/plans/completed/` only after every automated gate
  passes.

## Solution Overview

Use one explicit server configuration boundary and shared validation rules:

1. Build the allowed HTTP origin from the launcher's configured host and port,
   pass it and the project root into the web child, and store the normalized
   origin in application state. Consent routes compare a parsed request Origin
   against this immutable configured value; they never derive trust from
   `Host` or forwarded headers.
2. Refactor private-input validation into shared per-input validators. The
   existing aggregate validator composes them for workers, while setup invokes
   each independently and maps failures to fixed, path-free UI messages.
3. Share one OpenAI credential-presence predicate between setup preflight and
   provider construction so both reject blank or padded values identically.
4. Represent setup blocking through one form-context field and render one alert
   with the Setup link for both ordinary and HTMX responses.

## Technical Details

### Trusted origin and launcher propagation

- Extend the server boundary to receive `project_root`, `host`, and `port` from
  `Launcher`; preserve spawn-safe primitive/path arguments only.
- Normalize one configured origin as `http://<host>:<port>`, omitting the port
  only when it is the HTTP default and handling IPv6 host formatting safely.
- Pass the project root and trusted origin into `create_app()` and store only
  their normalized values in application state during lifespan startup.
- Keep a deterministic explicit trusted-origin argument for in-process tests;
  production must not infer trust from request headers.
- Parse Origin values strictly: require HTTP, an exact normalized host and port,
  and no userinfo, path, query, or fragment. Reject missing or malformed values
  before any consent repository mutation.
- A forged `Host` matching a forged Origin must remain forbidden because neither
  value changes the configured trusted origin.

### Independent private-input readiness

- Extract shared profile, template, and layout validators from
  `validate_private_inputs()` without weakening size, containment, symlink,
  encoding, DOCX, YAML, layout-bound, or hash checks.
- Keep worker-facing aggregate validation and `PrivateInputs` output unchanged.
- Let setup evaluate all three inputs independently, even when another input is
  missing or invalid.
- Map each `SettingsError` to a fixed label without inspecting error text for
  path-bearing keywords and without placing exception text in template context.
- Report every invalid input as `Missing or invalid`; report `Ready` only after
  that input's complete validator succeeds.

### Shared credential and form rules

- Extract the provider's existing credential condition into a small pure helper
  that accepts an environment mapping or value without logging or returning the
  credential.
- Use the same helper in provider construction and setup inspection; a value is
  ready only when non-empty and exactly equal to its trimmed form.
- Keep provider construction responsible for the sanitized configuration error
  and setup responsible for its fixed UI message.
- On setup precondition failure, populate one alert field. Preserve repository
  validation errors in the ordinary `error` field and keep Jinja autoescaping.

## What Goes Where

- **Implementation Steps** contain code, tests, plan updates, and automated
  verification achievable in this repository.
- **Post-Completion** contains manual browser/launcher verification and GitHub
  workflow updates requiring external state.

## Implementation Steps

### Task 1: Bind consent mutations to configured server origin

**Files:**

- Modify: `app/routes/setup.py`
- Modify: `app/main.py`
- Modify: `app/cli.py`
- Modify: `tests/conftest.py`
- Modify: `tests/test_setup_routes.py`
- Modify: `tests/test_cli.py`

- [x] Add explicit trusted-origin configuration to `create_app()` and store its
  normalized value in application state.
- [x] Update origin validation to compare strictly against configured state and
  reject request-derived Host trust, malformed origins, and wrong ports.
- [x] Propagate `project_root`, host, and port through `Launcher`,
  `app.cli.run_server`, and `app.main.run_server` into `create_app()`.
- [x] Write route success tests for the configured origin and consent
  acknowledgement/revocation idempotence.
- [x] Write route error tests for missing, malformed, forged, wrong-port, and
  matching forged-Host origins; assert consent state remains unchanged.
- [x] Write launcher/server tests proving a custom project root and configured
  origin reach the web app while dispatcher arguments remain unchanged.
- [x] Run `uv run pytest tests/test_setup_routes.py tests/test_cli.py`; all tests
  must pass before Task 2.

Task 1 focused verification: `tests/test_setup_routes.py` and
`tests/test_cli.py` — 56 passed.

### Task 2: Report exact private-input and credential readiness

**Files:**

- Modify: `app/settings.py`
- Modify: `app/routes/setup.py`
- Modify: `app/ai/providers/factory.py`
- Modify: `tests/test_settings.py`
- Modify: `tests/test_setup_routes.py`
- Modify: `tests/test_ai_providers.py`

- [x] Refactor aggregate private-input validation into shared per-input
  validators while preserving the existing `PrivateInputs` API and hashes.
- [x] Replace error-message keyword classification with independent complete
  validation and fixed redacted status messages for each input.
- [x] Extract and reuse one pure OpenAI credential-readiness predicate in setup
  and provider construction.
- [x] Write settings success tests proving aggregate validation and hashes are
  unchanged after composition through the per-input validators.
- [x] Write readiness error tests for each missing, corrupt, oversized, or
  symlinked input and combinations where all displayed states remain accurate.
- [x] Write setup/provider parity tests for absent, blank, padded, and valid
  credentials without exposing credential values.
- [x] Run `uv run pytest tests/test_settings.py tests/test_setup_routes.py
  tests/test_ai_providers.py`; all tests must pass before Task 3.

Task 2 focused verification: settings, setup, and provider suites — 84 passed.

### Task 3: Render one setup precondition alert

**Files:**

- Modify: `app/main.py`
- Modify: `app/templates/applications/_application_form.html`
- Modify: `tests/test_setup_routes.py`
- Modify: `tests/test_routes.py`

- [x] Separate repository field-validation context from setup-precondition
  context so one failure cannot populate both alert fields.
- [x] Render exactly one safe alert with one `Open setup` link for incomplete
  setup in ordinary and HTMX form responses.
- [x] Preserve one ordinary repository-validation alert without a misleading
  Setup link when local setup is ready.
- [x] Write ordinary-response tests for setup blocking and repository field
  validation success/error distinctions.
- [x] Write HTMX fragment tests for the same one-alert behavior and retained
  submitted form values.
- [x] Run `uv run pytest tests/test_setup_routes.py tests/test_routes.py`; all
  tests must pass before Task 4.

Task 3 focused verification: setup and application route suites — 57 passed.

### Task 4: Verify acceptance criteria and engineering standards

**Files:**

- Modify:
  `docs/plans/2026-08-11-task-10-fix-1-harden-setup-security-and-readiness.md`

- [x] Verify consent actions accept only the configured trusted origin and a
  matching forged Host/Origin cannot mutate consent.
- [x] Verify custom launcher project roots are used consistently by web and
  dispatcher children.
- [x] Verify every private input and OpenAI credential state is accurate,
  redacted, and blocks application creation when invalid.
- [x] Verify ordinary and HTMX blocked forms contain one alert and existing
  application pages remain readable.
- [x] Run `uv run pytest` and record the complete passing count.
- [x] Run `uv run ruff check .` and `uv run ruff format --check .`.
- [x] Run `uv run mypy app tests scripts` and
  `uv run python scripts/generate_ai_schemas.py --check`.
- [x] Run `uv lock --check` and `git diff --check`.

Task 4 verification: full suite — 445 passed; Ruff check and format check,
mypy (`app tests scripts`), generated-schema drift, lockfile, and whitespace
checks all passed.

### Task 5: Finalize documentation and archive the fix plan

**Files:**

- Modify: `README.md` only if trusted-origin or launcher usage changes operator
  instructions
- Modify: `AGENTS.md` only if implementation establishes a reusable rule
- Modify:
  `docs/plans/2026-08-06-deliver-ai-assessment-and-one-page-cv-v1.md`
- Move:
  `docs/plans/2026-08-11-task-10-fix-1-harden-setup-security-and-readiness.md`
  to `docs/plans/completed/`

- [x] Record the final focused/full test counts and automated gate results in
  this plan and the parent Task 10 verification history.
- [x] Update `README.md` only if operators need new trusted-origin or custom-root
  instructions; otherwise record that no README change is required.
- [x] Update `AGENTS.md` only for a genuinely reusable project-wide rule;
  otherwise record that no AGENTS change is required.
- [x] Confirm every checklist item is complete and move this plan to
  `docs/plans/completed/`.
- [ ] Keep the associated issue/commit/PR references synchronized through the
  repository workflow.

## Post-Completion

*These items require manual browser behavior or external repository state.*

**Manual verification:**

- Start `jobhunter` with its default project root and confirm setup
  acknowledgement, revocation, and application preflight work from the
  configured URL.
- Start with a temporary custom `--project-root` containing valid synthetic
  private inputs and confirm both the Setup page and worker use that root.
- Confirm a blocked ordinary form and HTMX dialog each show one alert and one
  Setup action.
- If non-loopback binding is supported, verify the documented configured origin
  explicitly before exposing the local application beyond loopback.

**External system and repository actions:**

- Use the associated GitHub issue number in every Conventional Commit.
- Push only the dedicated feature/fix branch and open a pull request; do not
  push or merge directly to `master`.
- Comment on the issue with the completed commit or pull-request link.
