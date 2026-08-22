"""End-to-end boundaries for diagnostics-only exact privacy reasons."""

from __future__ import annotations

import json
import logging
import os
import socket
import time
from pathlib import Path

import pytest

from openchronicle import cli
from openchronicle.capture import scheduler, window_meta
from openchronicle.capture.ax_models import AXCaptureResult
from openchronicle.capture.privacy import DisplayInfo, ScreenRegion
from openchronicle.capture.privacy_diagnostics import PrivacyDiagnosticsServer
from openchronicle.capture.privacy_diagnostics_guard import DiagnosticsLeaseManager
from openchronicle.capture.protection import ProtectionSnapshot, ProtectionState
from openchronicle.capture.protection_monitor import ProtectionDecision
from openchronicle.capture.protection_reason import (
    DisplayProtectionReasons,
    ProtectionReason,
    ProtectionReasonCode,
)
from openchronicle.config import CaptureConfig, Config
from openchronicle.mcp.server import build_server
from openchronicle.model_failures import ModelFailureEventWriter
from openchronicle.store import fts


class _SafeAXProvider:
    """Return one fixed AX tree containing no diagnostics values."""

    @property
    def available(self) -> bool:
        return True

    def capture_frontmost(
        self, *, focused_window_only: bool = True
    ) -> AXCaptureResult:
        raw_json = {
            "apps": [
                {
                    "name": "Cursor",
                    "bundle_id": "com.todesktop.230313mzl4w4u92",
                    "is_frontmost": True,
                    "windows": [
                        {
                            "title": "safe.py",
                            "focused": True,
                            "elements": [
                                {
                                    "role": "AXStaticText",
                                    "title": "safe visible text",
                                    "value": "safe visible text",
                                }
                            ],
                        }
                    ],
                }
            ]
        }
        return AXCaptureResult(
            raw_json=raw_json,
            timestamp="2026-08-22T12:00:00+08:00",
            apps=raw_json["apps"],
            metadata={},
        )

    def capture_all_visible(self) -> AXCaptureResult:
        return self.capture_frontmost()

    def capture_app(
        self, app_name: str, *, focused_window_only: bool = True
    ) -> AXCaptureResult:
        return self.capture_frontmost(focused_window_only=focused_window_only)


class _StaticProtectionMonitor:
    """Return supplied decisions in order, then retain the latest decision."""

    def __init__(self, *decisions: ProtectionDecision) -> None:
        self._decisions = list(decisions)

    @property
    def snapshot(self) -> ProtectionSnapshot:
        return self._decisions[-1].snapshot

    def decision_for_capture(self, *, force: bool = True) -> ProtectionDecision:
        if len(self._decisions) > 1:
            return self._decisions.pop(0)
        return self._decisions[0]


def _private_other_display_decision(marker: str) -> ProtectionDecision:
    now = time.monotonic()
    displays = (
        DisplayInfo(1, ScreenRegion(0, 0, 100, 100), True),
        DisplayInfo(2, ScreenRegion(100, 0, 100, 100), False),
    )
    reason = ProtectionReason(
        ProtectionReasonCode.WINDOW_TITLE_RULE,
        display_id=2,
        app_name="Microsoft Edge",
        bundle_id="com.microsoft.edgemac",
        window_title=marker,
        rule=marker,
    )
    return ProtectionDecision(
        ProtectionSnapshot(
            generation=7,
            state=ProtectionState.PROTECTED,
            capture_mode="separate",
            indicator_style="pill",
            displays=displays,
            protected_display_ids=frozenset({2}),
            active_display_id=1,
            created_monotonic=now,
            fresh_until=now + 1.0,
            reason_display="hybrid",
            reason_detail="exact",
            reason_trigger="hover",
            display_reasons=DisplayProtectionReasons.from_reasons((reason,)),
        ),
        indicator_confirmed=True,
    )


def _search_capture_fts(query: str):
    with fts.cursor() as conn:
        return fts.search_captures(conn, query=query)


def _round_trip(socket_path: Path, message: dict[str, object]) -> dict[str, object]:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.connect(str(socket_path))
        client.sendall(json.dumps(message, separators=(",", ":")).encode() + b"\n")
        with client.makefile("rb") as reader:
            line = reader.readline()
    assert line
    payload = json.loads(line)
    assert isinstance(payload, dict)
    return payload


