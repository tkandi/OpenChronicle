"""Capture denylist matching and visible-window screenshot privacy checks."""

from __future__ import annotations

import json
import math
import os
import platform
import re
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from ..config import CaptureConfig
from ..logger import get
from .protection_reason import ProtectionReasonCode

logger = get("openchronicle.capture")
_WARNED_BAD_PATTERNS: set[str] = set()
_WINDOW_LIST_TIMEOUT = 5


@dataclass(frozen=True)
class ScreenRegion:
    left: float
    top: float
    width: float
    height: float


@dataclass(frozen=True)
class DisplayInfo:
    id: int
    region: ScreenRegion
    is_primary: bool = False


@dataclass(frozen=True)
class VisibleWindow:
    app_name: str
    bundle_id: str
    title: str
    region: ScreenRegion
    is_active: bool = False
    title_available: bool = True
    is_active_candidate: bool = False


@dataclass(frozen=True)
class WindowInventory:
    windows: tuple[VisibleWindow, ...]
    displays: tuple[DisplayInfo, ...]


class ProtectionFailureReason(StrEnum):
    PAUSE_STATE_UNAVAILABLE = "pause_state_unavailable"
    INVENTORY_UNAVAILABLE = "inventory_unavailable"
    HELPER_EXIT = "helper_exit"
    HELPER_PARSE = "helper_parse"
    EMPTY_DISPLAYS = "empty_displays"
    MULTIPLE_ACTIVE_WINDOWS = "multiple_active_windows"
    INVALID_DISPLAY_INVENTORY = "invalid_display_inventory"
    ACTIVE_WINDOW_UNMAPPED = "active_window_unmapped"
    SENSITIVE_WINDOW_UNMAPPED = "sensitive_window_unmapped"


@dataclass(frozen=True)
class WindowListReadResult:
    raw: dict[str, Any] | None
    failure_reason: ProtectionFailureReason | None


@dataclass(frozen=True)
class InventoryReadResult:
    inventory: WindowInventory | None
    failure_reason: ProtectionFailureReason | None


@dataclass(frozen=True)
class VisibleWindowRuleMatch:
    kind: ProtectionReasonCode
    rule: str | None
    app_name: str | None
    bundle_id: str | None
    window_title: str | None


def exact_match(value: str | None, patterns: list[str]) -> bool:
    return bool(_exact_matching_rules(value, patterns))


def regex_match(value: str | None, patterns: list[str]) -> bool:
    return bool(_regex_matching_rules(value, patterns))


def _exact_matching_rules(value: str | None, patterns: list[str]) -> tuple[str, ...]:
    if not value:
        return ()
    folded = value.casefold()
    matches: list[str] = []
    seen: set[str] = set()
    for pattern in patterns:
        if not pattern or pattern.casefold() != folded or pattern.casefold() in seen:
            continue
        seen.add(pattern.casefold())
        matches.append(pattern)
    return tuple(matches)


def _regex_matching_rules(value: str | None, patterns: list[str]) -> tuple[str, ...]:
    if not value:
        return ()
    matches: list[str] = []
    seen: set[str] = set()
    for pattern in patterns:
        if not pattern:
            continue
        try:
            matched = re.search(pattern, value, flags=re.IGNORECASE) is not None
        except re.error:
            if pattern not in _WARNED_BAD_PATTERNS:
                logger.warning("invalid capture denylist regex; falling back to substring match")
                _WARNED_BAD_PATTERNS.add(pattern)
            matched = pattern.casefold() in value.casefold()
        if matched and pattern.casefold() not in seen:
            seen.add(pattern.casefold())
            matches.append(pattern)
    return tuple(matches)


def capture_denylist_reason(cfg: CaptureConfig, out: dict[str, Any]) -> str | None:
    """Return the first denylist field matched by a normal foreground capture."""
    meta = out.get("window_meta") or {}
    trigger = out.get("trigger") or {}
    focused = out.get("focused_element") or {}

    if exact_match(meta.get("app_name"), cfg.deny_app_names):
        return "app_name"
    if exact_match(meta.get("bundle_id"), cfg.deny_bundle_ids):
        return "bundle_id"
    if regex_match(meta.get("title"), cfg.deny_window_title_patterns):
        return "window_title"
    if regex_match(trigger.get("window_title"), cfg.deny_window_title_patterns):
        return "trigger_window_title"
    if regex_match(out.get("url"), cfg.deny_url_patterns):
        return "url"
    if regex_match(focused.get("value"), cfg.deny_text_patterns):
        return "focused_value"
    if regex_match(out.get("visible_text"), cfg.deny_text_patterns):
        return "visible_text"
    return None


