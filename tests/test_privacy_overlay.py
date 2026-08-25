import importlib.resources
import json
import os
import stat
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from openchronicle.capture import privacy_overlay
from openchronicle.capture.privacy import DisplayInfo, ScreenRegion
from openchronicle.capture.privacy_overlay import (
    PrivacyOverlayClient,
    _maybe_compile_overlay,
    _OverlayAcknowledgement,
    _resolve_overlay_path,
    _SubprocessOverlayTransport,
)
from openchronicle.capture.protection import ProtectionSnapshot, ProtectionState
from openchronicle.capture.protection_reason import (
    DisplayProtectionReasons,
    ProtectionReason,
    ProtectionReasonCode,
)


class FakeTransport:
    def __init__(
        self,
        *responses: bool,
        window_ids: tuple[int, ...] = (),
    ) -> None:
        self.writes: list[str] = []
        self.responses = list(responses)
        self.window_ids = window_ids
        self.closed = False

    def send_and_wait(
        self, line: str, generation: int, timeout: float
    ) -> _OverlayAcknowledgement | None:
        self.writes.append(line)
        rendered = self.responses.pop(0) if self.responses else False
        return _OverlayAcknowledgement(
            generation=generation,
            rendered=rendered,
            error=None if rendered else "test-error",
            window_ids=self.window_ids,
        )

    def close(self) -> None:
        self.closed = True


class FailingTransport(FakeTransport):
    def send_and_wait(
        self, line: str, generation: int, timeout: float
    ) -> _OverlayAcknowledgement | None:
        raise BrokenPipeError


class BlockingTransport(FakeTransport):
    def __init__(
        self,
        *responses: bool,
        window_ids: tuple[int, ...] = (),
    ) -> None:
        super().__init__(*responses)
        self.window_ids = window_ids
        self.started = threading.Event()
        self.release = threading.Event()
        self.closed_before_release = False

    def send_and_wait(
        self, line: str, generation: int, timeout: float
    ) -> _OverlayAcknowledgement | None:
        self.writes.append(line)
        self.started.set()
        assert self.release.wait(timeout=1.0)
        rendered = self.responses.pop(0) if self.responses else False
        return _OverlayAcknowledgement(
            generation=generation,
            rendered=rendered,
            error=None if rendered else "test-error",
            window_ids=self.window_ids,
        )

    def close(self) -> None:
        self.closed_before_release = not self.release.is_set()
        super().close()


class AttemptObservingLock:
    """Test-only lock that proves a second caller reached the contention point."""

    def __init__(self, *, bypass: bool = False) -> None:
        self._lock = threading.Lock()
        self._attempt_guard = threading.Lock()
        self._attempts = 0
        self.second_attempted = threading.Event()
        self._bypass = bypass

    def __enter__(self) -> "AttemptObservingLock":
        with self._attempt_guard:
            self._attempts += 1
            if self._attempts >= 2:
                self.second_attempted.set()
        if not self._bypass:
            self._lock.acquire()
        return self

    def __exit__(self, *_args) -> None:
        if not self._bypass:
            self._lock.release()

    def locked(self) -> bool:
        return self._lock.locked()


@pytest.fixture
def snapshot() -> ProtectionSnapshot:
    right = DisplayInfo(2, ScreenRegion(100, 0, 100, 100), False)
    return ProtectionSnapshot(
        generation=12,
        state=ProtectionState.PROTECTED,
        capture_mode="separate",
        indicator_style="pill",
        displays=(right,),
        protected_display_ids=frozenset({2}),
        active_display_id=1,
        created_monotonic=1.0,
        fresh_until=1.25,
    )


def _private_title_reason(display_id: int) -> ProtectionReason:
    return ProtectionReason(
        code=ProtectionReasonCode.WINDOW_TITLE_RULE,
        display_id=display_id,
        app_name="Edge",
        bundle_id="com.microsoft.edgemac",
        window_title="InPrivate",
        rule="InPrivate",
    )


def _protected_snapshot(
    *,
    reason_display: str = "hybrid",
    reason_detail: str = "exact",
    reason_trigger: str = "hover",
    reasons: tuple[ProtectionReason, ...] = (),
) -> ProtectionSnapshot:
    displays = (
        DisplayInfo(1, ScreenRegion(0, 0, 100, 100), True),
        DisplayInfo(2, ScreenRegion(100, 0, 100, 100), False),
    )
    return ProtectionSnapshot(
        generation=42,
        state=ProtectionState.PROTECTED,
        capture_mode="separate",
        indicator_style="pill",
        displays=displays,
        protected_display_ids=frozenset({2}),
        active_display_id=2,
        created_monotonic=1.0,
        fresh_until=1.25,
        reason_display=reason_display,
        reason_detail=reason_detail,
        reason_trigger=reason_trigger,
        display_reasons=DisplayProtectionReasons.from_reasons(reasons),
    )


