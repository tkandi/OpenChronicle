# Privacy Indicator Placement Presets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add three configurable privacy-indicator placement presets, defaulting to the physical screen's flush lower-left corner while preserving the current lower-right work-area behavior.

**Architecture:** The placement is a normalized `CaptureConfig` value copied into each immutable `ProtectionSnapshot`, hot-reloaded atomically with the indicator style, and serialized in the same generation-scoped overlay command. The native helper owns geometry: compact panels grow away from their anchored edge, border badges use a display-local anchor rectangle, and banner/off behavior is unchanged. The macOS app edits the same enum through the existing configuration snapshot/draft/patch path.

**Tech Stack:** Python 3.11+, pytest, AppKit/Swift 5.9, SwiftUI, Swift Package Manager, TOML, NDJSON overlay IPC.

## Global Constraints

- Exact config key: `capture.privacy_indicator_placement`.
- Exact values: `bottom-left-flush`, `bottom-left-inset`, `bottom-right-work-area`.
- Config default and UI default: `bottom-left-flush`.
- Native wire fallback when `placement` is absent: `bottom-right-work-area`.
- Insets are AppKit points: 0pt for flush and 12pt for inset/work-area presets.
- Compact left presets grow right and up; the right preset grows left and up; the status badge never moves while reasons expand.
- `border` keeps a full-screen panel but moves its badge/reason anchor; `banner` and `off` ignore placement.
- Placement reload must share one config read and one snapshot generation with style reload.
- Invalid explicit native wire values fail decoding; they do not silently choose a location.
- Existing overlay acknowledgement, window-ID exclusion, diagnostics guard, pause, denylist, and fail-closed semantics remain unchanged.
- No arbitrary coordinates, drag positioning, per-display settings, Hot Corner discovery, or Dock-animation tracking.
- Source specification: `docs/superpowers/specs/2026-08-24-privacy-indicator-placement-design.md`.

## File Map

- `src/openchronicle/config.py`: placement enum values, default, normalization, and generated config comments.
- `src/openchronicle/config_editor.py`: editable-field allowlist, validation, and secret-safe config snapshot.
- `src/openchronicle/capture/protection.py`: immutable snapshot field and snapshot construction.
- `src/openchronicle/capture/protection_monitor.py`: atomic style/placement hot reload and generation logging.
- `src/openchronicle/capture/privacy_overlay.py`: placement validation at the wire boundary and render-command serialization.
- `src/openchronicle/capture/scheduler.py`: include placement in the filtered-capture authorization fingerprint.
- `resources/mac-privacy-overlay-core.swift`: native placement enum, backward-compatible decoding, panel geometry, badge alignment, and reason expansion.
- `tests/swift/MacPrivacyOverlayCoreTests.swift`: deterministic native protocol and geometry coverage.
- `macos/OpenChronicleApp/Sources/OpenChronicleApp/Models/PrivacyIndicatorPlacement.swift`: UI option model.
- `macos/OpenChronicleApp/Sources/OpenChronicleApp/Models/Configuration.swift`: snapshot/draft/patch data path.
- `macos/OpenChronicleApp/Sources/OpenChronicleApp/Views/SettingsView.swift`: position menu and style-based availability.
- `macos/OpenChronicleApp/Tests/OpenChronicleAppTests/PrivacyIndicatorPlacementTests.swift`: option-model tests.
- `macos/OpenChronicleApp/Tests/OpenChronicleAppTests/ConfigurationTests.swift`: config round-trip/default/diff tests.
- `tests/test_config.py`, `tests/test_cli_config_editor.py`: Python configuration tests.
- `tests/test_protection.py`, `tests/test_protection_monitor.py`: snapshot and atomic reload tests.
- `tests/test_privacy_overlay.py`: Python-to-Swift wire payload tests.
- `tests/test_capture_scheduler_fts.py`: authorization fingerprint regression test.
- Existing indicator specs in `docs/superpowers/specs/`: remove stale lower-right-only statements after behavior is implemented.

---

### Task 1: Python Configuration, Snapshot, and Hot Reload

**Files:**
- Modify: `src/openchronicle/config.py:13-18,43-110,301-319`
- Modify: `src/openchronicle/config_editor.py:50-80,210-275,415-440`
- Modify: `src/openchronicle/capture/protection.py:56-78,326-345`
- Modify: `src/openchronicle/capture/protection_monitor.py:69-83,204-250,294-340`
- Modify: `src/openchronicle/capture/scheduler.py:93-118`
- Test: `tests/test_config.py`
- Test: `tests/test_cli_config_editor.py`
- Test: `tests/test_protection.py`
- Test: `tests/test_protection_monitor.py`
- Test: `tests/test_capture_scheduler_fts.py`

**Interfaces:**
- Produces: `PRIVACY_INDICATOR_PLACEMENTS: frozenset[str]`.
- Produces: `CaptureConfig.privacy_indicator_placement: str` normalized to one of the three exact values.
- Produces: `ProtectionSnapshot.indicator_placement: str` with default `bottom-left-flush`.
- Produces: `PrivacyProtectionMonitor._reload_indicator_settings() -> None`, atomically updating style and placement from one `config.load()` result.
- Consumed later by: `PrivacyOverlayClient._render_command()` and the macOS configuration snapshot.

- [ ] **Step 1: Add failing configuration and editor tests**

Add this test beside `test_capture_privacy_indicator_style_config` in `tests/test_config.py`:

```python
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
```

