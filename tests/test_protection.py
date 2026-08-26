import pytest

from openchronicle.capture.privacy import (
    DisplayInfo,
    ScreenRegion,
    VisibleWindow,
    WindowInventory,
)
from openchronicle.capture.protection import (
    ProtectionFailureReason,
    ProtectionSnapshot,
    ProtectionState,
    build_protection_snapshot,
    failure_requires_fail_closed,
)
from openchronicle.capture.protection_reason import (
    DisplayProtectionReasons,
    ProtectionReasonCode,
)
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


@pytest.mark.parametrize("privacy_mode", ["mask-window", "exclude-window"])
def test_window_filtered_modes_force_inventory_failures_closed(
    privacy_mode: str,
) -> None:
    cfg = CaptureConfig(
        screenshot_privacy_mode=privacy_mode,
        screenshot_privacy_fail_closed=False,
    )
    snapshot = build_protection_snapshot(
        cfg,
        None,
        paused=False,
        generation=91,
        now=21.0,
        failure_reason=ProtectionFailureReason.INVENTORY_UNAVAILABLE,
    )

    assert snapshot.state is ProtectionState.FAILED
    assert failure_requires_fail_closed(cfg, snapshot) is True


def test_presentation_state_failure_is_always_fail_closed() -> None:
    cfg = CaptureConfig(screenshot_privacy_fail_closed=False)
    snapshot = build_protection_snapshot(
        cfg,
        None,
        paused=False,
        generation=92,
        now=22.0,
        failure_reason=ProtectionFailureReason.PRESENTATION_STATE_INVALID,
    )
    assert snapshot.state is ProtectionState.FAILED
    assert failure_requires_fail_closed(cfg, snapshot) is True


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


def test_snapshot_carries_indicator_placement() -> None:
    cfg = CaptureConfig(privacy_indicator_placement="bottom-left-inset")
    inventory = WindowInventory(windows=(), displays=(LEFT, RIGHT))
    snapshot = build_protection_snapshot(
        cfg,
        inventory,
        paused=False,
        generation=7,
        now=1.0,
    )

    assert snapshot.indicator_placement == "bottom-left-inset"


def test_protection_snapshot_preserves_pre_indicator_placement_positional_signature() -> None:
    display_reasons = DisplayProtectionReasons()
    protected_region = ScreenRegion(1, 2, 3, 4)
    snapshot = ProtectionSnapshot(
        7,
        ProtectionState.FAILED,
        "separate",
        "border",
        (LEFT,),
        frozenset({1}),
        1,
        10.0,
        10.25,
        ProtectionFailureReason.INVENTORY_UNAVAILABLE,
        frozenset({2}),
        "overlay",
        "tiered",
        "click",
        display_reasons,
        True,
        True,
        frozenset({73}),
        (protected_region,),
        True,
    )

    assert (
        snapshot.generation,
        snapshot.state,
        snapshot.capture_mode,
        snapshot.indicator_style,
        snapshot.displays,
        snapshot.protected_display_ids,
        snapshot.active_display_id,
        snapshot.created_monotonic,
        snapshot.fresh_until,
        snapshot.failure_reason,
        snapshot.active_candidate_display_ids,
        snapshot.reason_display,
        snapshot.reason_detail,
        snapshot.reason_trigger,
        snapshot.display_reasons,
        snapshot.diagnostics_guard_invalid,
        snapshot.diagnostics_guard_active,
        snapshot.protected_window_ids,
        snapshot.protected_window_regions,
        snapshot.window_filterable,
    ) == (
        7,
        ProtectionState.FAILED,
        "separate",
        "border",
        (LEFT,),
        frozenset({1}),
        1,
        10.0,
        10.25,
        ProtectionFailureReason.INVENTORY_UNAVAILABLE,
        frozenset({2}),
        "overlay",
        "tiered",
        "click",
        display_reasons,
        True,
        True,
        frozenset({73}),
        (protected_region,),
        True,
    )
    assert snapshot.indicator_placement == "bottom-left-flush"


