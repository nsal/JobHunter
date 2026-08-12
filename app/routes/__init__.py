"""Shared route helpers for application workflow views."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.artefacts import (
    ArtefactStore,
    UnsafeArtefactPathError,
    sha256_bytes,
)
from app.assessments import AssessmentRepository
from app.cv_generations import CvGenerationRepository
from app.work.models import WorkState, WorkType
from app.work.repository import WorkRepository


def _assessment_view(
    record: dict[str, object], store: ArtefactStore
) -> dict[str, object]:
    """Expose assessment metadata and its safe, persisted analysis."""
    view: dict[str, object] = {
        key: record.get(key)
        for key in (
            "id",
            "outcome",
            "final_score",
            "supporting_alignment",
            "mandatory_coverage",
            "threshold",
            "all_mandatory_matched",
            "failed_hard_gates",
            "ambiguous_hard_gates",
            "model",
            "provider",
            "completed_at",
        )
    }
    analysis: object = {}
    try:
        analysis_bytes = store.read_bytes(str(record["analysis_path"]))
        expected_sha256 = record["analysis_sha256"]
        if not isinstance(expected_sha256, str):
            raise TypeError("Missing analysis digest.")
        if sha256_bytes(analysis_bytes) != expected_sha256:
            raise ValueError("Analysis digest does not match.")
        analysis = json.loads(analysis_bytes.decode("utf-8"))
    except OSError, TypeError, UnicodeDecodeError, ValueError, KeyError:
        analysis = {}
    if not isinstance(analysis, dict):
        analysis = {}
    analysis_text = analysis.get("analysis", "")
    gaps = analysis.get("gaps", [])
    view["analysis"] = analysis_text if isinstance(analysis_text, str) else ""
    view["gaps"] = (
        [gap for gap in gaps if isinstance(gap, (str, dict))]
        if isinstance(gaps, list)
        else []
    )
    return view


def application_workflow(
    database_path: str | Path,
    project_root: str | Path,
    application: dict[str, Any],
) -> dict[str, object]:
    """Build the non-private workflow data shared by dashboard and detail."""
    application_id = int(application["id"])
    assessment_records = AssessmentRepository(
        database_path
    ).list_for_application(application_id)
    generation_records = CvGenerationRepository(
        database_path
    ).list_for_application(application_id)
    work_items = WorkRepository(database_path).list_for_application(
        application_id
    )
    store: ArtefactStore | None = None
    try:
        store = ArtefactStore(Path(project_root) / "private" / "artefacts")
        artefact_directory = store.resolve(
            str(application["artefact_directory"])
        )
        artefacts_available = artefact_directory.is_dir()
    except OSError, UnsafeArtefactPathError:
        artefacts_available = False

    assessment = None
    if assessment_records and store is not None:
        assessment = _assessment_view(assessment_records[0], store)
    latest_work = work_items[0] if work_items else None
    active_work = next(
        (
            item
            for item in work_items
            if item.state in (WorkState.QUEUED, WorkState.RUNNING)
        ),
        None,
    )
    generation = generation_records[0] if generation_records else None
    current_stage = str(application.get("current_stage", ""))
    assessment_outcome = (
        assessment.get("outcome") if isinstance(assessment, dict) else None
    )
    assessment_retry_available = (
        latest_work is not None
        and latest_work.work_type is WorkType.ASSESSMENT
        and latest_work.state is WorkState.FAILED
        and current_stage == "Assessing"
    )
    cv_retry_available = (
        latest_work is not None
        and latest_work.work_type is WorkType.CV_GENERATION
        and latest_work.state is WorkState.FAILED
        and (
            (assessment_outcome == "matched" and current_stage == "Assessing")
            or (
                assessment_outcome is not None
                and assessment_outcome != "matched"
                and current_stage == "Mismatch"
            )
        )
    )
    return {
        "assessment": assessment,
        "generation": generation,
        "work_items": work_items,
        "latest_work": latest_work,
        "active_work": active_work,
        "assessment_retry_available": assessment_retry_available,
        "cv_retry_available": cv_retry_available,
        "artefacts_available": artefacts_available,
        "assessment_work": next(
            (
                item
                for item in work_items
                if item.work_type is WorkType.ASSESSMENT
            ),
            None,
        ),
        "generation_work": next(
            (
                item
                for item in work_items
                if item.work_type is WorkType.CV_GENERATION
            ),
            None,
        ),
    }


def add_workflow_to_applications(
    applications: list[dict[str, Any]],
    database_path: str | Path,
    project_root: str | Path,
) -> list[dict[str, Any]]:
    """Attach compact workflow summaries to dashboard application rows."""
    for application in applications:
        application.update(
            application_workflow(database_path, project_root, application)
        )
    return applications
