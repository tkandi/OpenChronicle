"""Owner-only Unix socket tests for protection diagnostics."""

from __future__ import annotations

import json
import os
import socket
import stat
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from io import BufferedReader
from pathlib import Path

import pytest

from openchronicle.capture import privacy_diagnostics as diagnostics_mod
from openchronicle.capture.privacy import (
    DisplayInfo,
    InventoryReadResult,
    ProtectionFailureReason,
    ScreenRegion,
    VisibleWindow,
    WindowInventory,
)
from openchronicle.capture.privacy_diagnostics import PrivacyDiagnosticsServer
from openchronicle.capture.privacy_diagnostics_guard import (
    DiagnosticsGuardSnapshot,
    DiagnosticsLeaseManager,
)
from openchronicle.capture.protection import (
    ProtectionSnapshot,
    ProtectionState,
    build_protection_snapshot,
)
from openchronicle.capture.protection_monitor import (
    PrivacyProtectionMonitor,
    ProtectionDecision,
)
from openchronicle.capture.protection_reason import (
    DisplayProtectionReasons,
    ProtectionReason,
    ProtectionReasonCode,
)
from openchronicle.capture.protection_smoothing import ProtectionPresentationPhase
from openchronicle.config import CaptureConfig

_MAX_LINE_BYTES = 64 * 1024


class FakeProtectionCallbacks:
    """Record the acquire/move handshake while returning generation 42."""

    def __init__(self, *, confirmed: bool = True) -> None:
        self.confirmed_generation = 42
        self.confirmed = confirmed
        self.refresh_requests = 0
        self.waited_display_ids: list[int] = []
        self.waited_after_generations: list[int] = []
        self.wait_observer: Callable[[int], None] | None = None

    def request_refresh(self) -> None:
        self.refresh_requests += 1

    def wait_for_display_protection(
        self,
        display_id: int,
        after_generation: int,
        timeout: float,
    ) -> int | None:
        self.waited_display_ids.append(display_id)
        self.waited_after_generations.append(after_generation)
        if self.wait_observer is not None:
            self.wait_observer(display_id)
        return self.confirmed_generation if self.confirmed else None


class _AlwaysConfirmedOverlay:
    def render(
        self,
        _snapshot: ProtectionSnapshot,
        timeout: float = 0.5,
        *,
        overlay_reasons_enabled: bool = True,
    ) -> bool:
        return True

    def clear(self, _generation: int, timeout: float = 0.5) -> bool:
        return True

    def mark_terminal(self) -> None:
        return None

    def close(self) -> None:
        return None


def _read_message(client: socket.socket) -> dict[str, object]:
    raw = bytearray()
    while b"\n" not in raw:
        chunk = client.recv(4096)
        if not chunk:
            raise AssertionError("diagnostics socket closed before one complete response")
        raw.extend(chunk)
        assert len(raw) <= _MAX_LINE_BYTES + 1
    line, remainder = bytes(raw).split(b"\n", 1)
    assert remainder == b""
    response = json.loads(line)
    assert isinstance(response, dict)
    return response


def _send_message(client: socket.socket, payload: dict[str, object]) -> None:
    client.sendall(json.dumps(payload, separators=(",", ":")).encode() + b"\n")


def _read_stream_message(reader: BufferedReader) -> dict[str, object]:
    line = reader.readline(_MAX_LINE_BYTES + 2)
    assert line.endswith(b"\n")
    assert len(line) <= _MAX_LINE_BYTES + 1
    response = json.loads(line)
    assert isinstance(response, dict)
    return response


def _connect(socket_path: Path) -> socket.socket:
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(1.0)
    client.connect(str(socket_path))
    return client


def _round_trip(socket_path: Path, payload: dict[str, object]) -> dict[str, object]:
    with _connect(socket_path) as client:
        _send_message(client, payload)
        return _read_message(client)


def _private_decision(
    marker: str = "private-window-title",
    *,
    generation: int = 7,
    diagnostics_display_id: int | None = None,
) -> ProtectionDecision:
    displays = (
        DisplayInfo(1, ScreenRegion(0, 0, 100, 100), True),
        DisplayInfo(2, ScreenRegion(100, 0, 100, 100), False),
    )
    reasons = [
        ProtectionReason(
            ProtectionReasonCode.WINDOW_TITLE_RULE,
            display_id=2,
            window_title=marker,
            rule="private-title-rule",
        )
    ]
    protected_ids = {2}
    if diagnostics_display_id is not None:
        protected_ids.add(diagnostics_display_id)
        reasons.append(
            ProtectionReason(
                ProtectionReasonCode.DIAGNOSTICS_REVEAL,
                display_id=diagnostics_display_id,
            )
        )
    snapshot = ProtectionSnapshot(
        generation=generation,
        state=ProtectionState.PROTECTED,
        capture_mode="separate",
        indicator_style="pill",
        displays=displays,
        protected_display_ids=frozenset(protected_ids),
        active_display_id=2,
        created_monotonic=time.monotonic(),
        fresh_until=time.monotonic() + 1.0,
        display_reasons=DisplayProtectionReasons.from_reasons(reasons),
    )
    return ProtectionDecision(snapshot=snapshot, indicator_confirmed=True)