@pytest.fixture
def fake_transport() -> FakeTransport:
    return FakeTransport(True)


def _helper_script(tmp_path: Path, body: str) -> Path:
    helper = tmp_path / "overlay-helper"
    helper.write_text(f"#!{sys.executable}\n{body}")
    helper.chmod(helper.stat().st_mode | stat.S_IXUSR)
    return helper


def _python_helper_transport(helper: Path) -> _SubprocessOverlayTransport:
    return _SubprocessOverlayTransport(helper, interpreter=sys.executable)


def test_overlay_command_contains_only_geometry_and_state(
    snapshot: ProtectionSnapshot, fake_transport: FakeTransport
) -> None:
    client = PrivacyOverlayClient(transport_factory=lambda: fake_transport)

    assert client.render(snapshot, timeout=0.1) is True
    payload = json.loads(fake_transport.writes[-1])
    assert payload == {
        "generation": snapshot.generation,
        "state": "protected",
        "style": "pill",
        "placement": "bottom-left-flush",
        "displays": [
            {
                "id": 2,
                "left": 100,
                "top": 0,
                "width": 100,
                "height": 100,
                "reasons": [],
            }
        ],
        "all_displays": False,
        "reason_display": "hybrid",
        "reason_detail": "exact",
        "reason_trigger": "hover",
        "reasons": [],
    }
    serialized = fake_transport.writes[-1]
    assert "InPrivate" not in serialized
    assert "Microsoft Edge" not in serialized


def test_render_command_includes_indicator_placement(
    snapshot: ProtectionSnapshot,
) -> None:
    command = PrivacyOverlayClient._render_command(
        replace(snapshot, indicator_placement="bottom-left-inset")
    )
    assert command["placement"] == "bottom-left-inset"


def test_overlay_exact_reason_is_sent_only_for_protected_display() -> None:
    snapshot = _protected_snapshot(
        reason_display="hybrid",
        reason_detail="exact",
        reason_trigger="hover",
        reasons=(_private_title_reason(display_id=2),),
    )

    command = PrivacyOverlayClient._render_command(snapshot)
    by_id = {row["id"]: row for row in command["displays"]}

    assert by_id[2]["reasons"][0] == {
        "code": "window_title_rule",
        "display_id": 2,
        "app_name": "Edge",
        "bundle_id": "com.microsoft.edgemac",
        "window_title": "InPrivate",
        "rule": "InPrivate",
    }
    assert 1 not in by_id
    assert command["reason_display"] == "hybrid"
    assert command["reason_detail"] == "exact"
    assert command["reason_trigger"] == "hover"


def test_transient_command_suppresses_only_overlay_reason_payloads() -> None:
    reason = _private_title_reason(2)
    snapshot = _protected_snapshot(reasons=(reason,))

    command = PrivacyOverlayClient._render_command(
        replace(snapshot, indicator_style="quiet-shield"),
        overlay_reasons_enabled=False,
    )

    assert command["style"] == "quiet-shield"
    assert command["reason_display"] == snapshot.reason_display
    assert command["reason_detail"] == snapshot.reason_detail
    assert command["reason_trigger"] == snapshot.reason_trigger
    assert command["displays"][0]["reasons"] == []
    assert command["reasons"] == []
    assert snapshot.display_reasons.reasons == (reason,)


def test_sustained_quiet_shield_restores_configured_reason_payloads() -> None:
    reason = _private_title_reason(2)
    snapshot = replace(
        _protected_snapshot(reasons=(reason,)),
        indicator_style="quiet-shield",
    )
    command = PrivacyOverlayClient._render_command(
        snapshot,
        overlay_reasons_enabled=True,
    )
    assert command["displays"][0]["reasons"][0]["code"] == "window_title_rule"


def test_diagnostics_only_overlay_payload_contains_no_reason_values() -> None:
    snapshot = _protected_snapshot(
        reason_display="diagnostics",
        reason_detail="exact",
        reasons=(_private_title_reason(display_id=2),),
    )

    raw = json.dumps(
        PrivacyOverlayClient._render_command(snapshot), separators=(",", ":")
    )

    assert '"reasons":[]' in raw
    assert "InPrivate" not in raw
    assert "com.microsoft.edgemac" not in raw


@pytest.mark.parametrize("detail", ["category", "tiered"])
def test_overlay_category_and_tiered_send_only_fixed_reason_codes(detail: str) -> None:
    snapshot = _protected_snapshot(
        reason_detail=detail,
        reasons=(_private_title_reason(display_id=2),),
    )

    command = PrivacyOverlayClient._render_command(snapshot)

    assert command["displays"][0]["reasons"] == [
        {"code": "window_title_rule", "display_id": 2}
    ]
    assert "InPrivate" not in json.dumps(command)


