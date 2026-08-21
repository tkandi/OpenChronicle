import pytest

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
    failure_requires_fail_closed,
)
from openchronicle.capture.protection_reason import ProtectionReasonCode
from openchronicle.config import CaptureConfig

LEFT = DisplayInfo(1, ScreenRegion(0, 0, 100, 100), True)
RIGHT = DisplayInfo(2, ScreenRegion(100, 0, 100, 100), False)


@pytest.mark.parametrize(
    ("reason", "configured_fail_closed", "expected"),
    [
        (ProtectionFailureReason.PAUSE_STATE_UNAVAILABLE, False, True),
        (ProtectionFailureReason.PAUSE_STATE_UNAVAILABLE, True, True),
        (ProtectionFailureReason.INVENTORY_UNAVAILABLE, False, False),
        (ProtectionFailureReason.INVENTORY_UNAVAILABLE, True, True),
    ],
)
def test_failure_policy_distinguishes_pause_state_from_inventory(
    reason: ProtectionFailureReason,
    configured_fail_closed: bool,
    expected: bool,
) -> None:
    cfg = CaptureConfig(screenshot_privacy_fail_closed=configured_fail_closed)
    snapshot = build_protection_snapshot(
        cfg,
        None,
        paused=False,
        generation=90,
        now=20.0,
        failure_reason=reason,
    )

    assert snapshot.state is ProtectionState.FAILED
    assert failure_requires_fail_closed(cfg, snapshot) is expected


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


def test_all_marks_every_display_and_active_candidate_blocks_ax() -> None:
    cfg = CaptureConfig(
        screenshot_monitor="all",
        deny_window_title_patterns=["Private"],
    )
    inventory = WindowInventory(
        windows=(
            VisibleWindow("Edge", "edge", "Private", ScreenRegion(110, 0, 80, 90), False),
            VisibleWindow(
                "Cursor",
                "cursor",
                "main.py",
                ScreenRegion(0, 0, 80, 90),
                is_active_candidate=True,
            ),
        ),
        displays=(LEFT, RIGHT),
    )

    snapshot = build_protection_snapshot(cfg, inventory, paused=False, generation=8, now=11.0)

    assert snapshot.state is ProtectionState.PROTECTED
    assert snapshot.protected_display_ids == frozenset({1, 2})
    assert snapshot.active_display_id is None
    assert snapshot.active_candidate_display_ids == frozenset({1})
    assert snapshot.ax_blocked is True


def test_all_mode_records_direct_and_inherited_display_reasons() -> None:
    snapshot = build_protection_snapshot(
        CaptureConfig(
            screenshot_monitor="all",
            deny_window_title_patterns=["InPrivate"],
        ),
        WindowInventory(
            windows=(VisibleWindow("Edge", "edge", "InPrivate", RIGHT.region),),
            displays=(LEFT, RIGHT),
        ),
        paused=False,
        generation=40,
        now=1.0,
    )

    assert [reason.code for reason in snapshot.reasons_for_display(2)] == [
        ProtectionReasonCode.WINDOW_TITLE_RULE,
    ]
    inherited = snapshot.reasons_for_display(1)
    assert inherited[0].code is ProtectionReasonCode.MODE_ALL_INHERITED
    assert inherited[0].source_display_id == 2


def test_diagnostic_display_reason_is_composed_with_direct_rule() -> None:
    snapshot = build_protection_snapshot(
        CaptureConfig(
            screenshot_monitor="separate",
            deny_window_title_patterns=["InPrivate"],
        ),
        WindowInventory(
            windows=(VisibleWindow("Edge", "edge", "InPrivate", RIGHT.region),),
            displays=(LEFT, RIGHT),
        ),
        paused=False,
        generation=41,
        now=2.0,
        diagnostic_display_ids=frozenset({2}),
    )

    assert snapshot.protected_display_ids == frozenset({2})
    assert [reason.code for reason in snapshot.reasons_for_display(2)] == [
        ProtectionReasonCode.DIAGNOSTICS_REVEAL,
        ProtectionReasonCode.WINDOW_TITLE_RULE,
    ]


def test_invalid_diagnostics_guard_forces_global_fail_closed_protection() -> None:
    cfg = CaptureConfig(
        screenshot_monitor="separate",
        screenshot_privacy_fail_closed=False,
    )
    snapshot = build_protection_snapshot(
        cfg,
        WindowInventory(windows=(), displays=(LEFT, RIGHT)),
        paused=False,
        generation=42,
        now=2.0,
        diagnostics_guard_invalid=True,
    )

    assert snapshot.state is ProtectionState.FAILED
    assert snapshot.protected_display_ids == frozenset({1, 2})
    assert snapshot.diagnostics_guard_invalid is True
    assert failure_requires_fail_closed(cfg, snapshot) is True
    assert [reason.code for reason in snapshot.reasons_for_display(1)] == [
        ProtectionReasonCode.DIAGNOSTICS_GUARD_INVALID
    ]


def test_paused_snapshot_blocks_ax_without_inventory() -> None:
    snapshot = build_protection_snapshot(
        CaptureConfig(),
        None,
        paused=True,
        generation=9,
        now=12.0,
    )

    assert snapshot.state is ProtectionState.PAUSED
    assert snapshot.displays == ()
    assert snapshot.protected_display_ids == frozenset()
    assert snapshot.ax_blocked is True


