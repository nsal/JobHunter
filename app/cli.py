"""Command-line launcher for the web process and work dispatcher."""

from __future__ import annotations

import argparse
import multiprocessing
import os
import signal
import sys
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from app.database import initialize_database
from app.settings import AiSettings, SettingsError, load_ai_settings
from app.work.dispatcher import (
    FORCED_CLEANUP_TIMEOUT_SECONDS,
    HARD_CLEANUP_TIMEOUT_SECONDS,
    dispatcher_supervision_timeout,
)

ROOT = Path(__file__).resolve().parent.parent


class ProcessLike(Protocol):
    """Small process surface used by launcher tests and factories."""

    @property
    def exitcode(self) -> int | None:
        """Return the child exit code when it has exited."""

    def start(self) -> None:
        """Start the child."""

    def is_alive(self) -> bool:
        """Return whether the child is running."""

    def join(self, timeout: float | None = None) -> None:
        """Wait for the child."""

    def terminate(self) -> None:
        """Request child termination."""

    def kill(self) -> None:
        """Force the child to stop when termination fails."""


@dataclass(frozen=True)
class CleanupResult:
    """Report whether bounded process cleanup was forced and confirmed."""

    forced: bool
    confirmed: bool


def _cleanup_process(process: ProcessLike) -> CleanupResult:
    """Terminate a process, escalating once, and confirm its final state."""
    if not process.is_alive():
        return CleanupResult(forced=False, confirmed=True)

    process.terminate()
    process.join(timeout=FORCED_CLEANUP_TIMEOUT_SECONDS)
    if not process.is_alive():
        return CleanupResult(forced=False, confirmed=True)

    kill = getattr(process, "kill", None)
    if not callable(kill):
        return CleanupResult(forced=True, confirmed=False)
    try:
        kill()
    except AttributeError, NotImplementedError:
        return CleanupResult(forced=True, confirmed=False)
    process.join(timeout=HARD_CLEANUP_TIMEOUT_SECONDS)
    return CleanupResult(forced=True, confirmed=not process.is_alive())


