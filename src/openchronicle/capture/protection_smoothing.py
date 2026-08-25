from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

from .protection import ProtectionSnapshot, ProtectionState

PROTECTED_PROMOTION_SECONDS: float = 0.8
SAFE_CONFIRMATION_SECONDS: float = 0.2


class ProtectionPresentationPhase(StrEnum):
    INACTIVE = "inactive"
    TRANSIENT_PROTECTED = "transient-protected"
    SUSTAINED_PROTECTED = "sustained-protected"
    CLEAR_PENDING = "clear-pending"
    BYPASS = "bypass"


class ProtectionSmoothingError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProtectionPresentationResult:
    snapshot: ProtectionSnapshot
    phase: ProtectionPresentationPhase
    next_deadline: float | None
    overlay_reasons_enabled: bool


class ProtectionPresentationSmoother:
    def __init__(
        self,
        *,
        promotion_seconds: float = PROTECTED_PROMOTION_SECONDS,
        safe_confirmation_seconds: float = SAFE_CONFIRMATION_SECONDS,
    ) -> None:
        if promotion_seconds < 0 or safe_confirmation_seconds < 0:
            raise ValueError("smoothing delays must be non-negative")
        self._promotion_seconds = promotion_seconds
        self._safe_confirmation_seconds = safe_confirmation_seconds
        self._episode_started_at: float | None = None
        self._clear_deadline: float | None = None
        self._last_effective_protected: ProtectionSnapshot | None = None
        self._last_generation: int | None = None
        self._last_overlay_reasons_enabled = False

    def _reset_episode(self) -> None:
        self._episode_started_at = None
        self._clear_deadline = None
        self._last_effective_protected = None
        self._last_overlay_reasons_enabled = False

    def reset(self) -> None:
        self._reset_episode()
        self._last_generation = None

    def resolve(
        self,
        raw_snapshot: ProtectionSnapshot,
        *,
        now: float,
    ) -> ProtectionPresentationResult:
        if (
            self._last_generation is not None
            and raw_snapshot.generation <= self._last_generation
        ):
            raise ProtectionSmoothingError(
                "snapshot generations must strictly increase"
            )
        self._last_generation = raw_snapshot.generation

        if raw_snapshot.state in (ProtectionState.PAUSED, ProtectionState.FAILED):
            self._reset_episode()
            return ProtectionPresentationResult(
                snapshot=raw_snapshot,
                phase=ProtectionPresentationPhase.BYPASS,
                next_deadline=None,
                overlay_reasons_enabled=True,
            )

        if raw_snapshot.state is ProtectionState.PROTECTED:
            if self._episode_started_at is None:
                self._episode_started_at = now
            self._clear_deadline = None
            promoted = now >= self._episode_started_at + self._promotion_seconds
            effective_style = raw_snapshot.indicator_style
            reasons_enabled = promoted and effective_style != "off"
            if not promoted and effective_style != "off":
                effective_style = "quiet-shield"
            effective = replace(raw_snapshot, indicator_style=effective_style)
            self._last_effective_protected = effective
            self._last_overlay_reasons_enabled = reasons_enabled
            return ProtectionPresentationResult(
                snapshot=effective,
                phase=(
                    ProtectionPresentationPhase.SUSTAINED_PROTECTED
                    if promoted
                    else ProtectionPresentationPhase.TRANSIENT_PROTECTED
                ),
                next_deadline=(
                    None
                    if promoted
                    else self._episode_started_at + self._promotion_seconds
                ),
                overlay_reasons_enabled=reasons_enabled,
            )

        if self._episode_started_at is None:
            if self._clear_deadline is not None or self._last_effective_protected is not None:
                raise ProtectionSmoothingError(
                    "inconsistent inactive episode state"
                )
            return ProtectionPresentationResult(
                snapshot=raw_snapshot,
                phase=ProtectionPresentationPhase.INACTIVE,
                next_deadline=None,
                overlay_reasons_enabled=True,
            )
        if self._last_effective_protected is None:
            raise ProtectionSmoothingError(
                "episode has no protected snapshot to hold"
            )

        if self._clear_deadline is None:
            self._clear_deadline = now + self._safe_confirmation_seconds
        if now < self._clear_deadline:
            held = replace(
                self._last_effective_protected,
                generation=raw_snapshot.generation,
                created_monotonic=raw_snapshot.created_monotonic,
                fresh_until=raw_snapshot.fresh_until,
                indicator_placement=raw_snapshot.indicator_placement,
            )
            self._last_effective_protected = held
            return ProtectionPresentationResult(
                snapshot=held,
                phase=ProtectionPresentationPhase.CLEAR_PENDING,
                next_deadline=self._clear_deadline,
                overlay_reasons_enabled=self._last_overlay_reasons_enabled,
            )

        self._reset_episode()
        return ProtectionPresentationResult(
            snapshot=raw_snapshot,
            phase=ProtectionPresentationPhase.INACTIVE,
            next_deadline=None,
            overlay_reasons_enabled=True,
        )
