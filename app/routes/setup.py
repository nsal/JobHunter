"""Setup readiness and remote profile-sharing acknowledgement routes."""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.ai.providers.factory import is_openai_credential_ready
from app.consent import ConsentRepository, ConsentState
from app.settings import (
    SettingsError,
    load_ai_settings,
    validate_layout_input,
    validate_profile_input,
    validate_template_input,
)

router = APIRouter()


@dataclass(frozen=True)
class SetupStatus:
    """Safe-to-render snapshot of the local setup state."""

    profile_ready: bool
    template_ready: bool
    layout_ready: bool
    settings_ready: bool
    credentials_ready: bool
    assessment_model: str | None
    cv_model: str | None
    consent: ConsentState
    errors: tuple[str, ...]

    @property
    def inputs_ready(self) -> bool:
        """Return whether all private files and tracked settings are valid."""
        return (
            self.profile_ready
            and self.template_ready
            and self.layout_ready
            and self.settings_ready
        )

    @property
    def ready(self) -> bool:
        """Return whether new profile-bearing work may be queued."""
        return (
            self.inputs_ready
            and self.credentials_ready
            and (self.consent.is_granted)
        )

    @property
    def blocking_message(self) -> str:
        """Return a short safe message suitable for an application form."""
        if not self.inputs_ready:
            return "Complete the required setup before creating an application."
        if not self.credentials_ready:
            return "Configure the OpenAI credential before creating an application."
        if not self.consent.is_granted:
            return (
                "Acknowledge remote profile transmission before creating an "
                "application."
            )
        return ""


def _private_readiness(root: Path) -> tuple[bool, bool, bool, list[str]]:
    """Validate private inputs while exposing no private path details."""
    validators = (
        (
            "profile",
            validate_profile_input,
            "Private profile is missing or invalid.",
        ),
        (
            "template",
            validate_template_input,
            "Private CV template is missing or invalid.",
        ),
        (
            "layout",
            validate_layout_input,
            "Private CV layout is missing or invalid.",
        ),
    )
    ready: dict[str, bool] = {}
    errors: list[str] = []
    for name, validator, label in validators:
        try:
            validator(root)
        except SettingsError:
            ready[name] = False
            errors.append(label)
        else:
            ready[name] = True
    return ready["profile"], ready["template"], ready["layout"], errors


def inspect_setup(root: str | Path, database_path: str | Path) -> SetupStatus:
    """Revalidate current settings, private inputs, credentials, and consent."""
    project_root = Path(root).resolve()
    profile_ready, template_ready, layout_ready, errors = _private_readiness(
        project_root
    )
    settings_ready = True
    assessment_model: str | None = None
    cv_model: str | None = None
    try:
        settings = load_ai_settings(project_root / "config" / "ai.yaml")
    except SettingsError:
        settings_ready = False
        errors.append("Tracked AI settings are missing or invalid.")
    else:
        assessment_model = settings.provider.assessment_model
        cv_model = settings.provider.cv_model

    credentials_ready = is_openai_credential_ready(os.environ)
    if not credentials_ready:
        errors.append("OpenAI credential is missing or invalid.")

    consent = ConsentRepository(database_path).get_openai_profile_sharing()
    if not consent.is_granted:
        errors.append("Remote profile transmission is not acknowledged.")

    return SetupStatus(
        profile_ready=profile_ready,
        template_ready=template_ready,
        layout_ready=layout_ready,
        settings_ready=settings_ready,
        credentials_ready=credentials_ready,
        assessment_model=assessment_model,
        cv_model=cv_model,
        consent=consent,
        errors=tuple(dict.fromkeys(errors)),
    )


def _status_context(request: Request) -> dict[str, object]:
    """Build the template context without including filesystem paths."""
    status = inspect_setup(
        request.app.state.project_root,
        request.app.state.database_path,
    )
    return {"setup": status}


