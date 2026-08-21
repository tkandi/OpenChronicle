from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from openchronicle.capture.privacy import (
    DisplayInfo,
    InventoryReadResult,
    ProtectionFailureReason,
    ScreenRegion,
    VisibleWindow,
    WindowInventory,
)
from openchronicle.capture.protection import ProtectionSnapshot, ProtectionState
from openchronicle.capture.protection_monitor import PrivacyProtectionMonitor
from openchronicle.config import CaptureConfig


class FakeOverlay:
    def __init__(self) -> None:
        self.render_result = True
        self.clear_result = True
        self.render_calls = 0
        self.clear_calls = 0
        self.snapshots: list[ProtectionSnapshot] = []
        self.clear_generations: list[int] = []
        self.close_calls = 0
        self.closed = threading.Event()
        self.terminal_marked = threading.Event()

    def render(self, snapshot: ProtectionSnapshot, timeout: float = 0.5) -> bool:
        self.render_calls += 1
        if self.terminal_marked.is_set():
            return False
        self.snapshots.append(snapshot)
        return self.render_result

    def clear(self, generation: int, timeout: float = 0.5) -> bool:
        self.clear_calls += 1
        if self.terminal_marked.is_set():
            return False
        self.clear_generations.append(generation)
        return self.clear_result

    def mark_terminal(self) -> None:
        self.terminal_marked.set()

    def close(self) -> None:
        self.close_calls += 1
        self.closed.set()


@pytest.fixture
def inventory() -> WindowInventory:
    displays = (
        DisplayInfo(1, ScreenRegion(0, 0, 100, 100), True),
        DisplayInfo(2, ScreenRegion(100, 0, 100, 100), False),
    )
    windows = (
        VisibleWindow("Edge", "edge", "InPrivate", ScreenRegion(110, 0, 80, 90), False),
    )
    return WindowInventory(windows=windows, displays=displays)


@pytest.fixture
def fake_overlay() -> FakeOverlay:
    return FakeOverlay()


def make_monitor(
    *,
    inventory: WindowInventory,
    overlay: FakeOverlay,
    style: str = "pill",
    config_path: Path | None = None,
    inventory_reader: Callable[[], WindowInventory | None] | None = None,
    pause_reader: Callable[[], bool] | None = None,
    watchdog_seconds: float = 0.01,
) -> PrivacyProtectionMonitor:
    cfg = CaptureConfig(
        screenshot_monitor="separate",
        privacy_indicator_style=style,
        deny_window_title_patterns=["InPrivate"],
    )
    return PrivacyProtectionMonitor(
        cfg,
        config_path=config_path or Path("/nonexistent/config.toml"),
        overlay=overlay,
        inventory_reader=inventory_reader or (lambda: inventory),
        pause_reader=pause_reader or (lambda: False),
        watchdog_seconds=watchdog_seconds,
    )


def test_monitor_renders_pause_on_all_displays(tmp_path, inventory, fake_overlay) -> None:
    cfg = CaptureConfig(privacy_indicator_style="pill")
    monitor = PrivacyProtectionMonitor(
        cfg,
        config_path=tmp_path / "config.toml",
        overlay=fake_overlay,
        inventory_reader=lambda: inventory,
        pause_reader=lambda: True,
        watchdog_seconds=0.01,
    )

    decision = monitor.decision_for_capture(force=True)

    assert decision.snapshot.state is ProtectionState.PAUSED
    assert decision.snapshot.protected_display_ids == frozenset({1, 2})
    assert decision.indicator_confirmed is True


def test_required_overlay_timeout_is_unconfirmed(inventory, fake_overlay) -> None:
    fake_overlay.render_result = False
    monitor = make_monitor(inventory=inventory, overlay=fake_overlay)

    decision = monitor.decision_for_capture(force=True)

    assert decision.snapshot.state is ProtectionState.PROTECTED
    assert decision.indicator_confirmed is False


def test_style_hot_reload_changes_only_indicator_style(tmp_path, inventory, fake_overlay) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('[capture]\nprivacy_indicator_style = "shield"\n')
    monitor = make_monitor(config_path=config_path, inventory=inventory, overlay=fake_overlay)

    first = monitor.decision_for_capture(force=True)
    previous_mtime = config_path.stat().st_mtime_ns
    config_path.write_text('[capture]\nprivacy_indicator_style = "banner"\n')
    os.utime(config_path, ns=(previous_mtime + 1, previous_mtime + 1))
    second = monitor.decision_for_capture(force=True)

    assert first.snapshot.indicator_style == "shield"
    assert second.snapshot.indicator_style == "banner"
    assert second.snapshot.capture_mode == first.snapshot.capture_mode
    assert second.snapshot.generation > first.snapshot.generation


