from pathlib import Path

from openchronicle import config


def test_defaults_when_no_file(tmp_path: Path) -> None:
    cfg = config.load(tmp_path / "missing.toml")
    assert cfg.capture.interval_minutes == 10
    assert cfg.session.gap_minutes == 5
    assert cfg.reducer.enabled is True
    default = cfg.model_for("reducer")
    assert default.model == "gpt-5.4-nano"


def test_stage_override_merges(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        """
[models.default]
model = "gpt-5.4-nano"
api_key_env = "OPENAI_API_KEY"

[models.classifier]
model = "claude-haiku-4-5"
api_key_env = "ANTHROPIC_API_KEY"
"""
    )
    cfg = config.load(path)
    default = cfg.model_for("default")
    classifier = cfg.model_for("classifier")
    assert default.model == "gpt-5.4-nano"
    assert default.api_key_env == "OPENAI_API_KEY"
    assert classifier.model == "claude-haiku-4-5"
    assert classifier.api_key_env == "ANTHROPIC_API_KEY"


def test_capture_denylist_config(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        r"""
[capture]
deny_app_names = ["Passwords"]
deny_bundle_ids = ["com.microsoft.edgemac"]
deny_window_title_patterns = ["InPrivate", "无痕"]
deny_url_patterns = ["account\\.example"]
deny_text_patterns = ["secret token"]
"""
    )
    cfg = config.load(path)
    assert cfg.capture.deny_app_names == ["Passwords"]
    assert cfg.capture.deny_bundle_ids == ["com.microsoft.edgemac"]
    assert cfg.capture.deny_window_title_patterns == ["InPrivate", "无痕"]
    assert cfg.capture.deny_url_patterns == ["account\\.example"]
    assert cfg.capture.deny_text_patterns == ["secret token"]


def test_unknown_title_protection_bundle_defaults_and_override(tmp_path: Path) -> None:
    missing = config.load(tmp_path / "missing.toml").capture
    assert missing.protect_unknown_title_bundle_ids == [
        "com.microsoft.edgemac",
        "com.google.Chrome",
        "org.mozilla.firefox",
    ]

    path = tmp_path / "config.toml"
    path.write_text(
        '[capture]\nprotect_unknown_title_bundle_ids = ["com.example.browser"]\n'
    )
    assert config.load(path).capture.protect_unknown_title_bundle_ids == [
        "com.example.browser"
    ]

    path.write_text('[capture]\nprotect_unknown_title_bundle_ids = []\n')
    assert config.load(path).capture.protect_unknown_title_bundle_ids == []


def test_capture_screenshot_monitor_config(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        """
[capture]
screenshot_monitor = "SEPARATE"
"""
    )
    cfg = config.load(path)
    assert cfg.capture.screenshot_monitor == "separate"

    path.write_text(
        """
[capture]
screenshot_monitor = "unknown"
"""
    )
    cfg = config.load(path)
    assert cfg.capture.screenshot_monitor == "primary"


def test_capture_screenshot_privacy_config(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        """
[capture]
screenshot_privacy_mode = "OFF"
screenshot_privacy_fail_closed = false
"""
    )
    cfg = config.load(path)
    assert cfg.capture.screenshot_privacy_mode == "off"
    assert cfg.capture.screenshot_privacy_fail_closed is False

    for value in ("MASK-WINDOW", "EXCLUDE-WINDOW"):
        path.write_text(
            f'''
[capture]
screenshot_privacy_mode = "{value}"
'''
        )
        cfg = config.load(path)
        assert cfg.capture.screenshot_privacy_mode == value.lower()

    path.write_text(
        """
[capture]
screenshot_privacy_mode = "unknown"
"""
    )
    cfg = config.load(path)
    assert cfg.capture.screenshot_privacy_mode == "skip-monitor"


