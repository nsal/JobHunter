from __future__ import annotations

import signal
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from app.cli import (
    CleanupResult,
    Launcher,
    _cleanup_dispatcher_process,
    _cleanup_process,
)
from app.settings import AiSettings, load_ai_settings


class FakeProcess:
    def __init__(
        self, exitcode: int | None = None, cooperative: bool = False
    ) -> None:
        self.exitcode = exitcode
        self.started = False
        self.alive = False
        self.terminated = False
        self.killed = False
        self.cooperative = cooperative
        self.pid: int | None = None

    def start(self) -> None:
        self.started = True
        self.alive = True

    def is_alive(self) -> bool:
        return self.alive

    def join(self, timeout: float | None = None) -> None:
        del timeout
        if self.cooperative:
            self.alive = False
            if self.exitcode is None:
                self.exitcode = 0

    def terminate(self) -> None:
        self.terminated = True
        self.alive = False

    def kill(self) -> None:
        self.killed = True
        self.alive = False


def test_cleanup_process_confirms_ordinary_termination() -> None:
    process = FakeProcess()
    process.alive = True

    result = _cleanup_process(process)

    assert result == CleanupResult(forced=False, confirmed=True)
    assert process.terminated
    assert not process.killed


def test_cleanup_process_escalates_when_termination_survives() -> None:
    class TerminationResistant(FakeProcess):
        def terminate(self) -> None:
            self.terminated = True

    process = TerminationResistant()
    process.alive = True

    result = _cleanup_process(process)

    assert result == CleanupResult(forced=True, confirmed=True)
    assert process.terminated
    assert process.killed
    assert not process.is_alive()


def test_cleanup_process_reports_an_unconfirmed_process() -> None:
    class Unstoppable(FakeProcess):
        def terminate(self) -> None:
            self.terminated = True

        def kill(self) -> None:
            self.killed = True

    process = Unstoppable()
    process.alive = True

    result = _cleanup_process(process)

    assert result == CleanupResult(forced=True, confirmed=False)
    assert process.terminated
    assert process.killed
    assert process.is_alive()


def test_cleanup_dispatcher_signals_its_isolated_process_group(
    monkeypatch: Any,
) -> None:
    process = FakeProcess()
    process.alive = True
    process.pid = 4321
    signals: list[tuple[int, int]] = []

    def signal_group(process_group: int, signal_number: int) -> None:
        if signal_number == 0:
            raise ProcessLookupError
        signals.append((process_group, signal_number))
        process.alive = False
        process.exitcode = 0

    monkeypatch.setattr("app.cli.os.killpg", signal_group)

    result = _cleanup_dispatcher_process(process)

    assert result == CleanupResult(forced=False, confirmed=True)
    assert signals == [(4321, signal.SIGTERM)]
    assert not process.terminated


def test_cleanup_dispatcher_hard_stops_live_group_after_parent_exit(
    monkeypatch: Any,
) -> None:
    process = FakeProcess()
    process.alive = True
    process.pid = 4321
    group_alive = True
    signals: list[int] = []

    def signal_group(process_group: int, signal_number: int) -> None:
        nonlocal group_alive
        assert process_group == 4321
        signals.append(signal_number)
        if signal_number == signal.SIGTERM:
            process.alive = False
        elif signal_number == signal.SIGKILL:
            group_alive = False
        elif signal_number == 0 and not group_alive:
            raise ProcessLookupError

    monkeypatch.setattr("app.cli.os.killpg", signal_group)

    result = _cleanup_dispatcher_process(process)

    assert result == CleanupResult(forced=True, confirmed=True)
    assert signals == [signal.SIGTERM, 0, signal.SIGKILL, 0]


def test_cleanup_dispatcher_stops_group_after_leader_already_exited(
    monkeypatch: Any,
) -> None:
    process = FakeProcess(exitcode=1)
    process.pid = 4321
    group_alive = True
    signals: list[int] = []

    def signal_group(process_group: int, signal_number: int) -> None:
        nonlocal group_alive
        assert process_group == 4321
        signals.append(signal_number)
        if signal_number == signal.SIGTERM:
            group_alive = False
        elif signal_number == 0 and not group_alive:
            raise ProcessLookupError

    monkeypatch.setattr("app.cli.os.killpg", signal_group)

    result = _cleanup_dispatcher_process(process)

    assert result == CleanupResult(forced=False, confirmed=True)
    assert signals == [0, signal.SIGTERM, 0]


def test_cleanup_dispatcher_falls_back_when_its_group_is_not_ready(
    monkeypatch: Any,
) -> None:
    process = FakeProcess()
    process.alive = True
    process.pid = 4321

    def missing_group(process_group: int, signal_number: int) -> None:
        del process_group, signal_number
        raise ProcessLookupError

    monkeypatch.setattr("app.cli.os.killpg", missing_group)

    result = _cleanup_dispatcher_process(process)

    assert result == CleanupResult(forced=False, confirmed=True)
    assert process.terminated


