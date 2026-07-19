"""Privacy-bounded model failure events for the native macOS notifier.

The Python backend deliberately does not attempt to post a macOS notification
itself.  It can also run outside the app, and notifications posted through an
``osascript`` subprocess have the wrong identity and fragile permission
behaviour.  Instead, final provider failures are appended as small JSONL
records which ``OpenChronicle.app`` consumes while it is running.

Only stage, model, exception class, and a short sanitized first line are
persisted.  Prompts, responses, and credentials are never part of an event.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from . import paths
from .logger import get

logger = get("openchronicle.writer")

_SCHEMA_VERSION = 1
_DEFAULT_COOLDOWN_SECONDS = 15 * 60
_MAX_MESSAGE_CHARS = 240

# Provider exceptions commonly echo credentials in one of these shapes.  The
# exact configured key is replaced separately before these defensive patterns.
_GENERIC_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b", re.IGNORECASE), "sk-[REDACTED]"),
    (
        re.compile(r"(?i)(authorization\s*[:=]\s*(?:bearer\s+)?)[^\s,;]+"),
        r"\1[REDACTED]",
    ),
    (
        re.compile(r"(?i)((?:api[_ -]?key|access[_ -]?token|secret)\s*[:=]\s*)[^\s,;]+"),
        r"\1[REDACTED]",
    ),
)


def _sanitize_message(exc: BaseException, *, api_key: str = "") -> str:
    raw = str(exc).strip()
    message = raw.splitlines()[0].strip() if raw else ""
    message = "".join(ch for ch in message if ch.isprintable())
    if api_key:
        message = message.replace(api_key, "[REDACTED]")
    for pattern, replacement in _GENERIC_SECRET_PATTERNS:
        message = pattern.sub(replacement, message)
    if not message:
        message = "No error details provided"
    if len(message) > _MAX_MESSAGE_CHARS:
        message = message[: _MAX_MESSAGE_CHARS - 1].rstrip() + "…"
    return message


class ModelFailureEventWriter:
    """Append sanitized failures with an in-process anti-spam cooldown."""

    def __init__(
        self,
        event_path: Path | None = None,
        *,
        cooldown_seconds: float = _DEFAULT_COOLDOWN_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._event_path = event_path
        self._cooldown_seconds = cooldown_seconds
        self._monotonic = monotonic
        self._now = now or (lambda: datetime.now().astimezone())
        self._recent: dict[tuple[str, str, str, str, str], float] = {}
        self._lock = threading.Lock()

    @property
    def event_path(self) -> Path:
        # Resolve lazily so OPENCHRONICLE_ROOT remains testable after import.
        return self._event_path or paths.model_failure_events_file()

    def record(
        self,
        *,
        stage: str,
        model: str,
        error: BaseException,
        api_key: str = "",
    ) -> bool:
        """Write one event, returning False when suppressed or persistence fails."""
        error_type = type(error).__name__
        message = _sanitize_message(error, api_key=api_key)
        event_path = self.event_path
        fingerprint = (str(event_path), stage, model, error_type, message)
        observed_at = self._monotonic()

        with self._lock:
            previous = self._recent.get(fingerprint)
            if previous is not None and observed_at - previous < self._cooldown_seconds:
                logger.debug(
                    "model failure notification suppressed by cooldown: stage=%s model=%s",
                    stage,
                    model,
                )
                return False

            event: dict[str, Any] = {
                "schema_version": _SCHEMA_VERSION,
                "id": uuid.uuid4().hex,
                "timestamp": self._now().isoformat(timespec="seconds"),
                "stage": stage,
                "model": model,
                "error_type": error_type,
                "message": message,
            }
            try:
                self._append(event_path, event)
            except OSError as exc:
                # Notification handoff must never replace or mask the original
                # model exception which the caller still needs to handle.
                logger.warning("could not persist model failure notification: %s", exc)
                return False

            self._recent[fingerprint] = observed_at
            stale_before = observed_at - max(self._cooldown_seconds * 2, 60)
            self._recent = {
                key: seen for key, seen in self._recent.items() if seen >= stale_before
            }
            return True

    @staticmethod
    def _append(event_path: Path, event: dict[str, Any]) -> None:
        event_path.parent.mkdir(parents=True, exist_ok=True)
        payload = (
            json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        fd = os.open(event_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            # Enforce private permissions even if an old file was created with
            # a permissive umask before this feature existed.
            os.fchmod(fd, 0o600)
            os.write(fd, payload)
        finally:
            os.close(fd)


_default_writer = ModelFailureEventWriter()


def record_model_failure(
    *,
    stage: str,
    model: str,
    error: BaseException,
    api_key: str = "",
) -> bool:
    """Best-effort public hook used by the shared LiteLLM wrapper."""
    return _default_writer.record(
        stage=stage,
        model=model,
        error=error,
        api_key=api_key,
    )
