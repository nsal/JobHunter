"""CV-generation queue and retry routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from app.cv_generations import CvGenerationRepository, CvGenerationStateError
from app.routes.assessments import (
    _context,
    _redirect_response,
    _require_workflow_action,
    _work_error,
)
from app.work.models import WorkState, WorkType
from app.work.repository import WorkRepository, WorkStateError

router = APIRouter()


def _assessment_id(context: dict[str, Any], supplied: str | None) -> str:
    """Use a submitted assessment ID or the latest completed assessment."""
    if supplied and supplied.strip():
        return supplied.strip()
    assessment = context.get("assessment")
    if isinstance(assessment, dict) and assessment.get("id"):
        return str(assessment["id"])
    raise HTTPException(
        status_code=409,
        detail="A completed assessment is required before CV generation.",
    )


@router.post(
    "/applications/{application_id}/cv-generations",
    response_class=HTMLResponse,
)
def generate_cv(
    application_id: int,
    request: Request,
    assessment_id: str | None = Form(None),
) -> Response:
    """Queue explicit CV generation for a completed mismatch."""
    guard_error = _require_workflow_action(request, application_id)
    if guard_error is not None:
        return guard_error
    context = _context(request, application_id)
    try:
        selected_assessment = _assessment_id(context, assessment_id)
        CvGenerationRepository(
            request.app.state.database_path
        ).enqueue_override(
            application_id,
            selected_assessment,
            request.app.state.now_value(),
            require_profile_consent=True,
        )
    except (CvGenerationStateError, ValueError) as error:
        return _work_error(request, application_id, str(error), 409)
    return _redirect_response(request, application_id)


@router.post(
    "/applications/{application_id}/cv-generations/retry",
    response_class=HTMLResponse,
)
def retry_cv_generation(application_id: int, request: Request) -> Response:
    """Queue a replacement CV attempt after a terminal failure."""
    guard_error = _require_workflow_action(request, application_id)
    if guard_error is not None:
        return guard_error
    context = _context(request, application_id)
    latest_work = context["latest_work"]
    if (
        latest_work is None
        or latest_work.work_type is not WorkType.CV_GENERATION
    ):
        return _work_error(
            request, application_id, "CV retry is not available.", 409
        )
    if latest_work.state is not WorkState.FAILED:
        return _work_error(
            request,
            application_id,
            "CV retry is available only after a failed generation.",
            409,
        )
    if latest_work.assessment_id is None:
        return _work_error(
            request, application_id, "CV generation assessment is missing.", 409
        )
    try:
        assessment = context.get("assessment")
        profile_sha256 = (
            str(assessment["profile_sha256"])
            if isinstance(assessment, dict) and assessment.get("profile_sha256")
            else None
        )
        jd_sha256 = (
            str(assessment["jd_sha256"])
            if isinstance(assessment, dict) and assessment.get("jd_sha256")
            else None
        )
        WorkRepository(request.app.state.database_path).enqueue(
            application_id,
            WorkType.CV_GENERATION,
            request.app.state.now_value(),
            assessment_id=latest_work.assessment_id,
            profile_sha256=profile_sha256,
            jd_sha256=jd_sha256,
            require_profile_consent=True,
        )
    except WorkStateError as error:
        return _work_error(request, application_id, str(error), 409)
    return _redirect_response(request, application_id)


@router.post(
    "/applications/{application_id}/generate-cv",
    include_in_schema=False,
)
def generate_cv_alias(
    application_id: int,
    request: Request,
    assessment_id: str | None = Form(None),
) -> Response:
    """Support the concise action URL used by older local templates."""
    return generate_cv(application_id, request, assessment_id)
