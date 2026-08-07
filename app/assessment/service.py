"""Evidence-grounded assessment execution and safe persistence."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from app.ai.providers.base import (
    StructuredGenerationRequest,
    StructuredGenerator,
)
from app.ai.schema_models import (
    AssessmentResult,
    RequirementMatch,
    SchemaVersion,
    validate_assessment_references,
)
from app.ai.source_blocks import SourceBlock, SourceKind, parse_source_blocks
from app.artefacts import ArtefactStore, sha256_bytes
from app.assessment.scoring import AssessmentScore, score_assessment
from app.assessment.taxonomy import get_taxonomy
from app.assessments import (
    AssessmentRepository,
    CompletedAssessment,
)
from app.settings import AiSettings

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INSTRUCTIONS_PATH = ROOT / "ai" / "instructions" / "assessment.md"


class AssessmentInputChangedError(RuntimeError):
    """Raised when source content changes during one assessment attempt."""

    def __init__(self) -> None:
        super().__init__("Assessment inputs changed during execution.")


@dataclass(frozen=True)
class AssessmentExecution:
    """Completed assessment returned to the worker orchestration layer."""

    assessment_id: str
    result: AssessmentResult
    score: AssessmentScore
    result_path: str
    analysis_path: str


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _schema_hash() -> str:
    schema = AssessmentResult.model_json_schema(mode="validation")
    encoded = json.dumps(
        schema, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return sha256_bytes(encoded)


def _block_document(block: SourceBlock) -> dict[str, object]:
    return {
        "block_id": block.block_id,
        "order": block.order,
        "kind": block.kind.value,
        "content": block.content,
        "content_sha256": block.content_sha256,
    }


def _request_input(
    profile_blocks: tuple[SourceBlock, ...],
    jd_blocks: tuple[SourceBlock, ...],
) -> str:
    return json.dumps(
        {
            "schema_version": SchemaVersion.V1.value,
            "profile_blocks": [
                _block_document(block) for block in profile_blocks
            ],
            "job_description_blocks": [
                _block_document(block) for block in jd_blocks
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _gap_document(result: AssessmentResult) -> list[dict[str, object]]:
    requirements = {
        requirement.requirement_id: requirement
        for requirement in result.requirements
    }
    return [
        {
            "requirement_id": evidence.requirement_id,
            "requirement": requirements[evidence.requirement_id].text,
            "mandatory": requirements[evidence.requirement_id].mandatory,
            "classification": evidence.classification.value,
            "rationale": evidence.rationale,
        }
        for evidence in result.evidence
        if evidence.classification is not RequirementMatch.MATCHED
    ]


class AssessmentService:
    """Run one structured assessment against fresh private/application input."""

    def __init__(
        self,
        repository: AssessmentRepository,
        artefacts: ArtefactStore,
        generator: StructuredGenerator,
        settings: AiSettings,
        profile_path: str | Path,
        *,
        instructions_path: str | Path = DEFAULT_INSTRUCTIONS_PATH,
    ) -> None:
        self._repository = repository
        self._artefacts = artefacts
        self._generator = generator
        self._settings = settings
        self._profile_path = Path(profile_path)
        self._instructions_path = Path(instructions_path)

    def execute(
        self,
        application_id: int,
        completed_at: str,
        *,
        expected_profile_sha256: str | None = None,
        expected_jd_sha256: str | None = None,
    ) -> AssessmentExecution:
        """Generate, validate, score, atomically write, and persist a result."""
        if not completed_at or completed_at != completed_at.strip():
            raise ValueError(
                "assessment completion time must be non-empty trimmed text"
            )
        application = self._repository.get_application(application_id)
        if application.current_stage != "Assessing":
            raise RuntimeError("Application is not awaiting assessment.")

        profile_bytes = self._profile_path.read_bytes()
        profile_sha256 = sha256_bytes(profile_bytes)
        jd_sha256 = _hash_text(application.full_jd)
        if (
            expected_profile_sha256 is not None
            and expected_profile_sha256 != profile_sha256
        ) or (
            expected_jd_sha256 is not None and expected_jd_sha256 != jd_sha256
        ):
            raise AssessmentInputChangedError

        profile_blocks = parse_source_blocks(profile_bytes, SourceKind.PROFILE)
        jd_blocks = parse_source_blocks(
            application.full_jd, SourceKind.JOB_DESCRIPTION
        )
        instructions = self._instructions_path.read_text(encoding="utf-8")
        taxonomy = get_taxonomy(self._settings.scoring.taxonomy_version)
        request = StructuredGenerationRequest(
            model=self._settings.provider.assessment_model,
            schema_name="assessment_result_v1",
            schema=AssessmentResult,
            instructions=instructions,
            input_text=_request_input(profile_blocks, jd_blocks),
            timeout_seconds=self._settings.provider.request_timeout_seconds,
            contains_profile=True,
        )
        generated = self._generator.generate(request)
        validate_assessment_references(
            generated.value, jd_blocks, profile_blocks
        )

        current_application = self._repository.get_application(application_id)
        current_profile_sha256 = sha256_bytes(self._profile_path.read_bytes())
        if (
            current_profile_sha256 != profile_sha256
            or _hash_text(current_application.full_jd) != jd_sha256
            or current_application.artefact_directory
            != application.artefact_directory
            or current_application.current_stage != "Assessing"
        ):
            raise AssessmentInputChangedError

        score = score_assessment(
            generated.value,
            self._settings.scoring.threshold,
            taxonomy,
        )
        assessment_id = uuid4().hex
        base = (
            Path(application.artefact_directory) / "assessments" / assessment_id
        )
        result_path = (base / "assessment-result.json").as_posix()
        analysis_path = (base / "match-analysis.json").as_posix()
        result_document = generated.value.model_dump(mode="json")
        analysis_document = {
            **score.as_document(taxonomy.version),
            "analysis": generated.value.analysis,
            "gaps": _gap_document(generated.value),
        }

        written: list[str] = []
        try:
            self._artefacts.write_json(result_path, result_document)
            written.append(result_path)
            self._artefacts.write_json(analysis_path, analysis_document)
            written.append(analysis_path)
            completed = CompletedAssessment(
                assessment_id=assessment_id,
                application_id=application_id,
                score=score,
                model=generated.model,
                model_sha256=_hash_text(generated.model),
                schema_version=generated.value.schema_version.value,
                schema_sha256=_schema_hash(),
                instruction_sha256=_hash_text(instructions),
                taxonomy_version=taxonomy.version,
                taxonomy_sha256=taxonomy.sha256,
                profile_sha256=profile_sha256,
                jd_sha256=jd_sha256,
                provider=generated.metadata.provider,
                response_ids=generated.response_ids,
                input_tokens=generated.usage.input_tokens,
                output_tokens=generated.usage.output_tokens,
                total_tokens=generated.usage.total_tokens,
                repair_attempted=generated.metadata.repair_attempted,
                result_path=result_path,
                result_sha256=self._artefacts.sha256(result_path),
                analysis_path=analysis_path,
                analysis_sha256=self._artefacts.sha256(analysis_path),
                completed_at=completed_at,
            )
            self._repository.add_completed(completed)
        except BaseException:
            for path in reversed(written):
                self._artefacts.resolve(path).unlink(missing_ok=True)
            raise

        return AssessmentExecution(
            assessment_id=assessment_id,
            result=generated.value,
            score=score,
            result_path=result_path,
            analysis_path=analysis_path,
        )

    def assess(
        self,
        application_id: int,
        completed_at: str,
        *,
        expected_profile_sha256: str | None = None,
        expected_jd_sha256: str | None = None,
    ) -> AssessmentExecution:
        """Compatibility alias expressing the domain action directly."""
        return self.execute(
            application_id,
            completed_at,
            expected_profile_sha256=expected_profile_sha256,
            expected_jd_sha256=expected_jd_sha256,
        )
