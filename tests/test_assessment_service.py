from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal
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
from app.assessment.scoring import AssessmentOutcome, score_assessment
from app.assessment.service import (
    AssessmentExecution,
    AssessmentInputChangedError,
    AssessmentService,
    _hash_text,
    _schema_hash,
)
from app.assessment.taxonomy import get_taxonomy
from app.assessments import (
    AssessmentRepository,
    AssessmentStateError,
    CompletedAssessment,
)
from app.database import connect, initialize_database
from app.repository import Repository
from app.settings import load_ai_settings
from app.work.models import WorkCheckpoint, WorkCheckpointProvenance, WorkType
from app.work.repository import StaleWorkerError, WorkRepository
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


def assessment_work_credentials(
    database_path: str, application_id: int, token: str = "worker-a"
) -> tuple[str, str]:
    repository = WorkRepository(database_path)
    work = repository.active_for_application(
        application_id, WorkType.ASSESSMENT
    )
    assert work is not None
    if work.state.value == "queued":
        repository.claim(work.id, token, "2026-08-07T09:01:00Z")
    return work.id, token


def run_assessment(
    service: AssessmentService,
    database_path: str,
    application_id: int,
    completed_at: str,
    *,
    expected_profile_sha256: str | None = None,
    expected_jd_sha256: str | None = None,
) -> AssessmentExecution:
    work_id, worker_token = assessment_work_credentials(
        database_path, application_id
    )
    return service.execute(
        application_id,
        completed_at,
        expected_profile_sha256=expected_profile_sha256,
        expected_jd_sha256=expected_jd_sha256,
        work_id=work_id,
        worker_token=worker_token,
    )


def completed_assessment(
    database_path: str,
    application_id: int,
    assessment_id: str = "assessment-replay",
    token: str = "assessment-finalizer",
) -> CompletedAssessment:
    """Build one direct repository completion with claimed work."""
    settings = load_ai_settings()
    work_repository = WorkRepository(database_path)
    work = work_repository.active_for_application(
        application_id, WorkType.ASSESSMENT
    )
    assert work is not None
    if work.state.value == "queued":
        work_repository.claim(work.id, token, "2026-08-07T09:01:00Z")
    score = score_assessment(
        assessment_result(),
        settings.scoring.threshold,
        get_taxonomy(settings.scoring.taxonomy_version),
    )
    return CompletedAssessment(
        assessment_id=assessment_id,
        application_id=application_id,
        score=score,
        model="gpt-test",
        model_sha256="a" * 64,
        schema_version="v1",
        schema_sha256="b" * 64,
        instruction_sha256="c" * 64,
        taxonomy_version=settings.scoring.taxonomy_version,
        taxonomy_sha256="d" * 64,
        profile_sha256="e" * 64,
        jd_sha256="f" * 64,
        provider="test",
        response_ids=("response-1",),
        input_tokens=10,
        output_tokens=20,
        total_tokens=30,
        repair_attempted=False,
        result_path="application/assessments/assessment-replay/result.json",
        result_sha256="1" * 64,
        analysis_path="application/assessments/assessment-replay/analysis.json",
        analysis_sha256="2" * 64,
        completed_at="2026-08-07T10:00:00+00:00",
        work_id=work.id,
        worker_token=token,
    )


def assessment_state_snapshot(
    database_path: str, application_id: int
) -> tuple[
    list[tuple[object, ...]], list[tuple[object, ...]], list[tuple[object, ...]]
]:
    """Capture domain, lifecycle, and work rows for atomicity assertions."""
    with connect(database_path) as connection:
        assessments = [
            tuple(row)
            for row in connection.execute(
                "SELECT * FROM assessments ORDER BY id"
            ).fetchall()
        ]
        lifecycle = [
            tuple(row)
            for row in connection.execute(
                """SELECT * FROM application_stage_history
                WHERE application_id = ? ORDER BY id""",
                (application_id,),
            ).fetchall()
        ]
        work = [
            tuple(row)
            for row in connection.execute(
                """SELECT * FROM work_items
                WHERE application_id = ? ORDER BY id""",
                (application_id,),
            ).fetchall()
        ]
    return assessments, lifecycle, work


def test_assessment_repository_imports_in_a_fresh_process() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from app.assessments import AssessmentRepository; "
                "from app.assessment import AssessmentService"
            ),
        ],
        cwd=Path(__file__).resolve().parent.parent,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


