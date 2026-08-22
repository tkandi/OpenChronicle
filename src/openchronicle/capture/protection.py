"""Pure privacy-protection snapshot construction."""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from enum import StrEnum

from ..config import CaptureConfig
from . import privacy
from .privacy import (
    DisplayInfo,
    ProtectionFailureReason,
    ScreenRegion,
    VisibleWindow,
    WindowInventory,
)
from .protection_reason import (
    DisplayProtectionReasons,
    ProtectionReason,
    ProtectionReasonCode,
)

SNAPSHOT_FRESH_SECONDS = 0.25
_DIRECT_WINDOW_RULE_CODES = frozenset(
    {
        ProtectionReasonCode.APP_RULE,
        ProtectionReasonCode.BUNDLE_RULE,
        ProtectionReasonCode.WINDOW_TITLE_RULE,
    }
)


class ProtectionState(StrEnum):
    INACTIVE = "inactive"
    PROTECTED = "protected"
    PAUSED = "paused"
    FAILED = "failed"


def failure_requires_fail_closed(
    cfg: CaptureConfig,
    snapshot: ProtectionSnapshot,
) -> bool:
    return snapshot.state is ProtectionState.FAILED and (
        snapshot.diagnostics_guard_active
        or snapshot.diagnostics_guard_invalid
        or cfg.screenshot_privacy_fail_closed
        or snapshot.failure_reason is ProtectionFailureReason.PAUSE_STATE_UNAVAILABLE
    )


@dataclass(frozen=True)
class ProtectionSnapshot:
    generation: int
    state: ProtectionState
    capture_mode: str
    indicator_style: str
    displays: tuple[DisplayInfo, ...]
    protected_display_ids: frozenset[int]
    active_display_id: int | None
    created_monotonic: float
    fresh_until: float
    failure_reason: ProtectionFailureReason | None = None
    active_candidate_display_ids: frozenset[int] = frozenset()
    reason_display: str = "hybrid"
    reason_detail: str = "exact"
    reason_trigger: str = "hover"
    display_reasons: DisplayProtectionReasons = field(default_factory=DisplayProtectionReasons)
    diagnostics_guard_invalid: bool = False
    diagnostics_guard_active: bool = False
    protected_window_ids: frozenset[int] = frozenset()
    protected_window_regions: tuple[ScreenRegion, ...] = ()
    window_filterable: bool = False

    @property
    def protected_regions(self) -> list[ScreenRegion]:
        return [
            display.region
            for display in self.displays
            if display.id in self.protected_display_ids
        ]

    @property
    def ax_blocked(self) -> bool:
        if self.state in (ProtectionState.PAUSED, ProtectionState.FAILED):
            return True
        if not self.protected_display_ids:
            return False
        if self.active_display_id is not None:
            return self.active_display_id in self.protected_display_ids
        return bool(self.active_candidate_display_ids & self.protected_display_ids)

    def reasons_for_display(self, display_id: int | None) -> tuple[ProtectionReason, ...]:
        return self.display_reasons.for_display(display_id)


def _intersection_area(left: ScreenRegion, right: ScreenRegion) -> float:
    width = max(
        0.0,
        min(left.left + left.width, right.left + right.width) - max(left.left, right.left),
    )
    height = max(
        0.0,
        min(left.top + left.height, right.top + right.height) - max(left.top, right.top),
    )
    return width * height


def _regions_intersect(left: ScreenRegion, right: ScreenRegion) -> bool:
    return _intersection_area(left, right) > 0


def _display_is_usable(display: DisplayInfo) -> bool:
    region = display.region
    return (
        all(math.isfinite(value) for value in (region.left, region.top, region.width, region.height))
        and region.width > 0
        and region.height > 0
    )


def _displays_are_usable(displays: tuple[DisplayInfo, ...]) -> bool:
    return bool(displays) and len({display.id for display in displays}) == len(displays) and all(
        _display_is_usable(display) for display in displays
    )


