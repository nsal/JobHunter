"""HTTP tests for setup readiness and remote profile-sharing consent."""

from __future__ import annotations

from pathlib import Path

import httpx2
import pytest
from asgi_lifespan import LifespanManager
from docx import Document

from app.ai.providers.factory import is_openai_credential_ready
from app.main import create_app
from app.routes.setup import normalize_configured_origin, normalize_http_origin

pytestmark = pytest.mark.anyio


APPLICATION_DATA = {
    "role": "Engineer",
    "company": "Acme",
    "full_jd": "Build reliable software.",
}


def write_valid_private_inputs(root: Path) -> None:
    """Create synthetic private inputs for readiness route tests."""
    profile_root = root / "private" / "profile"
    profile_root.mkdir(parents=True)
    (profile_root / "profile.md").write_text(
        "# Test Person\n\nPython engineer.\n", encoding="utf-8"
    )
    document = Document()
    document.add_paragraph("Template")
    document.save(str(profile_root / "cv-template.docx"))
    example_layout = Path("examples/profile/cv-layout.example.yaml")
    (profile_root / "cv-layout.yaml").write_text(
        example_layout.read_text(encoding="utf-8"), encoding="utf-8"
    )


async def test_setup_page_shows_safe_ready_state_and_navigation(
    client: httpx2.AsyncClient,
) -> None:
    response = await client.get("/setup")

    assert response.status_code == 200
    assert "Setup" in response.text
    assert "gpt-5-mini" in response.text
    assert "Profile" in response.text
    assert "Configured" in response.text
    assert "Acknowledged" in response.text
    assert 'href="/setup"' in (await client.get("/")).text


async def test_acknowledgement_and_revocation_are_idempotent(
    client: httpx2.AsyncClient,
) -> None:
    revoke = await client.post(
        "/setup/revoke", headers={"Origin": "http://testserver"}
    )
    assert revoke.status_code == 303
    assert "Revoked" in (await client.get("/setup")).text

    acknowledge = await client.post(
        "/setup/acknowledge", headers={"Origin": "http://testserver"}
    )
    repeated_acknowledge = await client.post(
        "/setup/acknowledge", headers={"Origin": "http://testserver"}
    )
    assert acknowledge.status_code == 303
    assert repeated_acknowledge.status_code == 303
    assert "Acknowledged" in (await client.get("/setup")).text

    repeated_revoke = await client.post(
        "/setup/revoke", headers={"Origin": "http://testserver"}
    )
    assert repeated_revoke.status_code == 303


@pytest.mark.parametrize("path", ["/setup/acknowledge", "/setup/revoke"])
async def test_consent_actions_reject_forged_or_missing_origin(
    client: httpx2.AsyncClient, path: str
) -> None:
    forged = await client.post(path, headers={"Origin": "https://evil.test"})
    missing = await client.post(path)

    assert forged.status_code == 403
    assert missing.status_code == 403
    assert "Unsafe request origin" in forged.text


@pytest.mark.parametrize("path", ["/setup/acknowledge", "/setup/revoke"])
@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "http://testserver:8001"},
        {"Origin": "http://testserver/path"},
        {"Origin": "http://testserver?forged=true"},
        {"Origin": "https://testserver"},
        {"Host": "evil.test", "Origin": "http://evil.test"},
    ],
)
async def test_consent_actions_reject_origins_not_in_configured_state(
    client: httpx2.AsyncClient, path: str, headers: dict[str, str]
) -> None:
    response = await client.post(path, headers=headers)

    assert response.status_code == 403
    assert "Unsafe request origin" in response.text
    assert "Acknowledged" in (await client.get("/setup")).text


def test_http_origin_normalization_handles_default_ports_and_ipv6() -> None:
    assert normalize_http_origin("example.test", 80) == "http://example.test"
    assert normalize_http_origin("::1", 8123) == "http://[::1]:8123"
    assert normalize_http_origin("0:0:0:0:0:0:0:1", 8123) == "http://[::1]:8123"
    assert normalize_configured_origin("http://[::1]:80") == "http://[::1]"
    assert (
        normalize_configured_origin("http://[0:0:0:0:0:0:0:1]:8123")
        == "http://[::1]:8123"
    )


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "[::]"])
def test_http_origin_rejects_unspecified_bind_addresses(host: str) -> None:
    with pytest.raises(ValueError, match="concrete address"):
        normalize_http_origin(host, 8000)