def test_overlay_reason_payload_is_priority_ordered_and_bounded_to_eight() -> None:
    direct = tuple(
        ProtectionReason(
            code=ProtectionReasonCode.APP_RULE,
            display_id=2,
            app_name=f"App {index}",
        )
        for index in range(9)
    )
    failed = ProtectionReason(
        code=ProtectionReasonCode.HELPER_EXIT,
        display_id=2,
    )
    snapshot = _protected_snapshot(reason_detail="category", reasons=direct + (failed,))

    payloads = privacy_overlay._reason_payloads_for_display(snapshot, 2)

    assert len(payloads) == 8
    assert payloads[0] == {"code": "helper_exit", "display_id": 2}


def test_overlay_old_snapshot_shape_renders_with_reason_defaults(
    snapshot: ProtectionSnapshot,
) -> None:
    legacy = SimpleNamespace(
        **{
            name: value
            for name, value in vars(snapshot).items()
            if name
            not in {
                "reason_display",
                "reason_detail",
                "reason_trigger",
                "display_reasons",
                "indicator_placement",
            }
        }
    )

    command = PrivacyOverlayClient._render_command(legacy)

    assert command["reason_trigger"] == "hover"
    assert command["placement"] == "bottom-left-flush"
    assert command["reasons"] == []
    assert command["displays"][0]["reasons"] == []


def test_clear_explicitly_removes_reason_content(fake_transport: FakeTransport) -> None:
    client = PrivacyOverlayClient(transport_factory=lambda: fake_transport)

    assert client.clear(99) is True

    command = json.loads(fake_transport.writes[-1])
    assert command["reason_display"] == "hybrid"
    assert command["reason_detail"] == "category"
    assert command["reason_trigger"] == "hover"
    assert command["reasons"] == []


def test_global_reason_is_available_to_all_displays_fallback() -> None:
    paused = ProtectionSnapshot(
        generation=43,
        state=ProtectionState.PAUSED,
        capture_mode="separate",
        indicator_style="pill",
        displays=(),
        protected_display_ids=frozenset(),
        active_display_id=None,
        created_monotonic=1.0,
        fresh_until=1.25,
        display_reasons=DisplayProtectionReasons.from_reasons(
            [ProtectionReason(ProtectionReasonCode.MANUAL_PAUSE, display_id=None)]
        ),
    )

    command = PrivacyOverlayClient._render_command(paused)

    assert command["all_displays"] is True
    assert command["reasons"] == [{"code": "manual_pause", "display_id": None}]


def test_paused_and_failed_cover_all_known_displays(
    snapshot: ProtectionSnapshot, fake_transport: FakeTransport
) -> None:
    displays = (
        DisplayInfo(1, ScreenRegion(0, 0, 100, 100), True),
        DisplayInfo(2, ScreenRegion(100, 0, 100, 100), False),
    )
    fake_transport.responses = [True, True]
    client = PrivacyOverlayClient(transport_factory=lambda: fake_transport)
    for state in (ProtectionState.PAUSED, ProtectionState.FAILED):
        current = ProtectionSnapshot(
            generation=snapshot.generation + len(fake_transport.writes),
            state=state,
            capture_mode="separate",
            indicator_style="banner",
            displays=displays,
            protected_display_ids=frozenset(),
            active_display_id=None,
            created_monotonic=1.0,
            fresh_until=1.25,
        )
        assert client.render(current) is True
        assert {display["id"] for display in json.loads(fake_transport.writes[-1])["displays"]} == {1, 2}


def test_empty_display_inventory_uses_helper_all_displays_fallback(
    snapshot: ProtectionSnapshot, fake_transport: FakeTransport
) -> None:
    failed = ProtectionSnapshot(
        generation=snapshot.generation,
        state=ProtectionState.FAILED,
        capture_mode="separate",
        indicator_style="banner",
        displays=(),
        protected_display_ids=frozenset(),
        active_display_id=None,
        created_monotonic=1.0,
        fresh_until=1.25,
    )
    assert PrivacyOverlayClient(transport_factory=lambda: fake_transport).render(failed) is True
    assert json.loads(fake_transport.writes[-1])["all_displays"] is True


def test_wrong_generation_or_timeout_is_not_confirmed(
    snapshot: ProtectionSnapshot, fake_transport: FakeTransport
) -> None:
    fake_transport.responses = [False]
    client = PrivacyOverlayClient(transport_factory=lambda: fake_transport)

    assert client.render(snapshot, timeout=0.01) is False
    assert fake_transport.closed is True


