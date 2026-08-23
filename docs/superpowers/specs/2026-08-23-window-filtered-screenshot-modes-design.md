# Window-Filtered Screenshot Modes Design

## Goal

Retain the existing whole-monitor privacy behavior and add two source-filtered screenshot modes that
preserve non-sensitive screen context while excluding every application identified as protected by
the before/after OS inventories from pixels returned to Python.

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
2. The helper retrieves `SCShareableContent` with onscreen windows only and fingerprints the
   requested displays plus windows owned by the resolved protected/overlay application PIDs,
   including each window's ID, owner PID, finite frame, and title.
3. Every requested display and excluded window ID must resolve exactly once. Every protected and
   overlay window must also resolve to one valid, unique owning application.
4. For each display, the helper creates
   `SCContentFilter(display:excludingApplications:exceptingWindows:)`, excluding the unique complete
   application set and excepting no windows. This intentionally removes every normal and auxiliary
   window from a protected application.
5. After all display captures, the helper reloads `SCShareableContent` and compares the scoped
   fingerprint before any PNG encoding or stdout. A change returns `content_changed` and no PNG.
6. The helper returns one lossless PNG per physical display plus display bounds and pixel dimensions.
7. Python applies an opaque rectangle in `mask-window`, leaves the image unmasked in
   `exclude-window`, resizes/encodes JPEG, and stitches per-display images for `all` mode.

The helper is a one-request process with this version-1 JSON wire shape (one line on stdin and one
line on stdout):

```json
{
  "version": 1,
  "displays": [{"id": 123, "width": 1920, "height": 1080}],
  "protected_window_ids": [456],
  "overlay_window_ids": [789]
}
```

Display `width` and `height` are either both positive integers or both omitted to request native
pixels. Protected IDs are non-empty; both ID lists contain unique positive UInt32 values and are
disjoint. A successful response is:

```json
{
  "version": 1,
  "status": "ok",
  "displays": [{
    "id": 123,
    "left": 0,
    "top": 0,
    "point_width": 1920,
    "point_height": 1080,
    "pixel_width": 1920,
    "pixel_height": 1080,
    "png_base64": "..."
  }]
}
```

Errors use `{"version":1,"status":"error","error":"<fixed-code>"}` with no titles,
application names, rule values, private IDs, paths, or OS error text. The fixed codes are
`unsupported_os`, `invalid_command`, `content_unavailable`, `display_not_found`,
`window_not_found`, `ambiguous_display`, `ambiguous_window`, `window_owner_unavailable`,
`content_changed`, `capture_failed`, and `encode_failed`. The helper rejects an empty protected-ID
list so it cannot accidentally become an unfiltered general screenshot path.

Both sides enforce the same constants: 65,536 command bytes, 16 displays, 16,384 pixels per
dimension, 128,000,000 aggregate pixels, 67,108,864 bytes per PNG, 134,217,728 aggregate PNG bytes,
188,743,680 response bytes, and 65,536 stderr bytes. Python streams bounded stdout/stderr from a
`Popen` process group and terminates, kills if necessary, and waits after timeout or overflow. It
bounds base64 before decoding, validates PNG IHDR dimensions before Pillow, and treats Pillow
decompression-bomb warnings and errors as failure.

Apple documents that `SCContentFilter` can capture a display while excluding applications and
that `SCScreenshotManager` captures a single image using that filter:

- https://developer.apple.com/documentation/screencapturekit/sccontentfilter
- https://developer.apple.com/documentation/screencapturekit/sccontentfilter/init%28display%3Aexcludingapplications%3Aexceptingwindows%3A%29
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
windows by promoting both sets to unique owning applications. With a non-`off` indicator, missing
or unresolved overlay IDs make window filtering ineligible and stop screenshot capture until a
later generation is confirmed.

The overlay executable is built and launched inside
`runtime/helpers/OpenChroniclePrivacyOverlay.app` under the active OpenChronicle root. The helper
bundle gives its panels a stable ScreenCaptureKit owning application; the default runtime resolver
never launches the source-tree bare executable.

Window-filtered capture is authorized only when every protected region comes from one or more valid,
uniquely mapped protected window IDs. Diagnostics display leases, pause states, inventory failures,
unknown mappings, and indicator failures are never authorized for window filtering.

## Fail-Closed Matrix

For `mask-window` and `exclude-window`, any of these conditions falls back to the current
`skip-monitor` path using `mss` and protected display regions:

- macOS earlier than 14.0 or unavailable ScreenCaptureKit symbols;
- missing Screen Recording permission;
- helper launch, timeout, parse, or image decode failure;
- a requested display or protected/overlay window ID missing from `SCShareableContent`;
- a requested window with missing or ambiguous owning-application identity;
- duplicate/invalid IDs or inconsistent bounds;
- diagnostics guard, unmapped owner/window, or incomplete indicator IDs;
- any filtered display missing from the helper response.

Pause and fail-closed inventory states stop the capture. A non-`off` unconfirmed indicator stops
before `mss`, including an unconfirmed inactive clear. Every `mss` fallback is checked again after
capture and discarded if indicator confirmation or authorization changes.

The fallback may lose non-sensitive context but does not run unblocked on a monitor protected by
either of its confirmed before/after decisions.

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

The mask is drawn only after ScreenCaptureKit has excluded the protected owning application, so its
pixels do not enter the Python image. The mask uses a fixed opaque neutral color and covers the intersection
between the protected CG frame and each display. It does not reveal the window behind the protected
window. No title, app name, or rule text is drawn into the screenshot.

Application-level filtering excludes new windows from an app already identified as protected, and
the scoped double inventory snapshot detects persistent additions, removals, frame changes, owner
changes, and title-classification changes for protected/overlay applications. Python's forced
post-helper protection decision drops persistent privacy changes in other applications.
ScreenCaptureKit, `SCShareableContent`, and the Python decision snapshots still cannot prove absence
of a different application's privacy window that appears and disappears entirely between those
snapshots. Documentation and acceptance claims must retain this residual race.

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
