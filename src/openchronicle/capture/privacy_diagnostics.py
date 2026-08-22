"""Bounded, owner-only AF_UNIX service for privacy protection diagnostics."""

from __future__ import annotations

import contextlib
import json
import os
import selectors
import socket
import stat
import struct
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..logger import get
from .privacy_diagnostics_guard import DiagnosticsLeaseManager
from .protection import ProtectionSnapshot, ProtectionState
from .protection_monitor import ProtectionDecision
from .protection_reason import ProtectionReasonCode

logger = get("openchronicle.capture")

_SCHEMA_VERSION = 1
_MAX_LINE_BYTES = 64 * 1024
_MAX_SEND_MESSAGES = 8
_MAX_SEND_BYTES = 4 * _MAX_LINE_BYTES
_MAX_CLIENTS = 16
_ACCEPT_BACKLOG = 8
_STOP_JOIN_GRACE = 1.0
_DARWIN_SOL_LOCAL = 0
_DARWIN_LOCAL_PEERPID = 2
_MAX_PID = (1 << 31) - 1
_MAX_DISPLAY_ID = (1 << 32) - 1

RequestRefresh = Callable[[], None]
WaitForDisplayProtection = Callable[[int, int, float], int | None]
Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _rfc3339_utc(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("privacy diagnostics clock must return an aware datetime")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class _PublishedDecision:
    decision: ProtectionDecision
    created_at: str


@dataclass
class _ClientState:
    sock: socket.socket
    peer_pid: int
    received: bytearray = field(default_factory=bytearray)
    outgoing: deque[bytes] = field(default_factory=deque)
    outgoing_bytes: int = 0
    send_offset: int = 0
    subscribed: bool = False
    detail: str = "category"
    lease_id: str | None = None
    exact_display_id: int | None = None
    protected_generation: int | None = None
    last_queued_generation: int = 0
    close_after_write: bool = False


def _peer_credentials(client: socket.socket) -> tuple[int | None, int | None]:
    """Return peer UID and PID using local-kernel credentials only."""
    if hasattr(socket, "SO_PEERCRED"):
        size = struct.calcsize("=3i")
        raw = client.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, size)
        pid, uid, _gid = struct.unpack("=3i", raw)
        return uid, pid

    if hasattr(socket, "LOCAL_PEERCRED"):
        raw = client.getsockopt(_DARWIN_SOL_LOCAL, socket.LOCAL_PEERCRED, 128)
        _version, uid = struct.unpack_from("=II", raw)
        peer_pid = client.getsockopt(
            _DARWIN_SOL_LOCAL,
            _DARWIN_LOCAL_PEERPID,
            struct.calcsize("=i"),
        )
        return uid, struct.unpack("=i", peer_pid)[0]

    getpeereid = getattr(client, "getpeereid", None)
    if getpeereid is not None:
        uid, _gid = getpeereid()
        return uid, None
    return None, None


def _error(code: str) -> dict[str, object]:
    return {"schema_version": _SCHEMA_VERSION, "type": "error", "code": code}


def _valid_positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _valid_pid(value: object) -> bool:
    return _valid_positive_int(value) and value <= _MAX_PID


def _valid_display_id(value: object) -> bool:
    return _valid_positive_int(value) and value <= _MAX_DISPLAY_ID