@pytest.mark.parametrize(
    ("mode", "expected_ids"),
    [
        ("separate", frozenset({2})),
        ("primary", frozenset({2})),
        ("all", frozenset({1, 2})),
    ],
)
def test_unknown_title_protects_only_the_mode_mapped_displays(
    mode: str, expected_ids: frozenset[int]
) -> None:
    cfg = CaptureConfig(
        screenshot_monitor=mode,
        deny_window_title_patterns=["InPrivate"],
    )
    inventory = WindowInventory(
        windows=(
            VisibleWindow(
                "Browser",
                "browser",
                "",
                ScreenRegion(110, 0, 80, 90),
                title_available=False,
            ),
        ),
        displays=(LEFT, RIGHT),
    )

    snapshot = build_protection_snapshot(
        cfg,
        inventory,
        paused=False,
        generation=10,
        now=13.0,
    )

    assert snapshot.state is ProtectionState.PROTECTED
    assert snapshot.protected_display_ids == expected_ids
    assert snapshot.failure_reason is None


def test_empty_title_patterns_do_not_protect_an_unknown_title() -> None:
    snapshot = build_protection_snapshot(
        CaptureConfig(
            screenshot_monitor="separate",
            deny_window_title_patterns=[""],
        ),
        WindowInventory(
            windows=(
                VisibleWindow(
                    "Browser",
                    "browser",
                    "",
                    RIGHT.region,
                    title_available=False,
                ),
            ),
            displays=(LEFT, RIGHT),
        ),
        paused=False,
        generation=42,
        now=3.0,
    )

    assert snapshot.state is ProtectionState.INACTIVE
    assert snapshot.protected_display_ids == frozenset()
    assert snapshot.reasons_for_display(2) == ()


def test_unmapped_unknown_title_does_not_fail_the_complete_inventory() -> None:
    inventory = WindowInventory(
        windows=(
            VisibleWindow(
                "Unsupported",
                "unsupported",
                "",
                ScreenRegion(500, 0, 80, 90),
                title_available=False,
            ),
        ),
        displays=(LEFT, RIGHT),
    )

    snapshot = build_protection_snapshot(
        CaptureConfig(deny_window_title_patterns=["InPrivate"]),
        inventory,
        paused=False,
        generation=11,
        now=14.0,
    )

    assert snapshot.state is ProtectionState.INACTIVE
    assert snapshot.protected_display_ids == frozenset()
    assert snapshot.failure_reason is None


def test_exact_app_match_still_protects_when_title_is_unknown() -> None:
    inventory = WindowInventory(
        windows=(
            VisibleWindow(
                "Passwords",
                "com.passwords",
                "",
                ScreenRegion(0, 0, 80, 90),
                title_available=False,
            ),
        ),
        displays=(LEFT, RIGHT),
    )

    snapshot = build_protection_snapshot(
        CaptureConfig(deny_app_names=["Passwords"]),
        inventory,
        paused=False,
        generation=11,
        now=14.0,
    )

    assert snapshot.state is ProtectionState.PROTECTED
    assert snapshot.protected_display_ids == frozenset({1})


@pytest.mark.parametrize(
    ("mode", "candidate_left", "expected_blocked"),
    [
        ("separate", True, False),
        ("separate", False, True),
        ("primary", True, False),
        ("primary", False, True),
        ("all", True, True),
        ("all", False, True),
    ],
)
def test_uncertain_active_identity_blocks_only_candidate_protected_display(
    mode: str, candidate_left: bool, expected_blocked: bool
) -> None:
    candidate_region = ScreenRegion(0, 0, 80, 90) if candidate_left else ScreenRegion(110, 0, 80, 90)
    inventory = WindowInventory(
        windows=(
            VisibleWindow("Edge", "edge", "InPrivate", ScreenRegion(110, 0, 80, 90)),
            VisibleWindow(
                "Cursor",
                "cursor",
                "main.py",
                candidate_region,
                is_active_candidate=True,
            ),
        ),
        displays=(LEFT, RIGHT),
    )

    snapshot = build_protection_snapshot(
        CaptureConfig(
            screenshot_monitor=mode,
            deny_window_title_patterns=["InPrivate"],
        ),
        inventory,
        paused=False,
        generation=12,
        now=15.0,
    )

    assert snapshot.state is ProtectionState.PROTECTED
    assert snapshot.active_display_id is None
    assert snapshot.active_candidate_display_ids == frozenset({1 if candidate_left else 2})
    assert snapshot.ax_blocked is expected_blocked


def test_active_candidate_without_privacy_display_does_not_block_ax() -> None:
    inventory = WindowInventory(
        windows=(
            VisibleWindow(
                "Cursor",
                "cursor",
                "main.py",
                ScreenRegion(0, 0, 80, 90),
                is_active_candidate=True,
            ),
        ),
        displays=(LEFT, RIGHT),
    )

    snapshot = build_protection_snapshot(
        CaptureConfig(deny_window_title_patterns=["InPrivate"]),
        inventory,
        paused=False,
        generation=13,
        now=16.0,
    )

    assert snapshot.state is ProtectionState.INACTIVE
    assert snapshot.active_candidate_display_ids == frozenset({1})
    assert snapshot.ax_blocked is False


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
