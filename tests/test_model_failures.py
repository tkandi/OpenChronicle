"""Tests for privacy-bounded model failure notification events."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from openchronicle.config import Config, ModelConfig
from openchronicle.model_failures import ModelFailureEventWriter
from openchronicle.writer import llm as llm_mod


class _Clock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value


def _events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def test_event_contains_only_bounded_sanitized_failure_metadata(tmp_path: Path) -> None:
    path = tmp_path / "events" / "model-failures.jsonl"
    writer = ModelFailureEventWriter(
        path,
        now=lambda: datetime(2026, 7, 18, 12, 0, tzinfo=UTC),
    )
    secret = "sk-super-secret-value"

    written = writer.record(
        stage="timeline",
        model="openai/gpt-test",
        error=RuntimeError(f"Unauthorized api_key={secret}\nrequest body: private prompt"),
        api_key=secret,
    )

    assert written is True
    event = _events(path)[0]
    assert event["schema_version"] == 1
    assert event["stage"] == "timeline"
    assert event["model"] == "openai/gpt-test"
    assert event["error_type"] == "RuntimeError"
    assert event["timestamp"] == "2026-07-18T12:00:00+00:00"
    assert "[REDACTED]" in event["message"]
    assert secret not in path.read_text()
    assert "private prompt" not in path.read_text()
    assert path.stat().st_mode & 0o777 == 0o600


def test_same_failure_is_suppressed_during_cooldown(tmp_path: Path) -> None:
    path = tmp_path / "model-failures.jsonl"
    clock = _Clock()
    writer = ModelFailureEventWriter(path, cooldown_seconds=900, monotonic=clock)
    error = TimeoutError("provider timed out")

    assert writer.record(stage="reducer", model="gpt-test", error=error) is True
    clock.value += 899
    assert writer.record(stage="reducer", model="gpt-test", error=error) is False
    clock.value += 1
    assert writer.record(stage="reducer", model="gpt-test", error=error) is True
    assert len(_events(path)) == 2


def test_call_llm_records_final_provider_exception(
    ac_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENCHRONICLE_LLM_MOCK", raising=False)
    import litellm

    class ProviderUnavailable(Exception):
        pass

    def fail(**kwargs):
        raise ProviderUnavailable("upstream unavailable")

    monkeypatch.setattr(litellm, "completion", fail)
    cfg = Config(
        models={"default": ModelConfig(model="gpt-test", api_key="sk-test-secret")}
    )

    with pytest.raises(ProviderUnavailable):
        llm_mod.call_llm(
            cfg,
            "classifier",
            messages=[{"role": "user", "content": "must not be persisted"}],
        )

    path = ac_root / "events" / "model-failures.jsonl"
    event = _events(path)[0]
    assert event["stage"] == "classifier"
    assert event["model"] == "gpt-test"
    assert event["message"] == "upstream unavailable"
    assert "must not be persisted" not in path.read_text()
