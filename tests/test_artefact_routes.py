"""Security and platform-boundary tests for the Finder handoff."""

from __future__ import annotations

import subprocess
from pathlib import Path

import httpx2
import pytest

from app.repository import Repository

pytestmark = pytest.mark.anyio


async def _prepare_directory(
    client: httpx2.AsyncClient, database_path: str, root: Path
) -> Path:
    await client.post(
        "/applications",
        data={
            "role": "Engineer",
            "company": "Acme",
            "full_jd": "Build reliable software.",
        },
    )
    application = Repository(database_path).get_application(1)
    directory = (
        root / "private" / "artefacts" / str(application["artefact_directory"])
    )
    directory.mkdir(parents=True)
    return directory


async def test_finder_handoff_is_origin_checked_and_uses_argument_array(
    client: httpx2.AsyncClient,
    database_path: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = await _prepare_directory(client, database_path, tmp_path)
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(arguments: list[str], **kwargs: object) -> None:
        calls.append((arguments, kwargs))

    # Patch the route module's platform/process boundary only.
    import app.routes.artefacts as route_module

    monkeypatch.setattr(route_module.sys, "platform", "darwin")
    monkeypatch.setattr(route_module.subprocess, "run", fake_run)

    response = await client.post(
        "/applications/1/artefacts/open",
        headers={"Origin": "http://testserver"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert calls[0][0] == ["open", str(directory)]
    assert calls[0][1]["shell"] is False


async def test_finder_handoff_rejects_forged_origin_and_non_macos(
    client: httpx2.AsyncClient,
    database_path: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _prepare_directory(client, database_path, tmp_path)
    forged = await client.post(
        "/applications/1/artefacts/open",
        headers={"Origin": "https://evil.test"},
    )
    assert forged.status_code == 403

    import app.routes.artefacts as route_module

    monkeypatch.setattr(route_module.sys, "platform", "linux")
    unsupported = await client.post(
        "/applications/1/artefacts/open",
        headers={"Origin": "http://testserver"},
    )
    assert unsupported.status_code == 501
    assert str(tmp_path) not in unsupported.text


async def test_finder_handoff_rejects_symlink_and_command_failure(
    client: httpx2.AsyncClient,
    database_path: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = await _prepare_directory(client, database_path, tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    directory.rmdir()
    directory.symlink_to(outside, target_is_directory=True)
    import app.routes.artefacts as route_module

    monkeypatch.setattr(route_module.sys, "platform", "darwin")
    symlink = await client.post(
        "/applications/1/artefacts/open",
        headers={"Origin": "http://testserver"},
    )
    assert symlink.status_code == 404

    directory.unlink()
    directory.mkdir()

    def fail_run(*args: object, **kwargs: object) -> None:
        raise subprocess.CalledProcessError(1, "open")

    monkeypatch.setattr(route_module.subprocess, "run", fail_run)
    failed = await client.post(
        "/applications/1/artefacts/open",
        headers={"Origin": "http://testserver"},
    )
    assert failed.status_code == 502
    assert str(tmp_path) not in failed.text


async def test_finder_handoff_is_post_only(client: httpx2.AsyncClient) -> None:
    response = await client.get("/applications/1/artefacts/open")

    assert response.status_code == 405
