"""Smoke tests for dependencies imported only by real provider response paths."""

from __future__ import annotations

import io
import os
import platform
import plistlib
import shutil
import stat
import subprocess
import tarfile
import tomllib
from pathlib import Path

import pytest

_MACH_O_MAGIC = frozenset(
    (
        b"\xfe\xed\xfa\xce",
        b"\xfe\xed\xfa\xcf",
        b"\xce\xfa\xed\xfe",
        b"\xcf\xfa\xed\xfe",
        b"\xca\xfe\xba\xbe",
        b"\xbe\xba\xfe\xca",
        b"\xca\xfe\xba\xbf",
        b"\xbf\xba\xfe\xca",
    )
)


def _archive_mach_o_entries(archive: tarfile.TarFile) -> list[str]:
    entries = []
    for member in archive.getmembers():
        if not member.isfile():
            continue
        contents = archive.extractfile(member)
        if contents is not None and contents.read(4) in _MACH_O_MAGIC:
            entries.append(member.name)
    return entries


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
    assert mappings["resources/mac-privacy-overlay-Info.plist"] == (
        "openchronicle/_bundled/mac-privacy-overlay-Info.plist"
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


@pytest.mark.skipif(platform.system() != "Darwin", reason="requires macOS codesign and Swift SDK")
@pytest.mark.parametrize("arch", ["arm64", "x86_64"])
def test_privacy_overlay_build_emits_signed_app_bundle(tmp_path: Path, arch: str) -> None:
    for name in (
        "mac-privacy-overlay-reason.swift",
        "mac-privacy-overlay-core.swift",
        "mac-privacy-overlay.swift",
        "mac-privacy-overlay-Info.plist",
        "build-mac-privacy-overlay.sh",
    ):
        shutil.copy2(Path("resources") / name, tmp_path / name)

    app = tmp_path / "runtime" / "helpers" / "OpenChroniclePrivacyOverlay.app"
    result = subprocess.run(
        ["bash", str(tmp_path / "build-mac-privacy-overlay.sh")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "CLANG_MODULE_CACHE_PATH": str(tmp_path / "module-cache"),
            "OPENCHRONICLE_PRIVACY_OVERLAY_ARCH": arch,
            "OPENCHRONICLE_PRIVACY_OVERLAY_APP_DIR": str(app),
        },
    )
    assert result.returncode == 0, result.stderr

    bare = tmp_path / "mac-privacy-overlay"
    executable = app / "Contents" / "MacOS" / "mac-privacy-overlay"
    assert bare.is_file() and bare.stat().st_mode & stat.S_IXUSR
    assert executable.is_file() and executable.stat().st_mode & stat.S_IXUSR
    with (app / "Contents" / "Info.plist").open("rb") as handle:
        info = plistlib.load(handle)
    assert info["CFBundleIdentifier"] == "com.openchronicle.privacy-overlay"
    assert info["CFBundleExecutable"] == "mac-privacy-overlay"
    architecture = subprocess.run(
        ["file", str(executable)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert arch in architecture.stdout

    verification = subprocess.run(
        ["codesign", "--verify", "--deep", "--strict", str(app)],
        capture_output=True,
        text=True,
    )
    assert verification.returncode == 0, verification.stderr


@pytest.mark.skipif(platform.system() != "Darwin", reason="requires macOS codesign and Swift SDK")
def test_privacy_overlay_publish_failure_restores_previous_app(tmp_path: Path) -> None:
    for name in (
        "mac-privacy-overlay-reason.swift",
        "mac-privacy-overlay-core.swift",
        "mac-privacy-overlay.swift",
        "mac-privacy-overlay-Info.plist",
        "build-mac-privacy-overlay.sh",
    ):
        shutil.copy2(Path("resources") / name, tmp_path / name)

    app = tmp_path / "runtime" / "helpers" / "OpenChroniclePrivacyOverlay.app"
    env = {
        **os.environ,
        "CLANG_MODULE_CACHE_PATH": str(tmp_path / "module-cache"),
        "OPENCHRONICLE_PRIVACY_OVERLAY_APP_DIR": str(app),
    }
    initial = subprocess.run(
        ["bash", str(tmp_path / "build-mac-privacy-overlay.sh")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=env,
    )
    assert initial.returncode == 0, initial.stderr
    executable = app / "Contents" / "MacOS" / "mac-privacy-overlay"
    previous = executable.read_bytes()

    with (tmp_path / "mac-privacy-overlay-core.swift").open("a") as handle:
        handle.write("\n")
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_codesign = fake_bin / "codesign"
    fake_codesign.write_text(
        "#!/bin/sh\n"
        "last=\n"
        "for arg in \"$@\"; do last=$arg; done\n"
        "if [ \"$1\" = \"--force\" ] && [ \"$last\" = \"$FAIL_APP\" ]; then exit 9; fi\n"
        "exit 0\n"
    )
    fake_codesign.chmod(0o755)

    failed = subprocess.run(
        ["bash", str(tmp_path / "build-mac-privacy-overlay.sh")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env={
            **env,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "FAIL_APP": str(app),
        },
    )
    assert failed.returncode != 0
    assert executable.read_bytes() == previous
    verification = subprocess.run(
        ["codesign", "--verify", "--deep", "--strict", str(app)],
        capture_output=True,
        text=True,
    )
    assert verification.returncode == 0, verification.stderr


@pytest.mark.skipif(platform.system() != "Darwin", reason="requires macOS shell behavior")
def test_privacy_overlay_concurrent_builds_publish_one_complete_bundle(tmp_path: Path) -> None:
    for name in (
        "mac-privacy-overlay-reason.swift",
        "mac-privacy-overlay-core.swift",
        "mac-privacy-overlay.swift",
        "mac-privacy-overlay-Info.plist",
        "build-mac-privacy-overlay.sh",
    ):
        shutil.copy2(Path("resources") / name, tmp_path / name)

    app = tmp_path / "runtime" / "helpers" / "OpenChroniclePrivacyOverlay.app"
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_swiftc = fake_bin / "swiftc"
    fake_swiftc.write_text(
        "#!/bin/sh\n"
        "out=\n"
        "while [ \"$#\" -gt 0 ]; do\n"
        "  if [ \"$1\" = \"-o\" ]; then out=$2; shift 2; continue; fi\n"
        "  shift\n"
        "done\n"
        "sleep 0.2\n"
        "printf '#!/bin/sh\\nexit 0\\n' > \"$out\"\n"
        "chmod +x \"$out\"\n"
    )
    fake_swiftc.chmod(0o755)
    for name in ("codesign", "xattr"):
        helper = fake_bin / name
        helper.write_text("#!/bin/sh\nexit 0\n")
        helper.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "CLANG_MODULE_CACHE_PATH": str(tmp_path / "module-cache"),
        "OPENCHRONICLE_PRIVACY_OVERLAY_APP_DIR": str(app),
    }

    processes = [
        subprocess.Popen(
            ["bash", str(tmp_path / "build-mac-privacy-overlay.sh")],
            cwd=tmp_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        for _ in range(2)
    ]
    results = [process.communicate(timeout=10) for process in processes]
    assert [process.returncode for process in processes] == [0, 0], results
    executable = app / "Contents" / "MacOS" / "mac-privacy-overlay"
    assert executable.read_text() == "#!/bin/sh\nexit 0\n"
    assert not (app.parent / ".privacy-overlay-build.lock").exists()


@pytest.mark.skipif(platform.system() != "Darwin", reason="requires macOS shell behavior")
def test_privacy_overlay_mktemp_failure_releases_build_lock(tmp_path: Path) -> None:
    for name in (
        "mac-privacy-overlay-reason.swift",
        "mac-privacy-overlay-core.swift",
        "mac-privacy-overlay.swift",
        "mac-privacy-overlay-Info.plist",
        "build-mac-privacy-overlay.sh",
    ):
        shutil.copy2(Path("resources") / name, tmp_path / name)
    app = tmp_path / "runtime" / "helpers" / "OpenChroniclePrivacyOverlay.app"
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_mktemp = fake_bin / "mktemp"
    fake_mktemp.write_text("#!/bin/sh\nexit 7\n")
    fake_mktemp.chmod(0o755)

    result = subprocess.run(
        ["bash", str(tmp_path / "build-mac-privacy-overlay.sh")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "OPENCHRONICLE_PRIVACY_OVERLAY_APP_DIR": str(app),
        },
    )
    assert result.returncode != 0
    assert not (app.parent / ".privacy-overlay-build.lock").exists()


@pytest.mark.skipif(platform.system() != "Darwin", reason="requires macOS codesign and Swift SDK")
def test_privacy_overlay_failed_restore_retains_previous_backup(tmp_path: Path) -> None:
    for name in (
        "mac-privacy-overlay-reason.swift",
        "mac-privacy-overlay-core.swift",
        "mac-privacy-overlay.swift",
        "mac-privacy-overlay-Info.plist",
        "build-mac-privacy-overlay.sh",
    ):
        shutil.copy2(Path("resources") / name, tmp_path / name)
    app = tmp_path / "runtime" / "helpers" / "OpenChroniclePrivacyOverlay.app"
    env = {
        **os.environ,
        "CLANG_MODULE_CACHE_PATH": str(tmp_path / "module-cache"),
        "OPENCHRONICLE_PRIVACY_OVERLAY_APP_DIR": str(app),
    }
    initial = subprocess.run(
        ["bash", str(tmp_path / "build-mac-privacy-overlay.sh")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=env,
    )
    assert initial.returncode == 0, initial.stderr
    executable = app / "Contents" / "MacOS" / "mac-privacy-overlay"
    previous = executable.read_bytes()
    with (tmp_path / "mac-privacy-overlay-core.swift").open("a") as handle:
        handle.write("\n")

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_mv = fake_bin / "mv"
    fake_mv.write_text(
        "#!/bin/sh\n"
        "last=\n"
        "for arg in \"$@\"; do last=$arg; done\n"
        "if [ \"$last\" = \"$FAIL_APP\" ]; then exit 8; fi\n"
        "exec /bin/mv \"$@\"\n"
    )
    fake_mv.chmod(0o755)
    for name in ("codesign", "xattr"):
        helper = fake_bin / name
        helper.write_text("#!/bin/sh\nexit 0\n")
        helper.chmod(0o755)

    failed = subprocess.run(
        ["bash", str(tmp_path / "build-mac-privacy-overlay.sh")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env={
            **env,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "FAIL_APP": str(app),
        },
    )
    assert failed.returncode != 0
    backups = list(app.parent.glob(".OpenChroniclePrivacyOverlay.backup.*"))
    assert len(backups) == 1
    backup_executable = backups[0] / "Contents" / "MacOS" / "mac-privacy-overlay"
    assert backup_executable.read_bytes() == previous


@pytest.mark.skipif(platform.system() != "Darwin", reason="requires macOS shell behavior")
def test_privacy_overlay_cleanup_failure_preserves_success_status(tmp_path: Path) -> None:
    for name in (
        "mac-privacy-overlay-reason.swift",
        "mac-privacy-overlay-core.swift",
        "mac-privacy-overlay.swift",
        "mac-privacy-overlay-Info.plist",
        "build-mac-privacy-overlay.sh",
    ):
        shutil.copy2(Path("resources") / name, tmp_path / name)
    app = tmp_path / "runtime" / "helpers" / "OpenChroniclePrivacyOverlay.app"
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_swiftc = fake_bin / "swiftc"
    fake_swiftc.write_text(
        "#!/bin/sh\n"
        "out=\n"
        "while [ \"$#\" -gt 0 ]; do\n"
        "  if [ \"$1\" = \"-o\" ]; then out=$2; shift 2; continue; fi\n"
        "  shift\n"
        "done\n"
        "printf '#!/bin/sh\\nexit 0\\n' > \"$out\"\n"
        "chmod +x \"$out\"\n"
    )
    fake_swiftc.chmod(0o755)
    for name in ("codesign", "xattr"):
        helper = fake_bin / name
        helper.write_text("#!/bin/sh\nexit 0\n")
        helper.chmod(0o755)
    fake_rm = fake_bin / "rm"
    fake_rm.write_text(
        "#!/bin/sh\n"
        "case \"$*\" in\n"
        "  *privacy-overlay-build.lock*) exec /bin/rm \"$@\" ;;\n"
        "  *) exit 9 ;;\n"
        "esac\n"
    )
    fake_rm.chmod(0o755)

    result = subprocess.run(
        ["bash", str(tmp_path / "build-mac-privacy-overlay.sh")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "OPENCHRONICLE_PRIVACY_OVERLAY_APP_DIR": str(app),
        },
    )
    assert result.returncode == 0, result.stderr
    assert (app / "Contents" / "MacOS" / "mac-privacy-overlay").is_file()


@pytest.mark.parametrize(
    "magic",
    (
        b"\xfe\xed\xfa\xce",
        b"\xfe\xed\xfa\xcf",
        b"\xce\xfa\xed\xfe",
        b"\xcf\xfa\xed\xfe",
        b"\xca\xfe\xba\xbe",
        b"\xbe\xba\xfe\xca",
        b"\xca\xfe\xba\xbf",
        b"\xbf\xba\xfe\xca",
    ),
)
def test_archive_mach_o_scanner_detects_thin_and_fat_magic(tmp_path: Path, magic: bytes) -> None:
    archive_path = tmp_path / "archive.tar.gz"
    member = tarfile.TarInfo("artifact")
    member.size = len(magic)
    with tarfile.open(archive_path, "w:gz") as archive:
        archive.addfile(member, io.BytesIO(magic))

    with tarfile.open(archive_path) as archive:
        assert _archive_mach_o_entries(archive) == ["artifact"]


def test_sdist_excludes_generated_macos_artifacts_and_mach_o(tmp_path: Path) -> None:
    project = tmp_path / "project"
    resources = project / "resources"
    package = project / "src" / "openchronicle"
    resources.mkdir(parents=True)
    package.mkdir(parents=True)
    shutil.copy2("pyproject.toml", project / "pyproject.toml")
    shutil.copy2("README.md", project / "README.md")
    shutil.copy2("src/openchronicle/__init__.py", package / "__init__.py")

    source_names = (
        "mac-ax-helper.swift",
        "build-mac-ax-helper.sh",
        "mac-ax-watcher.swift",
        "build-mac-ax-watcher.sh",
        "mac-window-list.swift",
        "mac-window-list-core.swift",
        "build-mac-window-list.sh",
        "mac-privacy-overlay-core.swift",
        "mac-privacy-overlay-reason.swift",
        "mac-privacy-overlay.swift",
        "mac-privacy-overlay-Info.plist",
        "build-mac-privacy-overlay.sh",
        "mac-screen-capture-core.swift",
        "mac-screen-capture.swift",
        "build-mac-screen-capture.sh",
    )
    for name in source_names:
        shutil.copy2(Path("resources") / name, resources / name)

    sentinels = {
        resources / "mac-window-list": b"\xfe\xed\xfa\xcewindow-list",
        resources / "mac-privacy-overlay": b"\xfe\xed\xfa\xcfprivacy-overlay",
        resources / "OpenChroniclePrivacyOverlay.app" / "Contents" / "MacOS"
        / "mac-privacy-overlay": b"\xcf\xfa\xed\feprivacy-overlay-app",
        resources / "mac-screen-capture": b"\xce\xfa\xed\xfescreen-capture",
        project / "macos" / "OpenChronicleApp" / ".build" / "fat-sentinel": b"\xca\xfe\xba\xbe",
        project / ".build" / "macos-app" / "fat-sentinel": b"\xca\xfe\xba\xbe",
    }
    for path, contents in sentinels.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)

    dist = project / "dist"
    result = subprocess.run(
        ["uv", "build", "--sdist", "--offline", "--out-dir", str(dist)],
        cwd=project,
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "UV_OFFLINE": "1", "UV_NO_PROGRESS": "1"},
    )
    assert result.returncode == 0, result.stderr

    archive_path = next(dist.glob("*.tar.gz"))
    with tarfile.open(archive_path) as archive:
        members = [member for member in archive.getmembers() if member.isfile()]
        names = {member.name.split("/", 1)[1] for member in members}
        assert {f"resources/{name}" for name in source_names} <= names
        assert not any(name in names for name in (
            "resources/mac-window-list",
            "resources/mac-privacy-overlay",
            "resources/mac-screen-capture",
        ))
        assert not any(name.startswith("resources/OpenChroniclePrivacyOverlay.app/") for name in names)
        assert not any(name.startswith("macos/OpenChronicleApp/.build/") for name in names)
        assert not any(name.startswith(".build/") for name in names)
        assert not _archive_mach_o_entries(archive)


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