def _usable_window_id(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and 0 < value <= 0xFFFFFFFF:
        return value
    return None


def _display_for_active_window(
    window: VisibleWindow | None,
    displays: tuple[DisplayInfo, ...],
) -> int | None:
    if window is None:
        return None
    areas = [(_intersection_area(window.region, display.region), display.id) for display in displays]
    area, display_id = max(areas, default=(0.0, -1))
    return display_id if area > 0 else None


def build_protection_snapshot(
    cfg: CaptureConfig,
    inventory: WindowInventory | None,
    *,
    paused: bool,
    generation: int,
    now: float,
    failure_reason: ProtectionFailureReason | None = None,
    pause_reason: ProtectionReason | None = None,
    diagnostic_display_ids: frozenset[int] = frozenset(),
    diagnostics_guard_invalid: bool = False,
) -> ProtectionSnapshot:
    displays = inventory.displays if inventory is not None else ()
    all_ids = frozenset(display.id for display in displays)
    requested_diagnostic_ids = frozenset(diagnostic_display_ids)
    diagnostics_guard_active = diagnostics_guard_invalid or bool(requested_diagnostic_ids)
    diagnostics_guard_unmapped = (
        bool(requested_diagnostic_ids)
        and inventory is not None
        and _displays_are_usable(displays)
        and not requested_diagnostic_ids <= all_ids
    )
    effective_guard_invalid = diagnostics_guard_invalid or diagnostics_guard_unmapped
    active_windows = tuple(window for window in inventory.windows if window.is_active) if inventory else ()
    active_window = active_windows[0] if len(active_windows) == 1 else None
    active_display_id = _display_for_active_window(active_window, displays)
    active_candidates = (
        tuple(window for window in inventory.windows if window.is_active_candidate)
        if inventory is not None and active_window is None
        else ()
    )
    active_candidate_display_ids = frozenset(
        display.id
        for display in displays
        if any(_regions_intersect(display.region, window.region) for window in active_candidates)
    )
    has_unmapped_guarded_active_candidate = diagnostics_guard_active and any(
        not any(
            _display_is_usable(display)
            and _regions_intersect(display.region, window.region)
            for display in displays
        )
        for window in active_candidates
    )
    sensitive_windows = (
        [
            (window, matches)
            for window in inventory.windows
            if (matches := privacy.visible_window_rule_matches(cfg, window))
        ]
        if inventory is not None
        else []
    )
    has_unmapped_sensitive_window = any(
        any(match.kind is not ProtectionReasonCode.WINDOW_TITLE_UNKNOWN for match in matches)
        and not any(_regions_intersect(display.region, window.region) for display in displays)
        for window, matches in sensitive_windows
    )
    derived_failure_reason = failure_reason
    if derived_failure_reason is None:
        if inventory is None:
            derived_failure_reason = ProtectionFailureReason.INVENTORY_UNAVAILABLE
        elif not displays:
            derived_failure_reason = ProtectionFailureReason.EMPTY_DISPLAYS
        elif not _displays_are_usable(displays):
            derived_failure_reason = ProtectionFailureReason.INVALID_DISPLAY_INVENTORY
        elif len(active_windows) > 1:
            derived_failure_reason = ProtectionFailureReason.MULTIPLE_ACTIVE_WINDOWS
        elif (
            active_window is not None and active_display_id is None
        ) or has_unmapped_guarded_active_candidate:
            derived_failure_reason = ProtectionFailureReason.ACTIVE_WINDOW_UNMAPPED
        elif has_unmapped_sensitive_window:
            derived_failure_reason = ProtectionFailureReason.SENSITIVE_WINDOW_UNMAPPED

    if effective_guard_invalid:
        state = ProtectionState.FAILED
        protected_ids = all_ids
    elif paused:
        state = ProtectionState.PAUSED
        protected_ids = all_ids
    elif derived_failure_reason is not None:
        state = ProtectionState.FAILED
        protected_ids = frozenset()
    else:
        matched_ids = frozenset(
            display.id
            for window, _matches in sensitive_windows
            for display in displays
            if _regions_intersect(display.region, window.region)
        ) | (requested_diagnostic_ids & all_ids)
        state = ProtectionState.PROTECTED if matched_ids else ProtectionState.INACTIVE
        protected_ids = all_ids if matched_ids and cfg.screenshot_monitor == "all" else matched_ids

    direct_reason_display_ids: set[int] = set()
    reasons: list[ProtectionReason] = []
    for window, matches in sensitive_windows:
        matched_display_ids = tuple(
            display.id
            for display in displays
            if _regions_intersect(display.region, window.region)
        )
        for display_id in matched_display_ids:
            direct_reason_display_ids.add(display_id)
            reasons.extend(
                ProtectionReason(
                    code=match.kind,
                    display_id=display_id,
                    app_name=match.app_name,
                    bundle_id=match.bundle_id,
                    window_title=match.window_title,
                    rule=match.rule,
                )
                for match in matches
            )

    diagnostic_ids = requested_diagnostic_ids & all_ids
    reasons.extend(
        ProtectionReason(ProtectionReasonCode.DIAGNOSTICS_REVEAL, display_id)
        for display_id in sorted(diagnostic_ids)
    )
    direct_reason_display_ids.update(diagnostic_ids)
    if (
        state is ProtectionState.PROTECTED
        and cfg.screenshot_monitor == "all"
        and direct_reason_display_ids
    ):
        source_display_id = min(direct_reason_display_ids)
        reasons.extend(
            ProtectionReason(
                ProtectionReasonCode.MODE_ALL_INHERITED,
                display.id,
                source_display_id=source_display_id,
            )
            for display in displays
            if display.id not in direct_reason_display_ids
        )
    if effective_guard_invalid:
        reasons.append(
            ProtectionReason(
                ProtectionReasonCode.DIAGNOSTICS_GUARD_INVALID,
                display_id=None,
            )
        )
    if paused:
        reasons.append(pause_reason or ProtectionReason(ProtectionReasonCode.MANUAL_PAUSE, None))
    elif derived_failure_reason is not None and not effective_guard_invalid:
        reasons.append(
            ProtectionReason(ProtectionReasonCode(derived_failure_reason.value), display_id=None)
        )

    direct_window_matches = tuple(
        (window, matches)
        for window, matches in sensitive_windows
        if any(match.kind in _DIRECT_WINDOW_RULE_CODES for match in matches)
    )
    protected_window_regions = tuple(window.region for window, _matches in direct_window_matches)
    window_ids = tuple(window.window_id for window, _matches in direct_window_matches)
    protected_window_ids = frozenset(
        window_id
        for window_id in window_ids
        if _usable_window_id(window_id) is not None
    )
    inventory_window_id_counts = Counter(
        window_id
        for window in (inventory.windows if inventory is not None else ())
        if (window_id := _usable_window_id(window.window_id)) is not None
    )
    window_filterable = (
        state is ProtectionState.PROTECTED
        and bool(direct_window_matches)
        and not diagnostics_guard_active
        and len(protected_window_ids) == len(window_ids)
        and all(inventory_window_id_counts[window_id] == 1 for window_id in protected_window_ids)
    )

    return ProtectionSnapshot(
        generation=generation,
        state=state,
        capture_mode=cfg.screenshot_monitor,
        indicator_style=cfg.privacy_indicator_style,
        displays=displays,
        protected_display_ids=protected_ids,
        active_display_id=active_display_id,
        created_monotonic=now,
        fresh_until=now + SNAPSHOT_FRESH_SECONDS,
        failure_reason=None if paused else derived_failure_reason,
        active_candidate_display_ids=active_candidate_display_ids,
        reason_display=cfg.privacy_reason_display,
        reason_detail=cfg.privacy_reason_detail,
        reason_trigger=cfg.privacy_reason_trigger,
        display_reasons=DisplayProtectionReasons.from_reasons(reasons),
        diagnostics_guard_invalid=effective_guard_invalid,
        diagnostics_guard_active=diagnostics_guard_active,
        protected_window_ids=protected_window_ids,
        protected_window_regions=protected_window_regions,
        window_filterable=window_filterable,
    )
