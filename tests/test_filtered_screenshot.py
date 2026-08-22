from __future__ import annotations

import base64
import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from openchronicle.capture import screenshot
from openchronicle.capture.privacy import DisplayInfo, ScreenRegion


def _display(
    display_id: int,
    left: float,
    top: float,
    width: float,
    height: float,
    *,
    primary: bool = False,
) -> DisplayInfo:
    return DisplayInfo(display_id, ScreenRegion(left, top, width, height), primary)


def _png(width: int, height: int, color: tuple[int, int, int]) -> str:
    image = Image.new("RGB", (width, height), color)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return base64.b64encode(output.getvalue()).decode("ascii")


def _response(
    displays: tuple[DisplayInfo, ...],
    colors: tuple[tuple[int, int, int], ...],
    *,
    scales: tuple[int, ...] | None = None,
    sizes: tuple[tuple[int, int], ...] | None = None,
) -> dict[str, object]:
    scales = scales or (1,) * len(displays)
    sizes = sizes or tuple(
        (int(display.region.width * scale), int(display.region.height * scale))
        for display, scale in zip(displays, scales, strict=True)
    )
    return {
        "version": 1,
        "status": "ok",
        "displays": [
            {
                "id": display.id,
                "left": display.region.left,
                "top": display.region.top,
                "point_width": display.region.width,
                "point_height": display.region.height,
                "pixel_width": width,
                "pixel_height": height,
                "png_base64": _png(
                    width,
                    height,
                    color,
                ),
            }
            for display, color, scale, (width, height) in zip(
                displays, colors, scales, sizes, strict=True
            )
        ],
    }


def _decoded(shot: screenshot.Screenshot) -> Image.Image:
    return Image.open(io.BytesIO(base64.b64decode(shot.image_base64))).convert("RGB")


def _install_helper(
    monkeypatch: pytest.MonkeyPatch,
    response: dict[str, object],
    *,
    stderr: str = "",
    returncode: int = 0,
    stdout_suffix: str = "\n",
) -> dict[str, object]:
    request: dict[str, object] = {}

    def run(_args, **kwargs):
        request.update(json.loads(kwargs["input_data"]))
        return screenshot._BoundedProcessResult(
            returncode=returncode,
            stdout=(json.dumps(response) + stdout_suffix).encode(),
            stderr=stderr.encode(),
        )

    monkeypatch.setattr(screenshot, "_resolve_filtered_capture_helper", lambda: "/helper")
    monkeypatch.setattr(screenshot, "_run_bounded_process", run)
    return request


def _grab(
    *,
    monitor_mode: str,
    displays: tuple[DisplayInfo, ...],
    privacy_mode: str = "exclude-window",
    regions: tuple[ScreenRegion, ...] = (ScreenRegion(2, 2, 3, 3),),
    protected_ids: object = (71,),
    overlay_ids: object = (),
    max_width: int = 0,
) -> list[screenshot.Screenshot] | None:
    return screenshot.grab_filtered_many(
        monitor_mode=monitor_mode,
        privacy_mode=privacy_mode,
        displays=displays,
        protected_window_ids=protected_ids,
        protected_window_regions=regions,
        overlay_window_ids=overlay_ids,
        max_width=max_width,
        jpeg_quality=100,
    )


def test_filtered_helper_decodes_png_and_sends_only_authorized_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    displays = (_display(31, 0, 0, 10, 8, primary=True),)
    request = _install_helper(monkeypatch, _response(displays, ((220, 10, 20),)))

    shots = _grab(
        monitor_mode="separate",
        displays=displays,
        protected_ids=frozenset({71}),
        overlay_ids=frozenset({82}),
    )

    assert shots is not None
    assert len(shots) == 1
    assert shots[0].mime_type == "image/jpeg"
    assert shots[0].width == 10
    assert shots[0].height == 8
    assert shots[0].monitor_index == 1
    assert _decoded(shots[0]).getpixel((0, 0))[0] > 180
    assert request == {
        "version": 1,
        "displays": [{"id": 31, "width": 10, "height": 8}],
        "protected_window_ids": [71],
        "overlay_window_ids": [82],
    }


