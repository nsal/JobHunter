from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.ai.schema_models import (
    ANALYSIS_TEXT_MAX,
    AssessmentResult,
    CategoryAlignment,
    CvContent,
    HardGateKind,
    SupportingCategory,
    validate_assessment_references,
    validate_cv_references,
)
from app.ai.source_blocks import SourceBlock, SourceKind, parse_source_blocks
from scripts import generate_ai_schemas

ROOT = Path(__file__).resolve().parent.parent


def assessment_values() -> dict[str, Any]:
    categories: list[dict[str, Any]] = []
    for category in SupportingCategory:
        categories.append(
            {
                "category": category.value,
                "alignment": (
                    "matched"
                    if category is SupportingCategory.CORE_SKILLS
                    else "not_applicable"
                ),
                "requirement_ids": (
                    ["req-001"]
                    if category is SupportingCategory.CORE_SKILLS
                    else []
                ),
                "profile_block_ids": (
                    ["profile-0002"]
                    if category is SupportingCategory.CORE_SKILLS
                    else []
                ),
                "rationale": "Cited profile evidence supports the category.",
            }
        )
    gates: list[dict[str, Any]] = [
        {
            "gate": gate.value,
            "status": "not_applicable",
            "requirement_ids": [],
            "jd_block_ids": [],
            "profile_block_ids": [],
            "rationale": "The job description does not activate this gate.",
        }
        for gate in HardGateKind
    ]
    return {
        "schema_version": "v1",
        "requirements": [
            {
                "requirement_id": "req-001",
                "text": "Build typed Python services.",
                "mandatory": True,
                "jd_block_ids": ["jd-0002"],
            }
        ],
        "evidence": [
            {
                "requirement_id": "req-001",
                "classification": "matched",
                "profile_block_ids": ["profile-0002"],
                "rationale": "The profile cites typed Python service work.",
            }
        ],
        "supporting_categories": categories,
        "hard_gates": gates,
        "analysis": "The cited evidence directly addresses the requirement.",
    }


def cv_values() -> dict[str, Any]:
    return {
        "schema_version": "v1",
        "identity": {
            "full_name": {
                "text": "Example Person",
                "profile_block_ids": ["profile-0001"],
            },
            "professional_title": {
                "text": "Python Engineer",
                "profile_block_ids": ["profile-0002"],
            },
            "email": None,
            "phone": None,
            "location": None,
            "website": None,
        },
        "sections": [
            {
                "section_id": "section-01",
                "kind": "summary",
                "heading": "Profile",
                "claims": [
                    {
                        "claim_id": "claim-001",
                        "text": "Builds typed Python services.",
                        "profile_block_ids": ["profile-0002"],
                        "requirement_ids": ["req-001"],
                    }
                ],
            }
        ],
    }


def source_documents() -> tuple[
    tuple[SourceBlock, ...], tuple[SourceBlock, ...]
]:
    profile = parse_source_blocks(
        "# Example Person\n\nPython Engineer building typed services.",
        SourceKind.PROFILE,
    )
    jd = parse_source_blocks(
        "# Role\n\nBuild typed Python services.",
        SourceKind.JOB_DESCRIPTION,
    )
    return profile, jd


def test_assessment_contract_round_trip_and_source_resolution() -> None:
    profile, jd = source_documents()
    result = AssessmentResult.model_validate_json(
        json.dumps(assessment_values())
    )

    validate_assessment_references(result, jd, profile)
    restored = AssessmentResult.model_validate_json(result.model_dump_json())

    assert restored == result
    assert restored.requirements[0].mandatory is True
    assert restored.evidence[0].profile_block_ids == ("profile-0002",)


def test_cv_contract_round_trip_and_source_resolution() -> None:
    profile, _ = source_documents()
    content = CvContent.model_validate_json(json.dumps(cv_values()))

    validate_cv_references(content, profile, {"req-001"})
    restored = CvContent.model_validate_json(content.model_dump_json())

    assert restored == content
    assert restored.identity.full_name.text == "Example Person"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda values: values.update({"invented": True}),
        lambda values: values.update({"schema_version": "v2"}),
        lambda values: values["evidence"][0].update(
            {"classification": "mostly"}
        ),
        lambda values: values.update(
            {"analysis": "x" * (ANALYSIS_TEXT_MAX + 1)}
        ),
        lambda values: values["requirements"].append(
            values["requirements"][0].copy()
        ),
        lambda values: values["supporting_categories"][0].update(
            {"requirement_ids": ["req-999"]}
        ),
    ],
)
def test_assessment_contract_rejects_invalid_provider_output(
    mutate: Any,
) -> None:
    values = assessment_values()
    mutate(values)

    with pytest.raises(ValidationError):
        AssessmentResult.model_validate_json(json.dumps(values))


def test_assessment_requires_complete_evidence_categories_and_gates() -> None:
    values = assessment_values()
    values["evidence"] = []
    with pytest.raises(ValidationError, match="at least 1 item"):
        AssessmentResult.model_validate_json(json.dumps(values))

    values = assessment_values()
    values["supporting_categories"].pop()
    with pytest.raises(ValidationError, match="at least 5 items"):
        AssessmentResult.model_validate_json(json.dumps(values))

    values = assessment_values()
    values["hard_gates"].pop()
    with pytest.raises(ValidationError, match="at least 5 items"):
        AssessmentResult.model_validate_json(json.dumps(values))