Add a focused editor test in `tests/test_cli_config_editor.py`:

```python
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
```

- [ ] **Step 2: Run the configuration tests and verify RED**

Run:

```bash
uv run pytest tests/test_config.py::test_capture_privacy_indicator_placement_config -q
uv run pytest tests/test_cli_config_editor.py::test_indicator_placement_patch_validate_and_snapshot -q
```

Expected: the first test fails because `CaptureConfig` has no placement field; the second fails because the editor snapshot/allowlist does not expose the key.

- [ ] **Step 3: Implement config normalization and editor support**

In `src/openchronicle/config.py`, add the exact allowed set and field:

```python
PRIVACY_INDICATOR_PLACEMENTS = frozenset(
    {"bottom-left-flush", "bottom-left-inset", "bottom-right-work-area"}
)

@dataclass
class CaptureConfig:
    privacy_indicator_style: str = "pill"
    privacy_indicator_placement: str = "bottom-left-flush"
```

Normalize it in `CaptureConfig.__post_init__` immediately after style normalization:

```python
indicator_placement = str(
    self.privacy_indicator_placement or "bottom-left-flush"
).strip().lower()
self.privacy_indicator_placement = (
    indicator_placement
    if indicator_placement in PRIVACY_INDICATOR_PLACEMENTS
    else "bottom-left-flush"
)
```

Add this generated config line after `privacy_indicator_style`:

```toml
privacy_indicator_placement = "bottom-left-flush" # bottom-left-flush, bottom-left-inset, or bottom-right-work-area
```

In `src/openchronicle/config_editor.py`:

1. Add `capture.privacy_indicator_placement` to `_EDITABLE_PATHS`.
2. Validate it with `_require_type` and `config_mod.PRIVACY_INDICATOR_PLACEMENTS`.
3. Emit exactly this error for an unknown value:

```python
raise ConfigEditorError(
    "capture.privacy_indicator_placement must be bottom-left-flush, "
    "bottom-left-inset, or bottom-right-work-area"
)
```

4. Add `"privacy_indicator_placement": cfg.capture.privacy_indicator_placement` to the capture snapshot.

- [ ] **Step 4: Run the configuration tests and verify GREEN**

Run:

```bash
uv run pytest tests/test_config.py tests/test_cli_config_editor.py -q
```

Expected: all config and editor tests pass, including default, case normalization, patching, and invalid-value rejection.

- [ ] **Step 5: Add failing snapshot, reload, and authorization-key tests**

In `tests/test_protection.py`, extend the existing default snapshot assertion or add:

```python
def test_snapshot_carries_indicator_placement() -> None:
    cfg = CaptureConfig(privacy_indicator_placement="bottom-left-inset")
    inventory = WindowInventory(windows=(), displays=(LEFT, RIGHT))
    snapshot = build_protection_snapshot(
        cfg,
        inventory,
        paused=False,
        generation=7,
        now=1.0,
    )
    assert snapshot.indicator_placement == "bottom-left-inset"
```

Replace the style-only hot-reload test in `tests/test_protection_monitor.py` with an atomic settings test:

```python
def test_indicator_settings_hot_reload_atomically(tmp_path, inventory, fake_overlay) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[capture]\nprivacy_indicator_style = "shield"\n'
        'privacy_indicator_placement = "bottom-left-flush"\n'
    )
    monitor = make_monitor(config_path=config_path, inventory=inventory, overlay=fake_overlay)
    first = monitor.decision_for_capture(force=True)

    previous_mtime = config_path.stat().st_mtime_ns
    config_path.write_text(
        '[capture]\nprivacy_indicator_style = "banner"\n'
        'privacy_indicator_placement = "bottom-right-work-area"\n'
    )
    os.utime(config_path, ns=(previous_mtime + 1, previous_mtime + 1))
    second = monitor.decision_for_capture(force=True)

    assert (first.snapshot.indicator_style, first.snapshot.indicator_placement) == (
        "shield",
        "bottom-left-flush",
    )
    assert (second.snapshot.indicator_style, second.snapshot.indicator_placement) == (
        "banner",
        "bottom-right-work-area",
    )
    assert second.snapshot.generation > first.snapshot.generation
```

In `tests/test_capture_scheduler_fts.py`, add a fingerprint regression test using an existing valid decision fixture or a minimal `ProtectionSnapshot`:

```python
def test_filtered_authorization_key_includes_indicator_placement() -> None:
    snapshot = ProtectionSnapshot(
        generation=1,
        state=ProtectionState.PROTECTED,
        capture_mode="separate",
        indicator_style="pill",
        displays=(),
        protected_display_ids=frozenset(),
        active_display_id=None,
        created_monotonic=1.0,
        fresh_until=1.25,
        indicator_placement="bottom-left-flush",
    )
    first = ProtectionDecision(snapshot=snapshot, indicator_confirmed=True)
    second = ProtectionDecision(
        snapshot=replace(snapshot, indicator_placement="bottom-left-inset"),
        indicator_confirmed=True,
    )
    assert scheduler_mod._filtered_authorization_key(first) != (
        scheduler_mod._filtered_authorization_key(second)
    )
```

Add `replace` from `dataclasses` to that test file if it is not already imported.

- [ ] **Step 6: Run the new snapshot/reload tests and verify RED**

Run:

```bash
uv run pytest tests/test_protection.py::test_snapshot_carries_indicator_placement -q
uv run pytest tests/test_protection_monitor.py::test_indicator_settings_hot_reload_atomically -q
uv run pytest tests/test_capture_scheduler_fts.py::test_filtered_authorization_key_includes_indicator_placement -q
```

