from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, Thread
from types import TracebackType
from typing import Any, Self, cast

import pytest
import yaml
from docx import Document

from app.ai.providers.base import (
    ProviderMetadata,
    StructuredGenerationRequest,
    StructuredGenerationResult,
    StructuredGenerator,
    TokenUsage,
)
from app.ai.schema_models import AssessmentResult, CvContent, RequirementMatch
from app.ai.source_blocks import SourceKind, parse_source_blocks
from app.artefacts import ArtefactStore
from app.assessment.service import AssessmentService
from app.assessments import AssessmentRepository
from app.cv.generator import (
    CvGenerationService,
    CvGroundingError,
    CvInputChangedError,
    validate_cited_cv_content,
)
from app.cv_generations import (
    CompletedCvGeneration,
    CvGenerationNotFoundError,
    CvGenerationRepository,
    CvGenerationStateError,
)
from app.database import connect, initialize_database
from app.documents.word_writer import WordWriter
from app.repository import Repository
from app.settings import (
    SettingsError,
    load_ai_settings,
    validate_private_inputs,
)
from tests.fixtures.assessment_cases import assessment_result

PROFILE = """# Avery Morgan

Software Engineer with Python and FastAPI experience. Built typed services and
improved throughput by 25% in 2024. Contact: avery@example.test and
https://example.test/avery.
"""
MASTER_PROFILE_PATH = Path(__file__).parent / "fixtures" / "profile_master.md"


class FakeGenerator:
    """Return one typed value and retain the provider request."""

    def __init__(
        self,
        value: AssessmentResult | CvContent,
        effect: Callable[[], None] | None = None,
    ) -> None:
        self.value = value
        self.effect = effect
        self.requests: list[StructuredGenerationRequest[Any]] = []

    def generate(
        self, request: StructuredGenerationRequest[Any]
    ) -> StructuredGenerationResult[Any]:
        self.requests.append(request)
        if self.effect is not None:
            self.effect()
        return StructuredGenerationResult(
            value=self.value,
            model=request.model,
            schema_name=request.schema_name,
            response_ids=("resp_cv",),
            usage=TokenUsage(30, 20, 50),
            metadata=ProviderMetadata(
                provider="openai",
                statuses=("completed",),
                service_tiers=("default",),
                repair_attempted=False,
            ),
        )


def cv_content() -> CvContent:
    return CvContent.model_validate_json(
        json.dumps(
            {
                "schema_version": "v1",
                "identity": {
                    "full_name": {
                        "text": "Avery Morgan",
                        "profile_block_ids": ["profile-0001"],
                    },
                    "professional_title": {
                        "text": "Software Engineer",
                        "profile_block_ids": ["profile-0002"],
                    },
                    "email": {
                        "text": "avery@example.test",
                        "profile_block_ids": ["profile-0002"],
                    },
                    "phone": None,
                    "location": None,
                    "website": {
                        "text": "https://example.test/avery",
                        "profile_block_ids": ["profile-0002"],
                    },
                },
                "sections": [
                    {
                        "section_id": "section-01",
                        "kind": "summary",
                        "heading": "Profile",
                        "claims": [
                            {
                                "claim_id": "claim-001",
                                "text": (
                                    "Built typed services and improved throughput "
                                    "by 25% in 2024."
                                ),
                                "profile_block_ids": ["profile-0002"],
                                "requirement_ids": ["req-001"],
                            }
                        ],
                    },
                    {
                        "section_id": "section-02",
                        "kind": "skills",
                        "heading": "Skills",
                        "claims": [
                            {
                                "claim_id": "claim-002",
                                "text": "Python, FastAPI",
                                "profile_block_ids": ["profile-0002"],
                                "requirement_ids": ["req-001"],
                            }
                        ],
                    },
                ],
            }
        )
    )


def layout_values() -> dict[str, object]:
    return {
        "page": {
            "size": "A4",
            "orientation": "portrait",
            "margins": {
                "top": 0.5,
                "right": 0.55,
                "bottom": 0.5,
                "left": 0.55,
            },
        },
        "fonts": {
            "body": "Aptos",
            "headings": "Aptos Display",
            "body_size_pt": 9.5,
            "heading_size_pt": 12,
        },
        "spacing": {
            "line": 1.0,
            "paragraph_after_pt": 3,
            "section_after_pt": 6,
        },
        "styles": {
            "heading_color": "1F2937",
            "accent_color": "2563EB",
            "bullet_indent_inches": 0.2,
        },
        "output": {"filename": "Tailored CV.docx"},
    }