def test_matched_assessment_is_grounded_scored_and_persisted(
    database_path: str, tmp_path: Path
) -> None:
    initialize_database(database_path)
    application_id = create_application(database_path)
    service, fake, profile_path, artefacts = build_service(
        database_path, tmp_path, assessment_result()
    )

    execution = run_assessment(
        service, database_path, application_id, "2026-08-07T10:00:00+00:00"
    )

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


def test_valid_assessment_checkpoint_reuses_exact_provenance(
    database_path: str, tmp_path: Path
) -> None:
    initialize_database(database_path)
    application_id = create_application(database_path)
    service, fake, profile_path, artefacts = build_service(
        database_path, tmp_path, assessment_result()
    )
    work_id, worker_token = assessment_work_credentials(
        database_path, application_id
    )
    application = AssessmentRepository(database_path).get_application(
        application_id
    )
    checkpoint_path = (
        Path(application.artefact_directory)
        / "assessments"
        / "checkpoint"
        / "assessment-result.json"
    ).as_posix()
    artefacts.write_json(
        checkpoint_path, assessment_result().model_dump(mode="json")
    )
    provenance = WorkCheckpointProvenance(
        model="gpt-original",
        provider="openai",
        response_ids=("response-original", "response-repair"),
        input_tokens=20,
        output_tokens=10,
        total_tokens=30,
        repair_attempted=True,
    )
    profile_sha256 = sha256_bytes(profile_path.read_bytes())
    instructions = service._instructions_path.read_text(encoding="utf-8")
    checkpoint_hashes: dict[str, str | None] = {
        "profile_sha256": profile_sha256,
        "jd_sha256": _hash_text(application.full_jd),
        "prompt_sha256": _hash_text(instructions),
        "schema_sha256": _schema_hash(),
    }
    repository = WorkRepository(database_path)
    repository.checkpoint(
        work_id,
        worker_token,
        "assessment_result",
        checkpoint_path,
        artefacts.sha256(checkpoint_path),
        hashes=checkpoint_hashes,
        provenance=provenance,
    )
    work = repository.get(work_id)

    execution = service.execute(
        application_id,
        "2026-08-07T10:00:00+00:00",
        work_id=work_id,
        worker_token=worker_token,
        resume_checkpoint=WorkCheckpoint(
            step=work.current_step,
            path=work.checkpoint_path or "",
            sha256=work.checkpoint_sha256 or "",
            hashes=checkpoint_hashes,
            provenance=work.checkpoint_provenance,
        ),
    )

    assert execution.result == assessment_result()
    assert fake.requests == []
    stored = AssessmentRepository(database_path).get(execution.assessment_id)
    assert stored["model"] == "gpt-original"
    assert stored["provider"] == "openai"
    assert stored["response_ids"] == ["response-original", "response-repair"]
    assert stored["input_tokens"] == 20
    assert stored["output_tokens"] == 10
    assert stored["total_tokens"] == 30
    assert stored["repair_attempted"] == 1


def test_assessment_rejects_blank_credentials_before_provider_or_profile_read(
    database_path: str, tmp_path: Path
) -> None:
    initialize_database(database_path)
    application_id = create_application(database_path)
    service, fake, profile_path, artefacts = build_service(
        database_path, tmp_path, assessment_result()
    )
    profile_path.unlink()

    with pytest.raises(ValueError, match="work ID"):
        service.execute(
            application_id,
            "2026-08-07T10:00:00+00:00",
            work_id=" ",
            worker_token="worker-a",
        )

    assert fake.requests == []
    assert list(artefacts.root.rglob("*")) == []


def test_assessment_completion_requires_current_worker_ownership(
    database_path: str, tmp_path: Path
) -> None:
    initialize_database(database_path)
    application_id = create_application(database_path)
    work_repository = WorkRepository(database_path)
    work = work_repository.active_for_application(
        application_id, WorkType.ASSESSMENT
    )
    assert work is not None
    work_repository.claim(
        work.id, "worker-a", "2026-08-07T09:01:00Z", lease_seconds=1
    )
    assert work_repository.recover_stale("2026-08-07T09:03:00Z") == 1
    work_repository.claim(work.id, "worker-b", "2026-08-07T09:04:00Z")
    service, fake, profile_path, artefacts = build_service(
        database_path, tmp_path, assessment_result()
    )
    profile_path.unlink()

    with pytest.raises(StaleWorkerError):
        service.execute(
            application_id,
            "2026-08-07T10:00:00+00:00",
            work_id=work.id,
            worker_token="worker-a",
        )

    assert assessment_count(database_path) == 0
    assert fake.requests == []
    assert list(artefacts.root.rglob("*.json")) == []


