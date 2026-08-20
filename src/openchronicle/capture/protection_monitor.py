"""Thread-safe, privacy-safe protection-state monitor for capture consumers."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from .. import config
from ..capture_pause import capture_is_paused
from ..config import CaptureConfig
from ..logger import get
from .privacy import WindowInventory, read_window_inventory
from .privacy_overlay import PrivacyOverlayClient
from .protection import ProtectionSnapshot, ProtectionState, build_protection_snapshot

logger = get("openchronicle.capture")


@dataclass(frozen=True)
class ProtectionDecision:
    snapshot: ProtectionSnapshot
    indicator_confirmed: bool


class PrivacyProtectionMonitor:
    """Publish fresh protection decisions from event-driven and watchdog refreshes."""

    def __init__(
        self,
        cfg: CaptureConfig,
        *,
        config_path: Path,
        overlay: PrivacyOverlayClient,
        inventory_reader: Callable[[], WindowInventory | None] = read_window_inventory,
        pause_reader: Callable[[], bool] = capture_is_paused,
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
            self._overlay.mark_terminal()
        threading.Thread(
            target=self._overlay.close,
            daemon=True,
            name="privacy-protection-overlay-close",
        ).start()

    def request_refresh(self) -> None:
        if not self._is_stopped():
            self._wake.set()

    def decision_for_capture(self, *, force: bool = True) -> ProtectionDecision:
        self._raise_if_stopped()
        with self._state_lock:
            current = self._decision
        if not force and current is not None and current.snapshot.fresh_until >= time.monotonic():
            return current
        return self._refresh()

    def _run(self) -> None:
        try:
            self._refresh()
        except RuntimeError:
            return
        while not self._stop.is_set():
            self._wake.wait(self._watchdog_seconds)
            self._wake.clear()
            if not self._stop.is_set():
                try:
                    self._refresh()
                except RuntimeError:
                    return

    def _refresh(self) -> ProtectionDecision:
        with self._refresh_lock:
            self._raise_if_stopped()

            self._reload_indicator_style()
            paused, inventory = self._read_protection_inputs()
            self._raise_if_stopped()
            now = time.monotonic()
            generation = self._generation + 1
            snapshot = build_protection_snapshot(
                replace(self._cfg, privacy_indicator_style=self._indicator_style),
                inventory,
                paused=paused,
                generation=generation,
                now=now,
            )
            self._raise_if_stopped()
            indicator_confirmed = self._render(snapshot)
            self._raise_if_stopped()
            decision = ProtectionDecision(snapshot, indicator_confirmed)
            with self._state_lock:
                self._raise_if_stopped()
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

    def _read_protection_inputs(self) -> tuple[bool, WindowInventory | None]:
        try:
            paused = self._pause_reader()
        except Exception as exc:  # A pause-read failure must not allow capture.
            logger.warning("privacy protection pause read failed: %s", type(exc).__name__)
            return False, None
        try:
            return paused, self._inventory_reader()
        except Exception as exc:  # Inventory metadata must never be written to logs.
            logger.warning("privacy protection inventory read failed: %s", type(exc).__name__)
            return paused, None

    def _render(self, snapshot: ProtectionSnapshot) -> bool:
        with self._lifecycle_lock:
            if self._stopped:
                return False
            self._before_overlay_call()
            try:
                if snapshot.indicator_style != "off" and snapshot.state is ProtectionState.INACTIVE:
                    return self._overlay.clear(snapshot.generation)
                return self._overlay.render(snapshot)
            except Exception as exc:  # Helper failure is reflected by acknowledgement, not exception text.
                logger.warning("privacy protection indicator failed: %s", type(exc).__name__)
                return False

    def _is_stopped(self) -> bool:
        with self._lifecycle_lock:
            return self._stopped

    def _raise_if_stopped(self) -> None:
        if self._is_stopped():
            raise RuntimeError("privacy protection monitor is stopped")