def prepare_private_inputs(tmp_path: Path, profile_text: str = PROFILE) -> Path:
    profile_root = tmp_path / "private" / "profile"
    profile_root.mkdir(parents=True)
    profile_path = profile_root / "profile.md"
    profile_path.write_text(profile_text, encoding="utf-8")
    document = Document()
    document.add_paragraph("Template placeholder")
    document.save(str(profile_root / "cv-template.docx"))
    (profile_root / "cv-layout.yaml").write_text(
        yaml.safe_dump(layout_values()), encoding="utf-8"
    )
    return profile_path


def create_application(database_path: str) -> int:
    return Repository(database_path).create_application(
        {
            "role": "Senior Python Engineer",
            "company": "Acme",
            "full_jd": "# Role\n\nRequirement 1",
        },
        "2026-08-07T09:00:00+00:00",
    )


def seed_assessment(
    database_path: str,
    artefacts: ArtefactStore,
    profile_path: Path,
    result: AssessmentResult | None = None,
    completed_at: str = "2026-08-07T10:00:00+00:00",
) -> str:
    service = AssessmentService(
        AssessmentRepository(database_path),
        artefacts,
        cast(StructuredGenerator, FakeGenerator(result or assessment_result())),
        load_ai_settings(),
        profile_path,
    )
    execution = service.execute(1, completed_at)
    return execution.assessment_id


def build_service(
    database_path: str,
    tmp_path: Path,
    value: CvContent,
    *,
    effect: Callable[[], None] | None = None,
    artefacts: ArtefactStore | None = None,
) -> tuple[CvGenerationService, FakeGenerator, ArtefactStore]:
    store = artefacts or ArtefactStore(tmp_path / "private" / "artefacts")
    fake = FakeGenerator(value, effect)
    service = CvGenerationService(
        CvGenerationRepository(database_path),
        store,
        cast(StructuredGenerator, fake),
        WordWriter(store, available_fonts={"Aptos", "Aptos Display"}),
        load_ai_settings(),
        validate_private_inputs(tmp_path),
    )
    return service, fake, store


def prepared_workflow(
    database_path: str, tmp_path: Path
) -> tuple[int, str, Path, ArtefactStore]:
    initialize_database(database_path)
    application_id = create_application(database_path)
    profile_path = prepare_private_inputs(tmp_path)
    artefacts = ArtefactStore(tmp_path / "private" / "artefacts")
    assessment_id = seed_assessment(database_path, artefacts, profile_path)
    return application_id, assessment_id, profile_path, artefacts


def completed_generation(
    application_id: int,
    assessment_id: str,
    generation_id: str = "generation-1",
) -> CompletedCvGeneration:
    return CompletedCvGeneration(
        generation_id=generation_id,
        application_id=application_id,
        assessment_id=assessment_id,
        model="gpt-test",
        model_sha256="a" * 64,
        schema_version="v1",
        schema_sha256="b" * 64,
        instruction_sha256="c" * 64,
        profile_sha256="d" * 64,
        jd_sha256="e" * 64,
        assessment_result_sha256="f" * 64,
        template_sha256="1" * 64,
        layout_sha256="2" * 64,
        provider="test",
        response_ids=("response-1",),
        input_tokens=10,
        output_tokens=20,
        total_tokens=30,
        repair_attempted=False,
        content_path="application/cv-generations/generation-1/content.json",
        content_sha256="3" * 64,
        candidate_path="application/cv-generations/generation-1/cv.docx",
        candidate_sha256="4" * 64,
        completed_at="2026-08-07T11:00:00+00:00",
    )


