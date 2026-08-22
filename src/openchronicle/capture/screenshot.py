"""Screenshot capture via mss + PIL. Extracted from Einsia-Partner capture_service.py."""

from __future__ import annotations

import base64
import io
import json
import math
import os
import platform
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..logger import get
from .privacy import DisplayInfo, ScreenRegion

logger = get("openchronicle.capture")
_FILTERED_CAPTURE_TIMEOUT = 15
_MAX_CAPTURE_DIMENSION = 32768
_MAX_CAPTURE_PIXELS = 128_000_000
_MASK_COLOR = (128, 128, 128)


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


@dataclass
class _FilteredDisplayImage:
    target: _FilteredCaptureTarget
    image: Any


@dataclass(frozen=True)
class _FilteredCaptureTarget:
    display: DisplayInfo
    width: int
    height: int
    left: int = 0
    top: int = 0


@dataclass(frozen=True)
class _FilteredCaptureLayout:
    targets: tuple[_FilteredCaptureTarget, ...]
    virtual_region: ScreenRegion | None = None
    virtual_width: int | None = None
    virtual_height: int | None = None


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


def grab_filtered_many(
    *,
    monitor_mode: str,
    privacy_mode: str,
    displays: Any,
    protected_window_ids: Any,
    protected_window_regions: Any,
    overlay_window_ids: Any,
    max_width: int,
    jpeg_quality: int,
) -> list[Screenshot] | None:
    """Return source-filtered screenshots, or ``None`` when filtering is unavailable.

    This path intentionally never falls back to ``mss``. Its caller must decide whether
    the existing skip-monitor behavior is appropriate after a filtered capture fails.
    """
    try:
        if monitor_mode not in {"primary", "separate", "all"}:
            return None
        if privacy_mode not in {"mask-window", "exclude-window"}:
            return None
        if not _valid_output_options(max_width, jpeg_quality):
            return None

        inventory = _validated_displays(displays)
        protected_ids = _validated_window_ids(protected_window_ids, require_nonempty=True)
        overlay_ids = _validated_window_ids(overlay_window_ids, require_nonempty=False)
        regions = _validated_regions(protected_window_regions)
        if (
            inventory is None
            or protected_ids is None
            or overlay_ids is None
            or regions is None
            or len(protected_ids) != len(regions)
            or set(protected_ids) & set(overlay_ids)
        ):
            return None

        layout = _filtered_capture_layout(inventory, monitor_mode, max_width)
        if layout is None:
            return None
        raw = _run_filtered_capture_helper(layout.targets, protected_ids, overlay_ids)
        frames = _decode_filtered_response(raw, layout.targets)
        if frames is None:
            return None

        if privacy_mode == "mask-window":
            for frame in frames:
                _mask_protected_regions(frame.image, frame.target, regions)

        if monitor_mode == "all":
            combined = _stitch_filtered_displays(frames, layout)
            if combined is None:
                return None
            image = combined
            if layout.virtual_region is None:
                return None
            return [
                _filtered_screenshot(
                    image,
                    monitor_index=0,
                    monitor_region=layout.virtual_region,
                    monitor_is_all=True,
                    jpeg_quality=jpeg_quality,
                )
            ]

        return [
            _filtered_screenshot(
                frame.image,
                monitor_index=inventory.index(frame.target.display) + 1,
                monitor_region=frame.target.display.region,
                monitor_is_all=False,
                jpeg_quality=jpeg_quality,
            )
            for frame in frames
        ]
    except Exception:  # noqa: BLE001 - this boundary must fail closed without payload logging
        return None


def _valid_output_options(max_width: Any, jpeg_quality: Any) -> bool:
    return (
        isinstance(max_width, int)
        and not isinstance(max_width, bool)
        and 0 <= max_width <= _MAX_CAPTURE_DIMENSION
        and isinstance(jpeg_quality, int)
        and not isinstance(jpeg_quality, bool)
        and 1 <= jpeg_quality <= 100
    )


def _validated_displays(value: Any) -> tuple[DisplayInfo, ...] | None:
    try:
        displays = tuple(value)
    except TypeError:
        return None
    if not displays or any(not isinstance(display, DisplayInfo) for display in displays):
        return None
    if sum(display.is_primary is True for display in displays) != 1:
        return None
    ids: set[int] = set()
    for display in displays:
        if not _is_positive_uint32(display.id) or display.id in ids:
            return None
        ids.add(display.id)
        region = display.region
        if not isinstance(region, ScreenRegion) or not _is_valid_region(region):
            return None
        if not isinstance(display.is_primary, bool):
            return None
    if any(_regions_overlap(first.region, second.region) for index, first in enumerate(displays) for second in displays[index + 1 :]):
        return None
    return displays