def has_visible_window_rules(cfg: CaptureConfig) -> bool:
    return bool(
        cfg.deny_app_names
        or cfg.deny_bundle_ids
        or cfg.deny_window_title_patterns
    )


def visible_window_denylist_reason(
    cfg: CaptureConfig, window: VisibleWindow
) -> str | None:
    """Return the legacy first-match category for existing callers."""
    matches = visible_window_rule_matches(cfg, window)
    return {
        ProtectionReasonCode.APP_RULE: "app_name",
        ProtectionReasonCode.BUNDLE_RULE: "bundle_id",
        ProtectionReasonCode.WINDOW_TITLE_UNKNOWN: "window_title_unknown",
        ProtectionReasonCode.WINDOW_TITLE_RULE: "window_title",
    }.get(matches[0].kind) if matches else None


def visible_window_rule_matches(
    cfg: CaptureConfig, window: VisibleWindow
) -> tuple[VisibleWindowRuleMatch, ...]:
    """Return every de-duplicated visible-window denylist match in fixed order."""
    app_name = window.app_name or None
    bundle_id = window.bundle_id or None
    window_title = window.title if window.title_available and window.title else None
    matches: list[VisibleWindowRuleMatch] = []
    for kind, rules in (
        (ProtectionReasonCode.APP_RULE, _exact_matching_rules(window.app_name, cfg.deny_app_names)),
        (ProtectionReasonCode.BUNDLE_RULE, _exact_matching_rules(window.bundle_id, cfg.deny_bundle_ids)),
    ):
        matches.extend(
            VisibleWindowRuleMatch(kind, rule, app_name, bundle_id, window_title)
            for rule in rules
        )
    if not window.title_available and cfg.deny_window_title_patterns:
        matches.append(
            VisibleWindowRuleMatch(
                ProtectionReasonCode.WINDOW_TITLE_UNKNOWN,
                None,
                app_name,
                bundle_id,
                None,
            )
        )
    else:
        matches.extend(
            VisibleWindowRuleMatch(
                ProtectionReasonCode.WINDOW_TITLE_RULE,
                rule,
                app_name,
                bundle_id,
                window_title,
            )
            for rule in _regex_matching_rules(window.title, cfg.deny_window_title_patterns)
        )
    return tuple(dict.fromkeys(matches))


def sensitive_window_regions(cfg: CaptureConfig) -> list[ScreenRegion] | None:
    """Return bounds of all visible denylisted windows.

    ``None`` means enumeration failed. Callers can then apply fail-closed policy.
    The helper reads CoreGraphics and top-level AX window metadata, not background AX trees.
    """
    if not has_visible_window_rules(cfg):
        return []

    windows = list_visible_windows()
    if windows is None:
        return None

    matches: list[tuple[ScreenRegion, str]] = []
    for window in windows:
        reason = visible_window_denylist_reason(cfg, window)
        if reason is not None:
            matches.append((window.region, reason))

    if matches:
        reasons = sorted({reason for _, reason in matches})
        logger.info(
            "screenshot privacy guard matched %d visible window(s): %s",
            len(matches),
            ", ".join(reasons),
        )
    return [region for region, _ in matches]


def _read_window_list_helper() -> WindowListReadResult:
    helper = _resolve_window_list_path()
    if helper is None:
        return WindowListReadResult(None, ProtectionFailureReason.INVENTORY_UNAVAILABLE)

    try:
        proc = subprocess.run(
            [str(helper)],
            capture_output=True,
            text=True,
            timeout=_WINDOW_LIST_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired):
        return WindowListReadResult(None, ProtectionFailureReason.INVENTORY_UNAVAILABLE)
    if proc.returncode != 0:
        return WindowListReadResult(None, ProtectionFailureReason.HELPER_EXIT)

    try:
        raw = json.loads(proc.stdout)
        if not isinstance(raw, dict):
            raise TypeError("helper output is not an object")
        return WindowListReadResult(raw, None)
    except (json.JSONDecodeError, TypeError):
        return WindowListReadResult(None, ProtectionFailureReason.HELPER_PARSE)


def _run_window_list_helper() -> dict[str, Any] | None:
    """Legacy raw helper view for callers that do not need a failure code."""
    return _read_window_list_helper().raw