def test_generation_is_cited_role_targeted_rendered_and_persisted(
    database_path: str, tmp_path: Path
) -> None:
    application_id, assessment_id, _, artefacts = prepared_workflow(
        database_path, tmp_path
    )
    service, fake, _ = build_service(
        database_path, tmp_path, cv_content(), artefacts=artefacts
    )

    execution = service.execute(
        application_id,
        assessment_id,
        "2026-08-07T11:00:00+00:00",
    )

    assert execution.candidate.filename == (
        "Avery Morgan - Senior Python Engineer.docx"
    )
    assert artefacts.resolve(
        execution.candidate.relative_path, must_exist=True
    ).is_file()
    assert (
        artefacts.read_json(execution.content_path)["identity"]["full_name"][
            "text"
        ]
        == "Avery Morgan"
    )
    assert len(fake.requests) == 1
    request = fake.requests[0]
    assert request.schema is CvContent
    assert request.model == load_ai_settings().provider.cv_model
    assert request.contains_profile is True
    request_document = json.loads(request.input_text)
    assert request_document["target_role"] == "Senior Python Engineer"
    assert request_document["allowed_evidence"] == {"req-001": ["profile-0002"]}

    stored = CvGenerationRepository(database_path).get(execution.generation_id)
    assert stored["response_ids"] == ["resp_cv"]
    assert stored["total_tokens"] == 50
    assert stored["candidate_path"] == execution.candidate.relative_path
    assert stored["candidate_sha256"] == execution.candidate.sha256
    assert all(
        len(str(stored[field])) == 64
        for field in (
            "model_sha256",
            "schema_sha256",
            "instruction_sha256",
            "profile_sha256",
            "jd_sha256",
            "assessment_result_sha256",
            "template_sha256",
            "layout_sha256",
            "content_sha256",
            "candidate_sha256",
        )
    )
    assert (
        Repository(database_path).get_application(application_id)[
            "current_stage"
        ]
        == "Assessing"
    )


@pytest.mark.parametrize(
    ("field", "changed_text", "label"),
    [
        ("full_name", "avery morgan", "full name"),
        ("professional_title", "software Engineer", "professional title"),
        ("email", "AVERY@example.test", "email"),
        ("website", "https://example.test/avery/", "website"),
    ],
)
def test_cited_draft_requires_verbatim_identity_values_in_cited_blocks(
    field: str, changed_text: str, label: str
) -> None:
    profile_blocks = parse_source_blocks(PROFILE, SourceKind.PROFILE)
    valid = cv_content()
    value = getattr(valid.identity, field)
    assert value is not None
    changed = valid.model_copy(
        update={
            "identity": valid.identity.model_copy(
                update={field: value.model_copy(update={"text": changed_text})}
            )
        }
    )

    with pytest.raises(CvGroundingError, match=label):
        validate_cited_cv_content(changed, assessment_result(), profile_blocks)


def test_cited_draft_retains_arbitrary_narrative_claims() -> None:
    profile_blocks = parse_source_blocks(PROFILE, SourceKind.PROFILE)
    valid = cv_content()
    summary = valid.sections[0].model_copy(
        update={
            "claims": (
                valid.sections[0]
                .claims[0]
                .model_copy(
                    update={
                        "text": (
                            "A human reviewer must verify this freely "
                            "paraphrased narrative, including Kubernetes, "
                            "a Principal Engineer title, and a 99% metric."
                        )
                    }
                ),
            )
        }
    )
    changed = valid.model_copy(
        update={"sections": (summary, valid.sections[1])}
    )

    validate_cited_cv_content(changed, assessment_result(), profile_blocks)


def test_cited_draft_rejects_invalid_and_disallowed_citations() -> None:
    profile_blocks = parse_source_blocks(PROFILE, SourceKind.PROFILE)
    valid = cv_content()
    dangling_name = valid.identity.full_name.model_copy(
        update={"profile_block_ids": ("profile-9999",)}
    )
    dangling = valid.model_copy(
        update={
            "identity": valid.identity.model_copy(
                update={"full_name": dangling_name}
            )
        }
    )
    with pytest.raises(ValueError, match="dangling profile block"):
        validate_cited_cv_content(dangling, assessment_result(), profile_blocks)

    unsupported_reference = (
        valid.sections[0]
        .claims[0]
        .model_copy(update={"profile_block_ids": ("profile-0001",)})
    )
    changed_section = valid.sections[0].model_copy(
        update={"claims": (unsupported_reference,)}
    )
    changed = valid.model_copy(
        update={"sections": (changed_section, valid.sections[1])}
    )
    with pytest.raises(CvGroundingError, match="not allowed"):
        validate_cited_cv_content(changed, assessment_result(), profile_blocks)


