from datetime import UTC, datetime
from pathlib import Path

import httpx2
import pytest

pytestmark = pytest.mark.anyio

APPLICATION_DATA = {
    "role": "Engineer",
    "company": "Acme",
    "full_jd": "Build reliable software.",
}


async def test_health_and_empty_register(client: httpx2.AsyncClient) -> None:
    response = await client.get("/health")
    assert response.json() == {"status": "ok"}
    assert "No applications yet" in (await client.get("/")).text


async def test_static_assets_revalidate_on_normal_refresh(
    client: httpx2.AsyncClient,
) -> None:
    stylesheet = await client.get("/static/app.css")

    assert stylesheet.status_code == 200
    assert stylesheet.headers["cache-control"] == "no-cache"
    assert ".applications-table" in stylesheet.text


async def test_create_starts_assessing_with_nullable_submission(
    client: httpx2.AsyncClient,
) -> None:
    response = await client.post(
        "/applications",
        data={**APPLICATION_DATA, "is_fully_remote": "on"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    location = response.headers["location"]
    detail = await client.get(location)
    dashboard = await client.get("/")
    assert "Engineer at Acme" in detail.text
    assert "Current stage: <strong>Assessing</strong>" in detail.text
    assert "Build reliable software." in detail.text
    assert "<dt>Submitted</dt><dd>—</dd>" in detail.text
    assert "Assessing" in dashboard.text
    assert 'class="date-cell" data-label="Submitted"' in dashboard.text


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (
            {"company": "Acme", "full_jd": "Build software."},
            "Role is required",
        ),
        (
            {"role": "Engineer", "full_jd": "Build software."},
            "Company is required",
        ),
        (
            {"role": "Engineer", "company": "Acme"},
            "Full job description is required",
        ),
    ],
)
async def test_create_requires_role_company_and_jd(
    client: httpx2.AsyncClient,
    data: dict[str, str],
    message: str,
) -> None:
    response = await client.post("/applications", data=data)

    assert response.status_code == 422
    assert message in response.text


async def test_create_form_requires_jd_and_has_no_cv_controls(
    client: httpx2.AsyncClient,
) -> None:
    form = await client.get("/applications/new", headers={"HX-Request": "true"})

    assert form.status_code == 200
    assert 'id="application-form"' in form.text
    assert 'name="full_jd" required' in form.text
    assert 'name="cv_path"' not in form.text
    assert 'name="cv_upload"' not in form.text
    assert 'type="file"' not in form.text
    assert 'enctype="multipart/form-data"' not in form.text
    assert 'data-close-dialog="application-dialog"' in form.text


