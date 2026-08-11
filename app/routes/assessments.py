"""Assessment status, retry, and result routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.repository import ApplicationNotFoundError
from app.routes import application_workflow
from app.routes.setup import (
    SetupIncompleteError,
    _require_same_origin,
    require_setup_ready,
)
from app.work.models import WorkState, WorkType
from app.work.repository import WorkRepository, WorkStateError

router = APIRouter()


def _context(request: Request, application_id: int) -> dict[str, Any]:
    """Load an application workflow context or raise a safe 404."""
    try:
        request.app.state.repository.promote_completed_generation(
            application_id
        )
        application = request.app.state.repository.get_application(
            application_id
        )
    except ApplicationNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return {
        "application": application,
        **application_workflow(
            request.app.state.database_path,
            request.app.state.project_root,
            application,
        ),
    }


def _templates() -> Any:
    """Import templates lazily to avoid a main/routes import cycle."""
    from app.main import templates

    return templates


def _redirect_response(request: Request, application_id: int) -> Response:
    """Redirect a completed action for both normal and HTMX clients."""
    location = f"/applications/{application_id}"
    if request.headers.get("HX-Request"):
        return Response(headers={"HX-Redirect": location})
    return RedirectResponse(location, status_code=303)


def _work_error(
    request: Request, application_id: int, message: str, status_code: int
) -> Response:
    """Render a safe action error without exposing implementation details."""
    context = _context(request, application_id)
    context["workflow_error"] = message
    if request.headers.get("HX-Request"):
        return _templates().TemplateResponse(
            request,
            "applications/_work_status.html",
            context,
            status_code=status_code,
        )
    raise HTTPException(status_code=status_code, detail=message)


def _require_workflow_action(
    request: Request, application_id: int
) -> Response | None:
    """Guard a profile-bearing workflow action before loading its context."""
    _require_same_origin(request)
    try:
        require_setup_ready(request)
    except SetupIncompleteError as error:
        return _work_error(request, application_id, str(error), 422)
    return None


@router.get(
    "/applications/{application_id}/work-status",
    response_class=HTMLResponse,
)
def work_status(application_id: int, request: Request) -> HTMLResponse:
    """Render the focused workflow fragment used while work is active."""
    return _templates().TemplateResponse(
        request,
        "applications/_work_status.html",
        _context(request, application_id),
    )


@router.get(
    "/applications/{application_id}/assessment",
    response_class=HTMLResponse,
)
def assessment_result(application_id: int, request: Request) -> HTMLResponse:
    """Render the latest completed assessment fragment."""
    return _templates().TemplateResponse(
        request,
        "applications/_assessment.html",
        _context(request, application_id),
    )


@router.post(
    "/applications/{application_id}/assessment/retry",
    response_class=HTMLResponse,
)
def retry_assessment(application_id: int, request: Request) -> Response:
    """Queue a new assessment only after a terminal assessment failure."""
    guard_error = _require_workflow_action(request, application_id)
    if guard_error is not None:
        return guard_error
    context = _context(request, application_id)
    latest_work = context["latest_work"]
    if latest_work is None or latest_work.work_type is not WorkType.ASSESSMENT:
        return _work_error(
            request,
            application_id,
            "Assessment retry is not available.",
            409,
        )
    if latest_work.state is not WorkState.FAILED:
        message = (
            "Assessment retry is available only after a failed assessment."
        )
        return _work_error(request, application_id, message, 409)
    try:
        WorkRepository(request.app.state.database_path).enqueue(
            application_id,
            WorkType.ASSESSMENT,
            request.app.state.now_value(),
            require_profile_consent=True,
        )
    except WorkStateError as error:
        return _work_error(request, application_id, str(error), 409)
    return _redirect_response(request, application_id)
