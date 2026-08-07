"""Persistence for immutable completed application assessments."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from app.assessment.scoring import AssessmentOutcome, AssessmentScore
from app.database import connect


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

    def add_completed(self, assessment: CompletedAssessment) -> None:
        """Persist one completed result and its mismatch transition atomically."""
        if not assessment.completed_at.strip():
            raise ValueError("assessment completion time is required")
        with connect(self.database_path) as connection:
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
            if assessment.completed_at < str(current["effective_from"]):
                raise ValueError(
                    "assessment completion cannot precede the current stage"
                )

            connection.execute(
                """INSERT INTO assessments (
                    id, application_id, outcome, final_score,
                    supporting_alignment, mandatory_coverage, threshold,
                    all_mandatory_matched, failed_hard_gates,
                    ambiguous_hard_gates, model, model_sha256, schema_version,
                    schema_sha256, instruction_sha256, taxonomy_version,
                    taxonomy_sha256, profile_sha256, jd_sha256, provider,
                    response_ids, input_tokens, output_tokens, total_tokens,
                    repair_attempted, result_path, result_sha256,
                    analysis_path, analysis_sha256, completed_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )""",
                (
                    assessment.assessment_id,
                    assessment.application_id,
                    assessment.score.outcome.value,
                    float(assessment.score.final_score),
                    float(assessment.score.supporting_alignment),
                    float(assessment.score.mandatory_coverage),
                    float(assessment.score.threshold),
                    int(assessment.score.all_mandatory_matched),
                    json.dumps(
                        [
                            gate.value
                            for gate in assessment.score.failed_hard_gates
                        ]
                    ),
                    json.dumps(
                        [
                            gate.value
                            for gate in assessment.score.ambiguous_hard_gates
                        ]
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
                ),
            )
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
