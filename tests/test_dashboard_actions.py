from pathlib import Path

import httpx2
import pytest

from app.work.models import WorkType
from app.work.repository import WorkRepository
from tests.test_work_repository import seed_assessment

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
    assert "Assessment: queued" in dashboard.text
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


async def test_dashboard_shows_terminal_assessment_failure(
    client: httpx2.AsyncClient,
    database_path: str,
) -> None:
    await client.post(
        "/applications",
        data={
            "role": "Engineer",
            "company": "Acme",
            "full_jd": "Build reliable software.",
        },
    )
    repository = WorkRepository(database_path)
    work = repository.active_for_application(1, WorkType.ASSESSMENT)
    assert work is not None
    repository.claim(work.id, "dashboard-worker", "2099-01-01T00:00:01Z")
    repository.fail(
        work.id,
        "dashboard-worker",
        RuntimeError("assessment failed"),
        "2099-01-01T00:00:02Z",
    )

    dashboard = await client.get("/")

    assert "Assessment: failed" in dashboard.text


async def test_dashboard_shows_terminal_cv_failure_over_assessment(
    client: httpx2.AsyncClient,
    database_path: str,
) -> None:
    await client.post(
        "/applications",
        data={
            "role": "Engineer",
            "company": "Acme",
            "full_jd": "Build reliable software.",
        },
    )
    seed_assessment(database_path, 1, outcome="matched")
    repository = WorkRepository(database_path)
    assessment_work = repository.active_for_application(1, WorkType.ASSESSMENT)
    assert assessment_work is not None
    repository.claim(
        assessment_work.id, "dashboard-worker", "2099-01-01T00:00:01Z"
    )
    repository.fail(
        assessment_work.id,
        "dashboard-worker",
        RuntimeError("assessment failed"),
        "2099-01-01T00:00:02Z",
    )
    generation_id = repository.enqueue(
        1,
        WorkType.CV_GENERATION,
        "2099-01-01T00:00:03Z",
        assessment_id="assessment-1",
    )
    repository.claim(generation_id, "dashboard-worker", "2099-01-01T00:00:04Z")
    repository.fail(
        generation_id,
        "dashboard-worker",
        RuntimeError("generation failed"),
        "2099-01-01T00:00:05Z",
    )

    dashboard = await client.get("/")

    assert "CV generation: failed" in dashboard.text
    assert "Assessment: matched" not in dashboard.text