def _start_test_server(
    tmp_path: Path,
    *,
    callbacks: FakeProtectionCallbacks | None = None,
    decision: ProtectionDecision | None = None,
    manager: DiagnosticsLeaseManager | None = None,
    clock: Callable[[], datetime] | None = None,
) -> PrivacyDiagnosticsServer:
    original_cwd = Path.cwd()
    os.chdir(tmp_path)
    runtime_dir = Path("runtime")
    runtime_dir.mkdir(mode=0o700, exist_ok=True)
    runtime_dir.chmod(0o700)
    callbacks = callbacks or FakeProtectionCallbacks()
    manager = manager or DiagnosticsLeaseManager(
        runtime_dir / "privacy-reveal.guard",
        process_alive=lambda _pid: True,
    )
    manager.load()
    server_kwargs = {
        "request_refresh": callbacks.request_refresh,
        "wait_for_display_protection": callbacks.wait_for_display_protection,
        "handshake_timeout": 0.05,
        "watchdog_seconds": 0.02,
    }
    if clock is not None:
        server_kwargs["clock"] = clock
    server = PrivacyDiagnosticsServer(
        runtime_dir / "privacy-diagnostics.sock",
        manager,
        **server_kwargs,
    )
    server.publish(decision or _private_decision())
    try:
        server.start()
    except BaseException:
        os.chdir(original_cwd)
        raise
    server._test_original_cwd = original_cwd  # type: ignore[attr-defined]
    server._test_working_dir = tmp_path  # type: ignore[attr-defined]
    return server


def _stop_test_server(server: PrivacyDiagnosticsServer) -> None:
    current_cwd = Path.cwd()
    working_dir = getattr(server, "_test_working_dir", current_cwd)
    os.chdir(working_dir)
    try:
        server.stop()
        assert server.thread is not None
        assert not server.thread.is_alive()
        assert not server.socket_path.exists()
    finally:
        os.chdir(getattr(server, "_test_original_cwd", current_cwd))


def test_category_subscription_never_contains_exact_values(tmp_path: Path) -> None:
    marker = "private-window-title"
    server = _start_test_server(tmp_path, decision=_private_decision(marker))
    try:
        response = _round_trip(
            server.socket_path,
            {"schema_version": 1, "action": "subscribe"},
        )
        assert response["type"] == "snapshot"
        assert response["displays"][0]["reasons"] == [
            {"code": "window_title_rule", "display_id": 2}
        ]
        assert marker not in json.dumps(response)
    finally:
        _stop_test_server(server)


def test_category_snapshot_exposes_safe_presentation_fields(tmp_path: Path) -> None:
    marker = "private-window-title"
    effective = _private_decision(marker).snapshot
    decision = ProtectionDecision(
        snapshot=replace(effective, indicator_style="off"),
        indicator_confirmed=True,
        raw_state=ProtectionState.PROTECTED,
        presentation_phase=ProtectionPresentationPhase.TRANSIENT_PROTECTED,
        overlay_reasons_enabled=False,
    )
    server = _start_test_server(tmp_path, decision=decision)
    try:
        response = _round_trip(
            server.socket_path,
            {"schema_version": 1, "action": "subscribe"},
        )
        assert response["raw_state"] == "protected"
        assert response["presentation_phase"] == "transient-protected"
        assert response["indicator_style"] == "off"
        assert response["overlay_reasons_enabled"] is False
        assert response["display_mapping_fallback_active"] is False
        assert marker not in json.dumps(response)
    finally:
        _stop_test_server(server)


def test_category_mapping_fallback_is_safe_and_reports_blocked_policy() -> None:
    app_marker = "private-fallback-app"
    bundle_marker = "private-fallback-bundle"
    title_marker = "private-fallback-title"
    rule_marker = "private-fallback-rule"
    reason = ProtectionReason(
        ProtectionReasonCode.WINDOW_TITLE_RULE,
        display_id=2,
        app_name=app_marker,
        bundle_id=bundle_marker,
        window_title=title_marker,
        rule=rule_marker,
    )
    snapshot = replace(
        _private_decision().snapshot,
        indicator_style="off",
        display_reasons=DisplayProtectionReasons.from_reasons([reason]),
        window_filterable=False,
        display_mapping_fallback_active=True,
    )
    decision = ProtectionDecision(
        snapshot=snapshot,
        indicator_confirmed=True,
        raw_state=ProtectionState.PROTECTED,
        presentation_phase=ProtectionPresentationPhase.TRANSIENT_MAPPING_FALLBACK,
        overlay_reasons_enabled=False,
        presentation_deadline_monotonic=11.0,
    )

    category = PrivacyDiagnosticsServer._snapshot_payload(
        decision,
        detail="category",
        created_at="2026-08-26T00:00:00Z",
    )
    exact = PrivacyDiagnosticsServer._snapshot_payload(
        decision,
        detail="exact",
        created_at="2026-08-26T00:00:00Z",
    )
    displays = {display["id"]: display for display in category["displays"]}

    assert category["schema_version"] == 1
    assert category["raw_state"] == "protected"
    assert category["state"] == "protected"
    assert category["presentation_phase"] == "transient-mapping-fallback"
    assert category["indicator_style"] == "off"
    assert category["overlay_reasons_enabled"] is False
    assert category["display_mapping_fallback_active"] is True
    assert displays[2]["state"] == "protected"
    assert displays[2]["screenshot_blocked"] is True
    assert displays[2]["ax_blocked"] is True
    assert displays[1]["state"] == "inactive"
    assert displays[1]["screenshot_blocked"] is False
    assert displays[1]["ax_blocked"] is False

    category_json = json.dumps(category)
    exact_json = json.dumps(exact)
    for marker in (app_marker, bundle_marker, title_marker, rule_marker):
        assert marker not in category_json
        assert marker in exact_json


