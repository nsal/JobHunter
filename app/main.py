"""Web routes for the local JobHunter application."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    RedirectResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.cv_files import (
    CvFileError,
    cv_path,
)
from app.cv_uploads import remove_cv_upload, store_cv_upload
from app.database import STAGES, initialize_database
from app.repository import ApplicationNotFoundError, Repository

ROOT = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(ROOT / "app" / "templates"))


def date_only(value: str | None) -> str:
    """Format an ISO timestamp for a date-only summary field."""
    return value[:10] if value else "—"


def preview_text(value: str | None, limit: int = 300) -> str:
    """Return a compact text preview with an ellipsis when it is truncated."""
    if not value:
        return "—"
    return value if len(value) <= limit else f"{value[:limit]}…"


templates.env.filters["date_only"] = date_only
templates.env.filters["preview_text"] = preview_text


def now_value() -> str:
    """Return a value suitable for a datetime-local input and SQLite sorting."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")


def form_values(
    role: str,
    company: str,
    payment: str | None,
    job_url: str | None,
    cv_path: str | None,
    is_recruiter: str | None,
    is_fully_remote: str | None,
    notes: str | None,
    full_jd: str | None,
) -> dict[str, Any]:
    """Collect submitted application fields in repository format."""
    return {
        "role": role,
        "company": company,
        "payment": payment,
        "job_url": job_url,
        "cv_path": cv_path,
        "is_recruiter": is_recruiter,
        "is_fully_remote": is_fully_remote,
        "notes": notes,
        "full_jd": full_jd,
    }