def test_exact_acknowledgement_confirms_sorted_window_ids_for_only_that_generation(
    snapshot: ProtectionSnapshot, tmp_path: Path
) -> None:
    helper = _helper_script(
        tmp_path,
        "import json, sys\n"
        "for line in sys.stdin:\n"
        "    command = json.loads(line)\n"
        "    print(json.dumps({\n"
        "        'generation': command['generation'],\n"
        "        'rendered': True,\n"
        "        'error': None,\n"
        "        'window_ids': [4294967295, 7],\n"
        "    }), flush=True)",
    )
    client = PrivacyOverlayClient(transport_factory=lambda: _python_helper_transport(helper))

    assert client.render(snapshot, timeout=0.2, overlay_reasons_enabled=False) is True
    assert client.confirmed_window_ids(snapshot.generation) == (7, 4294967295)
    assert client.confirmed_window_ids(snapshot.generation + 1) == ()


def test_transient_render_keeps_acknowledgement_and_window_ids(
    fake_transport: FakeTransport,
) -> None:
    fake_transport.responses = [True]
    client = PrivacyOverlayClient(transport_factory=lambda: fake_transport)
    snapshot = _protected_snapshot(reasons=(_private_title_reason(2),))

    assert client.render(snapshot, overlay_reasons_enabled=False) is True

    command = json.loads(fake_transport.writes[-1])
    assert command["displays"][0]["reasons"] == []
    assert command["reasons"] == []
    assert client.confirmed_window_ids(snapshot.generation) == ()


@pytest.mark.parametrize(
    "acknowledgement",
    [
        {"generation": 12, "rendered": True, "error": None},
        {"generation": 12, "rendered": True, "error": None, "window_ids": [0]},
        {"generation": 12, "rendered": True, "error": None, "window_ids": [7, 7]},
        {"generation": 12, "rendered": True, "error": None, "window_ids": [True]},
        {
            "generation": 12,
            "rendered": True,
            "error": None,
            "window_ids": [4294967296],
        },
        {"generation": 12, "rendered": True, "error": None, "window_ids": "7"},
        {"generation": 12, "rendered": True, "window_ids": [7]},
        {
            "generation": 12,
            "rendered": True,
            "error": "private-helper-detail",
            "window_ids": [7],
        },
    ],
    ids=[
        "missing-window-ids",
        "zero",
        "duplicate",
        "bool",
        "overflow",
        "not-array",
        "missing-error",
        "success-with-error",
    ],
)
def test_malformed_window_id_acknowledgements_fail_closed(
    snapshot: ProtectionSnapshot,
    tmp_path: Path,
    acknowledgement: dict[str, object],
) -> None:
    helper = _helper_script(
        tmp_path,
        "import json, sys\n"
        f"acknowledgement = {acknowledgement!r}\n"
        "for _line in sys.stdin:\n"
        "    print(json.dumps(acknowledgement), flush=True)",
    )
    client = PrivacyOverlayClient(transport_factory=lambda: _python_helper_transport(helper))

    assert client.render(snapshot, timeout=0.2) is False
    assert client.confirmed_window_ids(snapshot.generation) == ()


def test_clear_requires_an_empty_acknowledged_window_id_set(
    snapshot: ProtectionSnapshot, tmp_path: Path
) -> None:
    helper = _helper_script(
        tmp_path,
        "import json, sys\n"
        "for line in sys.stdin:\n"
        "    command = json.loads(line)\n"
        "    print(json.dumps({\n"
        "        'generation': command['generation'],\n"
        "        'rendered': True,\n"
        "        'error': None,\n"
        "        'window_ids': [7],\n"
        "    }), flush=True)",
    )
    client = PrivacyOverlayClient(transport_factory=lambda: _python_helper_transport(helper))

    assert client.clear(snapshot.generation, timeout=0.2) is False
    assert client.confirmed_window_ids(snapshot.generation) == ()


def test_render_failure_acknowledgement_is_unconfirmed_with_no_window_ids(
    snapshot: ProtectionSnapshot, tmp_path: Path
) -> None:
    helper = _helper_script(
        tmp_path,
        "import json, sys\n"
        "for line in sys.stdin:\n"
        "    command = json.loads(line)\n"
        "    print(json.dumps({\n"
        "        'generation': command['generation'],\n"
        "        'rendered': False,\n"
        "        'error': 'unresolved-window-id',\n"
        "        'window_ids': [],\n"
        "    }), flush=True)",
    )
    client = PrivacyOverlayClient(transport_factory=lambda: _python_helper_transport(helper))

    assert client.render(snapshot, timeout=0.2) is False
    assert client.confirmed_window_ids(snapshot.generation) == ()


def test_off_style_does_not_start_a_transport(snapshot: ProtectionSnapshot) -> None:
    starts = 0

    def factory() -> FakeTransport:
        nonlocal starts
        starts += 1
        return FakeTransport(True)

    off = ProtectionSnapshot(
        generation=snapshot.generation,
        state=snapshot.state,
        capture_mode=snapshot.capture_mode,
        indicator_style="off",
        displays=snapshot.displays,
        protected_display_ids=snapshot.protected_display_ids,
        active_display_id=snapshot.active_display_id,
        created_monotonic=snapshot.created_monotonic,
        fresh_until=snapshot.fresh_until,
    )

    client = PrivacyOverlayClient(transport_factory=factory)
    assert client.render(off) is True
    assert client.confirmed_window_ids(off.generation) == ()
    assert starts == 0