def test_category_title_uncertainty_phase_does_not_expose_exact_marker() -> None:
    marker = "private-unknown-title-marker"
    reason = ProtectionReason(
        ProtectionReasonCode.WINDOW_TITLE_UNKNOWN,
        display_id=2,
        app_name="private-unknown-app",
        bundle_id="private-unknown-bundle",
        window_title=marker,
    )
    snapshot = replace(
        _private_decision().snapshot,
        indicator_style="off",
        display_reasons=DisplayProtectionReasons.from_reasons([reason]),
    )
    decision = ProtectionDecision(
        snapshot=snapshot,
        indicator_confirmed=True,
        raw_state=ProtectionState.PROTECTED,
        presentation_phase=ProtectionPresentationPhase.TRANSIENT_TITLE_UNCERTAINTY,
        overlay_reasons_enabled=False,
    )

    category = PrivacyDiagnosticsServer._snapshot_payload(
        decision,
        detail="category",
        created_at="2026-08-26T00:00:00Z",
    )
    exact = PrivacyDiagnosticsServer._snapshot_payload(
        decision,
        detail="exact",
        created_at="2026-08-26T00:00:00Z",
    )

    assert category["presentation_phase"] == "transient-title-uncertainty"
    assert category["indicator_style"] == "off"
    assert category["overlay_reasons_enabled"] is False
    displays = {display["id"]: display for display in category["displays"]}
    assert displays[2]["screenshot_blocked"] is True
    assert displays[2]["ax_blocked"] is True
    assert displays[1]["screenshot_blocked"] is False
    assert displays[1]["ax_blocked"] is False
    assert marker not in json.dumps(category)
    assert marker in json.dumps(exact)


def test_category_snapshot_omits_nonfinite_monotonic_values(tmp_path: Path) -> None:
    snapshot = replace(_private_decision().snapshot, created_monotonic=float("nan"))
    decision = ProtectionDecision(
        snapshot=snapshot,
        indicator_confirmed=True,
        presentation_deadline_monotonic=float("inf"),
    )
    server = _start_test_server(tmp_path, decision=decision)
    try:
        response = _round_trip(
            server.socket_path,
            {"schema_version": 1, "action": "subscribe"},
        )
        assert response["snapshot_created_monotonic"] is None
        assert response["presentation_deadline_monotonic"] is None
        encoded = json.dumps(response)
        assert "NaN" not in encoded
        assert "Infinity" not in encoded
    finally:
        _stop_test_server(server)


def test_category_mapping_failure_presentation_does_not_expose_exact_values() -> None:
    app_marker = "private-mapping-app"
    bundle_marker = "private-mapping-bundle"
    title_marker = "private-mapping-title"
    alternate_title_marker = "private-mapping-alternate-title"
    title_rule_marker = "private-mapping-title-rule"
    alternate_rule_marker = "private-mapping-alternate-rule"
    inventory = WindowInventory(
        windows=(
            VisibleWindow(
                app_marker,
                bundle_marker,
                f"{title_marker} {title_rule_marker}",
                ScreenRegion(110, 0, 80, 90),
                alternate_title=(
                    f"https://{alternate_title_marker}/{alternate_rule_marker}"
                ),
                window_id=73,
            ),
        ),
        displays=(
            DisplayInfo(1, ScreenRegion(0, 0, 100, 100), True),
            DisplayInfo(2, ScreenRegion(100, 0, 100, 100), False),
        ),
    )
    cfg = CaptureConfig(
        screenshot_monitor="separate",
        privacy_indicator_style="pill",
        deny_app_names=[app_marker],
        deny_bundle_ids=[bundle_marker],
        deny_window_title_patterns=[title_rule_marker, alternate_rule_marker],
    )
    times = iter([10.0, 10.0, 11.0, 11.0])
    monitor = PrivacyProtectionMonitor(
        cfg,
        config_path=Path("/nonexistent/config.toml"),
        overlay=_AlwaysConfirmedOverlay(),
        inventory_reader=lambda: InventoryReadResult(
            inventory,
            ProtectionFailureReason.ACTIVE_WINDOW_UNMAPPED,
        ),
        pause_reader=lambda: False,
        monotonic=lambda: next(times),
    )
    transient_decision = monitor.decision_for_capture(force=True)
    sustained_decision = monitor.decision_for_capture(force=True)

    transient = PrivacyDiagnosticsServer._snapshot_payload(
        transient_decision,
        detail="category",
        created_at="2026-08-25T00:00:00Z",
    )
    sustained = PrivacyDiagnosticsServer._snapshot_payload(
        sustained_decision,
        detail="category",
        created_at="2026-08-25T00:00:01Z",
    )
    exact = PrivacyDiagnosticsServer._snapshot_payload(
        transient_decision,
        detail="exact",
        created_at="2026-08-25T00:00:00Z",
    )

    assert transient["raw_state"] == "failed"
    assert transient["state"] == "failed"
    assert transient["presentation_phase"] == "transient-mapping-failure"
    assert transient["indicator_style"] == "off"
    assert transient["overlay_reasons_enabled"] is False
    assert transient["display_mapping_fallback_active"] is False
    assert transient["snapshot_created_monotonic"] == pytest.approx(10.0)
    assert transient["presentation_deadline_monotonic"] == pytest.approx(11.0)
    assert sustained["presentation_phase"] == "sustained-mapping-failure"
    assert sustained["indicator_style"] == "pill"
    assert sustained["overlay_reasons_enabled"] is True
    assert sustained["snapshot_created_monotonic"] == pytest.approx(11.0)
    assert sustained["presentation_deadline_monotonic"] is None

    category_json = json.dumps([transient, sustained])
    exact_json = json.dumps(exact)
    private_markers = (
        app_marker,
        bundle_marker,
        title_marker,
        alternate_title_marker,
        title_rule_marker,
        alternate_rule_marker,
    )
    for marker in private_markers:
        assert marker in exact_json
        assert marker not in category_json


