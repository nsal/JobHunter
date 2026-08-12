"""Route tests for explicit mismatch CV generation actions."""

from __future__ import annotations

from pathlib import Path

import httpx2
import pytest

from app.ai.schema_models import RequirementMatch
from app.artefacts import ArtefactStore
from app.assessment.service import AssessmentService
from app.assessments import AssessmentRepository
from app.consent import ConsentRepository
from app.database import connect
from app.settings import load_ai_settings
from app.work.models import WorkType
from app.work.repository import WorkRepository
from tests.fixtures.assessment_cases import assessment_result
from tests.test_assessment_service import FakeGenerator

pytestmark = pytest.mark.anyio
TRUSTED_HEADERS = {"Origin": "http://testserver"}


async def _create_mismatch(
    client: httpx2.AsyncClient,
    database_path: str,
    *,
    mismatch: bool = True,
) -> None:
    project_root = Path(database_path).parent
    await client.post(
        "/applications",
        data={
            "role": "Engineer",
            "company": "Acme",
            "full_jd": "# Role\n\nRequirement 1",
        },
    )
    work_repository = WorkRepository(database_path)
    work = work_repository.active_for_application(1, WorkType.ASSESSMENT)
    assert work is not None
    work_repository.claim(work.id, "route-worker", "2099-01-01T09:01:00Z")
    profile_path = project_root / "private" / "profile" / "profile.md"
    service = AssessmentService(
        AssessmentRepository(database_path),
        ArtefactStore(project_root / "private" / "artefacts"),
        FakeGenerator(
            assessment_result(
                classifications=(
                    RequirementMatch.GAP
                    if mismatch
                    else RequirementMatch.MATCHED,
                )
            )
        ),
        load_ai_settings(project_root / "config" / "ai.yaml"),
        profile_path,
    )
    service.execute(
        1,
        "2099-01-01T09:02:00Z",
        work_id=work.id,
        worker_token="route-worker",
    )


async def test_mismatch_offers_override_and_preserves_assessment(
    client: httpx2.AsyncClient,
    database_path: str,
) -> None:
    await _create_mismatch(client, database_path)

    detail = await client.get("/applications/1")
    assert "skill_mismatch" in detail.text
    assert "Evidence-grounded overall analysis." in detail.text
    assert "Generate CV anyway" in detail.text

    generated = await client.post(
        "/applications/1/cv-generations",
        headers=TRUSTED_HEADERS,
        follow_redirects=False,
    )
    assert generated.status_code == 303
    assert (
        WorkRepository(database_path).active_for_application(
            1, WorkType.CV_GENERATION
        )
        is not None
    )
    with connect(database_path) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM assessments WHERE application_id = 1"
            ).fetchone()[0]
            == 1
        )


async def test_override_rejects_duplicate_generation_action(
    client: httpx2.AsyncClient,
    database_path: str,
) -> None:
    await _create_mismatch(client, database_path)
    first = await client.post(
        "/applications/1/cv-generations", headers=TRUSTED_HEADERS
    )
    second = await client.post(
        "/applications/1/cv-generations", headers=TRUSTED_HEADERS
    )

    assert first.status_code == 303
    assert second.status_code == 409


async def test_failed_mismatch_generation_exposes_only_retry(
    client: httpx2.AsyncClient,
    database_path: str,
) -> None:
    await _create_mismatch(client, database_path)
    generated = await client.post(
        "/applications/1/cv-generations", headers=TRUSTED_HEADERS
    )
    assert generated.status_code == 303

    work_repository = WorkRepository(database_path)
    work = work_repository.active_for_application(1, WorkType.CV_GENERATION)
    assert work is not None
    work_repository.claim(work.id, "cv-route-worker", "2099-01-01T09:03:00Z")
    with connect(database_path) as connection:
        connection.execute(
            "UPDATE work_items SET state = 'failed', error_code = 'timeout', "
            "worker_token = NULL, completed_at = ?, queued_at = ? "
            "WHERE id = ?",
            (
                "2099-01-01T09:04:00.000000+00:00",
                "2099-01-01T09:03:00.000000+00:00",
                work.id,
            ),
        )

    detail = await client.get("/applications/1")

    assert "Retry CV generation" in detail.text
    assert "Generate CV anyway" not in detail.text


async def test_matched_assessment_queues_generation_automatically(
    client: httpx2.AsyncClient,
    database_path: str,
) -> None:
    await _create_mismatch(client, database_path, mismatch=False)

    detail = await client.get("/applications/1")
    work = WorkRepository(database_path).active_for_application(
        1, WorkType.CV_GENERATION
    )
    assert work is not None
    assert "Generate CV anyway" not in detail.text
    assert "queued" in detail.text.lower()


