"""Transactional persistence and recovery for background work."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from app.ai.providers.base import StructuredGenerationError
from app.consent import has_openai_profile_sharing_consent
from app.database import connect
from app.work.models import (
    WorkCheckpointProvenance,
    WorkItem,
    WorkState,
    WorkType,
    canonical_timestamp,
    latest_timestamp,
    timestamp_precedes,
)


class WorkNotFoundError(ValueError):
    """Raised when a work item does not exist."""


class StaleWorkerError(RuntimeError):
    """Raised when a worker token no longer owns work."""


class WorkStateError(RuntimeError):
    """Raised when an operation is not valid for the current state."""


TRANSIENT_CODES = frozenset({"timeout", "rate_limit", "connection", "server"})
SAFE_CODES = TRANSIENT_CODES | frozenset(
    {
        "authentication",
        "invalid_request",
        "invalid_output",
        "configuration",
        "deterministic",
    }
)
SAFE_MESSAGES = {
    "unknown_error": "Work failed.",
    "process_timeout": "Worker process timed out.",
}


def classify_failure(error: BaseException) -> tuple[str, bool, str]:
    """Return a redacted code, retry decision, and bounded safe message."""
    if isinstance(error, TimeoutError):
        return "process_timeout", True, SAFE_MESSAGES["process_timeout"]
    if not isinstance(error, StructuredGenerationError):
        return "unknown_error", False, SAFE_MESSAGES["unknown_error"]
    raw_code = getattr(error, "code", None)
    code = str(raw_code) if raw_code is not None else ""
    if code not in SAFE_CODES:
        return "unknown_error", False, SAFE_MESSAGES["unknown_error"]
    retryable = code in TRANSIENT_CODES and bool(
        getattr(error, "retryable", False)
    )
    raw_message = getattr(error, "safe_message", None)
    message = raw_message if isinstance(raw_message, str) else "Work failed."
    return code, retryable, message[:500]


def _now(value: str | None = None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    canonical = canonical_timestamp(value)
    return datetime.fromisoformat(canonical)


def _row_to_item(row: sqlite3.Row) -> WorkItem:
    values = dict(row)
    values["work_type"] = WorkType(str(values["work_type"]))
    values["state"] = WorkState(str(values["state"]))
    values["checkpoint_provenance"] = WorkCheckpointProvenance.from_json(
        values.get("checkpoint_provenance")
    )
    return WorkItem(**values)


class WorkRepository:
    """Own all state transitions for durable work items."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = database_path

    def enqueue(
        self,
        application_id: int,
        work_type: WorkType,
        queued_at: str,
        *,
        assessment_id: str | None = None,
        profile_sha256: str | None = None,
        jd_sha256: str | None = None,
        assessment_result_sha256: str | None = None,
        prompt_sha256: str | None = None,
        schema_sha256: str | None = None,
        template_sha256: str | None = None,
        layout_sha256: str | None = None,
        require_profile_consent: bool = False,
    ) -> str:
        """Queue work, rejecting a second active item transactionally."""
        queued_at = canonical_timestamp(queued_at, "queued time")
        if work_type is WorkType.ASSESSMENT and assessment_id is not None:
            raise ValueError("Assessment work cannot reference an assessment.")
        if work_type is WorkType.CV_GENERATION:
            if assessment_id is None or not assessment_id.strip():
                raise ValueError("CV generation work requires an assessment.")
            assessment_id = assessment_id.strip()
        work_id = uuid4().hex
        with connect(self.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            if (
                require_profile_consent
                and not has_openai_profile_sharing_consent(connection)
            ):
                raise WorkStateError(
                    "Remote profile transmission is not acknowledged."
                )
            current_stage = connection.execute(
                """SELECT stage FROM application_stage_history
                WHERE application_id = ? AND is_current = 1""",
                (application_id,),
            ).fetchone()
            if current_stage is None:
                raise WorkStateError("Application was not found.")
            if work_type is WorkType.CV_GENERATION:
                assessment = connection.execute(
                    """SELECT application_id, outcome, profile_sha256,
                    jd_sha256
                    FROM assessments WHERE id = ?""",
                    (assessment_id,),
                ).fetchone()
                if (
                    assessment is None
                    or int(assessment["application_id"]) != application_id
                ):
                    raise WorkStateError("CV assessment was not found.")
                expected_stage = (
                    "Assessing"
                    if assessment["outcome"] == "matched"
                    else "Mismatch"
                )
                if current_stage["stage"] != expected_stage:
                    raise WorkStateError(
                        "Application is no longer eligible for CV generation."
                    )
                completed = connection.execute(
                    """SELECT 1 FROM cv_generations
                    WHERE application_id = ? AND assessment_id = ?""",
                    (application_id, assessment_id),
                ).fetchone()
                if completed is not None:
                    raise WorkStateError(
                        "CV generation has already been completed."
                    )
                expected_profile = str(assessment["profile_sha256"])
                expected_jd = str(assessment["jd_sha256"])
                if (
                    profile_sha256 is not None
                    and profile_sha256 != expected_profile
                ) or (jd_sha256 is not None and jd_sha256 != expected_jd):
                    raise WorkStateError("CV work hashes do not match.")
                profile_sha256 = expected_profile
                jd_sha256 = expected_jd
            elif current_stage["stage"] != "Assessing":
                raise WorkStateError(
                    "Application is no longer eligible for assessment."
                )
            try:
                connection.execute(
                    """INSERT INTO work_items (
                        id, application_id, work_type, state, available_at,
                        current_step, queued_at, assessment_id,
                        profile_sha256, jd_sha256, assessment_result_sha256,
                        prompt_sha256, schema_sha256,
                        template_sha256, layout_sha256
                    ) VALUES (?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        work_id,
                        application_id,
                        work_type.value,
                        queued_at,
                        "assessment"
                        if work_type is WorkType.ASSESSMENT
                        else "generation",
                        queued_at,
                        assessment_id,
                        profile_sha256,
                        jd_sha256,
                        assessment_result_sha256,
                        prompt_sha256,
                        schema_sha256,
                        template_sha256,
                        layout_sha256,
                    ),
                )
            except sqlite3.IntegrityError as error:
                if "one_active_work" in str(error) or "UNIQUE" in str(error):
                    raise WorkStateError(
                        "Application already has active work."
                    ) from error
                raise
        return work_id

    def get(self, work_id: str) -> WorkItem:
        with connect(self.database_path) as connection:
            row = connection.execute(
                "SELECT * FROM work_items WHERE id = ?", (work_id,)
            ).fetchone()
        if row is None:
            raise WorkNotFoundError("Work item not found.")
        return _row_to_item(row)

    def list_for_application(self, application_id: int) -> list[WorkItem]:
        """Return all work for an application, newest work first."""
        with connect(self.database_path) as connection:
            rows = connection.execute(
                """SELECT * FROM work_items
                WHERE application_id = ?
                ORDER BY queued_at DESC, sequence DESC""",
                (application_id,),
            ).fetchall()
        return [_row_to_item(row) for row in rows]

    def list_available(self, now: str, limit: int) -> list[WorkItem]:
        """Return queued work that can be claimed now.

        The dispatcher uses this read only to decide how many claim attempts
        to make.  Claiming remains transactional in :meth:`claim`, so a
        stale view cannot grant ownership to two workers.
        """
        if limit <= 0:
            raise ValueError("Available work limit must be positive.")
        now = canonical_timestamp(now, "availability time")
        with connect(self.database_path) as connection:
            rows = connection.execute(
                """SELECT * FROM work_items
                WHERE state = 'queued' AND available_at <= ?
                ORDER BY available_at, queued_at, sequence
                LIMIT ?""",
                (now, limit),
            ).fetchall()
        return [_row_to_item(row) for row in rows]

    def claim(
        self,
        work_id: str,
        worker_token: str,
        now: str,
        lease_seconds: float = 60,
    ) -> WorkItem:
        """Claim queued work using an immediate transaction."""
        if not worker_token.strip():
            raise ValueError("Worker token is required.")
        if lease_seconds <= 0:
            raise ValueError("Lease duration must be positive.")
        now = canonical_timestamp(now, "claim time")
        expires = canonical_timestamp(
            (_now(now) + timedelta(seconds=lease_seconds)).isoformat(),
            "lease expiry",
        )
        with connect(self.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """UPDATE work_items SET state = 'running', attempt_count =
                    attempt_count + 1, worker_token = ?, started_at = ?,
                    heartbeat_at = ?, lease_expires_at = ?, failure_token = NULL
                    WHERE id = ? AND state = 'queued' AND available_at <= ?""",
                (worker_token, now, now, expires, work_id, now),
            )
            if cursor.rowcount != 1:
                raise WorkStateError("Work item is not available to claim.")
        return self.get(work_id)

    def _owned(
        self, connection: sqlite3.Connection, work_id: str, token: str
    ) -> sqlite3.Row:
        if not token.strip():
            raise ValueError("Worker token is required.")
        row = connection.execute(
            "SELECT * FROM work_items WHERE id = ?", (work_id,)
        ).fetchone()
        if row is None:
            raise WorkNotFoundError("Work item not found.")
        if row["worker_token"] != token:
            raise StaleWorkerError("Worker token is no longer current.")
        return row

    def heartbeat(
        self,
        work_id: str,
        worker_token: str,
        now: str,
        lease_seconds: float = 60,
    ) -> None:
        """Extend a running worker lease."""
        if lease_seconds <= 0:
            raise ValueError("Lease duration must be positive.")
        now = canonical_timestamp(now, "heartbeat time")
        expires = canonical_timestamp(
            (_now(now) + timedelta(seconds=lease_seconds)).isoformat(),
            "lease expiry",
        )
        with connect(self.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._owned(connection, work_id, worker_token)
            if row["state"] != WorkState.RUNNING:
                raise WorkStateError("Only running work can heartbeat.")
            latest_activity = latest_timestamp(
                row["started_at"], row["heartbeat_at"]
            )
            if latest_activity is not None and timestamp_precedes(
                now, latest_activity
            ):
                raise ValueError(
                    "Heartbeat time cannot precede current work activity."
                )
            cursor = connection.execute(
                """UPDATE work_items SET heartbeat_at = ?, lease_expires_at = ?
                WHERE id = ? AND state = 'running' AND worker_token = ?""",
                (now, expires, work_id, worker_token),
            )
            if cursor.rowcount != 1:
                raise StaleWorkerError("Worker token is no longer current.")

    def checkpoint(
        self,
        work_id: str,
        worker_token: str,
        step: str,
        path: str,
        sha256: str,
        *,
        hashes: dict[str, str | None] | None = None,
        provenance: WorkCheckpointProvenance | None = None,
    ) -> None:
        """Record an already atomically-written and validated checkpoint."""
        if len(sha256) != 64 or not step.strip() or not path.strip():
            raise ValueError("Invalid checkpoint metadata.")
        hashes = hashes or {}
        allowed = {
            key: hashes.get(key)
            for key in (
                "profile_sha256",
                "jd_sha256",
                "assessment_result_sha256",
                "role_sha256",
                "prompt_sha256",
                "schema_sha256",
                "template_sha256",
                "layout_sha256",
            )
        }
        with connect(self.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._owned(connection, work_id, worker_token)
            if row["state"] != WorkState.RUNNING:
                raise WorkStateError("Only running work can checkpoint.")
            cursor = connection.execute(
                """UPDATE work_items SET current_step = ?, checkpoint_path = ?,
                checkpoint_sha256 = ?, checkpoint_provenance = ?,
                profile_sha256 = COALESCE(?, profile_sha256),
                jd_sha256 = COALESCE(?, jd_sha256),
                assessment_result_sha256 = COALESCE(?, assessment_result_sha256),
                role_sha256 = COALESCE(?, role_sha256),
                prompt_sha256 = COALESCE(?, prompt_sha256),
                schema_sha256 = COALESCE(?, schema_sha256), template_sha256 = COALESCE(?, template_sha256),
                layout_sha256 = COALESCE(?, layout_sha256)
                WHERE id = ? AND state = 'running' AND worker_token = ?""",
                (
                    step,
                    path,
                    sha256,
                    provenance.to_json() if provenance is not None else None,
                    *allowed.values(),
                    work_id,
                    worker_token,
                ),
            )
            if cursor.rowcount != 1:
                raise StaleWorkerError("Worker token is no longer current.")

    def fail(
        self, work_id: str, worker_token: str, error: BaseException, now: str
    ) -> WorkItem:
        """Fail work or schedule its single transient retry."""
        code, retryable, message = classify_failure(error)
        now = canonical_timestamp(now, "failure time")
        with connect(self.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            state = connection.execute(
                "SELECT state FROM work_items WHERE id = ?", (work_id,)
            ).fetchone()
            if state is None:
                raise WorkNotFoundError("Work item not found.")
            if state["state"] in (WorkState.QUEUED, WorkState.FAILED):
                row = connection.execute(
                    "SELECT * FROM work_items WHERE id = ?", (work_id,)
                ).fetchone()
                if row["failure_token"] != worker_token:
                    raise StaleWorkerError("Worker token is no longer current.")
                if row["error_code"] != code or row["error_message"] != message:
                    raise WorkStateError(
                        "Failure replay does not match stored failure."
                    )
                return _row_to_item(row)
            row = self._owned(connection, work_id, worker_token)
            if row["state"] != WorkState.RUNNING:
                raise WorkStateError("Only running work can fail.")
            latest_activity = latest_timestamp(
                row["started_at"], row["heartbeat_at"]
            )
            if latest_activity is not None and timestamp_precedes(
                now, latest_activity
            ):
                raise ValueError(
                    "Failure time cannot precede current work activity."
                )
            retry = retryable and int(row["attempt_count"]) < 2
            if retry:
                available = canonical_timestamp(
                    (_now(now) + timedelta(seconds=1)).isoformat(),
                    "retry availability",
                )
                connection.execute(
                    """UPDATE work_items SET state = 'queued', available_at = ?,
                    worker_token = NULL, lease_expires_at = NULL, error_code = ?,
                    error_message = ?, failure_token = ? WHERE id = ?""",
                    (available, code, message, worker_token, work_id),
                )
            else:
                connection.execute(
                    """UPDATE work_items SET state = 'failed', error_code = ?,
                    error_message = ?, worker_token = NULL, finalizer_token = NULL,
                    failure_token = ?, completed_at = ?, lease_expires_at = NULL
                    WHERE id = ?""",
                    (code, message, worker_token, now, work_id),
                )
            result = connection.execute(
                "SELECT * FROM work_items WHERE id = ?", (work_id,)
            ).fetchone()
        if result is None:
            raise WorkNotFoundError("Work item not found.")
        return _row_to_item(result)

    def checkpoint_matches(
        self, work_id: str, hashes: dict[str, str | None]
    ) -> bool:
        """Return whether a checkpoint still matches every supplied input."""
        item = self.get(work_id)
        if not item.checkpoint_path or not item.checkpoint_sha256:
            return False
        return all(
            getattr(item, key) == value
            for key, value in hashes.items()
            if value is not None
        )

    def recover_stale(self, now: str) -> int:
        """Requeue one retryable lease or terminally fail exhausted work."""
        now = canonical_timestamp(now, "recovery time")
        with connect(self.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """UPDATE work_items SET state = 'queued', worker_token = NULL,
                lease_expires_at = NULL, available_at = ?, error_code =
                'stale_worker', error_message = 'Worker lease expired.',
                failure_token = NULL
                WHERE state = 'running' AND attempt_count < 2
                AND lease_expires_at <= ?""",
                (now, now),
            )
            requeued = cursor.rowcount
            cursor = connection.execute(
                """UPDATE work_items SET state = 'failed', worker_token = NULL,
                lease_expires_at = NULL, error_code = 'stale_worker',
                error_message = 'Worker lease expired after retry.',
                completed_at = ?, failure_token = NULL
                WHERE state = 'running' AND attempt_count >= 2
                AND lease_expires_at <= ?""",
                (now, now),
            )
        return requeued + cursor.rowcount

    def active_for_application(
        self,
        application_id: int,
        work_type: WorkType,
        *,
        assessment_id: str | None = None,
    ) -> WorkItem | None:
        """Return the active work item for compatibility worker wiring."""
        with connect(self.database_path) as connection:
            row = connection.execute(
                """SELECT * FROM work_items
                WHERE application_id = ? AND work_type = ?
                AND state IN ('queued', 'running')
                AND (? IS NULL OR assessment_id = ?)""",
                (application_id, work_type.value, assessment_id, assessment_id),
            ).fetchone()
        return None if row is None else _row_to_item(row)
