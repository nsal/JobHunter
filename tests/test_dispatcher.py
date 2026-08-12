from __future__ import annotations

from pathlib import Path
from typing import Any

from app.database import connect, initialize_database
from app.repository import Repository
from app.settings import AiSettings, load_ai_settings
from app.work.dispatcher import (
    Dispatcher,
    ProcessLike,
    _isolate_worker_process_group,
)
from app.work.models import WorkState, WorkType
from app.work.repository import WorkRepository
from app.work.runner import WorkerRequest


class FakeProcess:
    """Small deterministic process double for dispatcher tests."""

    def __init__(self) -> None:
        self.alive = True
        self.exitcode: int | None = None
        self.started = False
        self.terminated = False
        self.killed = False

    def start(self) -> None:
        self.started = True

    def is_alive(self) -> bool:
        return self.alive

    def join(self, timeout: float | None = None) -> None:
        del timeout

    def terminate(self) -> None:
        self.terminated = True
        self.alive = False
        self.exitcode = -15

    def kill(self) -> None:
        self.killed = True
        self.alive = False
        self.exitcode = -9


class FakeWorkerSupervisor:
    """Records dispatcher ownership without using platform process APIs."""

    def __init__(self) -> None:
        self.assigned: list[ProcessLike] = []
        self.closed = False

    def assign(self, process: ProcessLike) -> None:
        self.assigned.append(process)

    def close(self) -> None:
        self.closed = True


def settings() -> AiSettings:
    """Load production-shaped settings with a short test poll interval."""
    return load_ai_settings(
        Path("config") / "ai.yaml",
        {
            "queue": {
                "poll_interval_seconds": 0.1,
                "heartbeat_interval_seconds": 1,
                "work_lease_seconds": 5,
            }
        },
    )


def queue_applications(database_path: str, count: int) -> list[int]:
    """Create applications whose initial assessment work is queued."""
    repository = Repository(database_path)
    return [
        repository.create_application(
            {
                "role": f"Developer {index}",
                "company": "Acme",
                "full_jd": "Build software.",
            },
            f"2026-01-01T00:00:0{index}",
        )
        for index in range(count)
    ]


def test_dispatcher_refills_a_slot_immediately_after_child_exit(
    database_path: str,
) -> None:
    initialize_database(database_path)
    queue_applications(database_path, 4)
    processes: list[FakeProcess] = []

    def factory(request: object) -> ProcessLike:
        del request
        process = FakeProcess()
        processes.append(process)
        return process

    dispatcher = Dispatcher(
        database_path,
        ".",
        settings(),
        clock=lambda: "2026-01-01T00:00:10+00:00",
        process_factory=factory,
    )

    assert dispatcher.run_once()
    assert dispatcher.active_count == 3
    assert len(processes) == 3

    processes[0].alive = False
    processes[0].exitcode = 0
    assert dispatcher.run_once()
    assert dispatcher.active_count == 3
    assert len(processes) == 4


def test_dispatcher_refill_never_exceeds_configured_concurrency(
    database_path: str,
) -> None:
    initialize_database(database_path)
    queue_applications(database_path, 6)
    processes: list[FakeProcess] = []

    def factory(request: object) -> ProcessLike:
        del request
        process = FakeProcess()
        processes.append(process)
        return process

    dispatcher = Dispatcher(
        database_path,
        ".",
        settings(),
        clock=lambda: "2026-01-01T00:00:10+00:00",
        process_factory=factory,
    )

    assert dispatcher.run_once()
    assert dispatcher.active_count == 3
    processes[0].alive = False
    processes[0].exitcode = 0
    processes[1].alive = False
    processes[1].exitcode = 0

    assert dispatcher.run_once()
    assert dispatcher.active_count == 3
    assert len(processes) == 5


def test_dispatcher_does_not_sleep_when_work_or_processes_change(
    database_path: str,
) -> None:
    initialize_database(database_path)
    queue_applications(database_path, 1)
    sleeps: list[float] = []
    process = FakeProcess()

    dispatcher = Dispatcher(
        database_path,
        ".",
        settings(),
        clock=lambda: "2026-01-01T00:00:10+00:00",
        sleeper=sleeps.append,
        process_factory=lambda request: process,
    )

    assert dispatcher.run_once()
    assert sleeps == []
    process.alive = False
    process.exitcode = 0
    assert dispatcher.run_once()
    assert sleeps == []


def test_dispatcher_does_not_register_an_unstarted_failed_child(
    database_path: str,
) -> None:
    initialize_database(database_path)
    queue_applications(database_path, 1)

    class StartFailure(FakeProcess):
        def start(self) -> None:
            raise OSError("spawn unavailable")

        def join(self, timeout: float | None = None) -> None:
            del timeout
            raise AssertionError("unstarted processes cannot be joined")

    dispatcher = Dispatcher(
        database_path,
        ".",
        settings(),
        clock=lambda: "2026-01-01T00:00:10+00:00",
        process_factory=lambda request: StartFailure(),
    )

    assert dispatcher.run_once()
    assert dispatcher.active_count == 0
    assert not dispatcher.run_once()


