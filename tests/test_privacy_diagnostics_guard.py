"""Tests for the non-sensitive, fail-closed diagnostics reveal guard."""

from __future__ import annotations

import json
import os
import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from openchronicle import paths
from openchronicle.capture import privacy_diagnostics_guard as guard_mod
from openchronicle.capture.privacy_diagnostics_guard import DiagnosticsLeaseManager


def make_manager(tmp_path: Path, *, process_alive=None) -> DiagnosticsLeaseManager:
    return DiagnosticsLeaseManager(
        tmp_path / "privacy-reveal.guard",
        process_alive=process_alive or (lambda _pid: True),
    )


def test_runtime_paths_are_under_owner_only_runtime_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing private runtime directory must be created with mode 0700."""
    monkeypatch.setenv("OPENCHRONICLE_ROOT", str(tmp_path / "openchronicle"))

    paths.ensure_dirs()

    assert paths.runtime_dir() == tmp_path / "openchronicle" / "runtime"
    assert paths.privacy_diagnostics_socket() == paths.runtime_dir() / "privacy-diagnostics.sock"
    assert paths.privacy_diagnostics_guard() == paths.runtime_dir() / "privacy-reveal.guard"
    assert stat.S_IMODE(paths.runtime_dir().stat().st_mode) == 0o700


def test_guard_contains_only_non_sensitive_metadata(tmp_path: Path) -> None:
    """Writing a lease must never persist values rendered by diagnostics."""
    marker = "private-window-title"
    manager = make_manager(tmp_path)

    lease = manager.acquire(pid=123, display_id=2)

    guard_path = tmp_path / "privacy-reveal.guard"
    raw = guard_path.read_text()
    payload = json.loads(raw)
    assert payload == {
        "schema_version": 1,
        "lease_id": lease.lease_id,
        "pid": 123,
        "display_ids": [2],
    }
    assert marker not in raw
    assert stat.S_IMODE(guard_path.stat().st_mode) == 0o600


def test_move_protects_old_and_new_until_commit(tmp_path: Path) -> None:
    """A move cannot briefly expose either display during its handoff."""
    manager = make_manager(tmp_path)
    lease = manager.acquire(pid=123, display_id=1)

    transition = manager.begin_move(lease.lease_id, pid=123, new_display_id=2)

    assert manager.snapshot().display_ids == frozenset({1, 2})
    manager.commit_move(transition.transition_id)
    assert manager.snapshot().display_ids == frozenset({2})


def test_restarted_manager_rejects_a_second_move_from_an_uncommitted_guard(tmp_path: Path) -> None:
    """A restart must not turn an interrupted two-display handoff into three displays."""
    manager = make_manager(tmp_path)
    lease = manager.acquire(pid=123, display_id=1)
    manager.begin_move(lease.lease_id, pid=123, new_display_id=2)
    restarted = make_manager(tmp_path)
    restarted.load()

    with pytest.raises(ValueError, match="uncommitted"):
        restarted.begin_move(lease.lease_id, pid=123, new_display_id=3)

    assert restarted.snapshot().display_ids == frozenset({1, 2})


def test_release_requires_matching_lease_id_and_pid(tmp_path: Path) -> None:
    """A stale page must not release a newer diagnostics reveal."""
    manager = make_manager(tmp_path)
    lease = manager.acquire(pid=123, display_id=2)

    with pytest.raises(ValueError, match="lease"):
        manager.release("0" * 32, pid=123)
    with pytest.raises(ValueError, match="pid"):
        manager.release(lease.lease_id, pid=456)

    assert manager.snapshot().display_ids == frozenset({2})


def test_release_removes_a_clean_guard(tmp_path: Path) -> None:
    """A valid matching release leaves no persisted reveal protection."""
    manager = make_manager(tmp_path)
    lease = manager.acquire(pid=123, display_id=2)

    manager.release(lease.lease_id, pid=123)

    assert not (tmp_path / "privacy-reveal.guard").exists()
    assert manager.snapshot().display_ids == frozenset()
    assert manager.snapshot().fail_closed_all is False


def test_load_restores_a_guard_when_the_app_pid_is_alive(tmp_path: Path) -> None:
    """A daemon restart must keep protecting a display owned by a live app."""
    original = make_manager(tmp_path)
    lease = original.acquire(pid=123, display_id=4)
    restarted = make_manager(tmp_path, process_alive=lambda pid: pid == 123)

    snapshot = restarted.load()

    assert snapshot == restarted.snapshot()
    assert snapshot.display_ids == frozenset({4})
    assert snapshot.fail_closed_all is False
    assert restarted.release(lease.lease_id, pid=123).display_ids == frozenset()


def test_load_prunes_guard_only_after_process_death_is_confirmed(tmp_path: Path) -> None:
    """Confirmed process death may clear an otherwise valid stale guard."""
    make_manager(tmp_path).acquire(pid=123, display_id=4)
    restarted = make_manager(tmp_path, process_alive=lambda _pid: False)

    snapshot = restarted.load()

    assert snapshot.display_ids == frozenset()
    assert snapshot.fail_closed_all is False
    assert not (tmp_path / "privacy-reveal.guard").exists()


@pytest.mark.parametrize("probe", [lambda _pid: None, lambda _pid: (_ for _ in ()).throw(OSError())])
def test_load_keeps_guard_when_process_state_is_uncertain(tmp_path: Path, probe) -> None:
    """Permission or probe failures must retain protection rather than guess."""
    make_manager(tmp_path).acquire(pid=123, display_id=4)
    restarted = make_manager(tmp_path, process_alive=probe)

    snapshot = restarted.load()

    assert snapshot.display_ids == frozenset({4})
    assert snapshot.fail_closed_all is False
    assert (tmp_path / "privacy-reveal.guard").exists()


@pytest.mark.parametrize(
    "raw",
    [
        "{",
        json.dumps(
            {
                "schema_version": 1,
                "lease_id": "0" * 32,
                "pid": 123,
                "display_ids": [2],
                "unexpected": "field",
            }
        ),
        json.dumps(
            {"schema_version": 1, "lease_id": "0" * 32, "pid": 0, "display_ids": [2]}
        ),
        json.dumps(
            {
                "schema_version": 1,
                "lease_id": "0" * 32,
                "pid": 1 << 31,
                "display_ids": [2],
            }
        ),
        json.dumps(
            {"schema_version": 1, "lease_id": "0" * 32, "pid": 123, "display_ids": [0]}
        ),
        json.dumps(
            {"schema_version": 2, "lease_id": "0" * 32, "pid": 123, "display_ids": [2]}
        ),
    ],
)
def test_malformed_guard_fails_closed_without_deleting_it(tmp_path: Path, raw: str) -> None:
    """Invalid, truncated, or expanded guard data must protect every display."""
    guard_path = tmp_path / "privacy-reveal.guard"
    guard_path.write_text(raw)
    manager = make_manager(tmp_path)

    snapshot = manager.load()

    assert snapshot.display_ids == frozenset()
    assert snapshot.fail_closed_all is True
    assert guard_path.read_text() == raw


@pytest.mark.parametrize("pid, display_id", [(0, 2), (-1, 2), (123, 0), (123, -1)])
def test_acquire_rejects_invalid_pid_or_display_id(tmp_path: Path, pid: int, display_id: int) -> None:
    """Special process IDs and nonsensical display IDs must never enter the guard."""
    manager = make_manager(tmp_path)

    with pytest.raises(ValueError):
        manager.acquire(pid=pid, display_id=display_id)

    assert not (tmp_path / "privacy-reveal.guard").exists()


def test_concurrent_acquire_creates_exactly_one_lease(tmp_path: Path) -> None:
    """The in-process lock prevents two app pages from winning the singleton guard."""
    manager = make_manager(tmp_path)
    start = threading.Barrier(8)

    def acquire_once() -> str:
        start.wait()
        try:
            return manager.acquire(pid=123, display_id=2).lease_id
        except ValueError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _index: acquire_once(), range(8)))

    assert len([result for result in results if result != "rejected"]) == 1
    assert manager.snapshot().display_ids == frozenset({2})
    assert json.loads((tmp_path / "privacy-reveal.guard").read_text())["display_ids"] == [2]


def test_guard_uses_a_same_directory_atomic_replace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The externally visible guard changes only through a fully-written 0600 replacement."""
    manager = make_manager(tmp_path)
    guard_path = tmp_path / "privacy-reveal.guard"
    real_replace = os.replace
    replacements: list[tuple[Path, Path]] = []

    def checking_replace(source: Path | str, destination: Path | str) -> None:
        temporary = Path(source)
        target = Path(destination)
        replacements.append((temporary, target))
        assert temporary.parent == guard_path.parent
        assert target == guard_path
        assert stat.S_IMODE(temporary.stat().st_mode) == 0o600
        assert json.loads(temporary.read_text())["display_ids"] == [2]
        real_replace(source, destination)

    monkeypatch.setattr(guard_mod.os, "replace", checking_replace)

    manager.acquire(pid=123, display_id=2)

    assert replacements
