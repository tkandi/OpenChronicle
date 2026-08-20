import json
import stat
import sys
from pathlib import Path

import pytest

from openchronicle.capture.privacy import DisplayInfo, ScreenRegion
from openchronicle.capture.privacy_overlay import (
    PrivacyOverlayClient,
    _resolve_overlay_path,
    _SubprocessOverlayTransport,
)
from openchronicle.capture.protection import ProtectionSnapshot, ProtectionState


class FakeTransport:
    def __init__(self) -> None:
        self.writes: list[str] = []
        self.acknowledged: set[int] = set()
        self.closed = False

    def acknowledge(self, *, generation: int) -> None:
        self.acknowledged.add(generation)

    def write_line(self, line: str) -> None:
        self.writes.append(line)

    def wait_for_generation(self, generation: int, timeout: float) -> bool:
        return generation in self.acknowledged

    def close(self) -> None:
        self.closed = True


class FailingTransport(FakeTransport):
    def write_line(self, line: str) -> None:
        raise BrokenPipeError


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
    return FakeTransport()


def _helper_script(tmp_path: Path, body: str) -> Path:
    helper = tmp_path / "overlay-helper"
    helper.write_text(f"#!{sys.executable}\n{body}")
    helper.chmod(helper.stat().st_mode | stat.S_IXUSR)
    return helper


def test_overlay_command_contains_only_geometry_and_state(
    snapshot: ProtectionSnapshot, fake_transport: FakeTransport
) -> None:
    client = PrivacyOverlayClient(transport_factory=lambda: fake_transport)
    fake_transport.acknowledge(generation=snapshot.generation)

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
        fake_transport.acknowledge(generation=current.generation)
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
    fake_transport.acknowledge(generation=failed.generation)

    assert PrivacyOverlayClient(transport_factory=lambda: fake_transport).render(failed) is True
    assert json.loads(fake_transport.writes[-1])["all_displays"] is True


def test_wrong_generation_or_timeout_is_not_confirmed(
    snapshot: ProtectionSnapshot, fake_transport: FakeTransport
) -> None:
    client = PrivacyOverlayClient(transport_factory=lambda: fake_transport)
    fake_transport.acknowledge(generation=snapshot.generation - 1)

    assert client.render(snapshot, timeout=0.01) is False
    assert fake_transport.closed is True


def test_off_style_does_not_start_a_transport(snapshot: ProtectionSnapshot) -> None:
    starts = 0

    def factory() -> FakeTransport:
        nonlocal starts
        starts += 1
        return FakeTransport()

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


def test_failed_transport_restarts_with_bounded_backoff(snapshot: ProtectionSnapshot, monkeypatch) -> None:
    transports = [FailingTransport(), FakeTransport()]
    starts = 0
    now = 10.0

    def factory() -> FakeTransport:
        nonlocal starts
        transport = transports[starts]
        starts += 1
        return transport

    monkeypatch.setattr("openchronicle.capture.privacy_overlay.time.monotonic", lambda: now)
    client = PrivacyOverlayClient(transport_factory=factory)

    assert client.render(snapshot) is False
    assert client.render(snapshot) is False
    assert starts == 1

    now += 1.0
    transports[1].acknowledge(generation=snapshot.generation)
    assert client.render(snapshot) is True
    assert starts == 2


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


def test_resolver_accepts_executable_environment_override(monkeypatch, tmp_path: Path) -> None:
    helper = _helper_script(tmp_path, "")
    monkeypatch.setattr("openchronicle.capture.privacy_overlay.platform.system", lambda: "Darwin")
    monkeypatch.setenv("OPENCHRONICLE_PRIVACY_OVERLAY_HELPER", str(helper))

    assert _resolve_overlay_path() == helper.resolve()