@pytest.mark.parametrize(
    ("cfg", "expected_blocked"),
    [
        (CaptureConfig(screenshot_monitor="separate"), True),
        (
            CaptureConfig(
                screenshot_monitor="separate",
                screenshot_privacy_mode="mask-window",
                screenshot_privacy_fail_closed=False,
            ),
            True,
        ),
        (
            CaptureConfig(
                screenshot_monitor="separate",
                screenshot_privacy_mode="skip-monitor",
                screenshot_privacy_fail_closed=False,
            ),
            False,
        ),
    ],
    ids=["default", "filtered", "legacy-fail-open"],
)
def test_real_monitor_failed_diagnostics_follow_resolved_capture_policy(
    cfg: CaptureConfig,
    expected_blocked: bool,
) -> None:
    inventory = WindowInventory(
        windows=(),
        displays=(
            DisplayInfo(1, ScreenRegion(0, 0, 100, 100), True),
            DisplayInfo(2, ScreenRegion(100, 0, 100, 100), False),
        ),
    )
    monitor = PrivacyProtectionMonitor(
        cfg,
        config_path=Path("/nonexistent/config.toml"),
        overlay=_AlwaysConfirmedOverlay(),
        inventory_reader=lambda: InventoryReadResult(
            inventory,
            ProtectionFailureReason.ACTIVE_WINDOW_UNMAPPED,
        ),
        pause_reader=lambda: False,
    )
    try:
        decision = monitor.decision_for_capture(force=True)
    finally:
        monitor.stop()

    payload = PrivacyDiagnosticsServer._snapshot_payload(
        decision,
        detail="category",
        created_at="2026-08-25T00:00:00Z",
    )

    assert decision.failure_capture_blocked is expected_blocked
    assert all(
        display["screenshot_blocked"] is expected_blocked
        and display["ax_blocked"] is expected_blocked
        for display in payload["displays"]
    )


@pytest.mark.parametrize(
    "reason",
    [
        ProtectionFailureReason.INVENTORY_UNAVAILABLE,
        ProtectionFailureReason.HELPER_EXIT,
    ],
)
def test_active_guard_inventory_failure_publishes_fixed_category_diagnostics(
    tmp_path: Path,
    reason: ProtectionFailureReason,
) -> None:
    manager = DiagnosticsLeaseManager(
        tmp_path / "runtime" / "privacy-reveal.guard",
        process_alive=lambda _pid: True,
    )
    manager.load()
    manager.acquire(pid=os.getpid(), display_id=2)
    cfg = CaptureConfig(screenshot_privacy_fail_closed=False)
    monitor = PrivacyProtectionMonitor(
        cfg,
        config_path=tmp_path / "missing-config.toml",
        overlay=_AlwaysConfirmedOverlay(),
        inventory_reader=lambda: InventoryReadResult(None, reason),
        pause_reader=lambda: False,
        diagnostics_guard_reader=manager.snapshot,
    )
    decision = monitor.decision_for_capture(force=True)
    server = _start_test_server(tmp_path, manager=manager, decision=decision)
    try:
        response = _round_trip(
            server.socket_path,
            {"schema_version": 1, "action": "subscribe"},
        )
        denied = _round_trip(
            server.socket_path,
            {"schema_version": 1, "action": "subscribe", "detail": "exact"},
        )

        assert response["type"] == "snapshot"
        assert response["state"] == "failed"
        assert response["diagnostics_guard_active"] is True
        assert response["displays"] == []
        assert response["reasons"] == [
            {"code": reason.value, "display_id": None}
        ]
        assert set(response["reasons"][0]) == {"code", "display_id"}
        assert denied == {
            "schema_version": 1,
            "type": "error",
            "code": "lease_required",
        }
    finally:
        _stop_test_server(server)
        monitor.stop()


def test_created_at_is_stored_publish_time_in_rfc3339_utc(tmp_path: Path) -> None:
    published_at = datetime(2026, 8, 22, 4, 5, 6, 789000, tzinfo=UTC)
    clock_calls = 0

    def fixed_clock() -> datetime:
        nonlocal clock_calls
        clock_calls += 1
        return published_at

    decision = _private_decision(generation=7)
    server = _start_test_server(tmp_path, decision=decision, clock=fixed_clock)
    try:
        first = _round_trip(
            server.socket_path,
            {"schema_version": 1, "action": "subscribe"},
        )
        second = _round_trip(
            server.socket_path,
            {"schema_version": 1, "action": "subscribe"},
        )

        assert first["created_at"] == "2026-08-22T04:05:06.789000Z"
        assert second["created_at"] == first["created_at"]
        decoded = datetime.fromisoformat(str(first["created_at"]).replace("Z", "+00:00"))
        assert decoded == published_at
        assert server.publish(decision) is False
        assert clock_calls == 1
    finally:
        _stop_test_server(server)


def test_exact_response_requires_confirmed_display_lease(tmp_path: Path) -> None:
    callbacks = FakeProtectionCallbacks()
    server = _start_test_server(
        tmp_path,
        callbacks=callbacks,
        decision=_private_decision(),
    )
    try:
        denied = _round_trip(
            server.socket_path,
            {"schema_version": 1, "action": "subscribe", "detail": "exact"},
        )
        assert denied == {
            "schema_version": 1,
            "type": "error",
            "code": "lease_required",
        }

        lease = _round_trip(
            server.socket_path,
            {
                "schema_version": 1,
                "action": "acquire_exact",
                "pid": os.getpid(),
                "display_id": 2,
            },
        )
        assert callbacks.refresh_requests == 1
        assert callbacks.waited_display_ids == [2]
        assert lease["type"] == "lease"
        assert lease["protected_generation"] == callbacks.confirmed_generation
    finally:
        _stop_test_server(server)


