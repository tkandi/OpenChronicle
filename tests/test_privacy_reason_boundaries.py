"""End-to-end boundaries for diagnostics-only exact privacy reasons."""

from __future__ import annotations

import json
import logging
import os
import socket
import time
from io import BufferedReader
from pathlib import Path

import pytest

from openchronicle import cli, paths
from openchronicle import config as config_mod
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
from openchronicle.store import fts
from openchronicle.writer import llm as llm_mod


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
                                    "title": "knownsafefts visible text",
                                    "value": "knownsafefts visible text",
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


def _private_other_display_decision(
    marker: str,
    *,
    generation: int = 7,
    diagnostics_display_ids: frozenset[int] = frozenset(),
) -> ProtectionDecision:
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
    reasons = [reason]
    reasons.extend(
        ProtectionReason(
            ProtectionReasonCode.DIAGNOSTICS_REVEAL,
            display_id=display_id,
        )
        for display_id in sorted(diagnostics_display_ids)
    )
    return ProtectionDecision(
        ProtectionSnapshot(
            generation=generation,
            state=ProtectionState.PROTECTED,
            capture_mode="separate",
            indicator_style="pill",
            displays=displays,
            protected_display_ids=frozenset({2}) | diagnostics_display_ids,
            active_display_id=1,
            created_monotonic=now,
            fresh_until=now + 1.0,
            reason_display="hybrid",
            reason_detail="exact",
            reason_trigger="hover",
            display_reasons=DisplayProtectionReasons.from_reasons(reasons),
            diagnostics_guard_active=bool(diagnostics_display_ids),
        ),
        indicator_confirmed=True,
    )


def _search_capture_fts(query: str):
    with fts.cursor() as conn:
        return fts.search_captures(conn, query=query)


def _round_trip(socket_path: Path, message: dict[str, object]) -> dict[str, object]:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.connect(str(socket_path))
        return _request(client, message)


def _request(
    client: socket.socket,
    message: dict[str, object],
    *,
    reader: BufferedReader | None = None,
) -> dict[str, object]:
    client.sendall(json.dumps(message, separators=(",", ":")).encode() + b"\n")
    if reader is not None:
        line = reader.readline()
        assert line.endswith(b"\n")
        payload = json.loads(line)
        assert isinstance(payload, dict)
        return payload

    raw = bytearray()
    while b"\n" not in raw:
        chunk = client.recv(4096)
        assert chunk
        raw.extend(chunk)
    line, remainder = bytes(raw).split(b"\n", 1)
    assert remainder == b""
    assert line
    payload = json.loads(line)
    assert isinstance(payload, dict)
    return payload