Expected: failures identify the missing snapshot field, style-only reload path, and authorization-key field.

- [ ] **Step 7: Implement immutable propagation and atomic hot reload**

In `ProtectionSnapshot`, add the defaulted field after `fresh_until` so existing keyword-based test constructors remain valid:

```python
indicator_placement: str = "bottom-left-flush"
```

Pass `cfg.privacy_indicator_placement` in `build_protection_snapshot`.

In `PrivacyProtectionMonitor.__init__`, retain both hot settings:

```python
self._indicator_style = cfg.privacy_indicator_style
self._indicator_placement = cfg.privacy_indicator_placement
```

Rename `_reload_indicator_style` to `_reload_indicator_settings`, load once, and only advance `_config_mtime_ns` after assigning both values:

```python
def _reload_indicator_settings(self) -> None:
    try:
        mtime_ns = self._config_path.stat().st_mtime_ns
    except OSError:
        return
    if self._config_mtime_ns == mtime_ns:
        return
    try:
        capture = config.load(self._config_path).capture
    except (OSError, TypeError, ValueError) as exc:
        logger.warning("privacy protection settings reload failed: %s", type(exc).__name__)
        return
    self._indicator_style = capture.privacy_indicator_style
    self._indicator_placement = capture.privacy_indicator_placement
    self._config_mtime_ns = mtime_ns
```

Call this method at the start of `_refresh`. Include both values in inactive snapshots and in the
`replace` call shown below before invoking `build_protection_snapshot`:

```python
snapshot_cfg = replace(
    self._cfg,
    privacy_indicator_style=self._indicator_style,
    privacy_indicator_placement=self._indicator_placement,
)
```

Extend the debug log with `placement=%s`, but do not add titles, rules, or reason text.

Add `snapshot.indicator_placement` immediately after `snapshot.indicator_style` in `_filtered_authorization_key`.

- [ ] **Step 8: Run the complete Task 1 test slice**

Run:

```bash
uv run pytest tests/test_config.py tests/test_cli_config_editor.py tests/test_protection.py tests/test_protection_monitor.py tests/test_capture_scheduler_fts.py -q
uv run ruff check src/openchronicle/config.py src/openchronicle/config_editor.py src/openchronicle/capture/protection.py src/openchronicle/capture/protection_monitor.py src/openchronicle/capture/scheduler.py tests/test_config.py tests/test_cli_config_editor.py tests/test_protection.py tests/test_protection_monitor.py tests/test_capture_scheduler_fts.py
```

Expected: all selected tests pass and Ruff reports `All checks passed!`.

- [ ] **Step 9: Commit Task 1**

```bash
git add src/openchronicle/config.py src/openchronicle/config_editor.py \
  src/openchronicle/capture/protection.py \
  src/openchronicle/capture/protection_monitor.py \
  src/openchronicle/capture/scheduler.py tests/test_config.py \
  tests/test_cli_config_editor.py tests/test_protection.py \
  tests/test_protection_monitor.py tests/test_capture_scheduler_fts.py
git commit -m "feat(config): add privacy indicator placement presets"
```

---

### Task 2: Overlay Wire Protocol and Native Geometry

**Files:**
- Modify: `src/openchronicle/capture/privacy_overlay.py:425-432,620-660`
- Modify: `resources/mac-privacy-overlay-core.swift:1-145,206-435,576-729`
- Test: `tests/test_privacy_overlay.py`
- Test: `tests/swift/MacPrivacyOverlayCoreTests.swift`

**Interfaces:**
- Consumes: `ProtectionSnapshot.indicator_placement` from Task 1.
- Produces: render-command JSON field `placement: str`.
- Produces: Swift `IndicatorPlacement: String, Codable` with cases `bottomLeftFlush`, `bottomLeftInset`, and `bottomRightWorkArea`.
- Produces: `OverlayCommand.placement: IndicatorPlacement`, defaulting to `.bottomRightWorkArea` only when the wire field is absent.
- Preserves: `OverlayAcknowledgement` shape and all window-ID confirmation behavior.

- [ ] **Step 1: Add a failing Python render-command test**

Extend the existing `snapshot` fixture or add this focused test in `tests/test_privacy_overlay.py`:

```python
def test_render_command_includes_indicator_placement(snapshot: ProtectionSnapshot) -> None:
    command = PrivacyOverlayClient._render_command(
        replace(snapshot, indicator_placement="bottom-left-inset")
    )
    assert command["placement"] == "bottom-left-inset"
```

Also update the legacy/simple-namespace render-command test to assert that Python-side missing snapshot attributes use the new config default:

```python
assert command["placement"] == "bottom-left-flush"
```

- [ ] **Step 2: Run the Python wire test and verify RED**

Run:

```bash
uv run pytest tests/test_privacy_overlay.py::test_render_command_includes_indicator_placement -q
```

Expected: FAIL with missing `placement` in the command.

- [ ] **Step 3: Serialize the normalized placement**

Add a private allowed set and boundary helper in `privacy_overlay.py`:

```python
_INDICATOR_PLACEMENTS = frozenset(
    {"bottom-left-flush", "bottom-left-inset", "bottom-right-work-area"}
)

def _placement_setting(snapshot: ProtectionSnapshot) -> str:
    value = getattr(snapshot, "indicator_placement", "bottom-left-flush")
    return value if isinstance(value, str) and value in _INDICATOR_PLACEMENTS else (
        "bottom-left-flush"
    )
```