def test_socket_and_runtime_directory_are_owner_only(tmp_path: Path) -> None:
    server = _start_test_server(tmp_path)
    try:
        assert stat.S_IMODE(server.socket_path.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(server.socket_path.stat().st_mode) == 0o600
        assert server.socket_path.parent.stat().st_uid == os.getuid()
        assert server.socket_path.stat().st_uid == os.getuid()
    finally:
        _stop_test_server(server)


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        (b'{"schema_version":1,"action":"private-invalid-json"\n', "invalid_json"),
        (
            b'{"schema_version":1,"action":"private-unknown-action"}\n',
            "unknown_action",
        ),
        (b'{"schema_version":1.0,"action":"subscribe"}\n', "unsupported_schema"),
        (
            b'{"schema_version":1,"action":"subscribe","detail":[]}\n',
            "invalid_request",
        ),
        (
            b'{"schema_version":' + (b"9" * 5000) + b',"action":"subscribe"}\n',
            "invalid_json",
        ),
    ],
    ids=[
        "invalid-json",
        "unknown-action",
        "float-schema",
        "non-string-detail",
        "integer-parser-limit",
    ],
)
def test_protocol_errors_are_fixed_and_never_echo_input(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    raw: bytes,
    code: str,
) -> None:
    server = _start_test_server(tmp_path)
    marker = "private-"
    try:
        with _connect(server.socket_path) as client:
            client.sendall(raw)
            response = _read_message(client)
        assert response == {"schema_version": 1, "type": "error", "code": code}
        assert marker not in json.dumps(response)
        assert marker not in caplog.text
    finally:
        _stop_test_server(server)


def test_overlong_line_is_rejected_without_echoing_body(tmp_path: Path) -> None:
    marker = b"private-overlong-body"
    server = _start_test_server(tmp_path)
    try:
        with _connect(server.socket_path) as client:
            client.sendall(marker + b"x" * _MAX_LINE_BYTES + b"\n")
            response = _read_message(client)
        assert response == {
            "schema_version": 1,
            "type": "error",
            "code": "line_too_long",
        }
        assert marker.decode() not in json.dumps(response)
    finally:
        _stop_test_server(server)


def test_stale_and_duplicate_generations_are_not_published(tmp_path: Path) -> None:
    server = _start_test_server(tmp_path, decision=_private_decision(generation=7))
    try:
        assert server.publish(_private_decision(generation=8)) is True
        assert server.publish(_private_decision(generation=8)) is False
        assert server.publish(_private_decision(generation=6)) is False
        response = _round_trip(
            server.socket_path,
            {"schema_version": 1, "action": "subscribe"},
        )
        assert response["generation"] == 8
    finally:
        _stop_test_server(server)


def test_exact_snapshot_follows_lease_only_after_confirmed_generation(tmp_path: Path) -> None:
    marker = "private-confirmed-title"
    callbacks = FakeProtectionCallbacks()
    server = _start_test_server(
        tmp_path,
        callbacks=callbacks,
        decision=_private_decision(marker, generation=7),
    )
    callbacks.wait_observer = lambda display_id: server.publish(
        _private_decision(
            marker,
            generation=callbacks.confirmed_generation,
            diagnostics_display_id=display_id,
        )
    )
    try:
        with _connect(server.socket_path) as client, client.makefile("rb") as reader:
            _send_message(client, {"schema_version": 1, "action": "subscribe"})
            category = _read_stream_message(reader)
            assert marker not in json.dumps(category)

            _send_message(
                client,
                {
                    "schema_version": 1,
                    "action": "acquire_exact",
                    "pid": os.getpid(),
                    "display_id": 2,
                },
            )
            lease = _read_stream_message(reader)
            exact = _read_stream_message(reader)

        assert lease["type"] == "lease"
        assert lease["protected_generation"] == callbacks.confirmed_generation
        assert exact["type"] == "snapshot"
        assert exact["generation"] == callbacks.confirmed_generation
        assert marker in json.dumps(exact)
    finally:
        _stop_test_server(server)


def test_acquire_timeout_rolls_back_guard_and_allows_conflict_free_retry(
    tmp_path: Path,
) -> None:
    callbacks = FakeProtectionCallbacks(confirmed=False)
    server = _start_test_server(tmp_path, callbacks=callbacks)
    guard_path = server.socket_path.parent / "privacy-reveal.guard"
    try:
        response = _round_trip(
            server.socket_path,
            {
                "schema_version": 1,
                "action": "acquire_exact",
                "pid": os.getpid(),
                "display_id": 2,
            },
        )
        assert response == {
            "schema_version": 1,
            "type": "error",
            "code": "protection_timeout",
        }
        assert not guard_path.exists()
        assert callbacks.refresh_requests == 2

        callbacks.confirmed = True
        acquired = _round_trip(
            server.socket_path,
            {
                "schema_version": 1,
                "action": "acquire_exact",
                "pid": os.getpid(),
                "display_id": 2,
            },
        )
        assert acquired["type"] == "lease"
        released = _round_trip(
            server.socket_path,
            {
                "schema_version": 1,
                "action": "release_exact",
                "pid": os.getpid(),
                "lease_id": acquired["lease_id"],
            },
        )
        assert released["released"] is True
        assert not guard_path.exists()
    finally:
        _stop_test_server(server)


