# Window-Filtered Screenshot Modes Design

## Goal

Retain the existing whole-monitor privacy behavior and add two source-filtered screenshot modes that
preserve non-sensitive screen context without ever returning protected-window pixels to Python.

## User-Visible Modes

`capture.screenshot_privacy_mode` accepts four values:

- `off`: no visible-window screenshot protection.
- `skip-monitor`: current behavior and default; omit every monitor intersected by a protected window.
- `mask-window`: capture a display with protected windows excluded at the OS capture source, then
  cover each excluded window frame with an opaque protection rectangle.
- `exclude-window`: capture a display with protected windows excluded at the OS capture source and
  expose the windows or desktop behind them.

The native Settings picker exposes all four values. Existing configs remain valid and continue to
default to `skip-monitor`.

## Capture Architecture

The current `mss` path remains unchanged for `off`, `skip-monitor`, and any capture without matched
windows. New window-filtered modes use a bundled Swift helper based on ScreenCaptureKit:

1. Python sends requested display IDs, protected CG window IDs, confirmed privacy-overlay window
   IDs, and capture dimensions to the helper.
2. The helper retrieves `SCShareableContent` with onscreen windows only.
3. Every requested display and excluded window ID must resolve exactly once.
4. For each display, the helper creates `SCContentFilter(display:excludingWindows:)` and uses
   `SCScreenshotManager.captureImage` to capture one source-filtered frame.
5. The helper returns one lossless PNG per physical display plus display bounds and pixel dimensions.
6. Python applies an opaque rectangle in `mask-window`, leaves the image unmasked in
   `exclude-window`, resizes/encodes JPEG, and stitches per-display images for `all` mode.

Apple documents that `SCContentFilter` can capture a display while excluding specific windows and
that `SCScreenshotManager` captures a single image using that filter:

- https://developer.apple.com/documentation/screencapturekit/sccontentfilter
- https://developer.apple.com/documentation/screencapturekit/sccontentfilter/init%28display%3Aexcludingwindows%3A%29
- https://developer.apple.com/documentation/screencapturekit/scscreenshotmanager/captureimage%28contentfilter%3Aconfiguration%3Acompletionhandler%3A%29

## Window Identity And Protection Decisions

`mac-window-list` already performs globally unique `(ownerPID, windowID)` matching between CG and AX
windows. Its JSON record adds the optional positive `window_id`. Python `VisibleWindow` carries this
ID, and `ProtectionSnapshot` carries protected window IDs alongside protected display regions.

Only rule-matched visible windows contribute IDs and mask regions. The snapshot keeps both values
in memory for the immediate screenshot decision. IDs and mask regions are not written to capture
JSON, FTS, logs, timeline, memory, model input, or MCP responses.

The native privacy overlay can reveal exact app, title, and rule details. A confirmed overlay
acknowledgement therefore also returns the CG window IDs of every rendered indicator/input panel.
Those IDs remain in the in-memory `ProtectionDecision` and are source-excluded with the protected
windows. With a non-`off` indicator, missing or unresolved overlay IDs make window filtering
ineligible and force the `skip-monitor` fallback.

Window-filtered capture is authorized only when every protected region comes from one or more valid,
uniquely mapped protected window IDs. Diagnostics display leases, pause states, inventory failures,
unknown mappings, and indicator failures are never authorized for window filtering.

## Fail-Closed Matrix

For `mask-window` and `exclude-window`, any of these conditions falls back to the current
`skip-monitor` path using `mss` and protected display regions:

- macOS earlier than 14.0 or unavailable ScreenCaptureKit symbols;
- missing Screen Recording permission;
- helper launch, timeout, parse, or image decode failure;
- a requested display or protected window ID missing from `SCShareableContent`;
- a confirmed non-`off` privacy overlay missing window IDs, or any overlay ID missing from
  `SCShareableContent`;
- duplicate/invalid IDs or inconsistent bounds;
- pause, diagnostics guard, inventory failure, unmapped window, or unconfirmed indicator;
- any filtered display missing from the helper response.

The fallback may lose non-sensitive context but may never produce an unfiltered image of a protected
monitor.

## Multi-Monitor Semantics

- `primary`: request only the primary display. If protected, use the selected filtered mode.
- `separate`: return one JPEG per physical display. Protected displays use the selected filtered
  mode; other displays may continue through the existing capture path.
- `all`: capture every physical display independently with the same protected-window exclusion,
  place each result in a virtual-desktop canvas using global bounds, then return one JPEG marked
  `monitor.is_all = true`.

A protected window spanning displays is excluded from every affected display and masked on every
intersection in `mask-window`.

## Mask Semantics

The mask is drawn only after ScreenCaptureKit has excluded the protected window, so sensitive pixels
never enter the Python image. The mask uses a fixed opaque neutral color and covers the intersection
between the protected CG frame and each display. It does not reveal the window behind the protected
window. No title, app name, or rule text is drawn into the screenshot.

## Compatibility And Packaging

The new helper sources and build script are bundled in wheel/sdist and compiled during `install.sh`.
On macOS 14+ the single-frame API is used. On older systems the helper returns a fixed unsupported
error and Python safely falls back to `skip-monitor`.

## Verification

- Pure Swift tests for command validation, display/window resolution, duplicate/missing IDs, and
  fixed error payloads.
- Python tests for helper output validation, JPEG conversion, masks, multi-display stitching, and
  fallback selection.
- Scheduler/boundary tests proving protected pixels and private markers cannot enter capture JSON,
  FTS, logs, model, or MCP paths.
- Full Python, Swift, Ruff, arm64/x86_64 helper, wheel isolation, and signed App verification.
- Live tests for all three protected modes using only a blank Edge InPrivate window and a synthetic
  title marker. Restore `skip-monitor` after acceptance.
