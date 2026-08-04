"""Private storage for CVs selected through a browser file input."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from pathlib import Path

from fastapi import UploadFile

from app.cv_files import CV_SUFFIXES, CvFileError, cv_path, cv_root

MAX_CV_BYTES = 10 * 1024 * 1024
CHUNK_SIZE = 64 * 1024


def _path_segment(value: str, fallback: str) -> str:
    """Return a portable non-empty directory segment."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    return cleaned.strip("._-") or fallback


def upload_directory(
    company: str, role: str, today: date | None = None
) -> Path:
    """Return the private destination directory for a CV upload."""
    upload_date = today or datetime.now(UTC).date()
    return (
        cv_root()
        / _path_segment(company, "unknown-company")
        / (f"{upload_date.isoformat()} {_path_segment(role, 'unknown-role')}")
    )


def _destination(directory: Path, filename: str) -> Path:
    """Return a non-conflicting destination within an upload directory."""
    safe_name = (
        _path_segment(Path(filename).stem, "cv") + Path(filename).suffix.lower()
    )
    candidate = directory / safe_name
    sequence = 2
    while candidate.exists():
        candidate = directory / f"{candidate.stem}-{sequence}{candidate.suffix}"
        sequence += 1
    return candidate


async def store_cv_upload(
    upload: UploadFile, company: str, role: str
) -> str | None:
    """Store an uploaded CV safely and return its canonical file URI."""
    filename = upload.filename or ""
    if not filename:
        await upload.close()
        return None
    if Path(filename).suffix.lower() not in CV_SUFFIXES:
        await upload.close()
        raise CvFileError("CV must be a PDF, DOC, or DOCX file.")

    directory = upload_directory(company, role)
    directory.mkdir(parents=True, exist_ok=True)
    destination = _destination(directory, filename)
    temporary = destination.with_name(f".{destination.name}.uploading")
    total = 0
    try:
        with temporary.open("xb") as handle:
            while chunk := await upload.read(CHUNK_SIZE):
                total += len(chunk)
                if total > MAX_CV_BYTES:
                    raise CvFileError("CV upload must not exceed 10 MiB.")
                handle.write(chunk)
        if total == 0:
            raise CvFileError("CV upload must not be empty.")
        temporary.replace(destination)
    except OSError as error:
        raise CvFileError("CV upload could not be saved.") from error
    finally:
        await upload.close()
        temporary.unlink(missing_ok=True)
    return destination.resolve().as_uri()


def remove_cv_upload(location: str | None) -> None:
    """Remove a newly stored CV after a later operation fails."""
    if not location:
        return
    try:
        cv_path(location).unlink(missing_ok=True)
    except CvFileError:
        return
