"""capture/scheduler.py: write-through to captures_fts + delete-through on cleanup."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import replace
from pathlib import Path

import pytest

from openchronicle.capture import scheduler as scheduler_mod
from openchronicle.capture import window_meta
from openchronicle.capture.ax_models import AXCaptureResult
from openchronicle.capture.privacy import (
    DisplayInfo,
    InventoryReadResult,
    ProtectionFailureReason,
    ScreenRegion,
    VisibleWindow,
    WindowInventory,
)
from openchronicle.capture.privacy_diagnostics import PrivacyDiagnosticsServer
from openchronicle.capture.privacy_diagnostics_guard import DiagnosticsLeaseManager
from openchronicle.capture.protection import ProtectionSnapshot, ProtectionState
from openchronicle.capture.protection_monitor import (
    PrivacyProtectionMonitor,
    ProtectionDecision,
)
from openchronicle.capture.protection_reason import ProtectionReasonCode
from openchronicle.capture.protection_smoothing import ProtectionPresentationPhase
from openchronicle.config import CaptureConfig
from openchronicle.store import fts


class _FakeProvider:
    def __init__(self, raw_json: dict | None = None) -> None:
        self.raw_json = raw_json
        self.calls = 0

    @property
    def available(self) -> bool:
        return True

    def capture_frontmost(self, *, focused_window_only: bool = True) -> AXCaptureResult | None:
        self.calls += 1
        if self.raw_json is None:
            return None
        return AXCaptureResult(
            raw_json=self.raw_json,
            timestamp="2026-04-22T14:00:00+08:00",
            apps=self.raw_json.get("apps", []),
            metadata={},
        )

    def capture_all_visible(self) -> AXCaptureResult | None:
        return self.capture_frontmost()

    def capture_app(
        self, app_name: str, *, focused_window_only: bool = True
    ) -> AXCaptureResult | None:
        return self.capture_frontmost(focused_window_only=focused_window_only)


class _FakeProtectionMonitor:
    def __init__(self, *decisions: ProtectionDecision) -> None:
        self.decisions = list(decisions)
        self.force_calls: list[bool] = []
        self.refresh_requests = 0

    @property
    def snapshot(self) -> ProtectionSnapshot:
        return self.decisions[-1].snapshot

    def decision_for_capture(self, *, force: bool = True) -> ProtectionDecision:
        self.force_calls.append(force)
        if len(self.decisions) > 1:
            return self.decisions.pop(0)
        return self.decisions[0]

    def request_refresh(self) -> None:
        self.refresh_requests += 1


class _AlwaysConfirmedOverlay:
    def render(
        self,
        _snapshot: ProtectionSnapshot,
        timeout: float = 0.5,
        *,
        overlay_reasons_enabled: bool = True,
    ) -> bool:
        return True

    def clear(self, _generation: int, timeout: float = 0.5) -> bool:
        return True

    def mark_terminal(self) -> None:
        return None

    def close(self) -> None:
        return None


def _protection_decision(
    *,
    generation: int = 20,
    state: ProtectionState = ProtectionState.PROTECTED,
    active_display_id: int | None,
    protected_ids: set[int],
    confirmed: bool,
    fresh: bool = True,
    failure_reason: ProtectionFailureReason | None = None,
    failure_capture_blocked: bool = True,
) -> ProtectionDecision:
    now = time.monotonic()
    displays = (
        DisplayInfo(1, ScreenRegion(0, 0, 100, 100), True),
        DisplayInfo(2, ScreenRegion(100, 0, 100, 100), False),
    )
    snapshot = ProtectionSnapshot(
        generation=generation,
        state=state,
        capture_mode="separate",
        indicator_style="pill",
        displays=displays,
        protected_display_ids=frozenset(protected_ids),
        active_display_id=active_display_id,
        created_monotonic=now,
        fresh_until=now + 1.0 if fresh else now - 1.0,
        failure_reason=failure_reason,
    )
    return ProtectionDecision(
        snapshot=snapshot,
        indicator_confirmed=confirmed,
        failure_capture_blocked=failure_capture_blocked,
    )


def _filtered_decision(
    *,
    generation: int = 20,
    monitor_mode: str = "separate",
    indicator_style: str = "pill",
    confirmed: bool = True,
    indicator_window_ids: tuple[int, ...] = (7, 41),
    protected_window_ids: frozenset[int] = frozenset({73}),
    protected_window_regions: tuple[ScreenRegion, ...] = (
        ScreenRegion(110, 10, 70, 70),
    ),
    window_filterable: bool = True,
    diagnostics_guard_active: bool = False,
    displays: tuple[DisplayInfo, ...] | None = None,
    protected_display_ids: frozenset[int] = frozenset({2}),
    active_display_id: int | None = 1,
    presentation_phase: ProtectionPresentationPhase = ProtectionPresentationPhase.BYPASS,
) -> ProtectionDecision:
    now = time.monotonic()
    resolved_displays = displays or (
        DisplayInfo(1, ScreenRegion(0, 0, 100, 100), True),
        DisplayInfo(2, ScreenRegion(100, 0, 100, 100), False),
    )
    decision = ProtectionDecision(
        snapshot=ProtectionSnapshot(
            generation=generation,
            state=ProtectionState.PROTECTED,
            capture_mode=monitor_mode,
            indicator_style=indicator_style,
            displays=resolved_displays,
            protected_display_ids=protected_display_ids,
            active_display_id=active_display_id,
            created_monotonic=now,
            fresh_until=now + 1.0,
            diagnostics_guard_active=diagnostics_guard_active,
            protected_window_ids=protected_window_ids,
            protected_window_regions=protected_window_regions,
            window_filterable=window_filterable,
        ),
        indicator_confirmed=confirmed,
        indicator_window_ids=indicator_window_ids,
        presentation_phase=presentation_phase,
    )
    return decision


def test_filtered_authorization_key_includes_indicator_placement() -> None:
    snapshot = ProtectionSnapshot(
        generation=1,
        state=ProtectionState.PROTECTED,
        capture_mode="separate",
        indicator_style="pill",
        displays=(),
        protected_display_ids=frozenset(),
        active_display_id=None,
        created_monotonic=1.0,
        fresh_until=1.25,
        indicator_placement="bottom-left-flush",
    )
    first = ProtectionDecision(snapshot=snapshot, indicator_confirmed=True)
    second = ProtectionDecision(
        snapshot=replace(snapshot, indicator_placement="bottom-left-inset"),
        indicator_confirmed=True,
    )

    assert scheduler_mod._filtered_authorization_key(first) != (
        scheduler_mod._filtered_authorization_key(second)
    )


def test_filtered_authorization_changes_across_protection_promotion() -> None:
    transient = _filtered_decision(
        generation=20,
        indicator_style="quiet-shield",
        indicator_window_ids=(7, 41),
    )
    sustained = _filtered_decision(
        generation=21,
        indicator_style="pill",
        indicator_window_ids=(8, 41),
    )
    assert scheduler_mod._filtered_authorization_key(transient) != (
        scheduler_mod._filtered_authorization_key(sustained)
    )


def test_filtered_authorization_changes_for_style_only_at_same_generation() -> None:
    transient = _filtered_decision(
        generation=20,
        indicator_style="quiet-shield",
        indicator_window_ids=(7, 41),
    )
    sustained = _filtered_decision(
        generation=20,
        indicator_style="pill",
        indicator_window_ids=(7, 41),
    )
    assert scheduler_mod._filtered_authorization_key(transient) != (
        scheduler_mod._filtered_authorization_key(sustained)
    )


def test_filtered_authorization_changes_for_confirmed_ids_only_at_same_generation() -> None:
    first = _filtered_decision(
        generation=20,
        indicator_style="quiet-shield",
        indicator_window_ids=(7, 41),
    )
    second = _filtered_decision(
        generation=20,
        indicator_style="quiet-shield",
        indicator_window_ids=(8, 41),
    )
    assert scheduler_mod._filtered_authorization_key(first) != (
        scheduler_mod._filtered_authorization_key(second)
    )


def _safe_active_window() -> window_meta.WindowMeta:
    return window_meta.WindowMeta(app_name="Cursor", title="main.py", bundle_id="cursor")


def _shot(label: str, *, mode: str = "separate") -> scheduler_mod.screenshot.Screenshot:
    return scheduler_mod.screenshot.Screenshot(
        image_base64=label,
        width=100,
        height=100,
        monitor_index=0 if mode == "all" else 1,
        monitor_left=0,
        monitor_top=0,
        monitor_width=200 if mode == "all" else 100,
        monitor_height=100,
        monitor_is_all=mode == "all",
    )


def _failed_decision(
    *,
    reason: ProtectionFailureReason = ProtectionFailureReason.INVENTORY_UNAVAILABLE,
    generation: int = 21,
    failure_capture_blocked: bool = True,
) -> ProtectionDecision:
    return _protection_decision(
        generation=generation,
        state=ProtectionState.FAILED,
        active_display_id=None,
        protected_ids=set(),
        confirmed=True,
        failure_reason=reason,
        failure_capture_blocked=failure_capture_blocked,
    )


def _paused_decision(*, generation: int = 22) -> ProtectionDecision:
    now = time.monotonic()
    return ProtectionDecision(
        snapshot=ProtectionSnapshot(
            generation=generation,
            state=ProtectionState.PAUSED,
            capture_mode="separate",
            indicator_style="pill",
            displays=(),
            protected_display_ids=frozenset(),
            active_display_id=None,
            created_monotonic=now,
            fresh_until=now + 1.0,
        ),
        indicator_confirmed=True,
    )


def _inactive_decision(
    *,
    generation: int = 23,
    confirmed: bool = True,
    indicator_style: str = "pill",
) -> ProtectionDecision:
    now = time.monotonic()
    return ProtectionDecision(
        snapshot=ProtectionSnapshot(
            generation=generation,
            state=ProtectionState.INACTIVE,
            capture_mode="separate",
            indicator_style=indicator_style,
            displays=(
                DisplayInfo(1, ScreenRegion(0, 0, 100, 100), True),
                DisplayInfo(2, ScreenRegion(100, 0, 100, 100), False),
            ),
            protected_display_ids=frozenset(),
            active_display_id=1,
            created_monotonic=now,
            fresh_until=now + 1.0,
        ),
        indicator_confirmed=confirmed,
    )


def test_transient_mapping_failure_keeps_resolved_scheduler_failure_policy() -> None:
    base = _failed_decision(reason=ProtectionFailureReason.ACTIVE_WINDOW_UNMAPPED)
    decision = replace(
        base,
        snapshot=replace(base.snapshot, indicator_style="quiet-shield"),
        presentation_phase=ProtectionPresentationPhase.TRANSIENT_MAPPING_FAILURE,
        overlay_reasons_enabled=False,
    )
    closed_cfg = CaptureConfig(
        screenshot_monitor="separate",
        screenshot_privacy_fail_closed=True,
    )
    fail_open_decision = replace(
        decision,
        failure_capture_blocked=False,
    )
    assert scheduler_mod._decision_is_terminal(closed_cfg, decision) is True
    assert scheduler_mod._decision_blocks_ax(closed_cfg, decision) is True
    assert scheduler_mod._decision_is_terminal(closed_cfg, fail_open_decision) is False
    assert scheduler_mod._decision_blocks_ax(closed_cfg, fail_open_decision) is False
    assert scheduler_mod._filtered_capture_is_eligible(closed_cfg, decision) is False


def _capture_dict(
    *, ts: str, app: str, title: str, value: str, text: str,
) -> dict:
    return {
        "timestamp": ts,
        "schema_version": 2,
        "trigger": {"event_type": "manual"},
        "window_meta": {
            "app_name": app, "title": title, "bundle_id": "com.test." + app.lower(),
        },
        "focused_element": {
            "role": "AXTextArea", "value": value,
            "is_editable": True, "value_length": len(value),
        },
        "visible_text": text,
        "url": "",
        "screenshot": {
            "image_base64": "AAAA", "mime_type": "image/jpeg",
            "width": 100, "height": 50,
        },
    }


def _edge_ax_tree(url: str, text: str = "visible page text") -> dict:
    return {
        "apps": [
            {
                "name": "Microsoft Edge",
                "bundle_id": "com.microsoft.edgemac",
                "is_frontmost": True,
                "windows": [
                    {
                        "title": "Account",
                        "focused": True,
                        "elements": [
                            {"role": "AXTextField", "title": "Address", "value": url},
                            {"role": "AXStaticText", "title": text, "value": text},
                        ],
                    }
                ],
            }
        ]
    }


def test_denylist_title_skips_before_ax_and_screenshot(
    ac_root: Path, monkeypatch,
) -> None:
    cfg = CaptureConfig(
        screenshot_privacy_mode="off",
        deny_window_title_patterns=["InPrivate", "无痕"],
    )
    provider = _FakeProvider(raw_json=_edge_ax_tree("https://example.com"))
    monkeypatch.setattr(
        scheduler_mod.window_meta,
        "active_window",
        lambda: window_meta.WindowMeta(
            app_name="Microsoft Edge",
            title="New tab - InPrivate Browsing",
            bundle_id="com.microsoft.edgemac",
        ),
    )
    monkeypatch.setattr(
        scheduler_mod.screenshot,
        "grab_many",
        lambda **_: (_ for _ in ()).throw(AssertionError("screenshot should be skipped")),
    )

    out = scheduler_mod._build_capture(
        cfg, provider, {"event_type": "AXFocusedWindowChanged"}
    )

    assert out is None
    assert provider.calls == 0


def test_denylist_url_skips_before_screenshot(ac_root: Path, monkeypatch) -> None:
    cfg = CaptureConfig(
        screenshot_privacy_mode="off",
        deny_url_patterns=["account\\.example"],
    )
    provider = _FakeProvider(raw_json=_edge_ax_tree("https://account.example/private"))
    monkeypatch.setattr(
        scheduler_mod.window_meta,
        "active_window",
        lambda: window_meta.WindowMeta(
            app_name="Microsoft Edge",
            title="Account",
            bundle_id="com.microsoft.edgemac",
        ),
    )
    monkeypatch.setattr(
        scheduler_mod.screenshot,
        "grab_many",
        lambda **_: (_ for _ in ()).throw(AssertionError("screenshot should be skipped")),
    )

    out = scheduler_mod._build_capture(cfg, provider, {"event_type": "AXValueChanged"})

    assert out is None
    assert provider.calls == 1


@pytest.mark.parametrize("deny_before_ax", [True, False])
def test_denylist_logs_do_not_expose_private_window_metadata(
    ac_root: Path, monkeypatch, caplog, deny_before_ax: bool,
) -> None:
    marker = "private-app-marker"
    cfg = (
        CaptureConfig(deny_app_names=[marker])
        if deny_before_ax
        else CaptureConfig(deny_url_patterns=["private\\.example"])
    )
    provider = _FakeProvider(raw_json=_edge_ax_tree("https://private.example/account"))
    monkeypatch.setattr(
        scheduler_mod.window_meta,
        "active_window",
        lambda: window_meta.WindowMeta(app_name=marker, title="private-title", bundle_id="private"),
    )

    with caplog.at_level(logging.INFO, logger="openchronicle.capture"):
        out = scheduler_mod._build_capture(cfg, provider, {"event_type": "manual"})

    assert out is None
    assert provider.calls == (0 if deny_before_ax else 1)
    assert marker not in caplog.text
    assert "private-title" not in caplog.text


def test_separate_screenshot_mode_writes_array_and_legacy_field(
    ac_root: Path, monkeypatch,
) -> None:
    cfg = CaptureConfig(screenshot_monitor="separate")
    provider = _FakeProvider(raw_json=None)
    calls: list[dict] = []
    monkeypatch.setattr(
        scheduler_mod.window_meta,
        "active_window",
        lambda: window_meta.WindowMeta(
            app_name="Cursor",
            title="main.py",
            bundle_id="com.todesktop.230313mzl4w4u92",
        ),
    )

    def fake_grab_many(**kwargs):
        calls.append(kwargs)
        return [
            scheduler_mod.screenshot.Screenshot(
                image_base64="AAAA",
                width=100,
                height=50,
                monitor_index=1,
                monitor_left=0,
                monitor_top=0,
                monitor_width=100,
                monitor_height=50,
            ),
            scheduler_mod.screenshot.Screenshot(
                image_base64="BBBB",
                width=200,
                height=80,
                monitor_index=2,
                monitor_left=100,
                monitor_top=0,
                monitor_width=200,
                monitor_height=80,
            ),
        ]

    monkeypatch.setattr(scheduler_mod.screenshot, "grab_many", fake_grab_many)

    out = scheduler_mod._build_capture(cfg, provider, {"event_type": "manual"})

    assert calls[0]["monitor_mode"] == "separate"
    assert out is not None
    assert out["screenshot"]["image_base64"] == "AAAA"
    assert out["screenshot"] == out["screenshots"][0]
    assert [shot["image_base64"] for shot in out["screenshots"]] == ["AAAA", "BBBB"]
    assert out["screenshots"][1]["monitor"] == {
        "index": 2,
        "left": 100,
        "top": 0,
        "width": 200,
        "height": 80,
    }


def test_all_screenshot_mode_writes_single_virtual_desktop_field(
    ac_root: Path, monkeypatch,
) -> None:
    cfg = CaptureConfig(screenshot_monitor="all")
    provider = _FakeProvider(raw_json=None)
    monkeypatch.setattr(
        scheduler_mod.window_meta,
        "active_window",
        lambda: window_meta.WindowMeta(app_name="Cursor", title="main.py", bundle_id=""),
    )
    monkeypatch.setattr(
        scheduler_mod.screenshot,
        "grab_many",
        lambda **_: [
            scheduler_mod.screenshot.Screenshot(
                image_base64="FULL",
                width=1920,
                height=2197,
                monitor_index=0,
                monitor_left=-68,
                monitor_top=-1080,
                monitor_width=1920,
                monitor_height=2197,
                monitor_is_all=True,
            )
        ],
    )

    out = scheduler_mod._build_capture(cfg, provider, {"event_type": "manual"})

    assert out is not None
    assert "screenshots" not in out
    assert out["screenshot"]["image_base64"] == "FULL"
    assert out["screenshot"]["monitor"]["is_all"] is True


def test_screenshot_privacy_guard_passes_sensitive_regions(
    ac_root: Path, monkeypatch,
) -> None:
    cfg = CaptureConfig(
        screenshot_monitor="separate",
        deny_window_title_patterns=["InPrivate"],
    )
    provider = _FakeProvider(raw_json=None)
    region = scheduler_mod.privacy.ScreenRegion(left=100, top=0, width=100, height=80)
    calls: list[dict] = []
    monkeypatch.setattr(
        scheduler_mod.window_meta,
        "active_window",
        lambda: window_meta.WindowMeta(app_name="Cursor", title="main.py", bundle_id=""),
    )
    monkeypatch.setattr(
        scheduler_mod.privacy, "sensitive_window_regions", lambda _cfg: [region]
    )
    monkeypatch.setattr(
        scheduler_mod.screenshot,
        "grab_many",
        lambda **kwargs: calls.append(kwargs) or [],
    )

    out = scheduler_mod._build_capture(cfg, provider, {"event_type": "manual"})

    assert out is not None
    assert calls[0]["blocked_regions"] == [region]


def test_screenshot_privacy_guard_fails_closed(
    ac_root: Path, monkeypatch,
) -> None:
    cfg = CaptureConfig(deny_app_names=["Passwords"])
    provider = _FakeProvider(raw_json=None)
    monkeypatch.setattr(
        scheduler_mod.window_meta,
        "active_window",
        lambda: window_meta.WindowMeta(app_name="Cursor", title="main.py", bundle_id=""),
    )
    monkeypatch.setattr(
        scheduler_mod.privacy, "sensitive_window_regions", lambda _cfg: None
    )
    monkeypatch.setattr(
        scheduler_mod.screenshot,
        "grab_many",
        lambda **_: (_ for _ in ()).throw(AssertionError("screenshot should be skipped")),
    )

    out = scheduler_mod._build_capture(cfg, provider, {"event_type": "manual"})

    assert out is not None
    assert "screenshot" not in out


def test_protected_active_display_skips_ax_but_captures_safe_monitor(
    ac_root: Path, monkeypatch,
) -> None:
    provider = _FakeProvider(raw_json=_edge_ax_tree("https://safe.example"))
    monitor = _FakeProtectionMonitor(
        _protection_decision(active_display_id=2, protected_ids={2}, confirmed=True)
    )
    screenshot_calls: list[dict] = []
    monkeypatch.setattr(
        scheduler_mod.window_meta,
        "active_window",
        lambda: window_meta.WindowMeta(app_name="Cursor", title="main.py", bundle_id="cursor"),
    )
    monkeypatch.setattr(
        scheduler_mod.screenshot,
        "grab_many",
        lambda **kwargs: screenshot_calls.append(kwargs) or [],
    )
    monkeypatch.setattr(
        scheduler_mod.s1_parser,
        "enrich",
        lambda _capture: (_ for _ in ()).throw(AssertionError("s1_parser.enrich must not run")),
    )

    out = scheduler_mod._build_capture(
        CaptureConfig(screenshot_monitor="separate"),
        provider,
        {"event_type": "manual"},
        protection_monitor=monitor,
    )

    assert out is not None
    assert provider.calls == 0
    assert "ax_tree" not in out
    assert "visible_text" not in out
    assert out["ax_skipped"] == "protected_display"
    assert screenshot_calls[0]["blocked_regions"] == monitor.snapshot.protected_regions


def test_title_uncertainty_style_off_keeps_ax_and_screenshot_authorization(
    ac_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decision = _filtered_decision(
        indicator_style="off",
        indicator_window_ids=(),
        window_filterable=False,
        active_display_id=2,
        presentation_phase=ProtectionPresentationPhase.TRANSIENT_TITLE_UNCERTAINTY,
    )
    monitor = _FakeProtectionMonitor(decision)
    provider = _FakeProvider(raw_json=_edge_ax_tree("https://must-not-capture.example"))
    mss_calls: list[dict[str, object]] = []
    monkeypatch.setattr(scheduler_mod.window_meta, "active_window", _safe_active_window)
    monkeypatch.setattr(
        scheduler_mod.screenshot,
        "grab_filtered_many",
        lambda **_kwargs: pytest.fail("unknown title must not authorize filtered capture"),
    )
    monkeypatch.setattr(
        scheduler_mod.screenshot,
        "grab_many",
        lambda **kwargs: mss_calls.append(kwargs)
        or [
            scheduler_mod.screenshot.Screenshot(
                image_base64="SAFE-DISPLAY-1",
                width=100,
                height=100,
                monitor_index=1,
                monitor_left=0,
                monitor_top=0,
                monitor_width=100,
                monitor_height=100,
            )
        ],
    )

    out = scheduler_mod._build_capture(
        CaptureConfig(
            screenshot_monitor="separate",
            screenshot_privacy_mode="exclude-window",
        ),
        provider,
        None,
        protection_monitor=monitor,
    )

    assert out is not None
    assert out["ax_skipped"] == "protected_display"
    assert provider.calls == 0
    assert out["screenshot"]["image_base64"] == "SAFE-DISPLAY-1"
    assert mss_calls[0]["blocked_regions"] == decision.snapshot.protected_regions
    assert decision.indicator_confirmed is True
    assert decision.indicator_window_ids == ()


def test_real_monitor_history_fallback_keeps_safe_display_and_revalidates_filtered_capture(
    ac_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    displays = (
        DisplayInfo(1, ScreenRegion(0, 0, 100, 100), True),
        DisplayInfo(2, ScreenRegion(100, 0, 100, 100), False),
    )

    def private_inventory(region: ScreenRegion) -> WindowInventory:
        return WindowInventory(
            windows=(
                VisibleWindow(
                    "Edge",
                    "edge",
                    "InPrivate",
                    region,
                    True,
                    window_id=73,
                ),
            ),
            displays=displays,
        )

    mapped_inventory = private_inventory(ScreenRegion(10, 0, 80, 90))
    fallback_inventory = private_inventory(ScreenRegion(300, 0, 80, 90))
    current_inventory = mapped_inventory
    cfg = CaptureConfig(
        screenshot_monitor="separate",
        screenshot_privacy_mode="exclude-window",
        privacy_indicator_style="off",
        deny_window_title_patterns=["InPrivate"],
    )
    monitor = PrivacyProtectionMonitor(
        cfg,
        config_path=ac_root / "missing-config.toml",
        overlay=_AlwaysConfirmedOverlay(),
        inventory_reader=lambda: current_inventory,
        pause_reader=lambda: False,
        monotonic=lambda: 10.0,
    )
    provider = _FakeProvider(raw_json=_edge_ax_tree("https://must-not-capture.example"))
    mss_calls: list[dict[str, object]] = []
    filtered_calls: list[dict[str, object]] = []

    def safe_display_shot(label: str) -> scheduler_mod.screenshot.Screenshot:
        return scheduler_mod.screenshot.Screenshot(
            image_base64=label,
            width=100,
            height=100,
            monitor_index=2,
            monitor_left=100,
            monitor_top=0,
            monitor_width=100,
            monitor_height=100,
        )

    def capture_unprotected_display(**kwargs):
        mss_calls.append(kwargs)
        assert kwargs["monitor_mode"] == "separate"
        assert kwargs["blocked_regions"] == [displays[0].region]
        return [safe_display_shot(f"SAFE-DISPLAY-2-{len(mss_calls)}")]

    def return_stale_filtered_frame(**kwargs):
        nonlocal current_inventory
        filtered_calls.append(kwargs)
        current_inventory = fallback_inventory
        return [_shot("STALE-PRE-FALLBACK")]

    monkeypatch.setattr(scheduler_mod.window_meta, "active_window", _safe_active_window)
    monkeypatch.setattr(
        scheduler_mod.screenshot,
        "grab_many",
        capture_unprotected_display,
    )
    monkeypatch.setattr(
        scheduler_mod.screenshot,
        "grab_filtered_many",
        return_stale_filtered_frame,
    )

    try:
        monitor.decision_for_capture(force=True)
        current_inventory = fallback_inventory
        fallback_out = scheduler_mod._build_capture(
            cfg,
            provider,
            {"event_type": "manual"},
            protection_monitor=monitor,
        )
        fallback_decision = monitor.decision_for_capture(force=False)

        assert fallback_out is not None
        assert fallback_out["ax_skipped"] == "protected_display"
        assert fallback_out["screenshot"]["image_base64"] == "SAFE-DISPLAY-2-1"
        assert fallback_out["screenshot"]["monitor"]["left"] == 100
        assert fallback_decision.snapshot.state is ProtectionState.PROTECTED
        assert fallback_decision.snapshot.active_display_id == 1
        assert fallback_decision.snapshot.ax_blocked is True
        assert fallback_decision.snapshot.protected_display_ids == frozenset({1})
        assert fallback_decision.snapshot.display_mapping_fallback_active is True
        assert fallback_decision.snapshot.window_filterable is False
        assert filtered_calls == []

        current_inventory = mapped_inventory
        revalidated_out = scheduler_mod._build_capture(
            cfg,
            provider,
            {"event_type": "manual"},
            protection_monitor=monitor,
        )
    finally:
        monitor.stop()

    assert revalidated_out is not None
    assert revalidated_out["screenshot"]["image_base64"] == "SAFE-DISPLAY-2-2"
    assert "STALE-PRE-FALLBACK" not in json.dumps(revalidated_out)
    assert len(filtered_calls) == 1
    assert len(mss_calls) == 2
    assert provider.calls == 0


def test_diagnostics_guard_uses_monitor_gate_without_leaking_exact_reason(
    ac_root: Path,
    monkeypatch,
) -> None:
    marker = "private-diagnostics-window-title"
    displays = (
        DisplayInfo(1, ScreenRegion(0, 0, 100, 100), True),
        DisplayInfo(2, ScreenRegion(100, 0, 100, 100), False),
    )
    inventory = WindowInventory(
        windows=(
            VisibleWindow("Edge", "edge", marker, ScreenRegion(110, 0, 80, 90), False),
            VisibleWindow("Cursor", "cursor", "main.py", ScreenRegion(110, 0, 80, 90), True),
        ),
        displays=displays,
    )
    cfg = CaptureConfig(
        screenshot_monitor="separate",
        deny_window_title_patterns=[marker],
    )
    manager = DiagnosticsLeaseManager(
        ac_root / "runtime" / "privacy-reveal.guard",
        process_alive=lambda _pid: True,
    )
    manager.load()
    manager.acquire(pid=os.getpid(), display_id=2)
    monitor = PrivacyProtectionMonitor(
        cfg,
        config_path=ac_root / "missing-config.toml",
        overlay=_AlwaysConfirmedOverlay(),
        inventory_reader=lambda: inventory,
        pause_reader=lambda: False,
        diagnostics_guard_reader=manager.snapshot,
    )
    provider = _FakeProvider(raw_json=_edge_ax_tree("https://private.example", marker))
    screenshot_calls: list[dict] = []
    monkeypatch.setattr(
        scheduler_mod.window_meta,
        "active_window",
        lambda: window_meta.WindowMeta(app_name="Cursor", title="main.py", bundle_id="cursor"),
    )

    def capture_only_safe_monitor(**kwargs):
        screenshot_calls.append(kwargs)
        assert kwargs["blocked_regions"] == [displays[1].region]
        return [
            scheduler_mod.screenshot.Screenshot(
                image_base64="SAFE",
                width=100,
                height=100,
                monitor_index=1,
                monitor_left=0,
                monitor_top=0,
                monitor_width=100,
                monitor_height=100,
            )
        ]

    monkeypatch.setattr(scheduler_mod.screenshot, "grab_many", capture_only_safe_monitor)
    try:
        out = scheduler_mod._build_capture(
            cfg,
            provider,
            {"event_type": "manual"},
            protection_monitor=monitor,
        )
    finally:
        monitor.stop()

    assert out is not None
    assert provider.calls == 0
    assert out["ax_skipped"] == "protected_display"
    assert [shot["image_base64"] for shot in out["screenshots"]] == ["SAFE"]
    assert screenshot_calls
    assert marker not in json.dumps(out)


@pytest.mark.parametrize("lease_display_id", [1, 2])
def test_diagnostics_guard_in_all_mode_skips_virtual_desktop_screenshot(
    ac_root: Path,
    monkeypatch,
    lease_display_id: int,
) -> None:
    displays = (
        DisplayInfo(1, ScreenRegion(0, 0, 100, 100), True),
        DisplayInfo(2, ScreenRegion(100, 0, 100, 100), False),
    )
    inventory = WindowInventory(
        windows=(VisibleWindow("Cursor", "cursor", "main.py", displays[0].region, True),),
        displays=displays,
    )
    cfg = CaptureConfig(screenshot_monitor="all")
    manager = DiagnosticsLeaseManager(
        ac_root / "runtime" / "privacy-reveal.guard",
        process_alive=lambda _pid: True,
    )
    manager.load()
    manager.acquire(pid=os.getpid(), display_id=lease_display_id)
    monitor = PrivacyProtectionMonitor(
        cfg,
        config_path=ac_root / "missing-config.toml",
        overlay=_AlwaysConfirmedOverlay(),
        inventory_reader=lambda: inventory,
        pause_reader=lambda: False,
        diagnostics_guard_reader=manager.snapshot,
    )
    provider = _FakeProvider(raw_json=_edge_ax_tree("https://safe.example"))
    screenshot_calls: list[dict] = []
    monkeypatch.setattr(
        scheduler_mod.window_meta,
        "active_window",
        lambda: window_meta.WindowMeta(app_name="Cursor", title="main.py", bundle_id="cursor"),
    )

    def skip_virtual_desktop(**kwargs):
        screenshot_calls.append(kwargs)
        assert kwargs["monitor_mode"] == "all"
        assert kwargs["blocked_regions"] == [display.region for display in displays]
        return []

    monkeypatch.setattr(scheduler_mod.screenshot, "grab_many", skip_virtual_desktop)
    try:
        out = scheduler_mod._build_capture(
            cfg,
            provider,
            {"event_type": "manual"},
            protection_monitor=monitor,
        )
    finally:
        monitor.stop()

    assert out is not None
    assert provider.calls == 0
    assert screenshot_calls
    assert "screenshot" not in out
    assert "screenshots" not in out


def test_skip_monitor_unconfirmed_indicator_keeps_legacy_no_screenshot_behavior(
    ac_root: Path, monkeypatch,
) -> None:
    monitor = _FakeProtectionMonitor(
        _protection_decision(active_display_id=1, protected_ids={2}, confirmed=False)
    )
    monkeypatch.setattr(
        scheduler_mod.window_meta,
        "active_window",
        lambda: window_meta.WindowMeta(app_name="Cursor", title="main.py", bundle_id="cursor"),
    )
    monkeypatch.setattr(
        scheduler_mod.screenshot,
        "grab_many",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("mss must not run")),
    )

    out = scheduler_mod._build_capture(
        CaptureConfig(),
        _FakeProvider(raw_json=None),
        None,
        protection_monitor=monitor,
    )

    assert out is not None
    assert "screenshot" not in out
    assert monitor.force_calls == [True, False]


def test_failed_protection_snapshot_writes_nothing(
    ac_root: Path, monkeypatch,
) -> None:
    provider = _FakeProvider(raw_json=_edge_ax_tree("https://private.example"))
    monitor = _FakeProtectionMonitor(_failed_decision())
    monkeypatch.setattr(
        scheduler_mod.window_meta,
        "active_window",
        lambda: window_meta.WindowMeta(app_name="Cursor", title="main.py", bundle_id="cursor"),
    )

    out = scheduler_mod._build_capture(
        CaptureConfig(),
        provider,
        None,
        protection_monitor=monitor,
    )

    assert out is None
    assert provider.calls == 0


def test_paused_initial_decision_writes_nothing_without_displays_or_capture_sources(
    ac_root: Path, monkeypatch,
) -> None:
    monitor = _FakeProtectionMonitor(_paused_decision())
    provider = scheduler_mod.ax_capture.UnavailableAXProvider("test unavailable")
    monkeypatch.setattr(
        scheduler_mod.window_meta,
        "active_window",
        lambda: pytest.fail("paused initial decision must precede foreground metadata"),
    )
    monkeypatch.setattr(
        scheduler_mod.s1_parser,
        "enrich",
        lambda _capture: (_ for _ in ()).throw(AssertionError("paused capture must not enrich")),
    )
    monkeypatch.setattr(
        scheduler_mod.screenshot,
        "grab_many",
        lambda **_: (_ for _ in ()).throw(AssertionError("paused capture must not screenshot")),
    )

    out = scheduler_mod._build_capture(
        CaptureConfig(include_screenshot=False),
        provider,
        None,
        protection_monitor=monitor,
    )

    assert out is None
    assert monitor.force_calls == [True]


def test_inventory_failure_is_fail_open_only_when_configured(
    ac_root: Path, monkeypatch,
) -> None:
    monitor = _FakeProtectionMonitor(
        ProtectionDecision(
            snapshot=_failed_decision().snapshot,
            indicator_confirmed=False,
            failure_capture_blocked=False,
        )
    )
    provider = _FakeProvider(raw_json=_edge_ax_tree("https://safe.example"))
    screenshot_calls: list[dict] = []
    monkeypatch.setattr(
        scheduler_mod.window_meta,
        "active_window",
        lambda: window_meta.WindowMeta(app_name="Cursor", title="main.py", bundle_id="cursor"),
    )
    monkeypatch.setattr(
        scheduler_mod.screenshot,
        "grab_many",
        lambda **kwargs: screenshot_calls.append(kwargs) or [],
    )

    out = scheduler_mod._build_capture(
        CaptureConfig(screenshot_privacy_fail_closed=False),
        provider,
        None,
        protection_monitor=monitor,
    )

    assert out is not None
    assert provider.calls == 1
    assert len(screenshot_calls) == 1
    assert screenshot_calls[0]["blocked_regions"] == []
    assert monitor.force_calls == [True, False]


@pytest.mark.parametrize(
    "reason",
    [
        ProtectionFailureReason.INVENTORY_UNAVAILABLE,
        ProtectionFailureReason.HELPER_EXIT,
    ],
)
def test_real_active_guard_makes_fail_open_inventory_failure_terminal_before_io(
    ac_root: Path,
    monkeypatch,
    reason: ProtectionFailureReason,
) -> None:
    cfg = CaptureConfig(screenshot_privacy_fail_closed=False)
    manager = DiagnosticsLeaseManager(
        ac_root / "runtime" / "privacy-reveal.guard",
        process_alive=lambda _pid: True,
    )
    manager.load()
    manager.acquire(pid=os.getpid(), display_id=2)
    monitor = PrivacyProtectionMonitor(
        cfg,
        config_path=ac_root / "missing-config.toml",
        overlay=_AlwaysConfirmedOverlay(),
        inventory_reader=lambda: InventoryReadResult(None, reason),
        pause_reader=lambda: False,
        diagnostics_guard_reader=manager.snapshot,
    )
    provider = _FakeProvider(raw_json=None)
    screenshot_calls: list[dict] = []
    monkeypatch.setattr(
        scheduler_mod.window_meta,
        "active_window",
        lambda: window_meta.WindowMeta(app_name="Cursor", title="main.py", bundle_id="cursor"),
    )
    monkeypatch.setattr(
        scheduler_mod.screenshot,
        "grab_many",
        lambda **kwargs: screenshot_calls.append(kwargs) or [],
    )
    try:
        out = scheduler_mod._build_capture(
            cfg,
            provider,
            {"event_type": "manual"},
            protection_monitor=monitor,
        )
    finally:
        monitor.stop()

    assert out is None
    assert provider.calls == 0
    assert screenshot_calls == []


def test_real_unmapped_guard_display_is_terminal_before_ax_or_screenshot(
    ac_root: Path,
    monkeypatch,
) -> None:
    cfg = CaptureConfig(screenshot_privacy_fail_closed=False)
    displays = (
        DisplayInfo(1, ScreenRegion(0, 0, 100, 100), True),
        DisplayInfo(2, ScreenRegion(100, 0, 100, 100), False),
    )
    inventory = WindowInventory(windows=(), displays=displays)
    manager = DiagnosticsLeaseManager(
        ac_root / "runtime" / "privacy-reveal.guard",
        process_alive=lambda _pid: True,
    )
    manager.load()
    manager.acquire(pid=os.getpid(), display_id=99)
    monitor = PrivacyProtectionMonitor(
        cfg,
        config_path=ac_root / "missing-config.toml",
        overlay=_AlwaysConfirmedOverlay(),
        inventory_reader=lambda: inventory,
        pause_reader=lambda: False,
        diagnostics_guard_reader=manager.snapshot,
    )
    provider = _FakeProvider(raw_json=None)
    screenshot_calls: list[dict] = []
    monkeypatch.setattr(
        scheduler_mod.window_meta,
        "active_window",
        lambda: window_meta.WindowMeta(app_name="Cursor", title="main.py", bundle_id="cursor"),
    )
    monkeypatch.setattr(
        scheduler_mod.screenshot,
        "grab_many",
        lambda **kwargs: screenshot_calls.append(kwargs) or [],
    )
    try:
        out = scheduler_mod._build_capture(
            cfg,
            provider,
            {"event_type": "manual"},
            protection_monitor=monitor,
        )
    finally:
        monitor.stop()

    assert out is None
    assert provider.calls == 0
    assert screenshot_calls == []


def test_guarded_unmapped_active_candidate_is_terminal_before_ax_or_screenshot(
    ac_root: Path,
    monkeypatch,
) -> None:
    cfg = CaptureConfig(screenshot_privacy_fail_closed=False)
    inventory = WindowInventory(
        windows=(
            VisibleWindow(
                "Cursor",
                "cursor",
                "main.py",
                ScreenRegion(250, 0, 80, 90),
                is_active_candidate=True,
            ),
        ),
        displays=(
            DisplayInfo(1, ScreenRegion(0, 0, 100, 100), True),
            DisplayInfo(2, ScreenRegion(100, 0, 100, 100), False),
        ),
    )
    manager = DiagnosticsLeaseManager(
        ac_root / "runtime" / "privacy-reveal.guard",
        process_alive=lambda _pid: True,
    )
    manager.load()
    manager.acquire(pid=os.getpid(), display_id=2)
    monitor = PrivacyProtectionMonitor(
        cfg,
        config_path=ac_root / "missing-config.toml",
        overlay=_AlwaysConfirmedOverlay(),
        inventory_reader=lambda: inventory,
        pause_reader=lambda: False,
        diagnostics_guard_reader=manager.snapshot,
    )
    provider = _FakeProvider(raw_json=_edge_ax_tree("https://must-not-capture.example"))
    screenshot_calls: list[dict] = []
    monkeypatch.setattr(
        scheduler_mod.window_meta,
        "active_window",
        lambda: window_meta.WindowMeta(app_name="Cursor", title="main.py", bundle_id="cursor"),
    )
    monkeypatch.setattr(
        scheduler_mod.screenshot,
        "grab_many",
        lambda **kwargs: screenshot_calls.append(kwargs) or [],
    )
    try:
        out = scheduler_mod._build_capture(
            cfg,
            provider,
            {"event_type": "manual"},
            protection_monitor=monitor,
        )
        decision = monitor.decision_for_capture(force=False)
    finally:
        monitor.stop()

    assert out is None
    assert decision.snapshot.failure_reason is ProtectionFailureReason.ACTIVE_WINDOW_UNMAPPED
    assert decision.snapshot.ax_blocked is True
    assert provider.calls == 0
    assert screenshot_calls == []


def test_real_monitor_without_guard_preserves_configured_inventory_fail_open(
    ac_root: Path,
    monkeypatch,
) -> None:
    cfg = CaptureConfig(screenshot_privacy_fail_closed=False)
    manager = DiagnosticsLeaseManager(
        ac_root / "runtime" / "privacy-reveal.guard",
        process_alive=lambda _pid: True,
    )
    manager.load()
    monitor = PrivacyProtectionMonitor(
        cfg,
        config_path=ac_root / "missing-config.toml",
        overlay=_AlwaysConfirmedOverlay(),
        inventory_reader=lambda: InventoryReadResult(
            None,
            ProtectionFailureReason.HELPER_EXIT,
        ),
        pause_reader=lambda: False,
        diagnostics_guard_reader=manager.snapshot,
    )
    provider = _FakeProvider(raw_json=None)
    screenshot_calls: list[dict] = []
    monkeypatch.setattr(
        scheduler_mod.window_meta,
        "active_window",
        lambda: window_meta.WindowMeta(app_name="Cursor", title="main.py", bundle_id="cursor"),
    )
    monkeypatch.setattr(
        scheduler_mod.screenshot,
        "grab_many",
        lambda **kwargs: screenshot_calls.append(kwargs) or [],
    )
    try:
        out = scheduler_mod._build_capture(
            cfg,
            provider,
            {"event_type": "manual"},
            protection_monitor=monitor,
        )
    finally:
        monitor.stop()

    assert out is not None
    assert provider.calls == 1
    assert len(screenshot_calls) == 1
    assert screenshot_calls[0]["blocked_regions"] == []


def test_pause_state_failure_blocks_before_ax_even_when_inventory_is_fail_open(
    ac_root: Path, monkeypatch,
) -> None:
    marker = "private-pause-marker-path"
    pause_path = ac_root / ".paused"
    original_read_bytes = Path.read_bytes

    def read_pause_file(path: Path) -> bytes:
        if path == pause_path:
            raise OSError(marker)
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", read_pause_file)
    provider = _FakeProvider(raw_json=_edge_ax_tree("https://safe.example"))
    displays = (
        DisplayInfo(1, ScreenRegion(0, 0, 100, 100), True),
        DisplayInfo(2, ScreenRegion(100, 0, 100, 100), False),
    )
    inventory = WindowInventory(windows=(), displays=displays)

    class Overlay:
        def __init__(self) -> None:
            self.snapshots: list[ProtectionSnapshot] = []
            self.clear_calls = 0

        def render(
            self,
            snapshot: ProtectionSnapshot,
            timeout: float = 0.5,
            *,
            overlay_reasons_enabled: bool = True,
        ) -> bool:
            self.snapshots.append(snapshot)
            return True

        def clear(self, _generation: int, timeout: float = 0.5) -> bool:
            self.clear_calls += 1
            return True

        def mark_terminal(self) -> None:
            return None

        def close(self) -> None:
            return None

    cfg = CaptureConfig(
        privacy_indicator_style="pill",
        screenshot_privacy_fail_closed=False,
    )
    overlay = Overlay()
    monitor = PrivacyProtectionMonitor(
        cfg,
        config_path=ac_root / "missing.toml",
        overlay=overlay,
        inventory_reader=lambda: inventory,
    )
    monkeypatch.setattr(
        scheduler_mod.window_meta,
        "active_window",
        lambda: window_meta.WindowMeta(app_name="Cursor", title="main.py", bundle_id="cursor"),
    )
    monkeypatch.setattr(
        scheduler_mod.screenshot,
        "grab_many",
        lambda **_: (_ for _ in ()).throw(AssertionError("screenshot must not run")),
    )

    messages: list[str] = []
    handler = logging.Handler()
    handler.emit = lambda record: messages.append(record.getMessage())  # type: ignore[method-assign]
    capture_logger = logging.getLogger("openchronicle.capture")
    original_propagate = capture_logger.propagate
    capture_logger.addHandler(handler)
    capture_logger.propagate = False
    try:
        out = scheduler_mod._build_capture(
            cfg,
            provider,
            None,
            protection_monitor=monitor,
        )
    finally:
        monitor.stop()
        capture_logger.removeHandler(handler)
        capture_logger.propagate = original_propagate

    assert out is None
    assert provider.calls == 0
    assert len(overlay.snapshots) == 1
    snapshot = overlay.snapshots[0]
    assert snapshot.state is ProtectionState.FAILED
    assert snapshot.failure_reason is ProtectionFailureReason.PAUSE_STATE_UNAVAILABLE
    assert overlay.clear_calls == 0
    decision = ProtectionDecision(snapshot=snapshot, indicator_confirmed=True)
    assert scheduler_mod._decision_is_terminal(cfg, decision)
    assert scheduler_mod._decision_blocks_ax(cfg, decision)
    assert messages == [
        "privacy protection pause read failed: OSError",
        "privacy protection failed closed: reason=pause_state_unavailable",
    ]
    assert marker not in "\n".join(messages)


def test_pause_state_failure_during_ax_discards_when_inventory_is_fail_open(
    ac_root: Path, monkeypatch,
) -> None:
    provider = _FakeProvider(raw_json=_edge_ax_tree("https://safe.example"))
    monitor = _FakeProtectionMonitor(
        _protection_decision(
            generation=70,
            active_display_id=1,
            protected_ids={2},
            confirmed=True,
        ),
        _failed_decision(
            reason=ProtectionFailureReason.PAUSE_STATE_UNAVAILABLE,
            generation=71,
        ),
    )
    monkeypatch.setattr(
        scheduler_mod.window_meta,
        "active_window",
        lambda: window_meta.WindowMeta(app_name="Cursor", title="main.py", bundle_id="cursor"),
    )

    out = scheduler_mod._build_capture(
        CaptureConfig(
            include_screenshot=False,
            screenshot_privacy_fail_closed=False,
        ),
        provider,
        None,
        protection_monitor=monitor,
    )

    assert out is None
    assert provider.calls == 1
    assert monitor.force_calls == [True, False]


def test_post_ax_validation_that_newly_blocks_ax_discards_whole_capture(
    ac_root: Path, monkeypatch,
) -> None:
    provider = _FakeProvider(raw_json=_edge_ax_tree("https://private.example"))
    monitor = _FakeProtectionMonitor(
        _protection_decision(
            generation=30,
            active_display_id=1,
            protected_ids={2},
            confirmed=True,
        ),
        _protection_decision(
            generation=31,
            active_display_id=1,
            protected_ids={1},
            confirmed=True,
        ),
    )
    monkeypatch.setattr(
        scheduler_mod.window_meta,
        "active_window",
        lambda: window_meta.WindowMeta(app_name="Cursor", title="main.py", bundle_id="cursor"),
    )
    monkeypatch.setattr(
        scheduler_mod.screenshot,
        "grab_many",
        lambda **_: (_ for _ in ()).throw(AssertionError("screenshot must not run")),
    )

    out = scheduler_mod._build_capture(
        CaptureConfig(),
        provider,
        None,
        protection_monitor=monitor,
    )

    assert out is None
    assert provider.calls == 1
    assert monitor.force_calls == [True, False]


@pytest.mark.parametrize("provider_available", [True, False], ids=["no-result", "unavailable"])
def test_post_ax_validation_discards_newly_blocked_capture_without_ax_tree(
    ac_root: Path, monkeypatch, provider_available: bool,
) -> None:
    provider = (
        _FakeProvider(raw_json=None)
        if provider_available
        else scheduler_mod.ax_capture.UnavailableAXProvider("test unavailable")
    )
    monitor = _FakeProtectionMonitor(
        _protection_decision(
            generation=60,
            active_display_id=1,
            protected_ids={2},
            confirmed=True,
        ),
        _protection_decision(
            generation=61,
            active_display_id=1,
            protected_ids={1},
            confirmed=True,
        ),
    )
    monkeypatch.setattr(
        scheduler_mod.window_meta,
        "active_window",
        lambda: window_meta.WindowMeta(app_name="Cursor", title="main.py", bundle_id="cursor"),
    )

    out = scheduler_mod._build_capture(
        CaptureConfig(include_screenshot=False),
        provider,
        None,
        protection_monitor=monitor,
    )

    assert out is None
    assert monitor.force_calls == [True, False]


@pytest.mark.parametrize(
    "latest_state",
    [ProtectionState.PROTECTED, ProtectionState.PAUSED, ProtectionState.FAILED],
)
def test_post_ax_validation_blocks_write_without_screenshot(
    ac_root: Path, monkeypatch, latest_state: ProtectionState,
) -> None:
    provider = _FakeProvider(raw_json=_edge_ax_tree("https://private.example"))
    if latest_state is ProtectionState.FAILED:
        latest = _failed_decision()
    elif latest_state is ProtectionState.PAUSED:
        latest = _paused_decision(generation=51)
    else:
        latest = _protection_decision(
            generation=51,
            active_display_id=1,
            protected_ids={1},
            confirmed=True,
        )
    monitor = _FakeProtectionMonitor(
        _protection_decision(
            generation=50,
            active_display_id=1,
            protected_ids={2},
            confirmed=True,
        ),
        latest,
    )
    monkeypatch.setattr(
        scheduler_mod.window_meta,
        "active_window",
        lambda: window_meta.WindowMeta(app_name="Cursor", title="main.py", bundle_id="cursor"),
    )

    out = scheduler_mod._build_capture(
        CaptureConfig(include_screenshot=False),
        provider,
        None,
        protection_monitor=monitor,
    )

    assert out is None
    assert provider.calls == 1
    assert monitor.force_calls == [True, False]


def test_event_during_ax_forces_post_ax_refresh_and_discards_capture(
    ac_root: Path, monkeypatch, caplog: pytest.LogCaptureFixture,
) -> None:
    marker = "private-window-after-initial-decision"
    displays = (
        DisplayInfo(1, ScreenRegion(0, 0, 100, 100), True),
        DisplayInfo(2, ScreenRegion(100, 0, 100, 100), False),
    )
    safe_inventory = WindowInventory(
        windows=(
            VisibleWindow("Cursor", "cursor", "main.py", ScreenRegion(0, 0, 80, 90), True),
        ),
        displays=displays,
    )
    protected_inventory = WindowInventory(
        windows=(
            VisibleWindow("Edge", "edge", marker, ScreenRegion(0, 0, 80, 90)),
            VisibleWindow("Cursor", "cursor", "main.py", ScreenRegion(0, 0, 80, 90), True),
        ),
        displays=displays,
    )
    inventory = safe_inventory

    class Overlay:
        def render(
            self,
            _snapshot: ProtectionSnapshot,
            timeout: float = 0.5,
            *,
            overlay_reasons_enabled: bool = True,
        ) -> bool:
            return True

        def clear(self, _generation: int, timeout: float = 0.5) -> bool:
            return True

        def mark_terminal(self) -> None:
            return None

        def close(self) -> None:
            return None

    monitor = PrivacyProtectionMonitor(
        CaptureConfig(
            screenshot_monitor="separate",
            deny_window_title_patterns=[marker],
        ),
        config_path=ac_root / "missing.toml",
        overlay=Overlay(),
        inventory_reader=lambda: inventory,
        pause_reader=lambda: False,
        watchdog_seconds=10.0,
    )

    class EventDuringAXProvider(_FakeProvider):
        def capture_frontmost(self, *, focused_window_only: bool = True) -> AXCaptureResult | None:
            nonlocal inventory
            inventory = protected_inventory
            monitor.request_refresh()
            return super().capture_frontmost(focused_window_only=focused_window_only)

    provider = EventDuringAXProvider(raw_json=_edge_ax_tree("https://safe.example"))
    monkeypatch.setattr(
        scheduler_mod.window_meta,
        "active_window",
        lambda: window_meta.WindowMeta(app_name="Cursor", title="main.py", bundle_id="cursor"),
    )
    monkeypatch.setattr(
        scheduler_mod.screenshot,
        "grab_many",
        lambda **_: (_ for _ in ()).throw(AssertionError("invalidated capture must not screenshot")),
    )

    try:
        with caplog.at_level(logging.DEBUG, logger="openchronicle.capture"):
            out = scheduler_mod._build_capture(
                CaptureConfig(
                    screenshot_monitor="separate",
                    deny_window_title_patterns=[marker],
                ),
                provider,
                None,
                protection_monitor=monitor,
            )
            latest = monitor.decision_for_capture(force=False)
    finally:
        monitor.stop()

    assert out is None
    assert provider.calls == 1
    latest_reasons = latest.snapshot.reasons_for_display(1)
    assert latest_reasons[0].code is ProtectionReasonCode.WINDOW_TITLE_RULE
    assert latest_reasons[0].window_title == marker
    assert latest_reasons[0].rule == marker
    assert marker not in caplog.text


def test_diagnostics_lease_acquired_during_ax_discards_in_memory_ax(
    ac_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = "private-ax-discard-marker"
    displays = (
        DisplayInfo(1, ScreenRegion(0, 0, 100, 100), True),
        DisplayInfo(2, ScreenRegion(100, 0, 100, 100), False),
    )
    inventory = WindowInventory(
        windows=(
            VisibleWindow("Cursor", "cursor", "main.py", ScreenRegion(0, 0, 80, 90), True),
        ),
        displays=displays,
    )
    cfg = CaptureConfig(screenshot_monitor="separate")
    manager = DiagnosticsLeaseManager(
        ac_root / "runtime" / "privacy-reveal.guard",
        process_alive=lambda _pid: True,
    )
    manager.load()
    monitor = PrivacyProtectionMonitor(
        cfg,
        config_path=ac_root / "missing.toml",
        overlay=_AlwaysConfirmedOverlay(),
        inventory_reader=lambda: inventory,
        pause_reader=lambda: False,
        diagnostics_guard_reader=manager.snapshot,
        watchdog_seconds=10.0,
    )

    class LeaseDuringAXProvider(_FakeProvider):
        def capture_frontmost(
            self, *, focused_window_only: bool = True
        ) -> AXCaptureResult | None:
            manager.acquire(pid=os.getpid(), display_id=1)
            monitor.request_refresh()
            return super().capture_frontmost(focused_window_only=focused_window_only)

    provider = LeaseDuringAXProvider(raw_json=_edge_ax_tree("https://safe.example", marker))
    monkeypatch.setattr(
        scheduler_mod.window_meta,
        "active_window",
        lambda: window_meta.WindowMeta(app_name="Cursor", title="main.py", bundle_id="cursor"),
    )
    monkeypatch.setattr(
        scheduler_mod.screenshot,
        "grab_many",
        lambda **_: (_ for _ in ()).throw(
            AssertionError("lease-invalidated capture must not screenshot")
        ),
    )

    try:
        out = scheduler_mod._build_capture(
            cfg,
            provider,
            None,
            protection_monitor=monitor,
        )
        latest = monitor.decision_for_capture(force=False)
    finally:
        monitor.stop()

    assert out is None
    assert provider.calls == 1
    assert latest.snapshot.ax_blocked is True
    assert (
        latest.snapshot.reasons_for_display(1)[0].code
        is ProtectionReasonCode.DIAGNOSTICS_REVEAL
    )


def test_post_ax_validation_uses_latest_generation_confirmation_and_regions(
    ac_root: Path, monkeypatch,
) -> None:
    provider = _FakeProvider(raw_json=_edge_ax_tree("https://safe.example"))
    monitor = _FakeProtectionMonitor(
        _protection_decision(
            generation=40,
            active_display_id=1,
            protected_ids={2},
            confirmed=False,
        ),
        _protection_decision(
            generation=41,
            active_display_id=2,
            protected_ids={1},
            confirmed=True,
        ),
    )
    screenshot_calls: list[dict] = []
    monkeypatch.setattr(
        scheduler_mod.window_meta,
        "active_window",
        lambda: window_meta.WindowMeta(app_name="Cursor", title="main.py", bundle_id="cursor"),
    )
    monkeypatch.setattr(
        scheduler_mod.screenshot,
        "grab_many",
        lambda **kwargs: screenshot_calls.append(kwargs) or [],
    )

    out = scheduler_mod._build_capture(
        CaptureConfig(screenshot_monitor="separate"),
        provider,
        None,
        protection_monitor=monitor,
    )

    assert out is not None
    assert monitor.force_calls == [True, False]
    assert screenshot_calls[0]["blocked_regions"] == [ScreenRegion(0, 0, 100, 100)]


def test_skip_monitor_keeps_safe_shot_when_only_filtered_authorization_fields_change(
    ac_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    initial = _filtered_decision(generation=80)
    current = _filtered_decision(
        generation=81,
        indicator_window_ids=(8,),
        protected_window_ids=frozenset({74}),
        protected_window_regions=(ScreenRegion(115, 15, 60, 60),),
        window_filterable=False,
    )
    future = _filtered_decision(
        generation=82,
        indicator_window_ids=(),
        protected_window_ids=frozenset({75}),
        protected_window_regions=(ScreenRegion(120, 20, 50, 50),),
        window_filterable=True,
    )
    assert current.snapshot.protected_regions == future.snapshot.protected_regions
    monitor = _FakeProtectionMonitor(initial, current, future)
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(scheduler_mod.window_meta, "active_window", _safe_active_window)
    monkeypatch.setattr(
        scheduler_mod.screenshot,
        "grab_filtered_many",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("filtered helper must not run")),
    )
    monkeypatch.setattr(
        scheduler_mod.screenshot,
        "grab_many",
        lambda **kwargs: calls.append(kwargs) or [_shot("SKIP-SAFE")],
    )

    out = scheduler_mod._build_capture(
        CaptureConfig(
            screenshot_monitor="separate",
            screenshot_privacy_mode="skip-monitor",
        ),
        _FakeProvider(raw_json=None),
        None,
        protection_monitor=monitor,
    )

    assert out is not None
    assert out["screenshot"]["image_base64"] == "SKIP-SAFE"
    assert calls[0]["blocked_regions"] == current.snapshot.protected_regions
    assert monitor.force_calls == [True, False]


def test_guard_only_off_keeps_safe_shot_when_filtered_authorization_fields_change(
    ac_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    initial = _filtered_decision(
        generation=90,
        diagnostics_guard_active=True,
        window_filterable=False,
    )
    current = _filtered_decision(
        generation=91,
        diagnostics_guard_active=True,
        indicator_window_ids=(8,),
        protected_window_ids=frozenset({74}),
        protected_window_regions=(ScreenRegion(115, 15, 60, 60),),
        window_filterable=False,
    )
    future = _filtered_decision(
        generation=92,
        diagnostics_guard_active=True,
        indicator_window_ids=(),
        protected_window_ids=frozenset({75}),
        protected_window_regions=(ScreenRegion(120, 20, 50, 50),),
        window_filterable=False,
    )
    assert current.snapshot.protected_regions == future.snapshot.protected_regions
    monitor = _FakeProtectionMonitor(initial, current, future)
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(scheduler_mod.window_meta, "active_window", _safe_active_window)
    monkeypatch.setattr(
        scheduler_mod.screenshot,
        "grab_filtered_many",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("filtered helper must not run")),
    )
    monkeypatch.setattr(
        scheduler_mod.screenshot,
        "grab_many",
        lambda **kwargs: calls.append(kwargs) or [_shot("GUARD-SAFE")],
    )

    out = scheduler_mod._build_capture(
        CaptureConfig(
            screenshot_monitor="separate",
            screenshot_privacy_mode="off",
        ),
        _FakeProvider(raw_json=None),
        None,
        protection_monitor=monitor,
    )

    assert out is not None
    assert out["screenshot"]["image_base64"] == "GUARD-SAFE"
    assert calls[0]["blocked_regions"] == current.snapshot.protected_regions
    assert monitor.force_calls == [True, False]


@pytest.mark.parametrize("monitor_mode", ["separate", "all", "primary"])
def test_window_filtered_capture_uses_exact_backend_contract_for_each_monitor_mode(
    ac_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    monitor_mode: str,
) -> None:
    decision = _filtered_decision(monitor_mode=monitor_mode)
    monitor = _FakeProtectionMonitor(decision)
    filtered_calls: list[dict[str, object]] = []
    monkeypatch.setattr(scheduler_mod.window_meta, "active_window", _safe_active_window)
    monkeypatch.setattr(
        scheduler_mod.screenshot,
        "grab_filtered_many",
        lambda **kwargs: filtered_calls.append(kwargs) or [_shot("FILTERED", mode=monitor_mode)],
    )
    monkeypatch.setattr(
        scheduler_mod.screenshot,
        "grab_many",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("mss fallback must not run")),
    )
    cfg = CaptureConfig(
        screenshot_monitor=monitor_mode,
        screenshot_privacy_mode="exclude-window",
    )

    out = scheduler_mod._build_capture(
        cfg,
        _FakeProvider(raw_json=None),
        None,
        protection_monitor=monitor,
    )

    assert out is not None
    assert out["screenshot"]["image_base64"] == "FILTERED"
    assert filtered_calls == [
        {
            "monitor_mode": monitor_mode,
            "privacy_mode": "exclude-window",
            "displays": decision.snapshot.displays,
            "protected_window_ids": decision.snapshot.protected_window_ids,
            "protected_window_regions": decision.snapshot.protected_window_regions,
            "overlay_window_ids": (7, 41),
            "max_width": cfg.screenshot_max_width,
            "jpeg_quality": cfg.screenshot_jpeg_quality,
        }
    ]
    assert monitor.force_calls == [True, False, True]


@pytest.mark.parametrize("privacy_mode", ["mask-window", "exclude-window"])
@pytest.mark.parametrize(
    "phase",
    [
        ProtectionPresentationPhase.TRANSIENT_PROTECTED,
        ProtectionPresentationPhase.CLEAR_PENDING,
    ],
)
def test_effective_smoothed_protection_blocks_ax_and_keeps_filtered_capture_safe(
    ac_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    privacy_mode: str,
    phase: ProtectionPresentationPhase,
) -> None:
    decision = _filtered_decision(
        indicator_style="quiet-shield",
        active_display_id=2,
        presentation_phase=phase,
    )
    monitor = _FakeProtectionMonitor(decision)
    provider = _FakeProvider(raw_json=_edge_ax_tree("https://must-not-capture.example"))
    filtered_calls: list[dict[str, object]] = []
    monkeypatch.setattr(scheduler_mod.window_meta, "active_window", _safe_active_window)
    monkeypatch.setattr(
        scheduler_mod.screenshot,
        "grab_filtered_many",
        lambda **kwargs: filtered_calls.append(kwargs) or [_shot("SAFE-FILTERED")],
    )
    monkeypatch.setattr(
        scheduler_mod.screenshot,
        "grab_many",
        lambda **_kwargs: pytest.fail("eligible filtered capture must not use mss"),
    )

    out = scheduler_mod._build_capture(
        CaptureConfig(
            screenshot_monitor="separate",
            screenshot_privacy_mode=privacy_mode,
        ),
        provider,
        None,
        protection_monitor=monitor,
    )

    assert out is not None
    assert out["ax_skipped"] == "protected_display"
    assert provider.calls == 0
    assert out["screenshot"]["image_base64"] == "SAFE-FILTERED"
    assert filtered_calls[0]["privacy_mode"] == privacy_mode
    assert filtered_calls[0]["overlay_window_ids"] == (7, 41)


@pytest.mark.parametrize("privacy_mode", ["mask-window", "exclude-window"])
@pytest.mark.parametrize(
    "phase",
    [
        ProtectionPresentationPhase.TRANSIENT_PROTECTED,
        ProtectionPresentationPhase.CLEAR_PENDING,
    ],
)
def test_helper_unconfirmed_smoothed_protection_blocks_ax_and_all_screenshot_helpers(
    ac_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    privacy_mode: str,
    phase: ProtectionPresentationPhase,
) -> None:
    decision = _filtered_decision(
        indicator_style="quiet-shield",
        confirmed=False,
        active_display_id=2,
        presentation_phase=phase,
    )
    monitor = _FakeProtectionMonitor(decision)
    provider = _FakeProvider(raw_json=_edge_ax_tree("https://must-not-capture.example"))
    monkeypatch.setattr(scheduler_mod.window_meta, "active_window", _safe_active_window)
    monkeypatch.setattr(
        scheduler_mod.screenshot,
        "grab_filtered_many",
        lambda **_kwargs: pytest.fail("unconfirmed helper must not authorize filtered capture"),
    )
    monkeypatch.setattr(
        scheduler_mod.screenshot,
        "grab_many",
        lambda **_kwargs: pytest.fail("unconfirmed helper must not authorize mss fallback"),
    )

    out = scheduler_mod._build_capture(
        CaptureConfig(
            screenshot_monitor="separate",
            screenshot_privacy_mode=privacy_mode,
        ),
        provider,
        None,
        protection_monitor=monitor,
    )

    assert out is not None
    assert out["ax_skipped"] == "protected_display"
    assert provider.calls == 0
    assert "screenshot" not in out


def test_style_off_authorizes_filtered_capture_without_overlay_window_ids(
    ac_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    decision = _filtered_decision(indicator_style="off", indicator_window_ids=())
    monitor = _FakeProtectionMonitor(decision)
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(scheduler_mod.window_meta, "active_window", _safe_active_window)
    monkeypatch.setattr(
        scheduler_mod.screenshot,
        "grab_filtered_many",
        lambda **kwargs: calls.append(kwargs) or [_shot("FILTERED")],
    )
    monkeypatch.setattr(
        scheduler_mod.screenshot,
        "grab_many",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("mss fallback must not run")),
    )

    out = scheduler_mod._build_capture(
        CaptureConfig(
            screenshot_monitor="separate",
            screenshot_privacy_mode="mask-window",
        ),
        _FakeProvider(raw_json=None),
        None,
        protection_monitor=monitor,
    )

    assert out is not None
    assert out["screenshot"]["image_base64"] == "FILTERED"
    assert calls[0]["overlay_window_ids"] == ()


@pytest.mark.parametrize(
    ("case", "decision"),
    [
        ("diagnostics", _filtered_decision(diagnostics_guard_active=True)),
        ("unknown-title", _filtered_decision(window_filterable=False)),
        ("missing-overlay-id", _filtered_decision(indicator_window_ids=())),
        ("duplicate-overlay-id", _filtered_decision(indicator_window_ids=(7, 7))),
        ("invalid-overlay-id", _filtered_decision(indicator_window_ids=(0,))),
    ],
)
def test_ineligible_filtered_capture_uses_protected_region_mss_fallback(
    ac_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    decision: ProtectionDecision,
) -> None:
    del case
    monitor = _FakeProtectionMonitor(decision)
    mss_calls: list[dict[str, object]] = []
    monkeypatch.setattr(scheduler_mod.window_meta, "active_window", _safe_active_window)
    monkeypatch.setattr(
        scheduler_mod.screenshot,
        "grab_filtered_many",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("filtered helper must not run")),
    )
    monkeypatch.setattr(
        scheduler_mod.screenshot,
        "grab_many",
        lambda **kwargs: mss_calls.append(kwargs) or [],
    )

    out = scheduler_mod._build_capture(
        CaptureConfig(
            screenshot_monitor="separate",
            screenshot_privacy_mode="exclude-window",
        ),
        _FakeProvider(raw_json=None),
        None,
        protection_monitor=monitor,
    )

    assert out is not None
    assert mss_calls[0]["blocked_regions"] == decision.snapshot.protected_regions


def test_filtered_helper_none_refreshes_fallback_to_latest_display_before_mss(
    ac_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = _filtered_decision(generation=30, active_display_id=None)
    before_helper = _filtered_decision(generation=31, active_display_id=None)
    latest = _filtered_decision(
        generation=32,
        protected_display_ids=frozenset({1}),
        active_display_id=None,
    )
    monitor = _FakeProtectionMonitor(old, before_helper, latest, latest)
    mss_calls: list[dict[str, object]] = []
    monkeypatch.setattr(scheduler_mod.window_meta, "active_window", _safe_active_window)
    monkeypatch.setattr(scheduler_mod.screenshot, "grab_filtered_many", lambda **_kwargs: None)
    monkeypatch.setattr(
        scheduler_mod.screenshot,
        "grab_many",
        lambda **kwargs: mss_calls.append(kwargs) or [_shot("FALLBACK")],
    )

    out = scheduler_mod._build_capture(
        CaptureConfig(
            screenshot_monitor="separate",
            screenshot_privacy_mode="mask-window",
        ),
        _FakeProvider(raw_json=None),
        None,
        protection_monitor=monitor,
    )

    assert out is not None
    assert out["screenshot"]["image_base64"] == "FALLBACK"
    assert mss_calls[0]["blocked_regions"] == latest.snapshot.protected_regions
    assert monitor.force_calls[:2] == [True, False]
    assert monitor.force_calls[2:] == [True, True]


def test_initial_ineligible_fallback_refreshes_to_latest_display_before_mss(
    ac_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = _filtered_decision(
        generation=40,
        window_filterable=False,
        active_display_id=None,
    )
    before_fallback = _filtered_decision(
        generation=41,
        window_filterable=False,
        active_display_id=None,
    )
    latest = _filtered_decision(
        generation=42,
        window_filterable=False,
        protected_display_ids=frozenset({1}),
        active_display_id=None,
    )
    monitor = _FakeProtectionMonitor(old, before_fallback, latest, latest)
    mss_calls: list[dict[str, object]] = []
    monkeypatch.setattr(scheduler_mod.window_meta, "active_window", _safe_active_window)
    monkeypatch.setattr(
        scheduler_mod.screenshot,
        "grab_filtered_many",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("filtered helper must not run")),
    )
    monkeypatch.setattr(
        scheduler_mod.screenshot,
        "grab_many",
        lambda **kwargs: mss_calls.append(kwargs) or [_shot("LATEST-FALLBACK")],
    )

    out = scheduler_mod._build_capture(
        CaptureConfig(
            screenshot_monitor="separate",
            screenshot_privacy_mode="exclude-window",
        ),
        _FakeProvider(raw_json=None),
        None,
        protection_monitor=monitor,
    )

    assert out is not None
    assert out["screenshot"]["image_base64"] == "LATEST-FALLBACK"
    assert mss_calls[0]["blocked_regions"] == latest.snapshot.protected_regions
    assert monitor.force_calls[2:] == [True, True]


@pytest.mark.parametrize(
    "terminal",
    [_paused_decision(generation=52), _failed_decision(generation=53)],
    ids=["paused", "failed"],
)
def test_filtered_helper_none_stops_before_mss_when_fresh_fallback_is_terminal(
    ac_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    terminal: ProtectionDecision,
) -> None:
    monitor = _FakeProtectionMonitor(
        _filtered_decision(generation=50),
        _filtered_decision(generation=51),
        terminal,
    )
    monkeypatch.setattr(scheduler_mod.window_meta, "active_window", _safe_active_window)
    monkeypatch.setattr(scheduler_mod.screenshot, "grab_filtered_many", lambda **_kwargs: None)
    monkeypatch.setattr(
        scheduler_mod.screenshot,
        "grab_many",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("mss must not run")),
    )

    out = scheduler_mod._build_capture(
        CaptureConfig(
            screenshot_monitor="separate",
            screenshot_privacy_mode="mask-window",
            screenshot_privacy_fail_closed=False,
        ),
        _FakeProvider(raw_json=None),
        None,
        protection_monitor=monitor,
    )

    assert out is None
    assert monitor.force_calls == [True, False, True]


def test_fresh_filtered_fallback_rejects_non_off_unconfirmed_before_mss(
    ac_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monitor = _FakeProtectionMonitor(
        _filtered_decision(generation=54),
        _filtered_decision(generation=55),
        _filtered_decision(generation=56, confirmed=False),
    )
    monkeypatch.setattr(scheduler_mod.window_meta, "active_window", _safe_active_window)
    monkeypatch.setattr(scheduler_mod.screenshot, "grab_filtered_many", lambda **_kwargs: None)
    monkeypatch.setattr(
        scheduler_mod.screenshot,
        "grab_many",
        lambda **_kwargs: pytest.fail("unconfirmed indicator must stop before mss"),
    )

    out = scheduler_mod._build_capture(
        CaptureConfig(
            screenshot_monitor="separate",
            screenshot_privacy_mode="mask-window",
        ),
        _FakeProvider(raw_json=None),
        None,
        protection_monitor=monitor,
    )

    assert out is not None
    assert "screenshot" not in out
    assert monitor.force_calls == [True, False, True]


def test_fallback_frames_are_discarded_when_authorization_changes_during_mss(
    ac_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = _filtered_decision(
        generation=60,
        window_filterable=False,
        active_display_id=None,
    )
    before_mss = _filtered_decision(
        generation=62,
        window_filterable=False,
        active_display_id=None,
    )
    after_mss = _filtered_decision(
        generation=63,
        window_filterable=False,
        protected_display_ids=frozenset({1}),
        active_display_id=None,
    )
    monitor = _FakeProtectionMonitor(old, old, before_mss, after_mss)
    monkeypatch.setattr(scheduler_mod.window_meta, "active_window", _safe_active_window)
    monkeypatch.setattr(
        scheduler_mod.screenshot,
        "grab_filtered_many",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("filtered helper must not run")),
    )
    monkeypatch.setattr(
        scheduler_mod.screenshot,
        "grab_many",
        lambda **_kwargs: [_shot("STALE-FALLBACK")],
    )

    out = scheduler_mod._build_capture(
        CaptureConfig(
            screenshot_monitor="separate",
            screenshot_privacy_mode="exclude-window",
        ),
        _FakeProvider(raw_json=None),
        None,
        protection_monitor=monitor,
    )

    assert out is not None
    assert "screenshot" not in out
    assert "STALE-FALLBACK" not in json.dumps(out)
    assert monitor.force_calls == [True, False, True, True]


def test_fallback_skips_mss_when_fresh_protected_regions_are_invalid(
    ac_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    invalid = _filtered_decision(
        generation=72,
        protected_display_ids=frozenset({3}),
        active_display_id=None,
    )
    monitor = _FakeProtectionMonitor(
        _filtered_decision(generation=70, active_display_id=None),
        _filtered_decision(generation=71, active_display_id=None),
        invalid,
    )
    monkeypatch.setattr(scheduler_mod.window_meta, "active_window", _safe_active_window)
    monkeypatch.setattr(
        scheduler_mod.screenshot,
        "grab_many",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("mss must not run")),
    )

    out = scheduler_mod._build_capture(
        CaptureConfig(
            screenshot_monitor="separate",
            screenshot_privacy_mode="exclude-window",
        ),
        _FakeProvider(raw_json=None),
        None,
        protection_monitor=monitor,
    )

    assert out is not None
    assert "screenshot" not in out


@pytest.mark.parametrize("privacy_mode", ["mask-window", "exclude-window"])
def test_filtered_modes_fail_closed_on_inventory_failure_even_when_legacy_flag_is_false(
    ac_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    privacy_mode: str,
) -> None:
    monitor = _FakeProtectionMonitor(_failed_decision())
    monkeypatch.setattr(scheduler_mod.window_meta, "active_window", _safe_active_window)
    monkeypatch.setattr(
        scheduler_mod.screenshot,
        "grab_many",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("mss must not run")),
    )
    monkeypatch.setattr(
        scheduler_mod.screenshot,
        "grab_filtered_many",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("filtered helper must not run")),
    )

    out = scheduler_mod._build_capture(
        CaptureConfig(
            screenshot_privacy_mode=privacy_mode,
            screenshot_privacy_fail_closed=False,
        ),
        _FakeProvider(raw_json=None),
        None,
        protection_monitor=monitor,
    )

    assert out is None


@pytest.mark.parametrize("privacy_mode", ["mask-window", "exclude-window"])
def test_direct_capture_without_monitor_only_uses_fail_closed_visible_window_check(
    ac_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    privacy_mode: str,
) -> None:
    monkeypatch.setattr(scheduler_mod.window_meta, "active_window", _safe_active_window)
    monkeypatch.setattr(
        scheduler_mod.privacy,
        "sensitive_window_regions",
        lambda _cfg: None,
    )
    monkeypatch.setattr(
        scheduler_mod.screenshot,
        "grab_many",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("mss must not run")),
    )
    monkeypatch.setattr(
        scheduler_mod.screenshot,
        "grab_filtered_many",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("filtered helper must not run")),
    )

    out = scheduler_mod._build_capture(
        CaptureConfig(
            screenshot_privacy_mode=privacy_mode,
            screenshot_privacy_fail_closed=False,
            deny_app_names=["Private"],
        ),
        _FakeProvider(raw_json=None),
        None,
    )

    assert out is not None
    assert "screenshot" not in out


def test_direct_skip_monitor_inventory_failure_preserves_legacy_fail_open(
    ac_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(scheduler_mod.window_meta, "active_window", _safe_active_window)
    monkeypatch.setattr(
        scheduler_mod.privacy,
        "sensitive_window_regions",
        lambda _cfg: None,
    )
    monkeypatch.setattr(
        scheduler_mod.screenshot,
        "grab_many",
        lambda **kwargs: calls.append(kwargs) or [_shot("LEGACY-FAIL-OPEN")],
    )

    out = scheduler_mod._build_capture(
        CaptureConfig(
            screenshot_privacy_mode="skip-monitor",
            screenshot_privacy_fail_closed=False,
            deny_app_names=["Private"],
        ),
        _FakeProvider(raw_json=None),
        None,
    )

    assert out is not None
    assert out["screenshot"]["image_base64"] == "LEGACY-FAIL-OPEN"
    assert calls == [
        {
            "monitor_mode": "primary",
            "max_width": 1920,
            "jpeg_quality": 80,
            "blocked_regions": [],
        }
    ]


def test_filtered_inactive_uses_current_fresh_decision_and_one_post_check(
    ac_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    initial = _inactive_decision(generation=100)
    current = _inactive_decision(generation=101)
    after = _inactive_decision(generation=102)
    monitor = _FakeProtectionMonitor(initial, current, after)
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(scheduler_mod.window_meta, "active_window", _safe_active_window)
    monkeypatch.setattr(
        scheduler_mod.screenshot,
        "grab_filtered_many",
        lambda **_kwargs: pytest.fail("inactive state must not call filtered helper"),
    )
    monkeypatch.setattr(
        scheduler_mod.screenshot,
        "grab_many",
        lambda **kwargs: calls.append(kwargs) or [_shot("INACTIVE")],
    )

    with caplog.at_level(logging.INFO, logger="openchronicle.capture"):
        out = scheduler_mod._build_capture(
            CaptureConfig(
                screenshot_monitor="separate",
                screenshot_privacy_mode="exclude-window",
            ),
            _FakeProvider(raw_json=None),
            None,
            protection_monitor=monitor,
        )

    assert out is not None
    assert out["screenshot"]["image_base64"] == "INACTIVE"
    assert calls[0]["blocked_regions"] == []
    assert monitor.force_calls == [True, False, True]
    assert "screenshot fallback:" not in caplog.text


def test_filtered_inactive_clear_failure_skips_screenshot_but_keeps_capture(
    ac_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monitor = _FakeProtectionMonitor(
        _inactive_decision(generation=110),
        _inactive_decision(generation=111, confirmed=False),
    )
    monkeypatch.setattr(scheduler_mod.window_meta, "active_window", _safe_active_window)
    monkeypatch.setattr(
        scheduler_mod.screenshot,
        "grab_many",
        lambda **_kwargs: pytest.fail("unconfirmed clear must not run mss"),
    )

    out = scheduler_mod._build_capture(
        CaptureConfig(
            screenshot_monitor="separate",
            screenshot_privacy_mode="mask-window",
        ),
        _FakeProvider(raw_json=_edge_ax_tree("https://safe.example")),
        None,
        protection_monitor=monitor,
    )

    assert out is not None
    assert "ax_tree" in out
    assert "screenshot" not in out
    assert monitor.force_calls == [True, False]


def test_real_monitor_unconfirmed_inactive_clear_blocks_only_screenshot_until_ack(
    ac_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    displays = (
        DisplayInfo(1, ScreenRegion(0, 0, 100, 100), True),
        DisplayInfo(2, ScreenRegion(100, 0, 100, 100), False),
    )
    safe_windows = (
        VisibleWindow("Cursor", "cursor", "main.py", displays[0].region, True),
    )
    protected_inventory = WindowInventory(
        windows=(
            VisibleWindow("Edge", "edge", "Private", displays[1].region),
            *safe_windows,
        ),
        displays=displays,
    )
    safe_inventory = WindowInventory(windows=safe_windows, displays=displays)
    inventory = protected_inventory
    now = 10.0

    class SequencedClearOverlay(_AlwaysConfirmedOverlay):
        def __init__(self) -> None:
            self.clear_results = iter((False, True, True))

        def clear(self, _generation: int, timeout: float = 0.5) -> bool:
            return next(self.clear_results)

    cfg = CaptureConfig(
        screenshot_monitor="separate",
        screenshot_privacy_mode="mask-window",
        privacy_indicator_style="pill",
        deny_window_title_patterns=["Private"],
    )
    monitor = PrivacyProtectionMonitor(
        cfg,
        config_path=ac_root / "missing-config.toml",
        overlay=SequencedClearOverlay(),
        inventory_reader=lambda: inventory,
        pause_reader=lambda: False,
        monotonic=lambda: now,
    )
    provider = _FakeProvider(raw_json=_edge_ax_tree("https://safe.example"))
    mss_calls: list[dict[str, object]] = []
    filtered_calls: list[dict[str, object]] = []
    monkeypatch.setattr(scheduler_mod.window_meta, "active_window", _safe_active_window)
    monkeypatch.setattr(
        scheduler_mod.screenshot,
        "grab_many",
        lambda **kwargs: mss_calls.append(kwargs) or [_shot("CONFIRMED-INACTIVE")],
    )
    monkeypatch.setattr(
        scheduler_mod.screenshot,
        "grab_filtered_many",
        lambda **kwargs: filtered_calls.append(kwargs) or [],
    )

    try:
        monitor.decision_for_capture(force=True)
        inventory = safe_inventory
        now = 10.1
        first_safe = monitor.decision_for_capture(force=True)
        assert first_safe.presentation_phase is ProtectionPresentationPhase.CLEAR_PENDING

        now = 10.3
        without_screenshot = scheduler_mod._build_capture(
            cfg,
            provider,
            None,
            protection_monitor=monitor,
        )
        unconfirmed = monitor.decision_for_capture(force=False)
        diagnostics = PrivacyDiagnosticsServer._snapshot_payload(
            unconfirmed,
            detail="category",
            created_at="2026-08-25T00:00:00Z",
        )

        assert without_screenshot is not None
        assert "ax_tree" in without_screenshot
        assert "screenshot" not in without_screenshot
        assert provider.calls == 1
        assert mss_calls == []
        assert filtered_calls == []
        assert unconfirmed.snapshot.state is ProtectionState.INACTIVE
        assert unconfirmed.indicator_confirmed is False
        assert all(display["screenshot_blocked"] is True for display in diagnostics["displays"])
        assert all(display["ax_blocked"] is False for display in diagnostics["displays"])

        resumed = scheduler_mod._build_capture(
            cfg,
            provider,
            None,
            protection_monitor=monitor,
        )
    finally:
        monitor.stop()

    assert resumed is not None
    assert "ax_tree" in resumed
    assert provider.calls == 2
    assert len(mss_calls) == 1
    assert filtered_calls == []


def test_filtered_inactive_post_change_discards_unblocked_mss_frame(
    ac_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    protected_after = _filtered_decision(generation=122)
    monitor = _FakeProtectionMonitor(
        _inactive_decision(generation=120),
        _inactive_decision(generation=121),
        protected_after,
    )
    monkeypatch.setattr(scheduler_mod.window_meta, "active_window", _safe_active_window)
    monkeypatch.setattr(
        scheduler_mod.screenshot,
        "grab_many",
        lambda **_kwargs: [_shot("STALE-INACTIVE")],
    )

    out = scheduler_mod._build_capture(
        CaptureConfig(
            screenshot_monitor="separate",
            screenshot_privacy_mode="exclude-window",
        ),
        _FakeProvider(raw_json=None),
        None,
        protection_monitor=monitor,
    )

    assert out is not None
    assert "screenshot" not in out
    assert "STALE-INACTIVE" not in json.dumps(out)
    assert monitor.force_calls == [True, False, True]


def test_direct_off_capture_ignores_background_sensitive_window_protection(
    ac_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(scheduler_mod.window_meta, "active_window", _safe_active_window)
    monkeypatch.setattr(
        scheduler_mod.privacy,
        "sensitive_window_regions",
        lambda _cfg: (_ for _ in ()).throw(
            AssertionError("off mode must not enumerate background windows")
        ),
    )
    monkeypatch.setattr(
        scheduler_mod.screenshot,
        "grab_many",
        lambda **kwargs: calls.append(kwargs) or [_shot("OFF")],
    )

    out = scheduler_mod._build_capture(
        CaptureConfig(
            screenshot_privacy_mode="off",
            deny_app_names=["Private"],
        ),
        _FakeProvider(raw_json=None),
        None,
    )

    assert out is not None
    assert out["screenshot"]["image_base64"] == "OFF"
    assert calls[0]["blocked_regions"] == []


@pytest.mark.parametrize(
    "changed_semantics",
    [
        "protected-window-ids",
        "protected-window-regions",
        "display-ids",
        "display-bounds",
        "protected-display-ids",
        "eligibility",
        "overlay-window-ids",
    ],
)
def test_post_helper_authorization_change_discards_stale_filtered_frames(
    ac_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    changed_semantics: str,
) -> None:
    initial = _filtered_decision(generation=30)
    before_helper = _filtered_decision(generation=31)
    changes: dict[str, object] = {}
    if changed_semantics == "protected-window-ids":
        changes["protected_window_ids"] = frozenset({74})
    elif changed_semantics == "protected-window-regions":
        changes["protected_window_regions"] = (ScreenRegion(115, 15, 60, 60),)
    elif changed_semantics == "display-ids":
        changes["displays"] = (
            DisplayInfo(1, ScreenRegion(0, 0, 100, 100), True),
            DisplayInfo(3, ScreenRegion(100, 0, 100, 100), False),
        )
        changes["protected_display_ids"] = frozenset({3})
    elif changed_semantics == "display-bounds":
        changes["displays"] = (
            DisplayInfo(1, ScreenRegion(0, 0, 100, 100), True),
            DisplayInfo(2, ScreenRegion(100, 0, 120, 100), False),
        )
    elif changed_semantics == "protected-display-ids":
        changes["protected_display_ids"] = frozenset({1})
    elif changed_semantics == "eligibility":
        changes["window_filterable"] = False
    else:
        changes["indicator_window_ids"] = (8, 41)
    latest = _filtered_decision(generation=32, **changes)
    monitor = _FakeProtectionMonitor(initial, before_helper, latest)
    mss_calls: list[dict[str, object]] = []
    monkeypatch.setattr(scheduler_mod.window_meta, "active_window", _safe_active_window)
    monkeypatch.setattr(
        scheduler_mod.screenshot,
        "grab_filtered_many",
        lambda **_kwargs: [_shot("STALE-FILTERED")],
    )
    monkeypatch.setattr(
        scheduler_mod.screenshot,
        "grab_many",
        lambda **kwargs: mss_calls.append(kwargs) or [_shot("LATEST-FALLBACK")],
    )

    out = scheduler_mod._build_capture(
        CaptureConfig(
            screenshot_monitor="separate",
            screenshot_privacy_mode="exclude-window",
        ),
        _FakeProvider(raw_json=None),
        None,
        protection_monitor=monitor,
    )

    assert out is not None
    assert out["screenshot"]["image_base64"] == "LATEST-FALLBACK"
    assert "STALE-FILTERED" not in json.dumps(out)
    assert mss_calls[0]["blocked_regions"] == latest.snapshot.protected_regions
    assert monitor.force_calls == [True, False, True, True]


def test_post_helper_terminal_decision_discards_stale_frames_without_screenshot_fallback(
    ac_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monitor = _FakeProtectionMonitor(
        _filtered_decision(generation=30),
        _filtered_decision(generation=31),
        _failed_decision(generation=32),
    )
    monkeypatch.setattr(scheduler_mod.window_meta, "active_window", _safe_active_window)
    monkeypatch.setattr(
        scheduler_mod.screenshot,
        "grab_filtered_many",
        lambda **_kwargs: [_shot("STALE-FILTERED")],
    )
    monkeypatch.setattr(
        scheduler_mod.screenshot,
        "grab_many",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("mss must not run")),
    )

    out = scheduler_mod._build_capture(
        CaptureConfig(
            screenshot_monitor="separate",
            screenshot_privacy_mode="mask-window",
            screenshot_privacy_fail_closed=False,
        ),
        _FakeProvider(raw_json=None),
        None,
        protection_monitor=monitor,
    )

    assert out is None


def test_post_helper_unconfirmed_decision_discards_without_unconfirmed_mss(
    ac_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    latest = _filtered_decision(generation=32, confirmed=False)
    monitor = _FakeProtectionMonitor(
        _filtered_decision(generation=30),
        _filtered_decision(generation=31),
        latest,
    )
    monkeypatch.setattr(scheduler_mod.window_meta, "active_window", _safe_active_window)
    monkeypatch.setattr(
        scheduler_mod.screenshot,
        "grab_filtered_many",
        lambda **_kwargs: [_shot("STALE-FILTERED")],
    )
    monkeypatch.setattr(
        scheduler_mod.screenshot,
        "grab_many",
        lambda **_kwargs: pytest.fail("unconfirmed indicator must stop before mss"),
    )

    out = scheduler_mod._build_capture(
        CaptureConfig(
            screenshot_monitor="separate",
            screenshot_privacy_mode="exclude-window",
        ),
        _FakeProvider(raw_json=None),
        None,
        protection_monitor=monitor,
    )

    assert out is not None
    assert "screenshot" not in out
    assert "STALE-FILTERED" not in json.dumps(out)
    assert monitor.force_calls == [True, False, True]


@pytest.mark.asyncio
async def test_watcher_requests_monitor_refresh_before_queueing_capture(monkeypatch) -> None:
    order: list[str] = []
    event_was_queued = asyncio.Event()

    class FakeMonitor:
        def request_refresh(self) -> None:
            order.append("refresh")

    class FakeRunner:
        def __init__(self, *_args, **_kwargs) -> None:
            return None

        def start_worker(self) -> None:
            return None

        def run_threaded(self, trigger) -> None:
            order.append("queue")
            if trigger is not None:
                event_was_queued.set()

        def stop_worker(self) -> None:
            return None

    class FakeWatcher:
        available = True

        def on_event(self, callback) -> None:
            self.callback = callback

        def start(self) -> None:
            self.callback({"event_type": "AXFocusedWindowChanged"})

        def stop(self) -> None:
            return None

    class FakeDispatcher:
        def __init__(self, callback, **_kwargs) -> None:
            self.callback = callback

        def on_event(self, event) -> None:
            self.callback(event)

        def shutdown(self) -> None:
            return None

    monkeypatch.setattr(scheduler_mod.ax_capture, "create_provider", lambda **_: _FakeProvider())
    monkeypatch.setattr(scheduler_mod, "_CaptureRunner", FakeRunner)
    monkeypatch.setattr(scheduler_mod, "AXWatcherProcess", FakeWatcher)
    monkeypatch.setattr(scheduler_mod, "EventDispatcher", FakeDispatcher)

    task = asyncio.create_task(
        scheduler_mod.run_forever(
            CaptureConfig(heartbeat_minutes=0),
            protection_monitor=FakeMonitor(),
        )
    )
    await asyncio.wait_for(event_was_queued.wait(), timeout=1.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert order[:2] == ["refresh", "queue"]


def test_write_capture_indexes_into_fts(ac_root: Path) -> None:
    out = _capture_dict(
        ts="2026-04-22T14:00:00+08:00",
        app="Cursor", title="main.py",
        value="def foo()", text="def foo(): return 1",
    )
    path = scheduler_mod._write_capture(out)
    assert path.exists()

    with fts.cursor() as conn:
        hits = fts.search_captures(conn, query="foo")
        assert len(hits) == 1
        assert hits[0].id == path.stem
    assert hits[0].app_name == "Cursor"


def test_cleanup_buffer_strips_screenshot_arrays(ac_root: Path) -> None:
    out = _capture_dict(
        ts="2026-04-22T14:00:00+08:00",
        app="Cursor", title="main.py",
        value="", text="visible text",
    )
    out["screenshots"] = [
        {
            "image_base64": "AAAA",
            "mime_type": "image/jpeg",
            "width": 100,
            "height": 50,
        },
        {
            "image_base64": "BBBB",
            "mime_type": "image/jpeg",
            "width": 100,
            "height": 50,
        },
    ]
    path = scheduler_mod._write_capture(out)
    long_ago = time.time() - 48 * 3600
    os.utime(path, (long_ago, long_ago))

    stats = scheduler_mod.cleanup_buffer(
        retention_hours=24 * 365,
        processed_before_ts="2099-01-01T00:00:00+00:00",
        screenshot_retention_hours=24,
        max_mb=0,
    )

    data = path.read_text()
    assert stats["stripped"] == 1
    assert '"screenshot"' not in data
    assert '"screenshots"' not in data
    assert '"screenshot_stripped": true' in data


def test_cleanup_buffer_removes_fts_rows(ac_root: Path) -> None:
    """Time-based delete pass should also drop matching FTS rows."""
    captures = [
        ("2026-04-22T10:00:00+08:00", "old1"),
        ("2026-04-22T11:00:00+08:00", "old2"),
        ("2026-04-22T12:00:00+08:00", "keep"),
    ]
    written: list[Path] = []
    for ts, marker in captures:
        out = _capture_dict(
            ts=ts, app="Cursor", title=f"win-{marker}",
            value="", text=f"unique-text-{marker}",
        )
        written.append(scheduler_mod._write_capture(out))

    with fts.cursor() as conn:
        assert len(fts.recent_captures(conn, limit=10)) == 3

    # Backdate the two "old" files so the delete pass picks them up.
    long_ago = time.time() - 10 * 24 * 3600
    for p in written[:2]:
        os.utime(p, (long_ago, long_ago))

    # processed_before_ts past every stem so all are considered "absorbed".
    stats = scheduler_mod.cleanup_buffer(
        retention_hours=24,
        processed_before_ts="2099-01-01T00:00:00+00:00",
        screenshot_retention_hours=None,
        max_mb=0,
    )
    assert stats["deleted"] == 2
    assert stats["evicted"] == 0

    with fts.cursor() as conn:
        rec = fts.recent_captures(conn, limit=10)
        assert {h.id for h in rec} == {written[2].stem}


def test_cleanup_eviction_also_drops_fts(ac_root: Path) -> None:
    """Size-based eviction should also drop matching FTS rows."""
    written: list[Path] = []
    for i in range(3):
        ts = f"2026-04-22T1{i}:00:00+08:00"
        out = _capture_dict(
            ts=ts, app="Cursor", title=f"w-{i}",
            value="", text="x" * 500_000,  # ~500 KB each → 1.5 MB total
        )
        written.append(scheduler_mod._write_capture(out))

    # Tight 1 MB cap forces eviction of the oldest.
    stats = scheduler_mod.cleanup_buffer(
        retention_hours=24 * 365,
        processed_before_ts="2099-01-01T00:00:00+00:00",
        screenshot_retention_hours=None,
        max_mb=1,
    )
    assert stats["evicted"] >= 1
    with fts.cursor() as conn:
        remaining = {h.id for h in fts.recent_captures(conn, limit=10)}
    assert len(remaining) == 3 - stats["evicted"]
    # Newest survives.
    assert written[-1].stem in remaining