def test_acquire_timeout_rollback_failure_stays_closed_until_dead_pid_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    marker = "private-rollback-error-marker"
    callbacks = FakeProtectionCallbacks(confirmed=False)
    runtime_dir = tmp_path / "runtime"
    manager = DiagnosticsLeaseManager(
        runtime_dir / "privacy-reveal.guard",
        process_alive=lambda _pid: True,
    )
    original_release = manager.release

    def fail_release(_lease_id: str, *, pid: int):
        raise OSError(marker)

    monkeypatch.setattr(manager, "release", fail_release)
    server = _start_test_server(tmp_path, callbacks=callbacks, manager=manager)
    guard_path = runtime_dir / "privacy-reveal.guard"
    try:
        response = _round_trip(
            server.socket_path,
            {
                "schema_version": 1,
                "action": "acquire_exact",
                "pid": os.getpid(),
                "display_id": 2,
            },
        )

        assert response == {
            "schema_version": 1,
            "type": "error",
            "code": "guard_unavailable",
        }
        assert json.loads(guard_path.read_text())["display_ids"] == [2]
        assert manager.snapshot() == DiagnosticsGuardSnapshot(frozenset({2}), False)
        assert callbacks.refresh_requests == 2
        assert marker not in caplog.text
    finally:
        monkeypatch.setattr(manager, "release", original_release)
        _stop_test_server(server)

    restarted = DiagnosticsLeaseManager(guard_path, process_alive=lambda _pid: False)
    assert restarted.load() == DiagnosticsGuardSnapshot(frozenset(), False)
    assert not guard_path.exists()


def test_move_timeout_keeps_the_known_lease_releasable(tmp_path: Path) -> None:
    callbacks = FakeProtectionCallbacks()
    server = _start_test_server(tmp_path, callbacks=callbacks)
    guard_path = server.socket_path.parent / "privacy-reveal.guard"
    try:
        with _connect(server.socket_path) as client:
            _send_message(
                client,
                {
                    "schema_version": 1,
                    "action": "acquire_exact",
                    "pid": os.getpid(),
                    "display_id": 1,
                },
            )
            acquired = _read_message(client)
            callbacks.confirmed = False
            _send_message(
                client,
                {
                    "schema_version": 1,
                    "action": "move_exact",
                    "pid": os.getpid(),
                    "lease_id": acquired["lease_id"],
                    "display_id": 2,
                },
            )
            assert _read_message(client) == {
                "schema_version": 1,
                "type": "error",
                "code": "protection_timeout",
            }
            assert json.loads(guard_path.read_text())["display_ids"] == [1, 2]

            _send_message(
                client,
                {
                    "schema_version": 1,
                    "action": "release_exact",
                    "pid": os.getpid(),
                    "lease_id": acquired["lease_id"],
                },
            )
            assert _read_message(client)["released"] is True
        assert not guard_path.exists()
    finally:
        _stop_test_server(server)


def test_wrong_peer_pid_and_lease_cannot_release_guard(tmp_path: Path) -> None:
    callbacks = FakeProtectionCallbacks()
    server = _start_test_server(tmp_path, callbacks=callbacks)
    guard_path = server.socket_path.parent / "privacy-reveal.guard"
    try:
        wrong_pid = _round_trip(
            server.socket_path,
            {
                "schema_version": 1,
                "action": "acquire_exact",
                "pid": os.getpid() + 1,
                "display_id": 2,
            },
        )
        assert wrong_pid == {
            "schema_version": 1,
            "type": "error",
            "code": "pid_mismatch",
        }

        lease = _round_trip(
            server.socket_path,
            {
                "schema_version": 1,
                "action": "acquire_exact",
                "pid": os.getpid(),
                "display_id": 2,
            },
        )
        rejected = _round_trip(
            server.socket_path,
            {
                "schema_version": 1,
                "action": "release_exact",
                "pid": os.getpid(),
                "lease_id": "0" * 32,
            },
        )
        assert rejected == {
            "schema_version": 1,
            "type": "error",
            "code": "invalid_lease",
        }
        assert lease["lease_id"] != "0" * 32
        assert guard_path.exists()
    finally:
        _stop_test_server(server)


def test_stale_release_cannot_clear_a_newer_lease(tmp_path: Path) -> None:
    callbacks = FakeProtectionCallbacks()
    server = _start_test_server(tmp_path, callbacks=callbacks)
    guard_path = server.socket_path.parent / "privacy-reveal.guard"
    try:
        with _connect(server.socket_path) as client:
            _send_message(
                client,
                {
                    "schema_version": 1,
                    "action": "acquire_exact",
                    "pid": os.getpid(),
                    "display_id": 1,
                },
            )
            first = _read_message(client)
            _send_message(
                client,
                {
                    "schema_version": 1,
                    "action": "release_exact",
                    "pid": os.getpid(),
                    "lease_id": first["lease_id"],
                },
            )
            assert _read_message(client)["released"] is True
            _send_message(
                client,
                {
                    "schema_version": 1,
                    "action": "acquire_exact",
                    "pid": os.getpid(),
                    "display_id": 2,
                },
            )
            second = _read_message(client)
            _send_message(
                client,
                {
                    "schema_version": 1,
                    "action": "release_exact",
                    "pid": os.getpid(),
                    "lease_id": first["lease_id"],
                },
            )
            rejected = _read_message(client)

        assert second["lease_id"] != first["lease_id"]
        assert rejected == {
            "schema_version": 1,
            "type": "error",
            "code": "invalid_lease",
        }
        assert json.loads(guard_path.read_text()) == {
            "schema_version": 1,
            "lease_id": second["lease_id"],
            "pid": os.getpid(),
            "display_ids": [2],
        }
    finally:
        _stop_test_server(server)


