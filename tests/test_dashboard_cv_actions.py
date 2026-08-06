from pathlib import Path

import httpx2
import pytest

pytestmark = pytest.mark.anyio


async def test_dashboard_uses_preview_and_download_cv_actions(
    client: httpx2.AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cv_root = tmp_path / "artefacts"
    cv_root.mkdir()
    monkeypatch.setenv("JOBHUNTER_CV_ROOT", str(cv_root))
    pdf = cv_root / "resume.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    docx = cv_root / "resume.docx"
    docx.write_bytes(b"word content")

    first = await client.post(
        "/applications",
        data={"role": "Engineer", "company": "Acme", "cv_path": str(pdf)},
        follow_redirects=False,
    )
    second = await client.post(
        "/applications",
        data={"role": "Writer", "company": "Beta", "cv_path": str(docx)},
        follow_redirects=False,
    )

    dashboard = await client.get("/")

    assert (
        f'data-preview-url="{first.headers["location"]}/cv/preview"'
        in dashboard.text
    )
    assert 'class="cv-preview-button"' in dashboard.text
    assert f'href="{second.headers["location"]}/cv/download"' in dashboard.text


async def test_dashboard_table_uses_semantic_alignment_and_font_hooks(
    client: httpx2.AsyncClient,
) -> None:
    created = await client.post(
        "/applications",
        data={
            "role": "Engineer",
            "company": "Acme",
            "notes": "Follow up next week.",
        },
        follow_redirects=False,
    )

    dashboard = await client.get("/")
    stylesheet = Path("app/static/app.css").read_text()

    assert created.status_code == 303
    assert 'class="application-role-cell" data-label="Role"' in dashboard.text
    assert (
        'class="application-company-cell" data-label="Company"'
        in dashboard.text
    )
    assert 'class="application-notes-cell" data-label="Notes"' in dashboard.text
    assert "data-open-notes-editor" in dashboard.text
    assert (
        """.applications-table td {
  height: calc(2.6em + .8rem);
  min-width: 0;
  overflow: hidden;
  overflow-wrap: anywhere;
  text-align: center;"""
        in stylesheet
    )
    assert (
        """.applications-table .application-role-cell,
.applications-table .application-company-cell,
.applications-table .application-notes-cell {
  text-align: left;
}"""
        in stylesheet
    )
    assert (
        ".applications-table .application-notes-cell .notes-edit {"
        in stylesheet
    )
    assert (
        """.applications-table .application-notes-cell .notes-edit {
  block-size: 2.6em;
  display: block;
  font: inherit;
  max-block-size: 2.6em;
  overflow: hidden;
  text-align: inherit;"""
        in stylesheet
    )
    assert (
        """.applications-table .cv-preview-button {
  font: inherit;"""
        in stylesheet
    )
    assert (
        """.applications-table td {
    align-items: start;
    border-bottom: 1px solid #b8c2cc;
    display: grid;
    gap: .5rem;
    grid-template-columns: minmax(7rem, 38%) 1fr;
    height: auto;
    overflow: visible;
    text-align: left;"""
        in stylesheet
    )