@pytest.mark.parametrize(
    "restricted",
    (
        "confidential",
        "proprietary",
        "trade-secret",
        "under an NDA",
        "NDA-protected",
        "nondisclosure",
        "non-disclosure",
        "internal-only",
        "do-not-disclose",
    ),
)
def test_cited_draft_rejects_configured_restricted_wording(
    restricted: str,
) -> None:
    profile_blocks = parse_source_blocks(PROFILE, SourceKind.PROFILE)
    valid = cv_content()
    confidential_claim = (
        valid.sections[0]
        .claims[0]
        .model_copy(update={"text": f"Built {restricted} typed services."})
    )
    changed_section = valid.sections[0].model_copy(
        update={"claims": (confidential_claim,)}
    )
    changed = valid.model_copy(
        update={"sections": (changed_section, valid.sections[1])}
    )
    with pytest.raises(CvGroundingError, match="confidential"):
        validate_cited_cv_content(changed, assessment_result(), profile_blocks)


def master_cv_content() -> CvContent:
    """Return fixed draft content citing the public master-profile fixture."""
    return CvContent.model_validate_json(
        json.dumps(
            {
                "schema_version": "v1",
                "identity": {
                    "full_name": {
                        "text": "Noor Al-Khatib",
                        "profile_block_ids": ("profile-0001",),
                    },
                    "professional_title": {
                        "text": "Principal Platform Engineer",
                        "profile_block_ids": ("profile-0006",),
                    },
                    "email": {
                        "text": "noor.al-khatib@example.test",
                        "profile_block_ids": ("profile-0002",),
                    },
                    "phone": {
                        "text": "+44 20 7946 0958",
                        "profile_block_ids": ("profile-0002",),
                    },
                    "location": {
                        "text": "London, United Kingdom",
                        "profile_block_ids": ("profile-0002",),
                    },
                    "website": {
                        "text": "https://portfolio.example.test/noor-al-khatib",
                        "profile_block_ids": ("profile-0002",),
                    },
                },
                "sections": (
                    {
                        "section_id": "section-01",
                        "kind": "summary",
                        "heading": "Profile",
                        "claims": (
                            {
                                "claim_id": "claim-001",
                                "text": (
                                    "A platform leader connecting product, "
                                    "reliability, and developer experience."
                                ),
                                "profile_block_ids": ("profile-0004",),
                                "requirement_ids": (),
                            },
                        ),
                    },
                    {
                        "section_id": "section-02",
                        "kind": "experience",
                        "heading": "Experience",
                        "claims": (
                            {
                                "claim_id": "claim-002",
                                "text": (
                                    "Coordinated international teams while "
                                    "modernising observable delivery workflows."
                                ),
                                "profile_block_ids": ("profile-0007",),
                                "requirement_ids": (),
                            },
                            {
                                "claim_id": "claim-003",
                                "text": (
                                    "Can be contacted in London about the "
                                    "assessed role."
                                ),
                                "profile_block_ids": ("profile-0002",),
                                "requirement_ids": ("req-001",),
                            },
                        ),
                    },
                    {
                        "section_id": "section-03",
                        "kind": "projects",
                        "heading": "Projects",
                        "claims": (
                            {
                                "claim_id": "claim-004",
                                "text": (
                                    "Turned multidisciplinary feedback into a "
                                    "sequenced and challengeable delivery backlog."
                                ),
                                "profile_block_ids": ("profile-0015",),
                                "requirement_ids": (),
                            },
                        ),
                    },
                ),
            }
        )
    )


def test_master_markdown_completes_assessment_to_cited_draft_generation(
    database_path: str, tmp_path: Path
) -> None:
    master_profile = MASTER_PROFILE_PATH.read_text(encoding="utf-8")
    initialize_database(database_path)
    application_id = create_application(database_path)
    profile_path = prepare_private_inputs(tmp_path, master_profile)
    artefacts = ArtefactStore(tmp_path / "private" / "artefacts")
    assessment_id = seed_assessment(
        database_path, artefacts, profile_path, assessment_result()
    )
    service, fake, _ = build_service(
        database_path,
        tmp_path,
        master_cv_content(),
        artefacts=artefacts,
    )

    execution = service.execute(
        application_id,
        assessment_id,
        "2026-08-07T11:00:00+00:00",
    )

    assert execution.candidate.filename == (
        "Noor Al-Khatib - Senior Python Engineer.docx"
    )
    assert (
        execution.content.sections[0]
        .claims[0]
        .text.startswith("A platform leader")
    )
    request = fake.requests[0]
    assert "human factual and editorial review" in request.instructions
    request_document = json.loads(request.input_text)
    assert len(request_document["profile_blocks"]) == 23
    assert "Disclosure limits" in request.input_text


