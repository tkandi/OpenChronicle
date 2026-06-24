"""Screenshot capture via mss + PIL. Extracted from Einsia-Partner capture_service.py."""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from typing import Any

from ..logger import get
from .privacy import ScreenRegion

logger = get("openchronicle.capture")


@dataclass
class Screenshot:
    image_base64: str
    mime_type: str = "image/jpeg"
    width: int = 0
    height: int = 0
    monitor_index: int | None = None
    monitor_left: int | None = None
    monitor_top: int | None = None
    monitor_width: int | None = None
    monitor_height: int | None = None
    monitor_is_all: bool = False


def grab(
    max_width: int = 1920,
    jpeg_quality: int = 80,
    monitor_mode: str = "primary",
    blocked_regions: list[ScreenRegion] | None = None,
) -> Screenshot | None:
    """Capture one monitor target and return a base64-encoded JPEG.

    ``monitor_mode`` accepts ``primary`` (the project default) or ``all`` (the
    virtual desktop containing every monitor in one image). ``separate`` is
    handled by :func:`grab_many`.
    """
    shots = grab_many(
        monitor_mode=monitor_mode,
        max_width=max_width,
        jpeg_quality=jpeg_quality,
        blocked_regions=blocked_regions,
    )
    return shots[0] if shots else None


def grab_many(
    monitor_mode: str = "primary",
    max_width: int = 1920,
    jpeg_quality: int = 80,
    blocked_regions: list[ScreenRegion] | None = None,
) -> list[Screenshot]:
    """Capture according to ``monitor_mode``.

    Modes:
      * primary  - one image for the primary/first monitor.
      * all      - one image for the all-monitors virtual desktop.
      * separate - one image per physical monitor.
    """
    try:
        import mss
        from PIL import Image
    except ImportError as exc:
        logger.warning("mss/Pillow not installed: %s", exc)
        return []

    mode = str(monitor_mode or "primary").strip().lower()
    if mode not in {"primary", "all", "separate"}:
        logger.warning("Unknown screenshot monitor mode %r; using primary", monitor_mode)
        mode = "primary"

    try:
        with mss.mss() as sct:
            monitors = sct.monitors
            if len(monitors) < 2:
                logger.warning("No monitors reported by mss")
                return []
            targets = _targets_for_mode(sct, monitors, mode)
            shots: list[Screenshot] = []
            for index, mon, is_all in targets:
                if blocked_regions and any(
                    _monitor_intersects_region(mon, region) for region in blocked_regions
                ):
                    logger.info(
                        "screenshot skipped for monitor %d (visible-window privacy guard)",
                        index,
                    )
                    continue
                shot = _grab_monitor(
                    sct,
                    Image,
                    mon,
                    monitor_index=index,
                    monitor_is_all=is_all,
                    max_width=max_width,
                    jpeg_quality=jpeg_quality,
                )
                if shot is not None:
                    shots.append(shot)
            return shots
    except Exception as exc:  # noqa: BLE001 — mss can raise a variety of OS errors
        logger.warning("Screenshot grab failed: %s", exc)
        return []


def to_dict(shot: Screenshot) -> dict[str, Any]:
    """Serialize a screenshot for capture-buffer JSON."""
    out: dict[str, Any] = {
        "image_base64": shot.image_base64,
        "mime_type": shot.mime_type,
        "width": shot.width,
        "height": shot.height,
    }
    if shot.monitor_index is not None:
        monitor: dict[str, Any] = {"index": shot.monitor_index}
        if shot.monitor_left is not None:
            monitor["left"] = shot.monitor_left
        if shot.monitor_top is not None:
            monitor["top"] = shot.monitor_top
        if shot.monitor_width is not None:
            monitor["width"] = shot.monitor_width
        if shot.monitor_height is not None:
            monitor["height"] = shot.monitor_height
        if shot.monitor_is_all:
            monitor["is_all"] = True
        out["monitor"] = monitor
    return out


def _targets_for_mode(sct: Any, monitors: list[dict[str, Any]], mode: str):
    if mode == "all":
        return [(0, monitors[0], True)]
    if mode == "separate":
        return [(idx, mon, False) for idx, mon in enumerate(monitors[1:], start=1)]
    return [_primary_target(sct, monitors)]


def _primary_target(sct: Any, monitors: list[dict[str, Any]]):
    try:
        primary = sct.primary_monitor
    except Exception:  # noqa: BLE001 - older mss versions may not expose it cleanly
        primary = None
    if isinstance(primary, dict):
        for idx, mon in enumerate(monitors):
            if idx > 0 and mon == primary:
                return (idx, mon, False)
        return (1, primary, False)

    for idx, mon in enumerate(monitors[1:], start=1):
        if mon.get("is_primary"):
            return (idx, mon, False)
    return (1, monitors[1], False)


def _grab_monitor(
    sct: Any,
    image_cls: Any,
    mon: dict[str, Any],
    *,
    monitor_index: int,
    monitor_is_all: bool,
    max_width: int,
    jpeg_quality: int,
) -> Screenshot | None:
    raw = sct.grab(mon)
    img = image_cls.frombytes("RGB", raw.size, raw.rgb)
    if max_width and img.width > max_width:
        ratio = max_width / img.width
        new_size = (max_width, int(img.height * ratio))
        img = img.resize(new_size, image_cls.Resampling.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=jpeg_quality, optimize=True)
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return Screenshot(
        image_base64=encoded,
        width=img.width,
        height=img.height,
        monitor_index=monitor_index,
        monitor_left=_int_or_none(mon.get("left")),
        monitor_top=_int_or_none(mon.get("top")),
        monitor_width=_int_or_none(mon.get("width")),
        monitor_height=_int_or_none(mon.get("height")),
        monitor_is_all=monitor_is_all,
    )


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _monitor_intersects_region(mon: dict[str, Any], region: ScreenRegion) -> bool:
    left = float(mon.get("left", 0))
    top = float(mon.get("top", 0))
    right = left + float(mon.get("width", 0))
    bottom = top + float(mon.get("height", 0))
    region_right = region.left + region.width
    region_bottom = region.top + region.height
    return (
        left < region_right
        and right > region.left
        and top < region_bottom
        and bottom > region.top
    )
