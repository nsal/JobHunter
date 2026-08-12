"""Versioned, evidence-grounded structured output contracts for AI calls."""

from __future__ import annotations

from collections.abc import Collection, Iterable
from enum import StrEnum
from typing import Annotated, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.ai.source_blocks import SourceBlock, SourceKind

SHORT_TEXT_MAX = 200
CLAIM_TEXT_MAX = 600
RATIONALE_TEXT_MAX = 1_000
ANALYSIS_TEXT_MAX = 2_000
MAX_REQUIREMENTS = 100
MAX_CV_SECTIONS = 12
MAX_CLAIMS_PER_SECTION = 30

RequirementId = Annotated[str, Field(pattern=r"^req-[0-9]{3}$")]
ClaimId = Annotated[str, Field(pattern=r"^claim-[0-9]{3}$")]
SectionId = Annotated[str, Field(pattern=r"^section-[0-9]{2}$")]
JdBlockId = Annotated[str, Field(pattern=r"^jd-[0-9]{4}$")]
ProfileBlockId = Annotated[str, Field(pattern=r"^profile-[0-9]{4}$")]


class ContractModel(BaseModel):
    """Immutable contract base that rejects provider-invented fields."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _validate_text(value: str) -> str:
    """Reject padded text and control characters unsafe for prompts/docs."""
    if value != value.strip():
        raise ValueError("text must not have surrounding whitespace")
    if any(
        ord(character) < 32 and character not in "\n\t" for character in value
    ):
        raise ValueError("text must not contain control characters")
    if "\x7f" in value:
        raise ValueError("text must not contain control characters")
    return value


def _ensure_unique(values: Iterable[str], label: str) -> None:
    items = tuple(values)
    if len(items) != len(set(items)):
        raise ValueError(f"duplicate {label}")


class SchemaVersion(StrEnum):
    """Published structured-output schema versions."""

    V1 = "v1"


class RequirementMatch(StrEnum):
    """Evidence-level match classifications returned by the model."""

    MATCHED = "matched"
    PARTIAL = "partial"
    GAP = "gap"
    UNKNOWN = "unknown"


class SupportingCategory(StrEnum):
    """Stable v1 dimensions later consumed by deterministic scoring."""

    CORE_SKILLS = "core_skills"
    RELEVANT_EXPERIENCE = "relevant_experience"
    DOMAIN_KNOWLEDGE = "domain_knowledge"
    RESPONSIBILITIES = "responsibilities"
    QUALIFICATIONS = "qualifications"


class CategoryAlignment(StrEnum):
    """Bounded qualitative input to deterministic category scoring."""

    STRONG = "strong"
    MATCHED = "matched"
    PARTIAL = "partial"
    GAP = "gap"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


class HardGateKind(StrEnum):
    """Explicit contradictions that can deterministically block a match."""

    SALARY_LOCATION = "salary_location"
    REMOTE_POLICY = "remote_policy"
    WORK_AUTHORIZATION = "work_authorization"
    SECURITY_CLEARANCE = "security_clearance"
    EXCLUDED_BUSINESS = "excluded_business"


class HardGateStatus(StrEnum):
    """Model-extracted fact relation; only conflict is a failed gate."""

    NO_CONFLICT = "no_conflict"
    CONFLICT = "conflict"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


class CitedRequirement(ContractModel):
    """One bounded job requirement cited to source JD blocks."""

    requirement_id: RequirementId
    text: str = Field(min_length=1, max_length=CLAIM_TEXT_MAX)
    mandatory: bool
    jd_block_ids: tuple[JdBlockId, ...] = Field(min_length=1, max_length=8)

    _text_is_safe = field_validator("text")(_validate_text)

    @model_validator(mode="after")
    def block_references_are_unique(self) -> Self:
        """Reject repeated citations that inflate an output."""
        _ensure_unique(self.jd_block_ids, "JD block reference")
        return self


class EvidenceMatch(ContractModel):
    """Profile evidence and classification for exactly one requirement."""

    requirement_id: RequirementId
    classification: RequirementMatch
    profile_block_ids: tuple[ProfileBlockId, ...] = Field(max_length=12)
    rationale: str = Field(min_length=1, max_length=RATIONALE_TEXT_MAX)

    _rationale_is_safe = field_validator("rationale")(_validate_text)

    @model_validator(mode="after")
    def citations_match_classification(self) -> Self:
        """Matched evidence must actually cite at least one profile block."""
        _ensure_unique(self.profile_block_ids, "profile block reference")
        if (
            self.classification
            in {RequirementMatch.MATCHED, RequirementMatch.PARTIAL}
            and not self.profile_block_ids
        ):
            raise ValueError("matched evidence must cite a profile block")
        return self


class SupportingCategoryAssessment(ContractModel):
    """Qualitative evidence for one fixed supporting-score category."""

    category: SupportingCategory
    alignment: CategoryAlignment
    requirement_ids: tuple[RequirementId, ...] = Field(
        max_length=MAX_REQUIREMENTS
    )
    profile_block_ids: tuple[ProfileBlockId, ...] = Field(max_length=24)
    rationale: str = Field(min_length=1, max_length=RATIONALE_TEXT_MAX)

    _rationale_is_safe = field_validator("rationale")(_validate_text)

    @model_validator(mode="after")
    def references_are_unique(self) -> Self:
        """Require grounded positive alignment and reject duplicates."""
        _ensure_unique(self.requirement_ids, "category requirement reference")
        _ensure_unique(self.profile_block_ids, "profile block reference")
        if self.alignment in {
            CategoryAlignment.STRONG,
            CategoryAlignment.MATCHED,
            CategoryAlignment.PARTIAL,
        } and (not self.requirement_ids or not self.profile_block_ids):
            raise ValueError(
                "positive category alignment must cite requirements and "
                "profile blocks"
            )
        return self


class HardGateAssessment(ContractModel):
    """Cited status for one supported hard-gate dimension."""

    gate: HardGateKind
    status: HardGateStatus
    requirement_ids: tuple[RequirementId, ...] = Field(
        max_length=MAX_REQUIREMENTS
    )
    jd_block_ids: tuple[JdBlockId, ...] = Field(max_length=12)
    profile_block_ids: tuple[ProfileBlockId, ...] = Field(max_length=12)
    rationale: str = Field(min_length=1, max_length=RATIONALE_TEXT_MAX)

    _rationale_is_safe = field_validator("rationale")(_validate_text)

    @model_validator(mode="after")
    def references_are_consistent(self) -> Self:
        """Require citations for a claimed conflict and reject duplicates."""
        _ensure_unique(self.requirement_ids, "gate requirement reference")
        _ensure_unique(self.jd_block_ids, "JD block reference")
        _ensure_unique(self.profile_block_ids, "profile block reference")
        if self.status is HardGateStatus.CONFLICT and (
            not self.jd_block_ids or not self.profile_block_ids
        ):
            raise ValueError(
                "a hard-gate conflict must cite JD and profile blocks"
            )
        return self


class AssessmentResult(ContractModel):
    """Complete provider output before deterministic validation and scoring."""

    schema_version: SchemaVersion
    requirements: tuple[CitedRequirement, ...] = Field(
        min_length=1, max_length=MAX_REQUIREMENTS
    )
    evidence: tuple[EvidenceMatch, ...] = Field(
        min_length=1, max_length=MAX_REQUIREMENTS
    )
    supporting_categories: tuple[SupportingCategoryAssessment, ...] = Field(
        min_length=len(SupportingCategory),
        max_length=len(SupportingCategory),
    )
    hard_gates: tuple[HardGateAssessment, ...] = Field(
        min_length=len(HardGateKind), max_length=len(HardGateKind)
    )
    analysis: str = Field(min_length=1, max_length=ANALYSIS_TEXT_MAX)

    _analysis_is_safe = field_validator("analysis")(_validate_text)

    @model_validator(mode="after")
    def cross_records_are_consistent(self) -> Self:
        """Resolve internal requirement references and enforce uniqueness."""
        requirement_ids = tuple(
            requirement.requirement_id for requirement in self.requirements
        )
        _ensure_unique(requirement_ids, "requirement ID")
        evidence_ids = tuple(item.requirement_id for item in self.evidence)
        _ensure_unique(evidence_ids, "evidence requirement reference")
        if set(evidence_ids) != set(requirement_ids):
            raise ValueError(
                "evidence must cover every requirement exactly once"
            )

        category_values = tuple(
            item.category for item in self.supporting_categories
        )
        _ensure_unique(category_values, "supporting category")
        if set(category_values) != set(SupportingCategory):
            raise ValueError(
                "assessment must include every supporting category"
            )
        gate_values = tuple(item.gate for item in self.hard_gates)
        _ensure_unique(gate_values, "hard gate")
        if set(gate_values) != set(HardGateKind):
            raise ValueError("assessment must include every hard gate")

        known = set(requirement_ids)
        category_references = (
            reference
            for item in self.supporting_categories
            for reference in item.requirement_ids
        )
        gate_references = (
            reference
            for item in self.hard_gates
            for reference in item.requirement_ids
        )
        if (
            dangling := (set(category_references) | set(gate_references))
            - known
        ):
            raise ValueError(
                "dangling requirement reference: " + ", ".join(sorted(dangling))
            )
        return self


class CvSectionKind(StrEnum):
    """Allowed one-page CV section types."""

    SUMMARY = "summary"
    SKILLS = "skills"
    EXPERIENCE = "experience"
    EDUCATION = "education"
    CERTIFICATIONS = "certifications"
    PROJECTS = "projects"
    ADDITIONAL = "additional"


class CitedValue(ContractModel):
    """An identity/contact value grounded in the private profile."""

    text: str = Field(min_length=1, max_length=SHORT_TEXT_MAX)
    profile_block_ids: tuple[ProfileBlockId, ...] = Field(
        min_length=1, max_length=4
    )

    _text_is_safe = field_validator("text")(_validate_text)

    @model_validator(mode="after")
    def references_are_unique(self) -> Self:
        """Reject duplicate identity citations."""
        _ensure_unique(self.profile_block_ids, "profile block reference")
        return self


class CvIdentity(ContractModel):
    """Bounded cited identity and optional contact details."""

    full_name: CitedValue
    professional_title: CitedValue | None
    email: CitedValue | None
    phone: CitedValue | None
    location: CitedValue | None
    website: CitedValue | None


class CvClaim(ContractModel):
    """One renderable CV claim with profile and relevance citations."""

    claim_id: ClaimId
    text: str = Field(min_length=1, max_length=CLAIM_TEXT_MAX)
    profile_block_ids: tuple[ProfileBlockId, ...] = Field(
        min_length=1, max_length=12
    )
    requirement_ids: tuple[RequirementId, ...] = Field(max_length=12)

    _text_is_safe = field_validator("text")(_validate_text)

    @model_validator(mode="after")
    def references_are_unique(self) -> Self:
        """Reject duplicate evidence or relevance references."""
        _ensure_unique(self.profile_block_ids, "profile block reference")
        _ensure_unique(self.requirement_ids, "claim requirement reference")
        return self


class CvSection(ContractModel):
    """One ordered, typed CV section."""

    section_id: SectionId
    kind: CvSectionKind
    heading: str = Field(min_length=1, max_length=SHORT_TEXT_MAX)
    claims: tuple[CvClaim, ...] = Field(
        min_length=1, max_length=MAX_CLAIMS_PER_SECTION
    )

    _heading_is_safe = field_validator("heading")(_validate_text)

    @model_validator(mode="after")
    def claim_ids_are_unique(self) -> Self:
        """Reject duplicate claim IDs within a section."""
        _ensure_unique((claim.claim_id for claim in self.claims), "CV claim ID")
        return self


class CvContent(ContractModel):
    """Complete evidence-grounded content for deterministic DOCX rendering."""

    schema_version: SchemaVersion
    identity: CvIdentity
    sections: tuple[CvSection, ...] = Field(
        min_length=1, max_length=MAX_CV_SECTIONS
    )

    @model_validator(mode="after")
    def sections_and_claims_are_unique(self) -> Self:
        """Enforce globally stable section kinds, IDs, and claim IDs."""
        _ensure_unique(
            (section.section_id for section in self.sections), "CV section ID"
        )
        _ensure_unique(
            (section.kind for section in self.sections), "CV section kind"
        )
        _ensure_unique(
            (
                claim.claim_id
                for section in self.sections
                for claim in section.claims
            ),
            "CV claim ID",
        )
        return self


def _block_index(
    blocks: Collection[SourceBlock], expected_kind: SourceKind
) -> dict[str, SourceBlock]:
    """Index validated blocks and reject mixed or duplicate documents."""
    if any(block.source_kind is not expected_kind for block in blocks):
        raise ValueError(f"expected only {expected_kind.value} source blocks")
    indexed = {block.block_id: block for block in blocks}
    if len(indexed) != len(blocks):
        raise ValueError("duplicate source block ID")
    return indexed


def validate_assessment_references(
    result: AssessmentResult,
    jd_blocks: Collection[SourceBlock],
    profile_blocks: Collection[SourceBlock],
) -> None:
    """Resolve every assessment citation against the supplied sources."""
    jd_ids = set(_block_index(jd_blocks, SourceKind.JOB_DESCRIPTION))
    profile_ids = set(_block_index(profile_blocks, SourceKind.PROFILE))
    cited_jd_ids = {
        block_id
        for requirement in result.requirements
        for block_id in requirement.jd_block_ids
    } | {
        block_id for gate in result.hard_gates for block_id in gate.jd_block_ids
    }
    cited_profile_ids = (
        {
            block_id
            for evidence in result.evidence
            for block_id in evidence.profile_block_ids
        }
        | {
            block_id
            for category in result.supporting_categories
            for block_id in category.profile_block_ids
        }
        | {
            block_id
            for gate in result.hard_gates
            for block_id in gate.profile_block_ids
        }
    )
    _raise_dangling(cited_jd_ids - jd_ids, "JD block")
    _raise_dangling(cited_profile_ids - profile_ids, "profile block")


def validate_cv_references(
    content: CvContent,
    profile_blocks: Collection[SourceBlock],
    requirement_ids: Collection[str],
) -> None:
    """Resolve every CV evidence and tailoring reference."""
    profile_ids = set(_block_index(profile_blocks, SourceKind.PROFILE))
    cited_profile_ids = {
        block_id
        for value in _identity_values(content.identity)
        for block_id in value.profile_block_ids
    } | {
        block_id
        for section in content.sections
        for claim in section.claims
        for block_id in claim.profile_block_ids
    }
    cited_requirement_ids = {
        requirement_id
        for section in content.sections
        for claim in section.claims
        for requirement_id in claim.requirement_ids
    }
    _raise_dangling(cited_profile_ids - profile_ids, "profile block")
    _raise_dangling(cited_requirement_ids - set(requirement_ids), "requirement")


def _identity_values(identity: CvIdentity) -> tuple[CitedValue, ...]:
    values = (
        identity.full_name,
        identity.professional_title,
        identity.email,
        identity.phone,
        identity.location,
        identity.website,
    )
    return tuple(value for value in values if value is not None)


def _raise_dangling(values: Collection[str], label: str) -> None:
    if values:
        raise ValueError(
            f"dangling {label} reference: " + ", ".join(sorted(values))
        )