def test_changed_profile_is_rejected_before_and_after_generation(
    database_path: str, tmp_path: Path
) -> None:
    application_id, assessment_id, profile_path, artefacts = prepared_workflow(
        database_path, tmp_path
    )
    service, fake, _ = build_service(
        database_path, tmp_path, cv_content(), artefacts=artefacts
    )
    profile_path.write_text("# Changed profile", encoding="utf-8")
    with pytest.raises(CvInputChangedError):
        service.execute(
            application_id,
            assessment_id,
            "2026-08-07T11:00:00+00:00",
        )
    assert fake.requests == []

    profile_path.write_text(PROFILE, encoding="utf-8")

    def change_profile() -> None:
        profile_path.write_text("# Changed after request", encoding="utf-8")

    service, _, _ = build_service(
        database_path,
        tmp_path,
        cv_content(),
        effect=change_profile,
        artefacts=artefacts,
    )
    with pytest.raises(CvInputChangedError):
        service.execute(
            application_id,
            assessment_id,
            "2026-08-07T11:00:00+00:00",
        )
    assert list(artefacts.root.rglob("cv-content.json")) == []
    assert list(artefacts.root.rglob("*.docx")) == []


def test_mismatch_requires_explicit_override(
    database_path: str, tmp_path: Path
) -> None:
    initialize_database(database_path)
    application_id = create_application(database_path)
    profile_path = prepare_private_inputs(tmp_path)
    artefacts = ArtefactStore(tmp_path / "private" / "artefacts")
    mismatch = assessment_result(classifications=(RequirementMatch.GAP,))
    assessment_id = seed_assessment(
        database_path, artefacts, profile_path, mismatch
    )
    content = cv_content()
    sections = tuple(
        section.model_copy(
            update={
                "claims": tuple(
                    claim.model_copy(update={"requirement_ids": ()})
                    for claim in section.claims
                )
            }
        )
        for section in content.sections
    )
    content = content.model_copy(update={"sections": sections})
    service, _, _ = build_service(
        database_path, tmp_path, content, artefacts=artefacts
    )

    with pytest.raises(CvGenerationStateError):
        service.execute(
            application_id,
            assessment_id,
            "2026-08-07T11:00:00+00:00",
        )

    execution = service.execute(
        application_id,
        assessment_id,
        "2026-08-07T11:00:00+00:00",
        allow_mismatch=True,
    )
    assert execution.candidate.filename.endswith("Senior Python Engineer.docx")
    assert (
        Repository(database_path).get_application(application_id)[
            "current_stage"
        ]
        == "Mismatch"
    )
    assert (
        len(
            CvGenerationRepository(database_path).list_for_application(
                application_id
            )
        )
        == 1
    )


def test_completed_generation_rejects_missing_and_ineligible_inputs(
    database_path: str,
    tmp_path: Path,
) -> None:
    initialize_database(database_path)
    repository = CvGenerationRepository(database_path)
    with pytest.raises(CvGenerationNotFoundError):
        repository.add_completed(completed_generation(999, "missing"))

    application_id = create_application(database_path)
    profile_path = prepare_private_inputs(tmp_path)
    artefacts = ArtefactStore(tmp_path / "private" / "artefacts")
    assessment_id = seed_assessment(
        database_path, artefacts, profile_path, assessment_result()
    )
    Repository(database_path).add_stage(
        application_id,
        "Ready for review",
        "2026-08-07T11:00:00+00:00",
    )

    with pytest.raises(CvGenerationStateError):
        repository.add_completed(
            completed_generation(application_id, assessment_id)
        )