def test_window_filtering_collects_rule_matched_ids_and_original_regions() -> None:
    first_region = ScreenRegion(-220, 10, 300, 200)
    second_region = ScreenRegion(110, 0, 80, 90)
    inventory = WindowInventory(
        windows=(
            VisibleWindow(
                "Private Browser",
                "com.example.private",
                "Private",
                first_region,
                window_id=73,
            ),
            VisibleWindow(
                "Passwords",
                "com.apple.Passwords",
                "Passwords",
                second_region,
                window_id=74,
            ),
        ),
        displays=(LEFT, RIGHT),
    )

    snapshot = build_protection_snapshot(
        CaptureConfig(deny_app_names=["Private Browser", "Passwords"]),
        inventory,
        paused=False,
        generation=8,
        now=11.0,
    )

    assert snapshot.state is ProtectionState.PROTECTED
    assert snapshot.protected_window_ids == frozenset({73, 74})
    assert snapshot.protected_window_regions == (first_region, second_region)
    assert snapshot.window_filterable is True


@pytest.mark.parametrize("window_ids", [(None, 74), (73, 73)])
def test_missing_or_duplicate_ids_cannot_authorize_window_filtering(
    window_ids: tuple[int | None, int],
) -> None:
    first_region = ScreenRegion(0, 0, 80, 90)
    second_region = ScreenRegion(110, 0, 80, 90)
    inventory = WindowInventory(
        windows=(
            VisibleWindow("Private Browser", "private", "Private", first_region, window_id=window_ids[0]),
            VisibleWindow("Passwords", "passwords", "Passwords", second_region, window_id=window_ids[1]),
        ),
        displays=(LEFT, RIGHT),
    )

    snapshot = build_protection_snapshot(
        CaptureConfig(deny_app_names=["Private Browser", "Passwords"]),
        inventory,
        paused=False,
        generation=9,
        now=12.0,
    )

    assert snapshot.protected_window_regions == (first_region, second_region)
    assert snapshot.window_filterable is False


def test_id_shared_with_non_sensitive_window_cannot_authorize_window_filtering() -> None:
    protected_region = ScreenRegion(0, 0, 80, 90)
    inventory = WindowInventory(
        windows=(
            VisibleWindow(
                "Private Browser",
                "private",
                "Private",
                protected_region,
                window_id=73,
            ),
            VisibleWindow("Cursor", "cursor", "main.py", ScreenRegion(110, 0, 80, 90), window_id=73),
        ),
        displays=(LEFT, RIGHT),
    )

    snapshot = build_protection_snapshot(
        CaptureConfig(deny_app_names=["Private Browser"]),
        inventory,
        paused=False,
        generation=10,
        now=13.0,
    )

    assert snapshot.protected_window_ids == frozenset({73})
    assert snapshot.protected_window_regions == (protected_region,)
    assert snapshot.window_filterable is False


def test_unknown_title_rule_protects_display_without_authorizing_window_filtering() -> None:
    region = ScreenRegion(0, 0, 80, 90)
    inventory = WindowInventory(
        windows=(
            VisibleWindow(
                "Private Browser",
                "private",
                "",
                region,
                title_available=False,
                window_id=73,
            ),
        ),
        displays=(LEFT, RIGHT),
    )

    snapshot = build_protection_snapshot(
        CaptureConfig(deny_window_title_patterns=["Private"]),
        inventory,
        paused=False,
        generation=11,
        now=14.0,
    )

    assert snapshot.state is ProtectionState.PROTECTED
    assert snapshot.protected_display_ids == frozenset({1})
    assert snapshot.protected_window_ids == frozenset()
    assert snapshot.protected_window_regions == ()
    assert snapshot.window_filterable is False