def test_off_style_discards_active_transport_without_starting_replacement(
    snapshot: ProtectionSnapshot,
) -> None:
    visible_transport = FakeTransport(True, window_ids=(7, 41))
    replacement_transport = FakeTransport(True)
    transports = (visible_transport, replacement_transport)
    starts = 0

    def factory() -> FakeTransport:
        nonlocal starts
        transport = transports[starts]
        starts += 1
        return transport

    client = PrivacyOverlayClient(transport_factory=factory)
    assert client.render(snapshot) is True
    assert starts == 1
    assert client.confirmed_window_ids(snapshot.generation) == (7, 41)

    off = replace(
        snapshot,
        generation=snapshot.generation + 1,
        indicator_style="off",
    )
    assert client.render(off) is True

    assert visible_transport.closed is True
    assert len(visible_transport.writes) == 1
    assert starts == 1
    assert replacement_transport.closed is False
    assert client._transport is None
    assert client._confirmed_generation == off.generation
    assert client.confirmed_window_ids(snapshot.generation) == ()
    assert client.confirmed_window_ids(off.generation) == ()


def test_closed_client_never_restarts_a_transport(snapshot: ProtectionSnapshot) -> None:
    starts = 0

    def factory() -> FakeTransport:
        nonlocal starts
        starts += 1
        return FakeTransport(True)

    client = PrivacyOverlayClient(transport_factory=factory)
    client.close()

    assert client.render(snapshot) is False
    assert client.clear(snapshot.generation + 1) is False
    assert starts == 0


def test_terminal_mark_prevents_a_later_transport_start(snapshot: ProtectionSnapshot) -> None:
    starts = 0

    def factory() -> FakeTransport:
        nonlocal starts
        starts += 1
        return FakeTransport(True)

    client = PrivacyOverlayClient(transport_factory=factory)
    client.mark_terminal()

    assert client.render(snapshot) is False
    assert client.clear(snapshot.generation + 1) is False
    assert starts == 0


def test_terminal_mark_is_nonblocking_and_rejects_late_acknowledgement(snapshot) -> None:
    transport = BlockingTransport(True, window_ids=(7, 41))
    client = PrivacyOverlayClient(transport_factory=lambda: transport)
    render_result: list[bool] = []
    mark_finished = threading.Event()

    render_thread = threading.Thread(
        target=lambda: render_result.append(client.render(snapshot, timeout=1.0))
    )
    render_thread.start()
    assert transport.started.wait(timeout=0.5)
    mark_thread = threading.Thread(target=lambda: (client.mark_terminal(), mark_finished.set()))
    mark_thread.start()
    try:
        assert mark_finished.wait(timeout=0.1)
        assert client.confirmed_window_ids(snapshot.generation) == ()
    finally:
        transport.release.set()
        render_thread.join(timeout=1.0)
        mark_thread.join(timeout=1.0)

    assert render_result == [False]
    assert mark_finished.is_set()
    assert client.confirmed_window_ids(snapshot.generation) == ()
    assert client.render(snapshot) is False
    assert client.clear(snapshot.generation + 1) is False


def test_restart_backoff_escalates_to_cap_and_resets_after_recovery(snapshot, monkeypatch) -> None:
    transports = [FailingTransport() for _ in range(7)] + [FakeTransport(True)]
    starts = 0
    now = 0.0

    def factory() -> FakeTransport:
        nonlocal starts
        transport = transports[starts]
        starts += 1
        return transport

    monkeypatch.setattr("openchronicle.capture.privacy_overlay.time.monotonic", lambda: now)
    client = PrivacyOverlayClient(transport_factory=factory)

    for delay in (1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0):
        assert client.render(snapshot) is False
        assert client.render(snapshot) is False
        now += delay

    assert starts == 7
    assert client.render(snapshot) is True
    assert starts == 8
    assert client._restart_delay == 1.0
    assert client._next_restart_at == 0.0

    assert client.render(snapshot) is False
    assert client._next_restart_at == now + 1.0


def test_malformed_then_valid_acknowledgements_fail_closed(snapshot, tmp_path: Path) -> None:
    helper = _helper_script(
        tmp_path,
        "import json, sys\n"
        "for line in sys.stdin:\n"
        "    command = json.loads(line)\n"
        "    print('{bad-json', flush=True)\n"
        "    print(json.dumps({'generation': command['generation'], 'rendered': True, "
        "'error': None, 'window_ids': []}), flush=True)",
    )
    client = PrivacyOverlayClient(transport_factory=lambda: _python_helper_transport(helper))

    assert client.render(snapshot, timeout=0.2) is False