@pytest.mark.parametrize(
    "completed_at",
    (
        "2026-08-07T11:00:00",
        "2026-08-07T13:00:00+02:00",
    ),
)
def test_completed_generation_normalizes_valid_timestamp_formats(
    database_path: str,
    tmp_path: Path,
    completed_at: str,
) -> None:
    application_id, assessment_id, _, _ = prepared_workflow(
        database_path, tmp_path
    )
    generation = replace(
        completed_generation(application_id, assessment_id),
        completed_at=completed_at,
    )

    CvGenerationRepository(database_path).add_completed(generation)

    stored = CvGenerationRepository(database_path).get(generation.generation_id)
    assert stored["completed_at"] == completed_at
    normalized = datetime.fromisoformat(completed_at)
    if normalized.tzinfo is None:
        normalized = normalized.replace(tzinfo=UTC)
    else:
        normalized = normalized.astimezone(UTC)
    assert normalized >= datetime(2026, 8, 7, 10, tzinfo=UTC)


def test_completed_generations_list_by_utc_instant_then_id(
    database_path: str, tmp_path: Path
) -> None:
    application_id, first_assessment_id, profile_path, artefacts = (
        prepared_workflow(database_path, tmp_path)
    )
    assessment_ids = [first_assessment_id]
    for _ in range(4):
        assessment_ids.append(
            seed_assessment(database_path, artefacts, profile_path)
        )
    generations = (
        replace(
            completed_generation(
                application_id, assessment_ids[0], "generation-offset"
            ),
            completed_at="2026-08-07T12:30:00+02:00",
        ),
        replace(
            completed_generation(
                application_id, assessment_ids[1], "generation-utc"
            ),
            completed_at="2026-08-07T11:00:00+00:00",
        ),
        replace(
            completed_generation(
                application_id, assessment_ids[2], "generation-equal-a"
            ),
            completed_at="2026-08-07T13:00:00+02:00",
        ),
        replace(
            completed_generation(
                application_id, assessment_ids[3], "generation-equal-z"
            ),
            completed_at="2026-08-07T11:00:00+00:00",
        ),
        replace(
            completed_generation(
                application_id, assessment_ids[4], "generation-naive"
            ),
            completed_at="2026-08-07T11:30:00",
        ),
    )
    repository = CvGenerationRepository(database_path)
    for generation in generations:
        repository.add_completed(generation)

    listed = repository.list_for_application(application_id)

    assert [row["id"] for row in listed] == [
        "generation-naive",
        "generation-utc",
        "generation-equal-z",
        "generation-equal-a",
        "generation-offset",
    ]
    assert [row["completed_at"] for row in listed] == [
        "2026-08-07T11:30:00",
        "2026-08-07T11:00:00+00:00",
        "2026-08-07T11:00:00+00:00",
        "2026-08-07T13:00:00+02:00",
        "2026-08-07T12:30:00+02:00",
    ]


def test_completed_generations_list_preserves_python_timestamp_precision(
    database_path: str, tmp_path: Path
) -> None:
    application_id, first_assessment_id, profile_path, artefacts = (
        prepared_workflow(database_path, tmp_path)
    )
    assessment_ids = [first_assessment_id]
    for _ in range(2):
        assessment_ids.append(
            seed_assessment(database_path, artefacts, profile_path)
        )
    generations = (
        replace(
            completed_generation(
                application_id, assessment_ids[0], "generation-basic"
            ),
            completed_at="20260807T120000+0000",
        ),
        replace(
            completed_generation(
                application_id, assessment_ids[1], "generation-micro-early"
            ),
            completed_at="2026-08-07T11:00:00.000001+00:00",
        ),
        replace(
            completed_generation(
                application_id, assessment_ids[2], "generation-micro-late"
            ),
            completed_at="2026-08-07T11:00:00.000002+00:00",
        ),
    )
    repository = CvGenerationRepository(database_path)
    for generation in generations:
        repository.add_completed(generation)

    listed = repository.list_for_application(application_id)

    assert [row["id"] for row in listed] == [
        "generation-basic",
        "generation-micro-late",
        "generation-micro-early",
    ]


@pytest.mark.parametrize(
    ("completed_at", "message"),
    (
        ("", "is required"),
        ("not-a-timestamp", "valid ISO-8601 timestamp"),
        ("2026-08-08", "valid ISO-8601 timestamp"),
        (
            "0001-01-01T00:00:00+23:59",
            "valid ISO-8601 timestamp",
        ),
    ),
)
def test_completed_generation_rejects_malformed_timestamps(
    database_path: str,
    tmp_path: Path,
    completed_at: str,
    message: str,
) -> None:
    application_id, assessment_id, _, _ = prepared_workflow(
        database_path, tmp_path
    )
    generation = replace(
        completed_generation(application_id, assessment_id),
        completed_at=completed_at,
    )

    with pytest.raises(ValueError, match=message):
        CvGenerationRepository(database_path).add_completed(generation)

    assert (
        CvGenerationRepository(database_path).list_for_application(
            application_id
        )
        == []
    )


