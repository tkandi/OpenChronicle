from __future__ import annotations

import pytest

from openchronicle.capture.privacy import (
    DisplayInfo,
    ScreenRegion,
    VisibleWindow,
    WindowInventory,
)
from openchronicle.capture.window_display_history import (
    WindowDisplayHistory,
    WindowDisplayHistoryError,
)

DISPLAY_1 = DisplayInfo(1, ScreenRegion(0, 0, 100, 100), True)
DISPLAY_2 = DisplayInfo(2, ScreenRegion(100, 0, 100, 100), False)


def _window(
    window_id: int | None,
    region: ScreenRegion,
    *,
    app_name: str = "Edge",
    bundle_id: str = "com.microsoft.edgemac",
) -> VisibleWindow:
    return VisibleWindow(
        app_name,
        bundle_id,
        "InPrivate",
        region,
        window_id=window_id,
    )


def _inventory(
    *windows: VisibleWindow,
    displays: tuple[DisplayInfo, ...] = (DISPLAY_1, DISPLAY_2),
) -> WindowInventory:
    return WindowInventory(windows=windows, displays=displays)


def test_actual_mapping_populates_history_then_unmapped_uses_it() -> None:
    history = WindowDisplayHistory()

    mapped = history.resolve(
        _inventory(_window(41, ScreenRegion(10, 10, 50, 50))),
        now=10.0,
    )
    fallback = history.resolve(
        _inventory(_window(41, ScreenRegion(5000, 5000, 50, 50))),
        now=10.1,
    )

    assert mapped.windows[0].fallback_display_ids == frozenset()
    assert fallback.windows[0].fallback_display_ids == frozenset({1})


def test_continuously_present_unmapped_window_does_not_expire() -> None:
    history = WindowDisplayHistory()
    history.resolve(_inventory(_window(41, ScreenRegion(10, 10, 50, 50))), now=0.0)

    first = history.resolve(
        _inventory(_window(41, ScreenRegion(5000, 5000, 50, 50))),
        now=100.0,
    )
    later = history.resolve(
        _inventory(_window(41, ScreenRegion(5000, 5000, 50, 50))),
        now=10_000.0,
    )

    assert first.windows[0].fallback_display_ids == frozenset({1})
    assert later.windows[0].fallback_display_ids == frozenset({1})


def test_continuous_fallback_refreshes_before_a_later_brief_absence() -> None:
    history = WindowDisplayHistory()
    history.resolve(_inventory(_window(41, ScreenRegion(10, 10, 50, 50))), now=0.0)
    history.resolve(
        _inventory(_window(41, ScreenRegion(5000, 5000, 50, 50))),
        now=100.0,
    )
    history.resolve(_inventory(), now=104.999)

    result = history.resolve(
        _inventory(_window(41, ScreenRegion(5000, 5000, 50, 50))),
        now=104.999,
    )

    assert result.windows[0].fallback_display_ids == frozenset({1})


def test_absent_entry_is_reusable_before_five_seconds() -> None:
    history = WindowDisplayHistory()
    history.resolve(_inventory(_window(41, ScreenRegion(10, 10, 50, 50))), now=0.0)
    history.resolve(_inventory(), now=4.999)

    result = history.resolve(
        _inventory(_window(41, ScreenRegion(5000, 5000, 50, 50))),
        now=4.999,
    )

    assert result.windows[0].fallback_display_ids == frozenset({1})


def test_absent_entry_expires_at_exactly_five_seconds() -> None:
    history = WindowDisplayHistory()
    history.resolve(_inventory(_window(41, ScreenRegion(10, 10, 50, 50))), now=0.0)
    history.resolve(_inventory(), now=5.0)

    result = history.resolve(
        _inventory(_window(41, ScreenRegion(5000, 5000, 50, 50))),
        now=5.0,
    )

    assert result.windows[0].fallback_display_ids == frozenset()


def test_observed_absence_expires_before_later_fallback_lookup() -> None:
    history = WindowDisplayHistory()
    history.resolve(_inventory(_window(41, ScreenRegion(10, 10, 50, 50))), now=0.0)
    history.resolve(_inventory(), now=1.0)

    result = history.resolve(
        _inventory(_window(41, ScreenRegion(5000, 5000, 50, 50))),
        now=6.0,
    )

    assert result.windows[0].fallback_display_ids == frozenset()


def test_owner_mismatch_rejects_cached_mapping() -> None:
    history = WindowDisplayHistory()
    history.resolve(_inventory(_window(41, ScreenRegion(10, 10, 50, 50))), now=1.0)

    changed = history.resolve(
        _inventory(
            _window(
                41,
                ScreenRegion(5000, 5000, 50, 50),
                app_name="Other",
                bundle_id="com.example.other",
            )
        ),
        now=1.1,
    )

    assert changed.windows[0].fallback_display_ids == frozenset()


