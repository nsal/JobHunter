"""Safe access to locally referenced CV files."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import unquote, urlsplit

CV_ROOT_ENV = "JOBHUNTER_CV_ROOT"
CV_SUFFIXES = frozenset({".pdf", ".doc", ".docx"})


class CvFileError(ValueError):
    """Raised when a CV file is unsafe or unsupported."""


def cv_root() -> Path:
    """Return the resolved private directory used for managed CV artefacts."""
    configured = os.getenv(CV_ROOT_ENV)
    return (
        Path(configured).expanduser().resolve()
        if configured
        else (
            Path(__file__).resolve().parent.parent
            / "private"
            / "cv"
            / "artefacts"
        ).resolve()
    )


def _location_path(value: str) -> Path:
    parsed = urlsplit(value)
    if parsed.scheme:
        if (
            parsed.scheme.lower() != "file"
            or parsed.netloc not in {"", "localhost"}
            or parsed.query
            or parsed.fragment
        ):
            raise CvFileError(
                "CV location must be an absolute local file path."
            )
        path = Path(unquote(parsed.path))
    else:
        path = Path(value)
    if not path.is_absolute():
        raise CvFileError("CV location must be an absolute local file path.")
    return path


def cv_path(value: str) -> Path:
    """Resolve a CV path and ensure it is a supported file under the root."""
    try:
        path = _location_path(value).resolve(strict=True)
    except OSError as error:
        raise CvFileError("CV file does not exist.") from error
    root = cv_root()
    try:
        path.relative_to(root)
    except ValueError as error:
        raise CvFileError(
            "CV file must be inside the configured CV root."
        ) from error
    if not path.is_file():
        raise CvFileError("CV location must refer to a file.")
    if path.suffix.lower() not in CV_SUFFIXES:
        raise CvFileError("CV must be a PDF, DOC, or DOCX file.")
    return path


def normalize_cv_location(value: str | None) -> str | None:
    """Return an optional CV location as a canonical validated file URI."""
    cleaned = (value or "").strip()
    if not cleaned:
        return None
    return cv_path(cleaned).as_uri()