def test_explicit_app_rule_can_authorize_window_with_unknown_title() -> None:
    region = ScreenRegion(0, 0, 80, 90)
    inventory = WindowInventory(
        windows=(
            VisibleWindow(
                "Private Browser",
                "private",
                "",
                region,
                title_available=False,
                window_id=73,
            ),
        ),
        displays=(LEFT, RIGHT),
    )

    snapshot = build_protection_snapshot(
        CaptureConfig(
            deny_app_names=["Private Browser"],
            deny_window_title_patterns=["Private"],
        ),
        inventory,
        paused=False,
        generation=12,
        now=15.0,
    )

    assert snapshot.protected_window_ids == frozenset({73})
    assert snapshot.protected_window_regions == (region,)
    assert snapshot.window_filterable is True


def test_unknown_only_sensitive_window_revokes_mixed_inventory_filterability() -> None:
    explicit_region = ScreenRegion(0, 0, 80, 90)
    unknown_region = ScreenRegion(110, 0, 80, 90)
    inventory = WindowInventory(
        windows=(
            VisibleWindow(
                "Private Browser",
                "private",
                "Private",
                explicit_region,
                window_id=73,
            ),
            VisibleWindow(
                "Browser",
                "browser",
                "",
                unknown_region,
                title_available=False,
                window_id=74,
            ),
        ),
        displays=(LEFT, RIGHT),
    )

    snapshot = build_protection_snapshot(
        CaptureConfig(
            deny_app_names=["Private Browser"],
            deny_window_title_patterns=["Private"],
        ),
        inventory,
        paused=False,
        generation=12,
        now=15.0,
    )

    assert snapshot.protected_display_ids == frozenset({1, 2})
    assert snapshot.protected_window_ids == frozenset({73})
    assert snapshot.protected_window_regions == (explicit_region,)
    assert snapshot.window_filterable is False


@pytest.mark.parametrize("window_id", [0, -1, True, 0x1_0000_0000])
def test_invalid_direct_window_ids_cannot_authorize_window_filtering(window_id: object) -> None:
    region = ScreenRegion(0, 0, 80, 90)
    inventory = WindowInventory(
        windows=(
            VisibleWindow("Private Browser", "private", "Private", region, window_id=window_id),  # type: ignore[arg-type]
        ),
        displays=(LEFT, RIGHT),
    )

    snapshot = build_protection_snapshot(
        CaptureConfig(deny_app_names=["Private Browser"]),
        inventory,
        paused=False,
        generation=13,
        now=16.0,
    )

    assert snapshot.protected_window_ids == frozenset()
    assert snapshot.protected_window_regions == (region,)
    assert snapshot.window_filterable is False


@pytest.mark.parametrize(
    ("paused", "failure_reason", "diagnostic_display_ids"),
    [
        (True, None, frozenset()),
        (False, ProtectionFailureReason.HELPER_EXIT, frozenset()),
        (False, None, frozenset({1})),
    ],
)
def test_pause_failure_and_diagnostics_do_not_authorize_window_filtering(
    paused: bool,
    failure_reason: ProtectionFailureReason | None,
    diagnostic_display_ids: frozenset[int],
) -> None:
    inventory = WindowInventory(
        windows=(
            VisibleWindow(
                "Private Browser",
                "private",
                "Private",
                ScreenRegion(0, 0, 80, 90),
                window_id=73,
            ),
        ),
        displays=(LEFT, RIGHT),
    )

    snapshot = build_protection_snapshot(
        CaptureConfig(deny_app_names=["Private Browser"]),
        inventory,
        paused=paused,
        generation=10,
        now=13.0,
        failure_reason=failure_reason,
        diagnostic_display_ids=diagnostic_display_ids,
    )

    assert snapshot.window_filterable is False


