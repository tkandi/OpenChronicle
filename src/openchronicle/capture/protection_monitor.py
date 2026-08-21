"""Thread-safe, privacy-safe protection-state monitor for capture consumers."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
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
from .privacy import (
    InventoryReadResult,
    ProtectionFailureReason,
    WindowInventory,
    read_window_inventory_result,
)
from .privacy_overlay import PrivacyOverlayClient
from .protection import (
    ProtectionSnapshot,
    ProtectionState,
    build_protection_snapshot,
    failure_requires_fail_closed,
)
from .protection_reason import ProtectionReason

logger = get("openchronicle.capture")
_MONITOR_JOIN_TIMEOUT = 0.25


@dataclass(frozen=True)
class ProtectionDecision:
    snapshot: ProtectionSnapshot
    indicator_confirmed: bool
    covered_request_epoch: int = 0


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
    ) -> None:
        self._cfg = cfg
        self._config_path = config_path
        self._overlay = overlay
        self._inventory_reader = inventory_reader
        self._pause_reader = pause_reader
        self._watchdog_seconds = max(0.0, watchdog_seconds)
        self._before_overlay_call = before_overlay_call or (lambda: None)
        self._indicator_style = cfg.privacy_indicator_style
        self._config_mtime_ns: int | None = None
        self._generation = 0
        self._requested_epoch = 0
        self._decision: ProtectionDecision | None = None

        self._state_lock = threading.Lock()
        self._refresh_lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = False
        self._stopped = False
        self._overlay_closed = False
        self._last_logged_failure: tuple[ProtectionFailureReason, bool] | None = None

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
            if self._overlay_closed:
                return
            self._overlay_closed = True
            monitor_thread = self._thread
        self._overlay.mark_terminal()
        if monitor_thread is not None and monitor_thread is not threading.current_thread():
            monitor_thread.join(timeout=_MONITOR_JOIN_TIMEOUT)
        self._overlay.close()

    def request_refresh(self) -> None:
        with self._lifecycle_lock:
            if self._stopped:
                return
            with self._state_lock:
                self._requested_epoch += 1
        self._wake.set()

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
                and current.snapshot.fresh_until >= time.monotonic()
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
            self._wake.wait(self._watchdog_seconds)
            self._wake.clear()
            if not self._stop.is_set():
                try:
                    self.decision_for_capture(force=True)
                except RuntimeError:
                    return

    def _refresh(self) -> ProtectionDecision:
        with self._refresh_lock:
            self._raise_if_stopped()

            self._reload_indicator_style()
            with self._state_lock:
                covered_request_epoch = self._requested_epoch
            paused, inventory, failure_reason, pause_reason = self._read_protection_inputs()
            self._raise_if_stopped()
            now = time.monotonic()
            generation = self._generation + 1
            snapshot = build_protection_snapshot(
                replace(self._cfg, privacy_indicator_style=self._indicator_style),
                inventory,
                paused=paused,
                generation=generation,
                now=now,
                failure_reason=failure_reason,
                pause_reason=pause_reason,
            )
            self._log_failure_transition(snapshot)
            self._raise_if_stopped()
            indicator_confirmed = self._render(snapshot)
            self._raise_if_stopped()
            decision = ProtectionDecision(
                snapshot,
                indicator_confirmed,
                covered_request_epoch=covered_request_epoch,
            )
            with self._lifecycle_lock:
                if self._stopped:
                    raise RuntimeError("privacy protection monitor is stopped")
                with self._state_lock:
                    self._generation = generation
                    self._decision = decision
            logger.debug(
                "privacy protection generation=%s state=%s style=%s displays=%s confirmed=%s",
                generation,
                snapshot.state.value,
                snapshot.indicator_style,
                sorted(snapshot.protected_display_ids),
                indicator_confirmed,
            )
            return decision

    def _reload_indicator_style(self) -> None:
        try:
            mtime_ns = self._config_path.stat().st_mtime_ns
        except OSError:
            return
        if self._config_mtime_ns == mtime_ns:
            return
        try:
            indicator_style = config.load(self._config_path).capture.privacy_indicator_style
        except (OSError, TypeError, ValueError) as exc:
            logger.warning("privacy protection style reload failed: %s", type(exc).__name__)
        else:
            self._indicator_style = indicator_style
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

    def _render(self, snapshot: ProtectionSnapshot) -> bool:
        with self._lifecycle_lock:
            if self._stopped:
                return False
        self._before_overlay_call()
        with self._lifecycle_lock:
            if self._stopped:
                return False
        try:
            if (
                snapshot.state is ProtectionState.FAILED
                and not failure_requires_fail_closed(self._cfg, snapshot)
            ):
                if snapshot.indicator_style == "off":
                    self._overlay.render(snapshot)
                else:
                    self._overlay.clear(snapshot.generation)
                return False
            if snapshot.indicator_style != "off" and snapshot.state is ProtectionState.INACTIVE:
                return self._overlay.clear(snapshot.generation)
            return self._overlay.render(snapshot)
        except Exception as exc:  # Helper failure is reflected by acknowledgement, not exception text.
            logger.warning("privacy protection indicator failed: %s", type(exc).__name__)
            return False

    def _log_failure_transition(self, snapshot: ProtectionSnapshot) -> None:
        if snapshot.state is not ProtectionState.FAILED or snapshot.failure_reason is None:
            self._last_logged_failure = None
            return
        requires_fail_closed = failure_requires_fail_closed(self._cfg, snapshot)
        key = (snapshot.failure_reason, requires_fail_closed)
        if key == self._last_logged_failure:
            return
        self._last_logged_failure = key
        if requires_fail_closed:
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