Add this to `_render_command`:

```python
"placement": _placement_setting(snapshot),
```

Do not change clear-command behavior; the Swift decoder's missing-field fallback handles inactive/off commands that do not need geometry.

- [ ] **Step 4: Run the Python overlay tests and verify GREEN**

Run:

```bash
uv run pytest tests/test_privacy_overlay.py -q
```

Expected: all overlay transport, acknowledgement, restart, and payload tests pass.

- [ ] **Step 5: Add failing Swift protocol and geometry tests**

At the start of `MacPrivacyOverlayCoreTests.main`, extend the existing decode assertions:

```swift
precondition(command.placement == .bottomRightWorkArea)

let explicitPlacement = try JSONDecoder().decode(
    OverlayCommand.self,
    from: Data(#"{"generation":10,"state":"protected","style":"pill","placement":"bottom-left-flush","displays":[],"all_displays":false}"#.utf8)
)
precondition(explicitPlacement.placement == .bottomLeftFlush)

do {
    _ = try JSONDecoder().decode(
        OverlayCommand.self,
        from: Data(#"{"generation":11,"state":"protected","style":"pill","placement":"future-placement","displays":[],"all_displays":false}"#.utf8)
    )
    preconditionFailure("unknown explicit placement must fail decoding")
} catch {
    precondition(error is DecodingError)
}
```

Add `testPlacementGeometry()` to the main test list. Use a screen whose physical frame and visible frame differ:

```swift
private static func testPlacementGeometry() {
    let screen = OverlayScreenGeometry(
        id: 1,
        frame: NSRect(x: -1200, y: -200, width: 1200, height: 800),
        visibleFrame: NSRect(x: -1200, y: -140, width: 1200, height: 740)
    )

    func renderedFrame(
        _ placement: IndicatorPlacement,
        screen: OverlayScreenGeometry
    ) -> NSRect {
        let panel = RecordingPanel(contentRect: .zero)
        let controller = PrivacyOverlayController(
            screenProvider: { [screen] },
            panelFactory: { panel }
        )
        controller.apply(
            OverlayCommand(
                generation: 30,
                state: .protected,
                style: .pill,
                displays: [OverlayDisplay(id: 1, left: 0, top: 0, width: 1, height: 1)],
                allDisplays: false,
                placement: placement
            )
        ) { precondition($0) }
        return panel.frame
    }

    let flush = renderedFrame(.bottomLeftFlush, screen: screen)
    precondition(flush.minX == screen.frame.minX)
    precondition(flush.minY == screen.frame.minY)

    let inset = renderedFrame(.bottomLeftInset, screen: screen)
    precondition(inset.minX == screen.frame.minX + 12)
    precondition(inset.minY == screen.frame.minY + 12)

    let workArea = renderedFrame(.bottomRightWorkArea, screen: screen)
    precondition(workArea.maxX == screen.visibleFrame.maxX - 12)
    precondition(workArea.minY == screen.visibleFrame.minY + 12)
}
```

In the same test, render `.bottomRightWorkArea` against these two additional `visibleFrame`
values and assert `panel.frame.maxX == visibleFrame.maxX - 12` and
`panel.frame.minY == visibleFrame.minY + 12` in both cases:

```swift
for visibleFrame in [
    NSRect(x: -1140, y: -200, width: 1140, height: 800), // Dock on left
    NSRect(x: -1200, y: -200, width: 1140, height: 800), // Dock on right
] {
    let dockScreen = OverlayScreenGeometry(
        id: 1,
        frame: screen.frame,
        visibleFrame: visibleFrame
    )
    let frame = renderedFrame(.bottomRightWorkArea, screen: dockScreen)
    precondition(frame.maxX == visibleFrame.maxX - 12)
    precondition(frame.minY == visibleFrame.minY + 12)
}
```

This proves that work-area anchoring follows `visibleFrame` when the Dock consumes either
horizontal edge, not only when it consumes the bottom edge.

Add this exact expansion-direction test by extending `reasonCommand` with a placement argument
whose default remains `.bottomRightWorkArea`:

```swift
private static func reasonCommand(
    trigger: OverlayReasonTrigger,
    style: IndicatorStyle,
    reasonDisplay: String = "hybrid",
    placement: IndicatorPlacement = .bottomRightWorkArea
) -> OverlayCommand {
    OverlayCommand(
        generation: 20,
        state: .protected,
        style: style,
        displays: [
            OverlayDisplay(
                id: 1,
                left: 0,
                top: 0,
                width: 800,
                height: 600,
                reasons: [
                    OverlayReason(
                        code: "window_title_rule",
                        displayID: 1,
                        sourceDisplayID: nil,
                        appName: "Edge",
                        bundleID: "com.microsoft.edgemac",
                        windowTitle: "InPrivate",
                        rule: "InPrivate"
                    )
                ]
            )
        ],
        allDisplays: false,
        placement: placement,
        reasonDisplay: reasonDisplay,
        reasonDetail: "exact",
        reasonTrigger: trigger,
        reasons: []
    )
}
```

