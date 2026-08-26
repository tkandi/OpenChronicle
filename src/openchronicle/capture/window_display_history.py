"""In-memory fallback display mappings for briefly unmapped visible windows."""

from __future__ import annotations

from dataclasses import dataclass, replace

from .privacy import (
    DisplayInfo,
    ScreenRegion,
    VisibleWindow,
    WindowInventory,
    inventory_structure_failure_reason,
)

WINDOW_DISPLAY_HISTORY_ABSENCE_SECONDS: float = 5.0


class WindowDisplayHistoryError(RuntimeError):
    """Raised when supplied monotonic timestamps move backwards."""


@dataclass(frozen=True)
class _WindowIdentity:
    window_id: int
    owner_key: str


@dataclass(frozen=True)
class _HistoryEntry:
    display_ids: frozenset[int]
    last_seen_monotonic: float
    absence_observed: bool = False


class WindowDisplayHistory:
    """Resolve unmapped windows from short-lived, process-local display history."""

    def __init__(self) -> None:
        self._entries: dict[_WindowIdentity, _HistoryEntry] = {}
        self._previous_now: float | None = None

    def reset(self) -> None:
        self._entries.clear()
        self._previous_now = None

    def resolve(self, inventory: WindowInventory, *, now: float) -> WindowInventory:
        if self._previous_now is not None and now < self._previous_now:
            raise WindowDisplayHistoryError("monotonic clock moved backwards")

        self._previous_now = now
        if inventory_structure_failure_reason(inventory) is not None:
            return replace(
                inventory,
                windows=tuple(
                    replace(window, fallback_display_ids=frozenset())
                    for window in inventory.windows
                ),
            )
        active_display_ids = {display.id for display in inventory.displays}
        duplicate_ids = _duplicate_window_ids(inventory.windows)
        observed_owners = _unique_observed_owners(inventory.windows, duplicate_ids)
        self._entries = {
            identity: _HistoryEntry(
                entry.display_ids & active_display_ids,
                entry.last_seen_monotonic,
                entry.absence_observed,
            )
            for identity, entry in self._entries.items()
            if identity.window_id not in duplicate_ids
            and (
                identity.window_id not in observed_owners
                or observed_owners[identity.window_id] == identity.owner_key
            )
            and (entry.display_ids & active_display_ids)
            and not (
                entry.absence_observed
                and now - entry.last_seen_monotonic >= WINDOW_DISPLAY_HISTORY_ABSENCE_SECONDS
            )
        }

        present_identities = {
            identity
            for window in inventory.windows
            if (identity := _window_identity(window)) is not None
            and identity.window_id not in duplicate_ids
        }
        resolved_windows: list[VisibleWindow] = []
        for window in inventory.windows:
            identity = _window_identity(window)
            if identity is None or identity.window_id in duplicate_ids:
                resolved_windows.append(replace(window, fallback_display_ids=frozenset()))
                continue

            actual_display_ids = _intersecting_display_ids(window.region, inventory.displays)
            if actual_display_ids:
                self._entries[identity] = _HistoryEntry(actual_display_ids, now)
                fallback_display_ids = frozenset()
            else:
                entry = self._entries.get(identity)
                fallback_display_ids = entry.display_ids if entry is not None else frozenset()
                if entry is not None:
                    self._entries[identity] = replace(
                        entry,
                        last_seen_monotonic=now,
                        absence_observed=False,
                    )

            resolved_windows.append(
                replace(window, fallback_display_ids=fallback_display_ids)
            )

        self._entries = {
            identity: (
                entry
                if identity in present_identities
                else replace(entry, absence_observed=True)
            )
            for identity, entry in self._entries.items()
        }
        return replace(inventory, windows=tuple(resolved_windows))


def _window_identity(window: VisibleWindow) -> _WindowIdentity | None:
    window_id = _valid_window_id(window.window_id)
    if window_id is None:
        return None
    owner_key = _owner_key(window)
    if owner_key is None:
        return None
    return _WindowIdentity(window_id, owner_key)


def _owner_key(window: VisibleWindow) -> str | None:
    owner_key = window.bundle_id.strip().casefold() or window.app_name.strip().casefold()
    return owner_key or None


def _valid_window_id(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and 0 < value <= 0xFFFFFFFF:
        return value
    return None


def _duplicate_window_ids(windows: tuple[VisibleWindow, ...]) -> set[int]:
    seen: set[int] = set()
    duplicates: set[int] = set()
    for window in windows:
        window_id = _valid_window_id(window.window_id)
        if window_id is None:
            continue
        if window_id in seen:
            duplicates.add(window_id)
        seen.add(window_id)
    return duplicates


def _unique_observed_owners(
    windows: tuple[VisibleWindow, ...],
    duplicate_ids: set[int],
) -> dict[int, str | None]:
    return {
        window_id: _owner_key(window)
        for window in windows
        if (window_id := _valid_window_id(window.window_id)) is not None
        and window_id not in duplicate_ids
    }


def _intersecting_display_ids(
    region: ScreenRegion,
    displays: tuple[DisplayInfo, ...],
) -> frozenset[int]:
    return frozenset(
        display.id
        for display in displays
        if _intersection_area(region, display.region) > 0
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
