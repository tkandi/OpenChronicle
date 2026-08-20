from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from pathlib import Path

import pytest

from openchronicle.capture.privacy import DisplayInfo, ScreenRegion, VisibleWindow, WindowInventory
from openchronicle.capture.protection import ProtectionSnapshot, ProtectionState
from openchronicle.capture.protection_monitor import PrivacyProtectionMonitor
from openchronicle.config import CaptureConfig


class FakeOverlay:
    def __init__(self) -> None:
        self.render_result = True
        self.clear_result = True
        self.snapshots: list[ProtectionSnapshot] = []
        self.clear_generations: list[int] = []
        self.close_calls = 0

    def render(self, snapshot: ProtectionSnapshot, timeout: float = 0.5) -> bool:
        self.snapshots.append(snapshot)
        return self.render_result

    def clear(self, generation: int, timeout: float = 0.5) -> bool:
        self.clear_generations.append(generation)
        return self.clear_result

    def close(self) -> None:
        self.close_calls += 1


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

    assert fake_overlay.close_calls == 1