def _dispatcher_group_is_alive(process: ProcessLike) -> bool:
    """Return whether an isolated POSIX dispatcher group still has members."""
    process_id = getattr(process, "pid", None)
    if os.name != "posix" or not isinstance(process_id, int) or process_id <= 0:
        return False
    try:
        os.killpg(process_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _signal_dispatcher_group(process: ProcessLike, signal_number: int) -> bool:
    """Signal the dispatcher's isolated POSIX group, with a safe fallback."""
    process_id = getattr(process, "pid", None)
    if os.name == "posix" and isinstance(process_id, int) and process_id > 0:
        try:
            os.killpg(process_id, signal_number)
            return True
        except ProcessLookupError:
            pass
        except PermissionError:
            pass
    if signal_number == signal.SIGTERM:
        process.terminate()
    else:
        process.kill()
    return False


def _cleanup_dispatcher_process(process: ProcessLike) -> CleanupResult:
    """Stop the dispatcher and its workers with bounded escalation."""
    if not process.is_alive() and not _dispatcher_group_is_alive(process):
        return CleanupResult(forced=False, confirmed=True)

    _signal_dispatcher_group(process, signal.SIGTERM)
    process.join(timeout=FORCED_CLEANUP_TIMEOUT_SECONDS)
    group_alive = _dispatcher_group_is_alive(process)
    if not process.is_alive() and not group_alive:
        return CleanupResult(forced=False, confirmed=True)

    try:
        if group_alive:
            _signal_dispatcher_group(process, signal.SIGKILL)
        elif process.is_alive():
            process.kill()
    except AttributeError, NotImplementedError:
        return CleanupResult(forced=True, confirmed=False)
    process.join(timeout=HARD_CLEANUP_TIMEOUT_SECONDS)
    return CleanupResult(
        forced=True,
        confirmed=(
            not process.is_alive() and not _dispatcher_group_is_alive(process)
        ),
    )


def run_server(
    database_path: str | Path,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Load and invoke the web process only when a child is started."""
    from app.main import run_server as actual_run_server

    actual_run_server(database_path, host, port)


def run_dispatcher(
    database_path: str | Path,
    project_root: str | Path,
    settings: AiSettings,
    stop_signal: Any = None,
    shutdown_timeout_seconds: float = 10.0,
) -> None:
    """Load and invoke the dispatcher only when a child is started."""
    from app.work.dispatcher import run_dispatcher as actual_run_dispatcher

    actual_run_dispatcher(
        database_path,
        project_root,
        settings,
        stop_signal,
        shutdown_timeout_seconds,
    )


Target = Callable[..., None]
LaunchFactory = Callable[[Target, tuple[Any, ...]], ProcessLike]


def spawn_process(target: Target, args: tuple[Any, ...]) -> ProcessLike:
    """Create a launcher child using multiprocessing spawn semantics."""
    context = multiprocessing.get_context("spawn")
    return context.Process(target=target, args=args)


def _exit_code(process: ProcessLike) -> int:
    """Normalize a child exit status for the parent CLI."""
    code = process.exitcode
    return code if isinstance(code, int) else 1


class Launcher:
    """Supervise exactly one web process and one dispatcher process."""

    def __init__(
        self,
        database_path: str | Path,
        project_root: str | Path,
        settings: AiSettings,
        *,
        host: str = "127.0.0.1",
        port: int = 8000,
        launch_factory: LaunchFactory = spawn_process,
        sleeper: Callable[[float], None] = time.sleep,
        dispatcher_shutdown_timeout_seconds: float = 10.0,
    ) -> None:
        if dispatcher_shutdown_timeout_seconds <= 0:
            raise ValueError("Dispatcher shutdown timeout must be positive.")
        self.database_path = str(Path(database_path).resolve())
        self.project_root = str(Path(project_root).resolve())
        self.settings = settings
        self.host = host
        self.port = port
        self._launch = launch_factory
        self._sleep = sleeper
        self._dispatcher_shutdown_timeout_seconds = (
            dispatcher_shutdown_timeout_seconds
        )

    @property
    def _dispatcher_supervision_timeout_seconds(self) -> float:
        """Cover cooperative drain, forced cleanup, and observation margin."""
        return dispatcher_supervision_timeout(
            self._dispatcher_shutdown_timeout_seconds
        )

    def _stop_dispatcher(
        self,
        dispatcher: ProcessLike,
        dispatcher_stop: Any,
    ) -> CleanupResult:
        """Request dispatcher shutdown and report forced/confirmed cleanup."""
        dispatcher_stop.set()
        if not dispatcher.is_alive():
            group_alive = _dispatcher_group_is_alive(dispatcher)
            result = _cleanup_dispatcher_process(dispatcher)
            return CleanupResult(
                forced=group_alive or result.forced,
                confirmed=result.confirmed,
            )
        dispatcher.join(timeout=self._dispatcher_supervision_timeout_seconds)
        if not dispatcher.is_alive():
            group_alive = _dispatcher_group_is_alive(dispatcher)
            result = _cleanup_dispatcher_process(dispatcher)
            return CleanupResult(
                forced=group_alive or result.forced,
                confirmed=result.confirmed,
            )
        result = _cleanup_dispatcher_process(dispatcher)
        return CleanupResult(forced=True, confirmed=result.confirmed)

    def run(self, stop_signal: threading.Event | None = None) -> int:
        """Run both children and propagate startup/runtime failures."""
        stop_requested = stop_signal or threading.Event()
        context = multiprocessing.get_context("spawn")
        dispatcher_stop = context.Event()
        server: ProcessLike | None = None
        dispatcher: ProcessLike | None = None
        try:
            initialize_database(self.database_path)
            server = self._launch(
                run_server,
                (
                    self.database_path,
                    self.host,
                    self.port,
                ),
            )
            server.start()
            dispatcher = self._launch(
                run_dispatcher,
                (
                    self.database_path,
                    self.project_root,
                    self.settings,
                    dispatcher_stop,
                    self._dispatcher_shutdown_timeout_seconds,
                ),
            )
            dispatcher.start()
        except Exception:  # noqa: BLE001 - startup failure must clean up both children.
            if dispatcher is not None:
                self._stop_dispatcher(dispatcher, dispatcher_stop)
            if server is not None:
                _cleanup_process(server)
            return 1

        try:
            while not stop_requested.is_set():
                if not server.is_alive():
                    server_code = _exit_code(server)
                    dispatcher_result = self._stop_dispatcher(
                        dispatcher, dispatcher_stop
                    )
                    if server_code != 0:
                        return server_code
                    if (
                        dispatcher_result.forced
                        or not dispatcher_result.confirmed
                    ):
                        return 1
                    return _exit_code(dispatcher)
                if not dispatcher.is_alive():
                    dispatcher_code = _exit_code(dispatcher)
                    dispatcher_result = self._stop_dispatcher(
                        dispatcher, dispatcher_stop
                    )
                    server_result = _cleanup_process(server)
                    if dispatcher_code != 0:
                        return dispatcher_code
                    if (
                        dispatcher_result.forced
                        or not dispatcher_result.confirmed
                        or server_result.forced
                        or not server_result.confirmed
                    ):
                        return 1
                    return 0
                self._sleep(0.05)
        except KeyboardInterrupt:
            pass

        dispatcher_result = self._stop_dispatcher(dispatcher, dispatcher_stop)
        server_result = _cleanup_process(server)
        dispatcher_code = _exit_code(dispatcher)
        if dispatcher_code != 0:
            return dispatcher_code
        if (
            dispatcher_result.forced
            or not dispatcher_result.confirmed
            or server_result.forced
            or not server_result.confirmed
        ):
            return 1
        return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jobhunter",
        description="Run the JobHunter web server and work dispatcher.",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=ROOT / "private" / "jobhunter.db",
        help="SQLite database path.",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=ROOT,
        help="JobHunter project root.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse launcher options, validate configuration, and supervise."""
    arguments = _parser().parse_args(argv)
    try:
        settings = load_ai_settings(
            arguments.project_root / "config" / "ai.yaml"
        )
    except SettingsError:
        print("JobHunter configuration is invalid.", file=sys.stderr)
        return 2

    stop_signal = threading.Event()

    def request_stop(signum: int, frame: Any) -> None:
        del signum, frame
        stop_signal.set()

    previous_term = signal.signal(signal.SIGTERM, request_stop)
    previous_int = signal.signal(signal.SIGINT, request_stop)
    try:
        return Launcher(
            arguments.database,
            arguments.project_root,
            settings,
            host=arguments.host,
            port=arguments.port,
        ).run(stop_signal)
    finally:
        signal.signal(signal.SIGTERM, previous_term)
        signal.signal(signal.SIGINT, previous_int)


if __name__ == "__main__":
    raise SystemExit(main())
