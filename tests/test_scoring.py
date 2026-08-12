from __future__ import annotations

from decimal import Decimal

import pytest

from app.ai.schema_models import (
    CategoryAlignment,
    HardGateKind,
    HardGateStatus,
    RequirementMatch,
    SupportingCategory,
)
from app.assessment.scoring import AssessmentOutcome, score_assessment
from app.assessment.taxonomy import V1_TAXONOMY, AssessmentTaxonomy
from tests.fixtures.assessment_cases import assessment_result


def test_v1_taxonomy_is_complete_and_totals_one_hundred() -> None:
    assert set(V1_TAXONOMY.category_weights) == set(SupportingCategory)
    assert sum(V1_TAXONOMY.category_weights.values()) == Decimal(100)
    assert V1_TAXONOMY.sha256 == V1_TAXONOMY.sha256


def test_no_mandatory_requirements_has_full_coverage() -> None:
    score = score_assessment(
        assessment_result(mandatory=(False,)), 75, V1_TAXONOMY
    )

    assert score.mandatory_coverage == Decimal("100.00")
    assert score.all_mandatory_matched is True
    assert score.outcome is AssessmentOutcome.MATCHED


@pytest.mark.parametrize(
    ("classifications", "expected"),
    [
        (
            (RequirementMatch.MATCHED, RequirementMatch.MATCHED),
            Decimal("100.00"),
        ),
        (
            (RequirementMatch.MATCHED, RequirementMatch.PARTIAL),
            Decimal("50.00"),
        ),
        (
            (RequirementMatch.GAP, RequirementMatch.UNKNOWN),
            Decimal("0.00"),
        ),
    ],
)
def test_mandatory_coverage_counts_only_full_matches(
    classifications: tuple[RequirementMatch, ...], expected: Decimal
) -> None:
    score = score_assessment(
        assessment_result(
            mandatory=(True, True), classifications=classifications
        ),
        0,
        V1_TAXONOMY,
    )

    assert score.mandatory_coverage == expected


def test_category_arithmetic_and_threshold_boundary_are_inclusive() -> None:
    result = assessment_result(
        alignments={
            SupportingCategory.CORE_SKILLS: CategoryAlignment.MATCHED,
            SupportingCategory.RELEVANT_EXPERIENCE: CategoryAlignment.PARTIAL,
            SupportingCategory.DOMAIN_KNOWLEDGE: CategoryAlignment.GAP,
            SupportingCategory.RESPONSIBILITIES: CategoryAlignment.UNKNOWN,
            SupportingCategory.QUALIFICATIONS: (
                CategoryAlignment.NOT_APPLICABLE
            ),
        }
    )

    score = score_assessment(result, Decimal("46.50"), V1_TAXONOMY)

    assert score.supporting_alignment == Decimal("46.50")
    assert score.final_score == Decimal("46.50")
    assert score.meets_threshold is True
    assert score.outcome is AssessmentOutcome.MATCHED


def test_threshold_comparison_uses_persisted_precision() -> None:
    result = assessment_result(
        alignments={
            category: CategoryAlignment.MATCHED
            for category in SupportingCategory
        }
    )

    score = score_assessment(result, Decimal("80.004"), V1_TAXONOMY)

    assert score.final_score == Decimal("80.00")
    assert score.threshold == Decimal("80.00")
    assert score.meets_threshold is True
    assert score.outcome is AssessmentOutcome.MATCHED


def test_rounding_is_decimal_half_up_and_stable() -> None:
    thirds = AssessmentTaxonomy(
        version="v-test",
        category_weights={
            SupportingCategory.CORE_SKILLS: Decimal("33.33"),
            SupportingCategory.RELEVANT_EXPERIENCE: Decimal("33.33"),
            SupportingCategory.DOMAIN_KNOWLEDGE: Decimal("33.34"),
            SupportingCategory.RESPONSIBILITIES: Decimal(0),
            SupportingCategory.QUALIFICATIONS: Decimal(0),
        },
        alignment_scores=V1_TAXONOMY.alignment_scores,
    )
    score = score_assessment(
        assessment_result(
            mandatory=(True, True, True),
            classifications=(
                RequirementMatch.MATCHED,
                RequirementMatch.MATCHED,
                RequirementMatch.GAP,
            ),
        ),
        0,
        thirds,
    )

    assert score.mandatory_coverage == Decimal("66.67")
    assert score.final_score == Decimal("66.67")


def test_gate_and_outcome_precedence_is_stable() -> None:
    result = assessment_result(
        classifications=(RequirementMatch.GAP,),
        gate_statuses={
            HardGateKind.SALARY_LOCATION: HardGateStatus.CONFLICT,
            HardGateKind.WORK_AUTHORIZATION: HardGateStatus.CONFLICT,
        },
    )

    score = score_assessment(result, 100, V1_TAXONOMY)

    assert score.failed_hard_gates == (
        HardGateKind.SALARY_LOCATION,
        HardGateKind.WORK_AUTHORIZATION,
    )
    assert score.outcome is AssessmentOutcome.SALARY_LOCATION_MISMATCH


def test_non_salary_gate_precedes_skill_mismatch() -> None:
    score = score_assessment(
        assessment_result(
            classifications=(RequirementMatch.GAP,),
            gate_statuses={HardGateKind.REMOTE_POLICY: HardGateStatus.CONFLICT},
        ),
        100,
        V1_TAXONOMY,
    )

    assert score.outcome is AssessmentOutcome.OTHER_MISMATCH


def test_ambiguity_is_visible_but_does_not_fail_a_hard_gate() -> None:
    score = score_assessment(
        assessment_result(
            gate_statuses={
                HardGateKind.SECURITY_CLEARANCE: HardGateStatus.UNKNOWN
            }
        ),
        75,
        V1_TAXONOMY,
    )

    assert score.failed_hard_gates == ()
    assert score.ambiguous_hard_gates == (HardGateKind.SECURITY_CLEARANCE,)
    assert score.outcome is AssessmentOutcome.MATCHED


def test_low_supporting_score_is_a_skill_mismatch() -> None:
    score = score_assessment(
        assessment_result(
            alignments={
                category: CategoryAlignment.GAP
                for category in SupportingCategory
            }
        ),
        75,
        V1_TAXONOMY,
    )

    assert score.final_score == Decimal("0.00")
    assert score.outcome is AssessmentOutcome.SKILL_MISMATCH
