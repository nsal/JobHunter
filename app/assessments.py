"""Persistence for immutable completed application assessments."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import uuid4

from app.assessment.scoring import AssessmentOutcome, AssessmentScore
from app.database import connect
from app.work.models import (
    WorkType,
    canonical_timestamp,
    latest_timestamp,
    timestamp_precedes,
)
from app.work.repository import StaleWorkerError, WorkStateError


class AssessmentNotFoundError(ValueError):
    """Raised when an assessment or its application cannot be found."""


class AssessmentStateError(RuntimeError):
    """Raised when lifecycle state no longer permits assessment completion."""


@dataclass(frozen=True)
class AssessmentApplication:
    """Immutable application input needed by the assessment service."""

    application_id: int
    full_jd: str
    artefact_directory: str
    current_stage: str


@dataclass(frozen=True)
class CompletedAssessment:
    """Allowlisted metadata for one successfully completed assessment."""

    assessment_id: str
    application_id: int
    score: AssessmentScore
    model: str
    model_sha256: str
    schema_version: str
    schema_sha256: str
    instruction_sha256: str
    taxonomy_version: str
    taxonomy_sha256: str
    profile_sha256: str
    jd_sha256: str
    provider: str
    response_ids: tuple[str, ...]
    input_tokens: int
    output_tokens: int
    total_tokens: int
    repair_attempted: bool
    result_path: str
    result_sha256: str
    analysis_path: str
    analysis_sha256: str
    completed_at: str
    work_id: str
    worker_token: str


_ASSESSMENT_COLUMNS = (
    "id",
    "application_id",
    "outcome",
    "final_score",
    "supporting_alignment",
    "mandatory_coverage",
    "threshold",
    "all_mandatory_matched",
    "failed_hard_gates",
    "ambiguous_hard_gates",
    "model",
    "model_sha256",
    "schema_version",
    "schema_sha256",
    "instruction_sha256",
    "taxonomy_version",
    "taxonomy_sha256",
    "profile_sha256",
    "jd_sha256",
    "provider",
    "response_ids",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "repair_attempted",
    "result_path",
    "result_sha256",
    "analysis_path",
    "analysis_sha256",
    "completed_at",
)


def _assessment_payload(
    assessment: CompletedAssessment,
) -> tuple[object, ...]:
    """Return the exact SQLite values for one completed assessment."""
    return (
        assessment.assessment_id,
        assessment.application_id,
        assessment.score.outcome.value,
        float(assessment.score.final_score),
        float(assessment.score.supporting_alignment),
        float(assessment.score.mandatory_coverage),
        float(assessment.score.threshold),
        int(assessment.score.all_mandatory_matched),
        json.dumps([gate.value for gate in assessment.score.failed_hard_gates]),
        json.dumps(
            [gate.value for gate in assessment.score.ambiguous_hard_gates]
        ),
        assessment.model,
        assessment.model_sha256,
        assessment.schema_version,
        assessment.schema_sha256,
        assessment.instruction_sha256,
        assessment.taxonomy_version,
        assessment.taxonomy_sha256,
        assessment.profile_sha256,
        assessment.jd_sha256,
        assessment.provider,
        json.dumps(list(assessment.response_ids)),
        assessment.input_tokens,
        assessment.output_tokens,
        assessment.total_tokens,
        int(assessment.repair_attempted),
        assessment.result_path,
        assessment.result_sha256,
        assessment.analysis_path,
        assessment.analysis_sha256,
        assessment.completed_at,
    )


class AssessmentRepository:
    """Read application inputs and append immutable assessment records."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = database_path

    def get_application(self, application_id: int) -> AssessmentApplication:
        """Return the current immutable JD and artefact directory."""
        with connect(self.database_path) as connection:
            row = connection.execute(
                """SELECT a.id, a.full_jd, a.artefact_directory,
                    history.stage AS current_stage
                FROM applications AS a
                JOIN application_stage_history AS history
                  ON history.application_id = a.id
                 AND history.is_current = 1
                WHERE a.id = ?""",
                (application_id,),
            ).fetchone()
        if row is None:
            raise AssessmentNotFoundError("Application not found.")
        return AssessmentApplication(
            application_id=int(row["id"]),
            full_jd=str(row["full_jd"]),
            artefact_directory=str(row["artefact_directory"]),
            current_stage=str(row["current_stage"]),
        )

    def preflight(
        self, application_id: int, work_id: str, worker_token: str
    ) -> None:
        """Verify exact assessment work ownership without changing state."""
        if not work_id.strip() or not worker_token.strip():
            raise ValueError(
                "Assessment work ID and worker token are required."
            )
        with connect(self.database_path) as connection:
            work = connection.execute(
                """SELECT application_id, work_type, state, worker_token
                FROM work_items WHERE id = ?""",
                (work_id,),
            ).fetchone()
        if work is None or int(work["application_id"]) != application_id:
            raise AssessmentStateError("Assessment work was not found.")
        if work["work_type"] != WorkType.ASSESSMENT.value:
            raise AssessmentStateError("Assessment work was not found.")
        if work["state"] != "running" or work["worker_token"] != worker_token:
            raise StaleWorkerError("Worker token is no longer current.")

    def add_completed(self, assessment: CompletedAssessment) -> None:
        """Persist one completed result and its mismatch transition atomically."""
        if not assessment.completed_at.strip():
            raise ValueError("assessment completion time is required")
        if (
            not assessment.work_id.strip()
            or not assessment.worker_token.strip()
        ):
            raise ValueError(
                "Assessment work ID and worker token are required."
            )
        work_completed_at = canonical_timestamp(
            assessment.completed_at, "assessment completion time"
        )
        canonical_assessment = replace(
            assessment, completed_at=work_completed_at
        )
        with connect(self.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            work = connection.execute(
                """SELECT * FROM work_items
                WHERE id = ? AND application_id = ?
                AND work_type = 'assessment'""",
                (assessment.work_id, assessment.application_id),
            ).fetchone()
            if work is None:
                raise AssessmentStateError("Assessment work was not found.")
            if (
                work["state"] == "succeeded"
                and work["finalizer_token"] == assessment.worker_token
            ):
                if work["assessment_id"] != assessment.assessment_id:
                    raise AssessmentStateError(
                        "Assessment completion replay does not match the "
                        "stored result."
                    )
                existing = connection.execute(
                    "SELECT * FROM assessments WHERE id = ?",
                    (assessment.assessment_id,),
                ).fetchone()
                if existing is not None and tuple(
                    existing[column] for column in _ASSESSMENT_COLUMNS
                ) == _assessment_payload(canonical_assessment):
                    return
                raise AssessmentStateError(
                    "Assessment completion replay does not match the stored "
                    "result."
                )
            if (
                work["state"] != "running"
                or work["worker_token"] != assessment.worker_token
            ):
                raise StaleWorkerError("Worker token is no longer current.")
            current = connection.execute(
                """SELECT id, stage, stage_sequence, effective_from
                FROM application_stage_history
                WHERE application_id = ? AND is_current = 1""",
                (assessment.application_id,),
            ).fetchone()
            if current is None:
                raise AssessmentNotFoundError("Application not found.")
            if str(current["stage"]) != "Assessing":
                raise AssessmentStateError(
                    "Application is no longer awaiting assessment."
                )
            latest_activity = latest_timestamp(
                work["started_at"], work["heartbeat_at"]
            )
            if latest_activity is not None and timestamp_precedes(
                work_completed_at, latest_activity
            ):
                raise ValueError(
                    "Assessment completion cannot precede current work "
                    "activity."
                )
            if timestamp_precedes(
                work_completed_at, str(current["effective_from"])
            ):
                raise ValueError(
                    "assessment completion cannot precede the current stage"
                )

            assessment = canonical_assessment

            connection.execute(
                f"INSERT INTO assessments ({', '.join(_ASSESSMENT_COLUMNS)}) "
                f"VALUES ({', '.join('?' for _ in _ASSESSMENT_COLUMNS)})",
                _assessment_payload(assessment),
            )
            cursor = connection.execute(
                """UPDATE work_items SET state = 'succeeded',
                completed_at = ?, assessment_id = ?, worker_token = NULL,
                finalizer_token = ?, lease_expires_at = NULL
                WHERE id = ? AND application_id = ?
                AND work_type = 'assessment' AND state = 'running'
                AND worker_token = ?""",
                (
                    work_completed_at,
                    assessment.assessment_id,
                    assessment.worker_token,
                    assessment.work_id,
                    assessment.application_id,
                    assessment.worker_token,
                ),
            )
            if cursor.rowcount != 1:
                raise StaleWorkerError("Worker token is no longer current.")
            if assessment.score.outcome is not AssessmentOutcome.MATCHED:
                connection.execute(
                    """UPDATE application_stage_history
                    SET effective_to = ?, is_current = 0
                    WHERE id = ?""",
                    (assessment.completed_at, current["id"]),
                )
                connection.execute(
                    """INSERT INTO application_stage_history (
                        application_id, stage, stage_sequence,
                        stage_description, effective_from, is_current
                    ) VALUES (?, 'Mismatch', ?, ?, ?, 1)""",
                    (
                        assessment.application_id,
                        int(current["stage_sequence"]) + 1,
                        assessment.score.outcome.value,
                        assessment.completed_at,
                    ),
                )
            elif assessment.score.outcome is AssessmentOutcome.MATCHED:
                try:
                    connection.execute(
                        """INSERT INTO work_items (
                            id, application_id, work_type, state,
                            available_at, current_step, queued_at, assessment_id,
                            profile_sha256, jd_sha256
                        ) VALUES (?, ?, ?, 'queued', ?, 'generation', ?, ?, ?, ?)""",
                        (
                            uuid4().hex,
                            assessment.application_id,
                            WorkType.CV_GENERATION.value,
                            work_completed_at,
                            work_completed_at,
                            assessment.assessment_id,
                            assessment.profile_sha256,
                            assessment.jd_sha256,
                        ),
                    )
                except sqlite3.IntegrityError as error:
                    raise WorkStateError(
                        "Application already has active generation work."
                    ) from error

    def get(self, assessment_id: str) -> dict[str, object]:
        """Return allowlisted assessment metadata by ID."""
        with connect(self.database_path) as connection:
            row = connection.execute(
                "SELECT * FROM assessments WHERE id = ?", (assessment_id,)
            ).fetchone()
        if row is None:
            raise AssessmentNotFoundError("Assessment not found.")
        result = dict(row)
        for field in (
            "failed_hard_gates",
            "ambiguous_hard_gates",
            "response_ids",
        ):
            result[field] = json.loads(str(result[field]))
        return result

    def list_for_application(
        self, application_id: int
    ) -> list[dict[str, object]]:
        """Return completed assessments newest first."""
        with connect(self.database_path) as connection:
            rows = connection.execute(
                """SELECT * FROM assessments WHERE application_id = ?
                ORDER BY completed_at DESC, id DESC""",
                (application_id,),
            ).fetchall()
        return [self.get(str(row["id"])) for row in rows]