@pytest.mark.parametrize(
    ("pid", "display_id"),
    [
        (1 << 31, 2),
        (os.getpid(), 1 << 32),
        (True, 2),
        (os.getpid(), True),
    ],
)
def test_acquire_rejects_out_of_range_pid_or_display_id(
    tmp_path: Path,
    pid: int,
    display_id: int,
) -> None:
    server = _start_test_server(tmp_path)
    guard_path = server.socket_path.parent / "privacy-reveal.guard"
    try:
        response = _round_trip(
            server.socket_path,
            {
                "schema_version": 1,
                "action": "acquire_exact",
                "pid": pid,
                "display_id": display_id,
            },
        )
        assert response == {
            "schema_version": 1,
            "type": "error",
            "code": "invalid_request",
        }
        assert not guard_path.exists()
    finally:
        _stop_test_server(server)


def test_disconnect_keeps_guard_until_process_death_is_confirmed(tmp_path: Path) -> None:
    server = _start_test_server(tmp_path)
    guard_path = server.socket_path.parent / "privacy-reveal.guard"
    try:
        lease = _round_trip(
            server.socket_path,
            {
                "schema_version": 1,
                "action": "acquire_exact",
                "pid": os.getpid(),
                "display_id": 2,
            },
        )
        assert lease["type"] == "lease"
        time.sleep(0.06)
        assert guard_path.exists()
        assert json.loads(guard_path.read_text())["lease_id"] == lease["lease_id"]
    finally:
        _stop_test_server(server)


def test_move_protects_both_displays_until_destination_is_confirmed(
    tmp_path: Path,
) -> None:
    callbacks = FakeProtectionCallbacks()
    runtime_dir = tmp_path / "runtime"
    manager = DiagnosticsLeaseManager(
        runtime_dir / "privacy-reveal.guard",
        process_alive=lambda _pid: True,
    )
    server = _start_test_server(tmp_path, callbacks=callbacks, manager=manager)
    observed: list[frozenset[int]] = []
    persisted: list[list[int]] = []
    protected: list[frozenset[int]] = []
    displays = (
        DisplayInfo(1, ScreenRegion(0, 0, 100, 100), True),
        DisplayInfo(2, ScreenRegion(100, 0, 100, 100), False),
    )
    inventory = WindowInventory(
        windows=(VisibleWindow("Cursor", "cursor", "main.py", displays[0].region, True),),
        displays=displays,
    )

    def observe_guard(_display_id: int) -> None:
        guard = manager.snapshot()
        observed.append(guard.display_ids)
        persisted.append(json.loads((runtime_dir / "privacy-reveal.guard").read_text())["display_ids"])
        protected.append(
            build_protection_snapshot(
                CaptureConfig(screenshot_monitor="separate"),
                inventory,
                paused=False,
                generation=len(protected) + 1,
                now=time.monotonic(),
                diagnostic_display_ids=guard.display_ids,
            ).protected_display_ids
        )

    callbacks.wait_observer = observe_guard
    try:
        with _connect(server.socket_path) as client:
            _send_message(
                client,
                {
                    "schema_version": 1,
                    "action": "acquire_exact",
                    "pid": os.getpid(),
                    "display_id": 1,
                },
            )
            acquired = _read_message(client)
            _send_message(
                client,
                {
                    "schema_version": 1,
                    "action": "move_exact",
                    "pid": os.getpid(),
                    "lease_id": acquired["lease_id"],
                    "display_id": 2,
                },
            )
            moved = _read_message(client)

        assert observed == [frozenset({1}), frozenset({1, 2})]
        assert persisted == [[1], [1, 2]]
        assert protected == [frozenset({1}), frozenset({1, 2})]
        assert manager.snapshot().display_ids == frozenset({2})
        assert callbacks.refresh_requests == 3
        assert moved["lease_id"] == acquired["lease_id"]
        assert moved["display_id"] == 2
    finally:
        _stop_test_server(server)


def test_release_clears_matching_guard_and_requests_refresh(tmp_path: Path) -> None:
    callbacks = FakeProtectionCallbacks()
    server = _start_test_server(tmp_path, callbacks=callbacks)
    guard_path = server.socket_path.parent / "privacy-reveal.guard"
    try:
        with _connect(server.socket_path) as client:
            _send_message(
                client,
                {
                    "schema_version": 1,
                    "action": "acquire_exact",
                    "pid": os.getpid(),
                    "display_id": 2,
                },
            )
            acquired = _read_message(client)
            _send_message(
                client,
                {
                    "schema_version": 1,
                    "action": "release_exact",
                    "pid": os.getpid(),
                    "lease_id": acquired["lease_id"],
                },
            )
            released = _read_message(client)

        assert released == {
            "schema_version": 1,
            "type": "lease",
            "lease_id": acquired["lease_id"],
            "released": True,
        }
        assert callbacks.refresh_requests == 2
        assert not guard_path.exists()
    finally:
        _stop_test_server(server)


def test_multiple_subscribers_receive_only_new_generations(tmp_path: Path) -> None:
    server = _start_test_server(tmp_path, decision=_private_decision(generation=7))
    try:
        with _connect(server.socket_path) as first, _connect(server.socket_path) as second:
            for client in (first, second):
                _send_message(client, {"schema_version": 1, "action": "subscribe"})
                assert _read_message(client)["generation"] == 7

            assert server.publish(_private_decision(generation=8)) is True
            for client in (first, second):
                assert _read_message(client)["generation"] == 8

            assert server.publish(_private_decision(generation=8)) is False
            for client in (first, second):
                client.settimeout(0.1)
                with pytest.raises(socket.timeout):
                    client.recv(1)
    finally:
        _stop_test_server(server)