def test_style_reload_retries_a_recovered_config_with_unchanged_mtime(tmp_path, inventory, fake_overlay) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("[capture\n")
    monitor = make_monitor(
        config_path=config_path,
        inventory=inventory,
        overlay=fake_overlay,
        style="border",
    )

    first = monitor.decision_for_capture(force=True)
    broken_mtime = config_path.stat().st_mtime_ns
    config_path.write_text('[capture]\nprivacy_indicator_style = "shield"\n')
    os.utime(config_path, ns=(broken_mtime, broken_mtime))
    second = monitor.decision_for_capture(force=True)

    assert first.snapshot.indicator_style == "border"
    assert second.snapshot.indicator_style == "shield"


def test_style_reload_normalizes_invalid_value_without_reloading_capture_policy(
    tmp_path, inventory, fake_overlay
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[capture]\nprivacy_indicator_style = "invalid"\nscreenshot_monitor = "all"\n'
    )
    monitor = make_monitor(config_path=config_path, inventory=inventory, overlay=fake_overlay)

    decision = monitor.decision_for_capture(force=True)

    assert decision.snapshot.indicator_style == "pill"
    assert decision.snapshot.capture_mode == "separate"
    assert decision.snapshot.protected_display_ids == frozenset({2})


def test_inactive_state_clears_indicator_and_reuses_fresh_decision(fake_overlay) -> None:
    safe_inventory = WindowInventory(
        windows=(),
        displays=(DisplayInfo(1, ScreenRegion(0, 0, 100, 100), True),),
    )
    monitor = make_monitor(inventory=safe_inventory, overlay=fake_overlay)

    first = monitor.decision_for_capture(force=True)
    cached = monitor.decision_for_capture(force=False)

    assert first.snapshot.state is ProtectionState.INACTIVE
    assert fake_overlay.clear_generations == [first.snapshot.generation]
    assert cached is first


def test_inventory_failure_is_failed_without_private_metadata_in_logs(inventory, fake_overlay, caplog) -> None:
    marker = "private-window-title"

    def fail_inventory() -> WindowInventory | None:
        raise RuntimeError(marker)

    monitor = make_monitor(
        inventory=inventory,
        overlay=fake_overlay,
        inventory_reader=fail_inventory,
    )

    with caplog.at_level(logging.WARNING, logger="openchronicle.capture"):
        decision = monitor.decision_for_capture(force=True)

    assert decision.snapshot.state is ProtectionState.FAILED
    assert decision.indicator_confirmed is True
    assert fake_overlay.snapshots[-1].state is ProtectionState.FAILED
    assert marker not in caplog.text


def test_failed_snapshot_logs_one_fixed_reason_without_private_metadata(inventory, fake_overlay) -> None:
    marker = "private-helper-stderr"
    monitor = make_monitor(
        inventory=inventory,
        overlay=fake_overlay,
        inventory_reader=lambda: InventoryReadResult(None, ProtectionFailureReason.HELPER_EXIT),
    )

    messages: list[str] = []
    handler = logging.Handler()
    handler.emit = lambda record: messages.append(record.getMessage())  # type: ignore[method-assign]
    capture_logger = logging.getLogger("openchronicle.capture")
    original_propagate = capture_logger.propagate
    capture_logger.addHandler(handler)
    capture_logger.propagate = False
    try:
        decision = monitor.decision_for_capture(force=True)
    finally:
        capture_logger.removeHandler(handler)
        capture_logger.propagate = original_propagate

    assert decision.snapshot.state is ProtectionState.FAILED
    assert decision.snapshot.failure_reason is ProtectionFailureReason.HELPER_EXIT
    assert messages == ["privacy protection failed closed: reason=helper_exit"]
    assert marker not in messages


def test_monitor_sanitizes_invalid_window_regex_logs(inventory, fake_overlay, caplog) -> None:
    marker = "[private-monitor-regex"
    cfg = CaptureConfig(
        screenshot_monitor="separate",
        privacy_indicator_style="pill",
        deny_window_title_patterns=[marker],
    )
    monitor = PrivacyProtectionMonitor(
        cfg,
        config_path=Path("/nonexistent/config.toml"),
        overlay=fake_overlay,
        inventory_reader=lambda: inventory,
        pause_reader=lambda: False,
    )

    with caplog.at_level(logging.WARNING, logger="openchronicle.capture"):
        decision = monitor.decision_for_capture(force=True)

    assert decision.snapshot.state is ProtectionState.INACTIVE
    assert marker not in caplog.text


def test_stop_before_first_decision_is_terminal(inventory, fake_overlay) -> None:
    monitor = make_monitor(inventory=inventory, overlay=fake_overlay)

    monitor.stop()

    with pytest.raises(RuntimeError, match="stopped"):
        monitor.decision_for_capture(force=True)
    assert fake_overlay.snapshots == []
    assert fake_overlay.clear_generations == []
    assert fake_overlay.closed.wait(timeout=0.5)
    assert fake_overlay.close_calls == 1


