from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import pytest

from openchronicle import daemon as daemon_mod
from openchronicle.capture import window_meta
from openchronicle.capture.privacy import (
    DisplayInfo,
    ProtectionFailureReason,
    ScreenRegion,
    VisibleWindow,
    WindowInventory,
)
from openchronicle.capture.privacy_diagnostics import PrivacyDiagnosticsServer
from openchronicle.capture.privacy_diagnostics_guard import DiagnosticsGuardSnapshot
from openchronicle.capture.protection import ProtectionSnapshot, ProtectionState
from openchronicle.capture.protection_monitor import (
    PrivacyProtectionMonitor,
    ProtectionDecision,
)
from openchronicle.capture.protection_reason import ProtectionReasonCode
from openchronicle.config import CaptureConfig, Config


class FakeMonitor:
    def __init__(self, events: list[str] | None = None) -> None:
        self.start_calls = 0
        self.stop_calls = 0
        self.events = events
        self.listeners = []

    def start(self) -> None:
        self.start_calls += 1
        if self.events is not None:
            self.events.append("start monitor")

    def stop(self) -> None:
        self.stop_calls += 1
        if self.events is not None:
            self.events.append("stop monitor")

    def add_decision_listener(self, listener) -> None:
        self.listeners.append(listener)

    def request_refresh(self) -> None:
        return None

    def wait_for_display_protection(
        self,
        display_id: int,
        after_generation: int,
        timeout: float,
    ) -> int | None:
        return after_generation + 1


class FakeLeaseManager:
    def __init__(self, _path: Path, events: list[str] | None = None) -> None:
        self.events = events
        self.load_calls = 0

    def load(self) -> DiagnosticsGuardSnapshot:
        self.load_calls += 1
        if self.events is not None:
            self.events.append("load guard")
        return self.snapshot()

    def snapshot(self) -> DiagnosticsGuardSnapshot:
        return DiagnosticsGuardSnapshot(frozenset(), False)

    def prune_dead(self) -> DiagnosticsGuardSnapshot:
        return self.snapshot()


class FakeDiagnosticsServer:
    def __init__(
        self,
        socket_path: Path,
        _manager: FakeLeaseManager,
        *,
        events: list[str] | None = None,
        **_kwargs,
    ) -> None:
        self.socket_path = socket_path
        self.events = events
        self.start_calls = 0
        self.stop_calls = 0
        self.published = []

    def start(self) -> None:
        self.start_calls += 1
        if self.events is not None:
            self.events.append("start diagnostics")
        self.socket_path.write_text("fake socket")

    def stop(self) -> None:
        self.stop_calls += 1
        if self.events is not None:
            self.events.append("stop diagnostics")
        self.socket_path.unlink(missing_ok=True)

    def publish(self, decision) -> bool:
        self.published.append(decision)
        return True


class FakeSessionManager:
    def __init__(self) -> None:
        self.force_end_calls: list[str] = []

    def on_event(self, _event) -> None:
        return None

    def force_end(self, *, reason: str) -> None:
        self.force_end_calls.append(reason)


class RejectingOverlay:
    def render(self, _snapshot: ProtectionSnapshot, timeout: float = 0.5) -> bool:
        return False

    def clear(self, _generation: int, timeout: float = 0.5) -> bool:
        return False

    def mark_terminal(self) -> None:
        return None

    def close(self) -> None:
        return None


def _configure_daemon(monkeypatch, monitor: FakeMonitor, session: FakeSessionManager) -> Config:
    async def park_forever(*_args, **_kwargs) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(
        daemon_mod,
        "_build_protection_monitor",
        lambda _cfg, **_kwargs: monitor,
    )
    monkeypatch.setattr(daemon_mod, "DiagnosticsLeaseManager", FakeLeaseManager)
    monkeypatch.setattr(daemon_mod, "PrivacyDiagnosticsServer", FakeDiagnosticsServer)
    monkeypatch.setattr(daemon_mod.session_tick, "build_manager", lambda _cfg: session)
    monkeypatch.setattr(daemon_mod.capture_scheduler, "run_forever", park_forever)
    monkeypatch.setattr(daemon_mod.session_tick, "run_check_cuts", park_forever)
    monkeypatch.setattr(daemon_mod.session_tick, "run_daily_safety_net", park_forever)
    cfg = Config()
    cfg.mcp.auto_start = False
    return cfg


