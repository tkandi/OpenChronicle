import importlib.resources
import json
import os
import stat
import sys
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from openchronicle.capture import privacy_overlay
from openchronicle.capture.privacy import DisplayInfo, ScreenRegion
from openchronicle.capture.privacy_overlay import (
    PrivacyOverlayClient,
    _maybe_compile_overlay,
    _resolve_overlay_path,
    _SubprocessOverlayTransport,
)
from openchronicle.capture.protection import ProtectionSnapshot, ProtectionState


class FakeTransport:
    def __init__(self, *responses: bool) -> None:
        self.writes: list[str] = []
        self.responses = list(responses)
        self.closed = False

    def send_and_wait(self, line: str, generation: int, timeout: float) -> bool:
        self.writes.append(line)
        return self.responses.pop(0) if self.responses else False

    def close(self) -> None:
        self.closed = True


class FailingTransport(FakeTransport):
    def send_and_wait(self, line: str, generation: int, timeout: float) -> bool:
        raise BrokenPipeError


class BlockingTransport(FakeTransport):
    def __init__(self, *responses: bool) -> None:
        super().__init__(*responses)
        self.started = threading.Event()
        self.release = threading.Event()
        self.closed_before_release = False

    def send_and_wait(self, line: str, generation: int, timeout: float) -> bool:
        self.writes.append(line)
        self.started.set()
        assert self.release.wait(timeout=1.0)
        return self.responses.pop(0) if self.responses else False

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


@pytest.fixture
def fake_transport() -> FakeTransport:
    return FakeTransport(True)


def _helper_script(tmp_path: Path, body: str) -> Path:
    helper = tmp_path / "overlay-helper"
    helper.write_text(f"#!{sys.executable}\n{body}")
    helper.chmod(helper.stat().st_mode | stat.S_IXUSR)
    return helper


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
        "displays": [
            {"id": 2, "left": 100, "top": 0, "width": 100, "height": 100}
        ],
        "all_displays": False,
    }
    serialized = fake_transport.writes[-1]
    assert "InPrivate" not in serialized
    assert "Microsoft Edge" not in serialized


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

    assert PrivacyOverlayClient(transport_factory=factory).render(off) is True
    assert starts == 0


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
        "    print(json.dumps({'generation': command['generation'], 'rendered': True}), flush=True)",
    )
    client = PrivacyOverlayClient(transport_factory=lambda: _SubprocessOverlayTransport(helper))

    assert client.render(snapshot, timeout=0.2) is False


def test_wrong_generation_then_valid_acknowledgements_fail_closed(snapshot, tmp_path: Path) -> None:
    helper = _helper_script(
        tmp_path,
        "import json, sys\n"
        "for line in sys.stdin:\n"
        "    command = json.loads(line)\n"
        "    print(json.dumps({'generation': command['generation'] + 1, 'rendered': True}), flush=True)\n"
        "    print(json.dumps({'generation': command['generation'], 'rendered': True}), flush=True)",
    )
    client = PrivacyOverlayClient(transport_factory=lambda: _SubprocessOverlayTransport(helper))

    assert client.render(snapshot, timeout=0.2) is False


def test_repeated_generation_acknowledgement_fails_closed(snapshot, tmp_path: Path) -> None:
    helper = _helper_script(
        tmp_path,
        "import json, sys\n"
        "for line in sys.stdin:\n"
        "    command = json.loads(line)\n"
        "    print(json.dumps({'generation': command['generation'], 'rendered': True}), flush=True)",
    )
    transport = _SubprocessOverlayTransport(helper)
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
        "        print(json.dumps({'generation': first_generation, 'rendered': True}), flush=True)\n"
        "    print(json.dumps({'generation': command['generation'], 'rendered': True}), flush=True)",
    )
    client = PrivacyOverlayClient(transport_factory=lambda: _SubprocessOverlayTransport(helper))

    assert client.render(snapshot, timeout=0.2) is True
    assert client.render(replace(snapshot, generation=snapshot.generation + 1), timeout=0.2) is False