def test_completed_generation_rejects_assessment_backdating(
    database_path: str, tmp_path: Path
) -> None:
    initialize_database(database_path)
    application_id = create_application(database_path)
    profile_path = prepare_private_inputs(tmp_path)
    artefacts = ArtefactStore(tmp_path / "private" / "artefacts")
    assessment_id = seed_assessment(
        database_path,
        artefacts,
        profile_path,
        completed_at="2026-08-07T11:00:00+00:00",
    )
    generation = replace(
        completed_generation(application_id, assessment_id),
        completed_at="2026-08-07T10:30:00+00:00",
    )

    with pytest.raises(ValueError, match="assessment completion"):
        CvGenerationRepository(database_path).add_completed(generation)


def test_completed_generation_rejects_stage_backdating(
    database_path: str, tmp_path: Path
) -> None:
    application_id, assessment_id, _, _ = prepared_workflow(
        database_path, tmp_path
    )
    with connect(database_path) as connection:
        connection.execute(
            """UPDATE application_stage_history
            SET effective_from = ?
            WHERE application_id = ? AND is_current = 1""",
            ("2026-08-07T12:00:00+00:00", application_id),
        )
    generation = replace(
        completed_generation(application_id, assessment_id),
        completed_at="2026-08-07T11:00:00+00:00",
    )

    with pytest.raises(ValueError, match="current stage"):
        CvGenerationRepository(database_path).add_completed(generation)


def test_generation_timestamp_failure_cleans_artefacts_and_row(
    database_path: str, tmp_path: Path
) -> None:
    application_id, assessment_id, _, artefacts = prepared_workflow(
        database_path, tmp_path
    )
    service, _, _ = build_service(
        database_path, tmp_path, cv_content(), artefacts=artefacts
    )

    with pytest.raises(ValueError, match="assessment completion"):
        service.execute(
            application_id,
            assessment_id,
            "2026-08-07T09:30:00+00:00",
        )

    assert list(artefacts.root.rglob("cv-content.json")) == []
    assert list(artefacts.root.rglob("*.docx")) == []
    with connect(database_path) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM cv_generations"
        ).fetchone()[0]
    assert count == 0


