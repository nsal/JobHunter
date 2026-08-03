from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def database_path(tmp_path: Path) -> str:
    return str(tmp_path / "jobhunter.db")


@pytest.fixture
def client(database_path: str) -> Iterator[TestClient]:
    with TestClient(create_app(database_path)) as test_client:
        yield test_client
