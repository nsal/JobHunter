from pathlib import Path
from urllib.parse import quote

import httpx2
import pytest

pytestmark = pytest.mark.anyio


async def test_health_and_empty_register(client: httpx2.AsyncClient) -> None:
    response = await client.get("/health")
    assert response.json() == {"status": "ok"}
    assert "No applications yet" in (await client.get("/")).text


async def test_create_view_update_and_stage(client: httpx2.AsyncClient) -> None:
    response = await client.post(
        "/applications",
        data={"role": "Engineer", "company": "Acme", "is_fully_remote": "on"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    location = response.headers["location"]
    detail = await client.get(location)
    assert "Engineer at Acme" in detail.text
    response = await client.post(
        location,
        data={
            "role": "Principal Engineer",
            "company": "Acme",
            "notes": "Updated",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    stage = await client.post(
        f"{location}/stages",
        data={
            "stage": "Interview",
            "effective_from": "2099-01-01T09:00:00",
            "stage_description": "Screen",
        },
        headers={"HX-Request": "true"},
    )
    assert stage.status_code == 200
    assert "Interview" in stage.text


async def test_invalid_create_and_absent_delete_route(
    client: httpx2.AsyncClient,
) -> None:
    response = await client.post("/applications", data={"company": "Acme"})
    assert response.status_code == 422
    assert "Role is required" in response.text
    assert (await client.delete("/applications/1")).status_code == 405


async def test_htmx_create_and_update_redirect_to_application_detail(
    client: httpx2.AsyncClient,
) -> None:
    created = await client.post(
        "/applications",
        data={"role": "Engineer", "company": "Acme"},
        headers={"HX-Request": "true"},
    )
    assert created.status_code == 200
    assert created.headers["HX-Redirect"] == "/applications/1"
    assert created.text == ""

    updated = await client.post(
        "/applications/1",
        data={"role": "Principal Engineer", "company": "Acme"},
        headers={"HX-Request": "true"},
    )
    assert updated.status_code == 200
    assert updated.headers["HX-Redirect"] == "/applications/1"
    assert updated.text == ""


async def test_htmx_validation_returns_the_targeted_form_fragment(
    client: httpx2.AsyncClient,
) -> None:
    invalid_create = await client.post(
        "/applications",
        data={"company": "Acme"},
        headers={"HX-Request": "true"},
    )
    assert invalid_create.status_code == 200
    assert 'id="application-form"' in invalid_create.text
    assert "Role is required" in invalid_create.text
    assert "<!doctype html>" not in invalid_create.text

    created = await client.post(
        "/applications",
        data={"role": "Engineer", "company": "Acme"},
        follow_redirects=False,
    )
    invalid_update = await client.post(
        created.headers["location"],
        data={
            "role": "Engineer",
            "company": "Acme",
            "job_url": "javascript:alert(1)",
        },
        headers={"HX-Request": "true"},
    )
    assert invalid_update.status_code == 200
    assert 'id="application-edit-form"' in invalid_update.text
    assert (
        "Job URL must be an absolute HTTP or HTTPS URL" in invalid_update.text
    )
    assert "<!doctype html>" not in invalid_update.text


async def test_link_validation_and_optional_job_url(
    client: httpx2.AsyncClient,
) -> None:
    invalid = await client.post(
        "/applications",
        data={
            "role": "Engineer",
            "company": "Acme",
            "job_url": "javascript:alert(1)",
        },
    )
    assert invalid.status_code == 422
    assert "Job URL must be an absolute HTTP or HTTPS URL" in invalid.text

    created = await client.post(
        "/applications",
        data={"role": "Engineer", "company": "Acme", "job_url": ""},
        follow_redirects=False,
    )
    assert created.status_code == 303


async def test_cv_picker_and_delivery_routes(
    client: httpx2.AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cv_root = tmp_path / "cvs"
    cv_root.mkdir()
    folder = cv_root / "roles"
    folder.mkdir()
    special_folder_name = "Résumé & # space"
    special_folder = cv_root / special_folder_name
    special_folder.mkdir()
    nested_special_folder = special_folder / "nested"
    nested_special_folder.mkdir()
    pdf = folder / "resume.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    docx = cv_root / "resume.docx"
    docx.write_bytes(b"word content")
    (cv_root / "ignore.txt").touch()
    monkeypatch.setenv("JOBHUNTER_CV_ROOT", str(cv_root))

    picker = await client.get("/cv-picker")
    assert picker.status_code == 200
    assert "roles" in picker.text
    assert "ignore.txt" not in picker.text
    encoded_special_folder = quote(special_folder_name, safe="")
    assert f"directory={encoded_special_folder}" in picker.text

    folder_picker = await client.get("/cv-picker?directory=roles")
    assert folder_picker.status_code == 200
    assert pdf.as_uri() in folder_picker.text

    encoded_nested_special_folder = quote(
        f"{special_folder_name}/nested", safe=""
    )
    special_picker = await client.get(
        f"/cv-picker?directory={encoded_nested_special_folder}"
    )
    assert special_picker.status_code == 200
    assert f"directory={encoded_special_folder}" in special_picker.text

    created = await client.post(
        "/applications",
        data={"role": "Engineer", "company": "Acme", "cv_path": str(pdf)},
        follow_redirects=False,
    )
    location = created.headers["location"]
    preview = await client.get(f"{location}/cv/preview")
    assert preview.status_code == 200
    assert preview.headers["content-type"] == "application/pdf"
    assert "inline" in preview.headers["content-disposition"]

    updated = await client.post(
        location,
        data={"role": "Engineer", "company": "Acme", "cv_path": str(docx)},
        follow_redirects=False,
    )
    assert updated.status_code == 303
    assert (await client.get(f"{location}/cv/preview")).status_code == 422
    download = await client.get(f"{location}/cv/download")
    assert download.status_code == 200
    assert "attachment" in download.headers["content-disposition"]


async def test_cv_detail_actions_are_case_insensitive(
    client: httpx2.AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cv_root = tmp_path / "cvs"
    cv_root.mkdir()
    monkeypatch.setenv("JOBHUNTER_CV_ROOT", str(cv_root))
    pdf = cv_root / "resume.PDF"
    pdf.write_bytes(b"%PDF-1.4")
    docx = cv_root / "resume.DOCX"
    docx.write_bytes(b"word content")

    created = await client.post(
        "/applications",
        data={"role": "Engineer", "company": "Acme", "cv_path": str(pdf)},
        follow_redirects=False,
    )
    location = created.headers["location"]
    detail = await client.get(location)
    assert "Preview CV" in detail.text
    assert "Download CV" not in detail.text
    assert (await client.get(f"{location}/cv/preview")).status_code == 200

    updated = await client.post(
        location,
        data={"role": "Engineer", "company": "Acme", "cv_path": str(docx)},
        follow_redirects=False,
    )
    assert updated.status_code == 303
    detail = await client.get(location)
    assert "Download CV" in detail.text
    assert "Preview CV" not in detail.text


async def test_cv_routes_reject_invalid_picker_and_legacy_paths(
    client: httpx2.AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cv_root = tmp_path / "cvs"
    cv_root.mkdir()
    monkeypatch.setenv("JOBHUNTER_CV_ROOT", str(cv_root))
    assert (
        await client.get("/cv-picker?directory=../outside")
    ).status_code == 400

    created = await client.post(
        "/applications",
        data={"role": "Engineer", "company": "Acme"},
        follow_redirects=False,
    )
    assert (
        await client.get(f"{created.headers['location']}/cv/download")
    ).status_code == 404


async def test_dashboard_overlay_search_dates_and_previews(
    client: httpx2.AsyncClient,
) -> None:
    description = "D" * 301
    notes = "N" * 301
    created = await client.post(
        "/applications",
        data={
            "role": "Engineer",
            "company": "Acme",
            "notes": notes,
            "full_jd": description,
        },
        follow_redirects=False,
    )
    location = created.headers["location"]

    dashboard = await client.get("/?q=acme")
    assert "data-open-application-dialog" in dashboard.text
    assert 'value="acme"' in dashboard.text
    assert "T" not in dashboard.text
    assert ("N" * 300) + "…" in dashboard.text

    form = await client.get("/applications/new", headers={"HX-Request": "true"})
    assert form.status_code == 200
    assert 'id="application-form"' in form.text
    assert "form-grid" in form.text

    detail = await client.get(location)
    assert "<details>" in detail.text
    assert ("D" * 300) + "…" in detail.text
