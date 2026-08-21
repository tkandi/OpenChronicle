"""Fail-closed, non-sensitive persistence for diagnostics reveal leases."""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_SCHEMA_VERSION = 1
_MAX_DISPLAY_ID = (1 << 32) - 1
_MAX_PID = (1 << 31) - 1

ProcessAliveProbe = Callable[[int], bool | None]


@dataclass(frozen=True)
class DiagnosticsRevealLease:
    """The one active diagnostics reveal lease persisted in the guard."""

    lease_id: str
    pid: int
    display_ids: frozenset[int]


@dataclass(frozen=True)
class DiagnosticsMoveTransition:
    """A move keeps both displays protected until it is committed."""

    transition_id: str
    lease_id: str
    pid: int
    old_display_ids: frozenset[int]
    new_display_id: int


@dataclass(frozen=True)
class DiagnosticsGuardSnapshot:
    """The current protections or a global fail-closed state."""

    display_ids: frozenset[int]
    fail_closed_all: bool


def _default_process_alive(pid: int) -> bool | None:
    """Return only confirmed process liveness; permission uncertainty is protected."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return None
    return True


def _valid_pid(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 < value <= _MAX_PID


def _valid_display_id(value: object) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 < value <= _MAX_DISPLAY_ID
    )


def _valid_lease_id(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return uuid.UUID(value).hex == value
    except (AttributeError, ValueError):
        return False


class DiagnosticsLeaseManager:
    """Serializes a singleton diagnostics lease and its on-disk guard."""

    def __init__(
        self,
        guard_path: Path,
        *,
        process_alive: ProcessAliveProbe = _default_process_alive,
    ) -> None:
        self._guard_path = guard_path
        self._process_alive = process_alive
        self._lock = threading.RLock()
        self._lease: DiagnosticsRevealLease | None = None
        self._transition: DiagnosticsMoveTransition | None = None
        self._fail_closed_all = False
        self._loaded = False

    def load(self) -> DiagnosticsGuardSnapshot:
        """Restore a valid guard, retaining it unless process death is confirmed."""
        with self._lock:
            self._lease = None
            self._transition = None
            self._fail_closed_all = False
            self._loaded = True
            try:
                raw = self._guard_path.read_text(encoding="utf-8")
            except FileNotFoundError:
                return self.snapshot()
            except OSError:
                self._fail_closed_all = True
                return self.snapshot()

            try:
                lease = self._parse_guard(raw)
            except (json.JSONDecodeError, TypeError, ValueError):
                self._fail_closed_all = True
                return self.snapshot()

            self._lease = lease
            return self.prune_dead()

    def acquire(self, *, pid: int, display_id: int) -> DiagnosticsRevealLease:
        """Persist one new lease after validating its non-sensitive metadata."""
        if not _valid_pid(pid):
            raise ValueError("pid must be a positive integer")
        if not _valid_display_id(display_id):
            raise ValueError("display_id must be a positive display ID")
        with self._lock:
            self._ensure_loaded()
            if self._fail_closed_all:
                raise RuntimeError("cannot acquire while the diagnostics guard is invalid")
            if self._lease is not None:
                raise ValueError("a diagnostics lease is already active")
            lease = DiagnosticsRevealLease(uuid.uuid4().hex, pid, frozenset({display_id}))
            self._write_guard(lease)
            self._lease = lease
            return lease

    def begin_move(
        self, lease_id: str, *, pid: int, new_display_id: int
    ) -> DiagnosticsMoveTransition:
        """Write the old and new display IDs before the diagnostics window moves."""
        if not _valid_display_id(new_display_id):
            raise ValueError("new_display_id must be a positive display ID")
        with self._lock:
            lease = self._require_lease(lease_id, pid)
            if self._transition is not None:
                raise ValueError("a diagnostics lease move is already active")
            if len(lease.display_ids) != 1:
                raise ValueError("a diagnostics lease has an uncommitted move")
            protected_ids = lease.display_ids | {new_display_id}
            moving_lease = DiagnosticsRevealLease(lease.lease_id, lease.pid, protected_ids)
            transition = DiagnosticsMoveTransition(
                transition_id=uuid.uuid4().hex,
                lease_id=lease.lease_id,
                pid=lease.pid,
                old_display_ids=lease.display_ids,
                new_display_id=new_display_id,
            )
            self._write_guard(moving_lease)
            self._lease = moving_lease
            self._transition = transition
            return transition

    def commit_move(self, transition_id: str) -> DiagnosticsRevealLease:
        """Complete a move after the destination display is known to be protected."""
        with self._lock:
            if self._transition is None or self._transition.transition_id != transition_id:
                raise ValueError("unknown diagnostics lease transition")
            transition = self._transition
            committed_lease = DiagnosticsRevealLease(
                transition.lease_id,
                transition.pid,
                frozenset({transition.new_display_id}),
            )
            self._write_guard(committed_lease)
            self._lease = committed_lease
            self._transition = None
            return committed_lease

    def release(self, lease_id: str, *, pid: int) -> DiagnosticsGuardSnapshot:
        """Clear a lease only when both its nonce and owning process match."""
        with self._lock:
            self._require_lease(lease_id, pid)
            self._clear_guard()
            self._lease = None
            self._transition = None
            return self.snapshot()

    def snapshot(self) -> DiagnosticsGuardSnapshot:
        """Return the current known display protections without reading the filesystem."""
        with self._lock:
            display_ids = self._lease.display_ids if self._lease is not None else frozenset()
            return DiagnosticsGuardSnapshot(display_ids, self._fail_closed_all)

    def prune_dead(self) -> DiagnosticsGuardSnapshot:
        """Clear a valid lease only when its app process is definitely gone."""
        with self._lock:
            if self._lease is None or self._fail_closed_all:
                return self.snapshot()
            try:
                alive = self._process_alive(self._lease.pid)
            except Exception:
                alive = None
            if alive is not False:
                return self.snapshot()
            try:
                self._clear_guard()
            except OSError:
                return self.snapshot()
            self._lease = None
            self._transition = None
            return self.snapshot()

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    def _require_lease(self, lease_id: str, pid: int) -> DiagnosticsRevealLease:
        self._ensure_loaded()
        if self._fail_closed_all:
            raise RuntimeError("cannot modify an invalid diagnostics guard")
        if self._lease is None or self._lease.lease_id != lease_id:
            raise ValueError("unknown diagnostics lease")
        if self._lease.pid != pid:
            raise ValueError("diagnostics lease pid does not match")
        return self._lease

    def _parse_guard(self, raw: str) -> DiagnosticsRevealLease:
        payload: Any = json.loads(raw)
        required_keys = {"schema_version", "lease_id", "pid", "display_ids"}
        if not isinstance(payload, dict) or set(payload) != required_keys:
            raise ValueError("invalid diagnostics guard keys")
        if payload["schema_version"] != _SCHEMA_VERSION or isinstance(
            payload["schema_version"], bool
        ):
            raise ValueError("invalid diagnostics guard schema")
        if not _valid_lease_id(payload["lease_id"]):
            raise ValueError("invalid diagnostics guard lease")
        if not _valid_pid(payload["pid"]):
            raise ValueError("invalid diagnostics guard pid")
        display_ids = payload["display_ids"]
        if (
            not isinstance(display_ids, list)
            or not 1 <= len(display_ids) <= 2
            or any(not _valid_display_id(display_id) for display_id in display_ids)
            or display_ids != sorted(set(display_ids))
        ):
            raise ValueError("invalid diagnostics guard display IDs")
        return DiagnosticsRevealLease(
            payload["lease_id"],
            payload["pid"],
            frozenset(display_ids),
        )

    def _write_guard(self, lease: DiagnosticsRevealLease) -> None:
        self._guard_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": _SCHEMA_VERSION,
            "lease_id": lease.lease_id,
            "pid": lease.pid,
            "display_ids": sorted(lease.display_ids),
        }
        fd, temporary_name = tempfile.mkstemp(
            dir=self._guard_path.parent,
            prefix=f".{self._guard_path.name}.",
            suffix=".tmp",
        )
        temporary = Path(temporary_name)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._guard_path)
            self._fsync_parent()
        except BaseException:
            with contextlib.suppress(OSError):
                temporary.unlink()
            raise

    def _clear_guard(self) -> None:
        self._guard_path.unlink(missing_ok=True)
        self._fsync_parent()

    def _fsync_parent(self) -> None:
        with contextlib.suppress(OSError):
            directory_fd = os.open(self._guard_path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