def test_completed_generation_serializes_racing_stage_transition(
    database_path: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application_id, assessment_id, _, _ = prepared_workflow(
        database_path, tmp_path
    )
    eligibility_read = Event()
    continue_generation = Event()
    stage_write_attempted = Event()
    stage_write_finished = Event()
    errors: list[Exception] = []

    class PausingConnection:
        def __init__(self, path: str | Path) -> None:
            self._connection = sqlite3.connect(path, timeout=10)
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA foreign_keys = ON")

        def __enter__(self) -> Self:
            self._connection.__enter__()
            return self

        def __exit__(
            self,
            exception_type: type[BaseException] | None,
            exception: BaseException | None,
            traceback: TracebackType | None,
        ) -> bool | None:
            return self._connection.__exit__(
                exception_type, exception, traceback
            )

        def execute(
            self,
            query: str,
            parameters: tuple[object, ...] = (),
        ) -> sqlite3.Cursor:
            cursor = self._connection.execute(query, parameters)
            if "SELECT history.stage, assessment.outcome" in query:
                eligibility_read.set()
                if not continue_generation.wait(timeout=10):
                    raise TimeoutError("generation test barrier timed out")
            return cursor

    monkeypatch.setattr(
        "app.cv_generations.connect",
        lambda path: PausingConnection(path),
    )

    def persist_generation() -> None:
        try:
            CvGenerationRepository(database_path).add_completed(
                completed_generation(application_id, assessment_id)
            )
        except Exception as error:  # noqa: BLE001 - propagate thread failure
            errors.append(error)

    def change_stage() -> None:
        try:
            with connect(database_path) as connection:
                current = connection.execute(
                    """SELECT id, stage_sequence
                    FROM application_stage_history
                    WHERE application_id = ? AND is_current = 1""",
                    (application_id,),
                ).fetchone()
                assert current is not None

                def trace(statement: str) -> None:
                    if statement.startswith("UPDATE application_stage_history"):
                        stage_write_attempted.set()

                connection.set_trace_callback(trace)
                connection.execute(
                    """UPDATE application_stage_history
                    SET effective_to = ?, is_current = 0 WHERE id = ?""",
                    ("2026-08-07T12:00:00+00:00", current["id"]),
                )
                connection.execute(
                    """INSERT INTO application_stage_history (
                        application_id, stage, stage_sequence,
                        effective_from, is_current
                    ) VALUES (?, 'Ready for review', ?, ?, 1)""",
                    (
                        application_id,
                        int(current["stage_sequence"]) + 1,
                        "2026-08-07T12:00:00+00:00",
                    ),
                )
        except Exception as error:  # noqa: BLE001 - propagate thread failure
            errors.append(error)
        finally:
            stage_write_finished.set()

    persistence_thread = Thread(target=persist_generation)
    stage_thread = Thread(target=change_stage)
    persistence_thread.start()
    try:
        assert eligibility_read.wait(timeout=5)
        stage_thread.start()
        assert stage_write_attempted.wait(timeout=5)
        assert not stage_write_finished.wait(timeout=0.2)
    finally:
        continue_generation.set()
    persistence_thread.join(timeout=10)
    stage_thread.join(timeout=10)

    assert not persistence_thread.is_alive()
    assert not stage_thread.is_alive()
    assert errors == []
    assert (
        Repository(database_path).get_application(application_id)[
            "current_stage"
        ]
        == "Ready for review"
    )
    assert (
        len(
            CvGenerationRepository(database_path).list_for_application(
                application_id
            )
        )
        == 1
    )


def test_candidate_write_failure_removes_partial_generation(
    database_path: str, tmp_path: Path
) -> None:
    application_id, assessment_id, _, seed_store = prepared_workflow(
        database_path, tmp_path
    )
    replacement_calls = 0

    def fail_candidate(source: Path, target: Path) -> None:
        nonlocal replacement_calls
        replacement_calls += 1
        if replacement_calls == 2:
            raise OSError("simulated candidate replacement failure")
        source.replace(target)

    store = ArtefactStore(seed_store.root, replace=fail_candidate)
    service, _, _ = build_service(
        database_path, tmp_path, cv_content(), artefacts=store
    )

    with pytest.raises(OSError, match="simulated candidate"):
        service.execute(
            application_id,
            assessment_id,
            "2026-08-07T11:00:00+00:00",
        )

    assert list(store.root.rglob("cv-content.json")) == []
    assert list(store.root.rglob("*.docx")) == []
    with connect(database_path) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM cv_generations"
        ).fetchone()[0]
    assert count == 0


def test_completed_generation_is_immutable(
    database_path: str, tmp_path: Path
) -> None:
    application_id, assessment_id, _, artefacts = prepared_workflow(
        database_path, tmp_path
    )
    service, _, _ = build_service(
        database_path, tmp_path, cv_content(), artefacts=artefacts
    )
    execution = service.execute(
        application_id,
        assessment_id,
        "2026-08-07T11:00:00+00:00",
    )

    with connect(database_path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE cv_generations SET model = 'changed' WHERE id = ?",
                (execution.generation_id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "DELETE FROM cv_generations WHERE id = ?",
                (execution.generation_id,),
            )


def test_private_layout_rejects_malformed_yaml_and_conflicting_order(
    tmp_path: Path,
) -> None:
    prepare_private_inputs(tmp_path)
    layout_path = tmp_path / "private" / "profile" / "cv-layout.yaml"
    layout_path.write_text("page: [unterminated", encoding="utf-8")
    with pytest.raises(SettingsError, match="YAML file is invalid"):
        validate_private_inputs(tmp_path)

    values = layout_values()
    values["section_order"] = [
        "summary",
        "summary",
        "experience",
        "projects",
        "education",
        "certifications",
        "additional",
    ]
    layout_path.write_text(yaml.safe_dump(values), encoding="utf-8")
    with pytest.raises(SettingsError, match="section order"):
        validate_private_inputs(tmp_path)
