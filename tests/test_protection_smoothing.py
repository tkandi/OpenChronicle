from __future__ import annotations

from dataclasses import replace

import pytest

from openchronicle.capture.privacy import DisplayInfo, ScreenRegion
from openchronicle.capture.protection import ProtectionSnapshot, ProtectionState
from openchronicle.capture.protection_reason import (
    DisplayProtectionReasons,
    ProtectionReason,
    ProtectionReasonCode,
)
from openchronicle.capture.protection_smoothing import (
    ProtectionPresentationPhase,
    ProtectionPresentationSmoother,
    ProtectionSmoothingError,
)

DISPLAY = DisplayInfo(1, ScreenRegion(0, 0, 100, 100), True)
REASON = ProtectionReason(
    ProtectionReasonCode.WINDOW_TITLE_RULE,
    display_id=1,
    app_name="Edge",
    window_title="InPrivate",
)


def _snapshot(
    generation: int,
    state: ProtectionState,
    *,
    style: str = "pill",
    now: float = 10.0,
    protected_ids: frozenset[int] = frozenset({1}),
) -> ProtectionSnapshot:
    protected = state is ProtectionState.PROTECTED
    return ProtectionSnapshot(
        generation=generation,
        state=state,
        capture_mode="separate",
        indicator_style=style,
        displays=(DISPLAY,),
        protected_display_ids=protected_ids if protected else frozenset(),
        active_display_id=1,
        created_monotonic=now,
        fresh_until=now + 0.25,
        display_reasons=DisplayProtectionReasons.from_reasons([REASON]) if protected else (
            DisplayProtectionReasons()
        ),
        protected_window_ids=frozenset({41}) if protected else frozenset(),
        protected_window_regions=(ScreenRegion(0, 0, 50, 50),) if protected else (),
        window_filterable=protected,
    )


def test_short_protection_stays_quiet_then_promotes_at_800ms() -> None:
    smoother = ProtectionPresentationSmoother()
    first = smoother.resolve(_snapshot(1, ProtectionState.PROTECTED), now=10.0)
    before = smoother.resolve(_snapshot(2, ProtectionState.PROTECTED), now=10.799)
    promoted = smoother.resolve(_snapshot(3, ProtectionState.PROTECTED), now=10.8)

    assert first.phase is ProtectionPresentationPhase.TRANSIENT_PROTECTED
    assert first.snapshot.indicator_style == "quiet-shield"
    assert first.snapshot.display_reasons.reasons == (REASON,)
    assert first.snapshot.ax_blocked is True
    assert first.overlay_reasons_enabled is False
    assert first.next_deadline == pytest.approx(10.8)
    assert before.snapshot.indicator_style == "quiet-shield"
    assert promoted.phase is ProtectionPresentationPhase.SUSTAINED_PROTECTED
    assert promoted.snapshot.indicator_style == "pill"
    assert promoted.overlay_reasons_enabled is True
    assert promoted.next_deadline is None


def test_safe_clear_requires_second_sample_after_200ms() -> None:
    smoother = ProtectionPresentationSmoother()
    protected = smoother.resolve(_snapshot(1, ProtectionState.PROTECTED), now=10.0)
    first_safe = smoother.resolve(_snapshot(2, ProtectionState.INACTIVE), now=10.1)
    early_safe = smoother.resolve(_snapshot(3, ProtectionState.INACTIVE), now=10.299)
    confirmed_safe = smoother.resolve(_snapshot(4, ProtectionState.INACTIVE), now=10.3)

    assert protected.snapshot.state is ProtectionState.PROTECTED
    assert first_safe.phase is ProtectionPresentationPhase.CLEAR_PENDING
    assert first_safe.snapshot.state is ProtectionState.PROTECTED
    assert first_safe.snapshot.ax_blocked is True
    assert first_safe.next_deadline == pytest.approx(10.3)
    assert early_safe.snapshot.state is ProtectionState.PROTECTED
    assert confirmed_safe.phase is ProtectionPresentationPhase.INACTIVE
    assert confirmed_safe.snapshot.state is ProtectionState.INACTIVE


def test_protection_returning_during_clear_pending_keeps_original_episode() -> None:
    smoother = ProtectionPresentationSmoother()
    smoother.resolve(_snapshot(1, ProtectionState.PROTECTED), now=10.0)
    smoother.resolve(_snapshot(2, ProtectionState.INACTIVE), now=10.1)
    returned = smoother.resolve(_snapshot(3, ProtectionState.PROTECTED), now=10.2)
    promoted = smoother.resolve(_snapshot(4, ProtectionState.PROTECTED), now=10.8)

    assert returned.phase is ProtectionPresentationPhase.TRANSIENT_PROTECTED
    assert returned.next_deadline == pytest.approx(10.8)
    assert promoted.phase is ProtectionPresentationPhase.SUSTAINED_PROTECTED


def test_protected_data_changes_do_not_restart_episode() -> None:
    smoother = ProtectionPresentationSmoother()
    smoother.resolve(_snapshot(1, ProtectionState.PROTECTED), now=10.0)
    changed = replace(
        _snapshot(2, ProtectionState.PROTECTED),
        protected_display_ids=frozenset({1, 2}),
        protected_window_ids=frozenset({41, 42}),
    )
    result = smoother.resolve(changed, now=10.8)
    assert result.phase is ProtectionPresentationPhase.SUSTAINED_PROTECTED