def test_all_mode_inheritance_does_not_revoke_window_filterability() -> None:
    region = ScreenRegion(0, 0, 80, 90)
    inventory = WindowInventory(
        windows=(
            VisibleWindow("Private Browser", "private", "Private", region, window_id=73),
        ),
        displays=(LEFT, RIGHT),
    )

    snapshot = build_protection_snapshot(
        CaptureConfig(screenshot_monitor="all", deny_app_names=["Private Browser"]),
        inventory,
        paused=False,
        generation=11,
        now=14.0,
    )

    assert snapshot.protected_display_ids == frozenset({1, 2})
    assert snapshot.protected_window_ids == frozenset({73})
    assert snapshot.protected_window_regions == (region,)
    assert snapshot.window_filterable is True


def test_alternate_title_rule_keeps_the_matching_title_in_exact_reason() -> None:
    cfg = CaptureConfig(
        screenshot_monitor="separate",
        deny_window_title_patterns=["InPrivate"],
    )
    inventory = WindowInventory(
        windows=(
            VisibleWindow(
                "Microsoft Edge",
                "com.microsoft.edgemac",
                "Google",
                RIGHT.region,
                alternate_title="Google - Microsoft Edge (InPrivate)",
            ),
        ),
        displays=(LEFT, RIGHT),
    )

    snapshot = build_protection_snapshot(cfg, inventory, paused=False, generation=8, now=11.0)

    assert snapshot.state is ProtectionState.PROTECTED
    assert snapshot.protected_display_ids == frozenset({2})
    assert [
        (reason.code, reason.rule, reason.window_title)
        for reason in snapshot.reasons_for_display(2)
    ] == [
        (
            ProtectionReasonCode.WINDOW_TITLE_RULE,
            "InPrivate",
            "Google - Microsoft Edge (InPrivate)",
        ),
    ]


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


@pytest.mark.parametrize(
    "reason",
    [
        ProtectionFailureReason.INVENTORY_UNAVAILABLE,
        ProtectionFailureReason.HELPER_EXIT,
    ],
)
def test_active_diagnostics_guard_keeps_inventory_failure_globally_closed(
    reason: ProtectionFailureReason,
) -> None:
    cfg = CaptureConfig(screenshot_privacy_fail_closed=False)
    snapshot = build_protection_snapshot(
        cfg,
        None,
        paused=False,
        generation=43,
        now=3.0,
        failure_reason=reason,
        diagnostic_display_ids=frozenset({2}),
    )

    assert snapshot.state is ProtectionState.FAILED
    assert snapshot.diagnostics_guard_active is True
    assert snapshot.diagnostics_guard_invalid is False
    assert snapshot.protected_display_ids == frozenset()
    assert snapshot.protected_regions == []
    assert snapshot.ax_blocked is True
    assert failure_requires_fail_closed(cfg, snapshot) is True
    assert [item.code for item in snapshot.reasons_for_display(None)] == [
        ProtectionReasonCode(reason.value)
    ]


def test_unmapped_active_diagnostics_guard_fails_closed_on_all_known_displays() -> None:
    cfg = CaptureConfig(
        screenshot_monitor="separate",
        screenshot_privacy_fail_closed=False,
    )
    snapshot = build_protection_snapshot(
        cfg,
        WindowInventory(windows=(), displays=(LEFT, RIGHT)),
        paused=False,
        generation=44,
        now=4.0,
        diagnostic_display_ids=frozenset({99}),
    )

    assert snapshot.state is ProtectionState.FAILED
    assert snapshot.diagnostics_guard_active is True
    assert snapshot.diagnostics_guard_invalid is True
    assert snapshot.protected_display_ids == frozenset({1, 2})
    assert snapshot.protected_regions == [LEFT.region, RIGHT.region]
    assert failure_requires_fail_closed(cfg, snapshot) is True
    assert [item.code for item in snapshot.reasons_for_display(None)] == [
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


def test_unmapped_active_candidate_without_guard_preserves_ordinary_behavior() -> None:
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
        displays=(LEFT, RIGHT),
    )

    snapshot = build_protection_snapshot(
        CaptureConfig(screenshot_privacy_fail_closed=False),
        inventory,
        paused=False,
        generation=14,
        now=17.0,
    )

    assert snapshot.state is ProtectionState.INACTIVE
    assert snapshot.failure_reason is None
    assert snapshot.active_candidate_display_ids == frozenset()
    assert snapshot.ax_blocked is False