async def test_htmx_create_and_update_redirect_to_application_detail(
    client: httpx2.AsyncClient,
) -> None:
    created = await client.post(
        "/applications",
        data=APPLICATION_DATA,
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


async def test_htmx_validation_returns_targeted_form_fragment(
    client: httpx2.AsyncClient,
) -> None:
    invalid_create = await client.post(
        "/applications",
        data={"company": "Acme", "full_jd": "JD"},
        headers={"HX-Request": "true"},
    )
    assert invalid_create.status_code == 200
    assert 'id="application-form"' in invalid_create.text
    assert "Role is required" in invalid_create.text
    assert "<!doctype html>" not in invalid_create.text

    await client.post("/applications", data=APPLICATION_DATA)
    invalid_update = await client.post(
        "/applications/1",
        data={
            "role": "Engineer",
            "company": "Acme",
            "job_url": "javascript:alert(1)",
        },
        headers={"HX-Request": "true"},
    )
    assert invalid_update.status_code == 200
    assert 'id="application-edit-form"' in invalid_update.text
    assert "Job URL must be an absolute HTTP or HTTPS URL" in (
        invalid_update.text
    )
    assert "<!doctype html>" not in invalid_update.text


async def test_edit_updates_metadata_but_keeps_jd_immutable(
    client: httpx2.AsyncClient,
) -> None:
    created = await client.post(
        "/applications", data=APPLICATION_DATA, follow_redirects=False
    )
    location = created.headers["location"]

    updated = await client.post(
        location,
        data={
            "role": "Principal Engineer",
            "company": "Acme Ltd",
            "payment": "100k",
            "notes": "Updated",
            "full_jd": "Attempted replacement",
        },
        follow_redirects=False,
    )
    assert updated.status_code == 303

    detail = await client.get(location)
    assert "Principal Engineer at Acme Ltd" in detail.text
    assert "100k" in detail.text
    assert "Updated" in detail.text
    assert "Build reliable software." in detail.text
    assert "Attempted replacement" not in detail.text
    assert 'name="full_jd"' not in detail.text


async def test_job_url_validation_and_safe_rendering(
    client: httpx2.AsyncClient,
) -> None:
    invalid = await client.post(
        "/applications",
        data={**APPLICATION_DATA, "job_url": "javascript:alert(1)"},
    )
    assert invalid.status_code == 422
    assert "Job URL must be an absolute HTTP or HTTPS URL" in invalid.text

    created = await client.post(
        "/applications",
        data={
            **APPLICATION_DATA,
            "job_url": "https://jobs.example.test/engineer",
        },
        follow_redirects=False,
    )
    dashboard = await client.get("/")
    detail = await client.get(created.headers["location"])
    assert 'href="https://jobs.example.test/engineer" target="_blank"' in (
        dashboard.text
    )
    assert 'rel="noopener noreferrer">URL</a>' in dashboard.text
    assert 'href="https://jobs.example.test/engineer" target="_blank"' in (
        detail.text
    )


async def test_legacy_cv_behavior_is_absent(
    client: httpx2.AsyncClient,
) -> None:
    created = await client.post(
        "/applications",
        data={**APPLICATION_DATA, "cv_path": "/tmp/resume.pdf"},
        follow_redirects=False,
    )
    assert created.status_code == 303
    location = created.headers["location"]

    dashboard = await client.get("/")
    detail = await client.get(location)
    assert ">CV<" not in dashboard.text
    assert "Preview CV" not in dashboard.text
    assert "Download CV" not in dashboard.text
    assert "<dt>CV</dt>" not in detail.text
    assert (await client.get(f"{location}/cv/preview")).status_code == 404
    assert (await client.get(f"{location}/cv/download")).status_code == 404


async def test_explicit_submitted_transition_populates_date(
    client: httpx2.AsyncClient,
) -> None:
    created = await client.post(
        "/applications", data=APPLICATION_DATA, follow_redirects=False
    )
    location = created.headers["location"]
    detail = await client.get(location)
    assert '<option value="Submitted"' in detail.text

    submitted = await client.post(
        f"{location}/stages",
        data={
            "stage": "Submitted",
            "effective_from": "2099-01-01T09:00:00",
            "stage_description": "Applied directly",
        },
        headers={"HX-Request": "true"},
    )
    assert submitted.status_code == 200
    assert "Current stage: <strong>Submitted</strong>" in submitted.text

    dashboard = await client.get("/")
    detail = await client.get(location)
    assert "2099-01-01" in dashboard.text
    assert "<dt>Submitted</dt><dd>2099-01-01</dd>" in detail.text


async def test_stage_routes_reject_invalid_transitions(
    client: httpx2.AsyncClient,
) -> None:
    created = await client.post(
        "/applications", data=APPLICATION_DATA, follow_redirects=False
    )
    location = created.headers["location"]

    blank = await client.post(
        f"{location}/stages",
        data={"stage": "", "effective_from": "2099-01-01T09:00:00"},
        headers={"HX-Request": "true"},
    )
    assert blank.status_code == 422
    assert "Choose a new stage." in blank.text
    assert "Current stage: <strong>Assessing</strong>" in blank.text

    invalid = await client.post(
        f"{location}/stages",
        data={"stage": "Invalid", "effective_from": "2099-01-01T09:00:00"},
        headers={"HX-Request": "true"},
    )
    assert invalid.status_code == 422
    assert "Choose a valid stage." in invalid.text

    backdated = await client.post(
        f"{location}/stages",
        data={"stage": "Submitted", "effective_from": "2000-01-01T00:00:00"},
        headers={"HX-Request": "true"},
    )
    assert backdated.status_code == 422
    assert "cannot precede" in backdated.text


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/applications/999"),
        ("POST", "/applications/999"),
        ("POST", "/applications/999/stages"),
        ("GET", "/applications/999/stage-editor"),
        ("POST", "/applications/999/stage-editor"),
        ("GET", "/applications/999/notes-editor"),
        ("POST", "/applications/999/notes-editor"),
    ],
)
async def test_missing_application_routes_return_404(
    client: httpx2.AsyncClient, method: str, path: str
) -> None:
    data = {
        "role": "Engineer",
        "company": "Acme",
        "stage": "Submitted",
        "effective_from": "2099-01-01T09:00:00",
    }

    response = await client.request(method, path, data=data)

    assert response.status_code == 404


