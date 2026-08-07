"""Synthetic evidence-grounded assessment results used across tests."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from app.ai.schema_models import (
    AssessmentResult,
    CategoryAlignment,
    HardGateKind,
    HardGateStatus,
    RequirementMatch,
    SupportingCategory,
)


def assessment_result(
    *,
    mandatory: tuple[bool, ...] = (True,),
    classifications: tuple[RequirementMatch, ...] = (RequirementMatch.MATCHED,),
    alignments: Mapping[SupportingCategory, CategoryAlignment] | None = None,
    gate_statuses: Mapping[HardGateKind, HardGateStatus] | None = None,
) -> AssessmentResult:
    """Build a complete strict result with valid local citations."""
    if len(mandatory) != len(classifications):
        raise ValueError("mandatory and classifications must have equal length")
    configured_alignments = {
        category: CategoryAlignment.STRONG for category in SupportingCategory
    }
    configured_alignments.update(alignments or {})
    configured_gates = {
        gate: HardGateStatus.NOT_APPLICABLE for gate in HardGateKind
    }
    configured_gates.update(gate_statuses or {})

    requirements: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    for index, (is_mandatory, classification) in enumerate(
        zip(mandatory, classifications, strict=True), start=1
    ):
        requirement_id = f"req-{index:03d}"
        requirements.append(
            {
                "requirement_id": requirement_id,
                "text": f"Requirement {index}",
                "mandatory": is_mandatory,
                "jd_block_ids": ["jd-0002"],
            }
        )
        evidence.append(
            {
                "requirement_id": requirement_id,
                "classification": classification.value,
                "profile_block_ids": (
                    ["profile-0002"]
                    if classification
                    in {RequirementMatch.MATCHED, RequirementMatch.PARTIAL}
                    else []
                ),
                "rationale": f"Evidence classification {index}.",
            }
        )

    categories = [
        {
            "category": category.value,
            "alignment": configured_alignments[category].value,
            "requirement_ids": (
                ["req-001"]
                if configured_alignments[category]
                in {
                    CategoryAlignment.STRONG,
                    CategoryAlignment.MATCHED,
                    CategoryAlignment.PARTIAL,
                }
                else []
            ),
            "profile_block_ids": (
                ["profile-0002"]
                if configured_alignments[category]
                in {
                    CategoryAlignment.STRONG,
                    CategoryAlignment.MATCHED,
                    CategoryAlignment.PARTIAL,
                }
                else []
            ),
            "rationale": f"Category {category.value} assessment.",
        }
        for category in SupportingCategory
    ]
    gates = [
        {
            "gate": gate.value,
            "status": configured_gates[gate].value,
            "requirement_ids": (
                ["req-001"]
                if configured_gates[gate] is HardGateStatus.CONFLICT
                else []
            ),
            "jd_block_ids": (
                ["jd-0002"]
                if configured_gates[gate] is HardGateStatus.CONFLICT
                else []
            ),
            "profile_block_ids": (
                ["profile-0002"]
                if configured_gates[gate] is HardGateStatus.CONFLICT
                else []
            ),
            "rationale": f"Gate {gate.value} assessment.",
        }
        for gate in HardGateKind
    ]
    return AssessmentResult.model_validate_json(
        json.dumps(
            {
                "schema_version": "v1",
                "requirements": requirements,
                "evidence": evidence,
                "supporting_categories": categories,
                "hard_gates": gates,
                "analysis": "Evidence-grounded overall analysis.",
            }
        )
    )