def _marker_config(ac_root: Path, marker: str) -> Config:
    config_path = ac_root / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                "[models.default]",
                'model = "boundary-test-model"',
                'api_key_env = ""',
                "",
                "[capture]",
                'privacy_reason_display = "hybrid"',
                'privacy_reason_detail = "exact"',
                f"deny_window_title_patterns = [{json.dumps(marker)}]",
                "",
                "[search]",
                "default_top_k = 17",
                "",
            ]
        )
    )
    cfg = config_mod.load(config_path)
    assert cfg.capture.deny_window_title_patterns == [marker]
    assert marker in json.dumps(cfg.capture.__dict__, ensure_ascii=False)
    return cfg


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
    safe_hits = _search_capture_fts("knownsafefts")
    assert [hit.id for hit in safe_hits] == [path.stem]
    assert _search_capture_fts(marker) == []
    assert marker not in caplog.text


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
    published = decision
    server: PrivacyDiagnosticsServer

    def request_refresh() -> None:
        nonlocal published
        guard = manager.snapshot()
        published = _private_other_display_decision(
            marker,
            generation=published.snapshot.generation + 1,
            diagnostics_display_ids=guard.display_ids,
        )
        server.publish(published)

    def wait_for_display_protection(
        display_id: int,
        after_generation: int,
        _timeout: float,
    ) -> int | None:
        assert published.snapshot.generation > after_generation
        assert display_id in published.snapshot.protected_display_ids
        assert any(
            reason.code is ProtectionReasonCode.DIAGNOSTICS_REVEAL
            for reason in published.snapshot.reasons_for_display(display_id)
        )
        return published.snapshot.generation

    server = PrivacyDiagnosticsServer(
        runtime_dir / "privacy-diagnostics.sock",
        manager,
        request_refresh=request_refresh,
        wait_for_display_protection=wait_for_display_protection,
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

        by_display = {display["id"]: display for display in category["displays"]}
        assert category["type"] == "snapshot"
        assert by_display[2]["reasons"] == [
            {
                "code": ProtectionReasonCode.WINDOW_TITLE_RULE.value,
                "display_id": 2,
            }
        ]
        assert marker not in json.dumps(category, ensure_ascii=False)
        assert denied == {
            "schema_version": 1,
            "type": "error",
            "code": "lease_required",
        }

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(str(server.socket_path))
            with client.makefile("rb") as reader:
                lease = _request(
                    client,
                    {
                        "schema_version": 1,
                        "action": "acquire_exact",
                        "pid": os.getpid(),
                        "display_id": 2,
                    },
                    reader=reader,
                )
                guard_raw = guard_path.read_text()
                assert json.loads(guard_raw) == {
                    "schema_version": 1,
                    "lease_id": lease["lease_id"],
                    "pid": os.getpid(),
                    "display_ids": [2],
                }
                assert marker not in guard_raw

                exact = _request(
                    client,
                    {"schema_version": 1, "action": "subscribe", "detail": "exact"},
                    reader=reader,
                )
                assert exact["type"] == "snapshot"
                assert marker in json.dumps(exact, ensure_ascii=False)

                released = _request(
                    client,
                    {
                        "schema_version": 1,
                        "action": "release_exact",
                        "pid": os.getpid(),
                        "lease_id": lease["lease_id"],
                    },
                    reader=reader,
                )
                assert released == {
                    "schema_version": 1,
                    "type": "lease",
                    "lease_id": lease["lease_id"],
                    "released": True,
                }
                assert not guard_path.exists()
    finally:
        server.stop()


@pytest.mark.asyncio
async def test_marker_bearing_runtime_has_no_status_model_failure_or_mcp_surface(
    ac_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = "private-reason-marker"
    cfg = _marker_config(ac_root, marker)
    decision = _private_other_display_decision(marker)
    exact_snapshot = PrivacyDiagnosticsServer._snapshot_payload(
        decision,
        detail="exact",
        created_at="2026-08-22T12:00:00.000000Z",
    )
    assert marker in json.dumps(exact_snapshot, ensure_ascii=False)

    status = cli._status_payload(cfg, model_checks=False)
    status_json = json.dumps(status, ensure_ascii=False)
    assert {model["model"] for model in status["models"].values()} == {
        "boundary-test-model"
    }
    assert marker not in status_json
    assert "privacy_reason" not in status_json
    assert "protection_reason" not in status_json

    monkeypatch.delenv("OPENCHRONICLE_LLM_MOCK", raising=False)
    import litellm

    class ProviderUnavailable(Exception):
        pass

    monkeypatch.setattr(
        litellm,
        "completion",
        lambda **_kwargs: (_ for _ in ()).throw(
            ProviderUnavailable("provider unavailable")
        ),
    )
    with pytest.raises(ProviderUnavailable):
        llm_mod.call_llm(
            cfg,
            "timeline",
            messages=[{"role": "user", "content": "safe boundary probe"}],
        )

    event_path = paths.model_failure_events_file()
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
    assert event["model"] == cfg.model_for("timeline").model
    assert marker not in event_raw
    assert "privacy_reason" not in event_raw
    assert "protection_reason" not in event_raw

    mcp_server = build_server(cfg)
    tools = await mcp_server.list_tools()
    resources = await mcp_server.list_resources()
    tool_payloads = [tool.model_dump(mode="json") for tool in tools]
    resource_payloads = [resource.model_dump(mode="json") for resource in resources]
    search_tool = next(tool for tool in tool_payloads if tool["name"] == "search")
    assert search_tool["inputSchema"]["properties"]["top_k"]["default"] == 17
    surface_json = json.dumps(
        {"tools": tool_payloads, "resources": resource_payloads},
        ensure_ascii=False,
    )
    assert marker not in surface_json
    assert "privacy_reason" not in surface_json
    assert "protection_reason" not in surface_json
    assert "acquire_exact" not in surface_json
    assert "privacy-diagnostics" not in surface_json
