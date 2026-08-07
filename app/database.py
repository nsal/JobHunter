"""SQLite connection and schema helpers."""

from __future__ import annotations

import sqlite3
from pathlib import Path

STAGES = (
    "Assessing",
    "Mismatch",
    "Ready to apply",
    "Submitted",
    "Viewed",
    "Call",
    "Interview",
    "Offer",
    "Reject",
    "Decline",
    "Closed",
)


def connect(database_path: str | Path) -> sqlite3.Connection:
    """Open a database connection with integrity checks enabled."""
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database(database_path: str | Path) -> None:
    """Create JobHunter's schema when it does not already exist."""
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    stage_values = ", ".join(f"'{stage}'" for stage in STAGES)
    with connect(path) as connection:
        connection.executescript(
            f"""
            CREATE TABLE IF NOT EXISTS applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL,
                company TEXT NOT NULL,
                payment TEXT,
                job_url TEXT,
                is_recruiter INTEGER NOT NULL DEFAULT 0
                    CHECK (is_recruiter IN (0, 1)),
                is_fully_remote INTEGER NOT NULL DEFAULT 0
                    CHECK (is_fully_remote IN (0, 1)),
                notes TEXT,
                full_jd TEXT NOT NULL CHECK (TRIM(full_jd) != ''),
                created_at TEXT NOT NULL,
                artefact_directory TEXT NOT NULL UNIQUE
                    CHECK (
                        TRIM(artefact_directory) != ''
                        AND artefact_directory NOT LIKE '/%'
                        AND artefact_directory NOT LIKE '../%'
                        AND artefact_directory NOT LIKE '%/../%'
                        AND artefact_directory NOT LIKE '%/..'
                        AND artefact_directory NOT LIKE '%//%'
                        AND INSTR(artefact_directory, CHAR(92)) = 0
                    )
            );

            CREATE TABLE IF NOT EXISTS application_stage_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                application_id INTEGER NOT NULL REFERENCES applications(id),
                stage TEXT NOT NULL CHECK (stage IN ({stage_values})),
                stage_sequence INTEGER NOT NULL CHECK (stage_sequence > 0),
                stage_description TEXT,
                effective_from TEXT NOT NULL,
                effective_to TEXT,
                is_current INTEGER NOT NULL CHECK (is_current IN (0, 1)),
                UNIQUE (application_id, stage_sequence),
                CHECK (effective_to IS NULL OR effective_to >= effective_from)
            );

            CREATE TABLE IF NOT EXISTS consents (
                consent_key TEXT PRIMARY KEY
                    CHECK (TRIM(consent_key) != ''),
                granted_at TEXT NOT NULL CHECK (TRIM(granted_at) != ''),
                revoked_at TEXT,
                CHECK (
                    revoked_at IS NULL
                    OR (
                        TRIM(revoked_at) != ''
                        AND revoked_at >= granted_at
                    )
                )
            );

            CREATE UNIQUE INDEX IF NOT EXISTS one_current_stage_per_application
            ON application_stage_history(application_id)
            WHERE is_current = 1;

            CREATE INDEX IF NOT EXISTS history_by_application_sequence
            ON application_stage_history(application_id, stage_sequence);

            CREATE INDEX IF NOT EXISTS current_stage_by_date
            ON application_stage_history(effective_from DESC)
            WHERE is_current = 1;

            CREATE TRIGGER IF NOT EXISTS immutable_application_jd
            BEFORE UPDATE OF full_jd ON applications
            WHEN NEW.full_jd IS NOT OLD.full_jd
            BEGIN
                SELECT RAISE(ABORT, 'full_jd is immutable');
            END;
            """
        )