async def test_dashboard_overlay_search_dates_and_long_jd_preview(
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

    dashboard = await client.get("/?q=acme")
    today = datetime.now(UTC).date().isoformat()
    assert "data-open-application-dialog" in dashboard.text
    assert 'value="acme"' in dashboard.text
    assert today in dashboard.text
    assert notes in dashboard.text
    assert 'class="button search-action"' in dashboard.text
    assert 'class="date-cell" data-label="Submitted"' in dashboard.text

    detail = await client.get(created.headers["location"])
    assert "<details>" in detail.text
    assert ("D" * 300) + "…" in detail.text


async def test_dashboard_table_markup_and_agency_labels(
    client: httpx2.AsyncClient,
) -> None:
    created = await client.post(
        "/applications",
        data={**APPLICATION_DATA, "is_recruiter": "on"},
        follow_redirects=False,
    )

    dashboard = await client.get("/")
    headers = (
        "Role",
        "Company",
        "Pay",
        "URL",
        "Agency",
        "Remote",
        "Stage",
        "Notes",
        "Submitted",
        "Updated",
    )
    assert '<main class="dashboard-main">' in dashboard.text
    assert dashboard.text.count('<col class="application-') == len(headers)
    assert dashboard.text.count("<tr") == 2
    for header in headers:
        assert f">{header}<" in dashboard.text
    assert '<abbr title="Agency flag">Agency</abbr>' in dashboard.text
    assert "Recruiter" not in dashboard.text

    detail = await client.get(created.headers["location"])
    assert "<dt>Agency</dt><dd>Yes</dd>" in detail.text
    assert 'name="is_recruiter"' in detail.text


async def test_dashboard_stage_and_notes_editors(
    client: httpx2.AsyncClient,
) -> None:
    created = await client.post(
        "/applications",
        data={**APPLICATION_DATA, "notes": "Old notes"},
        follow_redirects=False,
    )
    location = created.headers["location"]

    editor = await client.get(f"{location}/stage-editor")
    assert editor.status_code == 200
    assert '<option value="Assessing" selected>' in editor.text
    updated_stage = await client.post(
        f"{location}/stage-editor",
        data={"stage": "Mismatch", "stage_description": "Mandatory gap"},
        headers={"HX-Request": "true"},
    )
    assert updated_stage.status_code == 200
    assert "Mismatch" in updated_stage.text
    assert updated_stage.headers["HX-Trigger"] == "close-stage-editor"

    notes_editor = await client.get(f"{location}/notes-editor")
    assert "Old notes" in notes_editor.text
    updated_notes = await client.post(
        f"{location}/notes-editor",
        data={"notes": "Updated notes"},
        headers={"HX-Request": "true"},
    )
    assert updated_notes.status_code == 200
    assert "Updated notes" in updated_notes.text
    assert updated_notes.headers["HX-Trigger"] == "close-notes-editor"


def test_dashboard_styles_preserve_compact_table_layout() -> None:
    stylesheet = Path("app/static/app.css").read_text()

    assert ".button, button" in stylesheet
    assert "font-family: system-ui, sans-serif" in stylesheet
    assert ".applications-table-header th" in stylesheet
    assert ".stage-history-table" in stylesheet
    assert ".applications-table-wrap { overflow-x: hidden; }" in stylesheet
    assert ".applications-table { min-width: 0; table-layout: fixed; }" in (
        stylesheet
    )
    assert ".applications-table .application-notes-cell .notes-edit" in (
        stylesheet
    )