def require_setup_ready(request: Request) -> SetupStatus:
    """Return readiness or raise a safe precondition error."""
    status = inspect_setup(
        request.app.state.project_root,
        request.app.state.database_path,
    )
    if not status.ready:
        raise SetupIncompleteError(status.blocking_message)
    return status


class SetupIncompleteError(ValueError):
    """Raised when an operation requires complete local setup."""


def normalize_http_origin(host: str, port: int) -> str:
    """Build the configured HTTP origin from launcher host and port."""
    if not 1 <= port <= 65535:
        raise ValueError("HTTP port must be between 1 and 65535.")
    normalized_host = host.strip()
    if normalized_host.startswith("[") and normalized_host.endswith("]"):
        normalized_host = normalized_host[1:-1]
    if not normalized_host or any(
        character in normalized_host for character in "/?#@ \t\r\n"
    ):
        raise ValueError("HTTP host is invalid.")
    try:
        address = ipaddress.ip_address(normalized_host)
    except ValueError:
        address = None
    if address is not None and address.is_unspecified:
        raise ValueError("HTTP host must be a concrete address or hostname.")
    if address is not None:
        normalized_host = str(address)
    else:
        normalized_host = normalized_host.lower()
    display_host = (
        f"[{normalized_host}]" if ":" in normalized_host else normalized_host
    )
    port_suffix = "" if port == 80 else f":{port}"
    return f"http://{display_host}{port_suffix}"


def normalize_configured_origin(origin: str) -> str:
    """Normalize and validate one explicit configured HTTP origin."""
    if origin != origin.strip():
        raise ValueError("Trusted origin is invalid.")
    parsed = urlsplit(origin)
    if (
        parsed.scheme != "http"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.netloc.endswith(":")
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Trusted origin is invalid.")
    try:
        host = parsed.hostname
        port = parsed.port or 80
    except ValueError as error:
        raise ValueError("Trusted origin is invalid.") from error
    if host is None:
        raise ValueError("Trusted origin is invalid.")
    return normalize_http_origin(host, port)


def _require_same_origin(request: Request) -> None:
    """Require a request Origin matching immutable configured application state."""
    origin = request.headers.get("origin")
    if origin is None:
        raise HTTPException(status_code=403, detail="Unsafe request origin.")
    try:
        normalized_origin = normalize_configured_origin(origin)
    except ValueError:
        raise HTTPException(
            status_code=403, detail="Unsafe request origin."
        ) from None
    if normalized_origin != request.app.state.trusted_origin:
        raise HTTPException(status_code=403, detail="Unsafe request origin.")


@router.get("/setup", response_class=HTMLResponse)
def setup_detail(request: Request) -> HTMLResponse:
    """Render the current setup state."""
    from app.main import templates

    return templates.TemplateResponse(
        request,
        "setup/detail.html",
        _status_context(request),
    )


@router.get("/setup/status", response_class=HTMLResponse)
def setup_status(request: Request) -> HTMLResponse:
    """Render the setup status fragment for incremental refreshes."""
    from app.main import templates

    return templates.TemplateResponse(
        request,
        "setup/_status.html",
        _status_context(request),
    )


@router.post("/setup/acknowledge")
def acknowledge_setup(request: Request) -> RedirectResponse:
    """Record the user's explicit remote profile-sharing acknowledgement."""
    _require_same_origin(request)
    ConsentRepository(
        request.app.state.database_path
    ).acknowledge_openai_profile_sharing(request.app.state.now_value())
    return RedirectResponse("/setup", status_code=303)


@router.post("/setup/revoke")
def revoke_setup(request: Request) -> RedirectResponse:
    """Revoke the user's remote profile-sharing acknowledgement."""
    _require_same_origin(request)
    ConsentRepository(
        request.app.state.database_path
    ).revoke_openai_profile_sharing(request.app.state.now_value())
    return RedirectResponse("/setup", status_code=303)