def test_guarded_unmapped_active_candidate_fails_closed_with_fixed_reason() -> None:
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
        displays=(LEFT, RIGHT),
    )

    snapshot = build_protection_snapshot(
        cfg,
        inventory,
        paused=False,
        generation=15,
        now=18.0,
        diagnostic_display_ids=frozenset({2}),
    )

    assert snapshot.state is ProtectionState.FAILED
    assert snapshot.failure_reason is ProtectionFailureReason.ACTIVE_WINDOW_UNMAPPED
    assert snapshot.diagnostics_guard_active is True
    assert snapshot.diagnostics_guard_invalid is False
    assert snapshot.active_candidate_display_ids == frozenset()
    assert snapshot.ax_blocked is True
    assert failure_requires_fail_closed(cfg, snapshot) is True
    assert [reason.code for reason in snapshot.reasons_for_display(None)] == [
        ProtectionReasonCode.ACTIVE_WINDOW_UNMAPPED
    ]


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


@pytest.mark.parametrize(
    ("inventory", "expected_reason"),
    [
        (
            WindowInventory(windows=(), displays=()),
            ProtectionFailureReason.EMPTY_DISPLAYS,
        ),
        (
            WindowInventory(
                windows=(),
                displays=(DisplayInfo(1, ScreenRegion(0, 0, float("nan"), 100), True),),
            ),
            ProtectionFailureReason.INVALID_DISPLAY_INVENTORY,
        ),
        (
            WindowInventory(
                windows=(
                    VisibleWindow("One", "one", "", ScreenRegion(0, 0, 10, 10), True),
                    VisibleWindow("Two", "two", "", ScreenRegion(10, 0, 10, 10), True),
                ),
                displays=(LEFT, RIGHT),
            ),
            ProtectionFailureReason.MULTIPLE_ACTIVE_WINDOWS,
        ),
    ],
)
def test_direct_snapshot_resolver_rejects_invalid_inventory_structure(
    inventory: WindowInventory,
    expected_reason: ProtectionFailureReason,
) -> None:
    snapshot = build_protection_snapshot(
        CaptureConfig(), inventory, paused=False, generation=10, now=13.0
    )

    assert snapshot.state is ProtectionState.FAILED
    assert snapshot.failure_reason is expected_reason


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


def test_sensitive_window_display_history_protects_its_last_display() -> None:
    historical_region = ScreenRegion(300, 0, 80, 90)
    inventory = WindowInventory(
        windows=(
            VisibleWindow(
                "Edge",
                "edge",
                "InPrivate",
                historical_region,
                window_id=73,
                fallback_display_ids=frozenset({1}),
            ),
        ),
        displays=(LEFT, RIGHT),
    )

    snapshot = build_protection_snapshot(
        CaptureConfig(deny_window_title_patterns=["InPrivate"]),
        inventory,
        paused=False,
        generation=16,
        now=19.0,
    )

    assert snapshot.state is ProtectionState.PROTECTED
    assert snapshot.failure_reason is None
    assert snapshot.protected_display_ids == frozenset({1})
    assert snapshot.display_mapping_fallback_active is True
    assert snapshot.window_filterable is False
    assert [reason.display_id for reason in snapshot.display_reasons.reasons] == [1]


