"""Supervised SQLite work dispatcher with spawn-isolated children."""

from __future__ import annotations

import ctypes
import multiprocessing
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from app.ai.providers.base import ProviderErrorCode, StructuredGenerationError
from app.database import initialize_database
from app.settings import AiSettings
from app.work.models import WorkState
from app.work.repository import WorkRepository, WorkStateError
from app.work.runner import WorkerRequest, worker_main


class ProcessLike(Protocol):
    """Small process surface used by the dispatcher and its fakes."""

    @property
    def exitcode(self) -> int | None:
        """Return the child exit code when it has exited."""

    def start(self) -> None:
        """Start the child."""

    def is_alive(self) -> bool:
        """Return whether the child is still running."""

    def join(self, timeout: float | None = None) -> None:
        """Wait for the child."""

    def terminate(self) -> None:
        """Request child termination."""

    def kill(self) -> None:
        """Force the child to stop when cooperative termination fails."""


class StopSignal(Protocol):
    """Protocol shared by threading and multiprocessing events."""

    def is_set(self) -> bool:
        """Return whether shutdown was requested."""

    def wait(self, timeout: float | None = None) -> bool:
        """Wait until shutdown is requested or the timeout elapses."""


Clock = Callable[[], str]
Sleeper = Callable[[float], None]
ProcessFactory = Callable[[WorkerRequest], ProcessLike]
FORCED_CLEANUP_TIMEOUT_SECONDS = 1.0
HARD_CLEANUP_TIMEOUT_SECONDS = 1.0
SUPERVISION_MARGIN_SECONDS = 1.0


def dispatcher_supervision_timeout(cooperative_seconds: float) -> float:
    """Return the parent budget for the dispatcher's full shutdown."""
    if cooperative_seconds <= 0:
        raise ValueError("Shutdown timeout must be positive.")
    return (
        cooperative_seconds
        + FORCED_CLEANUP_TIMEOUT_SECONDS
        + HARD_CLEANUP_TIMEOUT_SECONDS
        + SUPERVISION_MARGIN_SECONDS
    )


@dataclass
class _Child:
    """Dispatcher-owned metadata for one spawned child."""

    request: WorkerRequest
    process: ProcessLike


class WorkerSupervisor(Protocol):
    """Own worker processes for parent-death cleanup on one platform."""

    def assign(self, process: ProcessLike) -> None:
        """Add one started worker to the supervisor's lifecycle boundary."""

    def close(self) -> None:
        """Release ownership after all supervised workers have stopped."""


class _IoCounters(ctypes.Structure):
    """Win32 IO_COUNTERS layout for extended Job Object limits."""

    _fields_ = [
        ("read_operation_count", ctypes.c_ulonglong),
        ("write_operation_count", ctypes.c_ulonglong),
        ("other_operation_count", ctypes.c_ulonglong),
        ("read_transfer_count", ctypes.c_ulonglong),
        ("write_transfer_count", ctypes.c_ulonglong),
        ("other_transfer_count", ctypes.c_ulonglong),
    ]


class _JobBasicLimitInformation(ctypes.Structure):
    """Win32 JOBOBJECT_BASIC_LIMIT_INFORMATION layout."""

    _fields_ = [
        ("per_process_user_time_limit", ctypes.c_longlong),
        ("per_job_user_time_limit", ctypes.c_longlong),
        ("limit_flags", ctypes.c_ulong),
        ("minimum_working_set_size", ctypes.c_size_t),
        ("maximum_working_set_size", ctypes.c_size_t),
        ("active_process_limit", ctypes.c_ulong),
        ("affinity", ctypes.c_size_t),
        ("priority_class", ctypes.c_ulong),
        ("scheduling_class", ctypes.c_ulong),
    ]


class _JobExtendedLimitInformation(ctypes.Structure):
    """Win32 JOBOBJECT_EXTENDED_LIMIT_INFORMATION layout."""

    _fields_ = [
        ("basic_limit_information", _JobBasicLimitInformation),
        ("io_info", _IoCounters),
        ("process_memory_limit", ctypes.c_size_t),
        ("job_memory_limit", ctypes.c_size_t),
        ("peak_process_memory_used", ctypes.c_size_t),
        ("peak_job_memory_used", ctypes.c_size_t),
    ]