@pytest.mark.parametrize("style", ["quiet-shield", "off"])
def test_quiet_and_off_promote_without_inventing_another_visual_style(style: str) -> None:
    smoother = ProtectionPresentationSmoother()
    first = smoother.resolve(_snapshot(1, ProtectionState.PROTECTED, style=style), now=10.0)
    promoted = smoother.resolve(_snapshot(2, ProtectionState.PROTECTED, style=style), now=10.8)
    expected_transient = "off" if style == "off" else "quiet-shield"
    assert first.snapshot.indicator_style == expected_transient
    assert promoted.snapshot.indicator_style == style
    assert promoted.phase is ProtectionPresentationPhase.SUSTAINED_PROTECTED
    assert promoted.snapshot.generation == 2


@pytest.mark.parametrize("state", [ProtectionState.PAUSED, ProtectionState.FAILED])
def test_pause_and_failure_bypass_smoothing(state: ProtectionState) -> None:
    smoother = ProtectionPresentationSmoother()
    result = smoother.resolve(_snapshot(1, state, style="banner"), now=10.0)
    assert result.phase is ProtectionPresentationPhase.BYPASS
    assert result.snapshot.indicator_style == "banner"
    assert result.overlay_reasons_enabled is True
    assert result.next_deadline is None


def test_transient_style_and_placement_reload_without_resetting_episode() -> None:
    smoother = ProtectionPresentationSmoother()
    smoother.resolve(_snapshot(1, ProtectionState.PROTECTED), now=10.0)
    changed = replace(
        _snapshot(2, ProtectionState.PROTECTED, style="border"),
        indicator_placement="bottom-right-work-area",
    )
    transient = smoother.resolve(changed, now=10.4)
    promoted = smoother.resolve(replace(changed, generation=3), now=10.8)
    assert transient.snapshot.indicator_style == "quiet-shield"
    assert transient.snapshot.indicator_placement == "bottom-right-work-area"
    assert transient.next_deadline == pytest.approx(10.8)
    assert promoted.snapshot.indicator_style == "border"


def test_clear_pending_holds_protected_data_with_fresh_generation_and_position() -> None:
    smoother = ProtectionPresentationSmoother()
    protected = smoother.resolve(_snapshot(1, ProtectionState.PROTECTED), now=10.0)
    safe = replace(
        _snapshot(2, ProtectionState.INACTIVE, now=10.1),
        indicator_placement="bottom-left-inset",
    )
    held = smoother.resolve(safe, now=10.1)
    assert held.snapshot.generation == 2
    assert held.snapshot.created_monotonic == 10.1
    assert held.snapshot.protected_display_ids == protected.snapshot.protected_display_ids
    assert held.snapshot.protected_window_ids == protected.snapshot.protected_window_ids
    assert held.snapshot.display_reasons == protected.snapshot.display_reasons
    assert held.snapshot.indicator_placement == "bottom-left-inset"


def test_reset_clears_deadlines_and_generation_memory() -> None:
    smoother = ProtectionPresentationSmoother()
    smoother.resolve(_snapshot(5, ProtectionState.PROTECTED), now=10.0)
    smoother.reset()
    restarted = smoother.resolve(_snapshot(1, ProtectionState.PROTECTED), now=20.0)
    assert restarted.phase is ProtectionPresentationPhase.TRANSIENT_PROTECTED
    assert restarted.next_deadline == pytest.approx(20.8)


def test_non_increasing_generation_is_rejected() -> None:
    smoother = ProtectionPresentationSmoother()
    smoother.resolve(_snapshot(5, ProtectionState.PROTECTED), now=10.0)
    with pytest.raises(ProtectionSmoothingError, match="strictly increase"):
        smoother.resolve(_snapshot(5, ProtectionState.PROTECTED), now=10.1)


@pytest.mark.parametrize(
    ("promotion_seconds", "safe_confirmation_seconds"),
    [(-0.001, 0.2), (0.8, -0.001)],
)
def test_negative_delays_are_rejected(
    promotion_seconds: float,
    safe_confirmation_seconds: float,
) -> None:
    with pytest.raises(ValueError, match="non-negative"):
        ProtectionPresentationSmoother(
            promotion_seconds=promotion_seconds,
            safe_confirmation_seconds=safe_confirmation_seconds,
        )


def test_impossible_held_state_is_rejected() -> None:
    smoother = ProtectionPresentationSmoother()
    smoother._episode_started_at = 10.0
    with pytest.raises(ProtectionSmoothingError, match="protected snapshot"):
        smoother.resolve(_snapshot(1, ProtectionState.INACTIVE), now=10.1)


@pytest.mark.parametrize(
    "orphan_state",
    ["clear-deadline", "held-protected", "clear-deadline-and-held-protected"],
)
def test_orphan_clear_state_is_rejected(orphan_state: str) -> None:
    smoother = ProtectionPresentationSmoother()
    if orphan_state in {"clear-deadline", "clear-deadline-and-held-protected"}:
        smoother._clear_deadline = 10.2
    if orphan_state in {"held-protected", "clear-deadline-and-held-protected"}:
        smoother._last_effective_protected = _snapshot(1, ProtectionState.PROTECTED)

    with pytest.raises(ProtectionSmoothingError, match="inconsistent"):
        smoother.resolve(_snapshot(2, ProtectionState.INACTIVE), now=10.1)
