from __future__ import annotations

import sqlite3

import pytest

from app.ai.providers.base import (
    ProviderErrorCode,
    StructuredGenerationError,
)
from app.database import connect, initialize_database
from app.work.models import WorkState, WorkType
from app.work.repository import (
    StaleWorkerError,
    WorkRepository,
    WorkStateError,
)


def create_application(database_path: str) -> int:
    with connect(database_path) as connection:
        cursor = connection.execute(
            """INSERT INTO applications (
                role, company, full_jd, created_at, artefact_directory
            ) VALUES ('Developer', 'Acme', 'Build software', ?, ?)""",
            ("2026-01-01T00:00:00+00:00", "Acme/2026-01-01_Developer"),
        )
        application_id = cursor.lastrowid
        if application_id is None:
            raise AssertionError("application ID was not created")
        connection.execute(
            """INSERT INTO application_stage_history (
                application_id, stage, stage_sequence, effective_from,
                is_current
            ) VALUES (?, 'Assessing', 1, ?, 1)""",
            (application_id, "2026-01-01T00:00:00+00:00"),
        )
    return application_id


def seed_assessment(
    database_path: str,
    application_id: int,
    assessment_id: str = "assessment-1",
) -> None:
    with connect(database_path) as connection:
        connection.execute(
            """INSERT INTO assessments(
                id, application_id, outcome, final_score,
                supporting_alignment, mandatory_coverage, threshold,
                all_mandatory_matched, failed_hard_gates,
                ambiguous_hard_gates, model, model_sha256, schema_version,
                schema_sha256, instruction_sha256, taxonomy_version,
                taxonomy_sha256, profile_sha256, jd_sha256, provider,
                response_ids, input_tokens, output_tokens, total_tokens,
                repair_attempted, result_path, result_sha256, analysis_path,
                analysis_sha256, completed_at
            ) VALUES (?, ?, 'matched', 90, 90, 90, 80, 1, '[]', '[]',
                      'model', ?, 'v1', ?, ?, 'taxonomy', ?, ?, ?, 'test',
                      '[]', 1, 2, 3, 0, 'result.json', ?, 'analysis.json',
                      ?, '2026-01-01T01:00:00+00:00')""",
            (
                assessment_id,
                application_id,
                "a" * 64,
                "b" * 64,
                "c" * 64,
                "d" * 64,
                "e" * 64,
                "f" * 64,
                "1" * 64,
                "2" * 64,
            ),
        )


class TransientFailure(StructuredGenerationError):
    def __init__(self) -> None:
        super().__init__(
            ProviderErrorCode.TIMEOUT,
            "Provider timed out.",
            retryable=True,
        )


class ForgedFailure(RuntimeError):
    code = "timeout"
    retryable = True
    safe_message = "private secret that must not be persisted"


def test_work_claim_heartbeat_and_checkpoint_without_generic_completion(
    database_path: str,
) -> None:
    initialize_database(database_path)
    application_id = create_application(database_path)
    repository = WorkRepository(database_path)
    work_id = repository.enqueue(
        application_id,
        WorkType.ASSESSMENT,
        "2026-01-01T00:00:00+00:00",
    )

    claimed = repository.claim(work_id, "worker-a", "2026-01-01T00:00:01+00:00")
    assert claimed.attempt_count == 1
    repository.heartbeat(work_id, "worker-a", "2026-01-01T00:00:02+00:00")
    repository.checkpoint(
        work_id,
        "worker-a",
        "validated",
        "assessments/result.json",
        "a" * 64,
        hashes={"profile_sha256": "b" * 64, "jd_sha256": "c" * 64},
    )
    assert repository.checkpoint_matches(
        work_id,
        {"profile_sha256": "b" * 64, "jd_sha256": "c" * 64},
    )
    assert not repository.checkpoint_matches(
        work_id, {"profile_sha256": "d" * 64}
    )
    assert not hasattr(repository, "complete")
    assert repository.get(work_id).state is WorkState.RUNNING


