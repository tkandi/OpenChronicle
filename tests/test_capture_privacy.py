from __future__ import annotations

import sys
from types import SimpleNamespace

from openchronicle.capture import privacy, screenshot
from openchronicle.config import CaptureConfig


def _window(
    *, app: str = "", bundle: str = "", title: str = "", left: float = 0
) -> privacy.VisibleWindow:
    return privacy.VisibleWindow(
        app_name=app,
        bundle_id=bundle,
        title=title,
        region=privacy.ScreenRegion(left=left, top=0, width=100, height=100),
    )


def test_sensitive_window_regions_match_all_metadata_fields(monkeypatch) -> None:
    cfg = CaptureConfig(
        deny_app_names=["Passwords"],
        deny_bundle_ids=["com.1password.1password"],
        deny_window_title_patterns=["InPrivate", "无痕"],
    )
    windows = [
        _window(app="Passwords", left=0),
        _window(bundle="com.1password.1password", left=100),
        _window(title="New tab - InPrivate", left=200),
        _window(app="Cursor", title="main.py", left=300),
    ]
    monkeypatch.setattr(privacy, "list_visible_windows", lambda: windows)

    regions = privacy.sensitive_window_regions(cfg)

    assert regions is not None
    assert [region.left for region in regions] == [0, 100, 200]


def test_sensitive_window_regions_propagates_enumeration_failure(monkeypatch) -> None:
    cfg = CaptureConfig(deny_app_names=["Passwords"])
    monkeypatch.setattr(privacy, "list_visible_windows", lambda: None)

    assert privacy.sensitive_window_regions(cfg) is None


def test_sensitive_window_regions_avoids_helper_without_window_rules(monkeypatch) -> None:
    cfg = CaptureConfig(deny_url_patterns=["private"])
    monkeypatch.setattr(
        privacy,
        "list_visible_windows",
        lambda: (_ for _ in ()).throw(AssertionError("helper should not run")),
    )

    assert privacy.sensitive_window_regions(cfg) == []


class _FakeMSS:
    monitors = [
        {"left": 0, "top": 0, "width": 200, "height": 100},
        {"left": 0, "top": 0, "width": 100, "height": 100},
        {"left": 100, "top": 0, "width": 100, "height": 100},
    ]
    primary_monitor = monitors[1]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


def test_separate_mode_skips_only_intersecting_monitor(monkeypatch) -> None:
    fake_mss = _FakeMSS()
    monkeypatch.setitem(sys.modules, "mss", SimpleNamespace(mss=lambda: fake_mss))
    monkeypatch.setitem(sys.modules, "PIL", SimpleNamespace(Image=object()))
    monkeypatch.setattr(
        screenshot,
        "_grab_monitor",
        lambda _sct, _image, _mon, **kwargs: screenshot.Screenshot(
            image_base64=str(kwargs["monitor_index"]),
            monitor_index=kwargs["monitor_index"],
        ),
    )

    shots = screenshot.grab_many(
        monitor_mode="separate",
        blocked_regions=[privacy.ScreenRegion(left=120, top=10, width=10, height=10)],
    )

    assert [shot.monitor_index for shot in shots] == [1]


def test_all_mode_skips_virtual_desktop_for_any_intersection(monkeypatch) -> None:
    fake_mss = _FakeMSS()
    monkeypatch.setitem(sys.modules, "mss", SimpleNamespace(mss=lambda: fake_mss))
    monkeypatch.setitem(sys.modules, "PIL", SimpleNamespace(Image=object()))
    monkeypatch.setattr(
        screenshot,
        "_grab_monitor",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("virtual desktop should be blocked")
        ),
    )

    shots = screenshot.grab_many(
        monitor_mode="all",
        blocked_regions=[privacy.ScreenRegion(left=120, top=10, width=10, height=10)],
    )

    assert shots == []
