"""Persistence for immutable completed CV generations."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from app.database import connect
from app.work.models import (
    canonical_timestamp,
    latest_timestamp,
    timestamp_precedes,
)
from app.work.repository import StaleWorkerError


class CvGenerationNotFoundError(ValueError):
    """Raised when generation inputs or records cannot be found."""


class CvGenerationStateError(RuntimeError):
    """Raised when an application cannot produce the requested CV."""


def _normalize_timestamp(value: str, label: str) -> datetime:
    """Parse an ISO-8601 timestamp and normalize it to UTC."""
    if not value.strip():
        raise ValueError(f"{label} is required.")
    try:
        if "T" not in value and " " not in value:
            raise ValueError
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    except (ValueError, OverflowError) as error:
        raise ValueError(
            f"{label} must be a valid ISO-8601 timestamp."
        ) from error


@dataclass(frozen=True)
class CvGenerationInput:
    """Application and assessment data required by CV generation."""

    application_id: int
    role: str
    full_jd: str
    artefact_directory: str
    current_stage: str
    assessment_id: str
    assessment_outcome: str
    assessment_profile_sha256: str
    assessment_jd_sha256: str
    assessment_result_path: str
    assessment_result_sha256: str


@dataclass(frozen=True)
class CompletedCvGeneration:
    """Allowlisted metadata for one completed CV candidate."""

    generation_id: str
    application_id: int
    assessment_id: str
    model: str
    model_sha256: str
    schema_version: str
    schema_sha256: str
    instruction_sha256: str
    profile_sha256: str
    jd_sha256: str
    assessment_result_sha256: str
    template_sha256: str
    layout_sha256: str
    provider: str
    response_ids: tuple[str, ...]
    input_tokens: int
    output_tokens: int
    total_tokens: int
    repair_attempted: bool
    content_path: str
    content_sha256: str
    candidate_path: str
    candidate_sha256: str
    completed_at: str
    work_id: str
    worker_token: str


_CV_GENERATION_COLUMNS = (
    "id",
    "application_id",
    "assessment_id",
    "model",
    "model_sha256",
    "schema_version",
    "schema_sha256",
    "instruction_sha256",
    "profile_sha256",
    "jd_sha256",
    "assessment_result_sha256",
    "template_sha256",
    "layout_sha256",
    "provider",
    "response_ids",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "repair_attempted",
    "content_path",
    "content_sha256",
    "candidate_path",
    "candidate_sha256",
    "completed_at",
)


def _cv_generation_payload(
    generation: CompletedCvGeneration,
) -> tuple[object, ...]:
    """Return the exact SQLite values for one completed CV generation."""
    return (
        generation.generation_id,
        generation.application_id,
        generation.assessment_id,
        generation.model,
        generation.model_sha256,
        generation.schema_version,
        generation.schema_sha256,
        generation.instruction_sha256,
        generation.profile_sha256,
        generation.jd_sha256,
        generation.assessment_result_sha256,
        generation.template_sha256,
        generation.layout_sha256,
        generation.provider,
        json.dumps(list(generation.response_ids)),
        generation.input_tokens,
        generation.output_tokens,
        generation.total_tokens,
        int(generation.repair_attempted),
        generation.content_path,
        generation.content_sha256,
        generation.candidate_path,
        generation.candidate_sha256,
        generation.completed_at,
    )


class CvGenerationRepository:
    """Read generation inputs and append immutable generation records."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = database_path

    def preflight(
        self,
        application_id: int,
        assessment_id: str,
        work_id: str,
        worker_token: str,
    ) -> None:
        """Verify exact CV work ownership without changing state."""
        if not work_id.strip() or not worker_token.strip():
            raise ValueError("CV work ID and worker token are required.")
        with connect(self.database_path) as connection:
            work = connection.execute(
                """SELECT application_id, work_type, state, assessment_id,
                    worker_token
                FROM work_items WHERE id = ?""",
                (work_id,),
            ).fetchone()
        if (
            work is None
            or int(work["application_id"]) != application_id
            or work["work_type"] != "cv_generation"
            or work["assessment_id"] != assessment_id
        ):
            raise CvGenerationStateError("CV generation work was not found.")
        if work["state"] != "running" or work["worker_token"] != worker_token:
            raise StaleWorkerError("Worker token is no longer current.")

    def get_input(
        self,
        application_id: int,
        assessment_id: str,
        *,
        allow_mismatch: bool = False,
    ) -> CvGenerationInput:
        """Return validated application and assessment generation inputs."""
        with connect(self.database_path) as connection:
            row = connection.execute(
                """SELECT a.id, a.role, a.full_jd, a.artefact_directory,
                    history.stage AS current_stage,
                    assessment.id AS assessment_id,
                    assessment.outcome AS assessment_outcome,
                    assessment.profile_sha256 AS assessment_profile_sha256,
                    assessment.jd_sha256 AS assessment_jd_sha256,
                    assessment.result_path AS assessment_result_path,
                    assessment.result_sha256 AS assessment_result_sha256
                FROM applications AS a
                JOIN application_stage_history AS history
                  ON history.application_id = a.id
                 AND history.is_current = 1
                JOIN assessments AS assessment
                  ON assessment.application_id = a.id
                 AND assessment.id = ?
                WHERE a.id = ?""",
                (assessment_id, application_id),
            ).fetchone()
        if row is None:
            raise CvGenerationNotFoundError(
                "Application assessment was not found."
            )
        outcome = str(row["assessment_outcome"])
        stage = str(row["current_stage"])
        permitted = outcome == "matched" and stage == "Assessing"
        permitted = permitted or (
            allow_mismatch and outcome != "matched" and stage == "Mismatch"
        )
        if not permitted:
            raise CvGenerationStateError(
                "Application is not eligible for CV generation."
            )
        return CvGenerationInput(
            application_id=int(row["id"]),
            role=str(row["role"]),
            full_jd=str(row["full_jd"]),
            artefact_directory=str(row["artefact_directory"]),
            current_stage=stage,
            assessment_id=str(row["assessment_id"]),
            assessment_outcome=outcome,
            assessment_profile_sha256=str(row["assessment_profile_sha256"]),
            assessment_jd_sha256=str(row["assessment_jd_sha256"]),
            assessment_result_path=str(row["assessment_result_path"]),
            assessment_result_sha256=str(row["assessment_result_sha256"]),
        )

    def enqueue_override(
        self, application_id: int, assessment_id: str, queued_at: str
    ) -> str:
        """Queue one explicit mismatch override without altering its result."""
        with connect(self.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            queued_at = canonical_timestamp(queued_at, "queued time")
            row = connection.execute(
                """SELECT outcome, profile_sha256, jd_sha256
                FROM assessments WHERE id = ? AND application_id = ?""",
                (assessment_id, application_id),
            ).fetchone()
            stage = connection.execute(
                """SELECT stage FROM application_stage_history
                WHERE application_id = ? AND is_current = 1""",
                (application_id,),
            ).fetchone()
            if row is None or stage is None:
                raise CvGenerationNotFoundError(
                    "Application assessment was not found."
                )
            if row["outcome"] == "matched" or stage["stage"] != "Mismatch":
                raise CvGenerationStateError(
                    "Only a completed mismatch can be overridden."
                )
            completed = connection.execute(
                """SELECT 1 FROM cv_generations
                WHERE application_id = ? AND assessment_id = ?""",
                (application_id, assessment_id),
            ).fetchone()
            if completed is not None:
                raise CvGenerationStateError(
                    "CV generation has already been completed."
                )
            try:
                connection.execute(
                    """INSERT INTO work_items (
                        id, application_id, work_type, state, available_at,
                        current_step, queued_at, assessment_id,
                        profile_sha256, jd_sha256
                    ) VALUES (?, ?, 'cv_generation', 'queued', ?, 'generation',
                              ?, ?, ?, ?)""",
                    (
                        uuid4().hex,
                        application_id,
                        queued_at,
                        queued_at,
                        assessment_id,
                        str(row["profile_sha256"]),
                        str(row["jd_sha256"]),
                    ),
                )
            except sqlite3.IntegrityError as error:
                if "one_active_work" in str(error) or "UNIQUE" in str(error):
                    raise CvGenerationStateError(
                        "Application already has active work."
                    ) from error
                raise
            row = connection.execute(
                """SELECT id FROM work_items
                WHERE application_id = ? AND assessment_id = ?
                AND state = 'queued'""",
                (application_id, assessment_id),
            ).fetchone()
            if row is not None:
                return str(row["id"])
            raise RuntimeError("Override work insertion did not return an ID.")

    def add_completed(self, generation: CompletedCvGeneration) -> None:
        """Persist one completed generation without changing lifecycle."""
        if (
            not generation.work_id.strip()
            or not generation.worker_token.strip()
        ):
            raise ValueError("CV work ID and worker token are required.")
        completed_timestamp = canonical_timestamp(
            generation.completed_at, "CV generation completion time"
        )
        canonical_generation = replace(
            generation, completed_at=completed_timestamp
        )
        with connect(self.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            work = connection.execute(
                """SELECT * FROM work_items
                WHERE id = ? AND application_id = ?
                AND work_type = 'cv_generation'
                AND assessment_id = ?""",
                (
                    generation.work_id,
                    generation.application_id,
                    generation.assessment_id,
                ),
            ).fetchone()
            if work is None:
                raise CvGenerationStateError(
                    "CV generation work was not found."
                )
            if (
                work["state"] == "succeeded"
                and work["finalizer_token"] == generation.worker_token
            ):
                existing = connection.execute(
                    "SELECT * FROM cv_generations WHERE id = ?",
                    (generation.generation_id,),
                ).fetchone()
                if existing is not None and tuple(
                    existing[column] for column in _CV_GENERATION_COLUMNS
                ) == _cv_generation_payload(canonical_generation):
                    return
                raise CvGenerationStateError(
                    "CV generation completion replay does not match the "
                    "stored result."
                )
            if (
                work["state"] != "running"
                or work["worker_token"] != generation.worker_token
            ):
                raise StaleWorkerError("Worker token is no longer current.")
            completed_at = _normalize_timestamp(
                generation.completed_at,
                "CV generation completion time",
            )
            current = connection.execute(
                """SELECT history.stage, assessment.outcome,
                    assessment.completed_at AS assessment_completed_at,
                    assessment.profile_sha256 AS assessment_profile_sha256,
                    assessment.jd_sha256 AS assessment_jd_sha256,
                    assessment.result_sha256 AS assessment_result_sha256,
                    history.effective_from AS stage_effective_from
                FROM application_stage_history AS history
                JOIN assessments AS assessment
                  ON assessment.application_id = history.application_id
                 AND assessment.id = ?
                WHERE history.application_id = ? AND history.is_current = 1""",
                (generation.assessment_id, generation.application_id),
            ).fetchone()
            if current is None:
                raise CvGenerationNotFoundError(
                    "Application assessment was not found."
                )
            stage = str(current["stage"])
            outcome = str(current["outcome"])
            if not (
                (outcome == "matched" and stage == "Assessing")
                or (outcome != "matched" and stage == "Mismatch")
            ):
                raise CvGenerationStateError(
                    "Application is no longer eligible for CV generation."
                )
            if (
                generation.profile_sha256 != work["profile_sha256"]
                or generation.profile_sha256
                != current["assessment_profile_sha256"]
                or generation.jd_sha256 != work["jd_sha256"]
                or generation.jd_sha256 != current["assessment_jd_sha256"]
                or generation.assessment_result_sha256
                != current["assessment_result_sha256"]
            ):
                raise CvGenerationStateError(
                    "CV generation hashes do not match owned inputs."
                )
            optional_hashes = (
                ("prompt_sha256", generation.instruction_sha256),
                ("schema_sha256", generation.schema_sha256),
                ("template_sha256", generation.template_sha256),
                ("layout_sha256", generation.layout_sha256),
            )
            if any(
                work[field] is not None and work[field] != value
                for field, value in optional_hashes
            ):
                raise CvGenerationStateError(
                    "CV generation hashes do not match owned inputs."
                )
            assessment_completed_at = _normalize_timestamp(
                str(current["assessment_completed_at"]),
                "Assessment completion time",
            )
            latest_activity = latest_timestamp(
                work["started_at"], work["heartbeat_at"]
            )
            if latest_activity is not None and timestamp_precedes(
                completed_timestamp, latest_activity
            ):
                raise ValueError(
                    "CV generation completion cannot precede current work "
                    "activity."
                )
            if completed_at < assessment_completed_at:
                raise ValueError(
                    "CV generation completion cannot precede assessment "
                    "completion."
                )
            if timestamp_precedes(
                completed_timestamp,
                canonical_timestamp(
                    str(current["stage_effective_from"]),
                    "Current stage effective time",
                ),
            ):
                raise ValueError(
                    "CV generation completion cannot precede current stage "
                    "effective time."
                )
            generation = replace(
                generation,
                completed_at=completed_timestamp,
            )
            connection.execute(
                f"INSERT INTO cv_generations "
                f"({', '.join(_CV_GENERATION_COLUMNS)}) VALUES "
                f"({', '.join('?' for _ in _CV_GENERATION_COLUMNS)})",
                _cv_generation_payload(generation),
            )
            cursor = connection.execute(
                """UPDATE work_items SET state = 'succeeded',
                completed_at = ?, worker_token = NULL,
                finalizer_token = ?, lease_expires_at = NULL
                WHERE id = ? AND application_id = ?
                AND work_type = 'cv_generation' AND state = 'running'
                AND worker_token = ?""",
                (
                    completed_timestamp,
                    generation.worker_token,
                    generation.work_id,
                    generation.application_id,
                    generation.worker_token,
                ),
            )
            if cursor.rowcount != 1:
                raise StaleWorkerError("Worker token is no longer current.")

    def get(self, generation_id: str) -> dict[str, object]:
        """Return allowlisted CV generation metadata by ID."""
        with connect(self.database_path) as connection:
            row = connection.execute(
                "SELECT * FROM cv_generations WHERE id = ?",
                (generation_id,),
            ).fetchone()
        if row is None:
            raise CvGenerationNotFoundError("CV generation was not found.")
        result = dict(row)
        result["response_ids"] = json.loads(str(result["response_ids"]))
        return result

    def list_for_application(
        self, application_id: int
    ) -> list[dict[str, object]]:
        """Return completed CV generations newest first."""
        with connect(self.database_path) as connection:
            rows = connection.execute(
                """SELECT id, completed_at FROM cv_generations
                WHERE application_id = ?""",
                (application_id,),
            ).fetchall()
        ordered_rows = sorted(
            rows,
            key=lambda row: (
                _normalize_timestamp(
                    str(row["completed_at"]),
                    "CV generation completion time",
                ),
                str(row["id"]),
            ),
            reverse=True,
        )
        return [self.get(str(row["id"])) for row in ordered_rows]
