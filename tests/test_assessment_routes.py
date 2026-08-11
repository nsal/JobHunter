"""Route tests for asynchronous assessment status and recovery actions."""

from __future__ import annotations

import httpx2
import pytest

from app.consent import ConsentRepository
from app.database import connect
from app.work.models import WorkState, WorkType
from app.work.repository import WorkRepository

pytestmark = pytest.mark.anyio


APPLICATION_DATA = {
    "role": "Engineer",
    "company": "Acme",
    "full_jd": "Build reliable software.",
}
TRUSTED_HEADERS = {"Origin": "http://testserver"}


async def test_creation_redirects_and_detail_polls_only_active_work(
    client: httpx2.AsyncClient,
) -> None:
    created = await client.post(
        "/applications", data=APPLICATION_DATA, follow_redirects=False
    )

    assert created.status_code == 303
    detail = await client.get(created.headers["location"])
    assert "Current stage: <strong>Assessing</strong>" in detail.text
    assert 'id="work-status"' in detail.text
    assert 'hx-get="/applications/1/work-status"' in detail.text
    assert 'hx-trigger="every 2s" hx-swap="outerHTML"' in detail.text
    assert 'hx-trigger="load, every 2s"' not in detail.text
    assert "Assessment is" in detail.text

    status = await client.get("/applications/1/work-status")
    assert status.status_code == 200
    assert 'hx-trigger="every 2s" hx-swap="outerHTML"' in status.text
    assert 'hx-trigger="load, every 2s"' not in status.text
    assert "Assessment is" in status.text


async def test_failed_assessment_renders_safe_retry_and_requeues(
    client: httpx2.AsyncClient,
    database_path: str,
) -> None:
    await client.post("/applications", data=APPLICATION_DATA)
    with connect(database_path) as connection:
        connection.execute(
            """UPDATE work_items SET state = 'failed',
            error_code = 'timeout', error_message = ?
            WHERE application_id = 1 AND work_type = 'assessment'""",
            ("<script>alert('private')</script>",),
        )

    detail = await client.get("/applications/1")
    assert "Retry assessment" in detail.text
    assert 'hx-get="/applications/1/work-status"' not in detail.text
    assert "hx-trigger=" not in detail.text
    assert (
        "&lt;script&gt;alert(&#39;private&#39;)&lt;/script&gt;" in detail.text
    )
    assert "<script>alert" not in detail.text

    retried = await client.post(
        "/applications/1/assessment/retry",
        headers=TRUSTED_HEADERS,
        follow_redirects=False,
    )
    assert retried.status_code == 303
    work = WorkRepository(database_path).active_for_application(
        1, WorkType.ASSESSMENT
    )
    assert work is not None
    assert work.state is WorkState.QUEUED


async def test_assessment_retry_rejects_an_active_duplicate(
    client: httpx2.AsyncClient,
) -> None:
    await client.post("/applications", data=APPLICATION_DATA)

    response = await client.post(
        "/applications/1/assessment/retry", headers=TRUSTED_HEADERS
    )

    assert response.status_code == 409
    assert "only after a failed assessment" in response.text


@pytest.mark.parametrize(
    "headers", [{}, {"Origin": "https://attacker.example"}]
)
async def test_assessment_retry_rejects_unsafe_origins_without_queueing(
    client: httpx2.AsyncClient,
    database_path: str,
    headers: dict[str, str],
) -> None:
    await client.post("/applications", data=APPLICATION_DATA)
    with connect(database_path) as connection:
        connection.execute(
            "UPDATE work_items SET state = 'failed', error_code = 'timeout'"
            " WHERE application_id = 1"
        )

    response = await client.post(
        "/applications/1/assessment/retry", headers=headers
    )

    assert response.status_code == 403
    assert "Unsafe request origin" in response.text
    assert len(WorkRepository(database_path).list_for_application(1)) == 1


async def test_assessment_retry_rechecks_setup_before_queueing(
    client: httpx2.AsyncClient,
    database_path: str,
) -> None:
    await client.post("/applications", data=APPLICATION_DATA)
    with connect(database_path) as connection:
        connection.execute(
            "UPDATE work_items SET state = 'failed', error_code = 'timeout'"
            " WHERE application_id = 1"
        )
    ConsentRepository(database_path).revoke_openai_profile_sharing(
        "2026-01-01T00:00:00Z"
    )

    response = await client.post(
        "/applications/1/assessment/retry", headers=TRUSTED_HEADERS
    )

    assert response.status_code == 422
    assert "Acknowledge remote profile transmission" in response.text
    assert len(WorkRepository(database_path).list_for_application(1)) == 1


async def test_assessment_retry_is_hidden_and_rejected_after_stage_change(
    client: httpx2.AsyncClient,
    database_path: str,
) -> None:
    await client.post("/applications", data=APPLICATION_DATA)
    with connect(database_path) as connection:
        connection.execute(
            "UPDATE work_items SET state = 'failed', error_code = 'timeout'"
            " WHERE application_id = 1"
        )
    await client.post(
        "/applications/1/stages",
        data={"stage": "Mismatch", "effective_from": "2099-01-01"},
    )

    detail = await client.get("/applications/1")
    response = await client.post(
        "/applications/1/assessment/retry", headers=TRUSTED_HEADERS
    )

    assert "Retry assessment" not in detail.text
    assert response.status_code == 409
    assert len(WorkRepository(database_path).list_for_application(1)) == 1