def test_filtered_capture_uses_exact_primary_target_size_in_request_and_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    displays = (_display(31, 0, 0, 9.5, 5.5, primary=True),)
    request = _install_helper(
        monkeypatch,
        _response(displays, ((220, 10, 20),), sizes=((4, 2),)),
    )

    shots = _grab(monitor_mode="primary", displays=displays, max_width=4)

    assert shots is not None
    assert (shots[0].width, shots[0].height) == (4, 2)
    assert request["displays"] == [{"id": 31, "width": 4, "height": 2}]


def test_filtered_capture_rejects_response_not_matching_exact_target_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    displays = (_display(31, 0, 0, 10, 5, primary=True),)
    _install_helper(
        monkeypatch,
        _response(displays, ((220, 10, 20),), sizes=((6, 3),)),
    )

    assert _grab(monitor_mode="primary", displays=displays, max_width=4) is None


def test_mask_window_covers_only_protected_intersection_after_source_filtering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    displays = (_display(31, 0, 0, 10, 8, primary=True),)
    _install_helper(monkeypatch, _response(displays, ((220, 10, 20),)))

    shots = _grab(
        monitor_mode="primary",
        displays=displays,
        privacy_mode="mask-window",
        regions=(ScreenRegion(-2, 2, 6, 3),),
    )

    assert shots is not None
    image = _decoded(shots[0])
    masked = image.getpixel((1, 3))
    assert max(masked) - min(masked) < 35
    assert masked[0] in range(110, 160)
    assert image.getpixel((5, 3))[0] > 180
    assert image.getpixel((1, 1))[0] > 180


def test_exclude_window_does_not_draw_a_python_mask(monkeypatch: pytest.MonkeyPatch) -> None:
    displays = (_display(31, 0, 0, 10, 8, primary=True),)
    _install_helper(monkeypatch, _response(displays, ((220, 10, 20),)))

    shots = _grab(
        monitor_mode="primary",
        displays=displays,
        privacy_mode="exclude-window",
        regions=(ScreenRegion(2, 2, 3, 3),),
    )

    assert shots is not None
    assert _decoded(shots[0]).getpixel((3, 3))[0] > 180


def test_primary_and_separate_follow_display_inventory_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    displays = (
        _display(31, 100, 0, 10, 8),
        _display(12, 0, 0, 10, 8, primary=True),
    )
    request = _install_helper(monkeypatch, _response((displays[1],), ((10, 220, 20),)))

    primary = _grab(monitor_mode="primary", displays=displays)
    assert primary is not None
    assert len(primary) == 1
    assert primary[0].monitor_index == 2
    assert _decoded(primary[0]).getpixel((0, 0))[1] > 180
    assert request["displays"] == [{"id": 12, "width": 10, "height": 8}]

    request = _install_helper(
        monkeypatch,
        _response(displays, ((220, 10, 20), (10, 220, 20))),
    )
    separate = _grab(monitor_mode="separate", displays=displays)
    assert separate is not None
    assert [shot.monitor_index for shot in separate] == [1, 2]
    assert [_decoded(shot).getpixel((0, 0))[1] > 180 for shot in separate] == [False, True]
    assert request["displays"] == [
        {"id": 31, "width": 10, "height": 8},
        {"id": 12, "width": 10, "height": 8},
    ]


def test_all_stitches_global_bounds_with_negative_vertical_displays_and_scaling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    displays = (
        _display(31, -10, 0, 10, 10, primary=True),
        _display(12, 0, -10, 10, 10),
        _display(7, 0, 0, 10, 10),
    )
    request = _install_helper(
        monkeypatch,
        _response(
            displays,
            ((220, 10, 20), (10, 220, 20), (10, 20, 220)),
            sizes=((5, 5), (5, 5), (5, 5)),
        ),
    )

    shots = _grab(
        monitor_mode="all",
        displays=displays,
        privacy_mode="mask-window",
        regions=(ScreenRegion(-2, -2, 4, 4),),
        max_width=10,
    )

    assert shots is not None
    assert len(shots) == 1
    shot = shots[0]
    assert (shot.width, shot.height) == (10, 10)
    assert (shot.monitor_left, shot.monitor_top, shot.monitor_width, shot.monitor_height) == (-10, -10, 20, 20)
    assert shot.monitor_is_all is True
    assert request["displays"] == [
        {"id": 31, "width": 5, "height": 5},
        {"id": 12, "width": 5, "height": 5},
        {"id": 7, "width": 5, "height": 5},
    ]
    image = _decoded(shot)
    assert image.getpixel((1, 6))[0] > 180
    assert image.getpixel((6, 1))[1] > 180
    assert image.getpixel((7, 7))[2] > 180
    assert image.getpixel((5, 5))[0] in range(105, 160)


