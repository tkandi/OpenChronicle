from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from openchronicle.capture_pause import capture_is_paused, parse_pause_state


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
