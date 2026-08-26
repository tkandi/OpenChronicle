from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

from .privacy import ProtectionFailureReason
from .protection import ProtectionSnapshot, ProtectionState
from .protection_reason import ProtectionReasonCode

PROTECTED_PROMOTION_SECONDS: float = 0.8
SAFE_CONFIRMATION_SECONDS: float = 0.2
PRESENTATION_SMOOTHED_FAILURES = frozenset(
    {
        ProtectionFailureReason.ACTIVE_WINDOW_UNMAPPED,
        ProtectionFailureReason.SENSITIVE_WINDOW_UNMAPPED,
    }
)
_TITLE_UNCERTAINTY_CODES = frozenset(
    {
        ProtectionReasonCode.WINDOW_TITLE_UNKNOWN,
        ProtectionReasonCode.MODE_ALL_INHERITED,
    }
)


def _is_smoothed_mapping_failure(snapshot: ProtectionSnapshot) -> bool:
    return (
        snapshot.state is ProtectionState.FAILED
        and snapshot.failure_reason in PRESENTATION_SMOOTHED_FAILURES
        and not snapshot.diagnostics_guard_invalid
    )


def _is_mapping_fallback(snapshot: ProtectionSnapshot) -> bool:
    return (
        snapshot.state is ProtectionState.PROTECTED
        and snapshot.display_mapping_fallback_active
    )


def _is_title_uncertainty_only(snapshot: ProtectionSnapshot) -> bool:
    if (
        snapshot.state is not ProtectionState.PROTECTED
        or snapshot.display_mapping_fallback_active
    ):
        return False
    codes = {reason.code for reason in snapshot.display_reasons.reasons}
    return (
        ProtectionReasonCode.WINDOW_TITLE_UNKNOWN in codes
        and codes <= _TITLE_UNCERTAINTY_CODES
    )


class ProtectionPresentationPhase(StrEnum):
    INACTIVE = "inactive"
    TRANSIENT_PROTECTED = "transient-protected"
    SUSTAINED_PROTECTED = "sustained-protected"
    TRANSIENT_MAPPING_FALLBACK = "transient-mapping-fallback"
    SUSTAINED_MAPPING_FALLBACK = "sustained-mapping-fallback"
    TRANSIENT_MAPPING_FAILURE = "transient-mapping-failure"
    SUSTAINED_MAPPING_FAILURE = "sustained-mapping-failure"
    TRANSIENT_TITLE_UNCERTAINTY = "transient-title-uncertainty"
    SUSTAINED_TITLE_UNCERTAINTY = "sustained-title-uncertainty"
    CLEAR_PENDING = "clear-pending"
    BYPASS = "bypass"


def _risk_phase(
    *,
    promoted: bool,
    mapping_fallback: bool,
    mapping_failure: bool,
    title_uncertainty: bool,
) -> ProtectionPresentationPhase:
    if mapping_fallback:
        return (
            ProtectionPresentationPhase.SUSTAINED_MAPPING_FALLBACK
            if promoted
            else ProtectionPresentationPhase.TRANSIENT_MAPPING_FALLBACK
        )
    if mapping_failure:
        return (
            ProtectionPresentationPhase.SUSTAINED_MAPPING_FAILURE
            if promoted
            else ProtectionPresentationPhase.TRANSIENT_MAPPING_FAILURE
        )
    if title_uncertainty:
        return (
            ProtectionPresentationPhase.SUSTAINED_TITLE_UNCERTAINTY
            if promoted
            else ProtectionPresentationPhase.TRANSIENT_TITLE_UNCERTAINTY
        )
    return (
        ProtectionPresentationPhase.SUSTAINED_PROTECTED
        if promoted
        else ProtectionPresentationPhase.TRANSIENT_PROTECTED
    )


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
        self._last_effective_risk: ProtectionSnapshot | None = None
        self._last_generation: int | None = None
        self._last_overlay_reasons_enabled = False

    def _reset_episode(self) -> None:
        self._episode_started_at = None
        self._clear_deadline = None
        self._last_effective_risk = None
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

        mapping_failure = _is_smoothed_mapping_failure(raw_snapshot)
        mapping_fallback = _is_mapping_fallback(raw_snapshot)
        title_uncertainty = _is_title_uncertainty_only(raw_snapshot)
        risk_active = raw_snapshot.state is ProtectionState.PROTECTED or mapping_failure
        hard_bypass = raw_snapshot.state is ProtectionState.PAUSED or (
            raw_snapshot.state is ProtectionState.FAILED and not mapping_failure
        )

        if hard_bypass:
            self._reset_episode()
            return ProtectionPresentationResult(
                snapshot=raw_snapshot,
                phase=ProtectionPresentationPhase.BYPASS,
                next_deadline=None,
                overlay_reasons_enabled=True,
            )

        if risk_active:
            if self._episode_started_at is None:
                self._episode_started_at = now
            self._clear_deadline = None
            promoted = now >= self._episode_started_at + self._promotion_seconds
            effective_style = raw_snapshot.indicator_style
            reasons_enabled = promoted and effective_style != "off"
            if not promoted and effective_style != "off":
                effective_style = (
                    "off"
                    if mapping_fallback or mapping_failure or title_uncertainty
                    else "quiet-shield"
                )
            effective = replace(raw_snapshot, indicator_style=effective_style)
            self._last_effective_risk = effective
            self._last_overlay_reasons_enabled = reasons_enabled
            return ProtectionPresentationResult(
                snapshot=effective,
                phase=_risk_phase(
                    promoted=promoted,
                    mapping_fallback=mapping_fallback,
                    mapping_failure=mapping_failure,
                    title_uncertainty=title_uncertainty,
                ),
                next_deadline=(
                    None
                    if promoted
                    else self._episode_started_at + self._promotion_seconds
                ),
                overlay_reasons_enabled=reasons_enabled,
            )

        if self._episode_started_at is None:
            if (
                self._clear_deadline is not None
                or self._last_effective_risk is not None
                or self._last_overlay_reasons_enabled
            ):
                raise ProtectionSmoothingError(
                    "inconsistent inactive episode state"
                )
            return ProtectionPresentationResult(
                snapshot=raw_snapshot,
                phase=ProtectionPresentationPhase.INACTIVE,
                next_deadline=None,
                overlay_reasons_enabled=True,
            )
        if self._last_effective_risk is None:
            raise ProtectionSmoothingError(
                "episode has no risk snapshot to hold"
            )
        if (
            self._last_effective_risk.state is not ProtectionState.PROTECTED
            and not _is_smoothed_mapping_failure(self._last_effective_risk)
        ):
            raise ProtectionSmoothingError("episode has invalid risk snapshot")

        if self._clear_deadline is None:
            self._clear_deadline = now + self._safe_confirmation_seconds
        if now < self._clear_deadline:
            held = replace(
                self._last_effective_risk,
                generation=raw_snapshot.generation,
                created_monotonic=raw_snapshot.created_monotonic,
                fresh_until=raw_snapshot.fresh_until,
                indicator_placement=raw_snapshot.indicator_placement,
            )
            self._last_effective_risk = held
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