def test_all_uses_common_fractional_layout_for_mixed_scale_gap_and_cross_display_mask(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    displays = (
        _display(31, -5.5, 0, 5.5, 4, primary=True),
        _display(12, 2.5, 1.2, 7.5, 4),
    )
    request = _install_helper(
        monkeypatch,
        _response(displays, ((220, 10, 20), (10, 220, 20)), sizes=((4, 3), (5, 2))),
    )

    shots = _grab(
        monitor_mode="all",
        displays=displays,
        privacy_mode="mask-window",
        regions=(ScreenRegion(-1, 0.5, 5, 2.5),),
        max_width=10,
    )

    assert shots is not None
    assert (shots[0].width, shots[0].height) == (10, 3)
    assert request["displays"] == [
        {"id": 31, "width": 4, "height": 3},
        {"id": 12, "width": 5, "height": 2},
    ]
    image = _decoded(shots[0])
    assert image.getpixel((4, 1))[0] in range(105, 160)
    assert image.getpixel((6, 1))[1] < 200
    assert image.getpixel((2, 0))[0] > 150


@pytest.mark.parametrize(
    ("mutate", "stdout_suffix"),
    [
        (lambda payload: payload["displays"].pop(), "\n"),
        (lambda payload: payload["displays"].append(payload["displays"][0].copy()), "\n"),
        (
            lambda payload: payload["displays"].append(
                {**payload["displays"][0], "id": 99}
            ),
            "\n",
        ),
        (lambda payload: payload["displays"][0].__setitem__("left", 9), "\n"),
        (lambda payload: payload["displays"][0].__setitem__("png_base64", "not-base64"), "\n"),
        (lambda payload: payload["displays"][0].__setitem__("png_base64", base64.b64encode(b"not png").decode()), "\n"),
        (lambda payload: payload["displays"][0].__setitem__("pixel_width", 9), "\n"),
        (lambda payload: payload.__setitem__("unexpected", True), "\n"),
        (lambda payload: payload.__setitem__("version", True), "\n"),
        (lambda payload: payload, "\nextra"),
    ],
)
def test_filtered_capture_rejects_incomplete_or_malformed_helper_success(
    monkeypatch: pytest.MonkeyPatch,
    mutate,
    stdout_suffix: str,
) -> None:
    displays = (_display(31, 0, 0, 10, 8, primary=True),)
    response = _response(displays, ((220, 10, 20),))
    mutate(response)
    _install_helper(monkeypatch, response, stdout_suffix=stdout_suffix)

    assert _grab(monitor_mode="primary", displays=displays) is None


@pytest.mark.parametrize(
    ("stderr", "returncode", "response"),
    [
        ("private helper diagnostics", 0, {"version": 1, "status": "ok", "displays": []}),
        ("", 7, {"version": 1, "status": "ok", "displays": []}),
        ("", 0, {"version": 1, "status": "error", "error": "capture_failed"}),
    ],
)
def test_filtered_capture_rejects_helper_stderr_exit_or_error_status(
    monkeypatch: pytest.MonkeyPatch,
    stderr: str,
    returncode: int,
    response: dict[str, object],
) -> None:
    displays = (_display(31, 0, 0, 10, 8, primary=True),)
    _install_helper(monkeypatch, response, stderr=stderr, returncode=returncode)

    assert _grab(monitor_mode="primary", displays=displays) is None


def test_filtered_capture_rejects_helper_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    displays = (_display(31, 0, 0, 10, 8, primary=True),)
    monkeypatch.setattr(screenshot, "_resolve_filtered_capture_helper", lambda: "/helper")
    monkeypatch.setattr(
        screenshot,
        "_run_bounded_process",
        lambda *_args, **_kwargs: None,
    )

    assert _grab(monitor_mode="primary", displays=displays) is None


def test_python_and_swift_capture_resource_limits_are_identical() -> None:
    expected = {
        "maxCommandBytes": screenshot._FILTERED_MAX_COMMAND_BYTES,
        "maxDisplayCount": screenshot._FILTERED_MAX_DISPLAY_COUNT,
        "maxDimension": screenshot._MAX_CAPTURE_DIMENSION,
        "maxAggregatePixels": screenshot._MAX_CAPTURE_PIXELS,
        "maxPNGBytes": screenshot._FILTERED_MAX_PNG_BYTES,
        "maxAggregatePNGBytes": screenshot._FILTERED_MAX_AGGREGATE_PNG_BYTES,
        "maxResponseBytes": screenshot._FILTERED_MAX_RESPONSE_BYTES,
        "maxStderrBytes": screenshot._FILTERED_MAX_STDERR_BYTES,
    }
    assert expected == {
        "maxCommandBytes": 65_536,
        "maxDisplayCount": 16,
        "maxDimension": 16_384,
        "maxAggregatePixels": 128_000_000,
        "maxPNGBytes": 67_108_864,
        "maxAggregatePNGBytes": 134_217_728,
        "maxResponseBytes": 188_743_680,
        "maxStderrBytes": 65_536,
    }

    swift_source = (
        Path(__file__).resolve().parents[1]
        / "resources"
        / "mac-screen-capture-core.swift"
    ).read_text()
    for name, value in expected.items():
        assert f"static let {name} = {value:_}" in swift_source


def test_filtered_capture_rejects_display_count_and_aggregate_pixels_before_launch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    maximum = tuple(
        _display(index + 1, index * 10, 0, 10, 10, primary=index == 0)
        for index in range(screenshot._FILTERED_MAX_DISPLAY_COUNT)
    )
    assert screenshot._validated_displays(maximum) == maximum
    assert screenshot._validated_displays(
        maximum + (_display(99, 200, 0, 10, 10),)
    ) is None

    exact = (
        screenshot._FilteredCaptureTarget(maximum[0], 10_000, 10_000),
        screenshot._FilteredCaptureTarget(maximum[1], 10_000, 2_800),
    )
    assert screenshot._capture_targets_are_within_limits(exact)
    assert not screenshot._capture_targets_are_within_limits(
        exact + (screenshot._FilteredCaptureTarget(maximum[2], 1, 1),)
    )
    monkeypatch.setattr(
        screenshot,
        "_resolve_filtered_capture_helper",
        lambda: pytest.fail("helper must not launch"),
    )
    assert _grab(monitor_mode="separate", displays=maximum + (maximum[0],)) is None


def test_filtered_request_bytes_are_bounded_before_helper_launch() -> None:
    display = _display(31, 0, 0, 10, 8, primary=True)
    target = screenshot._FilteredCaptureTarget(display, 10, 8)
    assert screenshot._filtered_capture_request_bytes((target,), (71,), (82,)) is not None
    assert screenshot._filtered_capture_request_bytes(
        (target,),
        tuple(range(1, 20_000)),
        (),
    ) is None


def test_bounded_transport_streams_real_stdout_and_stderr() -> None:
    result = screenshot._run_bounded_process(
        [
            sys.executable,
            "-c",
            (
                "import sys,time; "
                "sys.stdin.buffer.read(); "
                "sys.stdout.buffer.write(b'ab'); sys.stdout.flush(); "
                "time.sleep(0.02); "
                "sys.stdout.buffer.write(b'cd'); "
                "sys.stderr.buffer.write(b'warn')"
            ),
        ],
        input_data=b"request",
        timeout=2,
        max_stdout_bytes=4,
        max_stderr_bytes=4,
    )

    assert result == screenshot._BoundedProcessResult(
        returncode=0,
        stdout=b"abcd",
        stderr=b"warn",
    )


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_bounded_transport_kills_and_reaps_on_stream_overflow(stream: str) -> None:
    writer = "sys.stdout" if stream == "stdout" else "sys.stderr"
    result = screenshot._run_bounded_process(
        [
            sys.executable,
            "-c",
            f"import sys,time; {writer}.write('x' * 65); {writer}.flush(); time.sleep(5)",
        ],
        input_data=b"",
        timeout=2,
        max_stdout_bytes=64,
        max_stderr_bytes=64,
    )

    assert result is None


def test_bounded_transport_timeout_rejects_partial_output() -> None:
    started = time.monotonic()
    result = screenshot._run_bounded_process(
        [
            sys.executable,
            "-c",
            "import sys,time; sys.stdout.write('{\"partial\":'); sys.stdout.flush(); time.sleep(5)",
        ],
        input_data=b"",
        timeout=0.05,
        max_stdout_bytes=1_024,
        max_stderr_bytes=1_024,
    )

    assert result is None
    assert time.monotonic() - started < 2


def test_bounded_transport_overflow_kills_helper_process_group(tmp_path: Path) -> None:
    orphan_marker = tmp_path / "orphan-survived"
    child_code = (
        "import pathlib,time; time.sleep(0.4); "
        f"pathlib.Path({str(orphan_marker)!r}).write_text('alive')"
    )
    parent_code = (
        "import subprocess,sys,time; "
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
        "sys.stdout.write('x' * 4096); sys.stdout.flush(); time.sleep(5)"
    )

    assert screenshot._run_bounded_process(
        [sys.executable, "-c", parent_code],
        input_data=b"",
        timeout=2,
        max_stdout_bytes=64,
        max_stderr_bytes=64,
    ) is None
    time.sleep(0.7)
    assert not orphan_marker.exists()


def test_real_helper_transport_wiring_decodes_complete_streamed_response(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    displays = (_display(31, 0, 0, 10, 8, primary=True),)
    payload = json.dumps(_response(displays, ((220, 10, 20),)))
    helper = _executable(
        tmp_path / "helper",
        "#!/usr/bin/env python3\n"
        "import sys, time\n"
        "sys.stdin.buffer.readline()\n"
        f"payload = {payload!r}\n"
        "middle = len(payload) // 2\n"
        "sys.stdout.write(payload[:middle]); sys.stdout.flush()\n"
        "time.sleep(0.02)\n"
        "sys.stdout.write(payload[middle:] + '\\n'); sys.stdout.flush()\n",
    )
    monkeypatch.setattr(screenshot, "_resolve_filtered_capture_helper", lambda: helper)

    shots = _grab(monitor_mode="primary", displays=displays)

    assert shots is not None
    assert (shots[0].width, shots[0].height) == (10, 8)


def test_png_header_dimensions_are_checked_before_pillow_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    display = _display(31, 0, 0, 2, 2, primary=True)
    target = screenshot._FilteredCaptureTarget(display, 2, 2)
    row = _response((display,), ((220, 10, 20),), sizes=((3, 2),))["displays"][0]
    row["pixel_width"] = 2
    row["pixel_height"] = 2
    monkeypatch.setattr(
        Image,
        "open",
        lambda *_args, **_kwargs: pytest.fail("Pillow must not open mismatched IHDR dimensions"),
    )

    assert screenshot._decode_filtered_display(row, target, Image) is None


@pytest.mark.parametrize("max_pixels", [3, 1], ids=["warning", "error"])
def test_filtered_png_decompression_bomb_warning_and_error_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    max_pixels: int,
) -> None:
    display = _display(31, 0, 0, 2, 2, primary=True)
    target = screenshot._FilteredCaptureTarget(display, 2, 2)
    row = _response((display,), ((220, 10, 20),), sizes=((2, 2),))["displays"][0]
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", max_pixels)

    assert screenshot._decode_filtered_display(row, target, Image) is None


def test_base64_and_decoded_png_byte_limits_fail_before_decode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    display = _display(31, 0, 0, 2, 2, primary=True)
    target = screenshot._FilteredCaptureTarget(display, 2, 2)
    row = _response((display,), ((220, 10, 20),), sizes=((2, 2),))["displays"][0]
    monkeypatch.setattr(screenshot, "_FILTERED_MAX_PNG_BYTES", 8)
    row["png_base64"] = "A" * 13
    monkeypatch.setattr(
        screenshot.base64,
        "b64decode",
        lambda *_args, **_kwargs: pytest.fail("oversized base64 must be rejected before decode"),
    )

    assert screenshot._decode_filtered_display(row, target, Image) is None


def test_helper_sources_wire_application_exclusion_double_snapshot_and_cg_id_mapping() -> None:
    root = Path(__file__).resolve().parents[1]
    capture_source = (root / "resources" / "mac-screen-capture.swift").read_text()
    window_source = (root / "resources" / "mac-window-list.swift").read_text()

    assert "excludingApplications:" in capture_source
    assert "exceptingWindows: []" in capture_source
    assert "excludingWindows:" not in capture_source
    assert capture_source.count("loadShareableContent()") >= 2
    assert "currentFingerprint:" in capture_source
    assert "window_id: windowRecordID(from: source.metadata)" in window_source


@pytest.mark.parametrize(
    ("protected_ids", "regions", "overlay_ids"),
    [
        ((), (ScreenRegion(0, 0, 1, 1),), ()),
        ((0,), (ScreenRegion(0, 0, 1, 1),), ()),
        ((True,), (ScreenRegion(0, 0, 1, 1),), ()),
        ((71, 71), (ScreenRegion(0, 0, 1, 1), ScreenRegion(2, 2, 1, 1)), ()),
        ((71,), (), ()),
        ((71,), (ScreenRegion(0, 0, 1, 1),), (71,)),
        ((71,), (ScreenRegion(0, 0, 1, 1),), (0x1_0000_0000,)),
    ],
)
def test_filtered_capture_rejects_invalid_window_authorization_before_launch(
    monkeypatch: pytest.MonkeyPatch,
    protected_ids: object,
    regions: tuple[ScreenRegion, ...],
    overlay_ids: object,
) -> None:
    displays = (_display(31, 0, 0, 10, 8, primary=True),)
    monkeypatch.setattr(
        screenshot,
        "_resolve_filtered_capture_helper",
        lambda: pytest.fail("helper must not launch"),
    )

    assert (
        _grab(
            monitor_mode="primary",
            displays=displays,
            protected_ids=protected_ids,
            regions=regions,
            overlay_ids=overlay_ids,
        )
        is None
    )


@pytest.mark.parametrize(
    "displays",
    [
        (),
        (_display(31, 0, 0, 10, 8),),
        (_display(31, 0, 0, 10, 8, primary=True), _display(31, 10, 0, 10, 8)),
        (_display(31, 0, 0, 0, 8, primary=True),),
    ],
)
def test_filtered_capture_rejects_invalid_display_inventory_before_launch(
    monkeypatch: pytest.MonkeyPatch,
    displays: tuple[DisplayInfo, ...],
) -> None:
    monkeypatch.setattr(
        screenshot,
        "_resolve_filtered_capture_helper",
        lambda: pytest.fail("helper must not launch"),
    )

    assert _grab(monitor_mode="primary", displays=displays) is None


def _executable(path: Path, content: str = "#!/bin/sh\nexit 0\n") -> Path:
    path.write_text(content)
    path.chmod(0o755)
    return path


def test_filtered_helper_resolver_prefers_override_then_wheel_then_source(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    override = _executable(tmp_path / "override")
    wheel = tmp_path / "wheel"
    source = tmp_path / "source"
    wheel.mkdir()
    source.mkdir()
    wheel_helper = _executable(wheel / "mac-screen-capture")
    source_helper = _executable(source / "mac-screen-capture")
    monkeypatch.setattr(screenshot.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(screenshot, "_filtered_capture_helper_directories", lambda: (wheel, source))
    monkeypatch.setenv("OPENCHRONICLE_SCREEN_CAPTURE_HELPER", str(override))

    assert screenshot._resolve_filtered_capture_helper() == override.resolve()

    monkeypatch.delenv("OPENCHRONICLE_SCREEN_CAPTURE_HELPER")
    assert screenshot._resolve_filtered_capture_helper() == wheel_helper

    wheel_helper.unlink()
    assert screenshot._resolve_filtered_capture_helper() == source_helper


def test_filtered_helper_freshness_includes_build_script(tmp_path: Path) -> None:
    binary = _executable(tmp_path / "mac-screen-capture")
    main = (tmp_path / "mac-screen-capture.swift")
    core = (tmp_path / "mac-screen-capture-core.swift")
    build = _executable(tmp_path / "build-mac-screen-capture.sh")
    main.write_text("main")
    core.write_text("core")
    now = time.time()
    for path in (main, core, build):
        os.utime(path, (now - 10, now - 10))
    os.utime(binary, (now, now))

    assert screenshot._filtered_capture_helper_is_fresh(binary, main, core, build)

    os.utime(build, (now + 10, now + 10))
    assert not screenshot._filtered_capture_helper_is_fresh(binary, main, core, build)


@pytest.mark.parametrize(
    ("result", "raises"),
    [
        (SimpleNamespace(returncode=0, stderr="warning"), None),
        (SimpleNamespace(returncode=7, stderr=""), None),
        (None, subprocess.TimeoutExpired("build", 120)),
    ],
)
def test_filtered_helper_rebuild_failure_keeps_existing_binary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    result: SimpleNamespace | None,
    raises: BaseException | None,
) -> None:
    binary = _executable(tmp_path / "mac-screen-capture", "old binary")
    main = tmp_path / "mac-screen-capture.swift"
    core = tmp_path / "mac-screen-capture-core.swift"
    build = _executable(tmp_path / "build-mac-screen-capture.sh")
    main.write_text("main")
    core.write_text("core")
    now = time.time()
    os.utime(binary, (now - 20, now - 20))
    for path in (main, core, build):
        os.utime(path, (now, now))

    def run(*_args, **_kwargs):
        if raises is not None:
            raise raises
        return result

    monkeypatch.setattr(screenshot.subprocess, "run", run)

    assert screenshot._ensure_filtered_capture_helper(binary) is False
    assert binary.read_text() == "old binary"


def _script_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = Path(__file__).resolve().parents[1]
    script = tmp_path / "build-mac-screen-capture.sh"
    script.write_text((root / "resources" / "build-mac-screen-capture.sh").read_text())
    script.chmod(0o755)
    (tmp_path / "mac-screen-capture.swift").write_text("main")
    (tmp_path / "mac-screen-capture-core.swift").write_text("core")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _executable(
        fake_bin / "uname",
        "#!/bin/sh\nif [ \"$1\" = \"-s\" ]; then echo Darwin; else echo arm64; fi\n",
    )
    _executable(fake_bin / "xcrun", "#!/bin/sh\necho 14.0\n")
    return script, fake_bin, tmp_path / "mac-screen-capture"


def test_build_script_rejects_compiler_stderr_without_replacing_binary(tmp_path: Path) -> None:
    script, fake_bin, output = _script_fixture(tmp_path)
    _executable(output, "old binary")
    _executable(
        fake_bin / "swiftc",
        "#!/bin/sh\nout=\nwhile [ \"$#\" -gt 0 ]; do\n"
        "  if [ \"$1\" = \"-o\" ]; then out=$2; shift 2; continue; fi\n"
        "  shift\ndone\nprintf replacement > \"$out\"\necho warning >&2\nexit 0\n",
    )
    env = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"}

    result = subprocess.run(["/bin/bash", str(script)], cwd=tmp_path, env=env, capture_output=True, text=True)

    assert result.returncode != 0
    assert output.read_text() == "old binary"


def test_build_script_replaces_binary_atomically_under_concurrent_builds(tmp_path: Path) -> None:
    script, fake_bin, output = _script_fixture(tmp_path)
    _executable(output, "old binary")
    _executable(
        fake_bin / "swiftc",
        "#!/bin/sh\nout=\nwhile [ \"$#\" -gt 0 ]; do\n"
        "  if [ \"$1\" = \"-o\" ]; then out=$2; shift 2; continue; fi\n"
        "  shift\ndone\nprintf partial > \"$out\"\nsleep 0.1\nprintf -- '-%s' \"$$\" >> \"$out\"\nchmod +x \"$out\"\n",
    )
    env = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"}
    processes = [
        subprocess.Popen(["/bin/bash", str(script)], cwd=tmp_path, env=env)
        for _ in range(2)
    ]
    observed: set[str] = set()
    while any(process.poll() is None for process in processes):
        observed.add(output.read_text())
        time.sleep(0.01)
    assert [process.wait() for process in processes] == [0, 0]
    observed.add(output.read_text())

    assert observed <= {"old binary"} | {value for value in observed if value.startswith("partial-")}
    assert output.read_text().startswith("partial-")