def test_wrong_generation_then_valid_acknowledgements_fail_closed(snapshot, tmp_path: Path) -> None:
    helper = _helper_script(
        tmp_path,
        "import json, sys\n"
        "for line in sys.stdin:\n"
        "    command = json.loads(line)\n"
        "    print(json.dumps({'generation': command['generation'] + 1, 'rendered': True, "
        "'error': None, 'window_ids': []}), flush=True)\n"
        "    print(json.dumps({'generation': command['generation'], 'rendered': True, "
        "'error': None, 'window_ids': []}), flush=True)",
    )
    client = PrivacyOverlayClient(transport_factory=lambda: _python_helper_transport(helper))

    assert client.render(snapshot, timeout=0.2) is False


def test_repeated_generation_acknowledgement_fails_closed(snapshot, tmp_path: Path) -> None:
    helper = _helper_script(
        tmp_path,
        "import json, sys\n"
        "for line in sys.stdin:\n"
        "    command = json.loads(line)\n"
        "    print(json.dumps({'generation': command['generation'], 'rendered': True, "
        "'error': None, 'window_ids': []}), flush=True)",
    )
    transport = _python_helper_transport(helper)
    client = PrivacyOverlayClient(transport_factory=lambda: transport)

    assert client.render(snapshot, timeout=0.2) is True, (
        transport._reader_finished,
        transport._protocol_failed,
        transport._pending_generation,
        transport._pending_result,
    )
    assert client.render(snapshot, timeout=0.2) is False


def test_duplicate_prior_acknowledgement_fails_next_command_closed(snapshot, tmp_path: Path) -> None:
    helper = _helper_script(
        tmp_path,
        "import json, sys\n"
        "first_generation = None\n"
        "for line in sys.stdin:\n"
        "    command = json.loads(line)\n"
        "    if first_generation is None:\n"
        "        first_generation = command['generation']\n"
        "    else:\n"
        "        print(json.dumps({'generation': first_generation, 'rendered': True, "
        "'error': None, 'window_ids': []}), flush=True)\n"
        "    print(json.dumps({'generation': command['generation'], 'rendered': True, "
        "'error': None, 'window_ids': []}), flush=True)",
    )
    client = PrivacyOverlayClient(transport_factory=lambda: _python_helper_transport(helper))

    assert client.render(snapshot, timeout=0.2) is True
    assert client.render(replace(snapshot, generation=snapshot.generation + 1), timeout=0.2) is False


def test_preexisting_acknowledgement_fails_closed(snapshot, tmp_path: Path) -> None:
    helper = _helper_script(
        tmp_path,
        "import time\n"
        "print('{\"generation\":12,\"rendered\":true,\"error\":null,"
        "\"window_ids\":[]}', flush=True)\n"
        "time.sleep(30)",
    )
    transport = _python_helper_transport(helper)
    with transport._condition:
        assert transport._condition.wait_for(lambda: transport._protocol_failed, timeout=0.5)
    client = PrivacyOverlayClient(transport_factory=lambda: transport)

    assert client.render(snapshot, timeout=0.2) is False


def test_malformed_acknowledgement_is_not_confirmed(
    snapshot: ProtectionSnapshot, tmp_path: Path
) -> None:
    helper = _helper_script(
        tmp_path,
        "import time\n"
        "print('{\"generation\":12,\"rendered\":\"true\",\"error\":null,"
        "\"window_ids\":[]}', flush=True)\n"
        "time.sleep(30)",
    )
    client = PrivacyOverlayClient(
        transport_factory=lambda: _python_helper_transport(helper)
    )

    assert client.render(snapshot, timeout=0.2) is False


def test_exited_child_is_not_confirmed(snapshot: ProtectionSnapshot, tmp_path: Path) -> None:
    helper = _helper_script(tmp_path, "")
    client = PrivacyOverlayClient(
        transport_factory=lambda: _python_helper_transport(helper)
    )

    assert client.render(snapshot, timeout=0.2) is False


def test_subprocess_transport_can_launch_helper_through_existing_interpreter(
    snapshot: ProtectionSnapshot, tmp_path: Path
) -> None:
    helper = _helper_script(
        tmp_path,
        "import json, sys\n"
        "for line in sys.stdin:\n"
        "    command = json.loads(line)\n"
        "    print(json.dumps({'generation': command['generation'], 'rendered': True, "
        "'error': None, 'window_ids': []}), flush=True)",
    )
    helper.chmod(stat.S_IRUSR | stat.S_IWUSR)
    transport = _SubprocessOverlayTransport(helper, interpreter=sys.executable)
    client = PrivacyOverlayClient(transport_factory=lambda: transport)

    try:
        assert client.render(snapshot, timeout=0.2) is True
    finally:
        client.close()