def test_unmapped_sensitive_window_without_display_history_still_fails() -> None:
    inventory = WindowInventory(
        windows=(
            VisibleWindow(
                "Edge",
                "edge",
                "InPrivate",
                ScreenRegion(300, 0, 80, 90),
                window_id=73,
            ),
        ),
        displays=(LEFT, RIGHT),
    )

    snapshot = build_protection_snapshot(
        CaptureConfig(deny_window_title_patterns=["InPrivate"]),
        inventory,
        paused=False,
        generation=17,
        now=20.0,
    )

    assert snapshot.state is ProtectionState.FAILED
    assert snapshot.failure_reason is ProtectionFailureReason.SENSITIVE_WINDOW_UNMAPPED


def test_actual_sensitive_geometry_takes_priority_over_stale_display_history() -> None:
    inventory = WindowInventory(
        windows=(
            VisibleWindow(
                "Edge",
                "edge",
                "InPrivate",
                ScreenRegion(110, 0, 80, 90),
                window_id=73,
                fallback_display_ids=frozenset({1}),
            ),
        ),
        displays=(LEFT, RIGHT),
    )

    snapshot = build_protection_snapshot(
        CaptureConfig(deny_window_title_patterns=["InPrivate"]),
        inventory,
        paused=False,
        generation=18,
        now=21.0,
    )

    assert snapshot.state is ProtectionState.PROTECTED
    assert snapshot.protected_display_ids == frozenset({2})
    assert snapshot.display_mapping_fallback_active is False
    assert snapshot.window_filterable is True
    assert [reason.display_id for reason in snapshot.display_reasons.reasons] == [2]


def test_all_mode_expands_sensitive_display_history_protection() -> None:
    inventory = WindowInventory(
        windows=(
            VisibleWindow(
                "Edge",
                "edge",
                "InPrivate",
                ScreenRegion(300, 0, 80, 90),
                window_id=73,
                fallback_display_ids=frozenset({1}),
            ),
        ),
        displays=(LEFT, RIGHT),
    )

    snapshot = build_protection_snapshot(
        CaptureConfig(screenshot_monitor="all", deny_window_title_patterns=["InPrivate"]),
        inventory,
        paused=False,
        generation=19,
        now=22.0,
    )

    assert snapshot.state is ProtectionState.PROTECTED
    assert snapshot.protected_display_ids == frozenset({1, 2})
    assert snapshot.display_mapping_fallback_active is True
    assert snapshot.window_filterable is False


def test_active_window_display_history_resolves_a_single_last_display() -> None:
    inventory = WindowInventory(
        windows=(
            VisibleWindow(
                "Cursor",
                "cursor",
                "main.py",
                ScreenRegion(300, 0, 80, 90),
                is_active=True,
                fallback_display_ids=frozenset({1}),
            ),
        ),
        displays=(LEFT, RIGHT),
    )

    snapshot = build_protection_snapshot(
        CaptureConfig(), inventory, paused=False, generation=20, now=23.0
    )

    assert snapshot.state is ProtectionState.INACTIVE
    assert snapshot.active_display_id == 1
    assert snapshot.active_candidate_display_ids == frozenset()


def test_active_candidate_display_history_keeps_multiple_last_displays_conservative() -> None:
    inventory = WindowInventory(
        windows=(
            VisibleWindow("Edge", "edge", "InPrivate", ScreenRegion(110, 0, 80, 90)),
            VisibleWindow(
                "Cursor",
                "cursor",
                "main.py",
                ScreenRegion(300, 0, 80, 90),
                is_active_candidate=True,
                fallback_display_ids=frozenset({1, 2}),
            ),
        ),
        displays=(LEFT, RIGHT),
    )

    snapshot = build_protection_snapshot(
        CaptureConfig(deny_window_title_patterns=["InPrivate"]),
        inventory,
        paused=False,
        generation=21,
        now=24.0,
    )

    assert snapshot.state is ProtectionState.PROTECTED
    assert snapshot.active_display_id is None
    assert snapshot.active_candidate_display_ids == frozenset({1, 2})
    assert snapshot.ax_blocked is True


