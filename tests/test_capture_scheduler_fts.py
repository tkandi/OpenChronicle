"""capture/scheduler.py: write-through to captures_fts + delete-through on cleanup."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path

import pytest

from openchronicle.capture import scheduler as scheduler_mod
from openchronicle.capture import window_meta
from openchronicle.capture.ax_models import AXCaptureResult
from openchronicle.capture.privacy import (
    DisplayInfo,
    ProtectionFailureReason,
    ScreenRegion,
    VisibleWindow,
    WindowInventory,
)
from openchronicle.capture.protection import ProtectionSnapshot, ProtectionState
from openchronicle.capture.protection_monitor import (
    PrivacyProtectionMonitor,
    ProtectionDecision,
)
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


def _protection_decision(
    *,
    generation: int = 20,
    state: ProtectionState = ProtectionState.PROTECTED,
    active_display_id: int | None,
    protected_ids: set[int],
    confirmed: bool,
    fresh: bool = True,
    failure_reason: ProtectionFailureReason | None = None,
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
    return ProtectionDecision(snapshot=snapshot, indicator_confirmed=confirmed)


def _failed_decision(
    *,
    reason: ProtectionFailureReason = ProtectionFailureReason.INVENTORY_UNAVAILABLE,
    generation: int = 21,
) -> ProtectionDecision:
    return _protection_decision(
        generation=generation,
        state=ProtectionState.FAILED,
        active_display_id=None,
        protected_ids=set(),
        confirmed=True,
        failure_reason=reason,
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


def test_unconfirmed_indicator_fails_screenshot_closed(
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
        lambda **_: (_ for _ in ()).throw(AssertionError("screenshot must not run")),
    )

    out = scheduler_mod._build_capture(
        CaptureConfig(),
        _FakeProvider(raw_json=None),
        None,
        protection_monitor=monitor,
    )

    assert out is not None
    assert "screenshot" not in out


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
        lambda: window_meta.WindowMeta(app_name="Cursor", title="main.py", bundle_id="cursor"),
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
        ProtectionDecision(snapshot=_failed_decision().snapshot, indicator_confirmed=False)
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


def test_pause_state_failure_blocks_before_ax_even_when_inventory_is_fail_open(
    ac_root: Path, monkeypatch,
) -> None:
    provider = _FakeProvider(raw_json=_edge_ax_tree("https://safe.example"))
    monitor = _FakeProtectionMonitor(
        _failed_decision(reason=ProtectionFailureReason.PAUSE_STATE_UNAVAILABLE)
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
        CaptureConfig(screenshot_privacy_fail_closed=False),
        provider,
        None,
        protection_monitor=monitor,
    )

    assert out is None
    assert provider.calls == 0
    assert monitor.force_calls == [True]


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
    ac_root: Path, monkeypatch,
) -> None:
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
            VisibleWindow("Edge", "edge", "InPrivate", ScreenRegion(0, 0, 80, 90)),
            VisibleWindow("Cursor", "cursor", "main.py", ScreenRegion(0, 0, 80, 90), True),
        ),
        displays=displays,
    )
    inventory = safe_inventory

    class Overlay:
        def render(self, _snapshot, timeout: float = 0.5) -> bool:
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
            deny_window_title_patterns=["InPrivate"],
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
        out = scheduler_mod._build_capture(
            CaptureConfig(
                screenshot_monitor="separate",
                deny_window_title_patterns=["InPrivate"],
            ),
            provider,
            None,
            protection_monitor=monitor,
        )
    finally:
        monitor.stop()

    assert out is None
    assert provider.calls == 1


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
