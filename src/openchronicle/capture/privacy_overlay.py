"""Privacy-safe client for the native macOS protection indicator."""

from __future__ import annotations

import contextlib
import json
import os
import platform
import subprocess
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


class OverlayTransport(Protocol):
    def write_line(self, line: str) -> None: ...

    def wait_for_generation(self, generation: int, timeout: float) -> bool: ...

    def close(self) -> None: ...


def _maybe_compile_overlay(core_path: Path, main_path: Path, binary_path: Path) -> None:
    """Build the two-source AppKit helper when its binary is missing or stale."""
    if not core_path.is_file() or not main_path.is_file():
        return
    if binary_path.is_file() and binary_path.stat().st_mtime >= max(
        core_path.stat().st_mtime, main_path.stat().st_mtime
    ):
        return

    cache = Path("/tmp/clang-module-cache")
    cache.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["CLANG_MODULE_CACHE_PATH"] = str(cache)
    arch = "arm64" if platform.machine() in ("arm64", "aarch64") else "x86_64"
    try:
        result = subprocess.run(
            [
                "swiftc",
                str(core_path),
                str(main_path),
                "-o",
                str(binary_path),
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
    except (FileNotFoundError, subprocess.TimeoutExpired):
        logger.warning("privacy overlay helper compilation unavailable")
        return
    if result.returncode != 0:
        logger.warning("privacy overlay helper compilation failed")


def _resolve_overlay_path() -> Path | None:
    """Find or build the privacy-overlay helper without inspecting user content."""
    if platform.system() != "Darwin":
        return None

    override = os.environ.get("OPENCHRONICLE_PRIVACY_OVERLAY_HELPER")
    if override:
        path = Path(override).expanduser().resolve()
        if path.is_file() and os.access(path, os.X_OK):
            return path
        logger.warning("OPENCHRONICLE_PRIVACY_OVERLAY_HELPER is not executable")

    candidates: list[Path] = []
    try:
        from importlib.resources import files as package_files

        bundled_dir = Path(str(package_files("openchronicle").joinpath("_bundled")))
        candidates.append(bundled_dir / "mac-privacy-overlay")
    except (ModuleNotFoundError, ValueError):
        pass

    dev_root = Path(__file__).resolve().parents[3]
    candidates.append(dev_root / "resources" / "mac-privacy-overlay")
    for binary_path in candidates:
        parent = binary_path.parent
        _maybe_compile_overlay(
            parent / "mac-privacy-overlay-core.swift",
            parent / "mac-privacy-overlay.swift",
            binary_path,
        )
        if binary_path.is_file() and os.access(binary_path, os.X_OK):
            return binary_path
    return None


class _SubprocessOverlayTransport:
    """NDJSON transport backed by one helper process and one reader thread."""

    def __init__(self, helper_path: Path) -> None:
        self._condition = threading.Condition()
        self._acknowledged: set[int] = set()
        self._closed = False
        self._reader_finished = False
        self._process: subprocess.Popen[str] | None = subprocess.Popen(
            [str(helper_path)],
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

    def write_line(self, line: str) -> None:
        with self._condition:
            process = self._process
            if (
                self._closed
                or self._reader_finished
                or process is None
                or process.poll() is not None
                or process.stdin is None
            ):
                raise BrokenPipeError("privacy overlay helper is not running")
            try:
                process.stdin.write(f"{line}\n")
                process.stdin.flush()
            except (BrokenPipeError, OSError, ValueError) as exc:
                raise BrokenPipeError("privacy overlay helper write failed") from exc

    def wait_for_generation(self, generation: int, timeout: float) -> bool:
        deadline = time.monotonic() + max(0.0, timeout)
        with self._condition:
            while generation not in self._acknowledged:
                process = self._process
                if (
                    self._closed
                    or self._reader_finished
                    or process is None
                    or process.poll() is not None
                ):
                    return False
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True

    def close(self) -> None:
        with self._condition:
            if self._closed:
                return
            self._closed = True
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
            if (
                not isinstance(message, dict)
                or isinstance(generation, bool)
                or not isinstance(generation, int)
                or rendered is not True
            ):
                return
        except (json.JSONDecodeError, KeyError, TypeError):
            return
        with self._condition:
            self._acknowledged.add(generation)
            self._condition.notify_all()


class PrivacyOverlayClient:
    """Render state-only protection indicators with confirmed generations."""

    def __init__(self, *, transport_factory: Callable[[], OverlayTransport] | None = None) -> None:
        self._transport_factory = transport_factory
        self._transport: OverlayTransport | None = None
        self._restart_delay = _INITIAL_RESTART_DELAY
        self._next_restart_at = 0.0

    def render(self, snapshot: ProtectionSnapshot, timeout: float = 0.5) -> bool:
        if snapshot.indicator_style == "off":
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
            },
            generation,
            timeout,
        )

    def close(self) -> None:
        self._discard_transport(schedule_restart=False)

    def _send(self, command: dict[str, Any], generation: int, timeout: float) -> bool:
        transport = self._ensure_transport()
        if transport is None:
            return False
        try:
            transport.write_line(json.dumps(command, separators=(",", ":")))
            confirmed = transport.wait_for_generation(generation, timeout)
        except (OSError, RuntimeError, ValueError):
            confirmed = False
        if confirmed:
            return True
        self._discard_transport(schedule_restart=True)
        return False

    def _ensure_transport(self) -> OverlayTransport | None:
        if self._transport is not None:
            return self._transport
        if time.monotonic() < self._next_restart_at:
            return None
        try:
            transport = self._transport_factory() if self._transport_factory else self._start_default_transport()
        except OSError:
            transport = None
        if transport is None:
            self._schedule_restart()
            return None
        self._transport = transport
        return transport

    def _start_default_transport(self) -> OverlayTransport | None:
        helper_path = _resolve_overlay_path()
        return _SubprocessOverlayTransport(helper_path) if helper_path is not None else None

    def _discard_transport(self, *, schedule_restart: bool) -> None:
        transport, self._transport = self._transport, None
        if transport is not None:
            with contextlib.suppress(OSError, RuntimeError, ValueError):
                transport.close()
        if schedule_restart:
            self._schedule_restart()

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
                }
                for display in displays
            ],
            "all_displays": not displays and snapshot.state in (
                ProtectionState.PAUSED,
                ProtectionState.FAILED,
            ),
        }