@pytest.mark.parametrize("value", [None, "", " ", " key ", "valid-key"])
def test_openai_credential_predicate_has_one_setup_rule(
    value: str | None,
) -> None:
    environment = {} if value is None else {"OPENAI_API_KEY": value}

    assert is_openai_credential_ready(environment) == (value == "valid-key")


async def test_incomplete_setup_is_safe_and_blocks_new_application(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    database_path = tmp_path / "jobhunter.db"
    app = create_app(database_path, project_root=tmp_path)

    async with (
        LifespanManager(app),
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url="http://testserver",
        ) as test_client,
    ):
        setup = await test_client.get("/setup")
        create = await test_client.post("/applications", data=APPLICATION_DATA)

    assert setup.status_code == 200
    assert "Missing or invalid" in setup.text
    assert "OpenAI credential is missing or invalid" in setup.text
    assert str(tmp_path) not in setup.text
    assert "# Test Person" not in setup.text
    assert create.status_code == 422
    assert "Open setup" in create.text
    assert "Complete the required setup" in create.text


async def test_private_input_corruption_is_redacted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    profile_root = tmp_path / "private" / "profile"
    profile_root.mkdir(parents=True)
    (profile_root / "profile.md").write_bytes(b"\xff")
    (profile_root / "cv-template.docx").write_bytes(b"invalid")
    (profile_root / "cv-layout.yaml").write_text(
        "not: [valid", encoding="utf-8"
    )
    app = create_app(tmp_path / "jobhunter.db", project_root=tmp_path)

    async with (
        LifespanManager(app),
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url="http://testserver",
        ) as test_client,
    ):
        response = await test_client.get("/setup/status")

    assert response.status_code == 200
    assert "missing or invalid" in response.text.lower()
    assert response.text.count("Missing or invalid") == 4
    assert "<dt>AI settings</dt>\n    <dd>Missing or invalid</dd>" in (
        response.text
    )
    assert str(tmp_path) not in response.text
    assert "\xff" not in response.text


@pytest.mark.parametrize("invalid_input", ["profile", "template", "layout"])
async def test_private_readiness_reports_each_input_independently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invalid_input: str,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    write_valid_private_inputs(tmp_path)
    profile_root = tmp_path / "private" / "profile"
    if invalid_input == "profile":
        (profile_root / "profile.md").write_bytes(b"\xff")
    elif invalid_input == "template":
        (profile_root / "cv-template.docx").write_bytes(b"invalid")
    else:
        (profile_root / "cv-layout.yaml").write_text(
            "not: [valid", encoding="utf-8"
        )
    app = create_app(tmp_path / "jobhunter.db", project_root=tmp_path)

    async with (
        LifespanManager(app),
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url="http://testserver",
        ) as test_client,
    ):
        response = await test_client.get("/setup/status")

    assert response.status_code == 200
    assert response.text.count("Missing or invalid") == 2
    for name in ("Profile", "CV template", "CV layout"):
        expected = (
            "Missing or invalid" if invalid_input in name.lower() else "Ready"
        )
        assert f"<dt>{name}</dt>\n    <dd>{expected}</dd>" in response.text


async def test_application_preflight_succeeds_when_setup_is_ready(
    client: httpx2.AsyncClient,
) -> None:
    response = await client.post(
        "/applications", data=APPLICATION_DATA, follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/applications/1"


async def test_missing_openai_credential_blocks_application_preflight(
    client: httpx2.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    response = await client.post("/applications", data=APPLICATION_DATA)

    assert response.status_code == 422
    assert "Configure the OpenAI credential" in response.text
    assert "Open setup" in response.text


@pytest.mark.parametrize("htmx", [False, True])
async def test_setup_blocking_renders_one_alert_and_retains_values(
    client: httpx2.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    htmx: bool,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    headers = {"HX-Request": "true"} if htmx else {}

    response = await client.post(
        "/applications",
        data={**APPLICATION_DATA, "role": "Submitted role"},
        headers=headers,
    )

    assert response.status_code == (200 if htmx else 422)
    assert response.text.count('role="alert"') == 1
    assert response.text.count("Open setup") == 1
    assert 'value="Submitted role"' in response.text
