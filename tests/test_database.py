import sqlite3

import pytest

from app.database import connect, initialize_database


def test_schema_contains_only_approved_tables(database_path: str) -> None:
    initialize_database(database_path)
    with connect(database_path) as connection:
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert names == {"applications", "submission_history", "sqlite_sequence"}


def test_history_constraints(database_path: str) -> None:
    initialize_database(database_path)
    with connect(database_path) as connection:
        connection.execute(
            "INSERT INTO applications(role, company) VALUES ('Dev', 'Acme')"
        )
        connection.execute(
            "INSERT INTO submission_history(application_id, stage, stage_sequence, effective_from, is_current) VALUES (1, 'Submitted', 1, '2026-01-01T00:00:00', 1)"
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO submission_history(application_id, stage, stage_sequence, effective_from, is_current) VALUES (1, 'Nope', 2, '2026-01-02T00:00:00', 0)"
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO submission_history(application_id, stage, stage_sequence, effective_from, is_current) VALUES (1, 'Viewed', 2, '2026-01-02T00:00:00', 1)"
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO submission_history(application_id, stage, stage_sequence, effective_from, is_current) VALUES (99, 'Viewed', 2, '2026-01-02T00:00:00', 0)"
            )