_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001


class _WindowsWorkerJob:
    """Kill all assigned workers when the dispatcher process exits."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise RuntimeError("Windows worker jobs require Windows.")
        library_loader: Any = ctypes.__dict__["WinDLL"]
        self._kernel32: Any = library_loader("kernel32", use_last_error=True)
        self._configure_api()
        self._handle = self._kernel32.CreateJobObjectW(None, None)
        if not self._handle:
            raise OSError("Could not create the Windows worker job.")
        limits = _JobExtendedLimitInformation()
        limits.basic_limit_information.limit_flags = (
            _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )
        if not self._kernel32.SetInformationJobObject(
            self._handle,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ):
            self.close()
            raise OSError("Could not configure the Windows worker job.")

    def _configure_api(self) -> None:
        self._kernel32.CreateJobObjectW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_wchar_p,
        ]
        self._kernel32.CreateJobObjectW.restype = ctypes.c_void_p
        self._kernel32.SetInformationJobObject.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_ulong,
        ]
        self._kernel32.SetInformationJobObject.restype = ctypes.c_int
        self._kernel32.AssignProcessToJobObject.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        self._kernel32.AssignProcessToJobObject.restype = ctypes.c_int
        self._kernel32.OpenProcess.argtypes = [
            ctypes.c_ulong,
            ctypes.c_int,
            ctypes.c_ulong,
        ]
        self._kernel32.OpenProcess.restype = ctypes.c_void_p
        self._kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        self._kernel32.CloseHandle.restype = ctypes.c_int

    def assign(self, process: ProcessLike) -> None:
        """Associate one started worker with this dispatcher's Job Object."""
        process_id = getattr(process, "pid", None)
        if not isinstance(process_id, int) or process_id <= 0:
            raise ValueError("Started Windows workers require a process ID.")
        access = _PROCESS_SET_QUOTA | _PROCESS_TERMINATE
        process_handle = self._kernel32.OpenProcess(access, False, process_id)
        if not process_handle:
            raise OSError("Could not open the Windows worker process.")
        try:
            if not self._kernel32.AssignProcessToJobObject(
                self._handle, process_handle
            ):
                raise OSError("Could not assign the Windows worker process.")
        finally:
            self._kernel32.CloseHandle(process_handle)

    def close(self) -> None:
        """Close the final Job Object handle and end any remaining workers."""
        if self._handle:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None


def _create_worker_supervisor() -> WorkerSupervisor | None:
    """Return the platform owner that prevents orphaned worker processes."""
    return _WindowsWorkerJob() if os.name == "nt" else None


WorkerSupervisorFactory = Callable[[], WorkerSupervisor | None]


def utc_now() -> str:
    """Return a timestamp suitable for work repository operations."""
    return datetime.now(UTC).isoformat()


def spawn_process(request: WorkerRequest) -> ProcessLike:
    """Create a worker with explicit multiprocessing spawn semantics."""
    context = multiprocessing.get_context("spawn")
    process = context.Process(target=worker_main, args=(request,))
    process.daemon = True
    return process


def _isolate_worker_process_group() -> None:
    """Make a spawned dispatcher the leader of its worker process group.

    The launcher can then signal the dispatcher and every worker together if
    cooperative shutdown times out.  Direct in-process use of the dispatcher
    deliberately keeps its caller's process group unchanged.
    """
    if os.name == "posix" and multiprocessing.parent_process() is not None:
        os.setsid()


def _worker_process_error(message: str) -> StructuredGenerationError:
    """Build a safe retryable diagnostic without process/provider details."""
    return StructuredGenerationError(
        ProviderErrorCode.SERVER,
        message,
        retryable=True,
    )