def test_matched_evidence_and_gate_conflicts_require_citations() -> None:
    values = assessment_values()
    values["evidence"][0]["profile_block_ids"] = []
    with pytest.raises(ValidationError, match="must cite a profile block"):
        AssessmentResult.model_validate_json(json.dumps(values))

    values = assessment_values()
    values["hard_gates"][0].update(
        {
            "status": "conflict",
            "jd_block_ids": ["jd-0002"],
            "profile_block_ids": [],
        }
    )
    with pytest.raises(ValidationError, match="must cite JD and profile"):
        AssessmentResult.model_validate_json(json.dumps(values))


@pytest.mark.parametrize("value", ["false", "true", 0, 1])
def test_assessment_rejects_coercible_mandatory_values(value: object) -> None:
    values = assessment_values()
    values["requirements"][0]["mandatory"] = value

    with pytest.raises(ValidationError):
        AssessmentResult.model_validate_json(json.dumps(values))


def test_assessment_accepts_json_native_boolean_and_enum_values() -> None:
    result = AssessmentResult.model_validate_json(
        json.dumps(assessment_values())
    )

    assert result.requirements[0].mandatory is True
    assert (
        result.supporting_categories[0].alignment is CategoryAlignment.MATCHED
    )


@pytest.mark.parametrize(
    "alignment",
    [
        CategoryAlignment.STRONG,
        CategoryAlignment.MATCHED,
        CategoryAlignment.PARTIAL,
    ],
)
@pytest.mark.parametrize(
    "citation_field", ["requirement_ids", "profile_block_ids"]
)
def test_positive_category_alignments_require_both_citation_types(
    alignment: CategoryAlignment, citation_field: str
) -> None:
    values = assessment_values()
    category = values["supporting_categories"][0]
    category["alignment"] = alignment.value
    category[citation_field] = []

    with pytest.raises(ValidationError, match="must cite requirements"):
        AssessmentResult.model_validate_json(json.dumps(values))


@pytest.mark.parametrize(
    "alignment",
    [
        CategoryAlignment.GAP,
        CategoryAlignment.UNKNOWN,
        CategoryAlignment.NOT_APPLICABLE,
    ],
)
def test_non_positive_category_alignments_allow_empty_citations(
    alignment: CategoryAlignment,
) -> None:
    values = assessment_values()
    category = values["supporting_categories"][0]
    category["alignment"] = alignment.value
    category["requirement_ids"] = []
    category["profile_block_ids"] = []

    result = AssessmentResult.model_validate_json(json.dumps(values))

    assert result.supporting_categories[0].alignment is alignment


def test_external_validation_rejects_dangling_or_mixed_block_references() -> (
    None
):
    profile, jd = source_documents()
    values = assessment_values()
    values["requirements"][0]["jd_block_ids"] = ["jd-0099"]
    result = AssessmentResult.model_validate_json(json.dumps(values))

    with pytest.raises(ValueError, match="dangling JD block.*jd-0099"):
        validate_assessment_references(result, jd, profile)
    with pytest.raises(ValueError, match="expected only job_description"):
        validate_assessment_references(result, profile, profile)


def test_cv_contract_rejects_duplicate_and_dangling_claim_references() -> None:
    profile, _ = source_documents()
    values = cv_values()
    duplicate = values["sections"][0].copy()
    duplicate["section_id"] = "section-02"
    values["sections"].append(duplicate)
    with pytest.raises(ValidationError, match="duplicate CV section kind"):
        CvContent.model_validate_json(json.dumps(values))

    values = cv_values()
    values["sections"][0]["claims"][0]["profile_block_ids"] = ["profile-0099"]
    content = CvContent.model_validate_json(json.dumps(values))
    with pytest.raises(ValueError, match="dangling profile block"):
        validate_cv_references(content, profile, {"req-001"})

    values = cv_values()
    values["sections"][0]["claims"][0]["requirement_ids"] = ["req-999"]
    content = CvContent.model_validate_json(json.dumps(values))
    with pytest.raises(ValueError, match="dangling requirement"):
        validate_cv_references(content, profile, {"req-001"})


def _object_schemas(value: object) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        if value.get("type") == "object":
            yield value
        for nested in value.values():
            yield from _object_schemas(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _object_schemas(nested)


@pytest.mark.parametrize(
    ("filename", "model"),
    [
        ("assessment-result.json", AssessmentResult),
        ("cv-content.json", CvContent),
    ],
)
def test_committed_json_schemas_are_strict_and_current(
    filename: str, model: type[AssessmentResult | CvContent]
) -> None:
    path = ROOT / "app" / "ai" / "schemas" / "v1" / filename
    committed = json.loads(path.read_text(encoding="utf-8"))

    assert committed == model.model_json_schema(mode="validation")
    for object_schema in _object_schemas(committed):
        assert object_schema["additionalProperties"] is False
        assert set(object_schema.get("properties", ())) == set(
            object_schema.get("required", ())
        )


def test_schema_check_mode_fails_on_missing_or_drifted_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    schema_directory = tmp_path / "schemas" / "v1"
    monkeypatch.setattr(
        generate_ai_schemas, "SCHEMA_DIRECTORY", schema_directory
    )
    monkeypatch.setattr(generate_ai_schemas, "ROOT", tmp_path)

    assert generate_ai_schemas.generate(check=True) is False
    assert generate_ai_schemas.generate(check=False) is True
    assert generate_ai_schemas.generate(check=True) is True

    drifted = schema_directory / "assessment-result.json"
    drifted.write_text("{}\n", encoding="utf-8")
    assert generate_ai_schemas.generate(check=True) is False