def test_stopped_monitor_rejects_cached_and_forced_decisions(inventory, fake_overlay) -> None:
    monitor = make_monitor(inventory=inventory, overlay=fake_overlay)
    monitor.decision_for_capture(force=True)

    monitor.stop()

    with pytest.raises(RuntimeError, match="stopped"):
        monitor.decision_for_capture(force=False)
    with pytest.raises(RuntimeError, match="stopped"):
        monitor.decision_for_capture(force=True)
    assert fake_overlay.closed.wait(timeout=0.5)


def test_stop_during_blocked_force_refresh_is_bounded_and_prevents_late_render(
    inventory, fake_overlay
) -> None:
    inventory_started = threading.Event()
    release_inventory = threading.Event()
    refresh_errors: list[Exception] = []

    def block_inventory() -> WindowInventory:
        inventory_started.set()
        assert release_inventory.wait(timeout=5.0)
        return inventory

    monitor = make_monitor(
        inventory=inventory,
        overlay=fake_overlay,
        inventory_reader=block_inventory,
    )

    def force_refresh() -> None:
        try:
            monitor.decision_for_capture(force=True)
        except RuntimeError as exc:
            refresh_errors.append(exc)

    refresh_thread = threading.Thread(target=force_refresh)
    refresh_thread.start()
    assert inventory_started.wait(timeout=0.5)
    try:
        started_at = time.monotonic()
        monitor.stop()
        assert time.monotonic() - started_at < 0.5
    finally:
        release_inventory.set()
        refresh_thread.join(timeout=1.0)

    assert not refresh_thread.is_alive()
    assert refresh_errors and "stopped" in str(refresh_errors[0])
    assert fake_overlay.render_calls == 0
    assert fake_overlay.clear_calls == 0
    assert fake_overlay.snapshots == []
    assert fake_overlay.clear_generations == []
    assert fake_overlay.closed.wait(timeout=0.5)
    assert fake_overlay.close_calls == 1


def test_stop_marks_overlay_terminal_before_a_pre_overlay_barrier_releases(inventory, fake_overlay) -> None:
    reached_barrier = threading.Event()
    release_barrier = threading.Event()
    stop_finished = threading.Event()
    refresh_errors: list[Exception] = []

    def before_overlay_call() -> None:
        reached_barrier.set()
        assert release_barrier.wait(timeout=1.0)

    monitor = PrivacyProtectionMonitor(
        CaptureConfig(
            screenshot_monitor="separate",
            privacy_indicator_style="pill",
            deny_window_title_patterns=["InPrivate"],
        ),
        config_path=Path("/nonexistent/config.toml"),
        overlay=fake_overlay,
        inventory_reader=lambda: inventory,
        pause_reader=lambda: False,
        before_overlay_call=before_overlay_call,
    )

    def force_refresh() -> None:
        try:
            monitor.decision_for_capture(force=True)
        except RuntimeError as exc:
            refresh_errors.append(exc)

    refresh_thread = threading.Thread(target=force_refresh)
    refresh_thread.start()
    assert reached_barrier.wait(timeout=0.5)
    stop_thread = threading.Thread(target=lambda: (monitor.stop(), stop_finished.set()))
    stop_thread.start()
    try:
        assert stop_finished.wait(timeout=0.1) is False
        assert fake_overlay.terminal_marked.is_set() is False
    finally:
        release_barrier.set()
        refresh_thread.join(timeout=1.0)
        stop_thread.join(timeout=1.0)

    assert not refresh_thread.is_alive()
    assert not stop_thread.is_alive()
    assert stop_finished.is_set()
    assert fake_overlay.terminal_marked.is_set()
    assert fake_overlay.render_calls == 1
    assert fake_overlay.clear_calls == 0
    assert len(fake_overlay.snapshots) == 1
    assert fake_overlay.clear_generations == []


def test_request_refresh_wakes_daemon_and_stop_closes_overlay_once(inventory, fake_overlay) -> None:
    calls = 0
    second_refresh = threading.Event()
    call_lock = threading.Lock()

    def count_inventory() -> WindowInventory:
        nonlocal calls
        with call_lock:
            calls += 1
            if calls >= 2:
                second_refresh.set()
        return inventory

    monitor = make_monitor(
        inventory=inventory,
        overlay=fake_overlay,
        inventory_reader=count_inventory,
        watchdog_seconds=10.0,
    )
    monitor.start()
    try:
        assert second_refresh.wait(timeout=0.5) is False
        monitor.request_refresh()
        assert second_refresh.wait(timeout=0.5)
    finally:
        monitor.stop()
        monitor.stop()

    assert fake_overlay.closed.wait(timeout=0.5)
    assert fake_overlay.close_calls == 1
