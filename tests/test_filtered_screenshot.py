from __future__ import annotations

import base64
import io
import json
import subprocess
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
) -> dict[str, object]:
    scales = scales or (1,) * len(displays)
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
                "pixel_width": int(display.region.width * scale),
                "pixel_height": int(display.region.height * scale),
                "png_base64": _png(
                    int(display.region.width * scale),
                    int(display.region.height * scale),
                    color,
                ),
            }
            for display, color, scale in zip(displays, colors, scales, strict=True)
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
        request.update(json.loads(kwargs["input"]))
        return SimpleNamespace(
            returncode=returncode,
            stdout=json.dumps(response) + stdout_suffix,
            stderr=stderr,
        )

    monkeypatch.setattr(screenshot, "_resolve_filtered_capture_helper", lambda: "/helper")
    monkeypatch.setattr(screenshot.subprocess, "run", run)
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
        "displays": [{"id": 31}],
        "protected_window_ids": [71],
        "overlay_window_ids": [82],
    }


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
    assert request["displays"] == [{"id": 12}]

    request = _install_helper(
        monkeypatch,
        _response(displays, ((220, 10, 20), (10, 220, 20))),
    )
    separate = _grab(monitor_mode="separate", displays=displays)
    assert separate is not None
    assert [shot.monitor_index for shot in separate] == [1, 2]
    assert [_decoded(shot).getpixel((0, 0))[1] > 180 for shot in separate] == [False, True]
    assert request["displays"] == [{"id": 31}, {"id": 12}]


def test_all_stitches_global_bounds_with_negative_vertical_displays_and_scaling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    displays = (
        _display(31, -10, 0, 10, 10, primary=True),
        _display(12, 0, -10, 10, 10),
        _display(7, 0, 0, 10, 10),
    )
    _install_helper(monkeypatch, _response(displays, ((220, 10, 20), (10, 220, 20), (10, 20, 220))))

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
    image = _decoded(shot)
    assert image.getpixel((1, 6))[0] > 180
    assert image.getpixel((6, 1))[1] > 180
    assert image.getpixel((7, 7))[2] > 180
    assert image.getpixel((5, 5))[0] in range(105, 160)


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
        screenshot.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired("helper", 1)),
    )

    assert _grab(monitor_mode="primary", displays=displays) is None


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