```swift
private static func testCompactExpansionKeepsPlacementAnchor() {
    for placement in [
        IndicatorPlacement.bottomLeftFlush,
        .bottomLeftInset,
        .bottomRightWorkArea,
    ] {
        let screen = OverlayScreenGeometry(
            id: 1,
            frame: NSRect(x: 0, y: 0, width: 800, height: 600),
            visibleFrame: NSRect(x: 0, y: 60, width: 800, height: 540)
        )
        let panel = RecordingPanel(contentRect: .zero)
        var pointer = NSPoint(x: -100, y: -100)
        var tick: (() -> Void)?
        let controller = PrivacyOverlayController(
            screenProvider: { [screen] },
            panelFactory: { panel },
            pointerProvider: { pointer },
            timerFactory: { _, handler in tick = handler; return {} }
        )
        controller.apply(
            reasonCommand(trigger: .hover, style: .pill, placement: placement)
        ) { precondition($0) }
        let compact = panel.frame
        pointer = NSPoint(x: compact.midX, y: compact.midY)
        tick?()
        let expanded = panel.frame

        precondition(expanded.minY == compact.minY)
        if placement.isLeading {
            precondition(expanded.minX == compact.minX)
            precondition(expanded.maxX > compact.maxX)
        } else {
            precondition(expanded.maxX == compact.maxX)
            precondition(expanded.minX < compact.minX)
        }
    }
}
```

Add this border hit-target test, reusing the existing `RecordingPanel` and `reasonCommand` helpers:

```swift
private static func testBorderHitTargetFollowsPlacement() {
    let screen = OverlayScreenGeometry(
        id: 1,
        frame: NSRect(x: 0, y: 0, width: 800, height: 600),
        visibleFrame: NSRect(x: 0, y: 60, width: 800, height: 540)
    )
    for placement in [
        IndicatorPlacement.bottomLeftFlush,
        .bottomRightWorkArea,
    ] {
        let visualPanel = RecordingPanel(contentRect: .zero)
        let inputPanel = RecordingPanel(contentRect: .zero)
        let controller = PrivacyOverlayController(
            screenProvider: { [screen] },
            panelFactory: { visualPanel },
            inputPanelFactory: { inputPanel },
            pointerProvider: { NSPoint(x: 400, y: 300) },
            timerFactory: { _, _ in {} }
        )
        controller.apply(
            reasonCommand(trigger: .click, style: .border, placement: placement)
        ) { precondition($0) }

        precondition(visualPanel.frame == screen.frame)
        let compact = inputPanel.frame
        if placement.isLeading {
            precondition(compact.minX == screen.frame.minX)
            precondition(compact.minY == screen.frame.minY)
        } else {
            precondition(compact.maxX == screen.visibleFrame.maxX - 12)
            precondition(compact.minY == screen.visibleFrame.minY + 12)
        }

        let event = NSEvent.mouseEvent(
            with: .leftMouseDown,
            location: NSPoint(x: compact.width / 2, y: compact.height / 2),
            modifierFlags: [],
            timestamp: 0,
            windowNumber: inputPanel.windowNumber,
            context: nil,
            eventNumber: 1,
            clickCount: 1,
            pressure: 1
        )!
        inputPanel.contentView?.mouseDown(with: event)
        let expanded = inputPanel.frame
        precondition(expanded.minY == compact.minY)
        if placement.isLeading {
            precondition(expanded.minX == compact.minX)
        } else {
            precondition(expanded.maxX == compact.maxX)
        }
    }
}
```

Pass `.bottomLeftFlush` to the existing banner transition and assert its visual panel is still
exactly `screen.frame`. Use an inactive/off command with the same placement and assert all panels
close, preserving the existing `orderOutCount` checks.

- [ ] **Step 6: Compile and run the Swift core tests to verify RED**

Run:

```bash
swiftc resources/mac-privacy-overlay-reason.swift resources/mac-privacy-overlay-core.swift tests/swift/MacPrivacyOverlayCoreTests.swift -o /tmp/openchronicle-overlay-core-tests -framework AppKit
/tmp/openchronicle-overlay-core-tests
```

Expected: compilation fails because `IndicatorPlacement`, the command field, and initializer argument do not exist.

- [ ] **Step 7: Implement native placement decoding and geometry**

Add the enum near `IndicatorStyle`:

```swift
enum IndicatorPlacement: String, Codable {
    case bottomLeftFlush = "bottom-left-flush"
    case bottomLeftInset = "bottom-left-inset"
    case bottomRightWorkArea = "bottom-right-work-area"

    var isLeading: Bool { self != .bottomRightWorkArea }
    var inset: CGFloat { self == .bottomLeftFlush ? 0 : 12 }
}
```

Add `placement` to `OverlayCommand`, its initializer, and coding keys. Preserve old test/programmatic behavior with:

```swift
placement: IndicatorPlacement = .bottomRightWorkArea
```

Decode a missing field with:

```swift
placement = try container.decodeIfPresent(
    IndicatorPlacement.self,
    forKey: .placement
) ?? .bottomRightWorkArea
```

Pass placement through `IndicatorView.init` and `update`. Add a read-only layout property so the controller never introspects private state through reflection:

```swift
var placementForLayout: IndicatorPlacement { placement }
```

Implement compact panel placement in `panelFrame(for:view:)`:

```swift
switch view.placementForLayout {
case .bottomLeftFlush:
    return NSRect(x: display.frame.minX, y: display.frame.minY,
                  width: size.width, height: size.height)
case .bottomLeftInset:
    return NSRect(x: display.frame.minX + 12, y: display.frame.minY + 12,
                  width: size.width, height: size.height)
case .bottomRightWorkArea:
    return NSRect(x: display.visibleFrame.maxX - size.width - 12,
                  y: display.visibleFrame.minY + 12,
                  width: size.width, height: size.height)
}
```

