"""Web routes for the local JobHunter application."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import (
    HTMLResponse,
    RedirectResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.types import Scope

from app.consent import ConsentRequiredError
from app.database import STAGES, initialize_database
from app.repository import ApplicationNotFoundError, Repository
from app.routes import add_workflow_to_applications, application_workflow
from app.routes.artefacts import router as artefacts_router
from app.routes.assessments import router as assessments_router
from app.routes.cv_generations import router as cv_generations_router
from app.routes.setup import (
    SetupIncompleteError,
    _require_same_origin,
    inspect_setup,
    normalize_configured_origin,
    normalize_http_origin,
    require_setup_ready,
    router,
)

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


class NoCacheStaticFiles(StaticFiles):
    """Serve local static files that browsers revalidate on each refresh."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        """Add the cache policy after the static response is resolved."""
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


def now_value() -> str:
    """Return a value suitable for a datetime-local input and SQLite sorting."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")


def run_server(
    database_path: str | Path,
    project_root: str | Path = ROOT,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Run one FastAPI/Uvicorn process for the launcher."""
    import uvicorn

    uvicorn.run(
        create_app(
            database_path,
            project_root=project_root,
            trusted_origin=normalize_http_origin(host, port),
        ),
        host=host,
        port=port,
        log_level="info",
    )


def form_values(
    role: str,
    company: str,
    payment: str | None,
    job_url: str | None,
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
        "is_recruiter": is_recruiter,
        "is_fully_remote": is_fully_remote,
        "notes": notes,
        "full_jd": full_jd,
    }


def create_app(
    database_path: str | Path | None = None,
    project_root: str | Path | None = None,
    trusted_origin: str = "http://testserver",
) -> FastAPI:
    """Build an application instance, optionally using a supplied database."""
    configured_path = os.getenv("JOBHUNTER_DATABASE")
    path = Path(
        database_path or configured_path or ROOT / "private" / "jobhunter.db"
    )
    root = Path(project_root or ROOT).resolve()
    normalized_origin = normalize_configured_origin(trusted_origin)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        initialize_database(path)
        app.state.repository = Repository(path)
        app.state.database_path = path
        app.state.project_root = root
        app.state.now_value = now_value
        app.state.trusted_origin = normalized_origin
        yield

    app = FastAPI(title="JobHunter", lifespan=lifespan)
    app.include_router(router)
    app.include_router(assessments_router)
    app.include_router(cv_generations_router)
    app.include_router(artefacts_router)
    static_directory = ROOT / "app" / "static"
    app.mount(
        "/static",
        NoCacheStaticFiles(directory=static_directory),
        name="static",
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    def application_list(request: Request, q: str = "") -> HTMLResponse:
        search = q.strip()
        repository = request.app.state.repository
        applications = repository.list_applications(search)
        for application in applications:
            repository.promote_completed_generation(int(application["id"]))
        if applications:
            applications = repository.list_applications(search)
        add_workflow_to_applications(
            applications,
            request.app.state.database_path,
            request.app.state.project_root,
        )
        return templates.TemplateResponse(
            request,
            "applications/index.html",
            {"applications": applications, "q": search},
        )

    @app.get("/applications/new", response_class=HTMLResponse)
    def new_application(request: Request) -> HTMLResponse:
        setup_error = inspect_setup(
            request.app.state.project_root,
            request.app.state.database_path,
        ).blocking_message
        context = {
            "application": {},
            "action": "/applications",
            "submit_label": "Create application",
            "is_dialog": bool(request.headers.get("HX-Request")),
            "setup_error": setup_error,
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
    def create_application(
        request: Request,
        role: str = Form(""),
        company: str = Form(""),
        payment: str | None = Form(None),
        job_url: str | None = Form(None),
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
            is_recruiter,
            is_fully_remote,
            notes,
            full_jd,
        )
        try:
            _require_same_origin(request)
            require_setup_ready(request)
            application_id = request.app.state.repository.create_application(
                values,
                now_value(),
                require_profile_consent=True,
            )
        except (
            ConsentRequiredError,
            SetupIncompleteError,
            ValueError,
        ) as error:
            context = {
                "application": values,
                "action": "/applications",
                "submit_label": "Create application",
                "error": ""
                if isinstance(error, SetupIncompleteError)
                else str(error),
                "setup_error": (
                    str(error)
                    if isinstance(
                        error, (ConsentRequiredError, SetupIncompleteError)
                    )
                    else ""
                ),
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
            request.app.state.repository.promote_completed_generation(
                application_id
            )
            application = request.app.state.repository.get_application(
                application_id
            )
        except ApplicationNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        workflow = application_workflow(
            request.app.state.database_path,
            request.app.state.project_root,
            application,
        )
        application.update(workflow)
        return templates.TemplateResponse(
            request,
            "applications/detail.html",
            {
                "application": application,
                **workflow,
                "stages": STAGES,
                "now": now_value(),
            },
        )

    @app.post("/applications/{application_id}", response_class=HTMLResponse)
    def update_application(
        application_id: int,
        request: Request,
        role: str = Form(""),
        company: str = Form(""),
        payment: str | None = Form(None),
        job_url: str | None = Form(None),
        is_recruiter: str | None = Form(None),
        is_fully_remote: str | None = Form(None),
        notes: str | None = Form(None),
    ) -> Response:
        values = form_values(
            role,
            company,
            payment,
            job_url,
            is_recruiter,
            is_fully_remote,
            notes,
            None,
        )
        try:
            request.app.state.repository.update_application(
                application_id, values
            )
        except ApplicationNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
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
            application.update(
                application_workflow(
                    request.app.state.database_path,
                    request.app.state.project_root,
                    application,
                )
            )
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
            application.update(
                application_workflow(
                    request.app.state.database_path,
                    request.app.state.project_root,
                    application,
                )
            )
            return templates.TemplateResponse(
                request,
                "applications/_application_row.html",
                {"application": application},
                headers={"HX-Trigger": "close-notes-editor"},
            )
        return RedirectResponse("/", status_code=303)

    return app


app = create_app(
    trusted_origin=normalize_http_origin("127.0.0.1", 8000),
)
