import sqlite3

import pytest

from app.database import STAGES, connect, initialize_database


def test_schema_contains_fresh_lifecycle_tables(database_path: str) -> None:
    initialize_database(database_path)

    with connect(database_path) as connection:
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(applications)")
        }
        work_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(work_items)")
        }

    assert names == {
        "applications",
        "application_stage_history",
        "assessments",
        "consents",
        "cv_generations",
        "work_items",
        "sqlite_sequence",
    }
    assert "created_at" in columns
    assert "full_jd" in columns
    assert "artefact_directory" in columns
    assert "cv_path" not in columns
    assert "role_sha256" in work_columns
    assert "submission_history" not in names


def test_work_schema_validates_nullable_role_checkpoint_hash(
    database_path: str,
) -> None:
    initialize_database(database_path)

    with connect(database_path) as connection:
        connection.execute(
            """INSERT INTO applications(
                role, company, full_jd, created_at, artefact_directory
            ) VALUES ('Dev', 'Acme', 'JD', '2026-01-01', 'Acme/role-hash')"""
        )
        connection.execute(
            """INSERT INTO work_items(
                id, application_id, work_type, state, available_at,
                current_step, queued_at, role_sha256
            ) VALUES ('null-role-hash', 1, 'assessment', 'queued',
                      '2026-01-01', 'assessment', '2026-01-01', NULL)"""
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """INSERT INTO work_items(
                    id, application_id, work_type, state, available_at,
                    current_step, queued_at, role_sha256
                ) VALUES ('bad-role-hash', 1, 'assessment', 'queued',
                          '2026-01-01', 'assessment', '2026-01-01', 'bad')"""
            )


def test_application_constraints_require_jd_and_created_at(
    database_path: str,
) -> None:
    initialize_database(database_path)

    with (
        connect(database_path) as connection,
        pytest.raises(sqlite3.IntegrityError),
    ):
        connection.execute(
            """INSERT INTO applications(
                role, company, created_at, artefact_directory
            ) VALUES ('Dev', 'Acme', '2026-01-01T00:00:00', 'Acme/dev')"""
        )
    with (
        connect(database_path) as connection,
        pytest.raises(sqlite3.IntegrityError),
    ):
        connection.execute(
            """INSERT INTO applications(
                role, company, full_jd, created_at, artefact_directory
            ) VALUES ('Dev', 'Acme', '  ', '2026-01-01T00:00:00',
                      'Acme/dev')"""
        )


def test_full_jd_cannot_be_changed_in_the_database(
    database_path: str,
) -> None:
    initialize_database(database_path)

    with connect(database_path) as connection:
        connection.execute(
            """INSERT INTO applications(
                role, company, full_jd, created_at, artefact_directory
            ) VALUES ('Dev', 'Acme', 'Original JD',
                      '2026-01-01T00:00:00', 'Acme/dev')"""
        )
    with (
        connect(database_path) as connection,
        pytest.raises(sqlite3.IntegrityError, match="immutable"),
    ):
        connection.execute(
            "UPDATE applications SET full_jd = 'Changed' WHERE id = 1"
        )


def test_artefact_directory_must_be_unique_and_relative(
    database_path: str,
) -> None:
    initialize_database(database_path)

    with connect(database_path) as connection:
        connection.execute(
            """INSERT INTO applications(
                role, company, full_jd, created_at, artefact_directory
            ) VALUES ('Dev', 'Acme', 'JD', '2026-01-01', 'Acme/dev')"""
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """INSERT INTO applications(
                    role, company, full_jd, created_at, artefact_directory
                ) VALUES ('Dev', 'Acme', 'JD', '2026-01-01', 'Acme/dev')"""
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """INSERT INTO applications(
                    role, company, full_jd, created_at, artefact_directory
                ) VALUES ('Dev', 'Acme', 'JD', '2026-01-01',
                          '../escape')"""
            )