@pytest.mark.parametrize("credential_error", ("missing", "wrong-token"))
def test_assessment_preflight_rejects_unowned_work_before_private_input(
    database_path: str,
    tmp_path: Path,
    credential_error: str,
) -> None:
    initialize_database(database_path)
    application_id = create_application(database_path)
    work_repository = WorkRepository(database_path)
    work = work_repository.active_for_application(
        application_id, WorkType.ASSESSMENT
    )
    assert work is not None
    token = "worker-a"
    work_repository.claim(work.id, token, "2026-08-07T09:01:00Z")
    service, fake, profile_path, artefacts = build_service(
        database_path, tmp_path, assessment_result()
    )
    profile_path.unlink()

    work_id = "missing-work" if credential_error == "missing" else work.id
    worker_token = "worker-b" if credential_error == "wrong-token" else token
    expected_error = (
        AssessmentStateError
        if credential_error == "missing"
        else StaleWorkerError
    )

    with pytest.raises(expected_error):
        service.execute(
            application_id,
            "2026-08-07T10:00:00+00:00",
            work_id=work_id,
            worker_token=worker_token,
        )

    assert fake.requests == []
    assert assessment_count(database_path) == 0
    assert list(artefacts.root.rglob("*")) == []


@pytest.mark.parametrize(
    "credential_error", ("wrong-application", "wrong-type")
)
def test_assessment_preflight_rejects_wrong_application_or_work_type(
    database_path: str,
    tmp_path: Path,
    credential_error: str,
) -> None:
    initialize_database(database_path)
    application_id = create_application(database_path)
    work_repository = WorkRepository(database_path)
    work = work_repository.active_for_application(
        application_id, WorkType.ASSESSMENT
    )
    assert work is not None
    token = "worker-a"
    work_repository.claim(work.id, token, "2026-08-07T09:01:00Z")
    call_application_id = application_id
    call_work_id = work.id
    if credential_error == "wrong-application":
        call_application_id += 1
    else:
        completion = completed_assessment(
            database_path,
            application_id,
            token=token,
        )
        AssessmentRepository(database_path).add_completed(completion)
        generation = work_repository.active_for_application(
            application_id, WorkType.CV_GENERATION
        )
        assert generation is not None
        call_work_id = generation.id
        work_repository.claim(call_work_id, token, "2026-08-07T10:01:00Z")
    service, fake, profile_path, artefacts = build_service(
        database_path, tmp_path, assessment_result()
    )
    profile_path.unlink()

    with pytest.raises(AssessmentStateError):
        service.execute(
            call_application_id,
            "2026-08-07T10:00:00+00:00",
            work_id=call_work_id,
            worker_token=token,
        )

    assert fake.requests == []
    assert list(artefacts.root.rglob("*")) == []


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("score.final_score", Decimal(99)),
        ("score.supporting_alignment", Decimal(99)),
        ("score.mandatory_coverage", Decimal(99)),
        ("score.threshold", Decimal(99)),
        ("score.all_mandatory_matched", False),
        ("score.failed_hard_gates", (HardGateKind.SALARY_LOCATION,)),
        ("score.ambiguous_hard_gates", (HardGateKind.SALARY_LOCATION,)),
        ("score.outcome", AssessmentOutcome.SKILL_MISMATCH),
        ("model", "changed-model"),
        ("model_sha256", "3" * 64),
        ("schema_version", "changed-schema"),
        ("schema_sha256", "4" * 64),
        ("instruction_sha256", "5" * 64),
        ("taxonomy_version", "changed-taxonomy"),
        ("taxonomy_sha256", "6" * 64),
        ("profile_sha256", "7" * 64),
        ("jd_sha256", "8" * 64),
        ("provider", "changed-provider"),
        ("response_ids", ("changed-response",)),
        ("input_tokens", 11),
        ("output_tokens", 21),
        ("total_tokens", 31),
        ("repair_attempted", True),
        ("result_path", "changed-result.json"),
        ("result_sha256", "9" * 64),
        ("analysis_path", "changed-analysis.json"),
        ("analysis_sha256", "0" * 64),
        ("completed_at", "2026-08-07T10:00:01+00:00"),
    ),
)
def test_assessment_completion_replay_requires_exact_payload_atomically(
    database_path: str,
    field: str,
    value: object,
) -> None:
    initialize_database(database_path)
    application_id = create_application(database_path)
    completion = completed_assessment(database_path, application_id)
    repository = AssessmentRepository(database_path)
    repository.add_completed(completion)
    before = assessment_state_snapshot(database_path, application_id)

    if field.startswith("score."):
        score_changes: dict[str, Any] = {field.removeprefix("score."): value}
        changed = replace(
            completion,
            score=replace(completion.score, **score_changes),
        )
    else:
        completion_changes: dict[str, Any] = {field: value}
        changed = replace(completion, **completion_changes)
    with pytest.raises(AssessmentStateError, match="does not match"):
        repository.add_completed(changed)

    assert assessment_state_snapshot(database_path, application_id) == before
    repository.add_completed(completion)
    assert assessment_state_snapshot(database_path, application_id) == before


