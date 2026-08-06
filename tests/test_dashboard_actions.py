from pathlib import Path

import httpx2
import pytest

pytestmark = pytest.mark.anyio


async def test_dashboard_table_uses_semantic_alignment_and_font_hooks(
    client: httpx2.AsyncClient,
) -> None:
    created = await client.post(
        "/applications",
        data={
            "role": "Engineer",
            "company": "Acme",
            "notes": "Follow up next week.",
            "full_jd": "Build reliable software.",
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
        """.applications-table td {
  height: calc(2.6em + .8rem);
  min-width: 0;
  overflow: hidden;
  overflow-wrap: anywhere;
  text-align: center;
  vertical-align: middle;"""
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
