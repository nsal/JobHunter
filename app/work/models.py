"""Typed values used by the durable work repository."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

MAX_CHECKPOINT_MODEL_LENGTH = 100
MAX_CHECKPOINT_PROVIDER_LENGTH = 100
MAX_CHECKPOINT_RESPONSE_IDS = 32
MAX_CHECKPOINT_RESPONSE_ID_LENGTH = 200
_CHECKPOINT_TEXT_PATTERN = re.compile(r"[^\x00-\x1f\x7f]+")

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
class WorkCheckpointProvenance:
    """Allowlisted provider metadata retained with a work checkpoint."""

    model: str
    provider: str
    response_ids: tuple[str, ...]
    input_tokens: int
    output_tokens: int
    total_tokens: int
    repair_attempted: bool

    def __post_init__(self) -> None:
        """Reject metadata that cannot be used in a completion record."""
        for label, value, limit in (
            ("model", self.model, MAX_CHECKPOINT_MODEL_LENGTH),
            ("provider", self.provider, MAX_CHECKPOINT_PROVIDER_LENGTH),
        ):
            if (
                not isinstance(value, str)
                or not value
                or value != value.strip()
                or len(value) > limit
                or _CHECKPOINT_TEXT_PATTERN.fullmatch(value) is None
            ):
                raise ValueError(f"{label} must be bounded trimmed text.")
        if not isinstance(self.response_ids, tuple):
            raise TypeError("response IDs must be a tuple.")
        if len(self.response_ids) > MAX_CHECKPOINT_RESPONSE_IDS:
            raise ValueError("Too many response IDs.")
        for response_id in self.response_ids:
            if (
                not isinstance(response_id, str)
                or not response_id
                or response_id != response_id.strip()
                or len(response_id) > MAX_CHECKPOINT_RESPONSE_ID_LENGTH
                or _CHECKPOINT_TEXT_PATTERN.fullmatch(response_id) is None
            ):
                raise ValueError("Response IDs must be bounded text.")
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in (
                self.input_tokens,
                self.output_tokens,
                self.total_tokens,
            )
        ):
            raise ValueError("Token counts must be non-negative integers.")
        if not isinstance(self.repair_attempted, bool):
            raise TypeError("Repair metadata must be boolean.")

    def to_json(self) -> str:
        """Serialize only the durable provenance allowlist."""
        return json.dumps(
            {
                "model": self.model,
                "provider": self.provider,
                "response_ids": list(self.response_ids),
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "total_tokens": self.total_tokens,
                "repair_attempted": self.repair_attempted,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, value: object) -> WorkCheckpointProvenance | None:
        """Parse stored provenance, returning no trust for bad data."""
        if not isinstance(value, str):
            return None
        try:
            document = json.loads(value)
        except TypeError, ValueError:
            return None
        if not isinstance(document, dict):
            return None
        expected = {
            "model",
            "provider",
            "response_ids",
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "repair_attempted",
        }
        if set(document) != expected:
            return None
        response_ids = document["response_ids"]
        if not isinstance(response_ids, list) or not all(
            isinstance(response_id, str) for response_id in response_ids
        ):
            return None
        try:
            return cls(
                model=document["model"],
                provider=document["provider"],
                response_ids=tuple(response_ids),
                input_tokens=document["input_tokens"],
                output_tokens=document["output_tokens"],
                total_tokens=document["total_tokens"],
                repair_attempted=document["repair_attempted"],
            )
        except TypeError, ValueError:
            return None


@dataclass(frozen=True)
class WorkCheckpoint:
    """Durable checkpoint metadata supplied to a worker attempt."""

    step: str
    path: str
    sha256: str
    hashes: dict[str, str | None]
    provenance: WorkCheckpointProvenance | None = None


@dataclass(frozen=True)
class WorkItem:
    """A safe, allowlisted view of one work item."""

    sequence: int
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
    assessment_result_sha256: str | None
    role_sha256: str | None
    prompt_sha256: str | None
    schema_sha256: str | None
    template_sha256: str | None
    layout_sha256: str | None
    checkpoint_path: str | None
    checkpoint_sha256: str | None
    checkpoint_provenance: WorkCheckpointProvenance | None
    error_code: str | None
    error_message: str | None
    finalizer_token: str | None = None
    failure_token: str | None = None