def test_server_rejects_clients_beyond_fixed_connection_cap(tmp_path: Path) -> None:
    server = _start_test_server(tmp_path)
    clients: list[socket.socket] = []
    try:
        for _index in range(16):
            client = _connect(server.socket_path)
            clients.append(client)
            _send_message(client, {"schema_version": 1, "action": "subscribe"})
            assert _read_message(client)["type"] == "snapshot"

        with _connect(server.socket_path) as rejected:
            assert rejected.recv(1) == b""
    finally:
        for client in clients:
            client.close()
        _stop_test_server(server)


def test_non_owner_peer_is_rejected_before_request_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = _start_test_server(tmp_path)
    monkeypatch.setattr(
        diagnostics_mod,
        "_peer_credentials",
        lambda _client: (os.getuid() + 1, os.getpid()),
    )
    try:
        with _connect(server.socket_path) as client:
            assert client.recv(1) == b""
    finally:
        _stop_test_server(server)


def test_second_server_cannot_replace_a_live_socket(tmp_path: Path) -> None:
    callbacks = FakeProtectionCallbacks()
    server = _start_test_server(tmp_path, callbacks=callbacks)
    second_manager = DiagnosticsLeaseManager(
        Path("runtime") / "second.guard",
        process_alive=lambda _pid: True,
    )
    second_manager.load()
    second = PrivacyDiagnosticsServer(
        server.socket_path,
        second_manager,
        request_refresh=callbacks.request_refresh,
        wait_for_display_protection=callbacks.wait_for_display_protection,
    )
    try:
        with pytest.raises(RuntimeError, match="already active"):
            second.start()
        response = _round_trip(
            server.socket_path,
            {"schema_version": 1, "action": "subscribe"},
        )
        assert response["type"] == "snapshot"
    finally:
        second.stop()
        _stop_test_server(server)


def test_stop_does_not_unlink_a_same_user_replacement_socket(tmp_path: Path) -> None:
    server = _start_test_server(tmp_path)
    replacement = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    replacement_path = server.socket_path.with_name("replacement.sock")
    try:
        replacement.bind(str(replacement_path))
        os.chmod(replacement_path, 0o600)
        replacement.listen(1)
        replacement_identity = (
            replacement_path.lstat().st_dev,
            replacement_path.lstat().st_ino,
        )
        server.socket_path.unlink()
        replacement_path.replace(server.socket_path)

        server.stop()
        server.stop()

        current = server.socket_path.lstat()
        assert (current.st_dev, current.st_ino) == replacement_identity
        assert stat.S_ISSOCK(current.st_mode)
        assert current.st_uid == os.getuid()
        assert server.thread is not None
        assert not server.thread.is_alive()
    finally:
        server.stop()
        replacement.close()
        server.socket_path.unlink(missing_ok=True)
        replacement_path.unlink(missing_ok=True)
        os.chdir(getattr(server, "_test_original_cwd", Path.cwd()))


def test_stop_is_idempotent_and_unlinks_socket(tmp_path: Path) -> None:
    server = _start_test_server(tmp_path)

    _stop_test_server(server)
    _stop_test_server(server)


def test_stop_disconnects_clients_before_blocked_handshake_finishes(
    tmp_path: Path,
) -> None:
    callbacks = FakeProtectionCallbacks()
    wait_started = threading.Event()
    release_wait = threading.Event()

    def blocked_wait(
        display_id: int,
        after_generation: int,
        timeout: float,
    ) -> int | None:
        callbacks.waited_display_ids.append(display_id)
        callbacks.waited_after_generations.append(after_generation)
        wait_started.set()
        assert release_wait.wait(timeout=1.0)
        return callbacks.confirmed_generation

    callbacks.wait_for_display_protection = blocked_wait  # type: ignore[method-assign]
    server = _start_test_server(tmp_path, callbacks=callbacks)
    stop_thread = threading.Thread(target=server.stop)
    try:
        with _connect(server.socket_path) as client:
            _send_message(
                client,
                {
                    "schema_version": 1,
                    "action": "acquire_exact",
                    "pid": os.getpid(),
                    "display_id": 2,
                },
            )
            assert wait_started.wait(timeout=0.5)
            stop_thread.start()
            client.settimeout(0.25)
            assert client.recv(1) == b""
    finally:
        release_wait.set()
        stop_thread.join(timeout=1.0)
        _stop_test_server(server)

    assert not stop_thread.is_alive()


def test_immediate_stop_cleans_wakeup_sockets_before_worker_initializes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_started = threading.Event()
    release_run = threading.Event()
    original_run = PrivacyDiagnosticsServer._run

    def delayed_run(server: PrivacyDiagnosticsServer) -> None:
        run_started.set()
        assert release_run.wait(timeout=1.0)
        original_run(server)

    monkeypatch.setattr(PrivacyDiagnosticsServer, "_run", delayed_run)
    server = _start_test_server(tmp_path)
    assert run_started.wait(timeout=0.5)
    stop_thread = threading.Thread(target=server.stop)
    try:
        stop_thread.start()
        release_run.set()
        stop_thread.join(timeout=1.0)
        assert not stop_thread.is_alive()
        assert server._listener is None
        assert server._wake_reader is None
        assert server._wake_writer is None
    finally:
        release_run.set()
        stop_thread.join(timeout=1.0)
        _stop_test_server(server)
