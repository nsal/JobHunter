from pathlib import Path

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


async def test_cv_delivery_routes(
    client: httpx2.AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cv_root = tmp_path / "cvs"
    cv_root.mkdir()
    pdf = cv_root / "resume.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    docx = cv_root / "resume.docx"
    docx.write_bytes(b"word content")
    monkeypatch.setenv("JOBHUNTER_CV_ROOT", str(cv_root))

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


async def test_cv_routes_reject_missing_paths(
    client: httpx2.AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cv_root = tmp_path / "cvs"
    cv_root.mkdir()
    monkeypatch.setenv("JOBHUNTER_CV_ROOT", str(cv_root))
    created = await client.post(
        "/applications",
        data={"role": "Engineer", "company": "Acme"},
        follow_redirects=False,
    )
    assert (
        await client.get(f"{created.headers['location']}/cv/download")
    ).status_code == 404


async def test_native_cv_upload_storage_and_replacement(
    client: httpx2.AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cv_root = tmp_path / "artefacts"
    monkeypatch.setenv("JOBHUNTER_CV_ROOT", str(cv_root))
    created = await client.post(
        "/applications",
        data={"role": "Engineer", "company": "Acme / Europe"},
        files={"cv_upload": ("resume.pdf", b"%PDF-1.4", "application/pdf")},
        follow_redirects=False,
    )
    assert created.status_code == 303
    location = created.headers["location"]
    stored = list(cv_root.rglob("resume.pdf"))
    assert len(stored) == 1
    assert stored[0].relative_to(cv_root).parts[:2] == (
        "Acme-Europe",
        "2026-08-04 Engineer",
    )
    assert (await client.get(f"{location}/cv/preview")).status_code == 200

    preserved = await client.post(
        location,
        data={"role": "Engineer", "company": "Acme / Europe"},
        follow_redirects=False,
    )
    assert preserved.status_code == 303
    assert (await client.get(f"{location}/cv/preview")).status_code == 200

    replaced = await client.post(
        location,
        data={"role": "Engineer", "company": "Acme / Europe"},
        files={
            "cv_upload": (
                "resume.docx",
                b"word content",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
        follow_redirects=False,
    )
    assert replaced.status_code == 303
    assert (await client.get(f"{location}/cv/preview")).status_code == 422
    assert (await client.get(f"{location}/cv/download")).status_code == 200


async def test_native_cv_upload_rejects_unsafe_input_without_files(
    client: httpx2.AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cv_root = tmp_path / "artefacts"
    monkeypatch.setenv("JOBHUNTER_CV_ROOT", str(cv_root))
    invalid_type = await client.post(
        "/applications",
        data={"role": "Engineer", "company": "Acme"},
        files={"cv_upload": ("resume.txt", b"not a CV", "text/plain")},
    )
    assert invalid_type.status_code == 422
    assert "CV must be a PDF, DOC, or DOCX file" in invalid_type.text

    empty_upload = await client.post(
        "/applications",
        data={"role": "Engineer", "company": "Acme"},
        files={"cv_upload": ("resume.pdf", b"", "application/pdf")},
    )
    assert empty_upload.status_code == 422
    assert "CV upload must not be empty" in empty_upload.text

    invalid_application = await client.post(
        "/applications",
        data={"company": "Acme"},
        files={"cv_upload": ("resume.pdf", b"%PDF-1.4", "application/pdf")},
    )
    assert invalid_application.status_code == 422
    assert list(cv_root.rglob("*.pdf")) == []


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
    assert "2026-08-04" in dashboard.text
    assert notes in dashboard.text
    assert ("N" * 300) + "…" not in dashboard.text
    assert 'class="search-action"' in dashboard.text
    assert 'class="button search-action"' in dashboard.text
    assert 'class="date-cell" data-label="Submitted"' in dashboard.text
    assert 'datetime="2026-08-04T' in dashboard.text

    form = await client.get("/applications/new", headers={"HX-Request": "true"})
    assert form.status_code == 200
    assert 'id="application-form"' in form.text
    assert "form-grid" in form.text
    assert 'id="application-cv-upload"' in form.text
    assert 'class="file-picker-input"' in form.text
    assert 'type="file"' in form.text
    assert 'name="cv_upload"' in form.text
    assert "data-cv-upload" in form.text
    assert "data-cv-status" in form.text
    assert "No file selected" in form.text
    assert "PDF, DOC, or DOCX up to 10 MiB" in form.text
    assert (
        'aria-describedby="application-cv-status application-cv-guidance"'
        in (form.text)
    )
    assert ">Close</button>" not in form.text
    assert 'data-close-dialog="application-dialog"' in form.text
    assert 'enctype="multipart/form-data"' in form.text

    direct_form = await client.get("/applications/new")
    assert 'class="button" href="/">Cancel</a>' in direct_form.text
    assert "cv-picker-dialog" not in direct_form.text

    detail = await client.get(location)
    assert "<details>" in detail.text
    assert ("D" * 300) + "…" in detail.text


async def test_dashboard_uses_consistent_table_markup_and_safe_job_links(
    client: httpx2.AsyncClient,
) -> None:
    created = await client.post(
        "/applications",
        data={
            "role": "Engineer",
            "company": "Acme",
            "job_url": "https://jobs.example.test/engineer",
        },
        follow_redirects=False,
    )
    assert created.status_code == 303

    dashboard = await client.get("/")
    assert '<main class="dashboard-main">' in dashboard.text
    assert 'class="applications-table"' in dashboard.text
    assert 'class="applications-table-header"' in dashboard.text
    assert 'class="table-wrap applications-table-wrap"' in dashboard.text
    headers = (
        "Role",
        "Company",
        "Pay",
        "URL",
        "CV",
        "Agency",
        "Remote",
        "Stage",
        "Notes",
        "Submitted",
        "Updated",
    )
    assert dashboard.text.count('<col class="application-') == len(headers)
    assert '<tbody id="applications">' in dashboard.text
    assert '<tr id="application-1">' in dashboard.text
    assert 'class="application-group"' not in dashboard.text
    assert dashboard.text.count("<tr") == 2
    for header in headers:
        assert f">{header}<" in dashboard.text
    assert 'data-label="Submitted"' in dashboard.text
    assert 'data-label="Updated"' in dashboard.text
    assert 'href="https://jobs.example.test/engineer" target="_blank"' in (
        dashboard.text
    )
    assert 'rel="noopener noreferrer">URL</a>' in dashboard.text

    detail = await client.get(created.headers["location"])
    assert 'href="https://jobs.example.test/engineer" target="_blank"' in (
        detail.text
    )
    assert (
        'rel="noopener noreferrer">https://jobs.example.test/engineer</a>'
        in (detail.text)
    )


async def test_dashboard_uses_an_absent_marker_for_missing_job_urls(
    client: httpx2.AsyncClient,
) -> None:
    created = await client.post(
        "/applications",
        data={"role": "Engineer", "company": "Acme"},
        follow_redirects=False,
    )
    assert created.status_code == 303

    dashboard = await client.get("/")
    assert ">URL</a>" not in dashboard.text
    assert 'data-label="URL"' in dashboard.text
    assert "—" in dashboard.text

    detail = await client.get(created.headers["location"])
    assert "<dt>Job URL</dt>" in detail.text
    assert 'href="https://' not in detail.text


async def test_stage_history_uses_compact_layout_classes_for_multiple_entries(
    client: httpx2.AsyncClient,
) -> None:
    created = await client.post(
        "/applications",
        data={"role": "Engineer", "company": "Acme"},
        follow_redirects=False,
    )
    location = created.headers["location"]
    for stage, timestamp in (
        ("Viewed", "2099-01-01T09:00:00"),
        ("Call", "2099-01-02T09:00:00"),
        ("Interview", "2099-01-03T09:00:00"),
    ):
        updated = await client.post(
            f"{location}/stages",
            data={"stage": stage, "effective_from": timestamp},
            follow_redirects=False,
        )
        assert updated.status_code == 200

    detail = await client.get(location)
    assert 'class="stage-history"' in detail.text
    assert 'class="stage-history-form"' in detail.text
    assert 'class="stage-history-table"' in detail.text
    assert "Interview" in detail.text


async def test_stage_history_requires_a_non_submitted_stage(
    client: httpx2.AsyncClient,
) -> None:
    created = await client.post(
        "/applications",
        data={"role": "Engineer", "company": "Acme"},
        follow_redirects=False,
    )
    location = created.headers["location"]
    detail = await client.get(location)
    assert '<option value="" disabled selected>' in detail.text
    assert '<option value="Submitted"' not in detail.text

    blank = await client.post(
        f"{location}/stages",
        data={"stage": "", "effective_from": "2099-01-01T09:00:00"},
        headers={"HX-Request": "true"},
    )
    assert blank.status_code == 422
    assert "Choose a new stage." in blank.text
    assert "Current stage: <strong>Submitted</strong>" in blank.text

    submitted = await client.post(
        f"{location}/stages",
        data={"stage": "Submitted", "effective_from": "2099-01-01T09:00:00"},
        headers={"HX-Request": "true"},
    )
    assert submitted.status_code == 422
    assert "Submitted is created with the application." in submitted.text
    assert "Current stage: <strong>Submitted</strong>" in submitted.text

    valid = await client.post(
        f"{location}/stages",
        data={"stage": "Viewed", "effective_from": "2099-01-01T09:00:00"},
        headers={"HX-Request": "true"},
    )
    assert valid.status_code == 200
    assert "Current stage: <strong>Viewed</strong>" in valid.text


def test_dashboard_styles_define_shared_typography_and_compact_history() -> (
    None
):
    stylesheet = Path("app/static/app.css").read_text()
    assert ".button, button" in stylesheet
    assert "font-family: system-ui, sans-serif" in stylesheet
    assert ".applications-table-header th" in stylesheet
    assert "vertical-align: middle" in stylesheet
    assert "white-space: nowrap" in stylesheet
    assert ".stage-history-table" in stylesheet
    assert "padding: .35rem .45rem" in stylesheet
    assert ".applications-table-wrap { overflow-x: hidden; }" in stylesheet
    assert ".applications-table { min-width: 0; table-layout: fixed; }" in (
        stylesheet
    )
    assert "font-size: .8125rem" in stylesheet
    assert "main.dashboard-main" in stylesheet
    assert "max-width: none" in stylesheet
    assert "overflow-wrap: anywhere" in stylesheet
    assert ".applications-table .table-cell-content" in stylesheet
    assert "-webkit-line-clamp: 2" in stylesheet
    assert "box-sizing: border-box" in stylesheet
    assert "text-overflow: ellipsis" in stylesheet
    assert "width: 100%" in stylesheet
    assert ".applications-table .cv-preview-button" in stylesheet
    assert ".applications-table td::before" in stylesheet
    assert "content: attr(data-label)" in stylesheet
    assert ".applications-table .date-cell" in stylesheet
    assert "min-width: 0" in stylesheet


def test_new_application_upload_status_uses_accessible_client_markup() -> None:
    stylesheet = Path("app/static/app.css").read_text()
    script = Path("app/static/app.js").read_text()
    assert ".file-upload-status, .file-upload-guidance" in stylesheet
    assert "font-size: .65rem" in stylesheet
    assert ".file-picker-input" in stylesheet
    assert ".file-picker-button:focus-within" in stylesheet
    assert "outline: 3px solid" in stylesheet
    assert "input.dataset.cvUpload === undefined" in script
    assert '"No file selected"' in script


async def test_dashboard_clamps_long_application_values_to_two_lines(
    client: httpx2.AsyncClient,
) -> None:
    role = "Principal Platform Engineer with a Very Long Title"
    company = "International Example Company with a Long Legal Name"
    stage_note = "First interview with several team members and a long agenda"
    notes = "N" * 301
    created = await client.post(
        "/applications",
        data={
            "role": role,
            "company": company,
            "notes": notes,
        },
        follow_redirects=False,
    )
    location = created.headers["location"]
    updated = await client.post(
        f"{location}/stage-editor",
        data={"stage": "Submitted", "stage_description": stage_note},
        follow_redirects=False,
    )
    assert updated.status_code == 303

    dashboard = await client.get("/")
    assert role in dashboard.text
    assert company in dashboard.text
    assert stage_note in dashboard.text
    assert notes in dashboard.text
    assert f"{notes[:300]}…" not in dashboard.text
    assert dashboard.text.count("<tr") == 2
    assert 'data-label="Stage"' in dashboard.text
    assert 'data-label="Stage note"' not in dashboard.text
    assert f'title="Submitted — {stage_note}"' in dashboard.text
    assert 'data-label="Notes"' in dashboard.text
    assert f'title="{notes}"' in dashboard.text
    assert 'data-label="Submitted"' in dashboard.text
    assert 'data-label="Updated"' in dashboard.text


async def test_agency_labels_preserve_the_existing_recruiter_field(
    client: httpx2.AsyncClient,
) -> None:
    created = await client.post(
        "/applications",
        data={"role": "Engineer", "company": "Acme", "is_recruiter": "on"},
        follow_redirects=False,
    )
    assert created.status_code == 303
    location = created.headers["location"]

    create_form = await client.get("/applications/new")
    assert 'name="is_recruiter"' in create_form.text
    assert "Agency" in create_form.text
    assert "Recruiter" not in create_form.text

    dashboard = await client.get("/")
    assert '<abbr title="Agency flag">Agency</abbr>' in dashboard.text
    assert "Recruiter" not in dashboard.text
    assert '<span class="table-cell-content">Yes</span>' in dashboard.text

    detail = await client.get(location)
    assert "<dt>Agency</dt><dd>Yes</dd>" in detail.text
    assert "Recruiter" not in detail.text
    assert 'name="is_recruiter"' in detail.text
    assert "checked" in detail.text


async def test_dashboard_stage_editor_updates_row(
    client: httpx2.AsyncClient,
) -> None:
    created = await client.post(
        "/applications",
        data={"role": "Engineer", "company": "Acme"},
        follow_redirects=False,
    )
    location = created.headers["location"]
    editor = await client.get(f"{location}/stage-editor")
    assert editor.status_code == 200
    assert 'data-close-dialog="stage-editor-dialog"' in editor.text
    assert '<option value="Submitted" selected>' in editor.text

    updated = await client.post(
        f"{location}/stage-editor",
        data={"stage": "Interview", "stage_description": "First screen"},
        headers={"HX-Request": "true"},
    )
    assert updated.status_code == 200
    assert 'id="application-1"' in updated.text
    assert updated.text.count("<tr") == 1
    assert "Interview" in updated.text
    assert updated.headers["HX-Trigger"] == "close-stage-editor"

    invalid = await client.post(
        f"{location}/stage-editor",
        data={"stage": "Invalid", "stage_description": "Bad"},
        headers={"HX-Request": "true"},
    )
    assert invalid.status_code == 422
    assert "Choose a valid stage" in invalid.text


async def test_dashboard_notes_editor_updates_and_clears_notes(
    client: httpx2.AsyncClient,
) -> None:
    created = await client.post(
        "/applications",
        data={"role": "Engineer", "company": "Acme", "notes": "<old>\ntext"},
        follow_redirects=False,
    )
    location = created.headers["location"]
    dashboard = await client.get("/")
    assert "&lt;old&gt;" in dashboard.text

    editor = await client.get(f"{location}/notes-editor")
    assert editor.status_code == 200
    assert "&lt;old&gt;" in editor.text
    assert 'data-close-dialog="notes-editor-dialog"' in editor.text

    updated = await client.post(
        f"{location}/notes-editor",
        data={"notes": "Updated\nnotes"},
        headers={"HX-Request": "true"},
    )
    assert updated.status_code == 200
    assert "Updated" in updated.text
    assert updated.headers["HX-Trigger"] == "close-notes-editor"

    cleared = await client.post(
        f"{location}/notes-editor",
        data={"notes": ""},
        headers={"HX-Request": "true"},
    )
    assert cleared.status_code == 200
    assert "—" in cleared.text