def test_owner_change_invalidates_the_old_identity_cache() -> None:
    history = WindowDisplayHistory()
    history.resolve(_inventory(_window(41, ScreenRegion(10, 10, 50, 50))), now=1.0)
    history.resolve(
        _inventory(
            _window(
                41,
                ScreenRegion(5000, 5000, 50, 50),
                app_name="Other",
                bundle_id="com.example.other",
            )
        ),
        now=1.1,
    )

    result = history.resolve(
        _inventory(_window(41, ScreenRegion(5000, 5000, 50, 50))),
        now=1.2,
    )

    assert result.windows[0].fallback_display_ids == frozenset()


@pytest.mark.parametrize("window_id", [None, 0, -1, True, 0x1_0000_0000])
def test_invalid_window_ids_never_seed_or_use_history(window_id: object) -> None:
    history = WindowDisplayHistory()
    history.resolve(
        _inventory(_window(window_id, ScreenRegion(10, 10, 50, 50))),
        now=1.0,
    )

    result = history.resolve(
        _inventory(_window(window_id, ScreenRegion(5000, 5000, 50, 50))),
        now=1.1,
    )

    assert result.windows[0].fallback_display_ids == frozenset()


def test_duplicate_window_id_invalidates_history_for_both_windows() -> None:
    history = WindowDisplayHistory()
    history.resolve(_inventory(_window(41, ScreenRegion(10, 10, 50, 50))), now=1.0)

    result = history.resolve(
        _inventory(
            _window(41, ScreenRegion(5000, 5000, 50, 50)),
            _window(41, ScreenRegion(6000, 6000, 50, 50)),
        ),
        now=1.1,
    )

    assert all(not window.fallback_display_ids for window in result.windows)
    later = history.resolve(
        _inventory(_window(41, ScreenRegion(5000, 5000, 50, 50))),
        now=1.2,
    )
    assert later.windows[0].fallback_display_ids == frozenset()


def test_actual_mapping_clears_input_fallback_and_replaces_history() -> None:
    history = WindowDisplayHistory()
    actual = VisibleWindow(
        "Edge",
        "com.microsoft.edgemac",
        "InPrivate",
        ScreenRegion(10, 10, 50, 50),
        window_id=41,
        fallback_display_ids=frozenset({2}),
    )

    mapped = history.resolve(_inventory(actual), now=1.0)
    fallback = history.resolve(
        _inventory(_window(41, ScreenRegion(5000, 5000, 50, 50))),
        now=1.1,
    )

    assert mapped.windows[0].fallback_display_ids == frozenset()
    assert fallback.windows[0].fallback_display_ids == frozenset({1})


def test_blank_owner_never_seeds_or_uses_history() -> None:
    history = WindowDisplayHistory()
    blank_owner = _window(
        41,
        ScreenRegion(10, 10, 50, 50),
        app_name=" ",
        bundle_id="",
    )
    history.resolve(_inventory(blank_owner), now=1.0)

    result = history.resolve(
        _inventory(
            _window(
                41,
                ScreenRegion(5000, 5000, 50, 50),
                app_name=" ",
                bundle_id="",
            )
        ),
        now=1.1,
    )

    assert result.windows[0].fallback_display_ids == frozenset()


def test_blank_owner_observation_clears_cached_id() -> None:
    history = WindowDisplayHistory()
    history.resolve(_inventory(_window(41, ScreenRegion(10, 10, 50, 50))), now=1.0)
    history.resolve(
        _inventory(
            _window(
                41,
                ScreenRegion(5000, 5000, 50, 50),
                app_name=" ",
                bundle_id="",
            )
        ),
        now=1.1,
    )

    result = history.resolve(
        _inventory(_window(41, ScreenRegion(5000, 5000, 50, 50))),
        now=1.2,
    )

    assert result.windows[0].fallback_display_ids == frozenset()


def test_removed_display_is_not_reused_from_history() -> None:
    history = WindowDisplayHistory()
    history.resolve(
        _inventory(_window(41, ScreenRegion(10, 10, 50, 50))),
        now=1.0,
    )

    result = history.resolve(
        _inventory(
            _window(41, ScreenRegion(5000, 5000, 50, 50)),
            displays=(DISPLAY_2,),
        ),
        now=1.1,
    )

    assert result.windows[0].fallback_display_ids == frozenset()
    assert history._entries == {}