class PrivacyDiagnosticsServer:
    """Serve category snapshots and generation-authorized exact snapshots."""

    def __init__(
        self,
        socket_path: Path,
        lease_manager: DiagnosticsLeaseManager,
        *,
        request_refresh: RequestRefresh,
        wait_for_display_protection: WaitForDisplayProtection,
        handshake_timeout: float = 1.0,
        watchdog_seconds: float = 1.0,
        clock: Clock = _utc_now,
    ) -> None:
        self.socket_path = socket_path
        self._lease_manager = lease_manager
        self._request_refresh = request_refresh
        self._wait_for_display_protection = wait_for_display_protection
        self._handshake_timeout = max(0.0, handshake_timeout)
        self._watchdog_seconds = max(0.01, watchdog_seconds)
        self._clock = clock

        self._decision_lock = threading.Lock()
        self._latest_publication: _PublishedDecision | None = None
        self._lifecycle_lock = threading.Lock()
        self._connections_lock = threading.Lock()
        self._connections: set[socket.socket] = set()
        self._stop = threading.Event()
        self._listener: socket.socket | None = None
        self._wake_reader: socket.socket | None = None
        self._wake_writer: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._started = False
        self._owned_socket_identity: tuple[int, int] | None = None

    @property
    def thread(self) -> threading.Thread | None:
        return self._thread

    def start(self) -> None:
        """Bind the private socket before starting the dedicated server thread."""
        with self._lifecycle_lock:
            if self._started:
                return
            if self._stop.is_set():
                raise RuntimeError("privacy diagnostics server is stopped")
            self._validate_runtime_directory()
            self._remove_stale_socket()

            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            wake_reader: socket.socket | None = None
            wake_writer: socket.socket | None = None
            bound_socket_identity: tuple[int, int] | None = None
            try:
                listener.bind(str(self.socket_path))
                bound_stat = self.socket_path.lstat()
                bound_socket_identity = (bound_stat.st_dev, bound_stat.st_ino)
                os.chmod(self.socket_path, 0o600)
                socket_stat = self.socket_path.lstat()
                if (
                    not stat.S_ISSOCK(socket_stat.st_mode)
                    or socket_stat.st_uid != os.getuid()
                    or stat.S_IMODE(socket_stat.st_mode) != 0o600
                    or (socket_stat.st_dev, socket_stat.st_ino)
                    != bound_socket_identity
                ):
                    raise RuntimeError("privacy diagnostics socket permissions are unsafe")
                self._owned_socket_identity = bound_socket_identity
                listener.listen(_ACCEPT_BACKLOG)
                listener.setblocking(False)
                wake_reader, wake_writer = socket.socketpair()
                wake_reader.setblocking(False)
                wake_writer.setblocking(False)
            except BaseException:
                listener.close()
                if wake_reader is not None:
                    wake_reader.close()
                if wake_writer is not None:
                    wake_writer.close()
                self._owned_socket_identity = None
                self._unlink_socket_identity(bound_socket_identity)
                raise

            self._listener = listener
            self._wake_reader = wake_reader
            self._wake_writer = wake_writer
            self._thread = threading.Thread(
                target=self._run,
                daemon=True,
                name="privacy-diagnostics-server",
            )
            self._started = True
            self._thread.start()

    def stop(self) -> None:
        """Stop accepting immediately, drain no private data, and unlink the socket."""
        with self._lifecycle_lock:
            self._stop.set()
            thread = self._thread
            listener = self._listener
            self._listener = None
        if listener is not None:
            listener.close()
        self._close_all_connections()
        self._unlink_owned_socket()
        self._wake()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=self._handshake_timeout + _STOP_JOIN_GRACE)
        self._unlink_owned_socket()

    def publish(self, decision: ProtectionDecision) -> bool:
        """Publish only a strictly newer generation and wake subscribed clients."""
        generation = decision.snapshot.generation
        with self._decision_lock:
            current = self._latest_publication
            if (
                current is not None
                and generation <= current.decision.snapshot.generation
            ):
                return False
            self._latest_publication = _PublishedDecision(
                decision=decision,
                created_at=_rfc3339_utc(self._clock()),
            )
        self._wake()
        return True

    def _run(self) -> None:
        selector = selectors.DefaultSelector()
        clients: dict[int, _ClientState] = {}
        listener = self._listener
        wake_reader = self._wake_reader
        wake_writer = self._wake_writer
        if listener is None or wake_reader is None or wake_writer is None:
            selector.close()
            for owned_socket in (listener, wake_reader, wake_writer):
                if owned_socket is not None:
                    owned_socket.close()
            self._listener = None
            self._wake_reader = None
            self._wake_writer = None
            self._unlink_owned_socket()
            return
        try:
            try:
                selector.register(listener, selectors.EVENT_READ, "listener")
                selector.register(wake_reader, selectors.EVENT_READ, "wake")
            except (OSError, ValueError) as exc:
                if not self._stop.is_set():
                    logger.warning(
                        "privacy diagnostics server failed: %s",
                        type(exc).__name__,
                    )
                return
            next_watchdog = time.monotonic() + self._watchdog_seconds
            while not self._stop.is_set():
                timeout = max(0.0, next_watchdog - time.monotonic())
                try:
                    selected = selector.select(timeout)
                except OSError:
                    if self._stop.is_set():
                        break
                    raise
                for key, events in selected:
                    if key.data == "listener":
                        self._accept_clients(selector, clients)
                    elif key.data == "wake":
                        self._drain_wakeup()
                        self._publish_to_subscribers(selector, clients)
                    else:
                        client = key.data
                        try:
                            if events & selectors.EVENT_READ:
                                self._read_client(selector, clients, client)
                            if (
                                events & selectors.EVENT_WRITE
                                and client.sock.fileno() >= 0
                            ):
                                self._write_client(selector, clients, client)
                        except Exception as exc:
                            logger.warning(
                                "privacy diagnostics client failed: %s",
                                type(exc).__name__,
                            )
                            self._close_client(selector, clients, client)
                if time.monotonic() >= next_watchdog:
                    self._prune_dead_lease(clients)
                    next_watchdog = time.monotonic() + self._watchdog_seconds
        finally:
            for client in tuple(clients.values()):
                self._close_client(selector, clients, client)
            with contextlib.suppress(Exception):
                selector.unregister(listener)
            with contextlib.suppress(Exception):
                selector.unregister(wake_reader)
            selector.close()
            listener.close()
            wake_reader.close()
            wake_writer.close()
            self._listener = None
            self._wake_reader = None
            self._wake_writer = None
            self._unlink_owned_socket()

    def _accept_clients(
        self,
        selector: selectors.BaseSelector,
        clients: dict[int, _ClientState],
    ) -> None:
        listener = self._listener
        if listener is None:
            return
        while True:
            try:
                client_socket, _address = listener.accept()
            except BlockingIOError:
                return
            except OSError:
                return
            if len(clients) >= _MAX_CLIENTS:
                client_socket.close()
                continue
            try:
                peer_uid, peer_pid = _peer_credentials(client_socket)
            except OSError:
                peer_uid, peer_pid = None, None
            if (
                peer_uid != os.getuid()
                or peer_pid is None
                or not _valid_pid(peer_pid)
            ):
                client_socket.close()
                continue
            client_socket.setblocking(False)
            with self._connections_lock:
                if self._stop.is_set():
                    client_socket.close()
                    continue
                self._connections.add(client_socket)
            state = _ClientState(client_socket, peer_pid)
            clients[client_socket.fileno()] = state
            selector.register(client_socket, selectors.EVENT_READ, state)

    def _read_client(
        self,
        selector: selectors.BaseSelector,
        clients: dict[int, _ClientState],
        client: _ClientState,
    ) -> None:
        try:
            chunk = client.sock.recv(8192)
        except BlockingIOError:
            return
        if not chunk:
            self._close_client(selector, clients, client)
            return
        client.received.extend(chunk)

        while not client.close_after_write:
            newline = client.received.find(b"\n")
            if newline < 0:
                if len(client.received) > _MAX_LINE_BYTES:
                    self._queue_error_and_close(
                        selector,
                        clients,
                        client,
                        "line_too_long",
                    )
                return
            line = bytes(client.received[:newline])
            del client.received[: newline + 1]
            if len(line) > _MAX_LINE_BYTES:
                self._queue_error_and_close(
                    selector,
                    clients,
                    client,
                    "line_too_long",
                )
                return
            self._handle_line(selector, clients, client, line)
            if client.sock.fileno() < 0:
                return

    def _handle_line(
        self,
        selector: selectors.BaseSelector,
        clients: dict[int, _ClientState],
        client: _ClientState,
        line: bytes,
    ) -> None:
        try:
            message: Any = json.loads(line)
        except (UnicodeDecodeError, ValueError, RecursionError):
            self._queue_payload(selector, clients, client, _error("invalid_json"))
            return
        if not isinstance(message, dict):
            self._queue_payload(selector, clients, client, _error("invalid_request"))
            return
        schema_version = message.get("schema_version")
        if (
            not isinstance(schema_version, int)
            or isinstance(schema_version, bool)
            or schema_version != _SCHEMA_VERSION
        ):
            self._queue_payload(selector, clients, client, _error("unsupported_schema"))
            return
        action = message.get("action")
        if not isinstance(action, str):
            self._queue_payload(selector, clients, client, _error("invalid_request"))
            return
        if action == "subscribe":
            self._handle_subscribe(selector, clients, client, message)
        elif action == "acquire_exact":
            self._handle_acquire(selector, clients, client, message)
        elif action == "move_exact":
            self._handle_move(selector, clients, client, message)
        elif action == "release_exact":
            self._handle_release(selector, clients, client, message)
        else:
            self._queue_payload(selector, clients, client, _error("unknown_action"))

    def _handle_subscribe(
        self,
        selector: selectors.BaseSelector,
        clients: dict[int, _ClientState],
        client: _ClientState,
        message: dict[str, Any],
    ) -> None:
        if not set(message) <= {"schema_version", "action", "detail"}:
            self._queue_payload(selector, clients, client, _error("invalid_request"))
            return
        detail = message.get("detail", "category")
        if not isinstance(detail, str) or detail not in {"category", "exact"}:
            self._queue_payload(selector, clients, client, _error("invalid_request"))
            return
        publication = self._current_publication()
        decision = publication.decision if publication is not None else None
        if detail == "exact" and not self._exact_is_authorized(client, decision):
            code = "lease_required" if client.lease_id is None else "stale_generation"
            self._queue_payload(selector, clients, client, _error(code))
            return
        client.subscribed = True
        client.detail = detail
        if publication is None:
            self._queue_payload(selector, clients, client, _error("unavailable"))
            return
        self._queue_snapshot(selector, clients, client, publication)

    def _handle_acquire(
        self,
        selector: selectors.BaseSelector,
        clients: dict[int, _ClientState],
        client: _ClientState,
        message: dict[str, Any],
    ) -> None:
        if set(message) != {"schema_version", "action", "pid", "display_id"}:
            self._queue_payload(selector, clients, client, _error("invalid_request"))
            return
        pid = message["pid"]
        display_id = message["display_id"]
        if not _valid_pid(pid) or not _valid_display_id(display_id):
            self._queue_payload(selector, clients, client, _error("invalid_request"))
            return
        if pid != client.peer_pid:
            self._queue_payload(selector, clients, client, _error("pid_mismatch"))
            return
        baseline = self._current_generation()
        try:
            lease = self._lease_manager.acquire(pid=pid, display_id=display_id)
        except ValueError:
            self._queue_payload(selector, clients, client, _error("lease_conflict"))
            return
        except RuntimeError:
            self._queue_payload(selector, clients, client, _error("guard_invalid"))
            return
        except OSError:
            self._queue_payload(selector, clients, client, _error("guard_unavailable"))
            return
        protected_generation = self._refresh_and_wait(display_id, baseline)
        if protected_generation is None:
            rollback_error = self._rollback_unacknowledged_acquire(
                lease.lease_id,
                pid=pid,
                clients=clients,
            )
            self._queue_payload(selector, clients, client, _error(rollback_error))
            return

        client.lease_id = lease.lease_id
        client.exact_display_id = display_id
        client.protected_generation = protected_generation
        client.detail = "exact"
        self._queue_payload(
            selector,
            clients,
            client,
            {
                "schema_version": _SCHEMA_VERSION,
                "type": "lease",
                "lease_id": lease.lease_id,
                "display_id": display_id,
                "protected_generation": client.protected_generation,
            },
        )
        if client.subscribed:
            publication = self._current_publication()
            decision = publication.decision if publication is not None else None
            if self._exact_is_authorized(client, decision):
                self._queue_snapshot(selector, clients, client, publication)

    def _rollback_unacknowledged_acquire(
        self,
        lease_id: str,
        *,
        pid: int,
        clients: dict[int, _ClientState],
    ) -> str:
        try:
            self._lease_manager.release(lease_id, pid=pid)
        except (OSError, RuntimeError, ValueError):
            error_code = "guard_unavailable"
        else:
            self._clear_lease_authorizations(clients, lease_id)
            error_code = "protection_timeout"
        try:
            self._request_refresh()
        except Exception:
            if error_code == "protection_timeout":
                return "refresh_failed"
        return error_code

    def _handle_move(
        self,
        selector: selectors.BaseSelector,
        clients: dict[int, _ClientState],
        client: _ClientState,
        message: dict[str, Any],
    ) -> None:
        if set(message) != {
            "schema_version",
            "action",
            "pid",
            "lease_id",
            "display_id",
        }:
            self._queue_payload(selector, clients, client, _error("invalid_request"))
            return
        pid = message["pid"]
        lease_id = message["lease_id"]
        display_id = message["display_id"]
        if (
            not _valid_pid(pid)
            or pid != client.peer_pid
            or not isinstance(lease_id, str)
            or not _valid_display_id(display_id)
        ):
            code = "pid_mismatch" if _valid_pid(pid) and pid != client.peer_pid else "invalid_request"
            self._queue_payload(selector, clients, client, _error(code))
            return
        baseline = self._current_generation()
        try:
            transition = self._lease_manager.begin_move(
                lease_id,
                pid=pid,
                new_display_id=display_id,
            )
        except (ValueError, RuntimeError):
            self._queue_payload(selector, clients, client, _error("invalid_lease"))
            return
        except OSError:
            self._queue_payload(selector, clients, client, _error("guard_unavailable"))
            return

        client.detail = "category"
        client.protected_generation = None
        protected_generation = self._refresh_and_wait(display_id, baseline)
        if protected_generation is None:
            self._queue_payload(selector, clients, client, _error("protection_timeout"))
            return
        try:
            lease = self._lease_manager.commit_move(transition.transition_id)
        except (OSError, RuntimeError, ValueError):
            self._queue_payload(selector, clients, client, _error("guard_unavailable"))
            return
        try:
            self._request_refresh()
        except Exception:
            self._queue_payload(selector, clients, client, _error("refresh_failed"))
            return

        client.lease_id = lease.lease_id
        client.exact_display_id = display_id
        client.protected_generation = protected_generation
        client.detail = "exact"
        self._queue_payload(
            selector,
            clients,
            client,
            {
                "schema_version": _SCHEMA_VERSION,
                "type": "lease",
                "lease_id": lease.lease_id,
                "display_id": display_id,
                "protected_generation": protected_generation,
            },
        )
        if client.subscribed:
            publication = self._current_publication()
            decision = publication.decision if publication is not None else None
            if self._exact_is_authorized(client, decision):
                self._queue_snapshot(selector, clients, client, publication)

    def _handle_release(
        self,
        selector: selectors.BaseSelector,
        clients: dict[int, _ClientState],
        client: _ClientState,
        message: dict[str, Any],
    ) -> None:
        if set(message) != {"schema_version", "action", "pid", "lease_id"}:
            self._queue_payload(selector, clients, client, _error("invalid_request"))
            return
        pid = message["pid"]
        lease_id = message["lease_id"]
        if not _valid_pid(pid) or not isinstance(lease_id, str):
            self._queue_payload(selector, clients, client, _error("invalid_request"))
            return
        if pid != client.peer_pid:
            self._queue_payload(selector, clients, client, _error("pid_mismatch"))
            return
        try:
            self._lease_manager.release(lease_id, pid=pid)
        except (ValueError, RuntimeError):
            self._queue_payload(selector, clients, client, _error("invalid_lease"))
            return
        except OSError:
            self._queue_payload(selector, clients, client, _error("guard_unavailable"))
            return

        self._clear_lease_authorizations(clients, lease_id)
        try:
            self._request_refresh()
        except Exception:
            self._queue_payload(selector, clients, client, _error("refresh_failed"))
            return
        self._queue_payload(
            selector,
            clients,
            client,
            {
                "schema_version": _SCHEMA_VERSION,
                "type": "lease",
                "lease_id": lease_id,
                "released": True,
            },
        )

    def _refresh_and_wait(self, display_id: int, baseline: int) -> int | None:
        try:
            self._request_refresh()
            generation = self._wait_for_display_protection(
                display_id,
                baseline,
                self._handshake_timeout,
            )
        except Exception:
            return None
        if (
            not isinstance(generation, int)
            or isinstance(generation, bool)
            or generation <= baseline
        ):
            return None
        return generation

    def _publish_to_subscribers(
        self,
        selector: selectors.BaseSelector,
        clients: dict[int, _ClientState],
    ) -> None:
        publication = self._current_publication()
        if publication is None:
            return
        decision = publication.decision
        for client in tuple(clients.values()):
            if (
                client.subscribed
                and decision.snapshot.generation > client.last_queued_generation
            ):
                self._queue_snapshot(selector, clients, client, publication)

    def _queue_snapshot(
        self,
        selector: selectors.BaseSelector,
        clients: dict[int, _ClientState],
        client: _ClientState,
        publication: _PublishedDecision,
    ) -> None:
        decision = publication.decision
        detail = (
            "exact"
            if client.detail == "exact" and self._exact_is_authorized(client, decision)
            else "category"
        )
        payload = self._snapshot_payload(
            decision,
            detail=detail,
            created_at=publication.created_at,
        )
        if self._queue_payload(selector, clients, client, payload):
            client.last_queued_generation = decision.snapshot.generation

    def _exact_is_authorized(
        self,
        client: _ClientState,
        decision: ProtectionDecision | None,
    ) -> bool:
        if (
            decision is None
            or client.lease_id is None
            or client.exact_display_id is None
            or client.protected_generation is None
            or decision.snapshot.generation < client.protected_generation
            or client.exact_display_id not in decision.snapshot.protected_display_ids
            or not (decision.indicator_confirmed or decision.snapshot.indicator_style == "off")
        ):
            return False
        guard = self._lease_manager.snapshot()
        if guard.fail_closed_all or client.exact_display_id not in guard.display_ids:
            return False
        return any(
            reason.code is ProtectionReasonCode.DIAGNOSTICS_REVEAL
            for reason in decision.snapshot.reasons_for_display(client.exact_display_id)
        )

    @staticmethod
    def _snapshot_payload(
        decision: ProtectionDecision,
        *,
        detail: str,
        created_at: str,
    ) -> dict[str, object]:
        snapshot = decision.snapshot
        displays = sorted(
            snapshot.displays,
            key=lambda display: (
                display.id not in snapshot.protected_display_ids,
                display.id,
            ),
        )
        return {
            "schema_version": _SCHEMA_VERSION,
            "type": "snapshot",
            "generation": snapshot.generation,
            "state": snapshot.state.value,
            "indicator_confirmed": decision.indicator_confirmed,
            "diagnostics_guard_active": snapshot.diagnostics_guard_active,
            "created_at": created_at,
            "reasons": [
                reason.to_payload(detail)
                for reason in snapshot.reasons_for_display(None)
            ],
            "displays": [
                PrivacyDiagnosticsServer._display_payload(
                    snapshot,
                    display.id,
                    display.is_primary,
                    decision.indicator_confirmed,
                    detail,
                )
                for display in displays
            ],
        }

    @staticmethod
    def _display_payload(
        snapshot: ProtectionSnapshot,
        display_id: int,
        is_primary: bool,
        indicator_confirmed: bool,
        detail: str,
    ) -> dict[str, object]:
        if snapshot.state in {ProtectionState.PAUSED, ProtectionState.FAILED}:
            display_state = snapshot.state
            screenshot_blocked = True
        elif display_id in snapshot.protected_display_ids:
            display_state = ProtectionState.PROTECTED
            screenshot_blocked = True
        else:
            display_state = ProtectionState.INACTIVE
            screenshot_blocked = False

        if snapshot.state in {ProtectionState.PAUSED, ProtectionState.FAILED}:
            ax_blocked = True
        elif snapshot.active_display_id == display_id:
            ax_blocked = snapshot.ax_blocked
        elif snapshot.active_display_id is None:
            ax_blocked = (
                display_id in snapshot.active_candidate_display_ids
                and display_id in snapshot.protected_display_ids
            )
        else:
            ax_blocked = False

        return {
            "id": display_id,
            "primary": is_primary,
            "state": display_state.value,
            "screenshot_blocked": screenshot_blocked,
            "ax_blocked": ax_blocked,
            "indicator_confirmed": indicator_confirmed,
            "reasons": [
                reason.to_payload(detail)
                for reason in snapshot.reasons_for_display(display_id)
            ],
        }

    def _queue_payload(
        self,
        selector: selectors.BaseSelector,
        clients: dict[int, _ClientState],
        client: _ClientState,
        payload: dict[str, object],
    ) -> bool:
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n"
        if len(encoded) > _MAX_LINE_BYTES + 1:
            encoded = json.dumps(
                _error("response_too_large"),
                separators=(",", ":"),
            ).encode() + b"\n"
        if (
            len(client.outgoing) >= _MAX_SEND_MESSAGES
            or client.outgoing_bytes + len(encoded) > _MAX_SEND_BYTES
        ):
            self._close_client(selector, clients, client)
            return False
        client.outgoing.append(encoded)
        client.outgoing_bytes += len(encoded)
        self._update_client_events(selector, clients, client)
        return True

    def _queue_error_and_close(
        self,
        selector: selectors.BaseSelector,
        clients: dict[int, _ClientState],
        client: _ClientState,
        code: str,
    ) -> None:
        client.received.clear()
        client.close_after_write = True
        if not self._queue_payload(selector, clients, client, _error(code)):
            return
        self._update_client_events(selector, clients, client)

    def _write_client(
        self,
        selector: selectors.BaseSelector,
        clients: dict[int, _ClientState],
        client: _ClientState,
    ) -> None:
        while client.outgoing:
            message = client.outgoing[0]
            try:
                sent = client.sock.send(message[client.send_offset :])
            except BlockingIOError:
                break
            if sent <= 0:
                self._close_client(selector, clients, client)
                return
            client.send_offset += sent
            if client.send_offset < len(message):
                break
            client.outgoing.popleft()
            client.outgoing_bytes -= len(message)
            client.send_offset = 0
        if not client.outgoing and client.close_after_write:
            self._close_client(selector, clients, client)
            return
        self._update_client_events(selector, clients, client)

    def _update_client_events(
        self,
        selector: selectors.BaseSelector,
        clients: dict[int, _ClientState],
        client: _ClientState,
    ) -> None:
        if client.sock.fileno() < 0:
            return
        events = 0 if client.close_after_write else selectors.EVENT_READ
        if client.outgoing:
            events |= selectors.EVENT_WRITE
        if events == 0:
            self._close_client(selector, clients, client)
            return
        with contextlib.suppress(KeyError, ValueError):
            selector.modify(client.sock, events, client)

    @staticmethod
    def _clear_lease_authorizations(
        clients: dict[int, _ClientState],
        lease_id: str,
    ) -> None:
        for client in clients.values():
            if client.lease_id == lease_id:
                client.detail = "category"
                client.lease_id = None
                client.exact_display_id = None
                client.protected_generation = None

    def _prune_dead_lease(self, clients: dict[int, _ClientState]) -> None:
        try:
            before = self._lease_manager.snapshot()
            after = self._lease_manager.prune_dead()
        except Exception as exc:
            logger.warning("privacy diagnostics watchdog failed: %s", type(exc).__name__)
            return
        if before == after:
            return
        for client in clients.values():
            client.detail = "category"
            client.lease_id = None
            client.exact_display_id = None
            client.protected_generation = None
        try:
            self._request_refresh()
        except Exception as exc:
            logger.warning("privacy diagnostics refresh failed: %s", type(exc).__name__)

    def _current_publication(self) -> _PublishedDecision | None:
        with self._decision_lock:
            return self._latest_publication

    def _current_generation(self) -> int:
        current = self._current_publication()
        return current.decision.snapshot.generation if current is not None else 0

    def _drain_wakeup(self) -> None:
        reader = self._wake_reader
        if reader is None:
            return
        while True:
            try:
                if not reader.recv(4096):
                    return
            except BlockingIOError:
                return
            except OSError:
                return

    def _wake(self) -> None:
        writer = self._wake_writer
        if writer is None:
            return
        with contextlib.suppress(BlockingIOError, OSError):
            writer.send(b"1")

    def _close_client(
        self,
        selector: selectors.BaseSelector,
        clients: dict[int, _ClientState],
        client: _ClientState,
    ) -> None:
        with contextlib.suppress(Exception):
            selector.unregister(client.sock)
        with self._connections_lock:
            self._connections.discard(client.sock)
        client.sock.close()
        for fileno, state in tuple(clients.items()):
            if state is client:
                clients.pop(fileno, None)
                break

    def _close_all_connections(self) -> None:
        with self._connections_lock:
            connections = tuple(self._connections)
            self._connections.clear()
        for connection in connections:
            with contextlib.suppress(OSError):
                connection.shutdown(socket.SHUT_RDWR)
            connection.close()

    def _validate_runtime_directory(self) -> None:
        try:
            directory_stat = self.socket_path.parent.lstat()
        except OSError as exc:
            raise RuntimeError("privacy diagnostics runtime directory unavailable") from exc
        if (
            not stat.S_ISDIR(directory_stat.st_mode)
            or directory_stat.st_uid != os.getuid()
            or stat.S_IMODE(directory_stat.st_mode) != 0o700
        ):
            raise RuntimeError("privacy diagnostics runtime directory permissions are unsafe")

    def _remove_stale_socket(self) -> None:
        try:
            socket_stat = self.socket_path.lstat()
        except FileNotFoundError:
            return
        if not stat.S_ISSOCK(socket_stat.st_mode) or socket_stat.st_uid != os.getuid():
            raise RuntimeError("privacy diagnostics socket path is unsafe")
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        probe.settimeout(0.1)
        try:
            probe.connect(str(self.socket_path))
        except (ConnectionRefusedError, FileNotFoundError):
            pass
        except OSError as exc:
            raise RuntimeError("privacy diagnostics socket state is uncertain") from exc
        else:
            raise RuntimeError("privacy diagnostics server is already active")
        finally:
            probe.close()
        current_stat = self.socket_path.lstat()
        if (current_stat.st_dev, current_stat.st_ino) != (
            socket_stat.st_dev,
            socket_stat.st_ino,
        ):
            raise RuntimeError("privacy diagnostics socket path changed")
        self.socket_path.unlink()

    def _unlink_owned_socket(self) -> None:
        socket_identity = self._owned_socket_identity
        self._owned_socket_identity = None
        self._unlink_socket_identity(socket_identity)

    def _unlink_socket_identity(
        self,
        socket_identity: tuple[int, int] | None,
    ) -> None:
        if socket_identity is None:
            return
        try:
            socket_stat = self.socket_path.lstat()
        except FileNotFoundError:
            return
        except OSError:
            return
        if (
            (socket_stat.st_dev, socket_stat.st_ino) == socket_identity
            and stat.S_ISSOCK(socket_stat.st_mode)
            and socket_stat.st_uid == os.getuid()
        ):
            with contextlib.suppress(OSError):
                self.socket_path.unlink()
