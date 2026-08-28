from __future__ import annotations

import logging
import math
import os
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from openchronicle.capture import privacy, screenshot
from openchronicle.config import CaptureConfig

DISPLAY_1 = privacy.DisplayInfo(1, privacy.ScreenRegion(0, 0, 100, 100), True)
DISPLAY_2 = privacy.DisplayInfo(2, privacy.ScreenRegion(100, 0, 100, 100), False)


def _window(
    *,
    app: str = "",
    bundle: str = "",
    title: str = "",
    alternate_title: str = "",
    left: float = 0,
    title_available: bool = True,
    layer: int = 0,
) -> privacy.VisibleWindow:
    return privacy.VisibleWindow(
        app_name=app,
        bundle_id=bundle,
        title=title,
        region=privacy.ScreenRegion(left=left, top=0, width=100, height=100),
        title_available=title_available,
        alternate_title=alternate_title,
        layer=layer,
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


def test_visible_window_parses_only_positive_integer_window_ids() -> None:
    row = {
        "app_name": "Private Browser",
        "bundle_id": "com.example.private",
        "title": "Private",
        "left": 10,
        "top": 20,
        "width": 300,
        "height": 200,
    }

    parsed = privacy._parse_visible_window({**row, "window_id": 73, "layer": 3})
    assert parsed.window_id == 73
    assert parsed.layer == 3
    assert privacy._parse_visible_window(row).layer == 0
    assert privacy._parse_visible_window(row).window_id is None

    for invalid_id in (0, -1, 1.0, True, "73", 0x1_0000_0000):
        with pytest.raises((TypeError, ValueError)):
            privacy._parse_visible_window({**row, "window_id": invalid_id})

    for invalid_layer in (1.0, True, "3"):
        with pytest.raises((TypeError, ValueError)):
            privacy._parse_visible_window({**row, "layer": invalid_layer})


def test_visible_window_rule_matches_keep_every_matching_rule_and_value() -> None:
    cfg = CaptureConfig(
        deny_app_names=["Private Browser"],
        deny_bundle_ids=["com.example.private"],
        deny_window_title_patterns=["InPrivate"],
    )
    window = _window(
        app="Private Browser",
        bundle="com.example.private",
        title="New InPrivate Window",
    )

    matches = privacy.visible_window_rule_matches(cfg, window)

    assert [(match.kind.value, match.rule) for match in matches] == [
        ("app_rule", "Private Browser"),
        ("bundle_rule", "com.example.private"),
        ("window_title_rule", "InPrivate"),
    ]
    assert all(match.app_name == "Private Browser" for match in matches)
    assert all(match.bundle_id == "com.example.private" for match in matches)
    assert all(match.window_title == "New InPrivate Window" for match in matches)


def test_visible_window_rule_matches_primary_title_before_alternate_title() -> None:
    cfg = CaptureConfig(deny_window_title_patterns=["Secret", "InPrivate"])

    matches = privacy.visible_window_rule_matches(
        cfg,
        _window(
            title="Secret browsing",
            alternate_title="Google - Microsoft Edge (InPrivate)",
        ),
    )

    assert [(match.rule, match.window_title) for match in matches] == [
        ("Secret", "Secret browsing"),
        ("InPrivate", "Google - Microsoft Edge (InPrivate)"),
    ]


def test_visible_window_rule_matches_deduplicate_casefolded_rule_across_titles() -> None:
    cfg = CaptureConfig(deny_window_title_patterns=["INPRIVATE", "inprivate"])

    matches = privacy.visible_window_rule_matches(
        cfg,
        _window(
            title="New InPrivate Window",
            alternate_title="Google - Microsoft Edge (InPrivate)",
        ),
    )

    assert [(match.rule, match.window_title) for match in matches] == [
        ("INPRIVATE", "New InPrivate Window"),
    ]


def test_unknown_window_title_has_no_invented_title_rule_or_value() -> None:
    cfg = CaptureConfig(deny_window_title_patterns=["InPrivate"])

    matches = privacy.visible_window_rule_matches(
        cfg,
        _window(
            app="Microsoft Edge",
            bundle="com.microsoft.edgemac",
            title="",
            title_available=False,
        ),
    )

    assert len(matches) == 1
    assert matches[0].kind.value == "window_title_unknown"
    assert matches[0].rule is None
    assert matches[0].window_title is None


@pytest.mark.parametrize(
    "bundle",
    ["com.microsoft.edgemac", "com.google.Chrome", "org.mozilla.firefox"],
)
def test_unknown_browser_title_is_protected_by_default(bundle: str) -> None:
    cfg = CaptureConfig(deny_window_title_patterns=["InPrivate"])

    matches = privacy.visible_window_rule_matches(
        cfg,
        _window(app="Browser", bundle=bundle, title="", title_available=False),
    )

    assert [match.kind.value for match in matches] == ["window_title_unknown"]


def test_non_layer_unknown_browser_title_does_not_match() -> None:
    cfg = CaptureConfig(deny_window_title_patterns=["InPrivate"])

    matches = privacy.visible_window_rule_matches(
        cfg,
        _window(
            app="Microsoft Edge",
            bundle="com.microsoft.edgemac",
            title="",
            title_available=False,
            layer=3,
        ),
    )

    assert matches == ()


def test_unknown_feishu_title_is_not_protected() -> None:
    cfg = CaptureConfig(deny_window_title_patterns=["InPrivate"])

    matches = privacy.visible_window_rule_matches(
        cfg,
        _window(
            app="飞书会议",
            bundle="com.electron.lark.iron",
            title="",
            title_available=False,
        ),
    )

    assert matches == ()


def test_unknown_title_bundle_scope_is_case_insensitive() -> None:
    cfg = CaptureConfig(
        deny_window_title_patterns=["InPrivate"],
        protect_unknown_title_bundle_ids=["COM.MICROSOFT.EDGEMAC"],
    )

    matches = privacy.visible_window_rule_matches(
        cfg,
        _window(bundle="com.microsoft.edgemac", title_available=False),
    )

    assert [match.kind.value for match in matches] == ["window_title_unknown"]


def test_empty_unknown_title_bundle_scope_disables_only_unknown_branch() -> None:
    cfg = CaptureConfig(
        deny_window_title_patterns=["InPrivate"],
        protect_unknown_title_bundle_ids=[],
    )

    assert privacy.visible_window_rule_matches(
        cfg,
        _window(bundle="com.microsoft.edgemac", title_available=False),
    ) == ()
    known = privacy.visible_window_rule_matches(
        cfg,
        _window(bundle="com.electron.lark.iron", title="New InPrivate Window"),
    )
    assert [match.kind.value for match in known] == ["window_title_rule"]


def test_direct_bundle_rule_still_protects_unknown_feishu_title() -> None:
    cfg = CaptureConfig(
        deny_bundle_ids=["com.electron.lark.iron"],
        deny_window_title_patterns=["InPrivate"],
        protect_unknown_title_bundle_ids=[],
    )

    matches = privacy.visible_window_rule_matches(
        cfg,
        _window(
            app="飞书会议",
            bundle="com.electron.lark.iron",
            title_available=False,
        ),
    )

    assert [match.kind.value for match in matches] == ["bundle_rule"]


def test_unknown_window_title_with_only_empty_patterns_has_no_match() -> None:
    cfg = CaptureConfig(deny_window_title_patterns=[""])

    matches = privacy.visible_window_rule_matches(
        cfg,
        _window(app="Browser", bundle="com.example.browser", title="", title_available=False),
    )

    assert matches == ()


def test_privacy_mode_off_preserves_every_foreground_denylist_field() -> None:
    cases = [
        (CaptureConfig(screenshot_privacy_mode="off", deny_app_names=["PrivateApp"]), {
            "window_meta": {"app_name": "PrivateApp"},
        }, "app_name"),
        (CaptureConfig(screenshot_privacy_mode="off", deny_bundle_ids=["private.bundle"]), {
            "window_meta": {"bundle_id": "private.bundle"},
        }, "bundle_id"),
        (CaptureConfig(screenshot_privacy_mode="off", deny_window_title_patterns=["private"]), {
            "window_meta": {"title": "Private window"},
        }, "window_title"),
        (CaptureConfig(screenshot_privacy_mode="off", deny_url_patterns=["private"]), {
            "url": "https://private.example",
        }, "url"),
        (CaptureConfig(screenshot_privacy_mode="off", deny_text_patterns=["private"]), {
            "focused_element": {"value": "private value"},
        }, "focused_value"),
        (CaptureConfig(screenshot_privacy_mode="off", deny_text_patterns=["private"]), {
            "visible_text": "private text",
        }, "visible_text"),
    ]

    for cfg, capture, expected_reason in cases:
        assert privacy.capture_denylist_reason(cfg, capture) == expected_reason


def test_unknown_title_is_locally_sensitive_when_title_rules_are_enabled(monkeypatch) -> None:
    cfg = CaptureConfig(deny_window_title_patterns=["InPrivate"])
    windows = [
        _window(
            app="Microsoft Edge",
            bundle="com.microsoft.edgemac",
            left=100,
            title_available=False,
        ),
        _window(app="Known", title="main.py", left=200),
    ]
    monkeypatch.setattr(privacy, "list_visible_windows", lambda: windows)

    regions = privacy.sensitive_window_regions(cfg)

    assert regions is not None
    assert [region.left for region in regions] == [100]


def test_sensitive_window_regions_propagates_enumeration_failure(monkeypatch) -> None:
    cfg = CaptureConfig(deny_app_names=["Passwords"])
    monkeypatch.setattr(privacy, "list_visible_windows", lambda: None)

    assert privacy.sensitive_window_regions(cfg) is None


def test_read_window_inventory_parses_displays_and_active_window(monkeypatch) -> None:
    monkeypatch.setattr(
        privacy,
        "_read_window_list_helper",
        lambda: privacy.WindowListReadResult({
            "schema_version": 1,
            "windows": [
                {
                    "app_name": "Cursor",
                    "bundle_id": "com.cursor.Cursor",
                    "title": "main.py",
                    "alternate_title": "main.py - Cursor",
                    "left": 100,
                    "top": 20,
                    "width": 90,
                    "height": 80,
                    "is_active": True,
                    "title_available": False,
                    "is_active_candidate": True,
                }
            ],
            "displays": [
                {
                    "id": 2,
                    "left": 100,
                    "top": 0,
                    "width": 100,
                    "height": 100,
                    "is_primary": False,
                }
            ],
        }, None),
    )

    inventory = privacy.read_window_inventory()

    assert inventory is not None
    assert inventory.displays == (
        privacy.DisplayInfo(2, privacy.ScreenRegion(100, 0, 100, 100), False),
    )
    assert inventory.windows[0].is_active is True
    assert inventory.windows[0].alternate_title == "main.py - Cursor"
    assert inventory.windows[0].title_available is False
    assert inventory.windows[0].is_active_candidate is True


@pytest.mark.parametrize("schema_version", [None, True, 2, "1"])
def test_read_window_inventory_rejects_incompatible_helper_schema(
    monkeypatch,
    schema_version,
) -> None:
    raw = {
        "windows": [],
        "displays": [
            {"id": 1, "left": 0, "top": 0, "width": 100, "height": 100}
        ],
    }
    if schema_version is not None:
        raw["schema_version"] = schema_version
    monkeypatch.setattr(
        privacy,
        "_read_window_list_helper",
        lambda: privacy.WindowListReadResult(raw, None),
    )

    result = privacy.read_window_inventory_result()

    assert result.inventory is None
    assert result.failure_reason is privacy.ProtectionFailureReason.HELPER_PARSE


def test_read_window_inventory_defaults_additive_flags_for_schema_one_helper(monkeypatch) -> None:
    monkeypatch.setattr(
        privacy,
        "_read_window_list_helper",
        lambda: privacy.WindowListReadResult(
            {
                "schema_version": 1,
                "windows": [
                    {
                        "title": "known",
                        "left": 0,
                        "top": 0,
                        "width": 10,
                        "height": 10,
                    }
                ],
                "displays": [
                    {"id": 1, "left": 0, "top": 0, "width": 100, "height": 100}
                ],
            },
            None,
        ),
    )

    inventory = privacy.read_window_inventory()

    assert inventory is not None
    assert inventory.windows[0].title_available is True
    assert inventory.windows[0].is_active_candidate is False
    assert inventory.windows[0].alternate_title == ""


def test_maybe_compile_rejects_stale_binary_when_compile_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    swift_path = tmp_path / "mac-window-list.swift"
    core_path = tmp_path / "mac-window-list-core.swift"
    binary_path = tmp_path / "mac-window-list"
    swift_path.write_text("// main")
    core_path.write_text("// core")
    binary_path.write_text("old binary")
    binary_path.chmod(0o755)
    os.utime(binary_path, (1, 1))
    os.utime(swift_path, (2, 2))
    os.utime(core_path, (2, 2))
    monkeypatch.setattr(
        privacy.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stderr="fixed failure"),
    )

    assert privacy._maybe_compile(swift_path, binary_path) is False