def settings() -> AiSettings:
    return load_ai_settings(Path("config") / "ai.yaml")


def test_launcher_propagates_child_failure_and_stops_sibling() -> None:
    created: list[FakeProcess] = []

    def factory(target: Any, args: tuple[Any, ...]) -> FakeProcess:
        del args
        process = FakeProcess(exitcode=7 if len(created) else None)
        created.append(process)
        if len(created) == 2:
            created[0].alive = False
            created[0].exitcode = 7
        return process

    result = Launcher(
        "/tmp/jobhunter.db",
        ".",
        settings(),
        launch_factory=factory,
        sleeper=lambda seconds: None,
    ).run()

    assert result == 7
    assert created[1].terminated


def test_launcher_stops_workers_after_dispatcher_leader_exit(
    monkeypatch: Any,
) -> None:
    created: list[FakeProcess] = []
    group_alive = True
    signals: list[int] = []

    class ExitedDispatcher(FakeProcess):
        def start(self) -> None:
            super().start()
            self.alive = False

    def signal_group(process_group: int, signal_number: int) -> None:
        nonlocal group_alive
        assert process_group == 4321
        signals.append(signal_number)
        if signal_number == signal.SIGTERM:
            group_alive = False
        elif signal_number == 0 and not group_alive:
            raise ProcessLookupError

    def factory(target: Any, args: tuple[Any, ...]) -> FakeProcess:
        del target, args
        process: FakeProcess = (
            ExitedDispatcher(exitcode=7) if created else FakeProcess()
        )
        if created:
            process.pid = 4321
        created.append(process)
        return process

    monkeypatch.setattr("app.cli.os.killpg", signal_group)
    result = Launcher(
        "/tmp/jobhunter.db",
        ".",
        settings(),
        launch_factory=factory,
        sleeper=lambda seconds: None,
    ).run()

    assert result == 7
    assert created[0].terminated
    assert signals == [0, 0, signal.SIGTERM, 0]


def test_launcher_shutdown_returns_cleanly_without_orphans() -> None:
    created: list[FakeProcess] = []
    stop = threading.Event()

    def factory(target: Any, args: tuple[Any, ...]) -> FakeProcess:
        del target, args
        process = FakeProcess(cooperative=len(created) == 1)
        created.append(process)
        return process

    def request_stop(seconds: float) -> None:
        del seconds
        stop.set()

    result = Launcher(
        "/tmp/jobhunter.db",
        ".",
        settings(),
        launch_factory=factory,
        sleeper=request_stop,
    ).run(stop)

    assert result == 0
    assert created[0].terminated
    assert not created[0].killed
    assert not created[1].terminated
    assert not created[1].killed


def test_launcher_reports_forced_dispatcher_shutdown() -> None:
    created: list[FakeProcess] = []
    stop = threading.Event()

    def factory(target: Any, args: tuple[Any, ...]) -> FakeProcess:
        del target, args
        process = FakeProcess()
        created.append(process)
        return process

    result = Launcher(
        "/tmp/jobhunter.db",
        ".",
        settings(),
        launch_factory=factory,
        sleeper=lambda seconds: stop.set(),
    ).run(stop)

    assert result != 0
    assert created[1].terminated


def test_launcher_rejects_zero_exit_after_forced_dispatcher_shutdown() -> None:
    created: list[FakeProcess] = []
    stop = threading.Event()

    def factory(target: Any, args: tuple[Any, ...]) -> FakeProcess:
        del target, args
        process = FakeProcess(exitcode=0 if len(created) == 1 else None)
        created.append(process)
        return process

    result = Launcher(
        "/tmp/jobhunter.db",
        ".",
        settings(),
        launch_factory=factory,
        sleeper=lambda seconds: stop.set(),
    ).run(stop)

    assert result != 0
    assert created[1].terminated


def test_launcher_escalates_dispatcher_cleanup_to_kill() -> None:
    created: list[FakeProcess] = []
    stop = threading.Event()

    class TerminationResistant(FakeProcess):
        def terminate(self) -> None:
            self.terminated = True

    def factory(target: Any, args: tuple[Any, ...]) -> FakeProcess:
        del target, args
        process: FakeProcess = (
            TerminationResistant() if len(created) == 1 else FakeProcess()
        )
        created.append(process)
        return process

    result = Launcher(
        "/tmp/jobhunter.db",
        ".",
        settings(),
        launch_factory=factory,
        sleeper=lambda seconds: stop.set(),
    ).run(stop)

    assert result != 0
    assert created[1].terminated
    assert created[1].killed
    assert not created[1].is_alive()


