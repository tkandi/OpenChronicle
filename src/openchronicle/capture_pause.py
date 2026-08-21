"""Privacy-safe timed capture pause state.

The native app stores structured state in ``~/.openchronicle/.paused``.  The
capture process still treats legacy timestamp-only files as indefinite pauses,
so upgrades never resume capture unexpectedly.

Timed pauses use a two-part safety gate.  Their deadline alone is not enough:
the app must first post the one-minute warning and keep a recent heartbeat in
the state file.  This prevents a sleeping, quit, or unresponsive app from
silently allowing capture to resume.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from . import paths
from .logger import get

logger = get("openchronicle.capture")

NOTICE_GRACE = timedelta(seconds=60)
HEARTBEAT_MAX_AGE = timedelta(seconds=90)


@dataclass(frozen=True)
class CapturePauseState:
    mode: str
    resume_at: datetime | None
    resume_armed_at: datetime | None
    app_heartbeat_at: datetime | None

    @property
    def effective_resume_at(self) -> datetime | None:
        if self.resume_at is None or self.resume_armed_at is None:
            return None
        return max(self.resume_at, self.resume_armed_at + NOTICE_GRACE)

    def can_auto_resume(self, now: datetime) -> bool:
        effective_resume_at = self.effective_resume_at
        if (
            self.mode != "timed"
            or effective_resume_at is None
            or self.app_heartbeat_at is None
            or now < effective_resume_at
        ):
            return False
        return now - self.app_heartbeat_at <= HEARTBEAT_MAX_AGE


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed


def parse_pause_state(raw: bytes) -> CapturePauseState | None:
    """Parse structured state; return None for legacy or invalid pause files."""
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return CapturePauseState(
        mode=str(payload.get("mode") or "indefinite"),
        resume_at=_parse_datetime(payload.get("resume_at")),
        resume_armed_at=_parse_datetime(payload.get("resume_armed_at")),
        app_heartbeat_at=_parse_datetime(payload.get("app_heartbeat_at")),
    )


def capture_is_paused_strict(
    *,
    pause_path: Path | None = None,
    now: datetime | None = None,
) -> bool:
    """Return whether capture is paused, propagating pause-state read failures."""
    pause_path = pause_path or paths.paused_flag()
    try:
        raw = pause_path.read_bytes()
    except FileNotFoundError:
        return False

    state = parse_pause_state(raw)
    observed_at = now or datetime.now().astimezone()
    if state is None or not state.can_auto_resume(observed_at):
        return True

    try:
        # Avoid deleting a pause that the app extended while this process was
        # evaluating the previous contents.
        if pause_path.read_bytes() != raw:
            return True
    except FileNotFoundError:
        return False

    try:
        pause_path.unlink()
    except FileNotFoundError:
        return False
    except OSError as exc:
        logger.warning(
            "could not clear expired capture pause; remaining paused: %s",
            type(exc).__name__,
        )
        return True

    logger.info("capture resumed automatically after timed privacy pause")
    return False


def capture_is_paused(
    *,
    pause_path: Path | None = None,
    now: datetime | None = None,
) -> bool:
    """Return pause policy for compatibility callers, failing closed on read errors."""
    try:
        return capture_is_paused_strict(pause_path=pause_path, now=now)
    except OSError as exc:
        logger.warning(
            "capture pause state unavailable; remaining paused: %s",
            type(exc).__name__,
        )
        return True
