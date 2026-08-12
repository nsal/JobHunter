"""Deterministic assessment scoring and execution."""

from typing import TYPE_CHECKING

from app.assessment.scoring import (
    AssessmentOutcome,
    AssessmentScore,
    score_assessment,
)

if TYPE_CHECKING:
    from app.assessment.service import AssessmentService

__all__ = [
    "AssessmentOutcome",
    "AssessmentScore",
    "AssessmentService",
    "score_assessment",
]


def __getattr__(name: str) -> object:
    """Load the service export without creating an import cycle."""
    if name == "AssessmentService":
        from app.assessment.service import AssessmentService

        return AssessmentService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
