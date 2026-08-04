# Migrate Route Tests from Deprecated Starlette TestClient

## Overview

Replace FastAPI's deprecated re-export of Starlette `TestClient` with the
supported HTTPX2 ASGI testing stack. This removes the `StarletteDeprecationWarning`
from pytest output while retaining the current route-test coverage, database
lifespan behavior, and synchronous production application code.

## Context (from discovery)

- Route tests import `fastapi.testclient.TestClient` in `tests/conftest.py` and
  `tests/test_routes.py`.
- FastAPI's `testclient.py` only re-exports Starlette's now-deprecated client;
  importing it emits the warning before tests run.
- The current lockfile resolves FastAPI 0.141.1, Starlette 1.3.1, and HTTPX
  0.28.1; `httpx2` is not declared.
- `httpx` has no application import and is currently a runtime dependency only
  because the previous test client required it.
- HTTPX2's ASGI transport requires an async client and does not automatically
  run ASGI lifespan events. The app's existing route fixture must therefore
  manage lifespan explicitly to preserve database setup.

## Development Approach

- **Testing approach:** Regular — update the test dependencies and shared
  fixture, migrate route tests, then run the complete validation suite.
- Remove the unused runtime `httpx` dependency and add `httpx2` plus
  `asgi-lifespan` to the uv `dev` dependency group.
- Use `httpx2.AsyncClient` with `httpx2.ASGITransport` and a stable
  `http://testserver` base URL.
- Use `asgi_lifespan.LifespanManager` around the app so startup and shutdown
  execute exactly as they did under Starlette `TestClient`.
- Mark route tests with pytest's built-in AnyIO integration and await all
  requests; do not change application routes or production request handling.
- Complete and validate each task before proceeding, updating this plan if the
  supported HTTPX2 API requires an adjustment.

## Testing Strategy

- Preserve all existing route-test success and validation/error assertions.
- Add fixture-level regression coverage that proves application lifespan startup
  runs with the new client, using the existing database-backed route behavior.
- Run `uv run --locked pytest` and confirm the warning is absent from its
  summary.
- Run `uv run --locked ruff check .`,
  `uv run --locked ruff format --check .`, and `uv run --locked mypy app` after
  test changes.
- This project has no browser end-to-end suite; ASGI route tests are the
  applicable integration coverage.

## Progress Tracking

- Mark completed items with `[x]` immediately during implementation.
- Add newly discovered work with a `➕` prefix and blockers with a `⚠️` prefix.
- Keep the plan synchronized with actual implementation work.

## Solution Overview

The shared route-test fixture will construct the FastAPI application, enter its
lifespan context with `asgi-lifespan`, and provide an HTTPX2 async client using
an in-process ASGI transport. Each route test will become an AnyIO test and
await its client calls. This moves all test code away from the deprecated
Starlette client while retaining no-network, in-process request coverage.

## Technical Details

- Remove `httpx` from `[project.dependencies]` after confirming no production
  module imports it.
- Add bounded `httpx2` and `asgi-lifespan` requirements to
  `[dependency-groups].dev`; keep runtime dependencies unchanged otherwise.
- Recreate `uv.lock` and sync with Python 3.14.
- Change the `client` fixture type to an async iterator of
  `httpx2.AsyncClient`.
- Create `httpx2.ASGITransport(app=app)` and pass it to
  `httpx2.AsyncClient(..., base_url="http://testserver")` within the
  lifespan-manager context.
- Apply `pytest.mark.anyio` to the route-test module, convert test functions to
  `async def`, and await all HTTP methods.

## What Goes Where

- **Implementation Steps** cover dependency metadata, the shared test fixture,
  route-test migration, validation, and documentation.
- **Post-Completion** contains optional local verification only; no external
  system changes are expected.

## Implementation Steps

### Task 1: Replace deprecated test-client dependencies

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`

- [x] confirm `rg` finds no `httpx` import in `app/` before removing the
      runtime dependency
- [x] remove `httpx` from `[project.dependencies]`
- [x] add bounded `httpx2` and `asgi-lifespan` entries to the `dev` dependency
      group
- [x] regenerate `uv.lock` with Python 3.14 and sync the locked environment
- [x] verify the lockfile includes the declared development dependencies and
      excludes direct `httpx` project metadata
- [x] run the transitional legacy-client suite: all 18 tests passed without the
      warning after `httpx` was removed

### Task 2: Provide an HTTPX2 ASGI route-test fixture

**Files:**
- Modify: `tests/conftest.py`
- Create: `tests/test_route_client.py`

- [x] replace the FastAPI `TestClient` import and type annotation with
      `httpx2.AsyncClient`
- [x] manage `create_app(database_path)` with `LifespanManager`, then provide
      an `AsyncClient` over `ASGITransport` with the test-server base URL
- [x] retain fixture cleanup so the HTTPX2 client closes before the application
      lifespan shuts down
- [x] write a regression test proving the fixture can make a health request and
      the lifespan-created database supports an initial application request
- [x] write a fixture error-path test that exposes the application's expected
      404 response instead of masking it
- [x] run the focused fixture tests successfully before task 3

### Task 3: Convert route tests to the asynchronous HTTPX2 interface

**Files:**
- Modify: `tests/test_routes.py`

- [x] add the AnyIO pytest marker and replace the deprecated TestClient type
      import with the HTTPX2 async client type
- [x] convert each route test to `async def` and await every client request
- [x] preserve redirect, HTMX, form-validation, and missing-route assertions
- [x] write or update success-path tests for create, update, stage, and health
      requests under the async fixture
- [x] write or update error-path tests for invalid form data, invalid URLs, and
      unavailable methods under the async fixture
- [x] run `uv run --locked pytest tests/test_routes.py` and then the full test
      suite successfully before task 4

### Task 4: Verify warning-free test execution and code quality

**Files:**
- Modify: `pyproject.toml` (only if test configuration needs an AnyIO setting)
- Modify: `docs/plans/2026-08-03-migrate-tests-to-httpx2.md`

- [x] run `uv run --locked pytest` and verify it has no
      `StarletteDeprecationWarning` entry
- [x] run `uv run --locked ruff check .` and apply only required Ruff fixes
- [x] run `uv run --locked ruff format --check .` and format with Ruff if
      needed
- [x] run `uv run --locked mypy app` with no application typing issues
- [x] record final validation: 20 tests passed, with all route success and
      error behavior retained

### Task 5: Update documentation and archive the completed plan

**Files:**
- Modify: `README.md` (only if the documented verification workflow changes)
- Modify: `AGENTS.md` (only if a durable testing convention is discovered)
- Move: `docs/plans/2026-08-03-migrate-tests-to-httpx2.md` to
  `docs/plans/completed/`

- [x] leave README unchanged because documented test commands do not change
- [x] leave AGENTS.md unchanged because it already requires uv-based test runs
- [x] ensure every plan task is complete and record final validation results:
      20 pytest tests, Ruff lint and format checks, mypy, Uvicorn smoke check,
      and `uv lock --check` all passed
- [x] move this plan to `docs/plans/completed/` after implementation completes

## Post-Completion

**Manual verification:**

- Run `uv sync --locked` in a fresh checkout, followed by
  `uv run --locked pytest`, and confirm no Starlette test-client deprecation
  warning appears.
- Start the local application with the documented Uvicorn command to confirm
  the test-only dependency change has not affected application startup.

**External system updates:**

- None expected.
