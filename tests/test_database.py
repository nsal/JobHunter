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

    assert names == {
        "applications",
        "application_stage_history",
        "assessments",
        "consents",
        "cv_generations",
        "sqlite_sequence",
    }
    assert "created_at" in columns
    assert "full_jd" in columns
    assert "artefact_directory" in columns
    assert "cv_path" not in columns
    assert "submission_history" not in names


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
