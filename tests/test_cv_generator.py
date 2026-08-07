from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

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
    validate_grounded_cv_content,
)
from app.cv_generations import (
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


def prepare_private_inputs(tmp_path: Path) -> Path:
    profile_root = tmp_path / "private" / "profile"
    profile_root.mkdir(parents=True)
    profile_path = profile_root / "profile.md"
    profile_path.write_text(PROFILE, encoding="utf-8")
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
) -> str:
    service = AssessmentService(
        AssessmentRepository(database_path),
        artefacts,
        cast(StructuredGenerator, FakeGenerator(result or assessment_result())),
        load_ai_settings(),
        profile_path,
    )
    execution = service.execute(1, "2026-08-07T10:00:00+00:00")
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


def test_generation_is_grounded_role_targeted_rendered_and_persisted(
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


def test_grounding_rejects_fabricated_identity_skill_metric_and_title() -> None:
    profile_blocks = parse_source_blocks(PROFILE, SourceKind.PROFILE)
    assessment = assessment_result()
    valid = cv_content()
    bad_name = valid.model_copy(
        update={
            "identity": valid.identity.model_copy(
                update={
                    "full_name": valid.identity.full_name.model_copy(
                        update={"text": "Invented Person"}
                    )
                }
            )
        }
    )
    with pytest.raises(CvGroundingError, match="full name"):
        validate_grounded_cv_content(bad_name, assessment, profile_blocks)

    skills = valid.sections[1]
    bad_skill = valid.model_copy(
        update={
            "sections": (
                valid.sections[0],
                skills.model_copy(
                    update={
                        "claims": (
                            skills.claims[0].model_copy(
                                update={"text": "Python, Kubernetes"}
                            ),
                        )
                    }
                ),
            )
        }
    )
    with pytest.raises(CvGroundingError, match="skill"):
        validate_grounded_cv_content(bad_skill, assessment, profile_blocks)

    summary = valid.sections[0]
    for unsupported in (
        "Improved throughput by 99% in 2024.",
        "Worked as Principal Engineer in 2024.",
    ):
        changed = valid.model_copy(
            update={
                "sections": (
                    summary.model_copy(
                        update={
                            "claims": (
                                summary.claims[0].model_copy(
                                    update={"text": unsupported}
                                ),
                            )
                        }
                    ),
                    valid.sections[1],
                )
            }
        )
        with pytest.raises(CvGroundingError):
            validate_grounded_cv_content(changed, assessment, profile_blocks)


def test_grounding_rejects_disallowed_evidence_and_confidential_wording() -> (
    None
):
    profile_blocks = parse_source_blocks(PROFILE, SourceKind.PROFILE)
    valid = cv_content()
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
        validate_grounded_cv_content(
            changed, assessment_result(), profile_blocks
        )

    confidential_claim = (
        valid.sections[0]
        .claims[0]
        .model_copy(update={"text": "Built confidential typed services."})
    )
    changed_section = valid.sections[0].model_copy(
        update={"claims": (confidential_claim,)}
    )
    changed = valid.model_copy(
        update={"sections": (changed_section, valid.sections[1])}
    )
    with pytest.raises(CvGroundingError, match="confidential"):
        validate_grounded_cv_content(
            changed, assessment_result(), profile_blocks
        )


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
