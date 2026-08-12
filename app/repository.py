"""Persistence operations for applications and immutable stage history."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from app.artefacts import allocate_application_directory
from app.consent import ConsentRequiredError, has_openai_profile_sharing_consent
from app.database import STAGES, connect
from app.work.models import canonical_timestamp


class ApplicationNotFoundError(ValueError):
    """Raised when an application does not exist."""


def _required(value: str | None, field: str) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        raise ValueError(f"{field} is required.")
    return cleaned


def _optional(value: str | None) -> str | None:
    cleaned = (value or "").strip()
    return cleaned or None


def _flag(value: bool | str | int | None) -> int:
    return int(value in (True, 1, "1", "true", "on", "yes"))


def _job_url(value: str | None) -> str | None:
    """Return an optional absolute HTTP(S) job URL."""
    cleaned = _optional(value)
    if cleaned is None:
        return None
    parsed = urlsplit(cleaned)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Job URL must be an absolute HTTP or HTTPS URL.")
    return cleaned


def _safe_job_url(value: Any) -> str | None:
    """Return a stored job URL only when it remains safe to render."""
    if not isinstance(value, str):
        return None
    try:
        return _job_url(value)
    except ValueError:
        return None


def _sanitize_stored_links(application: dict[str, Any]) -> dict[str, Any]:
    """Normalize persisted links before exposing them to a template."""
    application["job_url"] = _safe_job_url(application.get("job_url"))
    return application


class Repository:
    """Small repository layer backed by parameterised sqlite3 queries."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = database_path

    def create_application(
        self,
        values: Mapping[str, Any],
        effective_from: str,
        *,
        require_profile_consent: bool = False,
    ) -> int:
        """Create an application and its initial Assessing history.

        The application and stage record are written atomically.
        """
        role = _required(values.get("role"), "Role")
        company = _required(values.get("company"), "Company")
        full_jd = _required(values.get("full_jd"), "Full job description")
        if not effective_from:
            raise ValueError("Created date is required.")
        work_timestamp = canonical_timestamp(effective_from, "Created date")
        with connect(self.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            if (
                require_profile_consent
                and not has_openai_profile_sharing_consent(connection)
            ):
                raise ConsentRequiredError()
            pending_directory = f"pending/{uuid4().hex}"
            cursor = connection.execute(
                """INSERT INTO applications (
                    role, company, payment, job_url, is_recruiter,
                    is_fully_remote, notes, full_jd, created_at,
                    artefact_directory
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    role,
                    company,
                    _optional(values.get("payment")),
                    _job_url(values.get("job_url")),
                    _flag(values.get("is_recruiter")),
                    _flag(values.get("is_fully_remote")),
                    _optional(values.get("notes")),
                    full_jd,
                    effective_from,
                    pending_directory,
                ),
            )
            if cursor.lastrowid is None:
                raise RuntimeError(
                    "Application insertion did not return an ID."
                )
            application_id = cursor.lastrowid
            existing_directories = {
                str(row["artefact_directory"])
                for row in connection.execute(
                    """SELECT artefact_directory FROM applications
                    WHERE id != ?""",
                    (application_id,),
                )
            }
            artefact_directory = allocate_application_directory(
                company,
                role,
                effective_from,
                application_id,
                existing_directories,
            )
            connection.execute(
                """UPDATE applications SET artefact_directory = ?
                WHERE id = ?""",
                (artefact_directory, application_id),
            )
            connection.execute(
                """INSERT INTO application_stage_history (
                    application_id, stage, stage_sequence, effective_from,
                    is_current
                ) VALUES (?, 'Assessing', 1, ?, 1)""",
                (application_id, effective_from),
            )
            connection.execute(
                """INSERT INTO work_items (
                    id, application_id, work_type, state, available_at,
                    current_step, queued_at
                ) VALUES (?, ?, 'assessment', 'queued', ?, 'assessment', ?)""",
                (
                    uuid4().hex,
                    application_id,
                    work_timestamp,
                    work_timestamp,
                ),
            )
        return application_id

    def list_applications(
        self, search: str | None = None
    ) -> list[dict[str, Any]]:
        """Return applications ordered by their current-stage start time."""
        query = (search or "").strip()
        where = ""
        parameters: tuple[str, ...] = ()
        if query:
            pattern = f"%{query.lower()}%"
            where = """WHERE LOWER(a.role) LIKE ?
                OR LOWER(a.company) LIKE ?
                OR LOWER(COALESCE(a.notes, '')) LIKE ?"""
            parameters = (pattern, pattern, pattern)
        with connect(self.database_path) as connection:
            rows = connection.execute(
                """SELECT a.*, h.stage AS current_stage,
                    h.stage_description AS current_stage_description,
                    h.effective_from AS last_updated_date,
                    (SELECT submitted.effective_from
                     FROM application_stage_history AS submitted
                     WHERE submitted.application_id = a.id
                       AND submitted.stage = 'Submitted'
                     ORDER BY submitted.stage_sequence
                     LIMIT 1) AS submitted_date
                FROM applications AS a
                JOIN application_stage_history AS h
                  ON h.application_id = a.id AND h.is_current = 1
                """
                + where
                + """
                ORDER BY h.effective_from DESC, a.id DESC""",
                parameters,
            ).fetchall()
        return [_sanitize_stored_links(dict(row)) for row in rows]

    def get_application(self, application_id: int) -> dict[str, Any]:
        """Return application details, summary dates, and complete history."""
        with connect(self.database_path) as connection:
            row = connection.execute(
                """SELECT a.*, h.stage AS current_stage,
                    h.stage_description AS current_stage_description,
                    h.effective_from AS last_updated_date,
                    (SELECT submitted.effective_from
                     FROM application_stage_history AS submitted
                     WHERE submitted.application_id = a.id
                       AND submitted.stage = 'Submitted'
                     ORDER BY submitted.stage_sequence
                     LIMIT 1) AS submitted_date
                FROM applications AS a
                JOIN application_stage_history AS h
                  ON h.application_id = a.id AND h.is_current = 1
                WHERE a.id = ?""",
                (application_id,),
            ).fetchone()
            if row is None:
                raise ApplicationNotFoundError("Application not found.")
            history = connection.execute(
                """SELECT * FROM application_stage_history
                WHERE application_id = ?
                ORDER BY stage_sequence DESC""",
                (application_id,),
            ).fetchall()
        application = _sanitize_stored_links(dict(row))
        application["history"] = [dict(item) for item in history]
        return application

    def promote_completed_generation(self, application_id: int) -> bool:
        """Record Ready for review when a completed draft is first observed.

        CV persistence and lifecycle history remain separate transaction
        owners.  The web boundary reconciles the lifecycle idempotently when
        it observes the durable completed generation.
        """
        with connect(self.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                """SELECT id, stage, stage_sequence, effective_from
                FROM application_stage_history
                WHERE application_id = ? AND is_current = 1""",
                (application_id,),
            ).fetchone()
            generation = connection.execute(
                """SELECT completed_at FROM cv_generations
                WHERE application_id = ?
                ORDER BY completed_at DESC, id DESC LIMIT 1""",
                (application_id,),
            ).fetchone()
            if current is None or generation is None:
                return False
            if current["stage"] not in {"Assessing", "Mismatch"}:
                return False
            completed_at = canonical_timestamp(
                str(generation["completed_at"]),
                "CV generation completion time",
            )
            current_from = canonical_timestamp(
                str(current["effective_from"]), "Current stage effective time"
            )
            if completed_at < current_from:
                return False
            connection.execute(
                """UPDATE application_stage_history
                SET effective_from = ?, effective_to = ?, is_current = 0
                WHERE id = ?""",
                (current_from, completed_at, current["id"]),
            )
            connection.execute(
                """INSERT INTO application_stage_history (
                    application_id, stage, stage_sequence, stage_description,
                    effective_from, is_current
                ) VALUES (?, 'Ready for review', ?, ?, ?, 1)""",
                (
                    application_id,
                    int(current["stage_sequence"]) + 1,
                    "Editable DOCX draft; human review required.",
                    completed_at,
                ),
            )
        return True

    def update_application(
        self, application_id: int, values: Mapping[str, Any]
    ) -> None:
        """Update editable application fields without touching its history."""
        role = _required(values.get("role"), "Role")
        company = _required(values.get("company"), "Company")
        with connect(self.database_path) as connection:
            cursor = connection.execute(
                """UPDATE applications SET role = ?, company = ?, payment = ?,
                    job_url = ?, is_recruiter = ?, is_fully_remote = ?,
                    notes = ? WHERE id = ?""",
                (
                    role,
                    company,
                    _optional(values.get("payment")),
                    _job_url(values.get("job_url")),
                    _flag(values.get("is_recruiter")),
                    _flag(values.get("is_fully_remote")),
                    _optional(values.get("notes")),
                    application_id,
                ),
            )
            if cursor.rowcount == 0:
                raise ApplicationNotFoundError("Application not found.")

    def update_notes(self, application_id: int, notes: str | None) -> None:
        """Replace an application's notes without changing other fields."""
        with connect(self.database_path) as connection:
            cursor = connection.execute(
                "UPDATE applications SET notes = ? WHERE id = ?",
                (_optional(notes), application_id),
            )
            if cursor.rowcount == 0:
                raise ApplicationNotFoundError("Application not found.")

    def add_stage(
        self,
        application_id: int,
        stage: str,
        effective_from: str,
        description: str | None = None,
    ) -> None:
        """Close the current stage and append a new current stage atomically."""
        if not stage:
            raise ValueError("Choose a new stage.")
        if stage not in STAGES:
            raise ValueError("Choose a valid stage.")
        if not effective_from:
            raise ValueError("Effective-from date is required.")
        with connect(self.database_path) as connection:
            current = connection.execute(
                """SELECT id, effective_from, stage_sequence
                FROM application_stage_history
                WHERE application_id = ? AND is_current = 1""",
                (application_id,),
            ).fetchone()
            if current is None:
                exists = connection.execute(
                    "SELECT 1 FROM applications WHERE id = ?", (application_id,)
                ).fetchone()
                if exists is None:
                    raise ApplicationNotFoundError("Application not found.")
                raise ValueError("Application has no current stage.")
            if effective_from < current["effective_from"]:
                raise ValueError(
                    "Effective-from date cannot precede the current stage."
                )
            connection.execute(
                """UPDATE application_stage_history
                SET effective_to = ?, is_current = 0 WHERE id = ?""",
                (effective_from, current["id"]),
            )
            connection.execute(
                """INSERT INTO application_stage_history (
                    application_id, stage, stage_sequence, stage_description,
                    effective_from, is_current
                ) VALUES (?, ?, ?, ?, ?, 1)""",
                (
                    application_id,
                    stage,
                    int(current["stage_sequence"]) + 1,
                    _optional(description),
                    effective_from,
                ),
            )

    def update_current_stage(
        self,
        application_id: int,
        stage: str,
        description: str | None,
        effective_from: str,
    ) -> None:
        """Update a current note or append a stage transition."""
        if stage not in STAGES:
            raise ValueError("Choose a valid stage.")
        with connect(self.database_path) as connection:
            current = connection.execute(
                """SELECT id, stage FROM application_stage_history
                WHERE application_id = ? AND is_current = 1""",
                (application_id,),
            ).fetchone()
            if current is None:
                exists = connection.execute(
                    "SELECT 1 FROM applications WHERE id = ?", (application_id,)
                ).fetchone()
                if exists is None:
                    raise ApplicationNotFoundError("Application not found.")
                raise ValueError("Application has no current stage.")
            if current["stage"] == stage:
                connection.execute(
                    """UPDATE application_stage_history
                    SET stage_description = ? WHERE id = ?""",
                    (_optional(description), current["id"]),
                )
                return
        self.add_stage(application_id, stage, effective_from, description)
