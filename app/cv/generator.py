"""Generate evidence-cited CV drafts and deterministic DOCX candidates."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from app.ai.providers.base import (
    StructuredGenerationRequest,
    StructuredGenerator,
)
from app.ai.schema_models import (
    AssessmentResult,
    CvContent,
    RequirementMatch,
    SchemaVersion,
    validate_assessment_references,
    validate_cv_references,
)
from app.ai.source_blocks import SourceBlock, SourceKind, parse_source_blocks
from app.artefacts import ArtefactStore, sha256_bytes
from app.cv_generations import (
    CompletedCvGeneration,
    CvGenerationInput,
    CvGenerationRepository,
)
from app.documents.word_writer import CandidateDocument, WordWriter
from app.settings import AiSettings, CvLayoutSettings, PrivateInputs
from app.work.models import WorkCheckpoint, WorkCheckpointProvenance

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INSTRUCTIONS_PATH = ROOT / "ai" / "instructions" / "cv-generator.md"
Checkpoint = Callable[
    [str, str, str, dict[str, str | None], WorkCheckpointProvenance],
    None,
]
RESTRICTED_WORDING_PATTERN = re.compile(
    r"\b(?:confidential|proprietary|trade[\s-]+secret|"
    r"(?:under[\s-]+(?:an[\s-]+)?)?nda(?:[\s-]+protected)?|"
    r"non[\s-]*disclosure|internal[\s-]+only|do[\s-]+not[\s-]+disclose)\b",
    flags=re.IGNORECASE,
)


class CvInputChangedError(RuntimeError):
    """Raised when generation inputs differ from the assessed inputs."""

    def __init__(self) -> None:
        super().__init__("CV generation inputs changed during execution.")


class CvGroundingError(ValueError):
    """Raised when a generated draft violates the citation contract."""


@dataclass(frozen=True)
class CvGenerationExecution:
    """Completed content and candidate returned to worker orchestration."""

    generation_id: str
    content: CvContent
    content_path: str
    candidate: CandidateDocument


class CandidateWriter(Protocol):
    """Narrow deterministic writer contract used by CV generation."""

    def write(
        self,
        content: CvContent,
        role: str,
        base_path: str | Path,
        template_path: str | Path,
        layout: CvLayoutSettings,
    ) -> CandidateDocument:
        """Persist and return one rendered candidate."""


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _schema_hash() -> str:
    schema = CvContent.model_json_schema(mode="validation")
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


def _allowed_evidence(
    assessment: AssessmentResult,
) -> dict[str, tuple[str, ...]]:
    return {
        evidence.requirement_id: evidence.profile_block_ids
        for evidence in assessment.evidence
        if evidence.classification
        in {RequirementMatch.MATCHED, RequirementMatch.PARTIAL}
    }


def _request_input(
    role: str,
    assessment: AssessmentResult,
    profile_blocks: tuple[SourceBlock, ...],
) -> str:
    allowed = _allowed_evidence(assessment)
    requirements = [
        requirement.model_dump(mode="json")
        for requirement in assessment.requirements
        if requirement.requirement_id in allowed
    ]
    return json.dumps(
        {
            "schema_version": SchemaVersion.V1.value,
            "target_role": role,
            "requirements": requirements,
            "allowed_evidence": allowed,
            "profile_blocks": [
                _block_document(block) for block in profile_blocks
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _require_exact_cited_value(
    value: str,
    block_ids: tuple[str, ...],
    blocks: dict[str, SourceBlock],
    label: str,
) -> None:
    """Require an identity/contact value verbatim in one cited block."""
    if not any(value in blocks[block_id].content for block_id in block_ids):
        raise CvGroundingError(f"Unsupported {label} in generated CV.")


def _validate_confidential_text(content: CvContent) -> None:
    text_values = [
        value.text
        for value in (
            content.identity.full_name,
            content.identity.professional_title,
            content.identity.email,
            content.identity.phone,
            content.identity.location,
            content.identity.website,
        )
        if value is not None
    ]
    text_values.extend(section.heading for section in content.sections)
    text_values.extend(
        claim.text for section in content.sections for claim in section.claims
    )
    if any(RESTRICTED_WORDING_PATTERN.search(value) for value in text_values):
        raise CvGroundingError(
            "Generated CV contains confidential or restricted wording."
        )


def validate_cited_cv_content(
    content: CvContent,
    assessment: AssessmentResult,
    profile_blocks: tuple[SourceBlock, ...],
) -> None:
    """Validate citations, exact identity values, and restricted wording.

    Narrative claims remain provider-authored drafts for mandatory human
    factual and editorial review. Arbitrary Markdown does not provide a sound
    deterministic boundary for semantic fact validation.
    """
    allowed = _allowed_evidence(assessment)
    validate_cv_references(content, profile_blocks, set(allowed))
    blocks = {block.block_id: block for block in profile_blocks}
    _validate_confidential_text(content)

    identity_values = (
        ("full name", content.identity.full_name),
        ("professional title", content.identity.professional_title),
        ("email", content.identity.email),
        ("phone", content.identity.phone),
        ("location", content.identity.location),
        ("website", content.identity.website),
    )
    for label, value in identity_values:
        if value is None:
            continue
        _require_exact_cited_value(
            value.text,
            value.profile_block_ids,
            blocks,
            label,
        )

    for section in content.sections:
        for claim in section.claims:
            if claim.requirement_ids:
                permitted_blocks = {
                    block_id
                    for requirement_id in claim.requirement_ids
                    for block_id in allowed[requirement_id]
                }
                if not set(claim.profile_block_ids).issubset(permitted_blocks):
                    raise CvGroundingError(
                        "CV claim cites evidence not allowed by its assessment."
                    )


validate_grounded_cv_content = validate_cited_cv_content


class CvGenerationService:
    """Generate, validate, render, and persist an evidence-cited CV draft."""

    def __init__(
        self,
        repository: CvGenerationRepository,
        artefacts: ArtefactStore,
        generator: StructuredGenerator,
        writer: CandidateWriter,
        settings: AiSettings,
        private_inputs: PrivateInputs,
        *,
        instructions_path: str | Path = DEFAULT_INSTRUCTIONS_PATH,
    ) -> None:
        self._repository = repository
        self._artefacts = artefacts
        self._generator = generator
        self._writer = writer
        self._settings = settings
        self._inputs = private_inputs
        self._instructions_path = Path(instructions_path)

    def _ensure_private_inputs_unchanged(self) -> None:
        inputs = self._inputs
        current = (
            sha256_bytes(inputs.profile_path.read_bytes()),
            sha256_bytes(inputs.template_path.read_bytes()),
            sha256_bytes(inputs.layout_path.read_bytes()),
        )
        expected = (
            inputs.profile_sha256,
            inputs.template_sha256,
            inputs.layout_sha256,
        )
        if current != expected:
            raise CvInputChangedError

    def _load_assessment(
        self,
        generation_input: CvGenerationInput,
        profile_blocks: tuple[SourceBlock, ...],
    ) -> AssessmentResult:
        result_bytes = self._artefacts.read_bytes(
            generation_input.assessment_result_path
        )
        if (
            sha256_bytes(result_bytes)
            != generation_input.assessment_result_sha256
        ):
            raise CvInputChangedError
        assessment = AssessmentResult.model_validate_json(result_bytes)
        jd_blocks = parse_source_blocks(
            generation_input.full_jd, SourceKind.JOB_DESCRIPTION
        )
        validate_assessment_references(assessment, jd_blocks, profile_blocks)
        return assessment

    def execute(
        self,
        application_id: int,
        assessment_id: str,
        completed_at: str,
        *,
        allow_mismatch: bool = False,
        work_id: str,
        worker_token: str,
        checkpoint: Checkpoint | None = None,
        completion_clock: Callable[[], str] | None = None,
        resume_checkpoint: WorkCheckpoint | None = None,
    ) -> CvGenerationExecution:
        """Produce and persist one evidence-cited DOCX candidate."""
        if not completed_at or completed_at != completed_at.strip():
            raise ValueError(
                "CV generation completion time must be trimmed text"
            )
        if not work_id.strip() or not worker_token.strip():
            raise ValueError("CV work ID and worker token are required.")
        self._repository.preflight(
            application_id,
            assessment_id,
            work_id,
            worker_token,
        )
        generation_input = self._repository.get_input(
            application_id,
            assessment_id,
            allow_mismatch=allow_mismatch,
        )
        self._ensure_private_inputs_unchanged()
        profile_bytes = self._inputs.profile_path.read_bytes()
        profile_sha256 = sha256_bytes(profile_bytes)
        role = generation_input.role.strip()
        if not role:
            raise ValueError("CV target role is required.")
        jd_sha256 = _hash_text(generation_input.full_jd)
        if (
            profile_sha256 != generation_input.assessment_profile_sha256
            or jd_sha256 != generation_input.assessment_jd_sha256
        ):
            raise CvInputChangedError
        profile_blocks = parse_source_blocks(profile_bytes, SourceKind.PROFILE)
        assessment = self._load_assessment(generation_input, profile_blocks)
        instructions = self._instructions_path.read_text(encoding="utf-8")
        schema_sha256 = _schema_hash()
        checkpoint_hashes: dict[str, str | None] = {
            "profile_sha256": profile_sha256,
            "jd_sha256": jd_sha256,
            "assessment_result_sha256": (
                generation_input.assessment_result_sha256
            ),
            "role_sha256": _hash_text(role),
            "prompt_sha256": _hash_text(instructions),
            "schema_sha256": schema_sha256,
            "template_sha256": self._inputs.template_sha256,
            "layout_sha256": self._inputs.layout_sha256,
        }
        request = StructuredGenerationRequest(
            model=self._settings.provider.cv_model,
            schema_name="cv_content_v1",
            schema=CvContent,
            instructions=instructions,
            input_text=_request_input(role, assessment, profile_blocks),
            timeout_seconds=self._settings.provider.request_timeout_seconds,
            contains_profile=True,
        )
        content: CvContent | None = None
        generated_model = "checkpoint"
        generated_provider = "checkpoint"
        generated_response_ids: tuple[str, ...] = ()
        generated_input_tokens = 0
        generated_output_tokens = 0
        generated_total_tokens = 0
        generated_repair_attempted = False
        provenance: WorkCheckpointProvenance | None = None
        content_path: str | None = None
        if resume_checkpoint is not None:
            loaded = self._load_checkpoint(
                resume_checkpoint,
                generation_input.artefact_directory,
                checkpoint_hashes,
                assessment,
                profile_blocks,
            )
            if loaded is not None:
                content, provenance = loaded
                generated_model = provenance.model
                generated_provider = provenance.provider
                generated_response_ids = provenance.response_ids
                generated_input_tokens = provenance.input_tokens
                generated_output_tokens = provenance.output_tokens
                generated_total_tokens = provenance.total_tokens
                generated_repair_attempted = provenance.repair_attempted
                content_path = resume_checkpoint.path
        if content is None:
            generated = self._generator.generate(request)
            content = generated.value
            validate_cited_cv_content(content, assessment, profile_blocks)
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

        assert provenance is not None
        generation_id = uuid4().hex
        base = (
            Path(generation_input.artefact_directory)
            / "cv-generations"
            / generation_id
        )
        generated_content_path = (base / "cv-content.json").as_posix()
        written: list[str] = []
        checkpointed = False
        try:
            if content_path is None:
                content_path = generated_content_path
                self._artefacts.write_json(
                    content_path, content.model_dump(mode="json")
                )
                written.append(content_path)
                if checkpoint is not None:
                    checkpoint(
                        "cv_content",
                        content_path,
                        self._artefacts.sha256(content_path),
                        checkpoint_hashes,
                        provenance,
                    )
                    checkpointed = True
            candidate = self._writer.write(
                content,
                role,
                base,
                self._inputs.template_path,
                self._inputs.layout,
            )
            written.append(candidate.relative_path)
            self._ensure_private_inputs_unchanged()
            current_input = self._repository.get_input(
                application_id,
                assessment_id,
                allow_mismatch=allow_mismatch,
            )
            if (
                current_input != generation_input
                or self._artefacts.sha256(
                    generation_input.assessment_result_path
                )
                != generation_input.assessment_result_sha256
            ):
                raise CvInputChangedError
            if checkpoint is not None and not checkpointed:
                checkpoint(
                    "cv_content",
                    content_path,
                    self._artefacts.sha256(content_path),
                    checkpoint_hashes,
                    provenance,
                )
                checkpointed = True
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
                    "CV generation completion time must be trimmed text"
                )
            completed = CompletedCvGeneration(
                generation_id=generation_id,
                application_id=application_id,
                assessment_id=assessment_id,
                model=generated_model,
                model_sha256=_hash_text(generated_model),
                schema_version=content.schema_version.value,
                schema_sha256=schema_sha256,
                instruction_sha256=_hash_text(instructions),
                profile_sha256=profile_sha256,
                jd_sha256=jd_sha256,
                assessment_result_sha256=(
                    generation_input.assessment_result_sha256
                ),
                template_sha256=self._inputs.template_sha256,
                layout_sha256=self._inputs.layout_sha256,
                provider=generated_provider,
                response_ids=generated_response_ids,
                input_tokens=generated_input_tokens,
                output_tokens=generated_output_tokens,
                total_tokens=generated_total_tokens,
                repair_attempted=generated_repair_attempted,
                content_path=content_path,
                content_sha256=self._artefacts.sha256(content_path),
                candidate_path=candidate.relative_path,
                candidate_sha256=candidate.sha256,
                completed_at=completion_time,
                work_id=work_id,
                worker_token=worker_token,
            )
            self._repository.add_completed(completed)
        except BaseException:
            for path in reversed(written):
                if path != content_path or not checkpointed:
                    self._artefacts.resolve(path).unlink(missing_ok=True)
            raise
        assert content_path is not None
        return CvGenerationExecution(
            generation_id=generation_id,
            content=content,
            content_path=content_path,
            candidate=candidate,
        )

    def _load_checkpoint(
        self,
        checkpoint: WorkCheckpoint,
        application_directory: str,
        expected_hashes: dict[str, str | None],
        assessment: AssessmentResult,
        profile_blocks: tuple[SourceBlock, ...],
    ) -> tuple[CvContent, WorkCheckpointProvenance] | None:
        """Return validated content only when all current inputs still match."""
        if checkpoint.step != "cv_content":
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
            content = CvContent.model_validate_json(payload)
            validate_cited_cv_content(content, assessment, profile_blocks)
            return content, checkpoint.provenance
        except OSError, TypeError, ValueError, KeyError:
            return None


def default_word_writer(artefacts: ArtefactStore) -> WordWriter:
    """Build the ordinary deterministic writer used by worker wiring."""
    return WordWriter(artefacts)