def _validated_window_ids(value: Any, *, require_nonempty: bool) -> tuple[int, ...] | None:
    try:
        ids = tuple(value)
    except TypeError:
        return None
    if (require_nonempty and not ids) or any(not _is_positive_uint32(window_id) for window_id in ids):
        return None
    if len(set(ids)) != len(ids):
        return None
    return tuple(sorted(ids))


def _validated_regions(value: Any) -> tuple[ScreenRegion, ...] | None:
    try:
        regions = tuple(value)
    except TypeError:
        return None
    if any(not isinstance(region, ScreenRegion) or not _is_valid_region(region) for region in regions):
        return None
    return regions


def _is_positive_uint32(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 < value <= 0xFFFFFFFF


def _is_valid_region(region: ScreenRegion) -> bool:
    values = (region.left, region.top, region.width, region.height)
    return all(
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        for value in values
    ) and (
        region.width > 0
        and region.height > 0
        and math.isfinite(region.left + region.width)
        and math.isfinite(region.top + region.height)
    )


def _regions_overlap(left: ScreenRegion, right: ScreenRegion) -> bool:
    return (
        left.left < right.left + right.width
        and left.left + left.width > right.left
        and left.top < right.top + right.height
        and left.top + left.height > right.top
    )


def _filtered_capture_layout(
    displays: tuple[DisplayInfo, ...],
    monitor_mode: str,
    max_width: int,
) -> _FilteredCaptureLayout | None:
    if monitor_mode == "all":
        return _all_capture_layout(displays, max_width)
    selected = (
        tuple(display for display in displays if display.is_primary)
        if monitor_mode == "primary"
        else displays
    )
    targets = tuple(_physical_capture_target(display, max_width) for display in selected)
    return _FilteredCaptureLayout(targets) if all(targets) else None


def _physical_capture_target(
    display: DisplayInfo,
    max_width: int,
) -> _FilteredCaptureTarget | None:
    scale = min(1.0, max_width / display.region.width) if max_width else 1.0
    width = _rounded_edge(display.region.width * scale)
    height = _rounded_edge(display.region.height * scale)
    if not _is_valid_pixel_size(width, height):
        return None
    return _FilteredCaptureTarget(display, width, height)


def _all_capture_layout(
    displays: tuple[DisplayInfo, ...],
    max_width: int,
) -> _FilteredCaptureLayout | None:
    left = min(display.region.left for display in displays)
    top = min(display.region.top for display in displays)
    right = max(display.region.left + display.region.width for display in displays)
    bottom = max(display.region.top + display.region.height for display in displays)
    region = ScreenRegion(left, top, right - left, bottom - top)
    if not _is_valid_region(region):
        return None
    scale = min(1.0, max_width / region.width) if max_width else 1.0
    virtual_width = _rounded_edge(region.width * scale)
    virtual_height = _rounded_edge(region.height * scale)
    if not _is_valid_pixel_size(virtual_width, virtual_height):
        return None
    targets: list[_FilteredCaptureTarget] = []
    for display in displays:
        source = display.region
        pixel_left = _rounded_edge((source.left - left) * scale)
        pixel_top = _rounded_edge((source.top - top) * scale)
        pixel_right = _rounded_edge((source.left + source.width - left) * scale)
        pixel_bottom = _rounded_edge((source.top + source.height - top) * scale)
        width = pixel_right - pixel_left
        height = pixel_bottom - pixel_top
        if (
            not _is_valid_pixel_size(width, height)
            or pixel_left < 0
            or pixel_top < 0
            or pixel_right > virtual_width
            or pixel_bottom > virtual_height
        ):
            return None
        targets.append(_FilteredCaptureTarget(display, width, height, pixel_left, pixel_top))
    return _FilteredCaptureLayout(tuple(targets), region, virtual_width, virtual_height)


def _rounded_edge(value: float) -> int:
    if not math.isfinite(value) or value < 0:
        return -1
    return math.floor(value + 0.5)


def _run_filtered_capture_helper(
    targets: tuple[_FilteredCaptureTarget, ...],
    protected_window_ids: tuple[int, ...],
    overlay_window_ids: tuple[int, ...],
) -> dict[str, Any] | None:
    helper = _resolve_filtered_capture_helper()
    if helper is None:
        return None
    request = {
        "version": 1,
        "displays": [
            {"id": target.display.id, "width": target.width, "height": target.height}
            for target in targets
        ],
        "protected_window_ids": list(protected_window_ids),
        "overlay_window_ids": list(overlay_window_ids),
    }
    try:
        proc = subprocess.run(
            [str(helper)],
            input=json.dumps(request, separators=(",", ":")) + "\n",
            capture_output=True,
            text=True,
            timeout=_FILTERED_CAPTURE_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0 or proc.stderr or not _is_single_response_line(proc.stdout):
        return None
    try:
        payload = json.loads(proc.stdout[:-1], object_pairs_hook=_no_duplicate_json_object)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _is_single_response_line(value: Any) -> bool:
    return isinstance(value, str) and value.endswith("\n") and value.count("\n") == 1 and bool(value[:-1])


def _no_duplicate_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _decode_filtered_response(
    payload: dict[str, Any] | None,
    expected_targets: tuple[_FilteredCaptureTarget, ...],
) -> list[_FilteredDisplayImage] | None:
    if not isinstance(payload, dict) or set(payload) != {"version", "status", "displays"}:
        return None
    if (
        type(payload.get("version")) is not int
        or payload["version"] != 1
        or payload.get("status") != "ok"
    ):
        return None
    rows = payload.get("displays")
    if not isinstance(rows, list) or len(rows) != len(expected_targets):
        return None
    try:
        from PIL import Image
    except ImportError:
        return None

    frames: list[_FilteredDisplayImage] = []
    for row, target in zip(rows, expected_targets, strict=True):
        decoded = _decode_filtered_display(row, target, Image)
        if decoded is None:
            return None
        frames.append(decoded)
    return frames


def _decode_filtered_display(
    row: Any,
    target: _FilteredCaptureTarget,
    image_cls: Any,
) -> _FilteredDisplayImage | None:
    expected_keys = {
        "id",
        "left",
        "top",
        "point_width",
        "point_height",
        "pixel_width",
        "pixel_height",
        "png_base64",
    }
    if not isinstance(row, dict) or set(row) != expected_keys:
        return None
    display = target.display
    region = display.region
    if (
        type(row.get("id")) is not int
        or row["id"] != display.id
        or not _is_json_number(row.get("left"))
        or not _is_json_number(row.get("top"))
        or not _is_json_number(row.get("point_width"))
        or not _is_json_number(row.get("point_height"))
        or row["left"] != region.left
        or row["top"] != region.top
        or row["point_width"] != region.width
        or row["point_height"] != region.height
    ):
        return None
    pixel_width = row.get("pixel_width")
    pixel_height = row.get("pixel_height")
    if (
        not _is_valid_pixel_size(pixel_width, pixel_height)
        or (pixel_width, pixel_height) != (target.width, target.height)
    ):
        return None
    png_base64 = row.get("png_base64")
    if not isinstance(png_base64, str) or not png_base64:
        return None
    try:
        png_data = base64.b64decode(png_base64, validate=True)
        verified = image_cls.open(io.BytesIO(png_data))
        if verified.format != "PNG" or getattr(verified, "is_animated", False):
            return None
        verified.verify()
        image = image_cls.open(io.BytesIO(png_data))
        image.load()
        if image.size != (pixel_width, pixel_height):
            return None
        return _FilteredDisplayImage(target, image.convert("RGB"))
    except (OSError, ValueError, base64.binascii.Error):
        return None


def _is_json_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _is_valid_pixel_size(width: Any, height: Any) -> bool:
    return (
        isinstance(width, int)
        and not isinstance(width, bool)
        and isinstance(height, int)
        and not isinstance(height, bool)
        and 0 < width <= _MAX_CAPTURE_DIMENSION
        and 0 < height <= _MAX_CAPTURE_DIMENSION
        and width * height <= _MAX_CAPTURE_PIXELS
    )


def _mask_protected_regions(
    image: Any,
    target: _FilteredCaptureTarget,
    regions: tuple[ScreenRegion, ...],
) -> None:
    from PIL import ImageDraw

    display_region = target.display.region
    scale_x = target.width / display_region.width
    scale_y = target.height / display_region.height
    draw = ImageDraw.Draw(image)
    for region in regions:
        left = max(display_region.left, region.left)
        top = max(display_region.top, region.top)
        right = min(display_region.left + display_region.width, region.left + region.width)
        bottom = min(display_region.top + display_region.height, region.top + region.height)
        if left >= right or top >= bottom:
            continue
        pixel_left = max(0, math.floor((left - display_region.left) * scale_x))
        pixel_top = max(0, math.floor((top - display_region.top) * scale_y))
        pixel_right = min(image.width, math.ceil((right - display_region.left) * scale_x))
        pixel_bottom = min(image.height, math.ceil((bottom - display_region.top) * scale_y))
        if pixel_left < pixel_right and pixel_top < pixel_bottom:
            draw.rectangle(
                (pixel_left, pixel_top, pixel_right - 1, pixel_bottom - 1),
                fill=_MASK_COLOR,
            )


def _stitch_filtered_displays(
    frames: list[_FilteredDisplayImage],
    layout: _FilteredCaptureLayout,
) -> Any | None:
    try:
        from PIL import Image
    except ImportError:
        return None
    if not frames:
        return None
    if (
        layout.virtual_width is None
        or layout.virtual_height is None
        or len(frames) != len(layout.targets)
        or not _is_valid_pixel_size(layout.virtual_width, layout.virtual_height)
    ):
        return None
    canvas = Image.new("RGB", (layout.virtual_width, layout.virtual_height), _MASK_COLOR)
    for frame, target in zip(frames, layout.targets, strict=True):
        if frame.target != target or frame.image.size != (target.width, target.height):
            return None
        canvas.paste(frame.image, (target.left, target.top))
    return canvas


def _filtered_screenshot(
    image: Any,
    *,
    monitor_index: int,
    monitor_region: ScreenRegion,
    monitor_is_all: bool,
    jpeg_quality: int,
) -> Screenshot:
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=jpeg_quality, optimize=True)
    return Screenshot(
        image_base64=base64.b64encode(output.getvalue()).decode("ascii"),
        width=image.width,
        height=image.height,
        monitor_index=monitor_index,
        monitor_left=_int_or_none(monitor_region.left),
        monitor_top=_int_or_none(monitor_region.top),
        monitor_width=_int_or_none(monitor_region.width),
        monitor_height=_int_or_none(monitor_region.height),
        monitor_is_all=monitor_is_all,
    )


def _resolve_filtered_capture_helper() -> Path | None:
    if platform.system() != "Darwin":
        return None
    override = os.environ.get("OPENCHRONICLE_SCREEN_CAPTURE_HELPER")
    if override:
        path = Path(override).expanduser().resolve()
        return path if path.is_file() and os.access(path, os.X_OK) else None

    for directory in _filtered_capture_helper_directories():
        binary = directory / "mac-screen-capture"
        if _ensure_filtered_capture_helper(binary):
            return binary
    return None


def _filtered_capture_helper_directories() -> tuple[Path, ...]:
    candidates: list[Path] = []
    try:
        from importlib.resources import files as package_files

        bundled = Path(str(package_files("openchronicle").joinpath("_bundled")))
        candidates.append(bundled)
    except (ModuleNotFoundError, ValueError):
        pass
    candidates.append(Path(__file__).resolve().parents[3] / "resources")
    return tuple(candidates)


def _ensure_filtered_capture_helper(binary: Path) -> bool:
    main = binary.with_suffix(".swift")
    core = binary.with_name("mac-screen-capture-core.swift")
    if not main.is_file() or not core.is_file():
        return binary.is_file() and os.access(binary, os.X_OK)
    build_script = binary.with_name("build-mac-screen-capture.sh")
    if not build_script.is_file():
        return False
    if _filtered_capture_helper_is_fresh(binary, main, core, build_script):
        return True
    try:
        result = subprocess.run(
            ["/bin/bash", str(build_script)],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(binary.parent),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return (
        result.returncode == 0
        and not result.stderr
        and _filtered_capture_helper_is_fresh(binary, main, core, build_script)
    )


def _filtered_capture_helper_is_fresh(
    binary: Path,
    main: Path,
    core: Path,
    build_script: Path,
) -> bool:
    try:
        return (
            binary.is_file()
            and os.access(binary, os.X_OK)
            and binary.stat().st_mtime >= main.stat().st_mtime
            and binary.stat().st_mtime >= core.stat().st_mtime
            and binary.stat().st_mtime >= build_script.stat().st_mtime
        )
    except OSError:
        return False
