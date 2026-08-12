"""Evidence-grounded assessment execution and safe persistence."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from app.ai.providers.base import (
    MAX_INPUT_LENGTH,
    StructuredGenerationRequest,
    StructuredGenerator,
)
from app.ai.schema_models import (
    AssessmentResult,
    RequirementMatch,
    SchemaVersion,
    validate_assessment_references,
)
from app.ai.source_blocks import (
    SourceBlock,
    SourceBlockError,
    SourceKind,
    parse_source_blocks,
)
from app.artefacts import ArtefactStore, sha256_bytes
from app.assessment.scoring import AssessmentScore, score_assessment
from app.assessment.taxonomy import get_taxonomy
from app.assessments import (
    AssessmentRepository,
    CompletedAssessment,
)
from app.settings import AiSettings
from app.work.models import WorkCheckpoint, WorkCheckpointProvenance

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INSTRUCTIONS_PATH = ROOT / "ai" / "instructions" / "assessment.md"
Checkpoint = Callable[
    [str, str, str, dict[str, str | None], WorkCheckpointProvenance],
    None,
]


class AssessmentInputChangedError(RuntimeError):
    """Raised when source content changes during one assessment attempt."""

    def __init__(self) -> None:
        super().__init__("Assessment inputs changed during execution.")


class AssessmentInputError(ValueError):
    """Raised when assessment sources cannot fit the provider contract."""


@dataclass(frozen=True)
class AssessmentInput:
    """Canonical, bounded input shared by queue preflight and workers."""

    profile_blocks: tuple[SourceBlock, ...]
    jd_blocks: tuple[SourceBlock, ...]
    input_text: str


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


def build_assessment_input(
    profile: str | bytes, job_description: str
) -> AssessmentInput:
    """Parse and serialize assessment sources within the provider limit."""
    try:
        profile_blocks = parse_source_blocks(profile, SourceKind.PROFILE)
        jd_blocks = parse_source_blocks(
            job_description, SourceKind.JOB_DESCRIPTION
        )
        input_text = _request_input(profile_blocks, jd_blocks)
    except SourceBlockError as error:
        raise AssessmentInputError("Assessment input is invalid.") from error
    if not input_text or len(input_text) > MAX_INPUT_LENGTH:
        raise AssessmentInputError("Assessment input is too large.")
    return AssessmentInput(profile_blocks, jd_blocks, input_text)


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
        work_id: str,
        worker_token: str,
        checkpoint: Checkpoint | None = None,
        completion_clock: Callable[[], str] | None = None,
        resume_checkpoint: WorkCheckpoint | None = None,
    ) -> AssessmentExecution:
        """Generate, validate, score, atomically write, and persist a result."""
        if not completed_at or completed_at != completed_at.strip():
            raise ValueError(
                "assessment completion time must be non-empty trimmed text"
            )
        if not work_id.strip() or not worker_token.strip():
            raise ValueError(
                "Assessment work ID and worker token are required."
            )
        self._repository.preflight(application_id, work_id, worker_token)
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

        assessment_input = build_assessment_input(
            profile_bytes, application.full_jd
        )
        instructions = self._instructions_path.read_text(encoding="utf-8")
        taxonomy = get_taxonomy(self._settings.scoring.taxonomy_version)
        request = StructuredGenerationRequest(
            model=self._settings.provider.assessment_model,
            schema_name="assessment_result_v1",
            schema=AssessmentResult,
            instructions=instructions,
            input_text=assessment_input.input_text,
            timeout_seconds=self._settings.provider.request_timeout_seconds,
            contains_profile=True,
        )
        checkpoint_hashes: dict[str, str | None] = {
            "profile_sha256": profile_sha256,
            "jd_sha256": jd_sha256,
            "prompt_sha256": _hash_text(instructions),
            "schema_sha256": _schema_hash(),
        }
        result_path: str
        generated_model: str
        generated_provider: str
        generated_response_ids: tuple[str, ...]
        generated_input_tokens = 0
        generated_output_tokens = 0
        generated_total_tokens = 0
        generated_repair_attempted = False
        result: AssessmentResult | None = None
        provenance: WorkCheckpointProvenance | None = None
        if resume_checkpoint is not None:
            loaded = self._load_checkpoint(
                resume_checkpoint,
                application.artefact_directory,
                checkpoint_hashes,
                assessment_input.jd_blocks,
                assessment_input.profile_blocks,
            )
            if loaded is not None:
                result, provenance = loaded
                generated_model = provenance.model
                generated_provider = provenance.provider
                generated_response_ids = provenance.response_ids
                generated_input_tokens = provenance.input_tokens
                generated_output_tokens = provenance.output_tokens
                generated_total_tokens = provenance.total_tokens
                generated_repair_attempted = provenance.repair_attempted
        if result is None:
            generated = self._generator.generate(request)
            result = generated.value
            validate_assessment_references(
                result,
                assessment_input.jd_blocks,
                assessment_input.profile_blocks,
            )
            generated_model = generated.model
            generated_provider = generated.metadata.provider
            generated_response_ids = generated.response_ids
            generated_input_tokens = generated.usage.input_tokens
            generated_output_tokens = generated.usage.output_tokens
            generated_total_tokens = generated.usage.total_tokens
            generated_repair_attempted = generated.metadata.repair_attempted
            provenance = WorkCheckpointProvenance(
                model=generated.model,
                provider=generated.metadata.provider,
                response_ids=generated.response_ids,
                input_tokens=generated.usage.input_tokens,
                output_tokens=generated.usage.output_tokens,
                total_tokens=generated.usage.total_tokens,
                repair_attempted=generated.metadata.repair_attempted,
            )
            assessment_id = uuid4().hex
            base = (
                Path(application.artefact_directory)
                / "assessments"
                / assessment_id
            )
            result_path = (base / "assessment-result.json").as_posix()
        else:
            assert resume_checkpoint is not None
            assessment_id = uuid4().hex
            result_path = resume_checkpoint.path
            base = Path(result_path).parent

        assert result is not None
        assert provenance is not None
        score = score_assessment(
            result,
            self._settings.scoring.threshold,
            taxonomy,
        )
        if resume_checkpoint is None or result_path != resume_checkpoint.path:
            base = (
                Path(application.artefact_directory)
                / "assessments"
                / assessment_id
            )
            result_path = (base / "assessment-result.json").as_posix()
        analysis_path = (base / "match-analysis.json").as_posix()
        result_document = result.model_dump(mode="json")
        analysis_document = {
            **score.as_document(taxonomy.version),
            "analysis": result.analysis,
            "gaps": _gap_document(result),
        }

        written: list[str] = []
        checkpointed: set[str] = set()
        try:
            if (
                resume_checkpoint is None
                or result_path != resume_checkpoint.path
            ):
                self._artefacts.write_json(result_path, result_document)
                written.append(result_path)
                result_sha256 = self._artefacts.sha256(result_path)
                if checkpoint is not None:
                    checkpoint(
                        "assessment_result",
                        result_path,
                        result_sha256,
                        checkpoint_hashes,
                        provenance,
                    )
                    checkpointed.add(result_path)
            else:
                result_sha256 = resume_checkpoint.sha256
            self._artefacts.write_json(analysis_path, analysis_document)
            written.append(analysis_path)
            current_application = self._repository.get_application(
                application_id
            )
            current_profile_sha256 = sha256_bytes(
                self._profile_path.read_bytes()
            )
            if (
                current_profile_sha256 != profile_sha256
                or _hash_text(current_application.full_jd) != jd_sha256
                or current_application.artefact_directory
                != application.artefact_directory
                or current_application.current_stage != "Assessing"
            ):
                raise AssessmentInputChangedError
            if checkpoint is not None and result_path not in checkpointed:
                checkpoint(
                    "assessment_result",
                    result_path,
                    result_sha256,
                    checkpoint_hashes,
                    provenance,
                )
                checkpointed.add(result_path)
            completion_time = (
                completion_clock()
                if completion_clock is not None
                else completed_at
            )
            if (
                not completion_time
                or completion_time != completion_time.strip()
            ):
                raise ValueError(
                    "assessment completion time must be non-empty trimmed text"
                )
            completed = CompletedAssessment(
                assessment_id=assessment_id,
                application_id=application_id,
                score=score,
                model=generated_model,
                model_sha256=_hash_text(generated_model),
                schema_version=result.schema_version.value,
                schema_sha256=_schema_hash(),
                instruction_sha256=_hash_text(instructions),
                taxonomy_version=taxonomy.version,
                taxonomy_sha256=taxonomy.sha256,
                profile_sha256=profile_sha256,
                jd_sha256=jd_sha256,
                provider=generated_provider,
                response_ids=generated_response_ids,
                input_tokens=generated_input_tokens,
                output_tokens=generated_output_tokens,
                total_tokens=generated_total_tokens,
                repair_attempted=generated_repair_attempted,
                result_path=result_path,
                result_sha256=result_sha256,
                analysis_path=analysis_path,
                analysis_sha256=self._artefacts.sha256(analysis_path),
                completed_at=completion_time,
                work_id=work_id,
                worker_token=worker_token,
            )
            self._repository.add_completed(completed)
        except BaseException:
            for path in reversed(written):
                if path not in checkpointed:
                    self._artefacts.resolve(path).unlink(missing_ok=True)
            raise

        return AssessmentExecution(
            assessment_id=assessment_id,
            result=result,
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
        work_id: str,
        worker_token: str,
        checkpoint: Checkpoint | None = None,
        completion_clock: Callable[[], str] | None = None,
        resume_checkpoint: WorkCheckpoint | None = None,
    ) -> AssessmentExecution:
        """Compatibility alias expressing the domain action directly."""
        return self.execute(
            application_id,
            completed_at,
            expected_profile_sha256=expected_profile_sha256,
            expected_jd_sha256=expected_jd_sha256,
            work_id=work_id,
            worker_token=worker_token,
            checkpoint=checkpoint,
            completion_clock=completion_clock,
            resume_checkpoint=resume_checkpoint,
        )

    def _load_checkpoint(
        self,
        checkpoint: WorkCheckpoint,
        application_directory: str,
        expected_hashes: dict[str, str | None],
        jd_blocks: tuple[SourceBlock, ...],
        profile_blocks: tuple[SourceBlock, ...],
    ) -> tuple[AssessmentResult, WorkCheckpointProvenance] | None:
        """Return a checkpoint only when its path, digest, and content agree."""
        if checkpoint.step != "assessment_result":
            return None
        if checkpoint.provenance is None:
            return None
        if any(
            checkpoint.hashes.get(key) != value
            for key, value in expected_hashes.items()
        ):
            return None
        try:
            path = Path(checkpoint.path)
            if not path.is_relative_to(Path(application_directory)):
                return None
            payload = self._artefacts.read_bytes(checkpoint.path)
            if sha256_bytes(payload) != checkpoint.sha256:
                return None
            result = AssessmentResult.model_validate_json(payload)
            validate_assessment_references(result, jd_blocks, profile_blocks)
            return result, checkpoint.provenance
        except OSError, TypeError, ValueError, KeyError:
            return None
