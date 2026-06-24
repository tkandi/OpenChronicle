"""capture/scheduler.py: write-through to captures_fts + delete-through on cleanup."""

from __future__ import annotations

import os
import time
from pathlib import Path

from openchronicle.capture import scheduler as scheduler_mod
from openchronicle.capture import window_meta
from openchronicle.capture.ax_models import AXCaptureResult
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
    cfg = CaptureConfig(deny_window_title_patterns=["InPrivate", "无痕"])
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
    cfg = CaptureConfig(deny_url_patterns=["account\\.example"])
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
