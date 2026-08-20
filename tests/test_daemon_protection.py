from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from openchronicle import daemon as daemon_mod
from openchronicle.config import Config


class FakeMonitor:
    def __init__(self) -> None:
        self.start_calls = 0
        self.stop_calls = 0

    def start(self) -> None:
        self.start_calls += 1

    def stop(self) -> None:
        self.stop_calls += 1


class FakeSessionManager:
    def __init__(self) -> None:
        self.force_end_calls: list[str] = []

    def on_event(self, _event) -> None:
        return None

    def force_end(self, *, reason: str) -> None:
        self.force_end_calls.append(reason)


def _configure_daemon(monkeypatch, monitor: FakeMonitor, session: FakeSessionManager) -> Config:
    async def park_forever(*_args, **_kwargs) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(daemon_mod, "_build_protection_monitor", lambda _cfg: monitor)
    monkeypatch.setattr(daemon_mod.session_tick, "build_manager", lambda _cfg: session)
    monkeypatch.setattr(daemon_mod.capture_scheduler, "run_forever", park_forever)
    monkeypatch.setattr(daemon_mod.session_tick, "run_check_cuts", park_forever)
    monkeypatch.setattr(daemon_mod.session_tick, "run_daily_safety_net", park_forever)
    cfg = Config()
    cfg.mcp.auto_start = False
    return cfg


@pytest.mark.asyncio
async def test_daemon_owns_protection_monitor_lifecycle(
    ac_root: Path, monkeypatch,
) -> None:
    monitor = FakeMonitor()
    session = FakeSessionManager()
    seen_monitor = None

    async def capture_once_then_return(*_args, protection_monitor=None, **_kwargs) -> None:
        nonlocal seen_monitor
        seen_monitor = protection_monitor

    cfg = _configure_daemon(monkeypatch, monitor, session)
    monkeypatch.setattr(daemon_mod.capture_scheduler, "run_forever", capture_once_then_return)

    await daemon_mod._run(cfg, capture_only=True)

    assert monitor.start_calls == 1
    assert seen_monitor is monitor
    assert monitor.stop_calls == 1
    assert session.force_end_calls == ["daemon-shutdown"]


@pytest.mark.asyncio
async def test_daemon_removes_pid_when_monitor_factory_fails(
    ac_root: Path, monkeypatch,
) -> None:
    cfg = Config()
    monkeypatch.setattr(
        daemon_mod,
        "_build_protection_monitor",
        lambda _cfg: (_ for _ in ()).throw(RuntimeError("factory failed")),
    )

    with pytest.raises(RuntimeError, match="factory failed"):
        await daemon_mod._run(cfg, capture_only=True)

    assert not daemon_mod.paths.pid_file().exists()


@pytest.mark.asyncio
async def test_daemon_stops_monitor_and_removes_pid_when_start_fails(
    ac_root: Path, monkeypatch,
) -> None:
    class StartFailingMonitor(FakeMonitor):
        def start(self) -> None:
            super().start()
            raise RuntimeError("start failed")

    monitor = StartFailingMonitor()
    session = FakeSessionManager()
    cfg = _configure_daemon(monkeypatch, monitor, session)

    with pytest.raises(RuntimeError, match="start failed"):
        await daemon_mod._run(cfg, capture_only=True)

    assert monitor.start_calls == 1
    assert monitor.stop_calls == 1
    assert session.force_end_calls == []
    assert not daemon_mod.paths.pid_file().exists()


@pytest.mark.asyncio
async def test_daemon_cleans_up_task_created_before_later_task_creation_fails(
    ac_root: Path, monkeypatch,
) -> None:
    monitor = FakeMonitor()
    session = FakeSessionManager()
    cfg = _configure_daemon(monkeypatch, monitor, session)
    real_create_task = asyncio.create_task
    created_tasks: list[asyncio.Task] = []
    create_calls = 0

    def fail_second_create_task(coro, *args, **kwargs):
        nonlocal create_calls
        create_calls += 1
        if create_calls == 2:
            coro.close()
            raise RuntimeError("task creation failed")
        task = real_create_task(coro, *args, **kwargs)
        created_tasks.append(task)
        return task

    monkeypatch.setattr(daemon_mod.asyncio, "create_task", fail_second_create_task)

    with pytest.raises(RuntimeError, match="task creation failed"):
        await daemon_mod._run(cfg, capture_only=True)

    first_task_was_cleaned = created_tasks[0].done()
    if not first_task_was_cleaned:
        created_tasks[0].cancel()
        await asyncio.gather(created_tasks[0], return_exceptions=True)

    assert first_task_was_cleaned
    assert monitor.start_calls == 1
    assert monitor.stop_calls == 1
    assert session.force_end_calls == ["daemon-shutdown"]
    assert not daemon_mod.paths.pid_file().exists()


@pytest.mark.asyncio
async def test_daemon_stops_protection_monitor_when_capture_task_errors(
    ac_root: Path, monkeypatch,
) -> None:
    monitor = FakeMonitor()
    session = FakeSessionManager()

    async def fail_capture(*_args, **_kwargs) -> None:
        raise RuntimeError("capture failed")

    cfg = _configure_daemon(monkeypatch, monitor, session)
    monkeypatch.setattr(daemon_mod.capture_scheduler, "run_forever", fail_capture)

    await daemon_mod._run(cfg, capture_only=True)

    assert monitor.start_calls == 1
    assert monitor.stop_calls == 1
    assert session.force_end_calls == ["daemon-shutdown"]


@pytest.mark.asyncio
async def test_daemon_stops_protection_monitor_when_cancelled(
    ac_root: Path, monkeypatch,
) -> None:
    monitor = FakeMonitor()
    session = FakeSessionManager()
    capture_started = asyncio.Event()

    async def park_capture(*_args, **_kwargs) -> None:
        capture_started.set()
        await asyncio.Event().wait()

    cfg = _configure_daemon(monkeypatch, monitor, session)
    monkeypatch.setattr(daemon_mod.capture_scheduler, "run_forever", park_capture)

    task = asyncio.create_task(daemon_mod._run(cfg, capture_only=True))
    await asyncio.wait_for(capture_started.wait(), timeout=1.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert monitor.start_calls == 1
    assert monitor.stop_calls == 1
    assert session.force_end_calls == ["daemon-shutdown"]