def test_read_window_inventory_rejects_empty_displays(monkeypatch) -> None:
    monkeypatch.setattr(
        privacy,
        "_read_window_list_helper",
        lambda: privacy.WindowListReadResult(
            {"schema_version": 1, "windows": [], "displays": []},
            None,
        ),
    )

    assert privacy.read_window_inventory() is None


def test_read_window_inventory_rejects_invalid_display_bounds(monkeypatch) -> None:
    monkeypatch.setattr(
        privacy,
        "_read_window_list_helper",
        lambda: privacy.WindowListReadResult({
            "schema_version": 1,
            "windows": [],
            "displays": [
                {"id": 1, "left": 0, "top": 0, "width": 0, "height": 100, "is_primary": True}
            ],
        }, None),
    )

    assert privacy.read_window_inventory() is None


@pytest.mark.parametrize(
    ("inventory", "expected"),
    [
        (None, privacy.ProtectionFailureReason.INVENTORY_UNAVAILABLE),
        (
            privacy.WindowInventory(windows=(), displays=()),
            privacy.ProtectionFailureReason.EMPTY_DISPLAYS,
        ),
        (
            privacy.WindowInventory(windows=(), displays=(DISPLAY_1, replace(DISPLAY_1))),
            privacy.ProtectionFailureReason.INVALID_DISPLAY_INVENTORY,
        ),
        *[
            (
                privacy.WindowInventory(
                    windows=(),
                    displays=(replace(DISPLAY_1, id=display_id),),
                ),
                privacy.ProtectionFailureReason.INVALID_DISPLAY_INVENTORY,
            )
            for display_id in (True, 0, -1, 0x1_0000_0000)
        ],
        *[
            (
                privacy.WindowInventory(
                    windows=(),
                    displays=(
                        replace(
                            DISPLAY_1,
                            region=privacy.ScreenRegion(0, 0, width, 100),
                        ),
                    ),
                ),
                privacy.ProtectionFailureReason.INVALID_DISPLAY_INVENTORY,
            )
            for width in (math.nan, math.inf, -math.inf, 0, -1)
        ],
        (
            privacy.WindowInventory(
                windows=(
                    privacy.VisibleWindow("One", "one", "", privacy.ScreenRegion(0, 0, 10, 10), True),
                    privacy.VisibleWindow("Two", "two", "", privacy.ScreenRegion(10, 0, 10, 10), True),
                ),
                displays=(DISPLAY_1, DISPLAY_2),
            ),
            privacy.ProtectionFailureReason.MULTIPLE_ACTIVE_WINDOWS,
        ),
        (
            privacy.WindowInventory(windows=(), displays=(DISPLAY_1, DISPLAY_2)),
            None,
        ),
    ],
)
def test_inventory_structure_failure_reason(inventory, expected) -> None:
    assert privacy.inventory_structure_failure_reason(inventory) is expected