def test_active_work_is_unique_and_stale_tokens_are_rejected(
    database_path: str,
) -> None:
    initialize_database(database_path)
    application_id = create_application(database_path)
    repository = WorkRepository(database_path)
    work_id = repository.enqueue(
        application_id, WorkType.ASSESSMENT, "2026-01-01T00:00:00+00:00"
    )
    with pytest.raises(ValueError, match="requires an assessment"):
        repository.enqueue(
            application_id,
            WorkType.CV_GENERATION,
            "2026-01-01T00:00:00+00:00",
        )
    repository.claim(work_id, "worker-a", "2026-01-01T00:00:01+00:00")
    with pytest.raises(StaleWorkerError):
        repository.heartbeat(work_id, "worker-b", "2026-01-01T00:00:02+00:00")


def test_generic_cv_enqueue_rejects_completed_generation_atomically(
    database_path: str,
) -> None:
    initialize_database(database_path)
    application_id = create_application(database_path)
    seed_assessment(database_path, application_id)
    with connect(database_path) as connection:
        connection.execute(
            """INSERT INTO cv_generations(
                id, application_id, assessment_id, model, model_sha256,
                schema_version, schema_sha256, instruction_sha256,
                profile_sha256, jd_sha256, assessment_result_sha256,
                template_sha256, layout_sha256, provider, response_ids,
                input_tokens, output_tokens, total_tokens, repair_attempted,
                content_path, content_sha256, candidate_path,
                candidate_sha256, completed_at
            ) VALUES ('generation-1', ?, 'assessment-1', 'model', ?, 'v1',
                      ?, ?, ?, ?, ?, ?, ?, 'test', '[]', 1, 2, 3, 0,
                      'content.json', ?, 'candidate.docx', ?,
                      '2026-01-01T02:00:00+00:00')""",
            (
                application_id,
                "a" * 64,
                "b" * 64,
                "c" * 64,
                "d" * 64,
                "e" * 64,
                "f" * 64,
                "1" * 64,
                "2" * 64,
                "3" * 64,
                "4" * 64,
            ),
        )

    with pytest.raises(WorkStateError, match="already been completed"):
        WorkRepository(database_path).enqueue(
            application_id,
            WorkType.CV_GENERATION,
            "2026-01-01T03:00:00Z",
            assessment_id="assessment-1",
        )

    with connect(database_path) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM cv_generations"
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM work_items").fetchone()[0]
            == 0
        )


def test_generic_cv_enqueue_binds_assessment_hashes(
    database_path: str,
) -> None:
    initialize_database(database_path)
    application_id = create_application(database_path)
    seed_assessment(database_path, application_id)

    work_id = WorkRepository(database_path).enqueue(
        application_id,
        WorkType.CV_GENERATION,
        "2026-01-01T03:00:00Z",
        assessment_id="assessment-1",
        profile_sha256="e" * 64,
        jd_sha256="f" * 64,
    )

    work = WorkRepository(database_path).get(work_id)
    assert work.profile_sha256 == "e" * 64
    assert work.jd_sha256 == "f" * 64


def test_transient_failure_retries_once_then_fails(
    database_path: str,
) -> None:
    initialize_database(database_path)
    application_id = create_application(database_path)
    repository = WorkRepository(database_path)
    work_id = repository.enqueue(
        application_id, WorkType.ASSESSMENT, "2026-01-01T00:00:00+00:00"
    )
    repository.claim(work_id, "worker-a", "2026-01-01T00:00:01+00:00")
    assert (
        repository.fail(
            work_id,
            "worker-a",
            TransientFailure(),
            "2026-01-01T00:00:02+00:00",
        ).state.value
        == "queued"
    )
    repository.claim(work_id, "worker-b", "2026-01-01T00:00:04+00:00")
    failed = repository.fail(
        work_id,
        "worker-b",
        TransientFailure(),
        "2026-01-01T00:00:05+00:00",
    )
    assert failed.state.value == "failed"
    assert failed.error_message == "Provider timed out."
    assert failed.failure_token == "worker-b"