For compact styles, make `statusRect` use `container.minX` when `placement.isLeading` and `container.maxX - size.width` otherwise. Keep the bottom at `container.minY`.

For `border`, compute a display-local anchor rectangle before drawing/hit testing:

```swift
private func localAnchorRect(
    display: OverlayScreenGeometry,
    panelFrame: NSRect,
    placement: IndicatorPlacement
) -> NSRect {
    let global: NSRect
    switch placement {
    case .bottomLeftFlush:
        global = display.frame
    case .bottomLeftInset:
        global = display.frame.insetBy(dx: 12, dy: 12)
    case .bottomRightWorkArea:
        global = display.visibleFrame.insetBy(dx: 12, dy: 12)
    }
    return global.offsetBy(dx: -panelFrame.minX, dy: -panelFrame.minY)
}
```

Store that rectangle on `IndicatorView` during `layoutPanel`. Use its `minX/minY` for left border badges and `maxX/minY` for right border badges. Align the border reason box to the same side. Leave banner geometry unchanged.

When the panel expands, the left panel's global `minX/minY` must remain stable; the right panel's global `maxX/minY` must remain stable. Continue deriving the separate input panel from the final visual panel plus `hitTargetRect`.

- [ ] **Step 8: Run native tests and perform mutation checks**

Run:

```bash
swiftc resources/mac-privacy-overlay-reason.swift resources/mac-privacy-overlay-core.swift tests/swift/MacPrivacyOverlayCoreTests.swift -o /tmp/openchronicle-overlay-core-tests -framework AppKit
/tmp/openchronicle-overlay-core-tests
```

Expected: `MacPrivacyOverlayCoreTests passed`.

Then temporarily change the flush panel origin to `display.frame.minX + 1`, run the binary test, and confirm the flush assertion fails. Restore `display.frame.minX`, rerun, and confirm it passes. Repeat once by changing the decoder fallback to `.bottomLeftFlush`; confirm the missing-field compatibility assertion fails, then restore `.bottomRightWorkArea`.

- [ ] **Step 9: Run the complete Task 2 test slice**

Run:

```bash
uv run pytest tests/test_privacy_overlay.py tests/test_protection_monitor.py tests/test_capture_scheduler_fts.py -q
uv run ruff check src/openchronicle/capture/privacy_overlay.py tests/test_privacy_overlay.py
```

Expected: Python tests pass, Ruff is clean, and the Swift binary has already passed.

- [ ] **Step 10: Commit Task 2**

```bash
git add src/openchronicle/capture/privacy_overlay.py \
  resources/mac-privacy-overlay-core.swift tests/test_privacy_overlay.py \
  tests/swift/MacPrivacyOverlayCoreTests.swift
git commit -m "feat(macos): add privacy indicator placement layouts"
```

---

### Task 3: macOS Settings Integration

**Files:**
- Create: `macos/OpenChronicleApp/Sources/OpenChronicleApp/Models/PrivacyIndicatorPlacement.swift`
- Modify: `macos/OpenChronicleApp/Sources/OpenChronicleApp/Models/Configuration.swift:53-91,162-225,300-345`
- Modify: `macos/OpenChronicleApp/Sources/OpenChronicleApp/Views/SettingsView.swift:175-220,634-705`
- Create: `macos/OpenChronicleApp/Tests/OpenChronicleAppTests/PrivacyIndicatorPlacementTests.swift`
- Modify: `macos/OpenChronicleApp/Tests/OpenChronicleAppTests/ConfigurationTests.swift`

**Interfaces:**
- Consumes: config values defined in Task 1.
- Produces: `PrivacyIndicatorPlacementOption: String, CaseIterable, Identifiable`.
- Produces: `ConfigurationDraft.privacyIndicatorPlacement: String` and patch path `capture.privacy_indicator_placement`.
- UI rule: the menu is disabled only for `.off` and `.banner`; its stored value is never overwritten by style changes.

- [ ] **Step 1: Add failing option-model tests**

Create `PrivacyIndicatorPlacementTests.swift`:

```swift
import XCTest
@testable import OpenChronicleApp

final class PrivacyIndicatorPlacementTests: XCTestCase {
  func testValuesTitlesAndDefault() {
    XCTAssertEqual(
      PrivacyIndicatorPlacementOption.allCases.map(\.rawValue),
      ["bottom-left-flush", "bottom-left-inset", "bottom-right-work-area"]
    )
    XCTAssertEqual(PrivacyIndicatorPlacementOption.defaultValue, .bottomLeftFlush)
    XCTAssertEqual(PrivacyIndicatorPlacementOption.bottomLeftFlush.title, "左下角贴边")
    XCTAssertEqual(PrivacyIndicatorPlacementOption.bottomLeftInset.title, "左下角留白")
    XCTAssertEqual(PrivacyIndicatorPlacementOption.bottomRightWorkArea.title, "右下角避开 Dock")
  }

  func testStyleAvailability() {
    XCTAssertFalse(PrivacyIndicatorPlacementOption.isEnabled(for: .off))
    XCTAssertFalse(PrivacyIndicatorPlacementOption.isEnabled(for: .banner))
    XCTAssertTrue(PrivacyIndicatorPlacementOption.isEnabled(for: .pill))
    XCTAssertTrue(PrivacyIndicatorPlacementOption.isEnabled(for: .border))
  }
}
```

- [ ] **Step 2: Add failing configuration round-trip tests**

