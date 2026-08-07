from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

from app.ai.providers.base import (
    ProviderErrorCode,
    ProviderMetadata,
    StructuredGenerationError,
    StructuredGenerationRequest,
    StructuredGenerationResult,
    StructuredGenerator,
    TokenUsage,
)
from app.ai.schema_models import (
    AssessmentResult,
    HardGateKind,
    HardGateStatus,
    RequirementMatch,
)
from app.artefacts import ArtefactStore, sha256_bytes
from app.assessment.scoring import AssessmentOutcome
from app.assessment.service import (
    AssessmentInputChangedError,
    AssessmentService,
)
from app.assessments import AssessmentRepository
from app.database import connect, initialize_database
from app.repository import Repository
from app.settings import load_ai_settings
from tests.fixtures.assessment_cases import assessment_result


class FakeGenerator:
    """Deterministic structured generator with an optional side effect."""

    def __init__(
        self,
        result: AssessmentResult | BaseException,
        effect: Callable[[], None] | None = None,
    ) -> None:
        self.result = result
        self.effect = effect
        self.requests: list[StructuredGenerationRequest[Any]] = []

    def generate(self, request: StructuredGenerationRequest[Any]) -> Any:
        self.requests.append(request)
        if self.effect is not None:
            self.effect()
        if isinstance(self.result, BaseException):
            raise self.result
        return StructuredGenerationResult(
            value=self.result,
            model=request.model,
            schema_name=request.schema_name,
            response_ids=("resp_assessment",),
            usage=TokenUsage(20, 10, 30),
            metadata=ProviderMetadata(
                provider="openai",
                statuses=("completed",),
                service_tiers=("default",),
                repair_attempted=False,
            ),
        )


def create_application(database_path: str) -> int:
    return Repository(database_path).create_application(
        {
            "role": "Python Engineer",
            "company": "Acme",
            "full_jd": "# Role\n\nRequirement 1",
        },
        "2026-08-07T09:00:00+00:00",
    )


def build_service(
    database_path: str,
    tmp_path: Path,
    result: AssessmentResult | BaseException,
    *,
    effect: Callable[[], None] | None = None,
    store: ArtefactStore | None = None,
) -> tuple[AssessmentService, FakeGenerator, Path, ArtefactStore]:
    profile_path = tmp_path / "profile.md"
    if not profile_path.exists():
        profile_path.write_text(
            "# Example Person\n\nExperienced Python engineer.",
            encoding="utf-8",
        )
    fake = FakeGenerator(result, effect)
    artefacts = store or ArtefactStore(tmp_path / "artefacts")
    service = AssessmentService(
        AssessmentRepository(database_path),
        artefacts,
        cast(StructuredGenerator, fake),
        load_ai_settings(),
        profile_path,
    )
    return service, fake, profile_path, artefacts


def assessment_count(database_path: str) -> int:
    with connect(database_path) as connection:
        return int(
            connection.execute("SELECT COUNT(*) FROM assessments").fetchone()[0]
        )


def test_matched_assessment_is_grounded_scored_and_persisted(
    database_path: str, tmp_path: Path
) -> None:
    initialize_database(database_path)
    application_id = create_application(database_path)
    service, fake, profile_path, artefacts = build_service(
        database_path, tmp_path, assessment_result()
    )

    execution = service.execute(application_id, "2026-08-07T10:00:00+00:00")

    assert execution.score.outcome is AssessmentOutcome.MATCHED
    assert (
        Repository(database_path).get_application(application_id)[
            "current_stage"
        ]
        == "Assessing"
    )
    assert len(fake.requests) == 1
    request = fake.requests[0]
    assert request.schema is AssessmentResult
    assert request.contains_profile is True
    assert "profile-0002" in request.input_text
    assert "jd-0002" in request.input_text
    assert "Do not calculate a score" in request.instructions

    stored = AssessmentRepository(database_path).get(execution.assessment_id)
    assert stored["outcome"] == "matched"
    assert stored["profile_sha256"] == sha256_bytes(profile_path.read_bytes())
    assert stored["response_ids"] == ["resp_assessment"]
    assert stored["total_tokens"] == 30
    assert all(
        len(str(stored[field])) == 64
        for field in (
            "model_sha256",
            "schema_sha256",
            "instruction_sha256",
            "taxonomy_sha256",
            "profile_sha256",
            "jd_sha256",
            "result_sha256",
            "analysis_sha256",
        )
    )
    assert artefacts.read_json(execution.result_path)["analysis"] == (
        "Evidence-grounded overall analysis."
    )
    analysis = artefacts.read_json(execution.analysis_path)
    assert analysis["outcome"] == "matched"
    assert analysis["final_score"] == 100.0