def test_failure_replay_is_exactly_owner_qualified(
    database_path: str,
) -> None:
    initialize_database(database_path)
    application_id = create_application(database_path)
    repository = WorkRepository(database_path)
    work_id = repository.enqueue(
        application_id, WorkType.ASSESSMENT, "2026-01-01T00:00:00Z"
    )
    repository.claim(work_id, "worker-a", "2026-01-01T00:00:01Z")

    retried = repository.fail(
        work_id,
        "worker-a",
        TransientFailure(),
        "2026-01-01T00:00:02Z",
    )
    replay = repository.fail(
        work_id,
        "worker-a",
        TransientFailure(),
        "2026-01-01T00:00:00Z",
    )
    assert replay == retried
    with pytest.raises(StaleWorkerError):
        repository.fail(
            work_id,
            "worker-b",
            TransientFailure(),
            "2026-01-01T00:00:00Z",
        )
    with pytest.raises(WorkStateError, match="does not match"):
        repository.fail(
            work_id,
            "worker-a",
            StructuredGenerationError(
                ProviderErrorCode.RATE_LIMIT,
                "Provider rate limited.",
                retryable=True,
            ),
            "2026-01-01T00:00:00Z",
        )

    repository.claim(work_id, "worker-c", "2026-01-01T00:00:04Z")
    assert repository.get(work_id).failure_token is None
    failed = repository.fail(
        work_id,
        "worker-c",
        RuntimeError("classified failure"),
        "2026-01-01T00:00:05Z",
    )
    terminal_replay = repository.fail(
        work_id,
        "worker-c",
        RuntimeError("different private detail"),
        "2026-01-01T00:00:00Z",
    )
    assert terminal_replay == failed
    with pytest.raises(StaleWorkerError):
        repository.fail(
            work_id,
            "worker-d",
            RuntimeError("classified failure"),
            "2026-01-01T00:00:00Z",
        )
    with pytest.raises(WorkStateError, match="does not match"):
        repository.fail(
            work_id,
            "worker-c",
            TimeoutError("different classification"),
            "2026-01-01T00:00:00Z",
        )


def test_work_transitions_reject_backdated_activity(
    database_path: str,
) -> None:
    initialize_database(database_path)
    application_id = create_application(database_path)
    repository = WorkRepository(database_path)
    work_id = repository.enqueue(
        application_id, WorkType.ASSESSMENT, "2026-01-01T00:00:00Z"
    )
    repository.claim(work_id, "worker-a", "2026-01-01T00:00:02Z")

    with pytest.raises(ValueError, match="current work activity"):
        repository.heartbeat(work_id, "worker-a", "2026-01-01T00:00:01Z")
    with pytest.raises(ValueError, match="current work activity"):
        repository.fail(
            work_id,
            "worker-a",
            RuntimeError("failure"),
            "2026-01-01T00:00:01Z",
        )
    item = repository.get(work_id)
    assert item.state is WorkState.RUNNING
    assert item.heartbeat_at == item.started_at


def test_stale_running_work_returns_to_queue(database_path: str) -> None:
    initialize_database(database_path)
    application_id = create_application(database_path)
    repository = WorkRepository(database_path)
    work_id = repository.enqueue(
        application_id, WorkType.ASSESSMENT, "2026-01-01T00:00:00+00:00"
    )
    repository.claim(
        work_id,
        "worker-a",
        "2026-01-01T00:00:01+00:00",
        lease_seconds=1,
    )
    assert repository.recover_stale("2026-01-01T00:00:03+00:00") == 1
    assert repository.get(work_id).state.value == "queued"