def test_exact_reason_never_enters_capture_or_fts(
    ac_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    marker = "private-reason-marker"
    monitor = _StaticProtectionMonitor(_private_other_display_decision(marker))
    monkeypatch.setattr(
        scheduler.window_meta,
        "active_window",
        lambda: window_meta.WindowMeta(
            app_name="Cursor",
            title="safe.py",
            bundle_id="com.todesktop.230313mzl4w4u92",
        ),
    )
    monkeypatch.setattr(scheduler.screenshot, "grab_many", lambda **_kwargs: [])

    with caplog.at_level(logging.DEBUG, logger="openchronicle.capture"):
        out = scheduler._build_capture(
            CaptureConfig(
                screenshot_monitor="separate",
                privacy_reason_display="hybrid",
                privacy_reason_detail="exact",
            ),
            _SafeAXProvider(),
            None,
            protection_monitor=monitor,
        )
        assert out is not None
        assert marker not in json.dumps(out, ensure_ascii=False)
        path = scheduler._write_capture(out)

    assert marker not in path.read_text()
    assert _search_capture_fts(marker) == []
    assert marker not in caplog.text

    status = cli._status_payload(Config(), model_checks=False)
    status_json = json.dumps(status, ensure_ascii=False)
    assert marker not in status_json
    assert "privacy_reason" not in status_json
    assert "protection_reason" not in status_json


def test_category_socket_and_guard_never_persist_exact_reason(
    ac_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = "private-reason-marker"
    decision = _private_other_display_decision(marker)
    monkeypatch.chdir(ac_root)
    runtime_dir = Path("runtime")
    runtime_dir.chmod(0o700)
    guard_path = runtime_dir / "privacy-reveal.guard"
    manager = DiagnosticsLeaseManager(
        guard_path,
        process_alive=lambda _pid: True,
    )
    manager.load()
    server = PrivacyDiagnosticsServer(
        runtime_dir / "privacy-diagnostics.sock",
        manager,
        request_refresh=lambda: None,
        wait_for_display_protection=lambda _display_id, _generation, _timeout: None,
    )
    server.publish(decision)
    server.start()
    try:
        category = _round_trip(
            server.socket_path,
            {"schema_version": 1, "action": "subscribe"},
        )
        denied = _round_trip(
            server.socket_path,
            {"schema_version": 1, "action": "subscribe", "detail": "exact"},
        )
    finally:
        server.stop()

    category_json = json.dumps(category, ensure_ascii=False)
    assert marker not in category_json
    assert denied == {
        "schema_version": 1,
        "type": "error",
        "code": "lease_required",
    }

    lease = manager.acquire(pid=os.getpid(), display_id=2)
    guard_raw = guard_path.read_text()
    assert json.loads(guard_raw) == {
        "schema_version": 1,
        "lease_id": lease.lease_id,
        "pid": os.getpid(),
        "display_ids": [2],
    }
    assert marker not in guard_raw


@pytest.mark.asyncio
async def test_exact_reason_has_no_model_failure_or_mcp_surface(ac_root: Path) -> None:
    marker = "private-reason-marker"
    decision = _private_other_display_decision(marker)
    exact_reason = decision.snapshot.reasons_for_display(2)[0].to_payload("exact")
    assert marker in json.dumps(exact_reason, ensure_ascii=False)

    event_path = ac_root / "runtime" / "model-failures.jsonl"
    writer = ModelFailureEventWriter(event_path, cooldown_seconds=0)
    assert writer.record(
        stage="timeline",
        model="test-model",
        error=RuntimeError("provider unavailable"),
    )
    event_raw = event_path.read_text()
    event = json.loads(event_raw)
    assert set(event) == {
        "schema_version",
        "id",
        "timestamp",
        "stage",
        "model",
        "error_type",
        "message",
    }
    assert marker not in event_raw
    assert "privacy_reason" not in event_raw
    assert "protection_reason" not in event_raw

    mcp_server = build_server(Config())
    tools = await mcp_server.list_tools()
    resources = await mcp_server.list_resources()
    tool_payloads = [tool.model_dump(mode="json") for tool in tools]
    resource_payloads = [resource.model_dump(mode="json") for resource in resources]
    surface_json = json.dumps(
        {"tools": tool_payloads, "resources": resource_payloads},
        ensure_ascii=False,
    )
    assert marker not in surface_json
    assert "privacy_reason" not in surface_json
    assert "protection_reason" not in surface_json
    assert "acquire_exact" not in surface_json
    assert "privacy-diagnostics" not in surface_json
