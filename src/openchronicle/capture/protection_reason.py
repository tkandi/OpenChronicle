"""Bounded, structured explanations for capture protection decisions."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal, Self

_MAX_REASONS_PER_DISPLAY = 8


class ProtectionReasonCode(StrEnum):
    APP_RULE = "app_rule"
    BUNDLE_RULE = "bundle_rule"
    WINDOW_TITLE_RULE = "window_title_rule"
    WINDOW_TITLE_UNKNOWN = "window_title_unknown"
    MODE_ALL_INHERITED = "mode_all_inherited"
    DIAGNOSTICS_REVEAL = "diagnostics_reveal"
    DIAGNOSTICS_GUARD_INVALID = "diagnostics_guard_invalid"
    MANUAL_PAUSE = "manual_pause"
    TIMED_PAUSE = "timed_pause"
    TIMED_PAUSE_WAITING = "timed_pause_waiting"
    PAUSE_STATE_UNAVAILABLE = "pause_state_unavailable"
    INVENTORY_UNAVAILABLE = "inventory_unavailable"
    HELPER_EXIT = "helper_exit"
    HELPER_PARSE = "helper_parse"
    EMPTY_DISPLAYS = "empty_displays"
    INVALID_DISPLAY_INVENTORY = "invalid_display_inventory"
    MULTIPLE_ACTIVE_WINDOWS = "multiple_active_windows"
    ACTIVE_WINDOW_UNMAPPED = "active_window_unmapped"
    SENSITIVE_WINDOW_UNMAPPED = "sensitive_window_unmapped"
    INDICATOR_UNCONFIRMED = "indicator_unconfirmed"


class ProtectionReasonState(StrEnum):
    PROTECTED = "protected"
    PAUSED = "paused"
    FAILED = "failed"


_FAILED_CODES = frozenset(
    {
        ProtectionReasonCode.PAUSE_STATE_UNAVAILABLE,
        ProtectionReasonCode.INVENTORY_UNAVAILABLE,
        ProtectionReasonCode.HELPER_EXIT,
        ProtectionReasonCode.HELPER_PARSE,
        ProtectionReasonCode.EMPTY_DISPLAYS,
        ProtectionReasonCode.INVALID_DISPLAY_INVENTORY,
        ProtectionReasonCode.MULTIPLE_ACTIVE_WINDOWS,
        ProtectionReasonCode.ACTIVE_WINDOW_UNMAPPED,
        ProtectionReasonCode.SENSITIVE_WINDOW_UNMAPPED,
        ProtectionReasonCode.INDICATOR_UNCONFIRMED,
        ProtectionReasonCode.DIAGNOSTICS_GUARD_INVALID,
    }
)
_PAUSED_CODES = frozenset(
    {
        ProtectionReasonCode.MANUAL_PAUSE,
        ProtectionReasonCode.TIMED_PAUSE,
        ProtectionReasonCode.TIMED_PAUSE_WAITING,
    }
)
_PRIORITIES = {
    **{code: 600 for code in _FAILED_CODES},
    **{code: 500 for code in _PAUSED_CODES},
    ProtectionReasonCode.DIAGNOSTICS_REVEAL: 400,
    ProtectionReasonCode.APP_RULE: 300,
    ProtectionReasonCode.BUNDLE_RULE: 300,
    ProtectionReasonCode.WINDOW_TITLE_RULE: 300,
    ProtectionReasonCode.WINDOW_TITLE_UNKNOWN: 200,
    ProtectionReasonCode.MODE_ALL_INHERITED: 100,
}


def sanitize_reason_value(value: str, limit: int = 160) -> str:
    """Keep a reason value display-safe without retaining control characters."""
    cleaned = "".join(
        " " if unicodedata.category(character).startswith("C") else character
        for character in value
    )
    if limit <= 0:
        return ""
    if len(cleaned) <= limit:
        return cleaned
    if limit == 1:
        return "…"
    return f"{cleaned[: limit - 1]}…"


@dataclass(frozen=True)
class ProtectionReason:
    code: ProtectionReasonCode
    display_id: int | None
    source_display_id: int | None = None
    app_name: str | None = None
    bundle_id: str | None = None
    window_title: str | None = None
    rule: str | None = None
    effective_resume_at: datetime | None = None

    def __post_init__(self) -> None:
        for field_name in ("app_name", "bundle_id", "window_title", "rule"):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name, sanitize_reason_value(value))

    @property
    def state(self) -> ProtectionReasonState:
        if self.code in _FAILED_CODES:
            return ProtectionReasonState.FAILED
        if self.code in _PAUSED_CODES:
            return ProtectionReasonState.PAUSED
        return ProtectionReasonState.PROTECTED

    @property
    def priority(self) -> int:
        return _PRIORITIES[self.code]

    @property
    def match_kind(self) -> str | None:
        return {
            ProtectionReasonCode.APP_RULE: "app_name",
            ProtectionReasonCode.BUNDLE_RULE: "bundle_id",
            ProtectionReasonCode.WINDOW_TITLE_RULE: "window_title",
            ProtectionReasonCode.WINDOW_TITLE_UNKNOWN: "window_title",
        }.get(self.code)

    @property
    def inherited(self) -> bool:
        return self.code is ProtectionReasonCode.MODE_ALL_INHERITED

    def to_payload(self, detail: Literal["category", "exact"]) -> dict[str, object]:
        """Serialize the only two allowed reason-presentation detail levels."""
        payload: dict[str, object] = {"code": self.code.value, "display_id": self.display_id}
        if detail == "category":
            return payload
        if detail != "exact":
            raise ValueError(f"unsupported reason detail: {detail}")
        for field_name in (
            "source_display_id",
            "app_name",
            "bundle_id",
            "window_title",
            "rule",
        ):
            value = getattr(self, field_name)
            if value is not None:
                payload[field_name] = value
        if self.effective_resume_at is not None:
            payload["effective_resume_at"] = self.effective_resume_at.isoformat()
        return payload


@dataclass(frozen=True)
class DisplayProtectionReasons:
    """Immutable bounded views over reasons attached to displays or globally."""

    reasons: tuple[ProtectionReason, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "reasons", tuple(dict.fromkeys(self.reasons)))

    @classmethod
    def from_reasons(cls, reasons: tuple[ProtectionReason, ...] | list[ProtectionReason]) -> Self:
        return cls(tuple(reasons))

    def for_display(self, display_id: int | None) -> tuple[ProtectionReason, ...]:
        selected = (
            reason
            for reason in self.reasons
            if reason.display_id is None or reason.display_id == display_id
        )
        ordered = sorted(
            selected,
            key=lambda reason: (
                -reason.priority,
                reason.code.value,
                reason.source_display_id if reason.source_display_id is not None else -1,
                reason.rule or "",
                reason.app_name or "",
                reason.bundle_id or "",
                reason.window_title or "",
            ),
        )
        return tuple(ordered[:_MAX_REASONS_PER_DISPLAY])

    def primary_for_display(self, display_id: int | None) -> ProtectionReason | None:
        reasons = self.for_display(display_id)
        return reasons[0] if reasons else None

    def additional_count_for_display(self, display_id: int | None) -> int:
        return max(0, len(self.for_display(display_id)) - 1)