def test_stage_history_constraints(database_path: str) -> None:
    initialize_database(database_path)

    with connect(database_path) as connection:
        connection.execute(
            """INSERT INTO applications(
                role, company, full_jd, created_at, artefact_directory
            )
            VALUES ('Dev', 'Acme', 'Build software.',
                    '2026-01-01T00:00:00', 'Acme/dev')"""
        )
        connection.execute(
            """INSERT INTO application_stage_history(
                application_id, stage, stage_sequence, effective_from,
                is_current
            ) VALUES (1, 'Assessing', 1, '2026-01-01T00:00:00', 1)"""
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """INSERT INTO application_stage_history(
                    application_id, stage, stage_sequence, effective_from,
                    is_current
                ) VALUES (1, 'Nope', 2, '2026-01-02T00:00:00', 0)"""
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """INSERT INTO application_stage_history(
                    application_id, stage, stage_sequence, effective_from,
                    is_current
                ) VALUES (1, 'Ready to apply', 2,
                          '2026-01-02T00:00:00', 0)"""
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """INSERT INTO application_stage_history(
                    application_id, stage, stage_sequence, effective_from,
                    is_current
                ) VALUES (1, 'Mismatch', 2,
                          '2026-01-02T00:00:00', 1)"""
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """INSERT INTO application_stage_history(
                    application_id, stage, stage_sequence, effective_from,
                    is_current
                ) VALUES (99, 'Viewed', 2,
                          '2026-01-02T00:00:00', 0)"""
            )


def test_schema_accepts_every_lifecycle_stage(database_path: str) -> None:
    initialize_database(database_path)

    with connect(database_path) as connection:
        for sequence, stage in enumerate(STAGES, start=1):
            connection.execute(
                """INSERT INTO applications(
                    role, company, full_jd, created_at, artefact_directory
                ) VALUES (?, 'Acme', 'Build software.',
                          '2026-01-01T00:00:00', ?)""",
                (stage, f"Acme/{sequence}"),
            )
            connection.execute(
                """INSERT INTO application_stage_history(
                    application_id, stage, stage_sequence, effective_from,
                    is_current
                ) VALUES (?, ?, 1, '2026-01-01T00:00:00', 1)""",
                (sequence, stage),
            )


def test_work_schema_binds_assessment_ids_to_work_types(
    database_path: str,
) -> None:
    initialize_database(database_path)

    with connect(database_path) as connection:
        connection.execute(
            """INSERT INTO applications(
                role, company, full_jd, created_at, artefact_directory
            ) VALUES ('Dev', 'Acme', 'JD', '2026-01-01', 'Acme/work-type')"""
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """INSERT INTO work_items(
                    id, application_id, work_type, state, available_at,
                    current_step, queued_at
                ) VALUES ('cv-missing-assessment', 1, 'cv_generation',
                          'queued', '2026-01-01', 'generation', '2026-01-01')"""
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """INSERT INTO work_items(
                    id, application_id, work_type, state, available_at,
                    current_step, queued_at, assessment_id
                ) VALUES ('assessment-with-assessment', 1, 'assessment',
                          'queued', '2026-01-01', 'assessment', '2026-01-01',
                          'assessment-1')"""
            )


def test_succeeded_assessment_work_requires_result_association(
    database_path: str,
) -> None:
    initialize_database(database_path)

    with connect(database_path) as connection:
        connection.execute(
            """INSERT INTO applications(
                role, company, full_jd, created_at, artefact_directory
            ) VALUES ('Dev', 'Acme', 'JD', '2026-01-01', 'Acme/success')"""
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """INSERT INTO work_items(
                    id, application_id, work_type, state, available_at,
                    current_step, queued_at, started_at, completed_at
                ) VALUES ('succeeded-without-result', 1, 'assessment',
                          'succeeded', '2026-01-01', 'assessment',
                          '2026-01-01',
                          '2026-01-01T00:00:00.000000+00:00',
                          '2026-01-01T00:00:01.000000+00:00')"""
            )


def test_assessment_work_result_association_is_state_aware(
    database_path: str,
) -> None:
    initialize_database(database_path)

    with connect(database_path) as connection:
        connection.execute(
            """INSERT INTO applications(
                role, company, full_jd, created_at, artefact_directory
            ) VALUES ('Dev', 'Acme', 'JD', '2026-01-01', 'Acme/states')"""
        )
        for state in ("queued", "running", "failed"):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    """INSERT INTO work_items(
                        id, application_id, work_type, state, available_at,
                        current_step, queued_at, assessment_id, error_code
                    ) VALUES (?, 1, 'assessment', ?, '2026-01-01',
                              'assessment', '2026-01-01', 'result-1', ?)""",
                    (
                        f"{state}-with-result",
                        state,
                        "failure" if state == "failed" else None,
                    ),
                )


