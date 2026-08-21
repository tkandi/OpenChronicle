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
from enum import StrEnum
from pathlib import Path
from typing import Any

from . import paths
from .capture.protection_reason import ProtectionReason, ProtectionReasonCode
from .logger import get

logger = get("openchronicle.capture")

NOTICE_GRACE = timedelta(seconds=60)
HEARTBEAT_MAX_AGE = timedelta(seconds=90)


class CapturePauseKind(StrEnum):
    NOT_PAUSED = "not_paused"
    INDEFINITE = "indefinite"
    TIMED = "timed"
    TIMED_WAITING = "timed_waiting"


@dataclass(frozen=True)
class CapturePauseDecision:
    paused: bool
    kind: CapturePauseKind
    effective_resume_at: datetime | None = None


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


def capture_pause_decision_strict(
    *,
    pause_path: Path | None = None,
    now: datetime | None = None,
) -> CapturePauseDecision:
    """Read the pause file, preserving the existing fail-closed auto-resume gate."""
    pause_path = pause_path or paths.paused_flag()
    try:
        raw = pause_path.read_bytes()
    except FileNotFoundError:
        return CapturePauseDecision(False, CapturePauseKind.NOT_PAUSED)

    state = parse_pause_state(raw)
    observed_at = now or datetime.now().astimezone()
    if state is None or state.mode != "timed":
        return CapturePauseDecision(True, CapturePauseKind.INDEFINITE)

    effective_resume_at = state.effective_resume_at
    if not state.can_auto_resume(observed_at):
        if effective_resume_at is None:
            kind = (
                CapturePauseKind.TIMED
                if state.resume_at is not None and observed_at < state.resume_at
                else CapturePauseKind.TIMED_WAITING
            )
        else:
            kind = (
                CapturePauseKind.TIMED
                if observed_at < effective_resume_at
                else CapturePauseKind.TIMED_WAITING
            )
        return CapturePauseDecision(True, kind, effective_resume_at)

    try:
        # Avoid deleting a pause that the app extended while this process was
        # evaluating the previous contents.
        if pause_path.read_bytes() != raw:
            return CapturePauseDecision(True, CapturePauseKind.TIMED_WAITING, effective_resume_at)
    except FileNotFoundError:
        return CapturePauseDecision(False, CapturePauseKind.NOT_PAUSED)

    try:
        pause_path.unlink()
    except FileNotFoundError:
        return CapturePauseDecision(False, CapturePauseKind.NOT_PAUSED)
    except OSError as exc:
        logger.warning(
            "could not clear expired capture pause; remaining paused: %s",
            type(exc).__name__,
        )
        return CapturePauseDecision(True, CapturePauseKind.TIMED_WAITING, effective_resume_at)

    logger.info("capture resumed automatically after timed privacy pause")
    return CapturePauseDecision(False, CapturePauseKind.NOT_PAUSED)


def capture_is_paused_strict(
    *,
    pause_path: Path | None = None,
    now: datetime | None = None,
) -> bool:
    """Return the strict boolean pause decision for existing capture callers."""
    return capture_pause_decision_strict(pause_path=pause_path, now=now).paused


def pause_reason_from_decision(decision: CapturePauseDecision) -> ProtectionReason | None:
    """Convert a typed pause decision into its fixed, non-sensitive reason code."""
    if not decision.paused:
        return None
    code = {
        CapturePauseKind.INDEFINITE: ProtectionReasonCode.MANUAL_PAUSE,
        CapturePauseKind.TIMED: ProtectionReasonCode.TIMED_PAUSE,
        CapturePauseKind.TIMED_WAITING: ProtectionReasonCode.TIMED_PAUSE_WAITING,
    }.get(decision.kind, ProtectionReasonCode.MANUAL_PAUSE)
    return ProtectionReason(
        code=code,
        display_id=None,
        effective_resume_at=decision.effective_resume_at,
    )


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
