"""Typed values used by the durable work repository."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

WORK_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S.%f+00:00"


def canonical_timestamp(value: str, label: str = "timestamp") -> str:
    """Return one UTC representation for an ISO-8601 timestamp."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} is required.")
    try:
        parsed = datetime.fromisoformat(value.strip())
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        else:
            parsed = parsed.astimezone(UTC)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(
            f"{label} must be a valid ISO-8601 timestamp."
        ) from error
    return parsed.strftime(WORK_TIMESTAMP_FORMAT)


def latest_timestamp(*values: str | None) -> str | None:
    """Return the latest non-null timestamp in canonical UTC form."""
    normalized = [
        canonical_timestamp(value) for value in values if value is not None
    ]
    return max(normalized) if normalized else None


def timestamp_precedes(value: str, reference: str) -> bool:
    """Return whether one timestamp precedes another chronologically."""
    return canonical_timestamp(value) < canonical_timestamp(reference)


class WorkState(StrEnum):
    """Durable work lifecycle states."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class WorkType(StrEnum):
    """Supported background work types."""

    ASSESSMENT = "assessment"
    CV_GENERATION = "cv_generation"


@dataclass(frozen=True)
class WorkItem:
    """A safe, allowlisted view of one work item."""

    id: str
    application_id: int
    work_type: WorkType
    state: WorkState
    attempt_count: int
    available_at: str
    current_step: str
    worker_token: str | None
    queued_at: str
    started_at: str | None
    heartbeat_at: str | None
    lease_expires_at: str | None
    completed_at: str | None
    assessment_id: str | None
    profile_sha256: str | None
    jd_sha256: str | None
    prompt_sha256: str | None
    schema_sha256: str | None
    template_sha256: str | None
    layout_sha256: str | None
    checkpoint_path: str | None
    checkpoint_sha256: str | None
    error_code: str | None
    error_message: str | None
    finalizer_token: str | None = None
    failure_token: str | None = None