def test_subprocess_acknowledgement_state_stays_constant_over_long_run(tmp_path: Path) -> None:
    helper = _helper_script(
        tmp_path,
        "import json, sys\n"
        "for line in sys.stdin:\n"
        "    command = json.loads(line)\n"
        "    print(json.dumps({'generation': command['generation'], 'rendered': True, "
        "'error': None, 'window_ids': []}), flush=True)",
    )
    transport = _python_helper_transport(helper)

    try:
        for generation in range(1, 257):
            assert transport.send_and_wait(
                json.dumps({"generation": generation}),
                generation,
                timeout=0.2,
            )

        assert transport._last_completed_generation == 256
        assert not any(isinstance(value, set) for value in vars(transport).values())
        assert transport.send_and_wait('{"generation":1}', 1, timeout=0.01) is None
    finally:
        transport.close()


def test_close_stops_subprocess_reader_thread(snapshot: ProtectionSnapshot, tmp_path: Path) -> None:
    helper = _helper_script(
        tmp_path,
        "import json, sys\n"
        "for line in sys.stdin:\n"
        "    command = json.loads(line)\n"
        "    print(json.dumps({'generation': command['generation'], 'rendered': True, "
        "'error': None, 'window_ids': []}), flush=True)",
    )
    transport = _python_helper_transport(helper)
    client = PrivacyOverlayClient(transport_factory=lambda: transport)
    reader = transport._reader_thread
    process = transport._process

    assert client.render(snapshot) is True
    client.close()

    assert reader is not None
    assert process is not None
    assert not reader.is_alive()
    assert process.poll() is not None
    assert transport._reader_thread is None
    assert transport._process is None


def test_concurrent_renders_share_one_transport_and_serialize_commands(snapshot) -> None:
    transport = BlockingTransport(True, True)
    starts = 0

    def factory() -> BlockingTransport:
        nonlocal starts
        starts += 1
        return transport

    client = PrivacyOverlayClient(transport_factory=factory)
    send_lock = AttemptObservingLock()
    client._send_lock = send_lock
    results: list[bool] = []
    first = threading.Thread(target=lambda: results.append(client.render(snapshot, timeout=1.0)))
    second_snapshot = replace(snapshot, generation=snapshot.generation + 1)
    second = threading.Thread(target=lambda: results.append(client.render(second_snapshot, timeout=1.0)))

    first.start()
    assert transport.started.wait(timeout=0.5)
    second.start()
    try:
        assert send_lock.second_attempted.wait(timeout=0.5)
        assert send_lock.locked()
        assert len(transport.writes) == 1
    finally:
        transport.release.set()
        first.join(timeout=1.0)
        second.join(timeout=1.0)

    assert not first.is_alive()
    assert not second.is_alive()
    assert results.count(True) == 2
    assert starts == 1
    assert len(transport.writes) == 2


def test_close_waits_for_active_render_before_closing_transport(snapshot) -> None:
    transport = BlockingTransport(True)
    client = PrivacyOverlayClient(transport_factory=lambda: transport)
    send_lock = AttemptObservingLock()
    client._send_lock = send_lock
    render_result: list[bool] = []
    render_thread = threading.Thread(
        target=lambda: render_result.append(client.render(snapshot, timeout=1.0))
    )
    close_thread = threading.Thread(target=client.close)

    render_thread.start()
    assert transport.started.wait(timeout=0.5)
    close_thread.start()
    try:
        assert send_lock.second_attempted.wait(timeout=0.5)
        assert send_lock.locked()
        assert not transport.closed
    finally:
        transport.release.set()
        render_thread.join(timeout=1.0)
        close_thread.join(timeout=1.0)

    assert render_result == [False]
    assert transport.closed is True
    assert transport.closed_before_release is False


def test_resolver_accepts_executable_environment_override(monkeypatch, tmp_path: Path) -> None:
    for name in (
        "mac-privacy-overlay-reason.swift",
        "mac-privacy-overlay-core.swift",
        "mac-privacy-overlay.swift",
    ):
        (tmp_path / name).write_text(name)
    helper = _helper_script(tmp_path, "")
    monkeypatch.setattr("openchronicle.capture.privacy_overlay.platform.system", lambda: "Darwin")
    monkeypatch.setenv("OPENCHRONICLE_PRIVACY_OVERLAY_HELPER", str(helper))

    assert _resolve_overlay_path() == helper.resolve()