def test_active_index_is_sqlite_enforced(database_path: str) -> None:
    initialize_database(database_path)
    application_id = create_application(database_path)
    with connect(database_path) as connection:
        values = (
            "one",
            application_id,
            "assessment",
            "queued",
            "2026-01-01",
            "assessment",
            "2026-01-01",
        )
        connection.execute(
            """INSERT INTO work_items (
                id, application_id, work_type, state, available_at,
                current_step, queued_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            values,
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """INSERT INTO work_items (
                    id, application_id, work_type, state, available_at,
                    current_step, queued_at
                ) VALUES ('two', ?, 'cv_generation', 'queued', ?,
                    'generation', ?)""",
                (application_id, "2026-01-01", "2026-01-01"),
            )


def test_work_timestamps_are_canonical_and_lease_is_positive(
    database_path: str,
) -> None:
    initialize_database(database_path)
    application_id = create_application(database_path)
    repository = WorkRepository(database_path)
    work_id = repository.enqueue(
        application_id,
        WorkType.ASSESSMENT,
        "2026-01-01T01:00:00+01:00",
    )

    item = repository.get(work_id)
    assert item.queued_at == "2026-01-01T00:00:00.000000+00:00"
    with pytest.raises(ValueError, match="positive"):
        repository.claim(work_id, "worker-a", "2026-01-01T00:00:00Z", 0)
    with pytest.raises(ValueError, match="valid ISO-8601"):
        repository.enqueue(application_id, WorkType.ASSESSMENT, "invalid")


def test_process_timeout_retries_and_unknown_errors_are_redacted(
    database_path: str,
) -> None:
    initialize_database(database_path)
    application_id = create_application(database_path)
    repository = WorkRepository(database_path)
    work_id = repository.enqueue(
        application_id, WorkType.ASSESSMENT, "2026-01-01T00:00:00Z"
    )
    repository.claim(work_id, "worker-a", "2026-01-01T00:00:01Z")
    retried = repository.fail(
        work_id,
        "worker-a",
        TimeoutError("profile api_key=secret-value"),
        "2026-01-01T00:00:02Z",
    )
    assert retried.state is WorkState.QUEUED
    assert retried.error_code == "process_timeout"
    assert retried.error_message == "Worker process timed out."

    repository.claim(work_id, "worker-b", "2026-01-01T00:00:04Z")
    failed = repository.fail(
        work_id,
        "worker-b",
        RuntimeError("profile api_key=secret-value"),
        "2026-01-01T00:00:05Z",
    )
    assert failed.state is WorkState.FAILED
    assert failed.error_code == "unknown_error"
    assert failed.error_message == "Work failed."
    assert "secret-value" not in (failed.error_message or "")


def test_forged_failure_metadata_is_ignored(
    database_path: str,
) -> None:
    initialize_database(database_path)
    application_id = create_application(database_path)
    repository = WorkRepository(database_path)
    work_id = repository.enqueue(
        application_id, WorkType.ASSESSMENT, "2026-01-01T00:00:00Z"
    )
    repository.claim(work_id, "worker-a", "2026-01-01T00:00:01Z")

    failed = repository.fail(
        work_id,
        "worker-a",
        ForgedFailure("original private error"),
        "2026-01-01T00:00:02Z",
    )

    assert failed.state is WorkState.FAILED
    assert failed.error_code == "unknown_error"
    assert failed.error_message == "Work failed."


def test_second_stale_attempt_is_terminal_and_releases_active_work(
    database_path: str,
) -> None:
    initialize_database(database_path)
    application_id = create_application(database_path)
    repository = WorkRepository(database_path)
    work_id = repository.enqueue(
        application_id, WorkType.ASSESSMENT, "2026-01-01T00:00:00Z"
    )
    repository.claim(
        work_id, "worker-a", "2026-01-01T00:00:01Z", lease_seconds=1
    )
    assert repository.recover_stale("2026-01-01T00:00:03Z") == 1
    repository.claim(
        work_id, "worker-b", "2026-01-01T00:00:04Z", lease_seconds=1
    )
    assert repository.recover_stale("2026-01-01T00:00:06Z") == 1
    failed = repository.get(work_id)
    assert failed.state is WorkState.FAILED
    assert failed.attempt_count == 2
    assert failed.worker_token is None
    assert repository.recover_stale("2026-01-01T00:00:07Z") == 0

    replacement = repository.enqueue(
        application_id, WorkType.ASSESSMENT, "2026-01-01T00:00:08Z"
    )
    assert repository.get(replacement).state is WorkState.QUEUED