@pytest.mark.parametrize("display_id", [True, 0, -1, 0x1_0000_0000, 1.0, "1"])
def test_read_window_inventory_rejects_untyped_or_invalid_display_ids(monkeypatch, display_id) -> None:
    monkeypatch.setattr(
        privacy,
        "_read_window_list_helper",
        lambda: privacy.WindowListReadResult(
            {
                "schema_version": 1,
                "windows": [],
                "displays": [
                    {"id": display_id, "left": 0, "top": 0, "width": 100, "height": 100}
                ],
            },
            None,
        ),
    )

    result = privacy.read_window_inventory_result()

    assert result.inventory is None
    assert result.failure_reason is privacy.ProtectionFailureReason.INVALID_DISPLAY_INVENTORY


def test_read_window_inventory_rejects_duplicate_display_ids(monkeypatch) -> None:
    monkeypatch.setattr(
        privacy,
        "_read_window_list_helper",
        lambda: privacy.WindowListReadResult(
            {
                "schema_version": 1,
                "windows": [],
                "displays": [
                    {"id": 1, "left": 0, "top": 0, "width": 100, "height": 100},
                    {"id": 1, "left": 100, "top": 0, "width": 100, "height": 100},
                ],
            },
            None,
        ),
    )

    result = privacy.read_window_inventory_result()

    assert result.inventory is None
    assert result.failure_reason is privacy.ProtectionFailureReason.INVALID_DISPLAY_INVENTORY


