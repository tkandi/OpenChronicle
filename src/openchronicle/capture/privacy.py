"""Capture denylist matching and visible-window screenshot privacy checks."""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import CaptureConfig
from ..logger import get

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


@dataclass(frozen=True)
class WindowInventory:
    windows: tuple[VisibleWindow, ...]
    displays: tuple[DisplayInfo, ...]


def exact_match(value: str | None, patterns: list[str]) -> bool:
    if not value:
        return False
    folded = value.casefold()
    return any(pattern.casefold() == folded for pattern in patterns if pattern)


def regex_match(value: str | None, patterns: list[str]) -> bool:
    if not value:
        return False
    for pattern in patterns:
        if not pattern:
            continue
        try:
            if re.search(pattern, value, flags=re.IGNORECASE):
                return True
        except re.error as exc:
            if pattern not in _WARNED_BAD_PATTERNS:
                logger.warning(
                    "invalid capture denylist regex %r: %s; falling back to substring match",
                    pattern,
                    exc,
                )
                _WARNED_BAD_PATTERNS.add(pattern)
            if pattern.casefold() in value.casefold():
                return True
    return False


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
    if exact_match(window.app_name, cfg.deny_app_names):
        return "app_name"
    if exact_match(window.bundle_id, cfg.deny_bundle_ids):
        return "bundle_id"
    if regex_match(window.title, cfg.deny_window_title_patterns):
        return "window_title"
    return None


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


def _run_window_list_helper() -> dict[str, Any] | None:
    helper = _resolve_window_list_path()
    if helper is None:
        logger.warning("screenshot privacy helper unavailable")
        return None

    try:
        proc = subprocess.run(
            [str(helper)],
            capture_output=True,
            text=True,
            timeout=_WINDOW_LIST_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("visible-window enumeration failed: %s", exc)
        return None
    if proc.returncode != 0:
        logger.warning("visible-window helper exited %d", proc.returncode)
        return None

    try:
        raw = json.loads(proc.stdout)
        if not isinstance(raw, dict):
            raise TypeError("helper output is not an object")
        return raw
    except (json.JSONDecodeError, TypeError) as exc:
        logger.warning("invalid visible-window helper output: %s", exc)
        return None


def read_window_inventory() -> WindowInventory | None:
    raw = _run_window_list_helper()
    if raw is None:
        return None
    try:
        windows = tuple(_parse_visible_window(row) for row in raw["windows"])
        displays = tuple(_parse_display(row) for row in raw["displays"])
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning("invalid visible-window helper output: %s", exc)
        return None
    return WindowInventory(windows=windows, displays=displays)


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
        is_active=bool(row.get("is_active")),
    )


def _parse_display(row: Any) -> DisplayInfo:
    if not isinstance(row, dict):
        raise TypeError("display is not an object")
    return DisplayInfo(
        id=int(row["id"]),
        region=ScreenRegion(
            left=float(row["left"]),
            top=float(row["top"]),
            width=float(row["width"]),
            height=float(row["height"]),
        ),
        is_primary=bool(row.get("is_primary")),
    )


def _maybe_compile(swift_path: Path, binary_path: Path) -> None:
    if not swift_path.is_file():
        return
    if binary_path.is_file() and binary_path.stat().st_mtime >= swift_path.stat().st_mtime:
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