def test_capture_privacy_indicator_style_config(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[capture]\nprivacy_indicator_style = "SHIELD"\n')
    assert config.load(path).capture.privacy_indicator_style == "shield"

    path.write_text('[capture]\nprivacy_indicator_style = "unknown"\n')
    assert config.load(path).capture.privacy_indicator_style == "pill"
    assert config.load(tmp_path / "missing.toml").capture.privacy_indicator_style == "pill"


def test_capture_privacy_indicator_placement_config(tmp_path: Path) -> None:
    missing = config.load(tmp_path / "missing.toml").capture
    assert missing.privacy_indicator_placement == "bottom-left-flush"

    path = tmp_path / "config.toml"
    for raw, expected in (
        ("BOTTOM-LEFT-FLUSH", "bottom-left-flush"),
        ("bottom-left-inset", "bottom-left-inset"),
        ("bottom-right-work-area", "bottom-right-work-area"),
        ("unknown", "bottom-left-flush"),
    ):
        path.write_text(f'[capture]\nprivacy_indicator_placement = "{raw}"\n')
        assert config.load(path).capture.privacy_indicator_placement == expected


def test_capture_config_preserves_pre_indicator_placement_positional_signature() -> None:
    capture = config.CaptureConfig(
        "border",
        "overlay",
        "tiered",
        "click",
        False,
        11,
        1.5,
        2.5,
        3.5,
        4.5,
        12,
        169,
        25,
        2001,
        False,
        "all",
        "mask-window",
        False,
        1600,
        70,
        99,
        4,
        ["Private App"],
        ["com.example.private"],
        ["Private Window"],
        ["private.example"],
        ["private text"],
    )

    assert (
        capture.privacy_indicator_style,
        capture.privacy_reason_display,
        capture.privacy_reason_detail,
        capture.privacy_reason_trigger,
        capture.event_driven,
        capture.heartbeat_minutes,
        capture.debounce_seconds,
        capture.min_capture_gap_seconds,
        capture.dedup_interval_seconds,
        capture.same_window_dedup_seconds,
        capture.interval_minutes,
        capture.buffer_retention_hours,
        capture.screenshot_retention_hours,
        capture.buffer_max_mb,
        capture.include_screenshot,
        capture.screenshot_monitor,
        capture.screenshot_privacy_mode,
        capture.screenshot_privacy_fail_closed,
        capture.screenshot_max_width,
        capture.screenshot_jpeg_quality,
        capture.ax_depth,
        capture.ax_timeout_seconds,
        capture.deny_app_names,
        capture.deny_bundle_ids,
        capture.deny_window_title_patterns,
        capture.deny_url_patterns,
        capture.deny_text_patterns,
    ) == (
        "border",
        "overlay",
        "tiered",
        "click",
        False,
        11,
        1.5,
        2.5,
        3.5,
        4.5,
        12,
        169,
        25,
        2001,
        False,
        "all",
        "mask-window",
        False,
        1600,
        70,
        99,
        4,
        ["Private App"],
        ["com.example.private"],
        ["Private Window"],
        ["private.example"],
        ["private text"],
    )
    assert capture.privacy_indicator_placement == "bottom-left-flush"
    assert capture.protect_unknown_title_bundle_ids == [
        "com.microsoft.edgemac",
        "com.google.Chrome",
        "org.mozilla.firefox",
    ]


def test_privacy_reason_settings_default_and_normalize(tmp_path: Path) -> None:
    missing = config.load(tmp_path / "missing.toml").capture
    assert (
        missing.privacy_reason_display,
        missing.privacy_reason_detail,
        missing.privacy_reason_trigger,
    ) == ("hybrid", "exact", "hover")

    path = tmp_path / "config.toml"
    path.write_text(
        '[capture]\nprivacy_reason_display="OVERLAY"\n'
        'privacy_reason_detail="CATEGORY"\nprivacy_reason_trigger="CLICK"\n'
    )
    capture = config.load(path).capture
    assert (
        capture.privacy_reason_display,
        capture.privacy_reason_detail,
        capture.privacy_reason_trigger,
    ) == ("overlay", "category", "click")

    path.write_text(
        '[capture]\nprivacy_reason_display="bad"\n'
        'privacy_reason_detail="bad"\nprivacy_reason_trigger="bad"\n'
    )
    capture = config.load(path).capture
    assert (
        capture.privacy_reason_display,
        capture.privacy_reason_detail,
        capture.privacy_reason_trigger,
    ) == ("hybrid", "exact", "hover")


def test_write_default_creates_file(tmp_path: Path) -> None:
    p = tmp_path / "config.toml"
    assert config.write_default_if_missing(p)
    assert p.exists()
    assert "[models.default]" in p.read_text()
    # idempotent
    assert not config.write_default_if_missing(p)


def test_api_key_precedence(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ENV_KEY", "from-env")
    cfg = config.ModelConfig(api_key="direct", api_key_env="ENV_KEY")
    assert config.resolve_api_key(cfg) == "direct"
    cfg2 = config.ModelConfig(api_key="", api_key_env="ENV_KEY")
    assert config.resolve_api_key(cfg2) == "from-env"
