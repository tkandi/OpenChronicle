"""Safe configuration inspection and mutation for the CLI and macOS app.

The editor deliberately keeps TOML serialization narrow. Common settings are
patched in place so comments and unknown future keys survive, while the
advanced editor can replace the full document after strict validation.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import tempfile
import tomllib
from datetime import datetime
from pathlib import Path
from typing import Any

from . import config as config_mod


class ConfigEditorError(ValueError):
    """Base class for user-facing configuration errors."""


class ConfigConflictError(ConfigEditorError):
    """Raised when the file changed after the app loaded it."""


MODEL_STAGES = ("default", "timeline", "reducer", "classifier", "compact")
PRIVACY_FIELDS = (
    "deny_app_names",
    "deny_bundle_ids",
    "protect_unknown_title_bundle_ids",
    "deny_window_title_patterns",
    "deny_url_patterns",
    "deny_text_patterns",
)
PRIVACY_PATHS = {f"capture.{field}" for field in PRIVACY_FIELDS}

# The common App form may only touch these non-secret scalar fields. Advanced
# users can still edit every setting in the raw TOML editor.
EDITABLE_PATHS = {
    "models.default.model",
    "models.default.base_url",
    "models.default.api_key_env",
    "models.timeline.model",
    "models.reducer.model",
    "models.classifier.model",
    "models.compact.model",
    "capture.event_driven",
    "capture.heartbeat_minutes",
    "capture.include_screenshot",
    "capture.screenshot_monitor",
    "capture.screenshot_privacy_mode",
    "capture.screenshot_privacy_fail_closed",
    "capture.privacy_indicator_style",
    "capture.privacy_indicator_placement",
    "capture.privacy_reason_display",
    "capture.privacy_reason_detail",
    "capture.privacy_reason_trigger",
    "capture.buffer_retention_hours",
    "capture.screenshot_retention_hours",
    "capture.buffer_max_mb",
    "capture.screenshot_jpeg_quality",
    "timeline.window_minutes",
    "session.gap_minutes",
    "session.flush_minutes",
    "reducer.enabled",
    "classifier.interval_minutes",
    "memory.auto_dormant_days",
    "search.default_top_k",
    "mcp.auto_start",
    "mcp.transport",
    "mcp.host",
    "mcp.port",
} | PRIVACY_PATHS

DELETABLE_PATHS = {
    "models.timeline.model",
    "models.reducer.model",
    "models.classifier.model",
    "models.compact.model",
}


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _is_number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(float(value))


def _require_type(
    raw: dict[str, Any],
    key: str,
    expected: type | tuple[type, ...],
    label: str,
) -> Any:
    if key not in raw:
        return None
    value = raw[key]
    valid = type(value) in expected if isinstance(expected, tuple) else type(value) is expected
    if not valid:
        names = (
            "/".join(item.__name__ for item in expected)
            if isinstance(expected, tuple)
            else expected.__name__
        )
        raise ConfigEditorError(f"{label} must be {names}")
    return value


def _require_section(raw: dict[str, Any], name: str) -> dict[str, Any]:
    value = raw.get(name, {})
    if not isinstance(value, dict):
        raise ConfigEditorError(f"[{name}] must be a TOML table")
    return value


def _require_range(
    raw: dict[str, Any],
    key: str,
    label: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> None:
    if key not in raw:
        return
    value = raw[key]
    if not _is_number(value):
        raise ConfigEditorError(f"{label} must be a number")
    if minimum is not None and value < minimum:
        raise ConfigEditorError(f"{label} must be at least {minimum:g}")
    if maximum is not None and value > maximum:
        raise ConfigEditorError(f"{label} must be at most {maximum:g}")


def _validate_string_list(raw: dict[str, Any], key: str, label: str) -> list[str] | None:
    if key not in raw:
        return None
    value = raw[key]
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ConfigEditorError(f"{label} must be an array of strings")
    return value


def validate_mapping(raw: dict[str, Any]) -> None:
    """Strictly validate known settings while allowing unknown future keys."""
    if not isinstance(raw, dict):
        raise ConfigEditorError("configuration root must be a TOML table")

    models = _require_section(raw, "models")
    for stage, section in models.items():
        if not isinstance(section, dict):
            raise ConfigEditorError(f"[models.{stage}] must be a TOML table")
        for key in ("model", "base_url", "api_key", "api_key_env"):
            _require_type(section, key, str, f"models.{stage}.{key}")
        if "model" in section and not section["model"].strip():
            raise ConfigEditorError(f"models.{stage}.model cannot be empty")
        if "max_tokens" in section:
            value = section["max_tokens"]
            if type(value) is not int or value < 1:
                raise ConfigEditorError(f"models.{stage}.max_tokens must be a positive integer")

    capture = _require_section(raw, "capture")
    for key in ("event_driven", "include_screenshot", "screenshot_privacy_fail_closed"):
        _require_type(capture, key, bool, f"capture.{key}")
    for key in (
        "heartbeat_minutes",
        "interval_minutes",
        "buffer_retention_hours",
        "screenshot_retention_hours",
        "buffer_max_mb",
        "ax_depth",
    ):
        _require_type(capture, key, int, f"capture.{key}")
        _require_range(capture, key, f"capture.{key}", minimum=0)
    for key in (
        "debounce_seconds",
        "min_capture_gap_seconds",
        "dedup_interval_seconds",
        "same_window_dedup_seconds",
    ):
        _require_type(capture, key, (int, float), f"capture.{key}")
        _require_range(capture, key, f"capture.{key}", minimum=0)
    for key in ("screenshot_max_width", "ax_timeout_seconds"):
        _require_type(capture, key, int, f"capture.{key}")
        _require_range(capture, key, f"capture.{key}", minimum=1)
    _require_type(capture, "screenshot_jpeg_quality", int, "capture.screenshot_jpeg_quality")
    _require_range(
        capture,
        "screenshot_jpeg_quality",
        "capture.screenshot_jpeg_quality",
        minimum=1,
        maximum=100,
    )
    monitor = _require_type(capture, "screenshot_monitor", str, "capture.screenshot_monitor")
    if monitor is not None and monitor.lower() not in {"primary", "all", "separate"}:
        raise ConfigEditorError("capture.screenshot_monitor must be primary, all, or separate")
    privacy_mode = _require_type(
        capture,
        "screenshot_privacy_mode",
        str,
        "capture.screenshot_privacy_mode",
    )
    if (
        privacy_mode is not None
        and privacy_mode.lower() not in config_mod.SCREENSHOT_PRIVACY_MODES
    ):
        raise ConfigEditorError(
            "capture.screenshot_privacy_mode must be off, skip-monitor, mask-window, "
            "or exclude-window"
        )
    indicator_style = _require_type(
        capture,
        "privacy_indicator_style",
        str,
        "capture.privacy_indicator_style",
    )
    if (
        indicator_style is not None
        and indicator_style.lower() not in config_mod.PRIVACY_INDICATOR_STYLES
    ):
        raise ConfigEditorError(
            "capture.privacy_indicator_style must be off, border, shield, pill, "
            "quiet-shield, or banner"
        )
    indicator_placement = _require_type(
        capture,
        "privacy_indicator_placement",
        str,
        "capture.privacy_indicator_placement",
    )
    if (
        indicator_placement is not None
        and indicator_placement.lower() not in config_mod.PRIVACY_INDICATOR_PLACEMENTS
    ):
        raise ConfigEditorError(
            "capture.privacy_indicator_placement must be bottom-left-flush, "
            "bottom-left-inset, or bottom-right-work-area"
        )
    reason_display = _require_type(
        capture,
        "privacy_reason_display",
        str,
        "capture.privacy_reason_display",
    )
    if (
        reason_display is not None
        and reason_display.lower() not in config_mod.PRIVACY_REASON_DISPLAY_MODES
    ):
        raise ConfigEditorError(
            "capture.privacy_reason_display must be overlay, diagnostics, or hybrid"
        )
    reason_detail = _require_type(
        capture,
        "privacy_reason_detail",
        str,
        "capture.privacy_reason_detail",
    )
    if (
        reason_detail is not None
        and reason_detail.lower() not in config_mod.PRIVACY_REASON_DETAIL_MODES
    ):
        raise ConfigEditorError(
            "capture.privacy_reason_detail must be category, exact, or tiered"
        )
    reason_trigger = _require_type(
        capture,
        "privacy_reason_trigger",
        str,
        "capture.privacy_reason_trigger",
    )
    if (
        reason_trigger is not None
        and reason_trigger.lower() not in config_mod.PRIVACY_REASON_TRIGGERS
    ):
        raise ConfigEditorError(
            "capture.privacy_reason_trigger must be always, hover, or click"
        )
    for key in (
        "deny_app_names",
        "deny_bundle_ids",
        "protect_unknown_title_bundle_ids",
        "deny_window_title_patterns",
        "deny_url_patterns",
        "deny_text_patterns",
    ):
        values = _validate_string_list(capture, key, f"capture.{key}")
        if (
            key == "protect_unknown_title_bundle_ids"
            and values is not None
            and any(not value.strip() for value in values)
        ):
            raise ConfigEditorError(
                "capture.protect_unknown_title_bundle_ids cannot contain blank values"
            )
        if values is not None and key.endswith("_patterns"):
            for pattern in values:
                try:
                    re.compile(pattern)
                except re.error as exc:
                    raise ConfigEditorError(f"capture.{key} contains invalid regex: {exc}") from exc

    numeric_sections: dict[str, dict[str, tuple[float | None, float | None]]] = {
        "timeline": {
            "window_minutes": (1, None),
            "cold_lookback_minutes": (0, None),
            "recent_context_blocks": (1, None),
        },
        "writer": {
            "soft_limit_tokens": (1, None),
            "hard_limit_tokens": (1, None),
            "dedup_window_hours": (0, None),
            "cold_start_conservative_hours": (0, None),
            "max_tool_iterations": (1, None),
        },
        "session": {
            "gap_minutes": (1, None),
            "soft_cut_minutes": (0, None),
            "max_session_hours": (1, None),
            "tick_seconds": (1, None),
            "flush_minutes": (1, None),
        },
        "reducer": {
            "daily_tick_hour": (0, 23),
            "daily_tick_minute": (0, 59),
        },
        "classifier": {"interval_minutes": (1, None)},
        "memory": {"auto_dormant_days": (0, None)},
        "search": {"default_top_k": (1, None)},
    }
    for section_name, fields in numeric_sections.items():
        section = _require_section(raw, section_name)
        for key, (minimum, maximum) in fields.items():
            _require_type(section, key, int, f"{section_name}.{key}")
            _require_range(
                section,
                key,
                f"{section_name}.{key}",
                minimum=minimum,
                maximum=maximum,
            )

    writer = _require_section(raw, "writer")
    if (
        type(writer.get("soft_limit_tokens")) is int
        and type(writer.get("hard_limit_tokens")) is int
        and writer["hard_limit_tokens"] < writer["soft_limit_tokens"]
    ):
        raise ConfigEditorError("writer.hard_limit_tokens must be >= writer.soft_limit_tokens")

    reducer = _require_section(raw, "reducer")
    _require_type(reducer, "enabled", bool, "reducer.enabled")
    search = _require_section(raw, "search")
    _require_type(
        search,
        "filter_superseded_by_default",
        bool,
        "search.filter_superseded_by_default",
    )

    mcp = _require_section(raw, "mcp")
    _require_type(mcp, "auto_start", bool, "mcp.auto_start")
    transport = _require_type(mcp, "transport", str, "mcp.transport")
    if transport is not None and transport not in {"streamable-http", "sse", "stdio"}:
        raise ConfigEditorError("mcp.transport must be streamable-http, sse, or stdio")
    host = _require_type(mcp, "host", str, "mcp.host")
    if host is not None and not host.strip():
        raise ConfigEditorError("mcp.host cannot be empty")
    _require_type(mcp, "port", int, "mcp.port")
    _require_range(mcp, "port", "mcp.port", minimum=1, maximum=65535)


def validate_text(text: str) -> tuple[dict[str, Any], config_mod.Config]:
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigEditorError(f"Invalid TOML: {exc}") from exc
    validate_mapping(raw)
    return raw, config_mod.from_mapping(raw)


def _safe_model_payload(
    cfg: config_mod.Config,
    raw_models: dict[str, Any],
    stage: str,
) -> dict[str, Any]:
    model = cfg.model_for(stage)
    raw_stage = raw_models.get(stage, {})
    if not isinstance(raw_stage, dict):
        raw_stage = {}
    return {
        "model": model.model,
        "base_url": model.base_url,
        "api_key_env": model.api_key_env,
        "max_tokens": model.max_tokens,
        "model_explicit": "model" in raw_stage,
        "uses_direct_api_key": bool(model.api_key),
    }


def snapshot_payload(path: Path) -> dict[str, Any]:
    content = path.read_text(encoding="utf-8")
    payload: dict[str, Any] = {
        "schema_version": 1,
        "path": str(path),
        "sha256": sha256_text(content),
        "valid": False,
        "error": None,
        "contains_direct_api_keys": False,
        "values": None,
    }
    try:
        raw, cfg = validate_text(content)
    except ConfigEditorError as exc:
        payload["error"] = str(exc)
        return payload

    raw_models = raw.get("models", {})
    if not isinstance(raw_models, dict):
        raw_models = {}
    models = {
        stage: _safe_model_payload(cfg, raw_models, stage)
        for stage in MODEL_STAGES
    }
    payload.update(
        {
            "valid": True,
            "contains_direct_api_keys": any(
                model["uses_direct_api_key"] for model in models.values()
            ),
            "values": {
                "models": models,
                "capture": {
                    "event_driven": cfg.capture.event_driven,
                    "heartbeat_minutes": cfg.capture.heartbeat_minutes,
                    "buffer_retention_hours": cfg.capture.buffer_retention_hours,
                    "screenshot_retention_hours": cfg.capture.screenshot_retention_hours,
                    "buffer_max_mb": cfg.capture.buffer_max_mb,
                    "include_screenshot": cfg.capture.include_screenshot,
                    "screenshot_monitor": cfg.capture.screenshot_monitor,
                    "screenshot_privacy_mode": cfg.capture.screenshot_privacy_mode,
                    "screenshot_privacy_fail_closed": (
                        cfg.capture.screenshot_privacy_fail_closed
                    ),
                    "privacy_indicator_style": cfg.capture.privacy_indicator_style,
                    "privacy_indicator_placement": (
                        cfg.capture.privacy_indicator_placement
                    ),
                    "privacy_reason_display": cfg.capture.privacy_reason_display,
                    "privacy_reason_detail": cfg.capture.privacy_reason_detail,
                    "privacy_reason_trigger": cfg.capture.privacy_reason_trigger,
                    "screenshot_jpeg_quality": cfg.capture.screenshot_jpeg_quality,
                    "privacy_counts": {
                        field: len(getattr(cfg.capture, field))
                        for field in PRIVACY_FIELDS
                    },
                },
                "timeline": {"window_minutes": cfg.timeline.window_minutes},
                "session": {
                    "gap_minutes": cfg.session.gap_minutes,
                    "flush_minutes": cfg.session.flush_minutes,
                },
                "reducer": {"enabled": cfg.reducer.enabled},
                "classifier": {"interval_minutes": cfg.classifier.interval_minutes},
                "memory": {"auto_dormant_days": cfg.memory.auto_dormant_days},
                "search": {"default_top_k": cfg.search.default_top_k},
                "mcp": {
                    "auto_start": cfg.mcp.auto_start,
                    "transport": cfg.mcp.transport,
                    "host": cfg.mcp.host,
                    "port": cfg.mcp.port,
                },
            },
        }
    )
    return payload


def privacy_snapshot_payload(path: Path) -> dict[str, Any]:
    """Return denylist values only after an explicit privacy-specific request."""
    content = path.read_text(encoding="utf-8")
    payload: dict[str, Any] = {
        "schema_version": 1,
        "path": str(path),
        "sha256": sha256_text(content),
        "valid": False,
        "error": None,
        "values": None,
    }
    try:
        _, cfg = validate_text(content)
    except ConfigEditorError as exc:
        payload["error"] = str(exc)
        return payload

    payload.update(
        {
            "valid": True,
            "values": {
                field: list(getattr(cfg.capture, field))
                for field in PRIVACY_FIELDS
            },
        }
    )
    return payload


def _toml_value(value: Any) -> str:
    if type(value) is bool:
        return "true" if value else "false"
    if type(value) is int:
        return str(value)
    if type(value) is float and math.isfinite(value):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        if not value:
            return "[]"
        encoded = [f"  {json.dumps(item, ensure_ascii=False)}," for item in value]
        return "[\n" + "\n".join(encoded) + "\n]"
    raise ConfigEditorError(f"unsupported setting value: {value!r}")


def _trailing_comment(rhs: str) -> str:
    quote: str | None = None
    escaped = False
    for index, char in enumerate(rhs):
        if escaped:
            escaped = False
            continue
        if quote == '"' and char == "\\":
            escaped = True
            continue
        if char in {'"', "'"}:
            if quote is None:
                quote = char
            elif quote == char:
                quote = None
            continue
        if char == "#" and quote is None:
            return rhs[index:].rstrip()
    return ""


def _assignment_end(
    lines: list[str],
    assignment_index: int,
    section_end: int,
    key: str,
) -> int:
    """Find the exclusive end of a possibly multiline TOML assignment."""
    for end in range(assignment_index + 1, section_end + 1):
        candidate = "[probe]\n" + "".join(lines[assignment_index:end])
        try:
            parsed = tomllib.loads(candidate)
        except tomllib.TOMLDecodeError:
            continue
        probe = parsed.get("probe", {})
        if isinstance(probe, dict) and key in probe:
            return end
    raise ConfigEditorError(f"could not locate the end of {key}'s TOML value")


def patch_text(text: str, updates: dict[str, Any]) -> str:
    if not isinstance(updates, dict):
        raise ConfigEditorError("updates must be a JSON object")
    unknown = sorted(set(updates) - EDITABLE_PATHS)
    if unknown:
        raise ConfigEditorError(f"unsupported setting: {unknown[0]}")

    lines = text.splitlines(keepends=True)
    for path, value in updates.items():
        if value is None and path not in DELETABLE_PATHS:
            raise ConfigEditorError(f"{path} cannot be removed from the common settings form")
        section, key = path.rsplit(".", 1)
        header_re = re.compile(rf"^\s*\[{re.escape(section)}\]\s*(?:#.*)?$")
        key_re = re.compile(rf"^(\s*{re.escape(key)}\s*=\s*)(.*?)(\r?\n)?$")

        section_start: int | None = None
        section_end = len(lines)
        for index, line in enumerate(lines):
            stripped = line.rstrip("\r\n")
            if section_start is None:
                if header_re.match(stripped):
                    section_start = index + 1
                continue
            if re.match(r"^\s*\[", stripped):
                section_end = index
                break

        if section_start is None:
            if value is None:
                continue
            if lines and not lines[-1].endswith(("\n", "\r")):
                lines[-1] += "\n"
            if lines and lines[-1].strip():
                lines.append("\n")
            lines.extend([f"[{section}]\n", f"{key} = {_toml_value(value)}\n"])
            continue

        assignment_index: int | None = None
        assignment_end: int | None = None
        assignment_match: re.Match[str] | None = None
        for index in range(section_start, section_end):
            match = key_re.match(lines[index].rstrip("\r\n"))
            if match:
                assignment_index = index
                assignment_end = _assignment_end(lines, index, section_end, key)
                assignment_match = match
                break

        if value is None:
            if assignment_index is not None and assignment_end is not None:
                del lines[assignment_index:assignment_end]
            continue

        encoded = _toml_value(value)
        if (
            assignment_index is not None
            and assignment_end is not None
            and assignment_match is not None
        ):
            last_line = lines[assignment_end - 1]
            newline = "\r\n" if last_line.endswith("\r\n") else "\n"
            if not last_line.endswith(("\n", "\r")):
                newline = ""
            comment = (
                _trailing_comment(assignment_match.group(2))
                if assignment_end == assignment_index + 1
                else ""
            )
            suffix = f"  {comment}" if comment else ""
            lines[assignment_index:assignment_end] = [
                f"{assignment_match.group(1)}{encoded}{suffix}{newline}"
            ]
        else:
            insert_at = section_end
            while insert_at > section_start and not lines[insert_at - 1].strip():
                insert_at -= 1
            lines.insert(insert_at, f"{key} = {encoded}\n")

    result = "".join(lines)
    validate_text(result)
    return result


def _current_content(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def write_text_atomic(
    path: Path,
    content: str,
    *,
    expected_sha256: str | None,
) -> dict[str, Any]:
    validate_text(content)
    path.parent.mkdir(parents=True, exist_ok=True)
    current = _current_content(path)
    current_sha = sha256_text(current)
    if expected_sha256 is not None and current_sha != expected_sha256:
        raise ConfigConflictError(
            "config.toml changed after it was loaded; reload Settings before saving"
        )
    if current == content:
        return {
            "ok": True,
            "changed": False,
            "path": str(path),
            "backup": None,
            "sha256": current_sha,
        }

    backup: Path | None = None
    if path.exists():
        stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
        backup = path.with_name(f"{path.name}.backup-{stamp}")
        shutil.copy2(path, backup)

    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        if path.exists():
            os.fchmod(fd, path.stat().st_mode & 0o777)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise

    return {
        "ok": True,
        "changed": True,
        "path": str(path),
        "backup": str(backup) if backup else None,
        "sha256": sha256_text(content),
    }


def apply_updates(
    path: Path,
    updates: dict[str, Any],
    *,
    expected_sha256: str | None,
) -> dict[str, Any]:
    current = _current_content(path)
    if expected_sha256 is not None and sha256_text(current) != expected_sha256:
        raise ConfigConflictError(
            "config.toml changed after it was loaded; reload Settings before saving"
        )
    patched = patch_text(current, updates)
    return write_text_atomic(path, patched, expected_sha256=expected_sha256)