def create_app(database_path: str | Path | None = None) -> FastAPI:
    """Build an application instance, optionally using a supplied database."""
    configured_path = os.getenv("JOBHUNTER_DATABASE")
    path = Path(
        database_path or configured_path or ROOT / "private" / "jobhunter.db"
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        initialize_database(path)
        app.state.repository = Repository(path)
        yield

    app = FastAPI(title="JobHunter", lifespan=lifespan)
    static_directory = ROOT / "app" / "static"
    app.mount("/static", StaticFiles(directory=static_directory), name="static")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    def application_list(request: Request, q: str = "") -> HTMLResponse:
        search = q.strip()
        applications = request.app.state.repository.list_applications(search)
        return templates.TemplateResponse(
            request,
            "applications/index.html",
            {"applications": applications, "q": search},
        )

    @app.get("/applications/new", response_class=HTMLResponse)
    def new_application(request: Request) -> HTMLResponse:
        context = {
            "application": {},
            "action": "/applications",
            "submit_label": "Create application",
            "is_dialog": bool(request.headers.get("HX-Request")),
        }
        if request.headers.get("HX-Request"):
            return templates.TemplateResponse(
                request, "applications/_application_form.html", context
            )
        return templates.TemplateResponse(
            request,
            "applications/form.html",
            context,
        )

    @app.post("/applications", response_class=HTMLResponse)
    async def create_application(
        request: Request,
        role: str = Form(""),
        company: str = Form(""),
        payment: str | None = Form(None),
        job_url: str | None = Form(None),
        cv_path: str | None = Form(None),
        cv_upload: Annotated[UploadFile | None, File()] = None,
        is_recruiter: str | None = Form(None),
        is_fully_remote: str | None = Form(None),
        notes: str | None = Form(None),
        full_jd: str | None = Form(None),
    ) -> Response:
        values = form_values(
            role,
            company,
            payment,
            job_url,
            cv_path,
            is_recruiter,
            is_fully_remote,
            notes,
            full_jd,
        )
        uploaded_location: str | None = None
        try:
            if cv_upload is not None:
                uploaded_location = await store_cv_upload(
                    cv_upload, company, role
                )
                if uploaded_location:
                    values["cv_path"] = uploaded_location
            application_id = request.app.state.repository.create_application(
                values, now_value()
            )
        except (CvFileError, ValueError) as error:
            remove_cv_upload(uploaded_location)
            context = {
                "application": values,
                "action": "/applications",
                "submit_label": "Create application",
                "error": str(error),
                "is_dialog": bool(request.headers.get("HX-Request")),
            }
            if request.headers.get("HX-Request"):
                return templates.TemplateResponse(
                    request, "applications/_application_form.html", context
                )
            return templates.TemplateResponse(
                request,
                "applications/form.html",
                context,
                status_code=422,
            )
        if request.headers.get("HX-Request"):
            return Response(
                headers={"HX-Redirect": f"/applications/{application_id}"}
            )
        return RedirectResponse(
            f"/applications/{application_id}", status_code=303
        )

    @app.get("/applications/{application_id}", response_class=HTMLResponse)
    def application_detail(
        application_id: int, request: Request
    ) -> HTMLResponse:
        try:
            application = request.app.state.repository.get_application(
                application_id
            )
        except ApplicationNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return templates.TemplateResponse(
            request,
            "applications/detail.html",
            {"application": application, "stages": STAGES, "now": now_value()},
        )

    def application_cv(application_id: int, request: Request) -> Path:
        try:
            application = request.app.state.repository.get_application(
                application_id
            )
        except ApplicationNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        location = application.get("cv_path")
        if not isinstance(location, str):
            raise HTTPException(status_code=404, detail="CV file not found.")
        try:
            return cv_path(location)
        except CvFileError as error:
            raise HTTPException(
                status_code=404, detail="CV file not found."
            ) from error

    @app.get("/applications/{application_id}/cv/preview")
    def preview_cv(application_id: int, request: Request) -> FileResponse:
        path = application_cv(application_id, request)
        if path.suffix.lower() != ".pdf":
            raise HTTPException(
                status_code=422, detail="Only PDFs can be previewed."
            )
        return FileResponse(
            path,
            media_type="application/pdf",
            filename=path.name,
            content_disposition_type="inline",
        )

    @app.get("/applications/{application_id}/cv/download")
    def download_cv(application_id: int, request: Request) -> FileResponse:
        path = application_cv(application_id, request)
        media_type = (
            "application/msword"
            if path.suffix.lower() == ".doc"
            else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        return FileResponse(path, media_type=media_type, filename=path.name)

    @app.post("/applications/{application_id}", response_class=HTMLResponse)
    async def update_application(
        application_id: int,
        request: Request,
        role: str = Form(""),
        company: str = Form(""),
        payment: str | None = Form(None),
        job_url: str | None = Form(None),
        cv_path: str | None = Form(None),
        cv_upload: Annotated[UploadFile | None, File()] = None,
        is_recruiter: str | None = Form(None),
        is_fully_remote: str | None = Form(None),
        notes: str | None = Form(None),
        full_jd: str | None = Form(None),
    ) -> Response:
        values = form_values(
            role,
            company,
            payment,
            job_url,
            cv_path,
            is_recruiter,
            is_fully_remote,
            notes,
            full_jd,
        )
        uploaded_location: str | None = None
        try:
            existing = request.app.state.repository.get_application(
                application_id
            )
            if cv_upload is not None:
                uploaded_location = await store_cv_upload(
                    cv_upload, company, role
                )
                if uploaded_location:
                    values["cv_path"] = uploaded_location
            if not values["cv_path"]:
                values["cv_path"] = existing.get("cv_path")
            request.app.state.repository.update_application(
                application_id, values
            )
        except ApplicationNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except (CvFileError, ValueError) as error:
            remove_cv_upload(uploaded_location)
            values["id"] = application_id
            context = {
                "application": values,
                "action": f"/applications/{application_id}",
                "submit_label": "Save changes",
                "error": str(error),
            }
            if request.headers.get("HX-Request"):
                return templates.TemplateResponse(
                    request,
                    "applications/_application_edit_form.html",
                    context,
                )
            return templates.TemplateResponse(
                request,
                "applications/form.html",
                context,
                status_code=422,
            )
        if request.headers.get("HX-Request"):
            return Response(
                headers={"HX-Redirect": f"/applications/{application_id}"}
            )
        return RedirectResponse(
            f"/applications/{application_id}", status_code=303
        )

    @app.post(
        "/applications/{application_id}/stages", response_class=HTMLResponse
    )
    def add_stage(
        application_id: int,
        request: Request,
        stage: str = Form(""),
        effective_from: str = Form(""),
        stage_description: str | None = Form(None),
    ) -> HTMLResponse:
        try:
            request.app.state.repository.add_stage(
                application_id, stage, effective_from, stage_description
            )
            application = request.app.state.repository.get_application(
                application_id
            )
            return templates.TemplateResponse(
                request,
                "applications/_stage_history.html",
                {
                    "application": application,
                    "now": now_value(),
                    "stages": STAGES,
                },
            )
        except ApplicationNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            try:
                application = request.app.state.repository.get_application(
                    application_id
                )
            except ApplicationNotFoundError as missing:
                raise HTTPException(
                    status_code=404, detail=str(missing)
                ) from missing
            return templates.TemplateResponse(
                request,
                "applications/_stage_history.html",
                {
                    "application": application,
                    "error": str(error),
                    "now": now_value(),
                    "stages": STAGES,
                    "stage_values": {
                        "stage": stage,
                        "effective_from": effective_from,
                        "stage_description": stage_description,
                    },
                },
                status_code=422,
            )

    @app.get(
        "/applications/{application_id}/stage-editor",
        response_class=HTMLResponse,
    )
    def stage_editor(application_id: int, request: Request) -> HTMLResponse:
        try:
            application = request.app.state.repository.get_application(
                application_id
            )
        except ApplicationNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return templates.TemplateResponse(
            request,
            "applications/_stage_editor.html",
            {"application": application, "stages": STAGES},
        )

    @app.post(
        "/applications/{application_id}/stage-editor",
        response_class=HTMLResponse,
    )
    def update_dashboard_stage(
        application_id: int,
        request: Request,
        stage: str = Form(""),
        stage_description: str | None = Form(None),
    ) -> Response:
        try:
            request.app.state.repository.update_current_stage(
                application_id, stage, stage_description, now_value()
            )
            application = request.app.state.repository.get_application(
                application_id
            )
        except ApplicationNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            try:
                application = request.app.state.repository.get_application(
                    application_id
                )
            except ApplicationNotFoundError as missing:
                raise HTTPException(
                    status_code=404, detail=str(missing)
                ) from missing
            return templates.TemplateResponse(
                request,
                "applications/_stage_editor.html",
                {
                    "application": application,
                    "stages": STAGES,
                    "error": str(error),
                    "stage_values": {
                        "stage": stage,
                        "stage_description": stage_description,
                    },
                },
                status_code=422,
            )
        if request.headers.get("HX-Request"):
            return templates.TemplateResponse(
                request,
                "applications/_application_row.html",
                {"application": application},
                headers={"HX-Trigger": "close-stage-editor"},
            )
        return RedirectResponse("/", status_code=303)

    @app.get(
        "/applications/{application_id}/notes-editor",
        response_class=HTMLResponse,
    )
    def notes_editor(application_id: int, request: Request) -> HTMLResponse:
        try:
            application = request.app.state.repository.get_application(
                application_id
            )
        except ApplicationNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return templates.TemplateResponse(
            request,
            "applications/_notes_editor.html",
            {"application": application},
        )

    @app.post(
        "/applications/{application_id}/notes-editor",
        response_class=HTMLResponse,
    )
    def update_dashboard_notes(
        application_id: int,
        request: Request,
        notes: str | None = Form(None),
    ) -> Response:
        try:
            request.app.state.repository.update_notes(application_id, notes)
            application = request.app.state.repository.get_application(
                application_id
            )
        except ApplicationNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        if request.headers.get("HX-Request"):
            return templates.TemplateResponse(
                request,
                "applications/_application_row.html",
                {"application": application},
                headers={"HX-Trigger": "close-notes-editor"},
            )
        return RedirectResponse("/", status_code=303)

    return app


app = create_app()