def test_launcher_reports_unconfirmed_dispatcher_cleanup() -> None:
    created: list[FakeProcess] = []
    stop = threading.Event()

    class Unstoppable(FakeProcess):
        def terminate(self) -> None:
            self.terminated = True

        def kill(self) -> None:
            self.killed = True

    def factory(target: Any, args: tuple[Any, ...]) -> FakeProcess:
        del target, args
        process: FakeProcess = (
            Unstoppable() if len(created) == 1 else FakeProcess()
        )
        created.append(process)
        return process

    result = Launcher(
        "/tmp/jobhunter.db",
        ".",
        settings(),
        launch_factory=factory,
        sleeper=lambda seconds: stop.set(),
    ).run(stop)

    assert result != 0
    assert created[1].is_alive()


def test_launcher_reports_forced_server_shutdown() -> None:
    created: list[FakeProcess] = []
    stop = threading.Event()

    class TerminationResistant(FakeProcess):
        def terminate(self) -> None:
            self.terminated = True

    def factory(target: Any, args: tuple[Any, ...]) -> FakeProcess:
        del target, args
        process: FakeProcess = (
            TerminationResistant() if not created else FakeProcess()
        )
        created.append(process)
        return process

    result = Launcher(
        "/tmp/jobhunter.db",
        ".",
        settings(),
        launch_factory=factory,
        sleeper=lambda seconds: stop.set(),
    ).run(stop)

    assert result != 0
    assert created[0].terminated
    assert created[0].killed


def test_launcher_reports_unconfirmed_server_cleanup() -> None:
    created: list[FakeProcess] = []
    stop = threading.Event()

    class Unstoppable(FakeProcess):
        def terminate(self) -> None:
            self.terminated = True

        def kill(self) -> None:
            self.killed = True

    def factory(target: Any, args: tuple[Any, ...]) -> FakeProcess:
        del target, args
        process: FakeProcess = Unstoppable() if not created else FakeProcess()
        created.append(process)
        return process

    result = Launcher(
        "/tmp/jobhunter.db",
        ".",
        settings(),
        launch_factory=factory,
        sleeper=lambda seconds: stop.set(),
    ).run(stop)

    assert result != 0
    assert created[0].is_alive()


def test_launcher_propagates_dispatcher_failure_during_requested_shutdown() -> (
    None
):
    created: list[FakeProcess] = []
    stop = threading.Event()

    def factory(target: Any, args: tuple[Any, ...]) -> FakeProcess:
        del target, args
        process = FakeProcess(cooperative=len(created) == 1)
        if len(created) == 1:
            process.exitcode = 9
        created.append(process)
        return process

    result = Launcher(
        "/tmp/jobhunter.db",
        ".",
        settings(),
        launch_factory=factory,
        sleeper=lambda seconds: stop.set(),
    ).run(stop)

    assert result == 9


def test_installed_console_command_displays_help() -> None:
    result = subprocess.run(
        ["jobhunter", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "Run the JobHunter web server" in result.stdout


def test_installed_console_command_rejects_invalid_options() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "app.cli", "--not-an-option"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0


def test_launcher_initializes_schema_before_child_factory(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "fresh" / "jobhunter.db"
    created: list[FakeProcess] = []
    schema_seen: list[bool] = []
    stop = threading.Event()

    def factory(target: Any, args: tuple[Any, ...]) -> FakeProcess:
        del target, args
        with sqlite3.connect(database_path) as connection:
            schema_seen.append(
                connection.execute(
                    """SELECT 1 FROM sqlite_master
                    WHERE type = 'table' AND name = 'work_items'"""
                ).fetchone()
                is not None
            )
        process = FakeProcess(cooperative=len(created) == 1)
        created.append(process)
        if len(created) == 2:
            stop.set()
        return process

    result = Launcher(
        database_path,
        ".",
        settings(),
        launch_factory=factory,
        sleeper=lambda seconds: None,
    ).run(stop)

    assert result == 0
    assert schema_seen == [True, True]


def test_launcher_does_not_spawn_when_schema_initialization_fails(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    created = 0

    def fail_initialization(database_path: str | Path) -> None:
        del database_path
        raise OSError("database unavailable")

    def factory(target: Any, args: tuple[Any, ...]) -> FakeProcess:
        del target, args
        nonlocal created
        created += 1
        return FakeProcess()

    monkeypatch.setattr("app.cli.initialize_database", fail_initialization)
    result = Launcher(
        tmp_path / "broken.db",
        ".",
        settings(),
        launch_factory=factory,
    ).run()

    assert result != 0
    assert created == 0
