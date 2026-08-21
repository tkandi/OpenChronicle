"""Privacy-safe client for the native macOS protection indicator."""

from __future__ import annotations

import contextlib
import json
import os
import platform
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from ..logger import get
from .protection import ProtectionSnapshot, ProtectionState

logger = get("openchronicle.capture")
_INITIAL_RESTART_DELAY = 1.0
_MAX_RESTART_DELAY = 30.0
_CLOSE_TIMEOUT = 1.0
_REASON_DISPLAY_MODES = frozenset({"overlay", "diagnostics", "hybrid"})
_REASON_DETAIL_MODES = frozenset({"category", "exact", "tiered"})
_REASON_TRIGGERS = frozenset({"always", "hover", "click"})


class OverlayTransport(Protocol):
    def send_and_wait(self, line: str, generation: int, timeout: float) -> bool: ...

    def close(self) -> None: ...


def _is_executable(path: Path) -> bool:
    try:
        return path.is_file() and os.access(path, os.X_OK)
    except OSError:
        return False


def _sources_are_fresh(
    reason_path: Path,
    core_path: Path,
    main_path: Path,
    binary_path: Path,
) -> bool:
    try:
        return (
            _is_executable(binary_path)
            and binary_path.stat().st_mtime >= reason_path.stat().st_mtime
            and binary_path.stat().st_mtime >= core_path.stat().st_mtime
            and binary_path.stat().st_mtime >= main_path.stat().st_mtime
        )
    except OSError:
        return False


def _maybe_compile_overlay(
    reason_path: Path,
    core_path: Path,
    main_path: Path,
    binary_path: Path,
) -> Path | None:
    """Build the helper atomically and return only a confirmed-fresh executable."""
    try:
        if not reason_path.is_file() or not core_path.is_file() or not main_path.is_file():
            return None
        if _sources_are_fresh(reason_path, core_path, main_path, binary_path):
            return binary_path

        cache = Path(tempfile.gettempdir()) / "openchronicle-clang-cache"
        cache.mkdir(parents=True, exist_ok=True)
        build_dir = Path(tempfile.mkdtemp(prefix=".privacy-overlay-", dir=binary_path.parent))
    except OSError:
        logger.warning("privacy overlay helper preparation unavailable")
        return None

    temporary_binary = build_dir / "mac-privacy-overlay"
    try:
        env = os.environ.copy()
        env["CLANG_MODULE_CACHE_PATH"] = str(cache)
        arch = "arm64" if platform.machine() in ("arm64", "aarch64") else "x86_64"
        result = subprocess.run(
            [
                "swiftc",
                str(reason_path),
                str(core_path),
                str(main_path),
                "-o",
                str(temporary_binary),
                "-O",
                "-target",
                f"{arch}-apple-macos12.0",
                "-swift-version",
                "5",
                "-framework",
                "AppKit",
            ],
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )
        if result.returncode != 0 or not _is_executable(temporary_binary):
            logger.warning("privacy overlay helper compilation failed")
            return None
        os.replace(temporary_binary, binary_path)
        return (
            binary_path
            if _sources_are_fresh(reason_path, core_path, main_path, binary_path)
            else None
        )
    except (OSError, FileNotFoundError, subprocess.TimeoutExpired):
        logger.warning("privacy overlay helper compilation unavailable")
        return None
    finally:
        shutil.rmtree(build_dir, ignore_errors=True)


def _usable_overlay_binary(binary_path: Path) -> Path | None:
    parent = binary_path.parent
    reason_path = parent / "mac-privacy-overlay-reason.swift"
    core_path = parent / "mac-privacy-overlay-core.swift"
    main_path = parent / "mac-privacy-overlay.swift"
    try:
        reason_exists = reason_path.is_file()
        core_exists = core_path.is_file()
        main_exists = main_path.is_file()
    except OSError:
        return None
    if reason_exists or core_exists or main_exists:
        return _maybe_compile_overlay(reason_path, core_path, main_path, binary_path)
    return binary_path if _is_executable(binary_path) else None