def test_inventory_read_result_uses_fixed_reason_codes_without_private_markers(
    monkeypatch, caplog
) -> None:
    marker = "private-inventory-marker"
    cases = [
        (
            "inventory_unavailable",
            lambda: monkeypatch.setattr(privacy, "_resolve_window_list_path", lambda: None),
        ),
        (
            "helper_exit",
            lambda: (
                monkeypatch.setattr(privacy, "_resolve_window_list_path", lambda: Path("/helper")),
                monkeypatch.setattr(
                    privacy.subprocess,
                    "run",
                    lambda *_args, **_kwargs: SimpleNamespace(returncode=7, stdout="", stderr=marker),
                ),
            ),
        ),
        (
            "helper_parse",
            lambda: (
                monkeypatch.setattr(privacy, "_resolve_window_list_path", lambda: Path("/helper")),
                monkeypatch.setattr(
                    privacy.subprocess,
                    "run",
                    lambda *_args, **_kwargs: SimpleNamespace(
                        returncode=0, stdout="{\"windows\": [\"" + marker, stderr=""
                    ),
                ),
            ),
        ),
    ]

    for expected, configure in cases:
        monkeypatch.undo()
        configure()
        with caplog.at_level(logging.WARNING, logger="openchronicle.capture"):
            result = privacy.read_window_inventory_result()
        assert result.inventory is None
        assert result.failure_reason is not None
        assert result.failure_reason.value == expected
        assert marker not in caplog.text