def _invalid_inventory(kind: str, region: ScreenRegion) -> WindowInventory:
    if kind == "empty-displays":
        return _inventory(_window(41, region), displays=())
    if kind == "duplicate-displays":
        return _inventory(_window(41, region), displays=(DISPLAY_2, DISPLAY_2))
    if kind == "invalid-bounds":
        return _inventory(
            _window(41, region),
            displays=(DisplayInfo(1, ScreenRegion(0, 0, 0, 100), True),),
        )
    if kind == "multiple-active":
        return _inventory(
            VisibleWindow("Edge", "com.microsoft.edgemac", "InPrivate", region, True, window_id=41),
            VisibleWindow("Other", "other", "", ScreenRegion(10, 0, 10, 10), True),
        )
    raise AssertionError(f"unexpected invalid inventory: {kind}")


@pytest.mark.parametrize(
    "kind",
    ("empty-displays", "duplicate-displays", "invalid-bounds", "multiple-active"),
)
def test_invalid_inventory_cannot_seed_history(kind: str) -> None:
    history = WindowDisplayHistory()

    invalid = history.resolve(
        _invalid_inventory(kind, ScreenRegion(10, 10, 50, 50)),
        now=1.0,
    )
    fallback = history.resolve(
        _inventory(_window(41, ScreenRegion(5000, 5000, 50, 50))),
        now=1.1,
    )

    assert invalid.windows[0].fallback_display_ids == frozenset()
    assert fallback.windows[0].fallback_display_ids == frozenset()


def test_invalid_inventory_cannot_overwrite_or_clear_trusted_history() -> None:
    history = WindowDisplayHistory()
    history.resolve(_inventory(_window(41, ScreenRegion(10, 10, 50, 50))), now=1.0)

    invalid = history.resolve(
        _invalid_inventory("duplicate-displays", ScreenRegion(110, 10, 50, 50)),
        now=1.1,
    )
    fallback = history.resolve(
        _inventory(_window(41, ScreenRegion(5000, 5000, 50, 50))),
        now=1.2,
    )

    assert invalid.windows[0].fallback_display_ids == frozenset()
    assert fallback.windows[0].fallback_display_ids == frozenset({1})


def test_invalid_inventory_cannot_refresh_or_expire_absence_ttl() -> None:
    history = WindowDisplayHistory()
    history.resolve(_inventory(_window(41, ScreenRegion(10, 10, 50, 50))), now=0.0)
    history.resolve(_inventory(), now=1.0)

    history.resolve(
        _invalid_inventory("duplicate-displays", ScreenRegion(110, 10, 50, 50)),
        now=4.999,
    )
    expired = history.resolve(
        _inventory(_window(41, ScreenRegion(5000, 5000, 50, 50))),
        now=5.0,
    )

    assert expired.windows[0].fallback_display_ids == frozenset()


def test_invalid_inventory_cannot_mark_existing_history_absent_for_expiry() -> None:
    history = WindowDisplayHistory()
    history.resolve(_inventory(_window(41, ScreenRegion(10, 10, 50, 50))), now=0.0)

    history.resolve(_invalid_inventory("empty-displays", ScreenRegion(10, 10, 50, 50)), now=6.0)
    fallback = history.resolve(
        _inventory(_window(41, ScreenRegion(5000, 5000, 50, 50))),
        now=6.0,
    )

    assert fallback.windows[0].fallback_display_ids == frozenset({1})


def test_actual_display_mapping_overwrites_history_after_spanning_displays() -> None:
    history = WindowDisplayHistory()
    spanning = history.resolve(
        _inventory(_window(41, ScreenRegion(50, 10, 100, 50))),
        now=1.0,
    )
    moved = history.resolve(
        _inventory(_window(41, ScreenRegion(110, 10, 50, 50))),
        now=1.1,
    )
    fallback = history.resolve(
        _inventory(_window(41, ScreenRegion(5000, 5000, 50, 50))),
        now=1.2,
    )

    assert spanning.windows[0].fallback_display_ids == frozenset()
    assert moved.windows[0].fallback_display_ids == frozenset()
    assert fallback.windows[0].fallback_display_ids == frozenset({2})


def test_equal_monotonic_is_allowed_but_rollback_is_rejected() -> None:
    history = WindowDisplayHistory()

    history.resolve(_inventory(), now=10.0)
    history.resolve(_inventory(), now=10.0)

    with pytest.raises(WindowDisplayHistoryError, match="monotonic"):
        history.resolve(_inventory(), now=9.999)


def test_reset_removes_cached_mapping_and_clock_memory() -> None:
    history = WindowDisplayHistory()
    history.resolve(_inventory(_window(41, ScreenRegion(10, 10, 50, 50))), now=10.0)

    history.reset()
    result = history.resolve(
        _inventory(_window(41, ScreenRegion(5000, 5000, 50, 50))),
        now=1.0,
    )

    assert result.windows[0].fallback_display_ids == frozenset()