def test_work_schema_rejects_semantically_invalid_transition_timestamps(
    database_path: str,
) -> None:
    initialize_database(database_path)

    with connect(database_path) as connection:
        connection.execute(
            """INSERT INTO applications(
                role, company, full_jd, created_at, artefact_directory
            ) VALUES ('Dev', 'Acme', 'JD', '2026-01-01', 'Acme/semantic')"""
        )
        for index, timestamp in enumerate(
            (
                "2026-99-01T00:00:00.000000+00:00",
                "2026-02-30T00:00:00.000000+00:00",
                "2026-01-01T99:00:00.000000+00:00",
                "2026-01-01T24:00:00.000000+00:00",
                "2026-01-01T00:99:00.000000+00:00",
                "2026-01-01T00:00:99.000000+00:00",
            ),
            start=1,
        ):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    """INSERT INTO work_items(
                    id, application_id, work_type, state, available_at,
                        current_step, queued_at, started_at, heartbeat_at,
                        worker_token
                    ) VALUES (?, 1, 'assessment', 'running', '2026-01-01',
                              'assessment', '2026-01-01', ?, ?, 'worker')""",
                    (
                        f"invalid-{index}",
                        "2026-01-01T00:00:00.000000+00:00",
                        timestamp,
                    ),
                )


def test_work_schema_rejects_impossible_attempt_timestamp_order(
    database_path: str,
) -> None:
    initialize_database(database_path)

    with connect(database_path) as connection:
        connection.execute(
            """INSERT INTO applications(
                role, company, full_jd, created_at, artefact_directory
            ) VALUES ('Dev', 'Acme', 'JD', '2026-01-01', 'Acme/timestamps')"""
        )
        for work_id, values in (
            (
                "heartbeat-before-start",
                (
                    "2026-01-01T00:00:02.000000+00:00",
                    "2026-01-01T00:00:01.000000+00:00",
                    None,
                ),
            ),
            (
                "complete-before-start",
                (
                    "2026-01-01T00:00:02.000000+00:00",
                    "2026-01-01T00:00:02.000000+00:00",
                    "2026-01-01T00:00:01.000000+00:00",
                ),
            ),
            (
                "complete-before-heartbeat",
                (
                    "2026-01-01T00:00:02.000000+00:00",
                    "2026-01-01T00:00:03.000000+00:00",
                    "2026-01-01T00:00:02.000000+00:00",
                ),
            ),
            (
                "heartbeat-before-start-offset",
                (
                    "2026-01-01T00:00:00.000000+00:00",
                    "2026-01-01T01:00:00.000000+02:00",
                    None,
                ),
            ),
            (
                "complete-before-start-offset",
                (
                    "2026-01-01T00:00:00.000000+00:00",
                    None,
                    "2026-01-01T01:00:00.000000+02:00",
                ),
            ),
            (
                "complete-before-heartbeat-offset",
                (
                    "2026-01-01T00:00:00.000000+00:00",
                    "2026-01-01T01:00:00.000000+00:00",
                    "2026-01-01T01:00:00.000000+02:00",
                ),
            ),
            (
                "heartbeat-before-start-microsecond",
                (
                    "2026-01-01T00:00:00.000001+00:00",
                    "2026-01-01T00:00:00.000000+00:00",
                    None,
                ),
            ),
            (
                "complete-before-start-microsecond",
                (
                    "2026-01-01T00:00:00.000001+00:00",
                    None,
                    "2026-01-01T00:00:00.000000+00:00",
                ),
            ),
            (
                "complete-before-heartbeat-microsecond",
                (
                    "2026-01-01T00:00:00.000000+00:00",
                    "2026-01-01T00:00:00.000001+00:00",
                    "2026-01-01T00:00:00.000000+00:00",
                ),
            ),
        ):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    """INSERT INTO work_items(
                        id, application_id, work_type, state, available_at,
                        current_step, queued_at, started_at, heartbeat_at,
                        completed_at, worker_token
                    ) VALUES (?, 1, 'assessment', 'running',
                              '2026-01-01', 'assessment', '2026-01-01',
                              ?, ?, ?, 'worker')
                    """,
                    (work_id, *values),
                )

        connection.execute(
            """INSERT INTO work_items(
                id, application_id, work_type, state, available_at,
                current_step, queued_at, started_at, heartbeat_at,
                completed_at, worker_token
            ) VALUES (
                'microsecond-forward', 1, 'assessment', 'running',
                '2024-02-29', 'assessment', '2024-02-29',
                '2024-02-29T00:00:00.000000+00:00',
                '2024-02-29T00:00:00.000000+00:00',
                '2024-02-29T00:00:00.000001+00:00',
                'worker'
            )"""
        )