def _resolve_overlay_path() -> Path | None:
    """Find or build the privacy-overlay helper without inspecting user content."""
    if platform.system() != "Darwin":
        return None

    override = os.environ.get("OPENCHRONICLE_PRIVACY_OVERLAY_HELPER")
    if override:
        try:
            path = Path(override).expanduser().resolve()
        except OSError:
            logger.warning("OPENCHRONICLE_PRIVACY_OVERLAY_HELPER is unavailable")
            return None
        usable = _usable_overlay_binary(path)
        if usable is not None:
            return path
        logger.warning("OPENCHRONICLE_PRIVACY_OVERLAY_HELPER is not executable")

    candidates: list[Path] = []
    try:
        from importlib.resources import files as package_files

        bundled_dir = Path(str(package_files("openchronicle").joinpath("_bundled")))
        candidates.append(bundled_dir / "mac-privacy-overlay")
    except (ModuleNotFoundError, OSError, ValueError):
        pass

    try:
        dev_root = Path(__file__).resolve().parents[3]
    except OSError:
        return None
    candidates.append(dev_root / "resources" / "mac-privacy-overlay")
    for binary_path in candidates:
        usable = _usable_overlay_binary(binary_path)
        if usable is not None:
            return usable
    return None


class _SubprocessOverlayTransport:
    """NDJSON transport backed by one helper process and one reader thread."""

    def __init__(self, helper_path: Path, *, interpreter: str | None = None) -> None:
        self._condition = threading.Condition()
        self._command_lock = threading.Lock()
        self._closed = False
        self._reader_finished = False
        self._protocol_failed = False
        self._pending_generation: int | None = None
        self._pending_result: bool | None = None
        self._last_completed_generation: int | None = None
        command = [str(helper_path)] if interpreter is None else [interpreter, str(helper_path)]
        self._process: subprocess.Popen[str] | None = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        self._reader_thread: threading.Thread | None = threading.Thread(
            target=self._read_acknowledgements,
            daemon=True,
            name="privacy-overlay-reader",
        )
        self._reader_thread.start()

    def send_and_wait(self, line: str, generation: int, timeout: float) -> bool:
        with self._command_lock:
            with self._condition:
                process = self._process
                if (
                    self._closed
                    or self._protocol_failed
                    or self._reader_finished
                    or (
                        self._last_completed_generation is not None
                        and generation <= self._last_completed_generation
                    )
                    or process is None
                    or process.poll() is not None
                    or process.stdin is None
                ):
                    return False
                self._pending_generation = generation
                self._pending_result = None

            try:
                process.stdin.write(f"{line}\n")
                process.stdin.flush()
            except (BrokenPipeError, OSError, ValueError) as exc:
                with self._condition:
                    self._pending_generation = None
                    self._pending_result = None
                    self._condition.notify_all()
                raise BrokenPipeError("privacy overlay helper write failed") from exc

            deadline = time.monotonic() + max(0.0, timeout)
            with self._condition:
                while self._pending_result is None:
                    if self._closed or self._protocol_failed or self._reader_finished:
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    self._condition.wait(remaining)
                confirmed = self._pending_result is True and not self._protocol_failed
                if confirmed:
                    self._last_completed_generation = generation
                self._pending_generation = None
                self._pending_result = None
                return confirmed

    def close(self) -> None:
        with self._command_lock:
            with self._condition:
                if self._closed:
                    return
                self._closed = True
                self._pending_generation = None
                self._pending_result = None
                process = self._process
                self._condition.notify_all()

            if process is not None:
                if process.stdin is not None:
                    with contextlib.suppress(OSError, ValueError):
                        process.stdin.close()
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=_CLOSE_TIMEOUT)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        with contextlib.suppress(subprocess.TimeoutExpired):
                            process.wait(timeout=_CLOSE_TIMEOUT)
                if process.stdout is not None:
                    with contextlib.suppress(OSError, ValueError):
                        process.stdout.close()

            reader = self._reader_thread
            if reader is not None and reader.is_alive():
                reader.join(timeout=_CLOSE_TIMEOUT)
            self._reader_thread = None
            self._process = None

    def _read_acknowledgements(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        try:
            for line in process.stdout:
                self._record_acknowledgement(line)
        except (OSError, ValueError):
            pass
        finally:
            with self._condition:
                self._reader_finished = True
                self._condition.notify_all()

    def _record_acknowledgement(self, line: str) -> None:
        try:
            message: Any = json.loads(line)
            generation = message["generation"]
            rendered = message["rendered"]
        except (json.JSONDecodeError, KeyError, TypeError):
            message = None
            generation = None
            rendered = None
        with self._condition:
            exact = (
                isinstance(message, dict)
                and not isinstance(generation, bool)
                and isinstance(generation, int)
                and rendered is True
                and generation == self._pending_generation
                and self._pending_result is None
            )
            if self._pending_generation is None or self._pending_result is not None:
                self._protocol_failed = True
            else:
                self._pending_result = exact
            self._condition.notify_all()


def _reason_setting(snapshot: ProtectionSnapshot, name: str, default: str) -> str:
    allowed = {
        "reason_display": _REASON_DISPLAY_MODES,
        "reason_detail": _REASON_DETAIL_MODES,
        "reason_trigger": _REASON_TRIGGERS,
    }[name]
    value = getattr(snapshot, name, default)
    return value if isinstance(value, str) and value in allowed else default


def _reason_payloads_for_display(
    snapshot: ProtectionSnapshot,
    display_id: int | None,
) -> list[dict[str, object]]:
    """Serialize bounded reasons without widening the snapshot's capture boundary."""
    if _reason_setting(snapshot, "reason_display", "hybrid") not in {"overlay", "hybrid"}:
        return []
    display_reasons = getattr(snapshot, "display_reasons", None)
    if display_reasons is None:
        return []

    detail = _reason_setting(snapshot, "reason_detail", "exact")
    exact_allowed = detail == "exact" and (
        snapshot.state is ProtectionState.PAUSED
        or (
            display_id is not None
            and display_id in getattr(snapshot, "protected_display_ids", frozenset())
        )
    )
    payload_detail = "exact" if exact_allowed else "category"
    return [
        reason.to_payload(payload_detail)
        for reason in display_reasons.for_display(display_id)
    ]


class PrivacyOverlayClient:
    """Render state-only protection indicators with confirmed generations."""

    def __init__(self, *, transport_factory: Callable[[], OverlayTransport] | None = None) -> None:
        self._transport_factory = transport_factory
        self._transport: OverlayTransport | None = None
        self._restart_delay = _INITIAL_RESTART_DELAY
        self._next_restart_at = 0.0
        self._lifecycle_lock = threading.RLock()
        self._send_lock = threading.Lock()
        self._closed = False
        self._close_started = False

    def render(self, snapshot: ProtectionSnapshot, timeout: float = 0.5) -> bool:
        if snapshot.indicator_style == "off":
            with self._send_lock:
                if self._closed:
                    return False
                self._discard_transport(schedule_restart=False)
            return True
        return self._send(self._render_command(snapshot), snapshot.generation, timeout)

    def clear(self, generation: int, timeout: float = 0.5) -> bool:
        return self._send(
            {
                "generation": generation,
                "state": ProtectionState.INACTIVE.value,
                "style": "off",
                "displays": [],
                "all_displays": False,
                "reason_display": "hybrid",
                "reason_detail": "category",
                "reason_trigger": "hover",
                "reasons": [],
            },
            generation,
            timeout,
        )

    def close(self) -> None:
        self.mark_terminal()
        with self._lifecycle_lock:
            if self._close_started:
                return
            self._close_started = True
        with self._send_lock:
            self._discard_transport(schedule_restart=False)

    def mark_terminal(self) -> None:
        """Prevent later render or clear calls without waiting for transport cleanup."""
        with self._send_lock, self._lifecycle_lock:
            self._closed = True

    def _send(self, command: dict[str, Any], generation: int, timeout: float) -> bool:
        with self._send_lock:
            if self._closed:
                return False
            transport = self._ensure_transport()
            if transport is None:
                return False
            try:
                confirmed = transport.send_and_wait(
                    json.dumps(command, separators=(",", ":")), generation, timeout
                )
            except (OSError, RuntimeError, ValueError):
                confirmed = False
            if confirmed:
                with self._lifecycle_lock:
                    if self._transport is transport:
                        self._restart_delay = _INITIAL_RESTART_DELAY
                        self._next_restart_at = 0.0
                return True
            self._discard_transport(schedule_restart=True, expected=transport)
            return False

    def _ensure_transport(self) -> OverlayTransport | None:
        with self._lifecycle_lock:
            if self._closed:
                return None
            if self._transport is not None:
                return self._transport
            if time.monotonic() < self._next_restart_at:
                return None
        try:
            transport = self._transport_factory() if self._transport_factory else self._start_default_transport()
        except OSError:
            transport = None
        if transport is None:
            with self._lifecycle_lock:
                if not self._closed:
                    self._schedule_restart()
            return None
        with self._lifecycle_lock:
            if self._closed:
                closed_transport = transport
            else:
                self._transport = transport
                return transport
        with contextlib.suppress(OSError, RuntimeError, ValueError):
            closed_transport.close()
        return None

    def _start_default_transport(self) -> OverlayTransport | None:
        helper_path = _resolve_overlay_path()
        return _SubprocessOverlayTransport(helper_path) if helper_path is not None else None

    def _discard_transport(
        self, *, schedule_restart: bool, expected: OverlayTransport | None = None
    ) -> None:
        with self._lifecycle_lock:
            if expected is not None and self._transport is not expected:
                return
            transport, self._transport = self._transport, None
            if schedule_restart:
                self._schedule_restart()
        if transport is not None:
            with contextlib.suppress(OSError, RuntimeError, ValueError):
                transport.close()

    def _schedule_restart(self) -> None:
        self._next_restart_at = time.monotonic() + self._restart_delay
        self._restart_delay = min(self._restart_delay * 2, _MAX_RESTART_DELAY)

    @staticmethod
    def _render_command(snapshot: ProtectionSnapshot) -> dict[str, Any]:
        if snapshot.state in (ProtectionState.PAUSED, ProtectionState.FAILED):
            displays = snapshot.displays
        elif snapshot.state is ProtectionState.PROTECTED:
            displays = tuple(
                display
                for display in snapshot.displays
                if display.id in snapshot.protected_display_ids
            )
        else:
            displays = ()

        reason_display = _reason_setting(snapshot, "reason_display", "hybrid")
        reason_detail = _reason_setting(snapshot, "reason_detail", "exact")
        reason_trigger = _reason_setting(snapshot, "reason_trigger", "hover")
        all_displays = not displays and snapshot.state in (
            ProtectionState.PAUSED,
            ProtectionState.FAILED,
        )

        return {
            "generation": snapshot.generation,
            "state": snapshot.state.value,
            "style": snapshot.indicator_style,
            "displays": [
                {
                    "id": display.id,
                    "left": display.region.left,
                    "top": display.region.top,
                    "width": display.region.width,
                    "height": display.region.height,
                    "reasons": _reason_payloads_for_display(snapshot, display.id),
                }
                for display in displays
            ],
            "all_displays": all_displays,
            "reason_display": reason_display,
            "reason_detail": reason_detail,
            "reason_trigger": reason_trigger,
            "reasons": _reason_payloads_for_display(snapshot, None) if all_displays else [],
        }