def test_unconfirmed_indicator_publishes_fixed_diagnostic_without_exact_values(
    ac_root: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    marker = "private-indicator-reason-marker"
    displays = (
        DisplayInfo(1, ScreenRegion(0, 0, 100, 100), True),
        DisplayInfo(2, ScreenRegion(100, 0, 100, 100), False),
    )
    inventory = WindowInventory(
        windows=(
            VisibleWindow("Cursor", "cursor", "main.py", ScreenRegion(0, 0, 80, 90), True),
            VisibleWindow("Edge", "edge", marker, ScreenRegion(110, 0, 80, 90)),
        ),
        displays=displays,
    )
    monitor = PrivacyProtectionMonitor(
        CaptureConfig(
            screenshot_monitor="separate",
            privacy_indicator_style="pill",
            deny_window_title_patterns=[marker],
        ),
        config_path=ac_root / "missing.toml",
        overlay=RejectingOverlay(),
        inventory_reader=lambda: inventory,
        pause_reader=lambda: False,
    )

    with caplog.at_level(logging.DEBUG, logger="openchronicle.capture"):
        decision = monitor.decision_for_capture(force=True)

    category = PrivacyDiagnosticsServer._snapshot_payload(
        decision,
        detail="category",
        created_at="2026-08-22T12:00:00.000000Z",
    )
    exact = PrivacyDiagnosticsServer._snapshot_payload(
        decision,
        detail="exact",
        created_at="2026-08-22T12:00:00.000000Z",
    )

    assert decision.indicator_confirmed is False
    assert category["reasons"] == [
        {
            "code": ProtectionReasonCode.INDICATOR_UNCONFIRMED.value,
            "display_id": None,
        }
    ]
    assert marker not in json.dumps(category, ensure_ascii=False)
    assert marker in json.dumps(exact, ensure_ascii=False)
    assert marker not in caplog.text


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
async def test_daemon_orders_guard_monitor_diagnostics_capture_and_shutdown(
    ac_root: Path,
    monkeypatch,
) -> None:
    events: list[str] = []
    manager = FakeLeaseManager(ac_root / "runtime" / "privacy-reveal.guard", events)
    monitor = FakeMonitor(events)
    session = FakeSessionManager()
    server: FakeDiagnosticsServer | None = None

    async def capture_once_then_return(*_args, **_kwargs) -> None:
        events.append("start capture")

    async def park_forever(*_args, **_kwargs) -> None:
        await asyncio.Event().wait()

    def build_server(socket_path, lease_manager, **kwargs):
        nonlocal server
        assert lease_manager is manager
        assert kwargs["request_refresh"] == monitor.request_refresh
        assert kwargs["wait_for_display_protection"] == monitor.wait_for_display_protection
        server = FakeDiagnosticsServer(socket_path, lease_manager, events=events, **kwargs)
        return server

    monkeypatch.setattr(daemon_mod, "DiagnosticsLeaseManager", lambda _path: manager)
    monkeypatch.setattr(
        daemon_mod,
        "_build_protection_monitor",
        lambda _cfg, **_kwargs: monitor,
    )
    monkeypatch.setattr(daemon_mod, "PrivacyDiagnosticsServer", build_server)
    monkeypatch.setattr(daemon_mod.session_tick, "build_manager", lambda _cfg: session)
    monkeypatch.setattr(daemon_mod.capture_scheduler, "run_forever", capture_once_then_return)
    monkeypatch.setattr(daemon_mod.session_tick, "run_check_cuts", park_forever)
    monkeypatch.setattr(daemon_mod.session_tick, "run_daily_safety_net", park_forever)
    cfg = Config()
    cfg.mcp.auto_start = False

    await daemon_mod._run(cfg, capture_only=True)

    assert events == [
        "load guard",
        "start monitor",
        "start diagnostics",
        "start capture",
        "stop diagnostics",
        "stop monitor",
    ]
    assert server is not None
    assert monitor.listeners == [server.publish]
    assert server.start_calls == 1
    assert server.stop_calls == 1
    assert not daemon_mod.paths.privacy_diagnostics_socket().exists()
    assert not daemon_mod.paths.pid_file().exists()


@pytest.mark.asyncio
async def test_daemon_privacy_mode_off_bypasses_background_monitor(
    ac_root: Path, monkeypatch,
) -> None:
    session = FakeSessionManager()
    seen_monitor = object()

    def unexpected_monitor(*_args, **_kwargs):
        raise AssertionError("privacy mode off must not construct a background monitor")

    async def capture_once_then_return(*_args, protection_monitor=None, **_kwargs) -> None:
        nonlocal seen_monitor
        seen_monitor = protection_monitor

    async def park_forever(*_args, **_kwargs) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(daemon_mod, "PrivacyProtectionMonitor", unexpected_monitor)
    monkeypatch.setattr(daemon_mod, "PrivacyOverlayClient", lambda: object())
    monkeypatch.setattr(daemon_mod.session_tick, "build_manager", lambda _cfg: session)
    monkeypatch.setattr(daemon_mod.capture_scheduler, "run_forever", capture_once_then_return)
    monkeypatch.setattr(daemon_mod.session_tick, "run_check_cuts", park_forever)
    monkeypatch.setattr(daemon_mod.session_tick, "run_daily_safety_net", park_forever)
    cfg = Config()
    cfg.capture.screenshot_privacy_mode = "off"
    cfg.mcp.auto_start = False

    await daemon_mod._run(cfg, capture_only=True)

    assert seen_monitor is None
    assert session.force_end_calls == ["daemon-shutdown"]


@pytest.mark.asyncio
async def test_daemon_fail_open_inventory_failure_allows_unprotected_capture(
    ac_root: Path, monkeypatch,
) -> None:
    monitor = FakeMonitor()
    session = FakeSessionManager()
    now = 1.0
    failed = ProtectionDecision(
        ProtectionSnapshot(
            generation=1,
            state=ProtectionState.FAILED,
            capture_mode="primary",
            indicator_style="pill",
            displays=(),
            protected_display_ids=frozenset(),
            active_display_id=None,
            created_monotonic=now,
            fresh_until=now + 1.0,
            failure_reason=ProtectionFailureReason.HELPER_EXIT,
        ),
        indicator_confirmed=False,
    )
    monitor.decision_for_capture = lambda *, force=True: failed  # type: ignore[attr-defined]
    screenshot_calls = 0
    built_capture = None

    async def capture_once_then_return(capture_cfg, *, protection_monitor=None, **_kwargs) -> None:
        nonlocal built_capture
        built_capture = daemon_mod.capture_scheduler._build_capture(
            capture_cfg,
            daemon_mod.capture_scheduler.ax_capture.UnavailableAXProvider("test unavailable"),
            None,
            protection_monitor=protection_monitor,
        )

    def grab_many(**_kwargs):
        nonlocal screenshot_calls
        screenshot_calls += 1
        return []

    cfg = _configure_daemon(monkeypatch, monitor, session)
    cfg.capture.screenshot_privacy_fail_closed = False
    monkeypatch.setattr(daemon_mod.capture_scheduler, "run_forever", capture_once_then_return)
    monkeypatch.setattr(
        daemon_mod.capture_scheduler.window_meta,
        "active_window",
        lambda: window_meta.WindowMeta(app_name="Cursor", title="main.py", bundle_id="cursor"),
    )
    monkeypatch.setattr(daemon_mod.capture_scheduler.screenshot, "grab_many", grab_many)

    await daemon_mod._run(cfg, capture_only=True)

    assert built_capture is not None
    assert screenshot_calls == 1
    assert monitor.start_calls == 1
    assert monitor.stop_calls == 1


@pytest.mark.asyncio
async def test_daemon_removes_pid_when_monitor_factory_fails(
    ac_root: Path, monkeypatch,
) -> None:
    cfg = Config()
    monkeypatch.setattr(
        daemon_mod,
        "_build_protection_monitor",
        lambda _cfg, **_kwargs: (_ for _ in ()).throw(RuntimeError("factory failed")),
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
