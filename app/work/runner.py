"""Spawn-safe construction and execution of one durable work item."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from app.database import initialize_database
from app.repository import Repository
from app.settings import AiSettings, load_ai_settings, validate_private_inputs
from app.work.models import (
    WorkCheckpoint,
    WorkCheckpointProvenance,
    WorkState,
    WorkType,
)
from app.work.repository import WorkRepository

if TYPE_CHECKING:
    from app.assessment.service import AssessmentService
    from app.cv.generator import CvGenerationService


@dataclass(frozen=True)
class WorkerRequest:
    """The complete child-process payload.

    Application data is deliberately not included.  The child looks up the
    work row using the opaque work ID and proves ownership with its token.
    """

    database_path: str
    project_root: str
    work_id: str
    worker_token: str


@dataclass(frozen=True)
class WorkerResources:
    """Resources constructed inside a worker process."""

    settings: AiSettings
    repository: WorkRepository
    assessment_service: AssessmentService
    cv_service: CvGenerationService


ResourceFactory = Callable[[WorkerRequest], WorkerResources]
Clock = Callable[[], str]


def utc_now() -> str:
    """Return a canonical timestamp for durable work transitions."""
    return datetime.now(UTC).isoformat()


def build_worker_resources(request: WorkerRequest) -> WorkerResources:
    """Build every database, provider, private-input, and artefact resource.

    This function is a process entry boundary: callers pass only paths and
    opaque credentials, and no parent-owned SQLite or provider object crosses
    the spawn boundary.
    """
    root = Path(request.project_root).resolve()
    database_path = Path(request.database_path).resolve()
    from app.ai.providers.factory import create_structured_generator
    from app.artefacts import ArtefactStore
    from app.assessment.service import AssessmentService
    from app.assessments import AssessmentRepository
    from app.cv.generator import CvGenerationService, default_word_writer
    from app.cv_generations import CvGenerationRepository

    initialize_database(database_path)
    settings = load_ai_settings(root / "config" / "ai.yaml")
    private_inputs = validate_private_inputs(root)
    artefacts = ArtefactStore(root / "private" / "artefacts")
    generator = create_structured_generator(settings, database_path)
    repository = WorkRepository(database_path)
    assessment_service = AssessmentService(
        AssessmentRepository(database_path),
        artefacts,
        generator,
        settings,
        private_inputs.profile_path,
    )
    cv_service = CvGenerationService(
        CvGenerationRepository(database_path),
        artefacts,
        generator,
        default_word_writer(artefacts),
        settings,
        private_inputs,
    )
    return WorkerResources(
        settings=settings,
        repository=repository,
        assessment_service=assessment_service,
        cv_service=cv_service,
    )


class _Heartbeat:
    """Refresh a work lease without sharing service resources."""

    def __init__(
        self,
        repository: WorkRepository,
        request: WorkerRequest,
        interval_seconds: float,
        lease_seconds: float,
        clock: Clock,
    ) -> None:
        self._repository = repository
        self._request = request
        self._interval_seconds = interval_seconds
        self._lease_seconds = lease_seconds
        self._clock = clock
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="jobhunter-heartbeat",
            daemon=True,
        )

    def start(self) -> None:
        """Start the lease refresher."""
        self._thread.start()

    def stop(self) -> None:
        """Stop and join the lease refresher."""
        self._stop.set()
        self._thread.join(timeout=max(self._interval_seconds, 1.0) + 1.0)

    def _run(self) -> None:
        while not self._stop.wait(self._interval_seconds):
            try:
                self._repository.heartbeat(
                    self._request.work_id,
                    self._request.worker_token,
                    self._clock(),
                    lease_seconds=self._lease_seconds,
                )
            except sqlite3.OperationalError:
                # SQLite contention is transient; retry on the next scheduled
                # interval while the worker still owns the durable lease.
                continue
            except Exception:  # noqa: BLE001 - ownership loss stops heartbeats.
                # Ownership loss is handled by the domain finalizer or the
                # outer failure path.  A heartbeat thread must never expose
                # private/provider exception text or kill the worker process.
                return


class WorkerRunner:
    """Run one claimed assessment or CV-generation work item."""

    def __init__(
        self,
        resource_factory: ResourceFactory = build_worker_resources,
        clock: Clock = utc_now,
    ) -> None:
        self._resource_factory = resource_factory
        self._clock = clock

    def run(self, request: WorkerRequest) -> int:
        """Execute work and persist a safe failure when execution fails."""
        repository = WorkRepository(Path(request.database_path).resolve())
        heartbeat: _Heartbeat | None = None
        try:
            resources = self._resource_factory(request)
            if (
                Path(resources.repository.database_path).resolve()
                != Path(repository.database_path).resolve()
            ):
                raise ValueError("Worker resource database does not match.")
            work = resources.repository.get(request.work_id)
            if (
                work.state is not WorkState.RUNNING
                or work.worker_token != request.worker_token
            ):
                raise RuntimeError("Worker token is no longer current.")
            heartbeat = _Heartbeat(
                resources.repository,
                request,
                resources.settings.queue.heartbeat_interval_seconds,
                resources.settings.queue.work_lease_seconds,
                self._clock,
            )
            heartbeat.start()

            resume_checkpoint = None
            if work.checkpoint_path and work.checkpoint_sha256:
                resume_checkpoint = WorkCheckpoint(
                    step=work.current_step,
                    path=work.checkpoint_path,
                    sha256=work.checkpoint_sha256,
                    hashes={
                        "profile_sha256": work.profile_sha256,
                        "jd_sha256": work.jd_sha256,
                        "assessment_result_sha256": (
                            work.assessment_result_sha256
                        ),
                        "role_sha256": work.role_sha256,
                        "prompt_sha256": work.prompt_sha256,
                        "schema_sha256": work.schema_sha256,
                        "template_sha256": work.template_sha256,
                        "layout_sha256": work.layout_sha256,
                    },
                    provenance=work.checkpoint_provenance,
                )

            def completion_clock() -> str:
                """Stop lease renewal before choosing finalization time."""
                if heartbeat is not None:
                    heartbeat.stop()
                return self._clock()

            def checkpoint(
                step: str,
                path: str,
                sha256: str,
                hashes: dict[str, str | None],
                provenance: WorkCheckpointProvenance,
            ) -> None:
                resources.repository.checkpoint(
                    request.work_id,
                    request.worker_token,
                    step,
                    path,
                    sha256,
                    hashes=hashes,
                    provenance=provenance,
                )

            if work.work_type is WorkType.ASSESSMENT:
                resources.assessment_service.execute(
                    work.application_id,
                    self._clock(),
                    expected_profile_sha256=work.profile_sha256,
                    expected_jd_sha256=work.jd_sha256,
                    work_id=request.work_id,
                    worker_token=request.worker_token,
                    checkpoint=checkpoint,
                    completion_clock=completion_clock,
                    resume_checkpoint=resume_checkpoint,
                )
            elif work.work_type is WorkType.CV_GENERATION:
                if work.assessment_id is None:
                    raise RuntimeError("CV work has no assessment.")
                resources.cv_service.execute(
                    work.application_id,
                    work.assessment_id,
                    self._clock(),
                    allow_mismatch=True,
                    work_id=request.work_id,
                    worker_token=request.worker_token,
                    checkpoint=checkpoint,
                    completion_clock=completion_clock,
                    resume_checkpoint=resume_checkpoint,
                )
                Repository(request.database_path).promote_completed_generation(
                    work.application_id
                )
            else:
                raise RuntimeError("Unsupported work type.")
            return 0
        except Exception as error:  # noqa: BLE001 - worker failures are durable.
            try:
                repository.fail(
                    request.work_id,
                    request.worker_token,
                    error,
                    self._clock(),
                )
            except Exception:  # noqa: BLE001 - stale ownership is recoverable.
                # The owner may have been reclaimed while this process was
                # failing.  The dispatcher will retain the durable state.
                return 1
            return 0
        finally:
            if heartbeat is not None:
                heartbeat.stop()


def worker_main(request: WorkerRequest) -> None:
    """Multiprocessing target for a spawned worker."""
    raise SystemExit(WorkerRunner().run(request))
