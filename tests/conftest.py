from collections.abc import AsyncIterator
from pathlib import Path

import httpx2
import pytest
from asgi_lifespan import LifespanManager

from app.main import create_app


@pytest.fixture
def database_path(tmp_path: Path) -> str:
    return str(tmp_path / "jobhunter.db")


@pytest.fixture
async def client(database_path: str) -> AsyncIterator[httpx2.AsyncClient]:
    app = create_app(database_path)
    transport = httpx2.ASGITransport(app=app)
    async with (
        LifespanManager(app),
        httpx2.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as test_client,
    ):
        yield test_client
