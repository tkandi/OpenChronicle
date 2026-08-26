"""Thread-safe, privacy-safe protection-state monitor for capture consumers."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

from .. import config
from ..capture_pause import (
    CapturePauseDecision,
    CapturePauseKind,
    capture_pause_decision_strict,
    pause_reason_from_decision,
)
from ..config import CaptureConfig
from ..logger import get
from . import privacy
from .privacy import (
    InventoryReadResult,
    ProtectionFailureReason,
    WindowInventory,
    read_window_inventory_result,
)
from .privacy_diagnostics_guard import DiagnosticsGuardSnapshot
from .privacy_overlay import PrivacyOverlayClient
from .protection import (
    SNAPSHOT_FRESH_SECONDS,
    ProtectionSnapshot,
    ProtectionState,
    build_protection_snapshot,
    failure_requires_fail_closed,
)
from .protection_reason import (
    DisplayProtectionReasons,
    ProtectionReason,
    ProtectionReasonCode,
)
from .protection_smoothing import (
    ProtectionPresentationPhase,
    ProtectionPresentationSmoother,
    ProtectionSmoothingError,
)
from .window_display_history import WindowDisplayHistory, WindowDisplayHistoryError

logger = get("openchronicle.capture")
_MONITOR_JOIN_TIMEOUT = 0.25
_OVERLAY_DRAIN_TIMEOUT_SECONDS = 0.75


@dataclass(frozen=True)
class ProtectionDecision:
    snapshot: ProtectionSnapshot
    indicator_confirmed: bool
    covered_request_epoch: int = 0
    indicator_window_ids: tuple[int, ...] = ()
    failure_capture_blocked: bool = field(default=True, kw_only=True)
    raw_state: ProtectionState | None = field(default=None, kw_only=True)
    presentation_phase: ProtectionPresentationPhase = field(
        default=ProtectionPresentationPhase.BYPASS,
        kw_only=True,
    )
    overlay_reasons_enabled: bool = field(default=True, kw_only=True)
    presentation_deadline_monotonic: float | None = field(default=None, kw_only=True)

    @property
    def capture_confirmation_satisfied(self) -> bool:
        return (
            self.snapshot.indicator_style == "off"
            or self.indicator_confirmed
            or (
                self.snapshot.state is ProtectionState.FAILED
                and not self.failure_capture_blocked
            )
        )


class PrivacyProtectionMonitor:
    """Publish fresh protection decisions from event-driven and watchdog refreshes."""

    def __init__(
        self,
        cfg: CaptureConfig,
        *,
        config_path: Path,
        overlay: PrivacyOverlayClient,
        inventory_reader: Callable[[], WindowInventory | InventoryReadResult | None] = read_window_inventory_result,
        pause_reader: Callable[[], CapturePauseDecision | bool] = capture_pause_decision_strict,
        watchdog_seconds: float = 1.0,
        before_overlay_call: Callable[[], None] | None = None,
        diagnostics_guard_reader: Callable[[], DiagnosticsGuardSnapshot] | None = None,
        diagnostics_guard_only: bool = False,
        decision_listener: Callable[[ProtectionDecision], None] | None = None,
        smoother: ProtectionPresentationSmoother | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        window_display_history: WindowDisplayHistory | None = None,
    ) -> None:
        self._cfg = cfg
        self._config_path = config_path
        self._overlay = overlay
        self._inventory_reader = inventory_reader
        self._pause_reader = pause_reader
        self._watchdog_seconds = max(0.0, watchdog_seconds)
        self._before_overlay_call = before_overlay_call or (lambda: None)
        self._diagnostics_guard_reader = diagnostics_guard_reader
        self._diagnostics_guard_only = diagnostics_guard_only
        self._smoother = (
            smoother if smoother is not None else ProtectionPresentationSmoother()
        )
        self._monotonic = monotonic
        self._window_display_history = (
            window_display_history
            if window_display_history is not None
            else WindowDisplayHistory()
        )
        self._window_display_history_lock = threading.Lock()
        self._indicator_style = cfg.privacy_indicator_style
        self._indicator_placement = cfg.privacy_indicator_placement
        self._config_mtime_ns: int | None = None
        self._generation = 0
        self._requested_epoch = 0
        self._decision: ProtectionDecision | None = None
        self._next_smoothing_deadline: float | None = None

        self._state_lock = threading.Lock()
        self._decision_condition = threading.Condition(self._state_lock)
        self._refresh_lock = threading.Lock()
        self._listeners_lock = threading.Lock()
        self._decision_listeners = (
            [decision_listener] if decision_listener is not None else []
        )
        self._lifecycle_lock = threading.Lock()
        self._listener_dispatch_condition = threading.Condition(threading.Lock())
        self._listener_dispatches_blocked = False
        self._listener_dispatches_in_flight = 0
        self._listener_dispatches_by_thread: dict[int, int] = {}
        self._overlay_call_condition = threading.Condition(threading.Lock())
        self._overlay_calls_blocked = False
        self._overlay_calls_in_flight = 0
        self._overlay_close_deferred = False
        self._overlay_close_started = False
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = False
        self._stopped = False
        self._overlay_closed = False
        self._last_logged_failure: tuple[str, bool] | None = None

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._started or self._stopped:
                return
            self._started = True
            self._thread = threading.Thread(
                target=self._run,
                daemon=True,
                name="privacy-protection-monitor",
            )
            self._thread.start()

    def stop(self) -> None:
        with self._lifecycle_lock:
            self._stopped = True
            self._stop.set()
            self._wake.set()
            self._reset_window_display_history()
            with self._listener_dispatch_condition:
                self._listener_dispatches_blocked = True
            close_overlay = not self._overlay_closed
            if close_overlay:
                self._overlay_closed = True
                monitor_thread = self._thread
            else:
                monitor_thread = None
            with self._overlay_call_condition:
                self._overlay_calls_blocked = True
        with self._decision_condition:
            self._next_smoothing_deadline = None
            self._decision_condition.notify_all()
        if close_overlay:
            self._overlay.mark_terminal()
            drained = self._drain_overlay_calls()
            if drained:
                self._close_overlay_once()
            else:
                logger.warning(
                    "privacy protection indicator drain timed out: "
                    "category=overlay_call_in_flight"
                )
            if (
                drained
                and monitor_thread is not None
                and monitor_thread is not threading.current_thread()
            ):
                monitor_thread.join(timeout=_MONITOR_JOIN_TIMEOUT)
        self._drain_listener_dispatches()

    def request_refresh(self) -> None:
        with self._lifecycle_lock:
            if self._stopped:
                return
            with self._state_lock:
                self._requested_epoch += 1
        self._wake.set()

    def add_decision_listener(
        self,
        listener: Callable[[ProtectionDecision], None],
    ) -> None:
        """Register a post-publication listener without exposing monitor locks."""
        with self._listeners_lock:
            if listener not in self._decision_listeners:
                self._decision_listeners.append(listener)

    def wait_for_display_protection(
        self,
        display_id: int,
        after_generation: int,
        timeout: float,
    ) -> int | None:
        """Wait for a newer, acknowledged decision protecting one display."""
        deadline = time.monotonic() + max(0.0, timeout)
        with self._decision_condition:
            while True:
                current = self._decision
                if (
                    current is not None
                    and current.snapshot.generation > after_generation
                    and display_id in current.snapshot.protected_display_ids
                    and current.indicator_confirmed
                ):
                    return current.snapshot.generation
                if self._stop.is_set():
                    return None
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._decision_condition.wait(remaining)

    def decision_for_capture(self, *, force: bool = True) -> ProtectionDecision:
        must_refresh = force
        while True:
            self._raise_if_stopped()
            if must_refresh:
                self._refresh()
                must_refresh = False
            with self._state_lock:
                current = self._decision
                requested_epoch = self._requested_epoch
            if (
                current is not None
                and current.covered_request_epoch >= requested_epoch
                and current.snapshot.fresh_until >= self._monotonic()
            ):
                self._raise_if_stopped()
                return current
            self._refresh()

    def _run(self) -> None:
        try:
            self.decision_for_capture(force=True)
        except RuntimeError:
            return
        while not self._stop.is_set():
            timeout = self._watchdog_seconds
            with self._state_lock:
                deadline = self._next_smoothing_deadline
            if deadline is not None:
                timeout = min(
                    timeout,
                    max(0.0, deadline - self._monotonic()),
                )
            self._wake.wait(timeout)
            self._wake.clear()
            if not self._stop.is_set():
                try:
                    self.decision_for_capture(force=True)
                except RuntimeError:
                    return

    def _refresh(self) -> ProtectionDecision:
        with self._refresh_lock:
            self._raise_if_stopped()

            self._reload_indicator_settings()
            with self._state_lock:
                covered_request_epoch = self._requested_epoch
            diagnostics_guard = self._read_diagnostics_guard()
            self._raise_if_stopped()
            now = self._monotonic()
            generation = self._generation + 1
            if self._diagnostics_guard_only and not (
                diagnostics_guard.fail_closed_all or diagnostics_guard.display_ids
            ):
                raw_snapshot = ProtectionSnapshot(
                    generation=generation,
                    state=ProtectionState.INACTIVE,
                    capture_mode=self._cfg.screenshot_monitor,
                    indicator_style=self._indicator_style,
                    displays=(),
                    protected_display_ids=frozenset(),
                    active_display_id=None,
                    created_monotonic=now,
                    fresh_until=now + SNAPSHOT_FRESH_SECONDS,
                    indicator_placement=self._indicator_placement,
                    reason_display=self._cfg.privacy_reason_display,
                    reason_detail=self._cfg.privacy_reason_detail,
                    reason_trigger=self._cfg.privacy_reason_trigger,
                )
            else:
                paused, inventory, failure_reason, pause_reason = self._read_protection_inputs()
                self._raise_if_stopped()
                snapshot_cfg = replace(
                    self._cfg,
                    privacy_indicator_style=self._indicator_style,
                    privacy_indicator_placement=self._indicator_placement,
                )
                if self._diagnostics_guard_only:
                    snapshot_cfg = replace(
                        snapshot_cfg,
                        deny_app_names=[],
                        deny_bundle_ids=[],
                        deny_window_title_patterns=[],
                    )
                diagnostic_display_ids = diagnostics_guard.display_ids
                diagnostics_guard_invalid = diagnostics_guard.fail_closed_all
                if inventory is not None and failure_reason is None:
                    structure_failure = privacy.inventory_structure_failure_reason(inventory)
                    if structure_failure is not None:
                        inventory = None
                        failure_reason = structure_failure
                if inventory is not None and failure_reason is None:
                    try:
                        inventory = self._resolve_window_display_history(
                            inventory,
                            now=now,
                        )
                    except WindowDisplayHistoryError:
                        logger.warning(
                            "privacy window display history failed: "
                            "WindowDisplayHistoryError"
                        )
                        self._reset_window_display_history()
                        paused = False
                        inventory = None
                        failure_reason = (
                            ProtectionFailureReason.PRESENTATION_STATE_INVALID
                        )
                        pause_reason = None
                        diagnostic_display_ids = frozenset()
                        diagnostics_guard_invalid = False
                    self._reset_history_if_stopped()
                raw_snapshot = build_protection_snapshot(
                    snapshot_cfg,
                    inventory,
                    paused=paused,
                    generation=generation,
                    now=now,
                    failure_reason=failure_reason,
                    pause_reason=pause_reason,
                    diagnostic_display_ids=diagnostic_display_ids,
                    diagnostics_guard_invalid=diagnostics_guard_invalid,
                )
            try:
                result = self._smoother.resolve(raw_snapshot, now=now)
            except ProtectionSmoothingError:
                logger.warning(
                    "privacy protection smoothing failed: ProtectionSmoothingError"
                )
                self._smoother.reset()
                snapshot = replace(
                    raw_snapshot,
                    state=ProtectionState.FAILED,
                    failure_reason=ProtectionFailureReason.PRESENTATION_STATE_INVALID,
                    protected_display_ids=frozenset(),
                    active_candidate_display_ids=frozenset(),
                    display_reasons=DisplayProtectionReasons.from_reasons(
                        [
                            ProtectionReason(
                                ProtectionReasonCode.PRESENTATION_STATE_INVALID,
                                None,
                            )
                        ]
                    ),
                    protected_window_ids=frozenset(),
                    protected_window_regions=(),
                    window_filterable=False,
                )
                phase = ProtectionPresentationPhase.BYPASS
                overlay_reasons_enabled = True
                next_smoothing_deadline = None
                raw_state = raw_snapshot.state
            else:
                snapshot = result.snapshot
                phase = result.phase
                overlay_reasons_enabled = result.overlay_reasons_enabled
                next_smoothing_deadline = result.next_deadline
                raw_state = raw_snapshot.state
            failure_capture_blocked = failure_requires_fail_closed(self._cfg, snapshot)
            self._log_failure_transition(
                snapshot,
                failure_capture_blocked=failure_capture_blocked,
            )
            self._raise_if_stopped()
            rendered = self._render(
                snapshot,
                failure_capture_blocked=failure_capture_blocked,
                overlay_reasons_enabled=overlay_reasons_enabled,
            )
            indicator_confirmed = rendered or snapshot.indicator_style == "off"
            indicator_window_ids = self._confirmed_indicator_window_ids(
                snapshot,
                rendered=rendered,
            )
            if not indicator_confirmed and not (
                snapshot.state is ProtectionState.FAILED
                and not failure_capture_blocked
            ):
                snapshot = replace(
                    snapshot,
                    display_reasons=DisplayProtectionReasons.from_reasons(
                        snapshot.display_reasons.reasons
                        + (
                            ProtectionReason(
                                ProtectionReasonCode.INDICATOR_UNCONFIRMED,
                                display_id=None,
                            ),
                        )
                    ),
                )
            self._raise_if_stopped()
            decision = ProtectionDecision(
                snapshot,
                indicator_confirmed,
                covered_request_epoch=covered_request_epoch,
                indicator_window_ids=indicator_window_ids,
                failure_capture_blocked=failure_capture_blocked,
                raw_state=raw_state,
                presentation_phase=phase,
                overlay_reasons_enabled=overlay_reasons_enabled,
                presentation_deadline_monotonic=next_smoothing_deadline,
            )
            with self._lifecycle_lock:
                if self._stopped:
                    raise RuntimeError("privacy protection monitor is stopped")
                with self._decision_condition:
                    previous_smoothing_deadline = self._next_smoothing_deadline
                    self._generation = generation
                    self._decision = decision
                    self._next_smoothing_deadline = next_smoothing_deadline
                    wake_worker_for_deadline = (
                        previous_smoothing_deadline != next_smoothing_deadline
                        and self._thread is not None
                        and threading.current_thread() is not self._thread
                    )
                    self._decision_condition.notify_all()
            if wake_worker_for_deadline:
                self._wake.set()
            logger.debug(
                "privacy protection generation=%s state=%s style=%s placement=%s displays=%s confirmed=%s",
                generation,
                snapshot.state.value,
                snapshot.indicator_style,
                snapshot.indicator_placement,
                sorted(snapshot.protected_display_ids),
                indicator_confirmed,
            )
        self._notify_decision_listeners(decision)
        return decision

    def _read_diagnostics_guard(self) -> DiagnosticsGuardSnapshot:
        if self._diagnostics_guard_reader is None:
            return DiagnosticsGuardSnapshot(frozenset(), False)
        try:
            snapshot = self._diagnostics_guard_reader()
        except Exception as exc:
            logger.warning("privacy diagnostics guard read failed: %s", type(exc).__name__)
            return DiagnosticsGuardSnapshot(frozenset(), True)
        if not isinstance(snapshot, DiagnosticsGuardSnapshot):
            logger.warning("privacy diagnostics guard read failed: invalid_snapshot")
            return DiagnosticsGuardSnapshot(frozenset(), True)
        return snapshot

    def _notify_decision_listeners(self, decision: ProtectionDecision) -> None:
        with self._listeners_lock:
            listeners = tuple(self._decision_listeners)
        for listener in listeners:
            if not self._begin_listener_dispatch():
                return
            try:
                listener(decision)
            except Exception as exc:
                logger.warning("privacy protection listener failed: %s", type(exc).__name__)
            finally:
                self._end_listener_dispatch()

    def _begin_listener_dispatch(self) -> bool:
        with self._lifecycle_lock, self._listener_dispatch_condition:
            if self._listener_dispatches_blocked:
                return False
            thread_id = threading.get_ident()
            self._listener_dispatches_in_flight += 1
            self._listener_dispatches_by_thread[thread_id] = (
                self._listener_dispatches_by_thread.get(thread_id, 0) + 1
            )
            return True

    def _end_listener_dispatch(self) -> None:
        thread_id = threading.get_ident()
        with self._listener_dispatch_condition:
            self._listener_dispatches_in_flight -= 1
            current_thread_dispatches = self._listener_dispatches_by_thread[thread_id] - 1
            if current_thread_dispatches:
                self._listener_dispatches_by_thread[thread_id] = current_thread_dispatches
            else:
                del self._listener_dispatches_by_thread[thread_id]
            self._listener_dispatch_condition.notify_all()

    def _drain_listener_dispatches(self) -> None:
        thread_id = threading.get_ident()
        with self._listener_dispatch_condition:
            current_thread_dispatches = self._listener_dispatches_by_thread.get(
                thread_id,
                0,
            )
            while self._listener_dispatches_in_flight > current_thread_dispatches:
                self._listener_dispatch_condition.wait()

    def _reload_indicator_settings(self) -> None:
        try:
            mtime_ns = self._config_path.stat().st_mtime_ns
        except OSError:
            return
        if self._config_mtime_ns == mtime_ns:
            return
        try:
            capture = config.load(self._config_path).capture
        except (OSError, TypeError, ValueError) as exc:
            logger.warning("privacy protection settings reload failed: %s", type(exc).__name__)
            return
        self._indicator_style = capture.privacy_indicator_style
        self._indicator_placement = capture.privacy_indicator_placement
        self._config_mtime_ns = mtime_ns

    def _read_protection_inputs(
        self,
    ) -> tuple[
        bool,
        WindowInventory | None,
        ProtectionFailureReason | None,
        ProtectionReason | None,
    ]:
        try:
            pause_decision = self._normalize_pause_decision(self._pause_reader())
        except Exception as exc:  # A pause-read failure must not allow capture.
            logger.warning("privacy protection pause read failed: %s", type(exc).__name__)
            return False, None, ProtectionFailureReason.PAUSE_STATE_UNAVAILABLE, None
        paused = pause_decision.paused
        pause_reason = pause_reason_from_decision(pause_decision)
        try:
            result = self._inventory_reader()
        except Exception:  # Inventory metadata and exception text must never be logged.
            return paused, None, ProtectionFailureReason.INVENTORY_UNAVAILABLE, pause_reason
        if isinstance(result, InventoryReadResult):
            return paused, result.inventory, result.failure_reason, pause_reason
        return (
            paused,
            result,
            ProtectionFailureReason.INVENTORY_UNAVAILABLE if result is None else None,
            pause_reason,
        )

    def _resolve_window_display_history(
        self,
        inventory: WindowInventory,
        *,
        now: float,
    ) -> WindowInventory:
        with self._window_display_history_lock:
            return self._window_display_history.resolve(inventory, now=now)

    def _reset_window_display_history(self) -> None:
        with self._window_display_history_lock:
            self._window_display_history.reset()

    def _reset_history_if_stopped(self) -> None:
        if not self._is_stopped():
            return
        self._reset_window_display_history()
        self._raise_if_stopped()

    @staticmethod
    def _normalize_pause_decision(value: CapturePauseDecision | bool) -> CapturePauseDecision:
        if isinstance(value, CapturePauseDecision):
            return value
        if isinstance(value, bool):
            return CapturePauseDecision(
                paused=value,
                kind=CapturePauseKind.INDEFINITE if value else CapturePauseKind.NOT_PAUSED,
            )
        raise TypeError("pause reader returned an unsupported decision")

    def _render(
        self,
        snapshot: ProtectionSnapshot,
        *,
        failure_capture_blocked: bool,
        overlay_reasons_enabled: bool,
    ) -> bool:
        with self._lifecycle_lock:
            if self._stopped:
                return False
        self._before_overlay_call()
        if not self._begin_overlay_call():
            return False
        try:
            if (
                snapshot.state is ProtectionState.FAILED
                and not failure_capture_blocked
            ):
                if snapshot.indicator_style == "off":
                    self._overlay.render(
                        snapshot,
                        overlay_reasons_enabled=overlay_reasons_enabled,
                    )
                else:
                    self._overlay.clear(snapshot.generation)
                return False
            if snapshot.indicator_style != "off" and snapshot.state is ProtectionState.INACTIVE:
                return self._overlay.clear(snapshot.generation)
            return self._overlay.render(
                snapshot,
                overlay_reasons_enabled=overlay_reasons_enabled,
            )
        except Exception as exc:  # Helper failure is reflected by acknowledgement, not exception text.
            logger.warning("privacy protection indicator failed: %s", type(exc).__name__)
            return False
        finally:
            self._end_overlay_call()

    def _begin_overlay_call(self) -> bool:
        with self._lifecycle_lock:
            if self._stopped:
                return False
            with self._overlay_call_condition:
                if self._overlay_calls_blocked:
                    return False
                self._overlay_calls_in_flight += 1
                return True

    def _end_overlay_call(self) -> None:
        with self._overlay_call_condition:
            self._overlay_calls_in_flight -= 1
            if self._overlay_calls_in_flight == 0:
                self._overlay_call_condition.notify_all()
                close_deferred = self._overlay_close_deferred
            else:
                close_deferred = False
        if close_deferred:
            self._close_overlay_once()

    def _drain_overlay_calls(self) -> bool:
        deadline = time.monotonic() + _OVERLAY_DRAIN_TIMEOUT_SECONDS
        with self._overlay_call_condition:
            while self._overlay_calls_in_flight:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._overlay_close_deferred = True
                    return False
                self._overlay_call_condition.wait(remaining)
            return True

    def _close_overlay_once(self) -> None:
        with self._overlay_call_condition:
            if self._overlay_close_started:
                return
            if self._overlay_calls_in_flight:
                self._overlay_close_deferred = True
                return
            self._overlay_close_started = True
            self._overlay_close_deferred = False
        try:
            self._overlay.close()
        except Exception:  # noqa: BLE001 - close failures must remain fixed-category only.
            logger.warning(
                "privacy protection indicator close failed: category=overlay_close_failed"
            )

    def _confirmed_indicator_window_ids(
        self,
        snapshot: ProtectionSnapshot,
        *,
        rendered: bool,
    ) -> tuple[int, ...]:
        if not rendered or snapshot.indicator_style == "off":
            return ()
        getter = getattr(self._overlay, "confirmed_window_ids", None)
        if getter is None:
            return ()
        try:
            window_ids = tuple(getter(snapshot.generation))
        except Exception:  # noqa: BLE001 - ID retrieval failure must stay private and fail closed.
            return ()
        if (
            len(set(window_ids)) != len(window_ids)
            or any(
                not isinstance(window_id, int)
                or isinstance(window_id, bool)
                or not 0 < window_id <= 0xFFFFFFFF
                for window_id in window_ids
            )
        ):
            return ()
        return tuple(sorted(window_ids))

    def _log_failure_transition(
        self,
        snapshot: ProtectionSnapshot,
        *,
        failure_capture_blocked: bool,
    ) -> None:
        if snapshot.diagnostics_guard_invalid:
            key = ("diagnostics_guard_invalid", True)
            if key != self._last_logged_failure:
                self._last_logged_failure = key
                logger.warning(
                    "privacy protection failed closed: reason=diagnostics_guard_invalid"
                )
            return
        if snapshot.state is not ProtectionState.FAILED or snapshot.failure_reason is None:
            self._last_logged_failure = None
            return
        key = (snapshot.failure_reason.value, failure_capture_blocked)
        if key == self._last_logged_failure:
            return
        self._last_logged_failure = key
        if failure_capture_blocked:
            logger.warning(
                "privacy protection failed closed: reason=%s",
                snapshot.failure_reason.value,
            )
        else:
            logger.warning(
                "privacy protection unavailable: reason=%s policy=fail_open",
                snapshot.failure_reason.value,
            )

    def _is_stopped(self) -> bool:
        with self._lifecycle_lock:
            return self._stopped

    def _raise_if_stopped(self) -> None:
        if self._is_stopped():
            raise RuntimeError("privacy protection monitor is stopped")
