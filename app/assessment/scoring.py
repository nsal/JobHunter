"""Pure deterministic scoring for evidence-grounded assessments."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from app.ai.schema_models import (
    AssessmentResult,
    HardGateKind,
    HardGateStatus,
    RequirementMatch,
    SupportingCategory,
)
from app.assessment.taxonomy import AssessmentTaxonomy

SCORE_QUANTUM = Decimal("0.01")


class AssessmentOutcome(StrEnum):
    """Stable lifecycle decisions, ordered by deterministic precedence."""

    MATCHED = "matched"
    SKILL_MISMATCH = "skill_mismatch"
    SALARY_LOCATION_MISMATCH = "salary_location_mismatch"
    OTHER_MISMATCH = "other_mismatch"


@dataclass(frozen=True)
class CategoryScore:
    """One category's fixed weight, alignment, and weighted contribution."""

    category: SupportingCategory
    weight: Decimal
    alignment_score: Decimal
    contribution: Decimal


@dataclass(frozen=True)
class AssessmentScore:
    """All deterministic values derived from one validated model result."""

    supporting_alignment: Decimal
    mandatory_coverage: Decimal
    final_score: Decimal
    threshold: Decimal
    meets_threshold: bool
    all_mandatory_matched: bool
    category_scores: tuple[CategoryScore, ...]
    mandatory_gap_ids: tuple[str, ...]
    failed_hard_gates: tuple[HardGateKind, ...]
    ambiguous_hard_gates: tuple[HardGateKind, ...]
    outcome: AssessmentOutcome

    @property
    def is_match(self) -> bool:
        """Return whether automatic CV generation is allowed."""
        return self.outcome is AssessmentOutcome.MATCHED

    def as_document(self, taxonomy_version: str) -> dict[str, object]:
        """Return a JSON-safe, stable representation for persistence."""
        return {
            "taxonomy_version": taxonomy_version,
            "supporting_alignment": float(self.supporting_alignment),
            "mandatory_coverage": float(self.mandatory_coverage),
            "final_score": float(self.final_score),
            "threshold": float(self.threshold),
            "meets_threshold": self.meets_threshold,
            "all_mandatory_matched": self.all_mandatory_matched,
            "category_scores": [
                {
                    "category": item.category.value,
                    "weight": float(item.weight),
                    "alignment_score": float(item.alignment_score),
                    "contribution": float(item.contribution),
                }
                for item in self.category_scores
            ],
            "mandatory_gap_ids": list(self.mandatory_gap_ids),
            "failed_hard_gates": [
                gate.value for gate in self.failed_hard_gates
            ],
            "ambiguous_hard_gates": [
                gate.value for gate in self.ambiguous_hard_gates
            ],
            "outcome": self.outcome.value,
        }


def _round(value: Decimal) -> Decimal:
    return value.quantize(SCORE_QUANTUM, rounding=ROUND_HALF_UP)


def _outcome(
    *,
    failed_gates: tuple[HardGateKind, ...],
    meets_threshold: bool,
    all_mandatory_matched: bool,
) -> AssessmentOutcome:
    """Apply stable precedence when multiple mismatch reasons coexist."""
    if HardGateKind.SALARY_LOCATION in failed_gates:
        return AssessmentOutcome.SALARY_LOCATION_MISMATCH
    if failed_gates:
        return AssessmentOutcome.OTHER_MISMATCH
    if not all_mandatory_matched or not meets_threshold:
        return AssessmentOutcome.SKILL_MISMATCH
    return AssessmentOutcome.MATCHED


def score_assessment(
    result: AssessmentResult,
    threshold: float | Decimal,
    taxonomy: AssessmentTaxonomy,
) -> AssessmentScore:
    """Calculate supporting alignment, mandatory coverage, and outcome."""
    threshold_value = Decimal(str(threshold))
    if not Decimal(0) <= threshold_value <= Decimal(100):
        raise ValueError("assessment threshold must be between 0 and 100")
    threshold_value = _round(threshold_value)

    categories = {item.category: item for item in result.supporting_categories}
    if set(categories) != set(SupportingCategory):
        raise ValueError("assessment must contain every supporting category")
    category_scores = tuple(
        CategoryScore(
            category=category,
            weight=taxonomy.category_weights[category],
            alignment_score=taxonomy.alignment_scores[
                categories[category].alignment
            ],
            contribution=_round(
                taxonomy.category_weights[category]
                * taxonomy.alignment_scores[categories[category].alignment]
                / Decimal(100)
            ),
        )
        for category in SupportingCategory
    )
    supporting_alignment = _round(
        sum(
            (item.contribution for item in category_scores),
            start=Decimal(0),
        )
    )

    evidence = {item.requirement_id: item for item in result.evidence}
    mandatory = tuple(item for item in result.requirements if item.mandatory)
    matched_mandatory = sum(
        evidence[item.requirement_id].classification is RequirementMatch.MATCHED
        for item in mandatory
    )
    mandatory_coverage = (
        Decimal(100)
        if not mandatory
        else _round(
            Decimal(matched_mandatory) * Decimal(100) / Decimal(len(mandatory))
        )
    )
    mandatory_gap_ids = tuple(
        item.requirement_id
        for item in mandatory
        if evidence[item.requirement_id].classification
        is not RequirementMatch.MATCHED
    )
    all_mandatory_matched = not mandatory_gap_ids
    final_score = _round(
        supporting_alignment * mandatory_coverage / Decimal(100)
    )
    meets_threshold = final_score >= threshold_value

    gate_statuses = {item.gate: item.status for item in result.hard_gates}
    failed_gates = tuple(
        gate
        for gate in HardGateKind
        if gate_statuses.get(gate) is HardGateStatus.CONFLICT
    )
    ambiguous_gates = tuple(
        gate
        for gate in HardGateKind
        if gate_statuses.get(gate) is HardGateStatus.UNKNOWN
    )
    outcome = _outcome(
        failed_gates=failed_gates,
        meets_threshold=meets_threshold,
        all_mandatory_matched=all_mandatory_matched,
    )
    return AssessmentScore(
        supporting_alignment=supporting_alignment,
        mandatory_coverage=mandatory_coverage,
        final_score=final_score,
        threshold=threshold_value,
        meets_threshold=meets_threshold,
        all_mandatory_matched=all_mandatory_matched,
        category_scores=category_scores,
        mandatory_gap_ids=mandatory_gap_ids,
        failed_hard_gates=failed_gates,
        ambiguous_hard_gates=ambiguous_gates,
        outcome=outcome,
    )
