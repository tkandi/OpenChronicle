from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from openchronicle.capture import protection_monitor as protection_monitor_mod
from openchronicle.capture.privacy import (
    DisplayInfo,
    InventoryReadResult,
    ProtectionFailureReason,
    ScreenRegion,
    VisibleWindow,
    WindowInventory,
)
from openchronicle.capture.privacy_diagnostics import PrivacyDiagnosticsServer
from openchronicle.capture.privacy_diagnostics_guard import (
    DiagnosticsGuardSnapshot,
    DiagnosticsLeaseManager,
)
from openchronicle.capture.protection import (
    ProtectionSnapshot,
    ProtectionState,
    build_protection_snapshot,
    failure_requires_fail_closed,
)
from openchronicle.capture.protection_monitor import (
    PrivacyProtectionMonitor,
    ProtectionDecision,
)
from openchronicle.capture.protection_reason import ProtectionReasonCode
from openchronicle.capture.protection_smoothing import (
    ProtectionPresentationPhase,
    ProtectionPresentationSmoother,
    ProtectionSmoothingError,
)
from openchronicle.capture_pause import CapturePauseDecision, CapturePauseKind
from openchronicle.config import CaptureConfig


class FakeOverlay:
    def __init__(self) -> None:
        self.render_result = True
        self.clear_result = True
        self.render_calls = 0
        self.clear_calls = 0
        self.snapshots: list[ProtectionSnapshot] = []
        self.reason_visibility: list[bool] = []
        self.window_ids_by_generation: dict[int, tuple[int, ...]] = {}
        self.clear_generations: list[int] = []
        self.close_calls = 0
        self.closed = threading.Event()
        self.terminal_marked = threading.Event()
        self.rendered = threading.Event()

    def render(
        self,
        snapshot: ProtectionSnapshot,
        timeout: float = 0.5,
        *,
        overlay_reasons_enabled: bool = True,
    ) -> bool:
        self.render_calls += 1
        if self.terminal_marked.is_set():
            self.rendered.set()
            return False
        self.snapshots.append(snapshot)
        self.reason_visibility.append(overlay_reasons_enabled)
        if self.render_result and snapshot.indicator_style != "off":
            self.window_ids_by_generation[snapshot.generation] = (7, 41)
        self.rendered.set()
        return self.render_result

    def confirmed_window_ids(self, generation: int) -> tuple[int, ...]:
        return self.window_ids_by_generation.get(generation, ())

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


class MutableGuard:
    def __init__(
        self,
        *,
        display_ids: frozenset[int] = frozenset(),
        fail_closed_all: bool = False,
    ) -> None:
        self.display_ids = display_ids
        self.fail_closed_all = fail_closed_all

    def snapshot(self) -> DiagnosticsGuardSnapshot:
        return DiagnosticsGuardSnapshot(self.display_ids, self.fail_closed_all)


