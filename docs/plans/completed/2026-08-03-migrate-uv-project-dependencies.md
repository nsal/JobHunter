# Migrate JobHunter Dependencies to uv Project Metadata

## Overview

Move JobHunter's dependency declarations from `requirements.txt` into standard
`pyproject.toml` project metadata and regenerate `uv.lock`. This makes the
project reproducible with a pinned Python 3.14 interpreter and allows `uv run`
to discover and run the test, lint, format, and type-check commands without
manual environment setup.

## Context (from discovery)

- JobHunter is a local FastAPI application with SQLite-backed application
  tracking and a pytest test suite.
- `requirements.txt` currently contains both runtime and developer tooling
  dependencies.
- `pyproject.toml` only configures pytest, Ruff, and mypy; it has no `[project]`
  table, dependencies, or declared Python version.
- `uv.lock` therefore contains no packages, and `uv run pytest` / `uv run ruff`
  cannot find their executables.
- The README installs from `requirements.txt` but documents `uv run` commands,
  creating an inconsistent workflow.

## Development Approach

- **Testing approach:** Regular — update the dependency configuration first,
  then validate the application with the existing checks.
- Retain the existing dependency version ranges unless resolving them for
  Python 3.14 requires a compatible version adjustment.
- Make Python 3.14 an explicit project requirement; do not rely on uv's
  workspace default.
- Pin uv's selected interpreter with a committed `.python-version` file so
  local setup and continuous integration select Python 3.14 consistently.
- Keep runtime packages in `[project.dependencies]` and test, lint, and type
  checking tools in a uv development dependency group.
- Complete each task fully, including its listed validation, before the next.
- Update this plan if implementation scope changes.

## Testing Strategy

- Run the existing unit and route tests with `uv run pytest` after dependency
  metadata and lockfile changes.
- Run `uv run --locked ruff check .`,
  `uv run --locked ruff format --check .`, and `uv run --locked mypy app` to
  verify that all configured development tools are installed and executable
  from the committed lockfile.
- Verify `uv run python --version` selects Python 3.14 before the validation
  suite. Dependency metadata changes do not alter application behavior, so no
  new application test cases are expected.

## Progress Tracking

- Mark completed items with `[x]` immediately during implementation.
- Add newly discovered work with a `➕` prefix and blockers with a `⚠️` prefix.
- Keep this plan aligned with implementation changes.

## Solution Overview

`pyproject.toml` will become the authoritative dependency definition, while
`.python-version` will select Python 3.14. uv will resolve those inputs and
write the reproducible environment to `uv.lock`. The README will use
`uv sync --locked` as the normal setup command, after which all documented
`uv run --locked` checks work in a fresh checkout.

## Technical Details

- Add a PEP 621 `[project]` table with project identity and
  `requires-python = ">=3.14,<3.15"` to ensure the project runs on Python 3.14.
- Create `.python-version` with the `3.14` interpreter request using
  `uv python pin 3.14`.
- Transfer FastAPI, Uvicorn (with `standard` extra), Jinja2,
  python-multipart, and httpx to runtime dependencies, preserving their
  current bounds.
- Transfer pytest, Ruff, and mypy to uv's development dependency group,
  preserving their current bounds.
- Recreate `uv.lock` from the project metadata using uv under Python 3.14.
- Use `--locked` for post-lock validation so checks fail if metadata and the
  committed lockfile diverge instead of silently updating the lockfile.
- Retire `requirements.txt` so there is one maintained dependency source, and
  replace README setup instructions with `uv sync`.

## What Goes Where

- **Implementation Steps** are repository changes and validations that can be
  completed locally.
- **Post-Completion** lists optional manual verification for a clean clone.

## Implementation Steps

### Task 1: Define JobHunter's uv project metadata

**Files:**
- Modify: `pyproject.toml`
- Create: `.python-version`
- Delete: `requirements.txt`

- [x] add the PEP 621 `[project]` metadata and explicitly constrain supported
      Python to 3.14
