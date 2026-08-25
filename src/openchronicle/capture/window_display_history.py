"""In-memory fallback display mappings for briefly unmapped visible windows."""

from __future__ import annotations

from dataclasses import dataclass, replace

from .privacy import DisplayInfo, ScreenRegion, VisibleWindow, WindowInventory

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
        active_display_ids = {display.id for display in inventory.displays}
        duplicate_ids = _duplicate_window_ids(inventory.windows)
        self._entries = {
            identity: _HistoryEntry(entry.display_ids & active_display_ids, entry.last_seen_monotonic)
            for identity, entry in self._entries.items()
            if identity.window_id not in duplicate_ids
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
                if entry is not None and fallback_display_ids:
                    self._entries[identity] = replace(entry, last_seen_monotonic=now)

            resolved_windows.append(
                replace(window, fallback_display_ids=fallback_display_ids)
            )

        self._entries = {
            identity: entry
            for identity, entry in self._entries.items()
            if identity in present_identities
            or now - entry.last_seen_monotonic < WINDOW_DISPLAY_HISTORY_ABSENCE_SECONDS
        }
        return replace(inventory, windows=tuple(resolved_windows))


def _window_identity(window: VisibleWindow) -> _WindowIdentity | None:
    window_id = _valid_window_id(window.window_id)
    if window_id is None:
        return None
    owner_key = window.bundle_id.strip().casefold() or window.app_name.strip().casefold()
    return _WindowIdentity(window_id, owner_key)


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
