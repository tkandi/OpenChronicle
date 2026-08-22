# Window-Filtered Screenshot Modes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add mask-window and exclude-window screenshot privacy modes while retaining skip-monitor as
the default and fail-closed fallback.

**Architecture:** Carry protected CG window IDs through the in-memory protection decision, capture
affected displays with a bundled ScreenCaptureKit helper that excludes those windows at source, and
post-process only already-filtered pixels for masks, JPEG encoding, and multi-display composition.

**Tech Stack:** Python 3.11+, Swift 5/AppKit/ScreenCaptureKit/ImageIO, mss, Pillow, SwiftUI, pytest,
Swift standalone harnesses.

## Global Constraints

- `skip-monitor` remains the default and existing behavior remains byte-compatible.
- Protected-window pixels must never be returned by the helper in mask-window or exclude-window.
- Missing/ambiguous display or window identity always falls back to skip-monitor.
- Diagnostics guards, pause/failure states, and unconfirmed indicators always use full-monitor
  fail-closed behavior.
- Protected and privacy-overlay window IDs remain memory-only and never enter
  persisted/model/MCP/log payloads.
- New ScreenCaptureKit modes require macOS 14+; older systems safely fall back.

---

### Task 1: Configuration And Native Settings

**Files:**
- Modify: `src/openchronicle/config.py`
- Modify: `src/openchronicle/config_editor.py`
- Modify: `docs/config.md`
- Modify: `macos/OpenChronicleApp/Sources/OpenChronicleApp/Views/SettingsView.swift`
- Test: `tests/test_config.py`
- Test: `tests/test_cli_config_editor.py`
- Test: `macos/OpenChronicleApp/Tests/OpenChronicleAppTests/ConfigurationTests.swift`

**Interfaces:**
- Produces: privacy modes `off`, `skip-monitor`, `mask-window`, `exclude-window`

- [ ] Write failing Python and Swift tests for validation, defaults, snapshots, and picker values.
- [ ] Run focused tests and verify RED because the two new modes are rejected or absent.
- [ ] Add the two values without changing the default; update Settings labels and docs.
- [ ] Run focused tests and verify GREEN.
- [ ] Commit the task.

### Task 2: Protected Window Identity Pipeline

**Files:**
- Modify: `resources/mac-window-list.swift`
- Modify: `src/openchronicle/capture/privacy.py`
- Modify: `src/openchronicle/capture/protection.py`
- Test: `tests/swift/MacWindowListCoreTests.swift`
- Test: `tests/test_capture_privacy.py`
- Test: `tests/test_protection.py`

**Interfaces:**
- Produces: JSON `window_id: UInt32?`
- Produces: `VisibleWindow.window_id: int | None`
- Produces: `ProtectionSnapshot.protected_window_ids: frozenset[int]`
- Produces: `ProtectionSnapshot.protected_window_regions: tuple[ScreenRegion, ...]`
- Produces: a boolean/invariant proving all protected regions are window-filterable

- [ ] Write RED tests for valid IDs, missing/duplicate IDs, matched window ID collection, and
  diagnostics/pause/failure exclusion from window filtering.
- [ ] Implement positive-ID parsing plus protected ID/region aggregation without persistence.
- [ ] Run Swift/Python GREEN tests and marker boundary tests.
- [ ] Commit the task.

### Task 3: ScreenCaptureKit Single-Frame Helper

**Files:**
- Create: `resources/mac-screen-capture-core.swift`
- Create: `resources/mac-screen-capture.swift`
- Create: `resources/build-mac-screen-capture.sh`
- Modify: `pyproject.toml`
- Modify: `install.sh`
- Test: `tests/swift/MacScreenCaptureCoreTests.swift`
- Test: `tests/test_runtime_dependencies.py`

**Interfaces:**
- Consumes NDJSON/JSON command schema version 1 with display IDs, source-excluded protected/overlay
  window IDs, and per-display output dimensions.
- Produces JSON response schema version 1 with PNG images and display geometry, or fixed error code.

The exact wire schema and fixed error codes are defined in the design spec. The helper processes
exactly one line and exits. Protected and overlay IDs are separate, all IDs are positive unique
UInt32 values, the two lists are disjoint, and `protected_window_ids` must not be empty. Each
display's `width` and `height` are both omitted for native pixels or both positive integers.

- [ ] Write pure Swift RED tests for command validation, target resolution, missing/duplicate
  display or excluded-window IDs, output dimensions, unsupported OS, and response bounds.