Update the JSON fixture in `ConfigurationTests.swift` to include:

```json
"privacy_indicator_placement": "bottom-left-flush"
```

Extend its draft assertions and change set:

```swift
XCTAssertEqual(original.privacyIndicatorPlacement, "bottom-left-flush")
edited.privacyIndicatorPlacement = "bottom-left-inset"
XCTAssertEqual(
  updates["capture.privacy_indicator_placement"] as? String,
  "bottom-left-inset"
)
```

Add missing/unknown-value assertions:

```swift
XCTAssertEqual(
  try XCTUnwrap(ConfigurationDraft(snapshot: missingPlacement)).privacyIndicatorPlacement,
  PrivacyIndicatorPlacementOption.defaultValue.rawValue
)
XCTAssertEqual(
  try XCTUnwrap(ConfigurationDraft(snapshot: unknownPlacement)).privacyIndicatorPlacement,
  PrivacyIndicatorPlacementOption.defaultValue.rawValue
)
```

- [ ] **Step 3: Run focused Swift package tests and verify RED**

Run:

```bash
swift test --package-path macos/OpenChronicleApp --filter 'PrivacyIndicatorPlacementTests|ConfigurationTests'
```

Expected: compilation fails because the option model and configuration properties do not exist.

- [ ] **Step 4: Implement the option model**

Create `PrivacyIndicatorPlacement.swift`:

```swift
enum PrivacyIndicatorPlacementOption: String, CaseIterable, Identifiable {
  case bottomLeftFlush = "bottom-left-flush"
  case bottomLeftInset = "bottom-left-inset"
  case bottomRightWorkArea = "bottom-right-work-area"

  static let defaultValue: Self = .bottomLeftFlush
  var id: String { rawValue }

  var title: String {
    switch self {
    case .bottomLeftFlush: return "左下角贴边"
    case .bottomLeftInset: return "左下角留白"
    case .bottomRightWorkArea: return "右下角避开 Dock"
    }
  }

  var systemImage: String {
    switch self {
    case .bottomLeftFlush: return "arrow.down.left"
    case .bottomLeftInset: return "arrow.down.left.circle"
    case .bottomRightWorkArea: return "arrow.down.right"
    }
  }

  static func isEnabled(for style: PrivacyIndicatorStyleOption) -> Bool {
    style != .off && style != .banner
  }
}
```

- [ ] **Step 5: Extend the configuration snapshot/draft/patch path**

In `CaptureConfigurationValue`, add optional decode support:

```swift
let privacyIndicatorPlacement: String?
case privacyIndicatorPlacement = "privacy_indicator_placement"
```

In `ConfigurationDraft`, add:

```swift
var privacyIndicatorPlacement: String
```

Initialize it with enum validation:

```swift
privacyIndicatorPlacement = PrivacyIndicatorPlacementOption(
  rawValue: values.capture.privacyIndicatorPlacement ?? ""
)?.rawValue ?? PrivacyIndicatorPlacementOption.defaultValue.rawValue
```

Add this exact diff update after indicator style:

```swift
add(
  &updates,
  "capture.privacy_indicator_placement",
  privacyIndicatorPlacement,
  original.privacyIndicatorPlacement
)
```

- [ ] **Step 6: Add the Settings picker**

Immediately after `PrivacyIndicatorStylePicker`, add:

```swift
Picker(
  "Indicator position",
  selection: binding(
    \.privacyIndicatorPlacement,
    fallback: PrivacyIndicatorPlacementOption.defaultValue.rawValue
  )
) {
  ForEach(PrivacyIndicatorPlacementOption.allCases) { option in
    Label(option.title, systemImage: option.systemImage)
      .tag(option.rawValue)
  }
}
.pickerStyle(.menu)
.disabled(
  !PrivacyIndicatorPlacementOption.isEnabled(
    for: PrivacyIndicatorStyleOption(
      rawValue: configuration.draft?.privacyIndicatorStyle ?? ""
    ) ?? .defaultStyle
  )
)
```

Do not reset `privacyIndicatorPlacement` when the picker becomes disabled.

- [ ] **Step 7: Run focused and full app tests**

Run:

```bash
swift test --package-path macos/OpenChronicleApp --filter 'PrivacyIndicatorPlacementTests|ConfigurationTests'
swift test --package-path macos/OpenChronicleApp
```

Expected: focused tests pass, then the complete app test target passes.

- [ ] **Step 8: Commit Task 3**

```bash
git add macos/OpenChronicleApp/Sources/OpenChronicleApp/Models/PrivacyIndicatorPlacement.swift \
  macos/OpenChronicleApp/Sources/OpenChronicleApp/Models/Configuration.swift \
  macos/OpenChronicleApp/Sources/OpenChronicleApp/Views/SettingsView.swift \
  macos/OpenChronicleApp/Tests/OpenChronicleAppTests/PrivacyIndicatorPlacementTests.swift \
  macos/OpenChronicleApp/Tests/OpenChronicleAppTests/ConfigurationTests.swift
git commit -m "feat(macos): expose indicator placement setting"
```

---

### Task 4: Documentation, Full Verification, and Installed Product

**Files:**
- Modify: `docs/superpowers/specs/2026-08-21-privacy-protection-indicators-design.md`
- Modify: `docs/superpowers/specs/2026-08-21-privacy-protection-indicators-design.zh-CN.md`
- Modify: `docs/superpowers/specs/2026-08-24-privacy-indicator-placement-design.md`
- Verify: all files changed in Tasks 1-3
- Verify: `/Users/tkandi/.openchronicle/config.toml` without printing secrets
- Verify: `/Applications/OpenChronicle.app` and its App-owned process chain