def test_assessment_completion_replay_rejects_missing_row_and_wrong_finalizer(
    database_path: str,
) -> None:
    initialize_database(database_path)
    application_id = create_application(database_path)
    completion = completed_assessment(database_path, application_id)
    work_repository = WorkRepository(database_path)
    with connect(database_path) as connection:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            """UPDATE work_items SET state = 'succeeded',
            completed_at = ?, worker_token = NULL, finalizer_token = ?
            WHERE id = ?""",
            (
                "2026-08-07T10:00:00.000000+00:00",
                completion.worker_token,
                completion.work_id,
            ),
        )
        connection.execute("PRAGMA ignore_check_constraints = OFF")
    repository = AssessmentRepository(database_path)

    with pytest.raises(AssessmentStateError, match="does not match"):
        repository.add_completed(completion)

    work = work_repository.get(completion.work_id)
    assert work.state.value == "succeeded"
    assert work.finalizer_token == completion.worker_token

    with pytest.raises(StaleWorkerError):
        repository.add_completed(
            replace(completion, worker_token="different-finalizer")
        )


def test_assessment_completion_replay_cannot_cross_work_results(
    database_path: str,
) -> None:
    initialize_database(database_path)
    application_id = create_application(database_path)
    repository = AssessmentRepository(database_path)
    work_repository = WorkRepository(database_path)

    first = completed_assessment(
        database_path, application_id, "assessment-a", "worker-a"
    )
    repository.add_completed(first)
    generation = work_repository.active_for_application(
        application_id, WorkType.CV_GENERATION
    )
    assert generation is not None
    work_repository.claim(generation.id, "cv-worker", "2026-08-07T10:01:00Z")
    work_repository.fail(
        generation.id,
        "cv-worker",
        RuntimeError("complete setup"),
        "2026-08-07T10:02:00Z",
    )

    second_work_id = work_repository.enqueue(
        application_id, WorkType.ASSESSMENT, "2026-08-07T10:03:00Z"
    )
    work_repository.claim(second_work_id, "worker-a", "2026-08-07T10:03:01Z")
    second = replace(
        completed_assessment(
            database_path, application_id, "assessment-b", "worker-a"
        ),
        completed_at="2026-08-07T10:04:00+00:00",
    )
    repository.add_completed(second)

    before = assessment_state_snapshot(database_path, application_id)
    cross_work_replay = replace(second, work_id=first.work_id)
    with pytest.raises(AssessmentStateError, match="does not match"):
        repository.add_completed(cross_work_replay)

    assert assessment_state_snapshot(database_path, application_id) == before
    repository.add_completed(first)
    assert assessment_state_snapshot(database_path, application_id) == before


def test_assessment_completion_race_cleans_artefacts_and_keeps_new_owner(
    database_path: str, tmp_path: Path
) -> None:
    initialize_database(database_path)
    application_id = create_application(database_path)
    work_repository = WorkRepository(database_path)
    work = work_repository.active_for_application(
        application_id, WorkType.ASSESSMENT
    )
    assert work is not None
    work_repository.claim(
        work.id, "worker-a", "2026-08-07T09:01:00Z", lease_seconds=1
    )

    def reclaim_work() -> None:
        assert work_repository.recover_stale("2026-08-07T09:03:00Z") == 1
        work_repository.claim(work.id, "worker-b", "2026-08-07T09:04:00Z")

    service, fake, _, artefacts = build_service(
        database_path,
        tmp_path,
        assessment_result(),
        effect=reclaim_work,
    )

    with pytest.raises(StaleWorkerError):
        service.execute(
            application_id,
            "2026-08-07T10:00:00+00:00",
            work_id=work.id,
            worker_token="worker-a",
        )

    current = work_repository.get(work.id)
    assert current.state.value == "running"
    assert current.worker_token == "worker-b"
    assert assessment_count(database_path) == 0
    assert (
        Repository(database_path).get_application(application_id)[
            "current_stage"
        ]
        == "Assessing"
    )
    with connect(database_path) as connection:
        assert (
            connection.execute(
                """SELECT COUNT(*) FROM work_items
            WHERE application_id = ? AND work_type = 'cv_generation'""",
                (application_id,),
            ).fetchone()[0]
            == 0
        )
    assert len(fake.requests) == 1
    assert list(artefacts.root.rglob("*.json")) == []


