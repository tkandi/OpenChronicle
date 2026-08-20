"""Pure privacy-protection snapshot construction."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from ..config import CaptureConfig
from . import privacy
from .privacy import DisplayInfo, ScreenRegion, VisibleWindow, WindowInventory

SNAPSHOT_FRESH_SECONDS = 0.25


class ProtectionState(StrEnum):
    INACTIVE = "inactive"
    PROTECTED = "protected"
    PAUSED = "paused"
    FAILED = "failed"


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

    @property
    def protected_regions(self) -> list[ScreenRegion]:
        return [
            display.region
            for display in self.displays
            if display.id in self.protected_display_ids
        ]

    @property
    def ax_blocked(self) -> bool:
        if self.state is ProtectionState.FAILED:
            return True
        if not self.protected_display_ids:
            return False
        return (
            self.active_display_id is None
            or self.active_display_id in self.protected_display_ids
        )


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
) -> ProtectionSnapshot:
    displays = inventory.displays if inventory is not None else ()
    all_ids = frozenset(display.id for display in displays)
    active_windows = tuple(window for window in inventory.windows if window.is_active) if inventory else ()
    active_window = active_windows[0] if len(active_windows) == 1 else None
    active_display_id = _display_for_active_window(active_window, displays)
    sensitive_regions = (
        [
            window.region
            for window in inventory.windows
            if privacy.visible_window_denylist_reason(cfg, window) is not None
        ]
        if inventory is not None
        else []
    )
    has_unmapped_sensitive_window = any(
        not any(_regions_intersect(display.region, region) for display in displays)
        for region in sensitive_regions
    )
    mapping_failed = (
        len(active_windows) > 1
        or (active_window is not None and active_display_id is None)
        or has_unmapped_sensitive_window
    )

    if paused:
        state = ProtectionState.PAUSED
        protected_ids = all_ids
    elif inventory is None or not _displays_are_usable(displays) or mapping_failed:
        state = ProtectionState.FAILED
        protected_ids = frozenset()
    else:
        matched_ids = frozenset(
            display.id
            for display in displays
            if any(_regions_intersect(display.region, region) for region in sensitive_regions)
        )
        state = ProtectionState.PROTECTED if matched_ids else ProtectionState.INACTIVE
        protected_ids = all_ids if matched_ids and cfg.screenshot_monitor == "all" else matched_ids

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
    )