**Interfaces:**
- Consumes: all implementation from Tasks 1-3.
- Produces: installed App/daemon/helper with the three selectable presets.
- Produces: live evidence that hot reload and fail-closed behavior remain intact.

- [ ] **Step 1: Update stale documentation statements**

In both 2026-08-21 indicator specs:

1. Replace lower-right-only default language with the three placement presets.
2. Add the exact `privacy_indicator_placement` TOML key and default.
3. State that compact indicators and border badges follow placement while banner/off do not.
4. Extend the hot-reload statement from style-only to style-and-placement.

Change the 2026-08-24 design status from `已确认，等待书面规格复核` to
`已实现，等待实机验证`. The final verified status is committed only in Step 6 after the live
checks pass.

- [ ] **Step 2: Run all automated verification from a clean diff**

Run:

```bash
git diff --check
uv run ruff check src/openchronicle/config.py src/openchronicle/config_editor.py src/openchronicle/capture/protection.py src/openchronicle/capture/protection_monitor.py src/openchronicle/capture/privacy_overlay.py src/openchronicle/capture/scheduler.py tests/test_config.py tests/test_cli_config_editor.py tests/test_protection.py tests/test_protection_monitor.py tests/test_privacy_overlay.py tests/test_capture_scheduler_fts.py
uv run pytest -q
swiftc resources/mac-privacy-overlay-reason.swift resources/mac-privacy-overlay-core.swift tests/swift/MacPrivacyOverlayCoreTests.swift -o /tmp/openchronicle-overlay-core-tests -framework AppKit
/tmp/openchronicle-overlay-core-tests
swift test --package-path macos/OpenChronicleApp
```

Expected: no whitespace errors; changed-file Ruff clean; the Python suite reports zero failures; native core prints `MacPrivacyOverlayCoreTests passed`; Swift package tests report zero failures.

- [ ] **Step 3: Commit documentation after automated verification**

```bash
git add docs/superpowers/specs/2026-08-21-privacy-protection-indicators-design.md \
  docs/superpowers/specs/2026-08-21-privacy-protection-indicators-design.zh-CN.md \
  docs/superpowers/specs/2026-08-24-privacy-indicator-placement-design.md
git commit -m "docs: document indicator placement presets"
```

- [ ] **Step 4: Preserve config, stop the App-owned chain, and reinstall**

Record only the config hash:

```bash
shasum -a 256 /Users/tkandi/.openchronicle/config.toml
```

Then stop the App and daemon cleanly, verify `OpenChronicle`, `openchronicle start --foreground`, `mac-ax-watcher`, and `mac-privacy-overlay` have exited, and run both installation layers:

```bash
bash install.sh --no-client-config
bash scripts/install-macos-app.sh
```

The first command updates the backend and helper binaries without replacing the client config.
The second command builds, signs, installs, and launches the SwiftUI App bundle in
`/Applications/OpenChronicle.app`.

After startup, recompute the config hash. Expected: it exactly matches the pre-install hash.

- [ ] **Step 5: Verify installed health and process ownership**

Run:

```bash
/Users/tkandi/.local/bin/openchronicle status --json --no-model-checks
pgrep -fl 'OpenChronicle|/Users/tkandi/.openchronicle/venv/bin/openchronicle|mac-ax-watcher|mac-privacy-overlay'
```

Expected:

- App, daemon, one AX watcher, and one overlay helper form one App-owned chain.
- daemon is running, capture is active, and health becomes healthy after a fresh capture.
- No duplicate helper or daemon remains from the previous installation.

- [ ] **Step 6: Perform a safe three-preset live test**

Use only a blank Edge InPrivate window; do not display passwords, tokens, private chats, or real sensitive data.

For each setting in the macOS App:

1. Select `左下角贴边`; within one watchdog cycle confirm the pill touches the physical lower-left edges.
2. Select `左下角留白`; confirm the pill is 12pt from both physical edges.
3. Select `右下角避开 Dock`; confirm the pill returns above the Dock at the work-area lower-right.
4. For each left preset, hover to expand reasons and confirm the badge does not move while the panel grows right/up.
5. Switch to click mode once and verify only the bounded badge/reason hit target consumes clicks.
6. Repeat one left preset on a secondary display if connected, including a display with a negative global origin.

Read post-test logs and the newest safe capture JSON with secrets redacted. Expected:

- every setting change creates a newly confirmed generation;
- no `indicator failed`, helper crash, or capture fail-open transition appears;
- the overlay window ID is excluded from filtered screenshots;
- the blank private test marker does not enter screenshots, AX text, timeline, memory, or model input.

After every live assertion passes, change the 2026-08-24 design status to `已实现并验证` and
commit that evidence boundary:

```bash
git add docs/superpowers/specs/2026-08-24-privacy-indicator-placement-design.md
git commit -m "docs: mark indicator placement verified"
```

- [ ] **Step 7: Re-run the final regression gate after live testing**

Run:

```bash
uv run pytest -q
swift test --package-path macos/OpenChronicleApp
git status --short --branch
```

Expected: all tests remain green and the working tree is clean on the implementation branch.

- [ ] **Step 8: Finish the implementation branch**

Invoke `superpowers:finishing-a-development-branch`. Present the local merge, PR, and keep-branch options. Do not push or merge until the user selects an option.