- [x] run `uv python pin 3.14` and add the generated `.python-version` file
- [x] move the five runtime packages from `requirements.txt` into
      `[project.dependencies]`, preserving their current version ranges
- [x] move pytest, Ruff, and mypy into uv's development dependency group,
      preserving their current version ranges
- [x] retain the existing pytest, Ruff, and mypy tool configuration unchanged
- [x] remove `requirements.txt` after all of its dependency declarations are
      represented in `pyproject.toml`
- [x] verify metadata coverage: compare every former requirement with its new
      runtime or development declaration, including extras and bounds

### Task 2: Resolve the Python 3.14 environment and validate developer tools

**Files:**
- Modify: `uv.lock`
- Modify: `.venv/` (generated, untracked environment)

- [x] confirm `uv run python --version` selects Python 3.14.6
- [x] use `uv lock` with Python 3.14, then `uv sync --locked`, to resolve and
      install the declared project and development dependencies
- [x] confirm `uv.lock` contains the resolved application and development
      packages rather than only workspace metadata
- [x] run the unit and route test suite from the committed lockfile:
      `uv run --locked pytest`
- [x] run lint validation from the committed lockfile:
      `uv run --locked ruff check .`
- [x] run formatting validation from the committed lockfile:
      `uv run --locked ruff format --check .`
- [x] run static type validation from the committed lockfile:
      `uv run --locked mypy app`
- [x] fix Ruff's Python-3.14 `datetime.UTC` modernization finding and existing
      formatting differences, then rerun all checks successfully

### Task 3: Align documented setup and verification commands

**Files:**
- Modify: `README.md`

- [x] replace the `uv pip install -r requirements.txt` setup instruction with
      `uv sync --locked`
- [x] state that Python 3.14 is required for the project
- [x] retain and verify the documented `uv run --locked` server, pytest, Ruff,
      Ruff-format, and mypy commands against the synced environment
- [x] update any wording that identifies `requirements.txt` as the dependency
      source
- [x] re-run `uv run --locked pytest` after documentation changes to confirm
      the documented validation workflow remains valid

### Task 4: Verify acceptance criteria

**Files:**
- Modify: `docs/plans/2026-08-03-migrate-uv-project-dependencies.md`

- [x] verify all runtime and development dependencies have one authoritative
      declaration in `pyproject.toml`
- [x] verify `requires-python` constrains the project to Python 3.14
- [x] verify `.python-version` selects Python 3.14.6 with
      `uv run python --version`
- [x] move the generated `.venv` aside and verify a clean `uv sync --locked`
      recreates it from the committed `pyproject.toml` and `uv.lock`
- [x] run the full locked verification suite: `uv run --locked pytest`,
      `uv run --locked ruff check .`,
      `uv run --locked ruff format --check .`, and `uv run --locked mypy app`
- [x] record the sole test warning: FastAPI's test client emits an upstream
      Starlette deprecation warning for its `httpx` import; it does not fail
      the suite or block this dependency migration

### Task 5: Update project documentation and archive the completed plan

**Files:**
- Modify: `README.md` (only if verification exposes an omission)
- Modify: `AGENTS.md` (only if a lasting dependency-management convention is
  discovered)
- Move: `docs/plans/2026-08-03-migrate-uv-project-dependencies.md` to
  `docs/plans/completed/`

- [x] make final README corrections identified by acceptance verification
- [x] leave `AGENTS.md` unchanged: its existing uv workflow requirement
      already covers the durable convention
- [x] ensure all plan tasks are marked complete; final validation passed:
      18 pytest tests, Ruff lint and format checks, and mypy
- [x] move this plan to `docs/plans/completed/` after implementation is
      complete

## Post-Completion

**Manual verification:**

- In a clean checkout, run `uv run python --version` to confirm Python 3.14,
  then `uv sync --locked` followed by `uv run --locked pytest` to confirm no
  pre-existing virtual environment is needed.
- Start the app with `uv run --locked uvicorn app.main:app --reload` and
  confirm the local tracker opens normally.

**External system updates:**

- None expected; this is a local packaging and development-workflow migration.
