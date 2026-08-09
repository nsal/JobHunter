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

            CREATE TABLE IF NOT EXISTS assessments (
                id TEXT PRIMARY KEY CHECK (TRIM(id) != ''),
                application_id INTEGER NOT NULL REFERENCES applications(id),
                outcome TEXT NOT NULL CHECK (outcome IN (
                    'matched', 'skill_mismatch',
                    'salary_location_mismatch', 'other_mismatch'
                )),
                final_score REAL NOT NULL CHECK (
                    final_score >= 0 AND final_score <= 100
                ),
                supporting_alignment REAL NOT NULL CHECK (
                    supporting_alignment >= 0
                    AND supporting_alignment <= 100
                ),
                mandatory_coverage REAL NOT NULL CHECK (
                    mandatory_coverage >= 0 AND mandatory_coverage <= 100
                ),
                threshold REAL NOT NULL CHECK (
                    threshold >= 0 AND threshold <= 100
                ),
                all_mandatory_matched INTEGER NOT NULL
                    CHECK (all_mandatory_matched IN (0, 1)),
                failed_hard_gates TEXT NOT NULL,
                ambiguous_hard_gates TEXT NOT NULL,
                model TEXT NOT NULL CHECK (TRIM(model) != ''),
                model_sha256 TEXT NOT NULL CHECK (LENGTH(model_sha256) = 64),
                schema_version TEXT NOT NULL CHECK (TRIM(schema_version) != ''),
                schema_sha256 TEXT NOT NULL CHECK (LENGTH(schema_sha256) = 64),
                instruction_sha256 TEXT NOT NULL
                    CHECK (LENGTH(instruction_sha256) = 64),
                taxonomy_version TEXT NOT NULL
                    CHECK (TRIM(taxonomy_version) != ''),
                taxonomy_sha256 TEXT NOT NULL
                    CHECK (LENGTH(taxonomy_sha256) = 64),
                profile_sha256 TEXT NOT NULL
                    CHECK (LENGTH(profile_sha256) = 64),
                jd_sha256 TEXT NOT NULL CHECK (LENGTH(jd_sha256) = 64),
                provider TEXT NOT NULL CHECK (TRIM(provider) != ''),
                response_ids TEXT NOT NULL,
                input_tokens INTEGER NOT NULL CHECK (input_tokens >= 0),
                output_tokens INTEGER NOT NULL CHECK (output_tokens >= 0),
                total_tokens INTEGER NOT NULL CHECK (total_tokens >= 0),
                repair_attempted INTEGER NOT NULL
                    CHECK (repair_attempted IN (0, 1)),
                result_path TEXT NOT NULL CHECK (TRIM(result_path) != ''),
                result_sha256 TEXT NOT NULL
                    CHECK (LENGTH(result_sha256) = 64),
                analysis_path TEXT NOT NULL CHECK (TRIM(analysis_path) != ''),
                analysis_sha256 TEXT NOT NULL
                    CHECK (LENGTH(analysis_sha256) = 64),
                completed_at TEXT NOT NULL CHECK (TRIM(completed_at) != '')
            );

            CREATE TABLE IF NOT EXISTS cv_generations (
                id TEXT PRIMARY KEY CHECK (TRIM(id) != ''),
                application_id INTEGER NOT NULL REFERENCES applications(id),
                assessment_id TEXT NOT NULL REFERENCES assessments(id),
                model TEXT NOT NULL CHECK (TRIM(model) != ''),
                model_sha256 TEXT NOT NULL CHECK (LENGTH(model_sha256) = 64),
                schema_version TEXT NOT NULL CHECK (TRIM(schema_version) != ''),
                schema_sha256 TEXT NOT NULL CHECK (LENGTH(schema_sha256) = 64),
                instruction_sha256 TEXT NOT NULL
                    CHECK (LENGTH(instruction_sha256) = 64),
                profile_sha256 TEXT NOT NULL
                    CHECK (LENGTH(profile_sha256) = 64),
                jd_sha256 TEXT NOT NULL CHECK (LENGTH(jd_sha256) = 64),
                assessment_result_sha256 TEXT NOT NULL
                    CHECK (LENGTH(assessment_result_sha256) = 64),
                template_sha256 TEXT NOT NULL
                    CHECK (LENGTH(template_sha256) = 64),
                layout_sha256 TEXT NOT NULL
                    CHECK (LENGTH(layout_sha256) = 64),
                provider TEXT NOT NULL CHECK (TRIM(provider) != ''),
                response_ids TEXT NOT NULL,
                input_tokens INTEGER NOT NULL CHECK (input_tokens >= 0),
                output_tokens INTEGER NOT NULL CHECK (output_tokens >= 0),
                total_tokens INTEGER NOT NULL CHECK (total_tokens >= 0),
                repair_attempted INTEGER NOT NULL
                    CHECK (repair_attempted IN (0, 1)),
                content_path TEXT NOT NULL CHECK (TRIM(content_path) != ''),
                content_sha256 TEXT NOT NULL
                    CHECK (LENGTH(content_sha256) = 64),
                candidate_path TEXT NOT NULL CHECK (TRIM(candidate_path) != ''),
                candidate_sha256 TEXT NOT NULL
                    CHECK (LENGTH(candidate_sha256) = 64),
                completed_at TEXT NOT NULL CHECK (TRIM(completed_at) != ''),
                UNIQUE (application_id, assessment_id)
            );

            CREATE UNIQUE INDEX IF NOT EXISTS one_current_stage_per_application
            ON application_stage_history(application_id)
            WHERE is_current = 1;

            CREATE INDEX IF NOT EXISTS history_by_application_sequence
            ON application_stage_history(application_id, stage_sequence);

            CREATE INDEX IF NOT EXISTS current_stage_by_date
            ON application_stage_history(effective_from DESC)
            WHERE is_current = 1;

            CREATE INDEX IF NOT EXISTS assessments_by_application
            ON assessments(application_id, completed_at DESC);

            CREATE INDEX IF NOT EXISTS cv_generations_by_application
            ON cv_generations(application_id, completed_at DESC);

            CREATE TRIGGER IF NOT EXISTS immutable_application_jd
            BEFORE UPDATE OF full_jd ON applications
            WHEN NEW.full_jd IS NOT OLD.full_jd
            BEGIN
                SELECT RAISE(ABORT, 'full_jd is immutable');
            END;

            CREATE TRIGGER IF NOT EXISTS immutable_completed_assessment_update
            BEFORE UPDATE ON assessments
            BEGIN
                SELECT RAISE(ABORT, 'completed assessment is immutable');
            END;

            CREATE TRIGGER IF NOT EXISTS immutable_completed_assessment_delete
            BEFORE DELETE ON assessments
            BEGIN
                SELECT RAISE(ABORT, 'completed assessment is immutable');
            END;

            CREATE TRIGGER IF NOT EXISTS immutable_completed_cv_generation_update
            BEFORE UPDATE ON cv_generations
            BEGIN
                SELECT RAISE(ABORT, 'completed CV generation is immutable');
            END;

            CREATE TRIGGER IF NOT EXISTS immutable_completed_cv_generation_delete
            BEFORE DELETE ON cv_generations
            BEGIN
                SELECT RAISE(ABORT, 'completed CV generation is immutable');
            END;
            """
        )
