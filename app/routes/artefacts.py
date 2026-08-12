"""Safe handoff of private artefact directories to macOS Finder."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from app.artefacts import ArtefactStore, UnsafeArtefactPathError
from app.repository import ApplicationNotFoundError
from app.routes.setup import _require_same_origin

router = APIRouter()


@router.post("/applications/{application_id}/artefacts/open")
def open_artefacts(application_id: int, request: Request) -> Response:
    """Open an existing, contained application directory in Finder."""
    _require_same_origin(request)
    try:
        application = request.app.state.repository.get_application(
            application_id
        )
    except ApplicationNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error

    try:
        store = ArtefactStore(
            Path(request.app.state.project_root) / "private" / "artefacts"
        )
        directory = store.resolve(str(application["artefact_directory"]))
    except OSError, UnsafeArtefactPathError:
        raise HTTPException(
            status_code=404, detail="Artefact directory is unavailable."
        ) from None
    if not directory.is_dir():
        raise HTTPException(
            status_code=404, detail="Artefact directory is unavailable."
        )
    if sys.platform != "darwin":
        raise HTTPException(
            status_code=501,
            detail="Finder opening is supported only on macOS.",
        )
    try:
        subprocess.run(
            ["open", str(directory)],
            check=True,
            shell=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError, subprocess.SubprocessError:
        raise HTTPException(
            status_code=502, detail="Finder could not open the artefacts."
        ) from None
    if request.headers.get("HX-Request"):
        return Response(headers={"HX-Trigger": "artefacts-opened"})
    return RedirectResponse(f"/applications/{application_id}", status_code=303)
