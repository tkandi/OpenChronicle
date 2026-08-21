"""Smoke tests for dependencies imported only by real provider response paths."""

from __future__ import annotations

import tomllib
from pathlib import Path


def test_litellm_tool_call_response_path_imports() -> None:
    # LiteLLM imports proxy-oriented helpers lazily when any tools are passed.
    # Unit tests that replace litellm.completion do not exercise that import
    # chain, so pin the exact runtime path used by the classifier.
    import fastapi
    import orjson
    from litellm.responses.mcp.chat_completions_handler import acompletion_with_mcp

    assert fastapi.__version__
    assert orjson.__version__
    assert callable(acompletion_with_mcp)


def test_privacy_overlay_sources_are_declared_for_wheel() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())
    mappings = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]

    assert mappings["resources/mac-privacy-overlay-reason.swift"] == (
        "openchronicle/_bundled/mac-privacy-overlay-reason.swift"
    )
    assert mappings["resources/mac-privacy-overlay-core.swift"] == (
        "openchronicle/_bundled/mac-privacy-overlay-core.swift"
    )
    assert mappings["resources/mac-privacy-overlay.swift"] == (
        "openchronicle/_bundled/mac-privacy-overlay.swift"
    )
    assert mappings["resources/build-mac-privacy-overlay.sh"] == (
        "openchronicle/_bundled/build-mac-privacy-overlay.sh"
    )


def test_window_list_core_source_is_declared_for_wheel() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())
    mappings = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]

    assert mappings["resources/mac-window-list-core.swift"] == (
        "openchronicle/_bundled/mac-window-list-core.swift"
    )