@pytest.mark.parametrize(
    ("result", "outcome"),
    [
        (
            assessment_result(classifications=(RequirementMatch.GAP,)),
            AssessmentOutcome.SKILL_MISMATCH,
        ),
        (
            assessment_result(
                gate_statuses={
                    HardGateKind.SALARY_LOCATION: HardGateStatus.CONFLICT
                }
            ),
            AssessmentOutcome.SALARY_LOCATION_MISMATCH,
        ),
        (
            assessment_result(
                gate_statuses={
                    HardGateKind.EXCLUDED_BUSINESS: HardGateStatus.CONFLICT
                }
            ),
            AssessmentOutcome.OTHER_MISMATCH,
        ),
    ],
)
def test_mismatch_is_a_completed_assessment_and_transitions_lifecycle(
    database_path: str,
    tmp_path: Path,
    result: AssessmentResult,
    outcome: AssessmentOutcome,
) -> None:
    initialize_database(database_path)
    application_id = create_application(database_path)
    service, _, _, _ = build_service(database_path, tmp_path, result)

    execution = service.execute(application_id, "2026-08-07T10:00:00+00:00")

    application = Repository(database_path).get_application(application_id)
    assert execution.score.outcome is outcome
    assert application["current_stage"] == "Mismatch"
    assert application["current_stage_description"] == outcome.value
    assert assessment_count(database_path) == 1


def test_unsupported_model_reference_is_a_technical_failure(
    database_path: str, tmp_path: Path
) -> None:
    initialize_database(database_path)
    application_id = create_application(database_path)
    valid = assessment_result()
    requirement = valid.requirements[0].model_copy(
        update={"jd_block_ids": ("jd-0099",)}
    )
    unsupported = valid.model_copy(update={"requirements": (requirement,)})
    service, _, _, artefacts = build_service(
        database_path, tmp_path, unsupported
    )

    with pytest.raises(ValueError, match="dangling JD block"):
        service.execute(application_id, "2026-08-07T10:00:00+00:00")

    assert assessment_count(database_path) == 0
    assert (
        Repository(database_path).get_application(application_id)[
            "current_stage"
        ]
        == "Assessing"
    )
    assert list(artefacts.root.rglob("*.json")) == []


def test_changed_profile_is_rejected_after_provider_returns(
    database_path: str, tmp_path: Path
) -> None:
    initialize_database(database_path)
    application_id = create_application(database_path)
    profile_path = tmp_path / "profile.md"
    profile_path.write_text(
        "# Example Person\n\nExperienced Python engineer.", encoding="utf-8"
    )

    def change_profile() -> None:
        profile_path.write_text(
            "# Example Person\n\nChanged profile.", encoding="utf-8"
        )

    service, _, _, _ = build_service(
        database_path,
        tmp_path,
        assessment_result(),
        effect=change_profile,
    )

    with pytest.raises(AssessmentInputChangedError, match="inputs changed"):
        service.execute(application_id, "2026-08-07T10:00:00+00:00")

    assert assessment_count(database_path) == 0


def test_expected_hashes_reject_stale_work_before_provider(
    database_path: str, tmp_path: Path
) -> None:
    initialize_database(database_path)
    application_id = create_application(database_path)
    service, fake, _, _ = build_service(
        database_path, tmp_path, assessment_result()
    )

    with pytest.raises(AssessmentInputChangedError):
        service.execute(
            application_id,
            "2026-08-07T10:00:00+00:00",
            expected_profile_sha256="0" * 64,
        )

    assert fake.requests == []


@pytest.mark.parametrize(
    "error",
    [
        StructuredGenerationError(
            ProviderErrorCode.TIMEOUT,
            "Provider request timed out.",
            retryable=True,
        ),
        StructuredGenerationError(
            ProviderErrorCode.INVALID_OUTPUT,
            "Provider output failed validation after repair.",
            retryable=False,
        ),
    ],
)
def test_provider_and_repair_failures_do_not_become_mismatches(
    database_path: str, tmp_path: Path, error: StructuredGenerationError
) -> None:
    initialize_database(database_path)
    application_id = create_application(database_path)
    service, _, _, _ = build_service(database_path, tmp_path, error)

    with pytest.raises(StructuredGenerationError) as caught:
        service.execute(application_id, "2026-08-07T10:00:00+00:00")

    assert caught.value is error
    assert assessment_count(database_path) == 0
    assert (
        Repository(database_path).get_application(application_id)[
            "current_stage"
        ]
        == "Assessing"
    )


def test_atomic_write_failure_leaves_no_assessment_or_partial_file(
    database_path: str, tmp_path: Path
) -> None:
    initialize_database(database_path)
    application_id = create_application(database_path)

    replacements = 0

    def fail_second_replace(
        source: os.PathLike[str], target: os.PathLike[str]
    ) -> None:
        nonlocal replacements
        replacements += 1
        if replacements == 2:
            raise OSError("simulated atomic replacement failure")
        os.replace(source, target)

    store = ArtefactStore(tmp_path / "artefacts", replace=fail_second_replace)
    service, _, _, _ = build_service(
        database_path, tmp_path, assessment_result(), store=store
    )

    with pytest.raises(OSError, match="simulated atomic"):
        service.execute(application_id, "2026-08-07T10:00:00+00:00")

    assert assessment_count(database_path) == 0
    assert list(store.root.rglob("*.json")) == []
    assert list(store.root.rglob("*.tmp-*")) == []


def test_completed_assessment_rows_are_database_immutable(
    database_path: str, tmp_path: Path
) -> None:
    initialize_database(database_path)
    application_id = create_application(database_path)
    service, _, _, _ = build_service(
        database_path, tmp_path, assessment_result()
    )
    execution = service.execute(application_id, "2026-08-07T10:00:00+00:00")

    with (
        connect(database_path) as connection,
        pytest.raises(sqlite3.IntegrityError, match="immutable"),
    ):
        connection.execute(
            "UPDATE assessments SET final_score = 0 WHERE id = ?",
            (execution.assessment_id,),
        )