async def test_completed_generation_promotes_lifecycle_on_workflow_read(
    client: httpx2.AsyncClient,
    database_path: str,
) -> None:
    await _create_mismatch(client, database_path, mismatch=False)
    work_repository = WorkRepository(database_path)
    work = work_repository.active_for_application(1, WorkType.CV_GENERATION)
    assert work is not None and work.assessment_id is not None
    work_repository.claim(work.id, "cv-worker", "2099-01-01T09:03:00Z")
    with connect(database_path) as connection:
        assessment = connection.execute(
            "SELECT * FROM assessments WHERE application_id = 1"
        ).fetchone()
        assert assessment is not None
        values = (
            "generation-route",
            1,
            str(assessment["id"]),
            "gpt-5-mini",
            "a" * 64,
            "v1",
            "b" * 64,
            "c" * 64,
            str(assessment["profile_sha256"]),
            str(assessment["jd_sha256"]),
            str(assessment["result_sha256"]),
            "d" * 64,
            "e" * 64,
            "openai",
            "[]",
            1,
            1,
            2,
            0,
            "Acme/2026-08-11_Engineer/cv-generations/generation-route/cv-content.json",
            "f" * 64,
            "Acme/2026-08-11_Engineer/cv-generations/generation-route/Engineer.docx",
            "1" * 64,
            "2099-01-01T09:04:00.000000+00:00",
        )
        connection.execute(
            """INSERT INTO cv_generations (
                id, application_id, assessment_id, model, model_sha256,
                schema_version, schema_sha256, instruction_sha256,
                profile_sha256, jd_sha256, assessment_result_sha256,
                template_sha256, layout_sha256, provider, response_ids,
                input_tokens, output_tokens, total_tokens, repair_attempted,
                content_path, content_sha256, candidate_path,
                candidate_sha256, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                      ?, ?, ?, ?, ?, ?)""",
            values,
        )
        connection.execute(
            """UPDATE work_items SET state = 'succeeded', completed_at = ?,
            worker_token = NULL, finalizer_token = ? WHERE id = ?""",
            (values[-1], "cv-worker", work.id),
        )

    detail = await client.get("/applications/1")
    assert "Ready for review" in detail.text
    assert "factual, editorial, and pagination" in detail.text


@pytest.mark.parametrize(
    "path",
    [
        "/applications/1/cv-generations",
        "/applications/1/generate-cv",
    ],
)
async def test_cv_override_and_alias_reject_unsafe_origins_without_queueing(
    client: httpx2.AsyncClient,
    database_path: str,
    path: str,
) -> None:
    await _create_mismatch(client, database_path)

    request = client.build_request("POST", path)
    del request.headers["Origin"]
    response = await client.send(request)

    assert response.status_code == 403
    assert "Unsafe request origin" in response.text
    assert not WorkRepository(database_path).active_for_application(
        1, WorkType.CV_GENERATION
    )


async def test_cv_override_rechecks_setup_before_queueing(
    client: httpx2.AsyncClient,
    database_path: str,
) -> None:
    await _create_mismatch(client, database_path)
    ConsentRepository(database_path).revoke_openai_profile_sharing(
        "2026-01-01T00:00:00Z"
    )

    response = await client.post(
        "/applications/1/cv-generations", headers=TRUSTED_HEADERS
    )

    assert response.status_code == 422
    assert "Acknowledge remote profile transmission" in response.text
    assert not WorkRepository(database_path).active_for_application(
        1, WorkType.CV_GENERATION
    )


async def _create_failed_cv(
    client: httpx2.AsyncClient, database_path: str
) -> WorkRepository:
    """Create one failed matched CV work item for route guard tests."""
    await _create_mismatch(client, database_path, mismatch=False)
    work_repository = WorkRepository(database_path)
    work = work_repository.active_for_application(1, WorkType.CV_GENERATION)
    assert work is not None
    work_repository.claim(work.id, "cv-route-worker", "2099-01-01T09:03:00Z")
    with connect(database_path) as connection:
        connection.execute(
            "UPDATE work_items SET state = 'failed', error_code = 'timeout', "
            "worker_token = NULL WHERE id = ?",
            (work.id,),
        )
    return work_repository


@pytest.mark.parametrize(
    "headers", [{}, {"Origin": "https://attacker.example"}]
)
async def test_cv_retry_rejects_unsafe_origins_without_queueing(
    client: httpx2.AsyncClient,
    database_path: str,
    headers: dict[str, str],
) -> None:
    work_repository = await _create_failed_cv(client, database_path)

    if headers:
        response = await client.post(
            "/applications/1/cv-generations/retry", headers=headers
        )
    else:
        request = client.build_request(
            "POST", "/applications/1/cv-generations/retry"
        )
        del request.headers["Origin"]
        response = await client.send(request)

    assert response.status_code == 403
    assert "Unsafe request origin" in response.text
    assert len(work_repository.list_for_application(1)) == 2


async def test_tampered_analysis_is_not_rendered(
    client: httpx2.AsyncClient,
    database_path: str,
) -> None:
    await _create_mismatch(client, database_path)
    with connect(database_path) as connection:
        record = connection.execute(
            "SELECT analysis_path FROM assessments WHERE application_id = 1"
        ).fetchone()
    assert record is not None
    analysis_path = (
        Path(database_path).parent
        / "private"
        / "artefacts"
        / str(record["analysis_path"])
    )
    analysis_path.write_text(
        '{"analysis": "tampered private analysis", '
        '"gaps": [{"requirement_id": "tampered-gap"}]}',
        encoding="utf-8",
    )

    detail = await client.get("/applications/1")

    assert "tampered private analysis" not in detail.text
    assert "tampered-gap" not in detail.text


async def test_cv_retry_is_hidden_and_rejected_after_stage_change(
    client: httpx2.AsyncClient,
    database_path: str,
) -> None:
    await _create_mismatch(client, database_path, mismatch=False)
    work_repository = WorkRepository(database_path)
    work = work_repository.active_for_application(1, WorkType.CV_GENERATION)
    assert work is not None
    work_repository.claim(work.id, "cv-route-worker", "2099-01-01T09:03:00Z")
    with connect(database_path) as connection:
        connection.execute(
            "UPDATE work_items SET state = 'failed', error_code = 'timeout', "
            "worker_token = NULL WHERE id = ?",
            (work.id,),
        )
    await client.post(
        "/applications/1/stages",
        data={"stage": "Mismatch", "effective_from": "2099-01-01"},
    )

    detail = await client.get("/applications/1")
    response = await client.post(
        "/applications/1/cv-generations/retry", headers=TRUSTED_HEADERS
    )

    assert "Retry CV generation" not in detail.text
    assert response.status_code == 409
    assert len(work_repository.list_for_application(1)) == 2
