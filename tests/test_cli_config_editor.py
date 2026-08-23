from __future__ import annotations

import json
import tomllib
from pathlib import Path

from typer.testing import CliRunner

from openchronicle import cli


def _invoke_json(runner: CliRunner, args: list[str], payload: dict | None = None):
    input_text = json.dumps(payload) if payload is not None else None
    result = runner.invoke(cli.app, args, input=input_text)
    parsed = json.loads(result.output)
    return result, parsed


def test_config_json_is_secret_safe(ac_root: Path) -> None:
    secret = "sk-local-secret-that-must-not-leak"
    (ac_root / "config.toml").write_text(
        f"""
[models.default]
model = "gpt-test"
api_key = "{secret}"
api_key_env = "OPENAI_API_KEY"

[capture]
deny_text_patterns = ["private phrase that should stay in TOML"]
"""
    )

    result, payload = _invoke_json(CliRunner(), ["config", "--json"])

    assert result.exit_code == 0, result.output
    assert secret not in result.output
    assert "private phrase that should stay in TOML" not in result.output
    assert payload["valid"] is True
    assert payload["contains_direct_api_keys"] is True
    assert payload["values"]["models"]["default"]["uses_direct_api_key"] is True
    assert "api_key" not in payload["values"]["models"]["default"]
    assert payload["values"]["capture"]["privacy_counts"]["deny_text_patterns"] == 1


def test_config_indicator_style_is_editable_and_validated(ac_root: Path) -> None:
    path = ac_root / "config.toml"
    path.write_text('[capture]\nprivacy_indicator_style = "pill"\n')
    runner = CliRunner()
    _, snapshot = _invoke_json(runner, ["config", "--json"])
    assert snapshot["values"]["capture"]["privacy_indicator_style"] == "pill"

    result, payload = _invoke_json(
        runner,
        ["config", "--patch-json"],
        {
            "expected_sha256": snapshot["sha256"],
            "updates": {"capture.privacy_indicator_style": "border"},
        },
    )
    assert result.exit_code == 0, result.output
    assert tomllib.loads(path.read_text())["capture"]["privacy_indicator_style"] == "border"

    result, payload = _invoke_json(
        runner,
        ["config", "--validate-json"],
        {"content": '[capture]\nprivacy_indicator_style = "invalid"\n'},
    )
    assert result.exit_code == 2
    assert "privacy_indicator_style" in payload["error"]


def test_indicator_placement_patch_validate_and_snapshot(ac_root: Path) -> None:
    path = ac_root / "config.toml"
    path.write_text('[capture]\nprivacy_indicator_placement = "bottom-left-flush"\n')
    runner = CliRunner()
    _, snapshot = _invoke_json(runner, ["config", "--json"])
    assert snapshot["values"]["capture"]["privacy_indicator_placement"] == (
        "bottom-left-flush"
    )

    result, payload = _invoke_json(
        runner,
        ["config", "--patch-json"],
        {
            "expected_sha256": snapshot["sha256"],
            "updates": {
                "capture.privacy_indicator_placement": "bottom-left-inset"
            },
        },
    )
    assert result.exit_code == 0, result.output
    assert payload["changed"] is True
    assert tomllib.loads(path.read_text())["capture"]["privacy_indicator_placement"] == (
        "bottom-left-inset"
    )

    result, payload = _invoke_json(
        runner,
        ["config", "--validate-json"],
        {"content": '[capture]\nprivacy_indicator_placement = "invalid"\n'},
    )
    assert result.exit_code == 2
    assert "privacy_indicator_placement" in payload["error"]


def test_screenshot_privacy_window_modes_patch_validate_and_snapshot(ac_root: Path) -> None:
    path = ac_root / "config.toml"
    path.write_text('[capture]\nscreenshot_privacy_mode = "mask-window"\n')
    runner = CliRunner()
    _, snapshot = _invoke_json(runner, ["config", "--json"])
    assert snapshot["values"]["capture"]["screenshot_privacy_mode"] == "mask-window"

    result, payload = _invoke_json(
        runner,
        ["config", "--patch-json"],
        {
            "expected_sha256": snapshot["sha256"],
            "updates": {"capture.screenshot_privacy_mode": "exclude-window"},
        },
    )
    assert result.exit_code == 0, result.output
    assert payload["changed"] is True
    assert tomllib.loads(path.read_text())["capture"]["screenshot_privacy_mode"] == "exclude-window"

    result, payload = _invoke_json(
        runner,
        ["config", "--validate-json"],
        {"content": '[capture]\nscreenshot_privacy_mode = "invalid"\n'},
    )
    assert result.exit_code == 2
    assert "screenshot_privacy_mode" in payload["error"]


def test_privacy_reason_settings_patch_and_validate(ac_root: Path) -> None:
    path = ac_root / "config.toml"
    path.write_text("[capture]\nprivacy_reason_display = \"hybrid\"\n")
    runner = CliRunner()
    _, snapshot = _invoke_json(runner, ["config", "--json"])
    capture = snapshot["values"]["capture"]
    assert (
        capture["privacy_reason_display"],
        capture["privacy_reason_detail"],
        capture["privacy_reason_trigger"],
    ) == ("hybrid", "exact", "hover")

    for path_name, value in (
        ("capture.privacy_reason_display", "diagnostics"),
        ("capture.privacy_reason_detail", "category"),
        ("capture.privacy_reason_trigger", "click"),
    ):
        result, payload = _invoke_json(
            runner,
            ["config", "--patch-json"],
            {
                "expected_sha256": snapshot["sha256"],
                "updates": {path_name: value},
            },
        )
        assert result.exit_code == 0, result.output
        assert payload["changed"] is True
        snapshot = _invoke_json(runner, ["config", "--json"])[1]

    parsed = tomllib.loads(path.read_text())
    assert parsed["capture"] == {
        "privacy_reason_display": "diagnostics",
        "privacy_reason_detail": "category",
        "privacy_reason_trigger": "click",
    }

    for field in (
        "privacy_reason_display",
        "privacy_reason_detail",
        "privacy_reason_trigger",
    ):
        result, payload = _invoke_json(
            runner,
            ["config", "--validate-json"],
            {"content": f'[capture]\n{field} = "invalid"\n'},
        )
        assert result.exit_code == 2
        assert payload["error"].startswith(f"capture.{field} ")


