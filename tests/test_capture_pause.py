from __future__ import annotations

import json
import logging
import stat
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

from typer.testing import CliRunner

from openchronicle import capture_pause as capture_pause_mod
from openchronicle import cli as cli_mod
from openchronicle.capture.protection_reason import ProtectionReasonCode
from openchronicle.capture_pause import (
    CapturePauseKind,
    capture_is_paused,
    capture_is_paused_strict,
    capture_pause_decision_strict,
    parse_pause_state,
    pause_reason_from_decision,
)


def _state(
    *,
    resume_at: datetime,
    armed_at: datetime | None,
    heartbeat_at: datetime | None,
) -> bytes:
    return json.dumps(
        {
            "schema_version": 1,
            "id": "pause-test",
            "mode": "timed",
            "started_at": (resume_at - timedelta(minutes=30)).isoformat(),
            "resume_at": resume_at.isoformat(),
            "resume_armed_at": armed_at.isoformat() if armed_at else None,
            "app_heartbeat_at": heartbeat_at.isoformat() if heartbeat_at else None,
            "last_reminder_at": None,
        }
    ).encode()


def test_legacy_pause_never_auto_resumes(tmp_path) -> None:
    pause_file = tmp_path / ".paused"
    pause_file.write_text("2026-07-19T12:00:00+08:00")

    assert capture_is_paused(
        pause_path=pause_file,
        now=datetime(2026, 7, 19, 8, tzinfo=UTC),
    )
    assert pause_file.exists()


def test_timed_pause_requires_warning_arm(tmp_path) -> None:
    now = datetime(2026, 7, 19, 8, tzinfo=UTC)
    pause_file = tmp_path / ".paused"
    pause_file.write_bytes(
        _state(resume_at=now - timedelta(minutes=1), armed_at=None, heartbeat_at=now)
    )

    assert capture_is_paused(pause_path=pause_file, now=now)
    assert pause_file.exists()


def test_timed_pause_requires_recent_app_heartbeat(tmp_path) -> None:
    now = datetime(2026, 7, 19, 8, tzinfo=UTC)
    pause_file = tmp_path / ".paused"
    pause_file.write_bytes(
        _state(
            resume_at=now - timedelta(minutes=1),
            armed_at=now - timedelta(minutes=2),
            heartbeat_at=now - timedelta(minutes=2),
        )
    )

    assert capture_is_paused(pause_path=pause_file, now=now)
    assert pause_file.exists()


def test_safely_expired_timed_pause_resumes_capture(tmp_path) -> None:
    now = datetime(2026, 7, 19, 8, tzinfo=UTC)
    pause_file = tmp_path / ".paused"
    pause_file.write_bytes(
        _state(
            resume_at=now - timedelta(seconds=1),
            armed_at=now - timedelta(minutes=2),
            heartbeat_at=now,
        )
    )

    assert not capture_is_paused(pause_path=pause_file, now=now)
    assert not pause_file.exists()


def test_effective_deadline_preserves_full_warning_minute() -> None:
    deadline = datetime(2026, 7, 19, 8, tzinfo=UTC)
    armed_at = deadline + timedelta(seconds=10)
    state = parse_pause_state(
        _state(resume_at=deadline, armed_at=armed_at, heartbeat_at=armed_at)
    )

    assert state is not None
    assert state.effective_resume_at == armed_at + timedelta(minutes=1)


def test_pause_decision_classifies_missing_legacy_timed_and_safe_resume(tmp_path: Path) -> None:
    now = datetime(2026, 7, 19, 8, tzinfo=UTC)
    pause_file = tmp_path / ".paused"

    assert capture_pause_decision_strict(pause_path=pause_file, now=now).kind is CapturePauseKind.NOT_PAUSED

    pause_file.write_text("2026-07-19T12:00:00+08:00")
    assert capture_pause_decision_strict(pause_path=pause_file, now=now).kind is CapturePauseKind.INDEFINITE

    pause_file.write_bytes(
        _state(
            resume_at=now + timedelta(minutes=5),
            armed_at=now,
            heartbeat_at=now,
        )
    )
    timed = capture_pause_decision_strict(pause_path=pause_file, now=now)
    assert timed.paused is True
    assert timed.kind is CapturePauseKind.TIMED
    assert timed.effective_resume_at == now + timedelta(minutes=5)

    pause_file.write_bytes(
        _state(
            resume_at=now - timedelta(minutes=1),
            armed_at=now - timedelta(minutes=2),
            heartbeat_at=now,
        )
    )
    resumed = capture_pause_decision_strict(pause_path=pause_file, now=now)
    assert resumed.paused is False
    assert resumed.kind is CapturePauseKind.NOT_PAUSED
    assert not pause_file.exists()