class Dispatcher:
    """Claim queued work and supervise at most the configured child count."""

    def __init__(
        self,
        database_path: str | Path,
        project_root: str | Path,
        settings: AiSettings,
        *,
        clock: Clock = utc_now,
        sleeper: Sleeper = time.sleep,
        process_factory: ProcessFactory = spawn_process,
        worker_supervisor_factory: WorkerSupervisorFactory = (
            _create_worker_supervisor
        ),
        shutdown_timeout_seconds: float = 10.0,
    ) -> None:
        if shutdown_timeout_seconds <= 0:
            raise ValueError("Shutdown timeout must be positive.")
        self.database_path = Path(database_path)
        self.project_root = Path(project_root).resolve()
        self.settings = settings
        self._clock = clock
        self._sleep = sleeper
        self._process_factory = process_factory
        self._worker_supervisor = worker_supervisor_factory()
        self._shutdown_timeout_seconds = shutdown_timeout_seconds
        initialize_database(self.database_path)
        self._repository = WorkRepository(self.database_path)
        self._children: dict[str, _Child] = {}

    @property
    def active_count(self) -> int:
        """Return the number of claimed children currently supervised."""
        return len(self._children)

    def _record_failure(self, request: WorkerRequest, message: str) -> None:
        """Persist a safe process diagnostic when ownership still exists."""
        try:
            self._repository.fail(
                request.work_id,
                request.worker_token,
                _worker_process_error(message),
                self._clock(),
            )
        except Exception:  # noqa: BLE001 - stale ownership is a normal race.
            # The child may have finalized or a stale recovery may have
            # replaced its token.  The durable owner decides the final state.
            return

    def _reap(self, failure_message: str | None = None) -> bool:
        """Remove exited children and diagnose incomplete normal exits."""
        activity = False
        for work_id, child in tuple(self._children.items()):
            if child.process.is_alive():
                continue
            child.process.join(timeout=0)
            try:
                work = self._repository.get(work_id)
            except Exception:  # noqa: BLE001 - diagnostics must remain safe.
                work = None
            if work is not None and work.state is WorkState.RUNNING:
                self._record_failure(
                    child.request,
                    failure_message
                    or "Worker process exited before completing work.",
                )
            self._children.pop(work_id, None)
            activity = True
        return activity

    def _stop_reclaimed_children(self) -> bool:
        """Stop children whose durable lease ownership was reclaimed."""
        activity = False
        for work_id, child in tuple(self._children.items()):
            try:
                work = self._repository.get(work_id)
            except Exception:  # noqa: BLE001, S112 - child may disappear.
                continue
            if (
                work.state is WorkState.RUNNING
                and work.worker_token == child.request.worker_token
            ):
                continue
            if child.process.is_alive():
                child.process.terminate()
                child.process.join(timeout=0.5)
            if child.process.is_alive():
                continue
            self._children.pop(work_id, None)
            activity = True
        return activity

    def _recover(self) -> bool:
        """Recover expired leases and remove their obsolete children."""
        recovered = self._repository.recover_stale(self._clock())
        stopped = self._stop_reclaimed_children()
        return bool(recovered or stopped)

    def _fill_slots(self) -> bool:
        """Claim and spawn work until the configured concurrency is full."""
        slots = self.settings.queue.concurrency - len(self._children)
        if slots <= 0:
            return False
        activity = False
        candidate_limit = slots + len(self._children)
        for candidate in self._repository.list_available(
            self._clock(), candidate_limit
        ):
            if len(self._children) >= self.settings.queue.concurrency:
                break
            if candidate.id in self._children:
                continue
            token = uuid4().hex
            try:
                claimed = self._repository.claim(
                    candidate.id,
                    token,
                    self._clock(),
                    self.settings.queue.work_lease_seconds,
                )
            except WorkStateError:
                continue
            request = WorkerRequest(
                database_path=str(self.database_path),
                project_root=str(self.project_root),
                work_id=claimed.id,
                worker_token=token,
            )
            process: ProcessLike | None = None
            started = False
            try:
                process = self._process_factory(request)
                process.start()
                started = True
                if self._worker_supervisor is not None:
                    self._worker_supervisor.assign(process)
            except Exception:  # noqa: BLE001 - spawn failures are persisted safely.
                if process is not None and started:
                    self._children[claimed.id] = _Child(request, process)
                self._record_failure(
                    request,
                    "Worker process could not be started.",
                )
                activity = True
                continue
            self._children[claimed.id] = _Child(request, process)
            activity = True
        return activity

    def run_once(self, stopping: bool = False) -> bool:
        """Advance supervision once and report whether an event occurred."""
        activity = self._reap()
        activity = self._recover() or activity
        if not stopping:
            activity = self._fill_slots() or activity
        return activity

    def _hard_stop(self, process: ProcessLike) -> None:
        """Escalate one process to a hard stop when the API supports it."""
        kill = getattr(process, "kill", None)
        if callable(kill):
            try:
                kill()
                return
            except AttributeError, NotImplementedError:
                pass
        process.terminate()

    def _drain(self) -> bool:
        """Stop children within bounded phases and report cleanup success."""
        deadline = time.monotonic() + self._shutdown_timeout_seconds
        while self._children and time.monotonic() < deadline:
            if not self._reap():
                self._sleep(
                    min(
                        self.settings.queue.poll_interval_seconds,
                        max(0.0, deadline - time.monotonic()),
                    )
                )
        if not self._children:
            return True

        forced_deadline = time.monotonic() + FORCED_CLEANUP_TIMEOUT_SECONDS
        for child in tuple(self._children.values()):
            if child.process.is_alive():
                child.process.terminate()
        while self._children and time.monotonic() < forced_deadline:
            if not self._reap("Worker stopped during graceful shutdown."):
                self._sleep(
                    min(
                        self.settings.queue.poll_interval_seconds,
                        max(0.0, forced_deadline - time.monotonic()),
                    )
                )
        if not self._children:
            return True

        hard_deadline = time.monotonic() + HARD_CLEANUP_TIMEOUT_SECONDS
        for child in tuple(self._children.values()):
            if child.process.is_alive():
                self._hard_stop(child.process)
        while self._children and time.monotonic() < hard_deadline:
            if not self._reap("Worker stopped during graceful shutdown."):
                self._sleep(
                    min(
                        self.settings.queue.poll_interval_seconds,
                        max(0.0, hard_deadline - time.monotonic()),
                    )
                )

        for work_id, child in tuple(self._children.items()):
            if not child.process.is_alive():
                child.process.join(timeout=0)
                self._record_failure(
                    child.request,
                    "Worker stopped during graceful shutdown.",
                )
                self._children.pop(work_id, None)
        return not self._children

    def run(self, stop_signal: StopSignal | None = None) -> bool:
        """Run until shutdown, polling only when no queue/process event fires."""
        signal = stop_signal or threading.Event()
        clean_shutdown = False
        try:
            while not signal.is_set():
                if not self.run_once():
                    waiter = getattr(signal, "wait", None)
                    if callable(waiter):
                        waiter(self.settings.queue.poll_interval_seconds)
                    else:
                        self._sleep(self.settings.queue.poll_interval_seconds)
        except KeyboardInterrupt:
            pass
        finally:
            try:
                clean_shutdown = self._drain()
            finally:
                if self._worker_supervisor is not None:
                    self._worker_supervisor.close()
        return clean_shutdown


def run_dispatcher(
    database_path: str | Path,
    project_root: str | Path,
    settings: AiSettings,
    stop_signal: StopSignal | None = None,
    shutdown_timeout_seconds: float = 10.0,
) -> None:
    """Run the dispatcher process with a local signal-driven stop event."""
    _isolate_worker_process_group()
    local_stop_signal = stop_signal or threading.Event()
    dispatcher = Dispatcher(
        database_path,
        project_root,
        settings,
        shutdown_timeout_seconds=shutdown_timeout_seconds,
    )
    if not dispatcher.run(local_stop_signal):
        raise SystemExit(1)