- [ ] Implement the pure resolver and fixed wire protocol.
- [ ] Implement macOS 14+ ScreenCaptureKit capture using `SCContentFilter(display:excludingWindows:)`
  and `SCScreenshotManager.captureImage`; encode PNG with ImageIO.
- [ ] Build and run core/protocol tests plus arm64/x86_64 compilation.
- [ ] Verify wheel inclusion and resolver source requirements.
- [ ] Commit the task.

### Task 4: Python Filtered Screenshot Backend

**Files:**
- Modify: `src/openchronicle/capture/screenshot.py`
- Test: `tests/test_capture_privacy.py`
- Create or Modify: `tests/test_filtered_screenshot.py`

**Interfaces:**
- Produces:
  `grab_filtered_many(*, monitor_mode, privacy_mode, displays, protected_window_ids,
  protected_window_regions, overlay_window_ids, max_width, jpeg_quality) -> list[Screenshot] | None`
- `None` means safe filtered capture unavailable and requires skip-monitor fallback.
- Consumes the already-authorized union of protected and confirmed overlay window IDs; Python never
  receives pixels from either class of window.

`privacy_mode` accepts only `mask-window` and `exclude-window`. `displays` preserves native
inventory order and carries stable CG display IDs/bounds/primary metadata. Protected IDs are
non-empty; protected regions have the same cardinality; overlay IDs may be empty. The backend must
strictly validate the helper's exact success schema, requested display set/order, geometry, PNG
dimensions, and base64 before creating any `Screenshot`. Any launch/timeout/exit/stderr/protocol,
ID, geometry, image, or output-completeness failure returns `None` without partial screenshots.

- [ ] Write RED tests with a fake helper response for PNG decoding, opaque masks, coordinate scaling,
  separate/primary/all outputs, stitching, missing displays/windows, invalid images, and timeout.
- [ ] Implement helper resolution/subprocess bounds and strict response validation.
- [ ] Implement already-filtered PNG processing, mask drawing, resizing, JPEG encoding, and all-mode
  virtual-desktop composition.
- [ ] Run focused GREEN tests and Ruff.
- [ ] Commit the task.

### Task 5: Scheduler Integration And Fail-Closed Fallback

**Files:**
- Modify: `resources/mac-privacy-overlay-core.swift`
- Modify: `resources/mac-privacy-overlay.swift`
- Modify: `src/openchronicle/capture/privacy_overlay.py`
- Modify: `src/openchronicle/capture/protection_monitor.py`
- Modify: `src/openchronicle/daemon.py`
- Modify: `src/openchronicle/capture/scheduler.py`
- Modify: `docs/capture.md`
- Test: `tests/swift/MacPrivacyOverlayCoreTests.swift`
- Test: `tests/test_privacy_overlay.py`
- Test: `tests/test_daemon_protection.py`
- Test: `tests/test_capture_scheduler_fts.py`
- Test: `tests/test_privacy_reason_boundaries.py`

**Interfaces:**
- Consumes protected window IDs/regions and filtered screenshot backend.
- Produces safe mode selection: filtered capture or current skip-monitor fallback.

- [ ] Write RED tests for overlay acknowledgement window IDs, monitor creation in both new modes,
  and every fallback condition.
- [ ] Integrate filtered capture only for complete rule-matched window decisions.
- [ ] Pass every rendered overlay/input-panel window ID through the in-memory decision and
  source-exclude it; a non-`off` indicator without complete IDs falls back to `skip-monitor`.
- [ ] Prove diagnostics/pause/failure/unconfirmed/missing-ID/helper-error paths never call unfiltered
  protected-monitor capture.
- [ ] Run focused integration/boundary tests and commit.

### Task 6: Full Verification, Packaging, Installation, And Live Acceptance

**Files:**
- Modify: `docs/macos-app.md`
- Modify: `docs/capture.md` if Task 5 did not complete user-facing semantics

- [ ] Run full Python, changed-file Ruff, SwiftPM, all Swift helper harnesses, protocol tests,
  arm64/x86_64 helper builds, wheel/sdist, isolated wheel resolver, signed App, and `git diff --check`.
- [ ] Run final whole-branch privacy review.
- [ ] Install backend and signed App after a clean shutdown; verify source/install SHA and one healthy
  App-owned process chain.
- [ ] Back up the config and live-test skip-monitor, mask-window, and exclude-window using a blank
  Edge InPrivate window across primary/separate/all modes. Verify fail-closed fallback by making the
  helper unavailable in an isolated root.
- [ ] Restore the original config SHA, close all test windows, remove test artifacts, and verify the
  final runtime is healthy/active with no guard or marker leakage.
