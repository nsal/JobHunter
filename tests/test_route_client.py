"""Regression tests for the HTTPX2 route-test fixture."""

import httpx2
import pytest

pytestmark = pytest.mark.anyio


async def test_client_runs_application_lifespan(
    client: httpx2.AsyncClient,
) -> None:
    """The fixture starts the app before making in-process requests."""
    health = await client.get("/health")
    register = await client.get("/")

    assert health.json() == {"status": "ok"}
    assert "No applications yet" in register.text


async def test_client_preserves_application_http_errors(
    client: httpx2.AsyncClient,
) -> None:
    """Expected application HTTP errors remain observable to route tests."""
    response = await client.get("/applications/999")

    assert response.status_code == 404
