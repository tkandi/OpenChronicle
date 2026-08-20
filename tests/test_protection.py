from openchronicle.capture.privacy import (
    DisplayInfo,
    ScreenRegion,
    VisibleWindow,
    WindowInventory,
)
from openchronicle.capture.protection import ProtectionState, build_protection_snapshot
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