def test_invalid_diagnostics_guard_stays_failed_with_sensitive_display_history() -> None:
    historical_region = ScreenRegion(300, 0, 80, 90)
    inventory = WindowInventory(
        windows=(
            VisibleWindow(
                "Edge",
                "edge",
                "InPrivate",
                historical_region,
                window_id=73,
                fallback_display_ids=frozenset({1}),
            ),
        ),
        displays=(LEFT, RIGHT),
    )

    snapshot = build_protection_snapshot(
        CaptureConfig(deny_window_title_patterns=["InPrivate"]),
        inventory,
        paused=False,
        generation=22,
        now=25.0,
        diagnostics_guard_invalid=True,
    )

    assert snapshot.state is ProtectionState.FAILED
    assert snapshot.protected_display_ids == frozenset({1, 2})
    assert snapshot.diagnostics_guard_invalid is True
    assert snapshot.display_mapping_fallback_active is True
    assert snapshot.window_filterable is False


def test_title_unknown_display_history_marks_fallback_protection() -> None:
    inventory = WindowInventory(
        windows=(
            VisibleWindow(
                "Edge",
                "edge",
                "",
                ScreenRegion(300, 0, 80, 90),
                title_available=False,
                fallback_display_ids=frozenset({1}),
            ),
        ),
        displays=(LEFT, RIGHT),
    )

    snapshot = build_protection_snapshot(
        CaptureConfig(deny_window_title_patterns=["InPrivate"]),
        inventory,
        paused=False,
        generation=23,
        now=26.0,
    )

    assert snapshot.state is ProtectionState.PROTECTED
    assert snapshot.protected_display_ids == frozenset({1})
    assert snapshot.display_mapping_fallback_active is True
    assert snapshot.window_filterable is False
    assert [reason.code for reason in snapshot.reasons_for_display(1)] == [
        ProtectionReasonCode.WINDOW_TITLE_UNKNOWN
    ]


def test_active_window_multi_display_history_remains_a_conservative_candidate() -> None:
    inventory = WindowInventory(
        windows=(
            VisibleWindow("Edge", "edge", "InPrivate", ScreenRegion(110, 0, 80, 90)),
            VisibleWindow(
                "Cursor",
                "cursor",
                "main.py",
                ScreenRegion(300, 0, 80, 90),
                is_active=True,
                fallback_display_ids=frozenset({1, 2}),
            ),
        ),
        displays=(LEFT, RIGHT),
    )

    snapshot = build_protection_snapshot(
        CaptureConfig(deny_window_title_patterns=["InPrivate"]),
        inventory,
        paused=False,
        generation=24,
        now=27.0,
    )

    assert snapshot.state is ProtectionState.PROTECTED
    assert snapshot.protected_display_ids == frozenset({2})
    assert snapshot.active_display_id is None
    assert snapshot.active_candidate_display_ids == frozenset({1, 2})
    assert snapshot.ax_blocked is True


def test_mixed_actual_and_history_direct_windows_disable_filtering() -> None:
    actual_region = ScreenRegion(110, 0, 80, 90)
    historical_region = ScreenRegion(300, 0, 80, 90)
    inventory = WindowInventory(
        windows=(
            VisibleWindow("Edge", "edge", "InPrivate", actual_region, window_id=73),
            VisibleWindow(
                "Passwords",
                "com.apple.Passwords",
                "Passwords",
                historical_region,
                window_id=74,
                fallback_display_ids=frozenset({1}),
            ),
        ),
        displays=(LEFT, RIGHT),
    )

    snapshot = build_protection_snapshot(
        CaptureConfig(deny_app_names=["Edge", "Passwords"]),
        inventory,
        paused=False,
        generation=25,
        now=28.0,
    )

    assert snapshot.state is ProtectionState.PROTECTED
    assert snapshot.protected_display_ids == frozenset({1, 2})
    assert snapshot.protected_window_ids == frozenset({73, 74})
    assert snapshot.display_mapping_fallback_active is True
    assert snapshot.window_filterable is False