def test_dispatcher_does_not_replace_a_reclaimed_live_child(
    database_path: str,
) -> None:
    initialize_database(database_path)
    queue_applications(database_path, 3)
    queue = settings().queue.model_copy(update={"concurrency": 2})
    dispatcher_settings = settings().model_copy(update={"queue": queue})
    processes: list[FakeProcess] = []
    requests: list[WorkerRequest] = []

    class TerminationResistant(FakeProcess):
        def terminate(self) -> None:
            self.terminated = True

    def factory(request: WorkerRequest) -> ProcessLike:
        requests.append(request)
        process: FakeProcess = (
            TerminationResistant() if not processes else FakeProcess()
        )
        processes.append(process)
        return process

    now = ["2026-01-01T00:00:10+00:00"]
    dispatcher = Dispatcher(
        database_path,
        ".",
        dispatcher_settings,
        clock=lambda: now[0],
        process_factory=factory,
    )

    assert dispatcher.run_once()
    assert len(requests) == 2
    first_request = requests[0]
    second_request = requests[1]
    WorkRepository(database_path).heartbeat(
        second_request.work_id,
        second_request.worker_token,
        "2026-01-01T00:00:20+00:00",
        lease_seconds=100,
    )
    processes[1].alive = False
    processes[1].exitcode = 0

    now[0] = "2026-01-01T00:00:20+00:00"
    assert dispatcher.run_once()
    assert dispatcher.active_count == 2
    assert len(requests) == 3
    assert [request.work_id for request in requests].count(
        first_request.work_id
    ) == 1
    assert processes[0].terminated
    assert not processes[0].killed


def test_dispatcher_graceful_stop_does_not_orphan_children(
    database_path: str,
) -> None:
    initialize_database(database_path)
    application_id = queue_applications(database_path, 1)[0]
    process = FakeProcess()
    stop = type("Stop", (), {"is_set": lambda self: True})()
    dispatcher = Dispatcher(
        database_path,
        ".",
        settings(),
        clock=lambda: "2026-01-01T00:00:10+00:00",
        process_factory=lambda request: process,
        shutdown_timeout_seconds=0.01,
    )

    dispatcher.run_once()
    dispatcher.run(stop)

    assert process.terminated
    assert not process.is_alive()
    with connect(database_path) as connection:
        work_id = str(
            connection.execute(
                "SELECT id FROM work_items WHERE application_id = ?",
                (application_id,),
            ).fetchone()[0]
        )
    work = WorkRepository(database_path).get(work_id)
    assert work.state is WorkState.QUEUED
    assert work.work_type is WorkType.ASSESSMENT


def test_dispatcher_stop_signal_wakes_long_idle_poll(
    database_path: str,
) -> None:
    initialize_database(database_path)
    waits: list[float] = []

    class WakeableStop:
        def is_set(self) -> bool:
            return bool(waits)

        def wait(self, timeout: float | None = None) -> bool:
            assert timeout is not None
            waits.append(timeout)
            return True

    dispatcher = Dispatcher(
        database_path,
        ".",
        settings().model_copy(
            update={
                "queue": settings().queue.model_copy(
                    update={"poll_interval_seconds": 60.0}
                )
            }
        ),
        sleeper=lambda seconds: (_ for _ in ()).throw(
            AssertionError("idle polling should use the stop signal")
        ),
    )

    assert dispatcher.run(WakeableStop())
    assert waits == [60.0]


def test_dispatcher_escalates_a_worker_that_ignores_terminate(
    database_path: str,
) -> None:
    initialize_database(database_path)
    queue_applications(database_path, 1)

    class TerminationResistant(FakeProcess):
        def terminate(self) -> None:
            self.terminated = True

    process = TerminationResistant()
    stop = type("Stop", (), {"is_set": lambda self: True})()
    dispatcher = Dispatcher(
        database_path,
        ".",
        settings(),
        process_factory=lambda request: process,
        shutdown_timeout_seconds=0.01,
    )

    dispatcher.run_once()
    assert dispatcher.run(stop)
    assert process.terminated
    assert process.killed
    assert dispatcher.active_count == 0


def test_dispatcher_keeps_a_process_that_survives_hard_cleanup(
    database_path: str,
) -> None:
    initialize_database(database_path)
    queue_applications(database_path, 1)

    class Unstoppable(FakeProcess):
        def terminate(self) -> None:
            self.terminated = True

        def kill(self) -> None:
            self.killed = True

    process = Unstoppable()
    stop = type("Stop", (), {"is_set": lambda self: True})()
    dispatcher = Dispatcher(
        database_path,
        ".",
        settings(),
        process_factory=lambda request: process,
        shutdown_timeout_seconds=0.01,
    )

    dispatcher.run_once()
    assert not dispatcher.run(stop)
    assert dispatcher.active_count == 1
    assert process.is_alive()


def test_dispatcher_assigns_and_releases_platform_worker_ownership(
    database_path: str,
) -> None:
    initialize_database(database_path)
    queue_applications(database_path, 1)
    process = FakeProcess()
    supervisor = FakeWorkerSupervisor()
    stop = type("Stop", (), {"is_set": lambda self: True})()
    dispatcher = Dispatcher(
        database_path,
        ".",
        settings(),
        process_factory=lambda request: process,
        worker_supervisor_factory=lambda: supervisor,
        shutdown_timeout_seconds=0.01,
    )

    dispatcher.run_once()
    assert supervisor.assigned == [process]
    assert dispatcher.run(stop)
    assert supervisor.closed


def test_dispatcher_isolates_spawned_workers_in_its_own_process_group(
    monkeypatch: Any,
) -> None:
    calls: list[bool] = []

    monkeypatch.setattr(
        "app.work.dispatcher.multiprocessing.parent_process",
        lambda: object(),
    )
    monkeypatch.setattr(
        "app.work.dispatcher.os.setsid", lambda: calls.append(True)
    )

    _isolate_worker_process_group()

    assert calls == [True]