def read_window_inventory_result() -> InventoryReadResult:
    helper_result = _read_window_list_helper()
    if helper_result.raw is None:
        return InventoryReadResult(None, helper_result.failure_reason)

    raw = helper_result.raw
    try:
        windows = tuple(_parse_visible_window(row) for row in raw["windows"])
    except (KeyError, TypeError, ValueError):
        return InventoryReadResult(None, ProtectionFailureReason.HELPER_PARSE)
    try:
        displays = tuple(_parse_display(row) for row in raw["displays"])
    except (KeyError, TypeError, ValueError):
        return InventoryReadResult(None, ProtectionFailureReason.INVALID_DISPLAY_INVENTORY)
    if not displays:
        return InventoryReadResult(None, ProtectionFailureReason.EMPTY_DISPLAYS)
    if sum(window.is_active for window in windows) > 1:
        return InventoryReadResult(None, ProtectionFailureReason.MULTIPLE_ACTIVE_WINDOWS)
    return InventoryReadResult(WindowInventory(windows=windows, displays=displays), None)


def read_window_inventory() -> WindowInventory | None:
    return read_window_inventory_result().inventory


def list_visible_windows() -> list[VisibleWindow] | None:
    inventory = read_window_inventory()
    return list(inventory.windows) if inventory is not None else None


def _parse_visible_window(row: Any) -> VisibleWindow:
    if not isinstance(row, dict):
        raise TypeError("window is not an object")
    region = ScreenRegion(
        left=float(row["left"]),
        top=float(row["top"]),
        width=float(row["width"]),
        height=float(row["height"]),
    )
    if region.width <= 0 or region.height <= 0:
        raise ValueError("window has invalid bounds")
    return VisibleWindow(
        app_name=str(row.get("app_name") or ""),
        bundle_id=str(row.get("bundle_id") or ""),
        title=str(row.get("title") or ""),
        region=region,
        is_active=_optional_bool(row, "is_active", False),
        title_available=_optional_bool(row, "title_available", True),
        is_active_candidate=_optional_bool(row, "is_active_candidate", False),
    )


def _optional_bool(row: dict[str, Any], key: str, default: bool) -> bool:
    value = row.get(key, default)
    if not isinstance(value, bool):
        raise TypeError(f"{key} is not a boolean")
    return value


def _parse_display(row: Any) -> DisplayInfo:
    if not isinstance(row, dict):
        raise TypeError("display is not an object")
    region = ScreenRegion(
        left=float(row["left"]),
        top=float(row["top"]),
        width=float(row["width"]),
        height=float(row["height"]),
    )
    if (
        not all(math.isfinite(value) for value in (region.left, region.top, region.width, region.height))
        or region.width <= 0
        or region.height <= 0
    ):
        raise ValueError("display has invalid bounds")
    return DisplayInfo(
        id=int(row["id"]),
        region=region,
        is_primary=bool(row.get("is_primary")),
    )


def _maybe_compile(swift_path: Path, binary_path: Path) -> None:
    core_path = swift_path.with_name("mac-window-list-core.swift")
    if not swift_path.is_file() or not core_path.is_file():
        return
    if (
        binary_path.is_file()
        and binary_path.stat().st_mtime >= swift_path.stat().st_mtime
        and binary_path.stat().st_mtime >= core_path.stat().st_mtime
    ):
        return

    cache = Path("/tmp/clang-module-cache")
    cache.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["CLANG_MODULE_CACHE_PATH"] = str(cache)
    arch = "arm64" if platform.machine() in ("arm64", "aarch64") else "x86_64"
    target = f"{arch}-apple-macos12.0"
    try:
        result = subprocess.run(
            [
                "swiftc",
                str(core_path),
                str(swift_path),
                "-o",
                str(binary_path),
                "-O",
                "-target",
                target,
                "-swift-version",
                "5",
            ],
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.warning("mac-window-list compile failed: %s (install Xcode CLT?)", exc)
        return
    if result.returncode != 0:
        logger.warning(
            "mac-window-list compile failed (%d): %s",
            result.returncode,
            result.stderr.strip()[:300],
        )


def _resolve_window_list_path() -> Path | None:
    if platform.system() != "Darwin":
        return None

    override = os.environ.get("OPENCHRONICLE_WINDOW_LIST_HELPER")
    if override:
        path = Path(override).expanduser().resolve()
        if path.is_file() and os.access(path, os.X_OK):
            return path
        logger.warning("OPENCHRONICLE_WINDOW_LIST_HELPER is not executable: %s", path)

    candidates: list[Path] = []
    try:
        from importlib.resources import files as _pkg_files

        bundled_dir = Path(str(_pkg_files("openchronicle").joinpath("_bundled")))
        candidates.append(bundled_dir / "mac-window-list")
    except (ModuleNotFoundError, ValueError):
        pass

    dev_root = Path(__file__).resolve().parents[3]
    candidates.append(dev_root / "resources" / "mac-window-list")
    for binary_path in candidates:
        _maybe_compile(binary_path.with_suffix(".swift"), binary_path)
        if binary_path.is_file() and os.access(binary_path, os.X_OK):
            return binary_path
    return None
