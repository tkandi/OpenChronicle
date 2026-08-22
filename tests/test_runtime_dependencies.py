"""Smoke tests for dependencies imported only by real provider response paths."""

from __future__ import annotations

import os
import platform
import shutil
import stat
import subprocess
import tomllib
from pathlib import Path

import pytest


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


def test_screen_capture_sources_are_declared_for_wheel() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())
    mappings = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]

    assert mappings["resources/mac-screen-capture-core.swift"] == (
        "openchronicle/_bundled/mac-screen-capture-core.swift"
    )
    assert mappings["resources/mac-screen-capture.swift"] == (
        "openchronicle/_bundled/mac-screen-capture.swift"
    )
    assert mappings["resources/build-mac-screen-capture.sh"] == (
        "openchronicle/_bundled/build-mac-screen-capture.sh"
    )


@pytest.mark.parametrize(
    "generated_path",
    [
        "/resources/mac-window-list",
        "/resources/mac-privacy-overlay",
        "/resources/mac-screen-capture",
        "/macos/OpenChronicleApp/.build",
    ],
)
def test_generated_macos_artifacts_are_excluded_from_sdist(generated_path: str) -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())
    excludes = pyproject["tool"]["hatch"]["build"]["targets"]["sdist"]["exclude"]

    assert generated_path in excludes


@pytest.mark.skipif(platform.system() != "Darwin", reason="requires the macOS Swift SDK")
def test_screen_capture_build_script_is_relocatable_to_wheel_bundle(tmp_path: Path) -> None:
    bundled = tmp_path / "openchronicle" / "_bundled"
    bundled.mkdir(parents=True)
    for name in (
        "mac-screen-capture-core.swift",
        "mac-screen-capture.swift",
        "build-mac-screen-capture.sh",
    ):
        shutil.copy2(Path("resources") / name, bundled / name)

    result = subprocess.run(
        ["bash", str(bundled / "build-mac-screen-capture.sh")],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "CLANG_MODULE_CACHE_PATH": str(tmp_path / "module-cache")},
    )

    assert result.returncode == 0, result.stderr
    helper = bundled / "mac-screen-capture"
    assert helper.is_file()
    assert helper.stat().st_mode & stat.S_IXUSR


@pytest.mark.skipif(platform.system() != "Darwin", reason="requires the macOS Swift SDK")
def test_screen_capture_build_script_emits_unsupported_helper_for_old_sdk(
    tmp_path: Path,
) -> None:
    bundled = tmp_path / "openchronicle" / "_bundled"
    bundled.mkdir(parents=True)
    for name in (
        "mac-screen-capture-core.swift",
        "mac-screen-capture.swift",
        "build-mac-screen-capture.sh",
    ):
        shutil.copy2(Path("resources") / name, bundled / name)

    result = subprocess.run(
        ["bash", str(bundled / "build-mac-screen-capture.sh")],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "CLANG_MODULE_CACHE_PATH": str(tmp_path / "module-cache"),
            "OPENCHRONICLE_MACOS_SDK_VERSION": "13.3",
        },
    )

    assert result.returncode == 0, result.stderr
    helper = bundled / "mac-screen-capture"
    assert helper.is_file()
    assert helper.stat().st_mode & stat.S_IXUSR
    response = subprocess.run(
        [str(helper)],
        input="private malformed command\n",
        check=False,
        capture_output=True,
        text=True,
    )
    assert response.returncode == 0
    assert response.stdout == '{"version":1,"status":"error","error":"unsupported_os"}\n'
    assert response.stderr == ""
