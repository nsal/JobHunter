from collections.abc import AsyncIterator
from pathlib import Path

import httpx2
import pytest
from asgi_lifespan import LifespanManager
from docx import Document

from app.consent import ConsentRepository
from app.main import create_app


@pytest.fixture
def database_path(tmp_path: Path) -> str:
    return str(tmp_path / "jobhunter.db")


@pytest.fixture
async def client(
    database_path: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[httpx2.AsyncClient]:
    project_root = Path(database_path).parent
    profile_root = project_root / "private" / "profile"
    profile_root.mkdir(parents=True)
    (profile_root / "profile.md").write_text(
        "# Test Person\n\nPython engineer.\n", encoding="utf-8"
    )
    document = Document()
    document.add_paragraph("Template")
    document.save(str(profile_root / "cv-template.docx"))
    examples_root = Path(__file__).parent.parent / "examples" / "profile"
    (profile_root / "cv-layout.yaml").write_text(
        (examples_root / "cv-layout.example.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    config_root = project_root / "config"
    config_root.mkdir()
    (config_root / "ai.yaml").write_text(
        (Path(__file__).parent.parent / "config" / "ai.yaml").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = create_app(database_path, project_root=project_root)
    transport = httpx2.ASGITransport(app=app)
    async with (
        LifespanManager(app),
        httpx2.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as test_client,
    ):
        ConsentRepository(database_path).acknowledge_openai_profile_sharing(
            "2026-01-01T00:00:00Z"
        )
        yield test_client