def test_config_privacy_json_is_explicit_and_still_omits_api_keys(ac_root: Path) -> None:
    secret = "sk-local-secret-that-must-not-leak"
    private_rule = "private.example.com"
    (ac_root / "config.toml").write_text(
        f"""
[models.default]
api_key = "{secret}"

[capture]
deny_url_patterns = ["{private_rule}"]
"""
    )

    result, payload = _invoke_json(CliRunner(), ["config", "--privacy-json"])

    assert result.exit_code == 0, result.output
    assert payload["valid"] is True
    assert payload["values"]["deny_url_patterns"] == [private_rule]
    assert secret not in result.output
    assert "api_key" not in result.output


def test_config_patch_preserves_comments_and_creates_backup(ac_root: Path) -> None:
    path = ac_root / "config.toml"
    original = """# personal comments stay intact
[models.default]
model = "gpt-old"

[models.timeline]
model = "gpt-timeline"  # keep this note

[capture]
heartbeat_minutes = 10  # periodic fallback

[future]
unknown_setting = "preserve me"
"""
    path.write_text(original)
    runner = CliRunner()
    _, snapshot = _invoke_json(runner, ["config", "--json"])

    result, payload = _invoke_json(
        runner,
        ["config", "--patch-json"],
        {
            "expected_sha256": snapshot["sha256"],
            "updates": {
                "models.default.model": "gpt-new",
                "models.timeline.model": None,
                "capture.heartbeat_minutes": 15,
            },
        },
    )

    assert result.exit_code == 0, result.output
    assert payload["changed"] is True
    updated = path.read_text()
    assert 'model = "gpt-new"' in updated
    assert "gpt-timeline" not in updated
    assert "heartbeat_minutes = 15  # periodic fallback" in updated
    assert "# personal comments stay intact" in updated
    assert 'unknown_setting = "preserve me"' in updated
    backup = Path(payload["backup"])
    assert backup.exists()
    assert backup.read_text() == original


def test_config_write_rejects_invalid_toml_without_touching_file(ac_root: Path) -> None:
    path = ac_root / "config.toml"
    original = "[capture]\nheartbeat_minutes = 10\n"
    path.write_text(original)
    runner = CliRunner()
    _, snapshot = _invoke_json(runner, ["config", "--json"])

    result, payload = _invoke_json(
        runner,
        ["config", "--write-json"],
        {
            "expected_sha256": snapshot["sha256"],
            "content": "[capture\nheartbeat_minutes = -1",
        },
    )

    assert result.exit_code == 2
    assert payload["ok"] is False
    assert "Invalid TOML" in payload["error"]
    assert path.read_text() == original
    assert not list(ac_root.glob("config.toml.backup-*"))


def test_config_patch_detects_external_change(ac_root: Path) -> None:
    path = ac_root / "config.toml"
    path.write_text("[capture]\nheartbeat_minutes = 10\n")
    runner = CliRunner()
    _, snapshot = _invoke_json(runner, ["config", "--json"])
    path.write_text("[capture]\nheartbeat_minutes = 20\n")

    result, payload = _invoke_json(
        runner,
        ["config", "--patch-json"],
        {
            "expected_sha256": snapshot["sha256"],
            "updates": {"capture.heartbeat_minutes": 30},
        },
    )

    assert result.exit_code == 2
    assert payload["ok"] is False
    assert "changed after it was loaded" in payload["error"]
    assert "heartbeat_minutes = 20" in path.read_text()


def test_config_patch_replaces_multiline_privacy_arrays(ac_root: Path) -> None:
    path = ac_root / "config.toml"
    original = """[capture]
# keep before
deny_app_names = [
  "Private One",
  "Private Two", # internal array comments may be replaced with the field
]
deny_url_patterns = ["old.example"]
# keep after
heartbeat_minutes = 10
"""
    path.write_text(original)
    runner = CliRunner()
    _, snapshot = _invoke_json(runner, ["config", "--json"])

    result, payload = _invoke_json(
        runner,
        ["config", "--patch-json"],
        {
            "expected_sha256": snapshot["sha256"],
            "updates": {
                "capture.deny_app_names": ["Mail", "Password Manager"],
                "capture.deny_url_patterns": [r"accounts\.example\.com"],
            },
        },
    )

    assert result.exit_code == 0, result.output
    assert payload["changed"] is True
    updated = path.read_text()
    parsed = tomllib.loads(updated)
    assert parsed["capture"]["deny_app_names"] == ["Mail", "Password Manager"]
    assert parsed["capture"]["deny_url_patterns"] == [r"accounts\.example\.com"]
    assert "# keep before" in updated
    assert "# keep after" in updated
    assert Path(payload["backup"]).read_text() == original


def test_config_validate_json_checks_semantic_ranges(ac_root: Path) -> None:
    result, payload = _invoke_json(
        CliRunner(),
        ["config", "--validate-json"],
        {"content": "[mcp]\nport = 70000\n"},
    )

    assert result.exit_code == 2
    assert payload["ok"] is False
    assert "mcp.port must be at most 65535" in payload["error"]
