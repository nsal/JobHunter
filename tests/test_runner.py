from __future__ import annotations

import multiprocessing
import sqlite3
import threading
from pathlib import Path
from typing import Any, cast

from app.assessment.service import AssessmentService
from app.cv.generator import CvGenerationService
from app.database import initialize_database
from app.repository import Repository
from app.settings import AiSettings, load_ai_settings
from app.work.repository import WorkRepository
from app.work.runner import (
    WorkerRequest,
    WorkerResources,
    WorkerRunner,
    _Heartbeat,
)


class FakeAssessmentService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def execute(
        self,
        application_id: int,
        completed_at: str,
        **kwargs: Any,
    ) -> None:
        self.calls.append(
            {
                "application_id": application_id,
                "completed_at": completed_at,
                **kwargs,
            }
        )


class FailingAssessmentService(FakeAssessmentService):
    def execute(
        self,
        application_id: int,
        completed_at: str,
        **kwargs: Any,
    ) -> None:
        del application_id, completed_at, kwargs
        raise RuntimeError("provider response contains a secret")


def spawned_terminal_worker(
    database_path: str,
    work_id: str,
    worker_token: str,
) -> None:
    """Open SQLite only in a spawned child and finalize a safe failure."""
    WorkRepository(database_path).fail(
        work_id,
        worker_token,
        RuntimeError("deterministic smoke failure"),
        "2026-01-01T00:00:02+00:00",
    )


def ai_settings() -> AiSettings:
    """Load settings without constructing a provider in runner tests."""
    return load_ai_settings(Path("config") / "ai.yaml")


def request_for_assessment(database_path: str) -> WorkerRequest:
    """Create and claim one assessment work item."""
    initialize_database(database_path)
    application_id = Repository(database_path).create_application(
        {
            "role": "Developer",
            "company": "Acme",
            "full_jd": "Build software.",
        },
        "2026-01-01T00:00:00",
    )
    work_id = next(
        item.id
        for item in WorkRepository(database_path).list_available(
            "2026-01-01T00:00:00+00:00", 1
        )
        if item.application_id == application_id
    )
    token = "worker-token"
    WorkRepository(database_path).claim(
        work_id, token, "2026-01-01T00:00:01+00:00"
    )
    return WorkerRequest(database_path, ".", work_id, token)


def test_runner_constructs_resources_in_child_boundary_and_passes_ids(
    database_path: str,
) -> None:
    request = request_for_assessment(database_path)
    repository = WorkRepository(database_path)
    assessment = FakeAssessmentService()
    cv = FakeAssessmentService()
    resources = WorkerResources(
        ai_settings(),
        repository,
        cast(AssessmentService, assessment),
        cast(CvGenerationService, cv),
    )
    runner = WorkerRunner(
        resource_factory=lambda child_request: resources,
        clock=lambda: "2026-01-01T00:00:02+00:00",
    )

    assert runner.run(request) == 0
    assert assessment.calls[0]["application_id"] == 1
    assert assessment.calls[0]["work_id"] == request.work_id
    assert assessment.calls[0]["worker_token"] == request.worker_token
    assert callable(assessment.calls[0]["checkpoint"])


def test_runner_persists_redacted_failure_and_allows_retry(
    database_path: str,
) -> None:
    request = request_for_assessment(database_path)
    repository = WorkRepository(database_path)
    resources = WorkerResources(
        ai_settings(),
        repository,
        cast(AssessmentService, FailingAssessmentService()),
        cast(CvGenerationService, FakeAssessmentService()),
    )

    assert (
        WorkerRunner(
            resource_factory=lambda child_request: resources,
            clock=lambda: "2026-01-01T00:00:02+00:00",
        ).run(request)
        == 0
    )
    work = repository.get(request.work_id)
    assert work.state.value == "failed" or work.state.value == "queued"
    assert work.error_message != "provider response contains a secret"


def test_spawned_child_opens_independent_sqlite_and_finishes(
    database_path: str,
) -> None:
    request = request_for_assessment(database_path)
    context = multiprocessing.get_context("spawn")
    process = context.Process(
        target=spawned_terminal_worker,
        args=(database_path, request.work_id, request.worker_token),
    )
    process.start()
    process.join(timeout=10)

    assert not process.is_alive()
    assert process.exitcode == 0
    assert WorkRepository(database_path).get(request.work_id).state.value == (
        "failed"
    )


class HeartbeatDouble:
    def __init__(self) -> None:
        self.called = threading.Event()
        self.leases: list[float] = []
        self.fail = False
        self.errors: list[BaseException] = []

    def heartbeat(
        self,
        work_id: str,
        worker_token: str,
        now: str,
        lease_seconds: float = 60,
    ) -> None:
        del work_id, worker_token, now
        self.leases.append(lease_seconds)
        self.called.set()
        if self.errors:
            raise self.errors.pop(0)
        if self.fail:
            raise RuntimeError("ownership lost")


def test_heartbeat_uses_configured_work_lease() -> None:
    repository = HeartbeatDouble()
    heartbeat = _Heartbeat(
        cast(Any, repository),
        WorkerRequest("/tmp/db", ".", "work", "token"),
        0.001,
        37.5,
        lambda: "2026-01-01T00:00:01+00:00",
    )

    heartbeat.start()
    assert repository.called.wait(1)
    heartbeat.stop()

    assert repository.leases == [37.5]


def test_heartbeat_stops_after_ownership_loss() -> None:
    repository = HeartbeatDouble()
    repository.fail = True
    heartbeat = _Heartbeat(
        cast(Any, repository),
        WorkerRequest("/tmp/db", ".", "work", "token"),
        0.001,
        37.5,
        lambda: "2026-01-01T00:00:01+00:00",
    )

    heartbeat.start()
    assert repository.called.wait(1)
    heartbeat.stop()
    calls = len(repository.leases)

    threading.Event().wait(0.01)
    assert len(repository.leases) == calls


def test_heartbeat_retries_transient_sqlite_failure() -> None:
    repository = HeartbeatDouble()
    repository.errors = [sqlite3.OperationalError("database is locked")]
    heartbeat = _Heartbeat(
        cast(Any, repository),
        WorkerRequest("/tmp/db", ".", "work", "token"),
        0.001,
        37.5,
        lambda: "2026-01-01T00:00:01+00:00",
    )

    heartbeat.start()
    assert repository.called.wait(1)
    repository.called.clear()
    assert repository.called.wait(1)
    heartbeat.stop()

    assert repository.leases[:2] == [37.5, 37.5]
