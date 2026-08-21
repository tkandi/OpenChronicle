from openchronicle.capture.privacy import (
    DisplayInfo,
    ScreenRegion,
    VisibleWindow,
    WindowInventory,
)
from openchronicle.capture.protection import (
    ProtectionFailureReason,
    ProtectionState,
    build_protection_snapshot,
)
from openchronicle.config import CaptureConfig

LEFT = DisplayInfo(1, ScreenRegion(0, 0, 100, 100), True)
RIGHT = DisplayInfo(2, ScreenRegion(100, 0, 100, 100), False)


def test_separate_marks_only_sensitive_display_and_blocks_ax_there() -> None:
    cfg = CaptureConfig(
        screenshot_monitor="separate",
        deny_window_title_patterns=["InPrivate"],
    )
    inventory = WindowInventory(
        windows=(
            VisibleWindow(
                "Microsoft Edge", "com.microsoft.edgemac", "InPrivate",
                ScreenRegion(110, 0, 80, 90), False,
            ),
            VisibleWindow(
                "Cursor", "com.cursor.Cursor", "main.py",
                ScreenRegion(115, 5, 70, 80), True,
            ),
        ),
        displays=(LEFT, RIGHT),
    )

    snapshot = build_protection_snapshot(cfg, inventory, paused=False, generation=7, now=10.0)

    assert snapshot.state is ProtectionState.PROTECTED
    assert snapshot.protected_display_ids == frozenset({2})
    assert snapshot.active_display_id == 2
    assert snapshot.ax_blocked is True


def test_all_marks_every_display_and_unknown_active_display_blocks_ax() -> None:
    cfg = CaptureConfig(
        screenshot_monitor="all",
        deny_window_title_patterns=["Private"],
    )
    inventory = WindowInventory(
        windows=(
            VisibleWindow("Edge", "edge", "Private", ScreenRegion(110, 0, 80, 90), False),
        ),
        displays=(LEFT, RIGHT),
    )

    snapshot = build_protection_snapshot(cfg, inventory, paused=False, generation=8, now=11.0)

    assert snapshot.state is ProtectionState.PROTECTED
    assert snapshot.protected_display_ids == frozenset({1, 2})
    assert snapshot.active_display_id is None
    assert snapshot.ax_blocked is True


def test_empty_displays_fail_closed_for_sensitive_active_window() -> None:
    cfg = CaptureConfig(deny_window_title_patterns=["InPrivate"])
    inventory = WindowInventory(
        windows=(
            VisibleWindow(
                "Edge", "edge", "InPrivate", ScreenRegion(0, 0, 80, 90), True,
            ),
        ),
        displays=(),
    )

    snapshot = build_protection_snapshot(cfg, inventory, paused=False, generation=9, now=12.0)

    assert snapshot.state is ProtectionState.FAILED
    assert snapshot.protected_display_ids == frozenset()
    assert snapshot.active_display_id is None
    assert snapshot.ax_blocked is True


def test_multiple_active_windows_fail_closed() -> None:
    inventory = WindowInventory(
        windows=(
            VisibleWindow("Edge", "edge", "first", ScreenRegion(0, 0, 80, 90), True),
            VisibleWindow("Cursor", "cursor", "second", ScreenRegion(110, 0, 80, 90), True),
        ),
        displays=(LEFT, RIGHT),
    )

    snapshot = build_protection_snapshot(CaptureConfig(), inventory, paused=False, generation=10, now=13.0)

    assert snapshot.state is ProtectionState.FAILED
    assert snapshot.ax_blocked is True


def test_snapshot_classifies_non_inventory_failure_reasons() -> None:
    duplicate_displays = WindowInventory(
        windows=(),
        displays=(LEFT, DisplayInfo(1, ScreenRegion(100, 0, 100, 100), False)),
    )
    active_unmapped = WindowInventory(
        windows=(VisibleWindow("App", "app", "private-active", ScreenRegion(500, 0, 10, 10), True),),
        displays=(LEFT,),
    )
    sensitive_unmapped = WindowInventory(
        windows=(VisibleWindow("App", "app", "InPrivate", ScreenRegion(500, 0, 10, 10), False),),
        displays=(LEFT,),
    )
    cfg = CaptureConfig(deny_window_title_patterns=["InPrivate"])

    assert build_protection_snapshot(
        cfg, duplicate_displays, paused=False, generation=11, now=14.0
    ).failure_reason is ProtectionFailureReason.INVALID_DISPLAY_INVENTORY
    assert build_protection_snapshot(
        cfg, active_unmapped, paused=False, generation=12, now=15.0
    ).failure_reason is ProtectionFailureReason.ACTIVE_WINDOW_UNMAPPED
    assert build_protection_snapshot(
        cfg, sensitive_unmapped, paused=False, generation=13, now=16.0
    ).failure_reason is ProtectionFailureReason.SENSITIVE_WINDOW_UNMAPPED