def test_durable_matched_assessment_enqueues_generation_atomically(
    database_path: str, tmp_path: Path
) -> None:
    initialize_database(database_path)
    application_id = create_application(database_path)
    work_repository = WorkRepository(database_path)
    work = work_repository.active_for_application(
        application_id, WorkType.ASSESSMENT
    )
    assert work is not None
    token = "worker-a"
    work_repository.claim(work.id, token, "2026-08-07T09:01:00Z")
    service, _, _, _ = build_service(
        database_path, tmp_path, assessment_result()
    )

    execution = service.execute(
        application_id,
        "2026-08-07T10:00:00+00:00",
        work_id=work.id,
        worker_token=token,
    )

    with connect(database_path) as connection:
        rows = connection.execute(
            """SELECT work_type, state, assessment_id FROM work_items
            WHERE application_id = ? ORDER BY work_type""",
            (application_id,),
        ).fetchall()
    assert execution.assessment_id
    assert [(row["work_type"], row["state"]) for row in rows] == [
        ("assessment", "succeeded"),
        ("cv_generation", "queued"),
    ]


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

    execution = run_assessment(
        service, database_path, application_id, "2026-08-07T10:00:00+00:00"
    )

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
        run_assessment(
            service,
            database_path,
            application_id,
            "2026-08-07T10:00:00+00:00",
        )

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
        run_assessment(
            service,
            database_path,
            application_id,
            "2026-08-07T10:00:00+00:00",
        )

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
        run_assessment(
            service,
            database_path,
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
        run_assessment(
            service,
            database_path,
            application_id,
            "2026-08-07T10:00:00+00:00",
        )

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
        run_assessment(
            service,
            database_path,
            application_id,
            "2026-08-07T10:00:00+00:00",
        )

    assert assessment_count(database_path) == 0
    assert list(store.root.rglob("*.json")) == []
    assert list(store.root.rglob("*.tmp-*")) == []


def test_profile_change_during_writes_rejects_stale_assessment(
    database_path: str, tmp_path: Path
) -> None:
    initialize_database(database_path)
    application_id = create_application(database_path)
    profile_path = tmp_path / "profile.md"
    replacements = 0

    def change_profile_on_first_replace(
        source: os.PathLike[str], target: os.PathLike[str]
    ) -> None:
        nonlocal replacements
        replacements += 1
        if replacements == 1:
            profile_path.write_text(
                "# Example Person\n\nChanged profile.", encoding="utf-8"
            )
        os.replace(source, target)

    store = ArtefactStore(
        tmp_path / "artefacts", replace=change_profile_on_first_replace
    )
    service, _, _, _ = build_service(
        database_path, tmp_path, assessment_result(), store=store
    )

    with pytest.raises(AssessmentInputChangedError, match="inputs changed"):
        run_assessment(
            service,
            database_path,
            application_id,
            "2026-08-07T10:00:00+00:00",
        )

    assert assessment_count(database_path) == 0
    assert list(store.root.rglob("*.json")) == []


def test_completed_assessment_rows_are_database_immutable(
    database_path: str, tmp_path: Path
) -> None:
    initialize_database(database_path)
    application_id = create_application(database_path)
    service, _, _, _ = build_service(
        database_path, tmp_path, assessment_result()
    )
    execution = run_assessment(
        service, database_path, application_id, "2026-08-07T10:00:00+00:00"
    )

    with (
        connect(database_path) as connection,
        pytest.raises(sqlite3.IntegrityError, match="immutable"),
    ):
        connection.execute(
            "UPDATE assessments SET final_score = 0 WHERE id = ?",
            (execution.assessment_id,),
        )