def test_inventory_read_result_classifies_empty_displays_and_multiple_active(monkeypatch) -> None:
    monkeypatch.setattr(
        privacy,
        "_read_window_list_helper",
        lambda: privacy.WindowListReadResult(
            {"schema_version": 1, "windows": [], "displays": []},
            None,
        ),
    )
    assert privacy.read_window_inventory_result().failure_reason.value == "empty_displays"

    monkeypatch.setattr(
        privacy,
        "_read_window_list_helper",
        lambda: privacy.WindowListReadResult({
            "schema_version": 1,
            "windows": [
                {"left": 0, "top": 0, "width": 10, "height": 10, "is_active": True},
                {"left": 10, "top": 0, "width": 10, "height": 10, "is_active": True},
            ],
            "displays": [{"id": 1, "left": 0, "top": 0, "width": 100, "height": 100}],
        }, None),
    )
    assert privacy.read_window_inventory_result().failure_reason.value == "multiple_active_windows"


def test_inventory_accepts_multiple_typed_active_candidates(monkeypatch) -> None:
    monkeypatch.setattr(
        privacy,
        "_read_window_list_helper",
        lambda: privacy.WindowListReadResult(
            {
                "schema_version": 1,
                "windows": [
                    {
                        "left": 0,
                        "top": 0,
                        "width": 10,
                        "height": 10,
                        "is_active_candidate": True,
                    },
                    {
                        "left": 20,
                        "top": 0,
                        "width": 10,
                        "height": 10,
                        "is_active_candidate": True,
                    },
                ],
                "displays": [
                    {"id": 1, "left": 0, "top": 0, "width": 100, "height": 100}
                ],
            },
            None,
        ),
    )

    result = privacy.read_window_inventory_result()

    assert result.failure_reason is None
    assert result.inventory is not None
    assert [window.is_active_candidate for window in result.inventory.windows] == [True, True]


def test_inventory_rejects_non_boolean_uncertainty_flags(monkeypatch) -> None:
    base_window = {
        "left": 0,
        "top": 0,
        "width": 10,
        "height": 10,
    }
    display = {"id": 1, "left": 0, "top": 0, "width": 100, "height": 100}

    for field in ("is_active", "title_available", "is_active_candidate"):
        monkeypatch.setattr(
            privacy,
            "_read_window_list_helper",
            lambda field=field: privacy.WindowListReadResult(
                {
                    "schema_version": 1,
                    "windows": [{**base_window, field: "false"}],
                    "displays": [display],
                },
                None,
            ),
        )

        result = privacy.read_window_inventory_result()

        assert result.inventory is None
        assert result.failure_reason is privacy.ProtectionFailureReason.HELPER_PARSE


def test_read_window_inventory_does_not_log_parser_private_marker(monkeypatch, caplog) -> None:
    marker = "private-marker"
    monkeypatch.setattr(
        privacy,
        "_read_window_list_helper",
        lambda: privacy.WindowListReadResult({
            "schema_version": 1,
            "windows": [],
            "displays": [
                {
                    "id": 1,
                    "left": marker,
                    "top": 0,
                    "width": 100,
                    "height": 100,
                    "is_primary": True,
                }
            ],
        }, None),
    )

    with caplog.at_level(logging.WARNING, logger="openchronicle.capture"):
        assert privacy.read_window_inventory() is None

    assert marker not in caplog.text


def test_window_list_helper_failure_does_not_log_stderr_metadata(monkeypatch, caplog) -> None:
    monkeypatch.setattr(privacy, "_resolve_window_list_path", lambda: Path("/helper"))
    monkeypatch.setattr(
        privacy.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stderr="InPrivate - secret-title"),
    )

    with caplog.at_level(logging.WARNING, logger="openchronicle.capture"):
        assert privacy._run_window_list_helper() is None

    assert "secret-title" not in caplog.text


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