def test_pause_decision_reports_timed_wait_and_effective_resume(tmp_path: Path) -> None:
    now = datetime(2026, 7, 19, 8, tzinfo=UTC)
    pause_file = tmp_path / ".paused"
    pause_file.write_bytes(
        _state(
            resume_at=now - timedelta(minutes=1),
            armed_at=now - timedelta(minutes=2),
            heartbeat_at=now - timedelta(minutes=2),
        )
    )

    decision = capture_pause_decision_strict(pause_path=pause_file, now=now)

    assert decision.paused is True
    assert decision.kind is CapturePauseKind.TIMED_WAITING
    assert decision.effective_resume_at == now - timedelta(minutes=1)
    assert capture_is_paused_strict(pause_path=pause_file, now=now) is True
    reason = pause_reason_from_decision(decision)
    assert reason is not None
    assert reason.code is ProtectionReasonCode.TIMED_PAUSE_WAITING
    assert reason.effective_resume_at == now - timedelta(minutes=1)


def test_capture_is_paused_fails_closed_with_sanitized_log(
    tmp_path: Path,
    monkeypatch,
) -> None:
    marker = "private-pause-marker-path"
    pause_file = tmp_path / ".paused"

    def fail_pause_read(_path: Path) -> bytes:
        raise OSError(marker)

    monkeypatch.setattr(Path, "read_bytes", fail_pause_read)

    messages: list[str] = []
    handler = logging.Handler()
    handler.emit = lambda record: messages.append(record.getMessage())  # type: ignore[method-assign]
    capture_logger = logging.getLogger("openchronicle.capture")
    original_propagate = capture_logger.propagate
    capture_logger.addHandler(handler)
    capture_logger.propagate = False
    try:
        assert capture_is_paused(pause_path=pause_file)
    finally:
        capture_logger.removeHandler(handler)
        capture_logger.propagate = original_propagate

    assert messages == ["capture pause state unavailable; remaining paused: OSError"]
    assert marker not in "\n".join(messages)


def test_pause_lock_path_is_stable_owner_only_sibling(tmp_path: Path) -> None:
    pause_file = tmp_path / ".paused"

    capture_pause_mod.write_capture_pause(b"paused", pause_path=pause_file)

    lock_path = capture_pause_mod.capture_pause_lock_path(pause_file)
    assert lock_path == tmp_path / ".paused.lock"
    assert stat.S_IMODE(lock_path.stat().st_mode) == 0o600


def test_extended_pause_written_under_lock_survives_daemon_auto_resume(
    tmp_path: Path,
    monkeypatch,
) -> None:
    now = datetime(2026, 7, 19, 8, tzinfo=UTC)
    pause_file = tmp_path / ".paused"
    expired_raw = _state(
        resume_at=now - timedelta(seconds=1),
        armed_at=now - timedelta(minutes=2),
        heartbeat_at=now,
    )
    extended_raw = _state(
        resume_at=now + timedelta(minutes=30),
        armed_at=None,
        heartbeat_at=now,
    )
    pause_file.write_bytes(expired_raw)

    writer_should_start = threading.Event()
    writer_started = threading.Event()
    writer_finished = threading.Event()
    original_unlink = Path.unlink

    def extend_pause() -> None:
        assert writer_should_start.wait(timeout=1)
        writer_started.set()
        capture_pause_mod.write_capture_pause(extended_raw, pause_path=pause_file)
        writer_finished.set()

    def controlled_unlink(path: Path, *args, **kwargs) -> None:
        if path == pause_file:
            writer_should_start.set()
            assert writer_started.wait(timeout=1)
            assert not writer_finished.wait(timeout=0.2)
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", controlled_unlink)
    writer = threading.Thread(target=extend_pause)
    writer.start()
    try:
        decision = capture_pause_decision_strict(pause_path=pause_file, now=now)
    finally:
        writer.join(timeout=1)

    assert decision.paused is False
    assert not writer.is_alive()
    assert writer_finished.is_set()
    assert pause_file.read_bytes() == extended_raw


def test_cli_pause_and_resume_use_the_shared_pause_boundary(
    tmp_path: Path,
    monkeypatch,
) -> None:
    pause_file = tmp_path / ".paused"
    monkeypatch.setattr(cli_mod.paths, "paused_flag", lambda: pause_file)
    monkeypatch.setattr(cli_mod.paths, "ensure_dirs", lambda: None)
    runner = CliRunner()

    paused = runner.invoke(cli_mod.app, ["pause"])

    assert paused.exit_code == 0
    assert pause_file.exists()
    assert capture_pause_mod.capture_pause_lock_path(pause_file).exists()

    resumed = runner.invoke(cli_mod.app, ["resume"])

    assert resumed.exit_code == 0
    assert not pause_file.exists()