class FakeMonotonic:
    def __init__(self, value: float = 10.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class WaitTrackingEvent:
    def __init__(self) -> None:
        self._event = threading.Event()
        self.wait_started = threading.Event()

    def set(self) -> None:
        self._event.set()

    def clear(self) -> None:
        self._event.clear()

    def wait(self, timeout: float | None = None) -> bool:
        self.wait_started.set()
        return self._event.wait(timeout)


class RaisingSmoother:
    def __init__(self, marker: str = "private-value-that-must-not-be-logged") -> None:
        self.marker = marker
        self.reset_calls = 0

    def resolve(self, _snapshot: ProtectionSnapshot, *, now: float):
        raise ProtectionSmoothingError(self.marker)

    def reset(self) -> None:
        self.reset_calls += 1


@pytest.fixture
def inventory() -> WindowInventory:
    displays = (
        DisplayInfo(1, ScreenRegion(0, 0, 100, 100), True),
        DisplayInfo(2, ScreenRegion(100, 0, 100, 100), False),
    )
    windows = (
        VisibleWindow(
            "Edge",
            "edge",
            "InPrivate",
            ScreenRegion(110, 0, 80, 90),
            False,
            window_id=73,
        ),
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
    inventory_reader: Callable[
        [], WindowInventory | InventoryReadResult | None
    ] | None = None,
    pause_reader: Callable[[], bool] | None = None,
    watchdog_seconds: float = 0.01,
    diagnostics_guard_reader: Callable[[], DiagnosticsGuardSnapshot] | None = None,
    decision_listener: Callable[[ProtectionDecision], None] | None = None,
    fail_closed: bool = True,
    smoother: ProtectionPresentationSmoother | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> PrivacyProtectionMonitor:
    cfg = CaptureConfig(
        screenshot_monitor="separate",
        privacy_indicator_style=style,
        deny_window_title_patterns=["InPrivate"],
        screenshot_privacy_fail_closed=fail_closed,
    )
    return PrivacyProtectionMonitor(
        cfg,
        config_path=config_path or Path("/nonexistent/config.toml"),
        overlay=overlay,
        inventory_reader=inventory_reader or (lambda: inventory),
        pause_reader=pause_reader or (lambda: False),
        watchdog_seconds=watchdog_seconds,
        diagnostics_guard_reader=diagnostics_guard_reader,
        decision_listener=decision_listener,
        smoother=smoother,
        monotonic=monotonic,
    )


def test_monitor_uses_injected_clock_for_snapshot_and_cache_freshness(
    tmp_path, inventory, fake_overlay, monkeypatch
) -> None:
    clock = FakeMonotonic()
    monitor = make_monitor(
        config_path=tmp_path / "config.toml",
        inventory=inventory,
        overlay=fake_overlay,
        monotonic=clock,
    )

    def unexpected_wall_clock() -> float:
        raise AssertionError("decision freshness used the module-level monotonic clock")

    monkeypatch.setattr(
        "openchronicle.capture.protection_monitor.time.monotonic",
        unexpected_wall_clock,
    )
    decision = monitor.decision_for_capture(force=True)
    assert decision.snapshot.created_monotonic == 10.0
    assert decision.snapshot.fresh_until == pytest.approx(10.25)


def test_monitor_publishes_quiet_then_configured_style_with_new_generations(
    tmp_path, inventory, fake_overlay
) -> None:
    clock = FakeMonotonic()
    monitor = make_monitor(
        config_path=tmp_path / "config.toml",
        inventory=inventory,
        overlay=fake_overlay,
        monotonic=clock,
    )
    transient = monitor.decision_for_capture(force=True)
    clock.advance(0.8)
    sustained = monitor.decision_for_capture(force=True)

    assert transient.snapshot.state is ProtectionState.PROTECTED
    assert transient.raw_state is ProtectionState.PROTECTED
    assert transient.presentation_phase is ProtectionPresentationPhase.TRANSIENT_PROTECTED
    assert transient.snapshot.indicator_style == "quiet-shield"
    assert transient.overlay_reasons_enabled is False
    assert sustained.raw_state is ProtectionState.PROTECTED
    assert sustained.presentation_phase is ProtectionPresentationPhase.SUSTAINED_PROTECTED
    assert sustained.snapshot.indicator_style == "pill"
    assert sustained.overlay_reasons_enabled is True
    assert fake_overlay.reason_visibility == [False, True]
    assert sustained.snapshot.generation > transient.snapshot.generation
    assert transient.indicator_confirmed and sustained.indicator_confirmed


def test_monitor_holds_capture_until_safe_confirmation_deadline(
    tmp_path: Path,
    inventory: WindowInventory,
    fake_overlay: FakeOverlay,
) -> None:
    safe_inventory = WindowInventory(windows=(), displays=inventory.displays)
    readings = iter([inventory, safe_inventory, safe_inventory, safe_inventory])
    clock = FakeMonotonic()
    monitor = make_monitor(
        config_path=tmp_path / "config.toml",
        inventory=inventory,
        inventory_reader=lambda: next(readings),
        overlay=fake_overlay,
        monotonic=clock,
    )

    protected = monitor.decision_for_capture(force=True)
    clock.advance(0.1)
    first_safe = monitor.decision_for_capture(force=True)
    clock.advance(0.199)
    early_safe = monitor.decision_for_capture(force=True)
    clock.advance(0.001)
    confirmed_safe = monitor.decision_for_capture(force=True)

    assert protected.snapshot.indicator_style == "quiet-shield"
    for held in (first_safe, early_safe):
        assert held.raw_state is ProtectionState.INACTIVE
        assert held.snapshot.state is ProtectionState.PROTECTED
        assert held.snapshot.protected_display_ids == frozenset({2})
        assert held.snapshot.protected_window_ids
        assert held.indicator_confirmed is True
        assert held.indicator_window_ids == (7, 41)
        assert held.presentation_phase is ProtectionPresentationPhase.CLEAR_PENDING
    assert confirmed_safe.snapshot.state is ProtectionState.INACTIVE
    assert confirmed_safe.raw_state is ProtectionState.INACTIVE
    assert confirmed_safe.presentation_phase is ProtectionPresentationPhase.INACTIVE
    assert confirmed_safe.snapshot.generation > early_safe.snapshot.generation


def test_monitor_cancels_clear_when_protection_returns(
    tmp_path, inventory, fake_overlay
) -> None:
    safe_inventory = WindowInventory(windows=(), displays=inventory.displays)
    readings = iter([inventory, safe_inventory, inventory])
    clock = FakeMonotonic()
    monitor = make_monitor(
        config_path=tmp_path / "config.toml",
        inventory=inventory,
        inventory_reader=lambda: next(readings),
        overlay=fake_overlay,
        monotonic=clock,
    )
    monitor.decision_for_capture(force=True)
    clock.advance(0.1)
    monitor.decision_for_capture(force=True)
    clock.advance(0.1)
    returned = monitor.decision_for_capture(force=True)
    assert returned.snapshot.state is ProtectionState.PROTECTED
    assert returned.presentation_phase is ProtectionPresentationPhase.TRANSIENT_PROTECTED
    assert returned.snapshot.indicator_style == "quiet-shield"


def test_worker_wakes_at_promotion_deadline_without_another_timer(
    tmp_path, inventory, fake_overlay
) -> None:
    before = {thread.ident for thread in threading.enumerate()}
    inventory_reads = 0

    def read_inventory() -> WindowInventory:
        nonlocal inventory_reads
        inventory_reads += 1
        return inventory

    monitor = make_monitor(
        config_path=tmp_path / "config.toml",
        inventory=inventory,
        inventory_reader=read_inventory,
        overlay=fake_overlay,
        smoother=ProtectionPresentationSmoother(promotion_seconds=0.03),
        watchdog_seconds=10.0,
    )
    monitor.start()
    try:
        deadline = time.monotonic() + 0.3
        while time.monotonic() < deadline:
            if [snapshot.indicator_style for snapshot in fake_overlay.snapshots][:2] == [
                "quiet-shield",
                "pill",
            ]:
                break
            time.sleep(0.005)
        assert [snapshot.indicator_style for snapshot in fake_overlay.snapshots][:2] == [
            "quiet-shield",
            "pill",
        ]
        assert inventory_reads >= 2
        created = [
            thread
            for thread in threading.enumerate()
            if thread.ident not in before and thread.name == "privacy-protection-monitor"
        ]
        assert len(created) == 1
    finally:
        renders_before_stop = fake_overlay.render_calls
        monitor.stop()
        time.sleep(0.05)
    assert fake_overlay.render_calls == renders_before_stop


def test_external_deadline_publication_wakes_worker_for_fresh_promotion(
    tmp_path, inventory, fake_overlay
) -> None:
    safe_inventory = WindowInventory(windows=(), displays=inventory.displays)
    current_inventory = safe_inventory
    inventory_reads = 0
    inventory_lock = threading.Lock()

    def read_inventory() -> WindowInventory:
        nonlocal inventory_reads
        with inventory_lock:
            inventory_reads += 1
            return current_inventory

    monitor = make_monitor(
        config_path=tmp_path / "config.toml",
        inventory=inventory,
        inventory_reader=read_inventory,
        overlay=fake_overlay,
        smoother=ProtectionPresentationSmoother(promotion_seconds=0.05),
        watchdog_seconds=10.0,
    )
    wake = WaitTrackingEvent()
    monitor._wake = wake  # type: ignore[assignment]
    monitor.start()
    try:
        assert wake.wait_started.wait(timeout=0.5)
        current_inventory = inventory
        external_decisions: list[ProtectionDecision] = []
        external_errors: list[BaseException] = []

        def publish_transient() -> None:
            try:
                external_decisions.append(monitor.decision_for_capture(force=True))
            except BaseException as exc:  # pragma: no cover - diagnostic capture
                external_errors.append(exc)

        publisher = threading.Thread(target=publish_transient)
        publisher.start()
        publisher.join(timeout=0.5)
        assert not publisher.is_alive()
        assert external_errors == []
        assert external_decisions[0].snapshot.indicator_style == "quiet-shield"

        deadline = time.monotonic() + 0.4
        while time.monotonic() < deadline:
            if any(
                snapshot.indicator_style == "pill"
                for snapshot in fake_overlay.snapshots
            ):
                break
            time.sleep(0.005)

        assert any(
            snapshot.indicator_style == "pill" for snapshot in fake_overlay.snapshots
        )
        with inventory_lock:
            assert 3 <= inventory_reads < 10
    finally:
        monitor.stop()


def test_off_keeps_effective_protection_without_overlay_ids(inventory, fake_overlay) -> None:
    monitor = make_monitor(inventory=inventory, overlay=fake_overlay, style="off")
    decision = monitor.decision_for_capture(force=True)
    assert decision.snapshot.state is ProtectionState.PROTECTED
    assert decision.snapshot.indicator_style == "off"
    assert decision.indicator_confirmed is True
    assert decision.indicator_window_ids == ()


def test_pause_and_inventory_failure_bypass_smoothing(inventory, fake_overlay) -> None:
    paused = make_monitor(
        inventory=inventory,
        overlay=fake_overlay,
        pause_reader=lambda: CapturePauseDecision(
            paused=True,
            kind=CapturePauseKind.INDEFINITE,
        ),
    ).decision_for_capture(force=True)
    assert paused.snapshot.state is ProtectionState.PAUSED
    assert paused.presentation_phase is ProtectionPresentationPhase.BYPASS
    assert paused.snapshot.indicator_style == "pill"

    failed = make_monitor(
        inventory=inventory,
        overlay=FakeOverlay(),
        inventory_reader=lambda: InventoryReadResult(
            None,
            ProtectionFailureReason.INVENTORY_UNAVAILABLE,
        ),
    ).decision_for_capture(force=True)
    assert failed.snapshot.state is ProtectionState.FAILED
    assert failed.raw_state is ProtectionState.FAILED
    assert failed.presentation_phase is ProtectionPresentationPhase.BYPASS
    assert failed.snapshot.indicator_style == "pill"


@pytest.mark.parametrize(
    "failure_reason",
    [
        ProtectionFailureReason.ACTIVE_WINDOW_UNMAPPED,
        ProtectionFailureReason.SENSITIVE_WINDOW_UNMAPPED,
    ],
)
def test_unmapped_failures_bypass_smoothing_without_transient_shield(
    inventory,
    failure_reason: ProtectionFailureReason,
) -> None:
    decision = make_monitor(
        inventory=inventory,
        overlay=FakeOverlay(),
        inventory_reader=lambda: InventoryReadResult(None, failure_reason),
    ).decision_for_capture(force=True)

    assert decision.raw_state is ProtectionState.FAILED
    assert decision.snapshot.state is ProtectionState.FAILED
    assert decision.presentation_phase is ProtectionPresentationPhase.BYPASS
    assert decision.snapshot.indicator_style == "pill"
    assert decision.snapshot.indicator_style != "quiet-shield"


def test_listener_and_wait_use_acknowledged_effective_decision(
    inventory, fake_overlay
) -> None:
    published: list[ProtectionDecision] = []
    monitor = make_monitor(
        inventory=inventory,
        overlay=fake_overlay,
        decision_listener=published.append,
    )
    transient = monitor.decision_for_capture(force=True)
    assert published == [transient]
    assert transient.snapshot.indicator_style == "quiet-shield"
    assert transient.snapshot.display_reasons.reasons
    assert monitor.wait_for_display_protection(
        2,
        after_generation=0,
        timeout=0.1,
    ) == transient.snapshot.generation

    fake_overlay.render_result = False
    unconfirmed = monitor.decision_for_capture(force=True)
    assert unconfirmed.indicator_confirmed is False
    assert unconfirmed.indicator_window_ids == ()
    assert monitor.wait_for_display_protection(
        2,
        after_generation=transient.snapshot.generation,
        timeout=0.01,
    ) is None


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


def test_monitor_accepts_typed_pause_decision_and_preserves_reason(inventory, fake_overlay) -> None:
    pause_decision = CapturePauseDecision(paused=True, kind=CapturePauseKind.TIMED)
    monitor = PrivacyProtectionMonitor(
        CaptureConfig(privacy_indicator_style="pill"),
        config_path=Path("/nonexistent/config.toml"),
        overlay=fake_overlay,
        inventory_reader=lambda: inventory,
        pause_reader=lambda: pause_decision,
    )

    protected = monitor.decision_for_capture(force=True)

    assert protected.snapshot.state is ProtectionState.PAUSED
    assert protected.snapshot.reasons_for_display(1)[0].code is ProtectionReasonCode.TIMED_PAUSE


def test_required_overlay_timeout_is_unconfirmed(inventory, fake_overlay) -> None:
    fake_overlay.render_result = False
    monitor = make_monitor(inventory=inventory, overlay=fake_overlay)

    decision = monitor.decision_for_capture(force=True)

    assert decision.snapshot.state is ProtectionState.PROTECTED
    assert decision.indicator_confirmed is False


def test_diagnostics_guard_is_published_and_waitable(inventory, fake_overlay) -> None:
    guard = MutableGuard(display_ids=frozenset({1}))
    published: list[ProtectionDecision] = []
    monitor = make_monitor(
        inventory=inventory,
        overlay=fake_overlay,
        diagnostics_guard_reader=guard.snapshot,
        decision_listener=published.append,
    )

    decision = monitor.decision_for_capture(force=True)

    assert 1 in decision.snapshot.protected_display_ids
    assert decision.snapshot.reasons_for_display(1)[0].code.value == "diagnostics_reveal"
    assert monitor.wait_for_display_protection(
        1,
        after_generation=0,
        timeout=0.1,
    ) == decision.snapshot.generation
    assert published == [decision]


def test_guard_only_monitor_ignores_normal_rules_and_becomes_inactive_after_release(
    inventory,
    fake_overlay,
) -> None:
    guard = MutableGuard(display_ids=frozenset({1}))
    clock = FakeMonotonic()
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
        diagnostics_guard_reader=guard.snapshot,
        diagnostics_guard_only=True,
        monotonic=clock,
    )

    guarded = monitor.decision_for_capture(force=True)
    guard.display_ids = frozenset()
    monitor.request_refresh()
    held = monitor.decision_for_capture(force=True)
    clock.advance(0.2)
    released = monitor.decision_for_capture(force=True)

    assert guarded.snapshot.state is ProtectionState.PROTECTED
    assert guarded.snapshot.protected_display_ids == frozenset({1})
    assert [reason.code for reason in guarded.snapshot.reasons_for_display(1)] == [
        ProtectionReasonCode.DIAGNOSTICS_REVEAL
    ]
    assert held.presentation_phase is ProtectionPresentationPhase.CLEAR_PENDING
    assert held.snapshot.protected_display_ids == frozenset({1})
    assert released.snapshot.state is ProtectionState.INACTIVE
    assert released.snapshot.protected_display_ids == frozenset()
    assert released.snapshot.display_reasons.reasons == ()


def test_wait_rejects_stale_or_unconfirmed_display_generation(
    inventory,
    fake_overlay,
) -> None:
    guard = MutableGuard(display_ids=frozenset({1}))
    fake_overlay.render_result = False
    monitor = make_monitor(
        inventory=inventory,
        overlay=fake_overlay,
        diagnostics_guard_reader=guard.snapshot,
    )
    decision = monitor.decision_for_capture(force=True)

    assert monitor.wait_for_display_protection(
        1,
        after_generation=0,
        timeout=0.01,
    ) is None
    fake_overlay.render_result = True
    confirmed = monitor.decision_for_capture(force=True)
    assert monitor.wait_for_display_protection(
        1,
        after_generation=confirmed.snapshot.generation,
        timeout=0.01,
    ) is None
    assert confirmed.snapshot.generation > decision.snapshot.generation


def test_invalid_or_unreadable_diagnostics_guard_fails_closed(
    inventory,
    fake_overlay,
) -> None:
    marker = "private-guard-reader-detail"

    def fail_guard_read() -> DiagnosticsGuardSnapshot:
        raise OSError(marker)

    monitor = make_monitor(
        inventory=inventory,
        overlay=fake_overlay,
        diagnostics_guard_reader=fail_guard_read,
    )

    decision = monitor.decision_for_capture(force=True)

    assert decision.snapshot.state is ProtectionState.FAILED
    assert decision.snapshot.protected_display_ids == frozenset({1, 2})
    assert decision.snapshot.diagnostics_guard_invalid is True
    assert marker not in " ".join(reason.code.value for reason in decision.snapshot.display_reasons.reasons)


@pytest.mark.parametrize(
    "reason",
    [
        ProtectionFailureReason.INVENTORY_UNAVAILABLE,
        ProtectionFailureReason.HELPER_EXIT,
    ],
)
@pytest.mark.parametrize("cleanup", ["release", "prune"])
def test_real_active_guard_keeps_inventory_failure_closed_until_safe_cleanup(
    tmp_path: Path,
    inventory: WindowInventory,
    fake_overlay: FakeOverlay,
    reason: ProtectionFailureReason,
    cleanup: str,
) -> None:
    process_alive = True
    manager = DiagnosticsLeaseManager(
        tmp_path / "privacy-reveal.guard",
        process_alive=lambda _pid: process_alive,
    )
    manager.load()
    lease = manager.acquire(pid=os.getpid(), display_id=2)
    monitor = make_monitor(
        inventory=inventory,
        overlay=fake_overlay,
        inventory_reader=lambda: InventoryReadResult(None, reason),
        diagnostics_guard_reader=manager.snapshot,
        fail_closed=False,
    )
    cfg = CaptureConfig(screenshot_privacy_fail_closed=False)

    protected = monitor.decision_for_capture(force=True)

    assert protected.snapshot.state is ProtectionState.FAILED
    assert protected.snapshot.failure_reason is reason
    assert protected.snapshot.diagnostics_guard_active is True
    assert protected.snapshot.diagnostics_guard_invalid is False
    assert protected.snapshot.protected_regions == []
    assert failure_requires_fail_closed(cfg, protected.snapshot) is True
    assert [item.code for item in protected.snapshot.reasons_for_display(None)] == [
        ProtectionReasonCode(reason.value)
    ]

    if cleanup == "release":
        manager.release(lease.lease_id, pid=os.getpid())
    else:
        process_alive = False
        manager.prune_dead()
    recovered = monitor.decision_for_capture(force=True)

    assert recovered.snapshot.state is ProtectionState.FAILED
    assert recovered.snapshot.diagnostics_guard_active is False
    assert failure_requires_fail_closed(cfg, recovered.snapshot) is False


def test_real_guard_display_missing_from_valid_inventory_fails_closed_globally(
    tmp_path: Path,
    inventory: WindowInventory,
    fake_overlay: FakeOverlay,
) -> None:
    manager = DiagnosticsLeaseManager(
        tmp_path / "privacy-reveal.guard",
        process_alive=lambda _pid: True,
    )
    manager.load()
    lease = manager.acquire(pid=os.getpid(), display_id=99)
    safe_inventory = WindowInventory(windows=(), displays=inventory.displays)
    monitor = make_monitor(
        inventory=safe_inventory,
        overlay=fake_overlay,
        diagnostics_guard_reader=manager.snapshot,
        fail_closed=False,
    )
    cfg = CaptureConfig(screenshot_privacy_fail_closed=False)

    protected = monitor.decision_for_capture(force=True)

    assert protected.snapshot.state is ProtectionState.FAILED
    assert protected.snapshot.diagnostics_guard_active is True
    assert protected.snapshot.diagnostics_guard_invalid is True
    assert protected.snapshot.protected_display_ids == frozenset({1, 2})
    assert failure_requires_fail_closed(cfg, protected.snapshot) is True
    assert [item.code for item in protected.snapshot.reasons_for_display(None)] == [
        ProtectionReasonCode.DIAGNOSTICS_GUARD_INVALID
    ]

    manager.release(lease.lease_id, pid=os.getpid())
    recovered = monitor.decision_for_capture(force=True)
    assert recovered.snapshot.state is ProtectionState.INACTIVE
    assert recovered.snapshot.diagnostics_guard_active is False


def test_listener_exception_is_sanitized_and_does_not_stop_monitor(
    inventory,
    fake_overlay,
) -> None:
    marker = "private-listener-body"

    def broken_listener(_decision: ProtectionDecision) -> None:
        raise RuntimeError(marker)

    monitor = make_monitor(
        inventory=inventory,
        overlay=fake_overlay,
        decision_listener=broken_listener,
    )

    messages: list[str] = []
    handler = logging.Handler()
    handler.emit = lambda record: messages.append(record.getMessage())  # type: ignore[method-assign]
    capture_logger = logging.getLogger("openchronicle.capture")
    capture_logger.addHandler(handler)
    try:
        first = monitor.decision_for_capture(force=True)
        second = monitor.decision_for_capture(force=True)
    finally:
        capture_logger.removeHandler(handler)

    assert second.snapshot.generation > first.snapshot.generation
    assert messages == [
        "privacy protection listener failed: RuntimeError",
        "privacy protection listener failed: RuntimeError",
    ]
    assert marker not in "\n".join(messages)


def test_transient_and_sustained_use_latest_hot_loaded_style_and_position(
    tmp_path, inventory, fake_overlay
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[capture]\nprivacy_indicator_style="pill"\n'
        'privacy_indicator_placement="bottom-left-flush"\n'
    )
    clock = FakeMonotonic()
    monitor = make_monitor(
        config_path=config_path,
        inventory=inventory,
        overlay=fake_overlay,
        monotonic=clock,
    )
    first = monitor.decision_for_capture(force=True)
    old_mtime = config_path.stat().st_mtime_ns
    config_path.write_text(
        '[capture]\nprivacy_indicator_style="border"\n'
        'privacy_indicator_placement="bottom-right-work-area"\n'
    )
    os.utime(config_path, ns=(old_mtime + 1, old_mtime + 1))
    clock.advance(0.4)
    transient = monitor.decision_for_capture(force=True)
    clock.advance(0.4)
    sustained = monitor.decision_for_capture(force=True)
    assert first.snapshot.indicator_style == "quiet-shield"
    assert transient.snapshot.indicator_style == "quiet-shield"
    assert transient.snapshot.indicator_placement == "bottom-right-work-area"
    assert sustained.snapshot.indicator_style == "border"


def test_style_reload_retries_a_recovered_config_with_unchanged_mtime(tmp_path, inventory, fake_overlay) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("[capture\n")
    monitor = make_monitor(
        config_path=config_path,
        inventory=inventory,
        overlay=fake_overlay,
        style="border",
        smoother=ProtectionPresentationSmoother(promotion_seconds=0),
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
    monitor = make_monitor(
        config_path=config_path,
        inventory=inventory,
        overlay=fake_overlay,
        smoother=ProtectionPresentationSmoother(promotion_seconds=0),
    )

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


def test_non_forced_decision_covers_refresh_requested_during_refresh(
    inventory, fake_overlay
) -> None:
    safe_inventory = WindowInventory(
        windows=(
            VisibleWindow("Cursor", "cursor", "main.py", ScreenRegion(0, 0, 80, 90), True),
        ),
        displays=inventory.displays,
    )
    second_read_started = threading.Event()
    release_second_read = threading.Event()
    read_count = 0
    read_lock = threading.Lock()

    def read_inventory() -> WindowInventory:
        nonlocal read_count
        with read_lock:
            read_count += 1
            current_read = read_count
        if current_read == 2:
            second_read_started.set()
            assert release_second_read.wait(timeout=1.0)
            return safe_inventory
        return safe_inventory if current_read == 1 else inventory

    monitor = make_monitor(
        inventory=inventory,
        overlay=fake_overlay,
        inventory_reader=read_inventory,
        watchdog_seconds=10.0,
    )
    first = monitor.decision_for_capture(force=True)
    monitor.request_refresh()
    decisions = []
    errors: list[BaseException] = []

    def validate() -> None:
        try:
            decisions.append(monitor.decision_for_capture(force=False))
        except BaseException as exc:  # pragma: no cover - diagnostic capture
            errors.append(exc)

    validation_thread = threading.Thread(target=validate)
    validation_thread.start()
    if not second_read_started.wait(timeout=0.5):
        release_second_read.set()
        validation_thread.join(timeout=1.0)
        pytest.fail("pending request reused a pre-request cached decision")

    monitor.request_refresh()
    release_second_read.set()
    validation_thread.join(timeout=1.0)

    assert not validation_thread.is_alive()
    assert errors == []
    assert len(decisions) == 1
    assert decisions[0].snapshot.state is ProtectionState.PROTECTED
    assert decisions[0].covered_request_epoch == 2
    assert decisions[0].covered_request_epoch > first.covered_request_epoch
    assert read_count == 3


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


def test_smoothing_invariant_failure_publishes_sanitized_fail_closed_decision(
    inventory, fake_overlay
) -> None:
    app_marker = "private-app-value"
    bundle_marker = "private-bundle-value"
    title_marker = "private-title-value"
    url_title_marker = "private-url-title.example"
    rule_marker = "private-rule-pattern"
    url_rule_marker = "private-url-rule-pattern"
    exception_marker = "private-exception-body"
    private_inventory = WindowInventory(
        windows=(
            VisibleWindow(
                app_marker,
                bundle_marker,
                f"{title_marker} {rule_marker}",
                ScreenRegion(110, 0, 80, 90),
                alternate_title=f"https://{url_title_marker}/{url_rule_marker}",
                window_id=73,
            ),
        ),
        displays=inventory.displays,
    )
    cfg = CaptureConfig(
        screenshot_monitor="separate",
        privacy_indicator_style="pill",
        deny_window_title_patterns=[rule_marker, url_rule_marker],
        screenshot_privacy_fail_closed=False,
    )
    raw_snapshot = build_protection_snapshot(
        cfg,
        private_inventory,
        paused=False,
        generation=1,
        now=10.0,
    )
    private_markers = (
        app_marker,
        bundle_marker,
        title_marker,
        url_title_marker,
        rule_marker,
        url_rule_marker,
        exception_marker,
    )
    raw_reasons = repr(raw_snapshot.display_reasons.reasons)
    for marker in private_markers[:-1]:
        assert marker in raw_reasons

    smoother = RaisingSmoother(exception_marker)
    monitor = PrivacyProtectionMonitor(
        cfg,
        config_path=Path("/nonexistent/config.toml"),
        overlay=fake_overlay,
        inventory_reader=lambda: private_inventory,
        pause_reader=lambda: False,
        smoother=smoother,  # type: ignore[arg-type]
    )
    records: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.emit = lambda record: records.append(record)  # type: ignore[method-assign]
    capture_logger = logging.getLogger("openchronicle.capture")
    original_propagate = capture_logger.propagate
    original_level = capture_logger.level
    capture_logger.addHandler(handler)
    capture_logger.propagate = False
    capture_logger.setLevel(logging.DEBUG)
    try:
        first = monitor.decision_for_capture(force=True)
        second = monitor.decision_for_capture(force=True)
    finally:
        capture_logger.removeHandler(handler)
        capture_logger.propagate = original_propagate
        capture_logger.setLevel(original_level)

    for decision in (first, second):
        assert decision.snapshot.state is ProtectionState.FAILED
        assert decision.raw_state is ProtectionState.PROTECTED
        assert (
            decision.snapshot.failure_reason
            is ProtectionFailureReason.PRESENTATION_STATE_INVALID
        )
        assert decision.presentation_phase is ProtectionPresentationPhase.BYPASS
        assert decision.overlay_reasons_enabled is True
        assert failure_requires_fail_closed(cfg, decision.snapshot) is True
        assert decision.snapshot.protected_display_ids == frozenset()
        assert decision.snapshot.active_candidate_display_ids == frozenset()
        assert decision.snapshot.protected_window_ids == frozenset()
        assert decision.snapshot.protected_window_regions == ()
        assert decision.snapshot.window_filterable is False
        assert [reason.to_payload("exact") for reason in decision.snapshot.reasons_for_display(None)] == [
            {"code": "presentation_state_invalid", "display_id": None}
        ]
        payload = PrivacyDiagnosticsServer._snapshot_payload(
            decision,
            detail="category",
            created_at="2026-08-25T00:00:00Z",
        )
        assert payload["raw_state"] == "protected"
        assert payload["state"] == "failed"
        assert payload["presentation_phase"] == "bypass"

    assert second.snapshot.generation > first.snapshot.generation
    assert smoother.reset_calls == 2
    assert fake_overlay.render_calls == 2
    assert fake_overlay.clear_calls == 0
    assert fake_overlay.snapshots == [first.snapshot, second.snapshot]
    assert fake_overlay.reason_visibility == [True, True]
    with monitor._state_lock:
        assert monitor._next_smoothing_deadline is None

    rendered = "\n".join(record.getMessage() for record in records)
    for marker in private_markers:
        assert marker not in rendered
    assert [record.getMessage() for record in records if record.levelno == logging.WARNING] == [
        "privacy protection smoothing failed: ProtectionSmoothingError",
        "privacy protection failed closed: reason=presentation_state_invalid",
        "privacy protection smoothing failed: ProtectionSmoothingError",
    ]
    assert capture_logger.level == original_level


def test_fail_open_inventory_failure_clears_indicator_without_visual_confirmation(
    inventory, fake_overlay
) -> None:
    cfg = CaptureConfig(
        privacy_indicator_style="pill",
        screenshot_privacy_fail_closed=False,
    )
    monitor = PrivacyProtectionMonitor(
        cfg,
        config_path=Path("/nonexistent/config.toml"),
        overlay=fake_overlay,
        inventory_reader=lambda: InventoryReadResult(
            None, ProtectionFailureReason.HELPER_EXIT
        ),
        pause_reader=lambda: False,
    )

    decision = monitor.decision_for_capture(force=True)

    assert decision.snapshot.state is ProtectionState.FAILED
    assert decision.indicator_confirmed is False
    assert fake_overlay.render_calls == 0
    assert fake_overlay.clear_generations == [decision.snapshot.generation]


@pytest.mark.parametrize("privacy_mode", ["mask-window", "exclude-window"])
def test_window_filtered_inventory_failure_renders_failed_indicator_and_logs_closed_policy(
    inventory,
    fake_overlay,
    privacy_mode: str,
) -> None:
    cfg = CaptureConfig(
        screenshot_privacy_mode=privacy_mode,
        privacy_indicator_style="pill",
        screenshot_privacy_fail_closed=False,
    )
    monitor = PrivacyProtectionMonitor(
        cfg,
        config_path=Path("/nonexistent/config.toml"),
        overlay=fake_overlay,
        inventory_reader=lambda: InventoryReadResult(
            None, ProtectionFailureReason.HELPER_EXIT
        ),
        pause_reader=lambda: False,
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

    assert failure_requires_fail_closed(cfg, decision.snapshot) is True
    assert decision.snapshot.state is ProtectionState.FAILED
    assert decision.indicator_confirmed is True
    assert fake_overlay.render_calls == 1
    assert fake_overlay.snapshots == [decision.snapshot]
    assert fake_overlay.clear_calls == 0
    assert messages == ["privacy protection failed closed: reason=helper_exit"]


def test_pause_reader_failure_stays_closed_when_inventory_policy_is_fail_open(
    ac_root, monkeypatch, inventory, fake_overlay,
) -> None:
    marker = "private-pause-marker-path"
    pause_available = False
    pause_path = ac_root / ".paused"
    safe_inventory = WindowInventory(windows=(), displays=inventory.displays)
    original_read_bytes = Path.read_bytes

    def read_pause_file(path: Path) -> bytes:
        if path == pause_path and not pause_available:
            raise OSError(marker)
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", read_pause_file)

    cfg = CaptureConfig(
        privacy_indicator_style="pill",
        screenshot_privacy_fail_closed=False,
    )
    monitor = PrivacyProtectionMonitor(
        cfg,
        config_path=Path("/nonexistent/config.toml"),
        overlay=fake_overlay,
        inventory_reader=lambda: safe_inventory,
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

        assert decision.snapshot.state is ProtectionState.FAILED
        assert (
            decision.snapshot.failure_reason
            is ProtectionFailureReason.PAUSE_STATE_UNAVAILABLE
        )
        assert decision.indicator_confirmed is True
        assert fake_overlay.render_calls == 1
        assert fake_overlay.clear_calls == 0
        assert messages == [
            "privacy protection pause read failed: OSError",
            "privacy protection failed closed: reason=pause_state_unavailable",
        ]
        assert marker not in "\n".join(messages)

        pause_available = True
        recovered = monitor.decision_for_capture(force=True)
    finally:
        capture_logger.removeHandler(handler)
        capture_logger.propagate = original_propagate

    assert recovered.snapshot.state is ProtectionState.INACTIVE
    assert fake_overlay.clear_calls == 1


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


def test_failed_snapshot_logs_only_on_failure_transition(inventory, fake_overlay) -> None:
    result = InventoryReadResult(None, ProtectionFailureReason.HELPER_EXIT)
    current_result: WindowInventory | InventoryReadResult = result

    def read_inventory() -> WindowInventory | InventoryReadResult:
        return current_result

    monitor = make_monitor(
        inventory=inventory,
        overlay=fake_overlay,
        inventory_reader=read_inventory,
    )

    messages: list[str] = []
    handler = logging.Handler()
    handler.emit = lambda record: messages.append(record.getMessage())  # type: ignore[method-assign]
    capture_logger = logging.getLogger("openchronicle.capture")
    original_propagate = capture_logger.propagate
    capture_logger.addHandler(handler)
    capture_logger.propagate = False
    try:
        monitor.decision_for_capture(force=True)
        monitor.decision_for_capture(force=True)
        current_result = inventory
        monitor.decision_for_capture(force=True)
        current_result = result
        monitor.decision_for_capture(force=True)
    finally:
        capture_logger.removeHandler(handler)
        capture_logger.propagate = original_propagate

    failures = [
        message
        for message in messages
        if message.startswith("privacy protection failed closed:")
    ]
    assert failures == [
        "privacy protection failed closed: reason=helper_exit",
        "privacy protection failed closed: reason=helper_exit",
    ]


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


def test_stop_waits_for_overlay_cleanup_to_finish(inventory) -> None:
    close_started = threading.Event()
    release_close = threading.Event()
    close_finished = threading.Event()

    class BlockingCloseOverlay(FakeOverlay):
        def close(self) -> None:
            self.close_calls += 1
            close_started.set()
            assert release_close.wait(timeout=1.0)
            self.closed.set()
            close_finished.set()

    overlay = BlockingCloseOverlay()
    monitor = make_monitor(inventory=inventory, overlay=overlay)
    stop_finished = threading.Event()
    stop_thread = threading.Thread(target=lambda: (monitor.stop(), stop_finished.set()))

    stop_thread.start()
    assert close_started.wait(timeout=0.5)
    try:
        assert stop_finished.wait(timeout=0.1) is False
        assert close_finished.is_set() is False
    finally:
        release_close.set()
        stop_thread.join(timeout=1.0)

    assert not stop_thread.is_alive()
    assert stop_finished.is_set()
    assert close_finished.is_set()
    assert overlay.close_calls == 1


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


def test_stop_marks_overlay_terminal_while_a_pre_overlay_barrier_is_blocked(
    inventory, fake_overlay
) -> None:
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
        assert stop_finished.wait(timeout=0.5)
        assert fake_overlay.terminal_marked.is_set()
        assert fake_overlay.closed.is_set()
    finally:
        release_barrier.set()
        refresh_thread.join(timeout=1.0)
        stop_thread.join(timeout=1.0)

    assert not refresh_thread.is_alive()
    assert not stop_thread.is_alive()
    assert stop_finished.is_set()
    assert fake_overlay.terminal_marked.is_set()
    assert refresh_errors and "stopped" in str(refresh_errors[0])
    assert fake_overlay.render_calls == 0
    assert fake_overlay.clear_calls == 0
    assert fake_overlay.snapshots == []
    assert fake_overlay.clear_generations == []


def test_stop_after_before_overlay_call_prevents_helper_entry(
    inventory, fake_overlay, monkeypatch
) -> None:
    before_finished = threading.Event()
    gate_reached = threading.Event()
    release_gate = threading.Event()
    stop_finished = threading.Event()
    refresh_errors: list[Exception] = []

    def before_overlay_call() -> None:
        before_finished.set()

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
    original_begin = getattr(monitor, "_begin_overlay_call", lambda: True)

    def blocked_begin() -> bool:
        gate_reached.set()
        assert release_gate.wait(timeout=1.0)
        return original_begin()

    monkeypatch.setattr(monitor, "_begin_overlay_call", blocked_begin, raising=False)

    def force_refresh() -> None:
        try:
            monitor.decision_for_capture(force=True)
        except RuntimeError as exc:
            refresh_errors.append(exc)

    refresh_thread = threading.Thread(target=force_refresh)
    refresh_thread.start()
    assert before_finished.wait(timeout=0.5)
    try:
        assert gate_reached.wait(timeout=0.5)
        stop_thread = threading.Thread(target=lambda: (monitor.stop(), stop_finished.set()))
        stop_thread.start()
        assert stop_finished.wait(timeout=0.5)
    finally:
        release_gate.set()
        refresh_thread.join(timeout=1.0)
        if "stop_thread" in locals():
            stop_thread.join(timeout=1.0)

    assert not refresh_thread.is_alive()
    assert not stop_thread.is_alive()
    assert refresh_errors and "stopped" in str(refresh_errors[0])
    assert fake_overlay.render_calls == 0
    assert fake_overlay.clear_calls == 0


def test_stop_signals_terminal_but_waits_for_started_helper_before_close(inventory) -> None:
    render_started = threading.Event()
    release_render = threading.Event()
    render_finished = threading.Event()
    stop_finished = threading.Event()

    class BlockingRenderOverlay(FakeOverlay):
        def render(
            self,
            snapshot: ProtectionSnapshot,
            timeout: float = 0.5,
            *,
            overlay_reasons_enabled: bool = True,
        ) -> bool:
            self.render_calls += 1
            render_started.set()
            assert release_render.wait(timeout=1.0)
            self.snapshots.append(snapshot)
            self.reason_visibility.append(overlay_reasons_enabled)
            render_finished.set()
            return True

    overlay = BlockingRenderOverlay()
    monitor = make_monitor(inventory=inventory, overlay=overlay)
    refresh_errors: list[Exception] = []

    def force_refresh() -> None:
        try:
            monitor.decision_for_capture(force=True)
        except RuntimeError as exc:
            refresh_errors.append(exc)

    refresh_thread = threading.Thread(target=force_refresh)
    refresh_thread.start()
    assert render_started.wait(timeout=0.5)
    stop_thread = threading.Thread(target=lambda: (monitor.stop(), stop_finished.set()))
    stop_thread.start()
    try:
        assert stop_finished.wait(timeout=0.1) is False
        assert overlay.terminal_marked.is_set() is True
        assert overlay.closed.is_set() is False
    finally:
        release_render.set()
        refresh_thread.join(timeout=1.0)
        stop_thread.join(timeout=1.0)

    assert render_finished.is_set()
    assert stop_finished.is_set()
    assert overlay.terminal_marked.is_set()
    assert overlay.closed.is_set()
    assert refresh_errors and "stopped" in str(refresh_errors[0])


def test_stop_signals_terminal_before_draining_terminal_dependent_helper(
    inventory,
) -> None:
    render_started = threading.Event()
    stop_finished = threading.Event()
    order: list[str] = []

    class TerminalDependentOverlay(FakeOverlay):
        def render(
            self,
            snapshot: ProtectionSnapshot,
            timeout: float = 0.5,
            *,
            overlay_reasons_enabled: bool = True,
        ) -> bool:
            self.render_calls += 1
            render_started.set()
            assert self.terminal_marked.wait(timeout=2.0)
            order.append("helper-exit")
            return True

        def mark_terminal(self) -> None:
            order.append("terminal")
            super().mark_terminal()

        def close(self) -> None:
            order.append("close")
            super().close()

    overlay = TerminalDependentOverlay()
    monitor = make_monitor(inventory=inventory, overlay=overlay)
    refresh_errors: list[Exception] = []

    def force_refresh() -> None:
        try:
            monitor.decision_for_capture(force=True)
        except RuntimeError as exc:
            refresh_errors.append(exc)

    refresh_thread = threading.Thread(target=force_refresh)
    refresh_thread.start()
    assert render_started.wait(timeout=0.5)
    stop_thread = threading.Thread(target=lambda: (monitor.stop(), stop_finished.set()))
    stop_thread.start()
    try:
        assert stop_finished.wait(timeout=0.5)
    finally:
        overlay.terminal_marked.set()
        refresh_thread.join(timeout=1.0)
        stop_thread.join(timeout=1.0)

    assert order == ["terminal", "helper-exit", "close"]
    assert overlay.close_calls == 1
    assert refresh_errors and "stopped" in str(refresh_errors[0])


def test_stop_bounds_noncompliant_helper_and_defers_close_to_helper_thread(
    inventory,
) -> None:
    render_started = threading.Event()
    release_render = threading.Event()
    stop_finished = threading.Event()
    stop_elapsed: list[float] = []
    helper_thread_ids: list[int] = []
    close_thread_ids: list[int] = []

    class NoncompliantOverlay(FakeOverlay):
        def render(
            self,
            snapshot: ProtectionSnapshot,
            timeout: float = 0.5,
            *,
            overlay_reasons_enabled: bool = True,
        ) -> bool:
            self.render_calls += 1
            helper_thread_ids.append(threading.get_ident())
            render_started.set()
            assert release_render.wait(timeout=2.0)
            return True

        def close(self) -> None:
            close_thread_ids.append(threading.get_ident())
            super().close()

    overlay = NoncompliantOverlay()
    monitor = make_monitor(
        inventory=inventory,
        overlay=overlay,
        monotonic=FakeMonotonic(),
    )
    refresh_errors: list[Exception] = []

    def force_refresh() -> None:
        try:
            monitor.decision_for_capture(force=True)
        except RuntimeError as exc:
            refresh_errors.append(exc)

    refresh_thread = threading.Thread(target=force_refresh)
    refresh_thread.start()
    assert render_started.wait(timeout=0.5)
    drain_bound = getattr(
        protection_monitor_mod,
        "_OVERLAY_DRAIN_TIMEOUT_SECONDS",
        0.75,
    )

    def stop_monitor() -> None:
        started = time.monotonic()
        monitor.stop()
        stop_elapsed.append(time.monotonic() - started)
        stop_finished.set()

    records: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.emit = lambda record: records.append(record)  # type: ignore[method-assign]
    capture_logger = logging.getLogger("openchronicle.capture")
    original_propagate = capture_logger.propagate
    original_level = capture_logger.level
    capture_logger.addHandler(handler)
    capture_logger.propagate = False
    capture_logger.setLevel(logging.DEBUG)
    try:
        stop_thread = threading.Thread(target=stop_monitor)
        stop_thread.start()
        try:
            assert overlay.terminal_marked.wait(timeout=0.2)
            assert stop_finished.wait(timeout=drain_bound + 0.25)
            assert drain_bound * 0.8 <= stop_elapsed[0] <= drain_bound + 0.2
            assert overlay.close_calls == 0
        finally:
            release_render.set()
            refresh_thread.join(timeout=1.0)
            stop_thread.join(timeout=1.0)
    finally:
        capture_logger.removeHandler(handler)
        capture_logger.propagate = original_propagate
        capture_logger.setLevel(original_level)

    assert overlay.closed.wait(timeout=0.5)
    assert close_thread_ids == helper_thread_ids
    assert overlay.close_calls == 1
    monitor.stop()
    assert overlay.close_calls == 1
    assert refresh_errors and "stopped" in str(refresh_errors[0])
    assert [record.getMessage() for record in records if record.levelno == logging.WARNING] == [
        "privacy protection indicator drain timed out: category=overlay_call_in_flight"
    ]
    assert capture_logger.level == original_level


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