def test_preexisting_acknowledgement_fails_closed(snapshot, tmp_path: Path) -> None:
    helper = _helper_script(
        tmp_path,
        "import time\n"
        "print('{\"generation\": 12, \"rendered\": true}', flush=True)\n"
        "time.sleep(30)",
    )
    transport = _SubprocessOverlayTransport(helper)
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
        "print('{\"generation\": 12, \"rendered\": \"true\"}', flush=True)\n"
        "time.sleep(30)",
    )
    client = PrivacyOverlayClient(
        transport_factory=lambda: _SubprocessOverlayTransport(helper)
    )

    assert client.render(snapshot, timeout=0.2) is False


def test_exited_child_is_not_confirmed(snapshot: ProtectionSnapshot, tmp_path: Path) -> None:
    helper = _helper_script(tmp_path, "")
    client = PrivacyOverlayClient(
        transport_factory=lambda: _SubprocessOverlayTransport(helper)
    )

    assert client.render(snapshot, timeout=0.2) is False


def test_close_stops_subprocess_reader_thread(snapshot: ProtectionSnapshot, tmp_path: Path) -> None:
    helper = _helper_script(
        tmp_path,
        "import json, sys\n"
        "for line in sys.stdin:\n"
        "    command = json.loads(line)\n"
        "    print(json.dumps({'generation': command['generation'], 'rendered': True}), flush=True)",
    )
    transport = _SubprocessOverlayTransport(helper)
    client = PrivacyOverlayClient(transport_factory=lambda: transport)

    assert client.render(snapshot) is True
    client.close()

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

    assert render_result == [True]
    assert transport.closed is True
    assert transport.closed_before_release is False


def test_resolver_accepts_executable_environment_override(monkeypatch, tmp_path: Path) -> None:
    helper = _helper_script(tmp_path, "")
    monkeypatch.setattr("openchronicle.capture.privacy_overlay.platform.system", lambda: "Darwin")
    monkeypatch.setenv("OPENCHRONICLE_PRIVACY_OVERLAY_HELPER", str(helper))

    assert _resolve_overlay_path() == helper.resolve()


def test_resolver_rejects_stale_binary_when_recompile_fails(monkeypatch, tmp_path: Path) -> None:
    source_root = tmp_path / "resources"
    source_root.mkdir()
    core = source_root / "mac-privacy-overlay-core.swift"
    main = source_root / "mac-privacy-overlay.swift"
    binary = source_root / "mac-privacy-overlay"
    core.write_text("core")
    main.write_text("main")
    binary.write_text("old binary")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    os.utime(binary, (1, 1))
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


def test_compiler_cache_metadata_failure_returns_none(monkeypatch, tmp_path: Path) -> None:
    core = tmp_path / "mac-privacy-overlay-core.swift"
    main = tmp_path / "mac-privacy-overlay.swift"
    core.write_text("core")
    main.write_text("main")

    def fail_mkdir(*_args, **_kwargs) -> None:
        raise OSError("denied")

    monkeypatch.setattr(privacy_overlay.Path, "mkdir", fail_mkdir)

    assert _maybe_compile_overlay(core, main, tmp_path / "mac-privacy-overlay") is None


def test_resolver_handles_override_path_metadata_failure(monkeypatch) -> None:
    monkeypatch.setattr("openchronicle.capture.privacy_overlay.platform.system", lambda: "Darwin")
    monkeypatch.setenv("OPENCHRONICLE_PRIVACY_OVERLAY_HELPER", "/unavailable/helper")

    def fail_resolve(*_args, **_kwargs) -> Path:
        raise OSError("metadata unavailable")

    monkeypatch.setattr(privacy_overlay.Path, "resolve", fail_resolve)

    assert _resolve_overlay_path() is None