def test_default_resolver_returns_fresh_app_bundle_executable(
    monkeypatch, tmp_path: Path
) -> None:
    source_dir = tmp_path / "_bundled"
    source_dir.mkdir()
    runtime_dir = tmp_path / "root" / "runtime"
    executable = (
        runtime_dir
        / "helpers"
        / "OpenChroniclePrivacyOverlay.app"
        / "Contents"
        / "MacOS"
        / "mac-privacy-overlay"
    )
    executable.parent.mkdir(parents=True)
    for name in (
        "mac-privacy-overlay-reason.swift",
        "mac-privacy-overlay-core.swift",
        "mac-privacy-overlay.swift",
        "mac-privacy-overlay-Info.plist",
        "build-mac-privacy-overlay.sh",
    ):
        (source_dir / name).write_text(name)
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o755)
    now = time.time()
    for source in source_dir.iterdir():
        if source.is_file():
            os.utime(source, (now - 10, now - 10))
    os.utime(executable, (now, now))

    monkeypatch.setattr(privacy_overlay.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(privacy_overlay.paths, "runtime_dir", lambda: runtime_dir)
    monkeypatch.delenv("OPENCHRONICLE_PRIVACY_OVERLAY_HELPER", raising=False)
    monkeypatch.setattr(
        privacy_overlay,
        "_overlay_source_directories",
        lambda: (source_dir,),
    )

    assert _resolve_overlay_path() == executable


def test_resolver_rejects_source_free_executable_environment_override(
    monkeypatch, tmp_path: Path
) -> None:
    helper = _helper_script(tmp_path, "")

    def missing_package_files(_: str):
        raise ModuleNotFoundError

    monkeypatch.setattr("openchronicle.capture.privacy_overlay.platform.system", lambda: "Darwin")
    monkeypatch.setenv("OPENCHRONICLE_PRIVACY_OVERLAY_HELPER", str(helper))
    monkeypatch.setattr(importlib.resources, "files", missing_package_files)
    monkeypatch.setattr(
        privacy_overlay,
        "__file__",
        str(tmp_path / "src/openchronicle/capture/privacy_overlay.py"),
    )

    assert _resolve_overlay_path() is None


def test_resolver_rejects_stale_binary_when_recompile_fails(monkeypatch, tmp_path: Path) -> None:
    source_root = tmp_path / "resources"
    source_root.mkdir()
    reason = source_root / "mac-privacy-overlay-reason.swift"
    core = source_root / "mac-privacy-overlay-core.swift"
    main = source_root / "mac-privacy-overlay.swift"
    binary = source_root / "mac-privacy-overlay"
    reason.write_text("reason")
    core.write_text("core")
    main.write_text("main")
    binary.write_text("old binary")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    os.utime(binary, (1, 1))
    os.utime(reason, (2, 2))
    os.utime(core, (2, 2))
    os.utime(main, (2, 2))

    def missing_package_files(_: str):
        raise ModuleNotFoundError

    monkeypatch.setattr(importlib.resources, "files", missing_package_files)
    monkeypatch.setattr(privacy_overlay, "__file__", str(tmp_path / "src/openchronicle/capture/privacy_overlay.py"))
    monkeypatch.setattr(
        privacy_overlay.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1),
    )

    assert _resolve_overlay_path() is None


@pytest.mark.parametrize(
    "missing_name",
    [
        "mac-privacy-overlay-reason.swift",
        "mac-privacy-overlay-core.swift",
        "mac-privacy-overlay.swift",
    ],
)
def test_compiler_rejects_an_old_binary_when_any_source_is_missing(
    missing_name: str, tmp_path: Path
) -> None:
    reason = tmp_path / "mac-privacy-overlay-reason.swift"
    core = tmp_path / "mac-privacy-overlay-core.swift"
    main = tmp_path / "mac-privacy-overlay.swift"
    binary = tmp_path / "mac-privacy-overlay"
    for source in (reason, core, main):
        if source.name != missing_name:
            source.write_text(source.stem)
    binary.write_text("old binary")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)

    assert _maybe_compile_overlay(reason, core, main, binary) is None


def test_compiler_cache_metadata_failure_returns_none(monkeypatch, tmp_path: Path) -> None:
    reason = tmp_path / "mac-privacy-overlay-reason.swift"
    core = tmp_path / "mac-privacy-overlay-core.swift"
    main = tmp_path / "mac-privacy-overlay.swift"
    reason.write_text("reason")
    core.write_text("core")
    main.write_text("main")

    def fail_mkdir(*_args, **_kwargs) -> None:
        raise OSError("denied")

    monkeypatch.setattr(privacy_overlay.Path, "mkdir", fail_mkdir)

    assert _maybe_compile_overlay(reason, core, main, tmp_path / "mac-privacy-overlay") is None


def test_resolver_handles_override_path_metadata_failure(monkeypatch) -> None:
    monkeypatch.setattr("openchronicle.capture.privacy_overlay.platform.system", lambda: "Darwin")
    monkeypatch.setenv("OPENCHRONICLE_PRIVACY_OVERLAY_HELPER", "/unavailable/helper")

    def fail_resolve(*_args, **_kwargs) -> Path:
        raise OSError("metadata unavailable")

    monkeypatch.setattr(privacy_overlay.Path, "resolve", fail_resolve)

    assert _resolve_overlay_path() is None
