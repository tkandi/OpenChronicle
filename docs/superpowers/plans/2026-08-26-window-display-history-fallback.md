# Window Display History Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace avoidable global unmapped failures with a per-display last-known mapping fallback, while making only mapping-uncertainty transients visually silent.

**Architecture:** A new pure `WindowDisplayHistory` enriches `VisibleWindow` values with category-only fallback display IDs before snapshot construction. The pure snapshot builder uses actual geometry first and history only when geometry is unmapped, marks fallback use, and disables window-filtered authorization. The existing presentation smoother renders mapping uncertainty as `off` before 800ms while preserving effective state and capture policy.

**Tech Stack:** Python 3.11+, dataclasses, monotonic time, pytest, Ruff, Swift Codable compatibility tests, AppKit/native helper regression, macOS category diagnostics.

## Global Constraints

- Actual positive-area display intersection always wins over history.
- History keys are valid `window_id` plus non-empty bundle ID, otherwise normalized app name.
- No valid/unique window ID or owner key means no history fallback.
- Continuously present unmapped windows retain history; absent entries expire at exactly 5.0 seconds.
- Owner mismatch, duplicate IDs, removed displays, or monotonic rollback invalidate history safely.
- History fallback effective state is PROTECTED on cached displays, never global FAILED.
- `screenshot_monitor="separate"` leaves unrelated displays available; `all` mode keeps existing all-display semantics.
- Any history use forces `window_filterable=false`; stale region/ID data must never authorize mask/exclude-window capture.
- Cache miss preserves existing mapping FAILED and failure policy.
- Only mapping uncertainty is silent before 800ms: history fallback PROTECTED and allowlisted mapping FAILED.
- Reliably mapped PROTECTED remains transient `quiet-shield`; hard failures and paused remain immediate configured presentation.
- Effective state, screenshot/AX policy, clear acknowledgement, legacy fail-open, and diagnostics guard boundaries remain unchanged.
- No occlusion inference, persisted cache, config option, thread, Timer, private API, or native helper wire field.
- No title, URL, app/bundle, owner key, rule, or cached identity in logs/diagnostics.
- Source specification: `docs/superpowers/specs/2026-08-26-window-display-history-fallback-design.md`.

## File Map

- Modify `src/openchronicle/capture/privacy.py`: keyword-only fallback display IDs on `VisibleWindow`.
- Create `src/openchronicle/capture/window_display_history.py`: pure identity/cache resolver.
- Create `tests/test_window_display_history.py`: deterministic cache tests.
- Modify `src/openchronicle/capture/protection.py`: actual/history mapping helper and fallback snapshot flag.
- Modify `tests/test_protection.py`: pure snapshot fallback behavior.
- Modify `src/openchronicle/capture/protection_monitor.py`: per-monitor history ownership and sanitized failure handling.
- Modify `tests/test_protection_monitor.py`, `tests/test_capture_scheduler_fts.py`: monitor/scheduler integration.
- Modify `src/openchronicle/capture/protection_smoothing.py`, `tests/test_protection_smoothing.py`: silent mapping phases.
- Modify `src/openchronicle/capture/privacy_diagnostics.py`, diagnostics tests, and Swift diagnostics model/tests: additive fallback category flag.
- Modify `docs/capture.md`, `docs/macos-app.md`, and the design status.

---

### Task 1: Pure Window Display History

**Files:**
- Modify: `src/openchronicle/capture/privacy.py`
- Create: `src/openchronicle/capture/window_display_history.py`
- Create: `tests/test_window_display_history.py`

**Interfaces:**
- Produces: `WINDOW_DISPLAY_HISTORY_ABSENCE_SECONDS: float = 5.0`.
- Produces: `WindowDisplayHistory.resolve(inventory, *, now) -> WindowInventory` and `reset()`.
- Produces: `WindowDisplayHistoryError(RuntimeError)` for monotonic rollback.
- Adds: `VisibleWindow.fallback_display_ids: frozenset[int]` as keyword-only, default empty.

- [ ] **Step 1: Add the keyword-only field and test factories**

Use `dataclasses.field` in `privacy.py`:

```python
fallback_display_ids: frozenset[int] = field(
    default_factory=frozenset,
    kw_only=True,
)
```

Create test constants and factories:

```python
DISPLAY_1 = DisplayInfo(1, ScreenRegion(0, 0, 100, 100), True)
DISPLAY_2 = DisplayInfo(2, ScreenRegion(100, 0, 100, 100), False)


def _window(
    window_id: int | None,
    region: ScreenRegion,
    *,
    app_name: str = "Edge",
    bundle_id: str = "com.microsoft.edgemac",
) -> VisibleWindow:
    return VisibleWindow(
        app_name,
        bundle_id,
        "InPrivate",
        region,
        window_id=window_id,
    )


def _inventory(*windows: VisibleWindow, displays=(DISPLAY_1, DISPLAY_2)) -> WindowInventory:
    return WindowInventory(windows=windows, displays=displays)
```

- [ ] **Step 2: Write actual-map and continuous-unmapped RED tests**

```python
def test_actual_mapping_populates_history_then_unmapped_uses_it() -> None:
    history = WindowDisplayHistory()
    mapped = history.resolve(
        _inventory(_window(41, ScreenRegion(10, 10, 50, 50))),
        now=10.0,
    )
    fallback = history.resolve(
        _inventory(_window(41, ScreenRegion(5000, 5000, 50, 50))),
        now=10.1,
    )
    assert mapped.windows[0].fallback_display_ids == frozenset()
    assert fallback.windows[0].fallback_display_ids == frozenset({1})


def test_continuously_present_unmapped_window_does_not_expire() -> None:
    history = WindowDisplayHistory()
    history.resolve(_inventory(_window(41, ScreenRegion(10, 10, 50, 50))), now=0.0)
    first = history.resolve(
        _inventory(_window(41, ScreenRegion(5000, 5000, 50, 50))),
        now=100.0,
    )
    later = history.resolve(
        _inventory(_window(41, ScreenRegion(5000, 5000, 50, 50))),
        now=10_000.0,
    )
    assert first.windows[0].fallback_display_ids == frozenset({1})
    assert later.windows[0].fallback_display_ids == frozenset({1})
```

- [ ] **Step 3: Write absence TTL RED tests**

```python
def test_absent_entry_is_reusable_before_five_seconds() -> None:
    history = WindowDisplayHistory()
    history.resolve(_inventory(_window(41, ScreenRegion(10, 10, 50, 50))), now=0.0)
    history.resolve(_inventory(), now=4.999)
    result = history.resolve(
        _inventory(_window(41, ScreenRegion(5000, 5000, 50, 50))),
        now=4.999,
    )
    assert result.windows[0].fallback_display_ids == frozenset({1})


def test_absent_entry_expires_at_exactly_five_seconds() -> None:
    history = WindowDisplayHistory()
    history.resolve(_inventory(_window(41, ScreenRegion(10, 10, 50, 50))), now=0.0)
    history.resolve(_inventory(), now=5.0)
    result = history.resolve(
        _inventory(_window(41, ScreenRegion(5000, 5000, 50, 50))),
        now=5.0,
    )
    assert result.windows[0].fallback_display_ids == frozenset()
```

- [ ] **Step 4: Write identity/display safety RED tests**

Add independent tests asserting:

```python
def test_owner_mismatch_rejects_cached_mapping() -> None:
    history = WindowDisplayHistory()
    history.resolve(_inventory(_window(41, ScreenRegion(10, 10, 50, 50))), now=1.0)
    changed = history.resolve(
        _inventory(
            _window(
                41,
                ScreenRegion(5000, 5000, 50, 50),
                app_name="Other",
                bundle_id="com.example.other",
            )
        ),
        now=1.1,
    )
    assert changed.windows[0].fallback_display_ids == frozenset()


@pytest.mark.parametrize("window_id", [None, 0, -1, True, 0x1_0000_0000])
def test_invalid_window_ids_never_use_history(window_id) -> None:
    history = WindowDisplayHistory()
    result = history.resolve(
        _inventory(_window(window_id, ScreenRegion(5000, 5000, 50, 50))),
        now=1.0,
    )
    assert result.windows[0].fallback_display_ids == frozenset()


def test_duplicate_window_id_invalidates_history_for_both_windows() -> None:
    history = WindowDisplayHistory()
    history.resolve(_inventory(_window(41, ScreenRegion(10, 10, 50, 50))), now=1.0)
    result = history.resolve(
        _inventory(
            _window(41, ScreenRegion(5000, 5000, 50, 50)),
            _window(41, ScreenRegion(6000, 6000, 50, 50)),
        ),
        now=1.1,
    )
    assert all(not window.fallback_display_ids for window in result.windows)
```

Add tests for display removal and a window spanning both displays. Actual geometry on display 2 must overwrite cached display 1.

- [ ] **Step 5: Write clock/reset RED tests**

```python
def test_equal_monotonic_is_allowed_but_rollback_is_rejected() -> None:
    history = WindowDisplayHistory()
    history.resolve(_inventory(), now=10.0)
    history.resolve(_inventory(), now=10.0)
    with pytest.raises(WindowDisplayHistoryError, match="monotonic"):
        history.resolve(_inventory(), now=9.999)


def test_reset_removes_cached_mapping_and_clock_memory() -> None:
    history = WindowDisplayHistory()
    history.resolve(_inventory(_window(41, ScreenRegion(10, 10, 50, 50))), now=10.0)
    history.reset()
    result = history.resolve(
        _inventory(_window(41, ScreenRegion(5000, 5000, 50, 50))),
        now=1.0,
    )
    assert result.windows[0].fallback_display_ids == frozenset()
```

- [ ] **Step 6: Run tests and verify RED**

```bash
PYTHONPATH=src uv run pytest -q tests/test_window_display_history.py
```

Expected: module/class/constants do not exist.

- [ ] **Step 7: Implement the pure resolver**

Use private dataclasses `_WindowIdentity` and `_HistoryEntry`. Owner keys are stripped/casefolded bundle IDs, otherwise stripped/casefolded app names. Valid IDs are non-bool integers from `1` through `0xFFFFFFFF`.

`resolve()` must:

1. reject only `now < previous_now`;
2. compute duplicate valid IDs before processing windows;
3. remove duplicate-ID entries;
4. intersect stored display IDs with active display IDs;
5. prefer actual positive-area intersection;
6. use matching history only when actual IDs are empty;
7. update `last_seen_monotonic` for mapped and continuously present fallback windows;
8. remove absent entries when `now - last_seen_monotonic >= 5.0`;
9. return a new immutable inventory via `dataclasses.replace`.

- [ ] **Step 8: Run tests/Ruff/mutations and commit**

```bash
PYTHONPATH=src uv run pytest -q tests/test_window_display_history.py tests/test_capture_privacy.py
uv run ruff check src/openchronicle/capture/privacy.py \
  src/openchronicle/capture/window_display_history.py \
  tests/test_window_display_history.py
```

Mutations must be killed: use history before actual mapping; expire continuous presence; allow duplicate IDs; change `>=5.0` to `>5.0`.

```bash
git add src/openchronicle/capture/privacy.py \
  src/openchronicle/capture/window_display_history.py \
  tests/test_window_display_history.py
git commit -m "feat(capture): track window display history"
```

---

### Task 2: Snapshot Builder History Fallback

**Files:**
- Modify: `src/openchronicle/capture/protection.py`
- Modify: `tests/test_protection.py`

**Interfaces:**
- Consumes: `VisibleWindow.fallback_display_ids`.
- Adds: keyword-only `ProtectionSnapshot.display_mapping_fallback_active: bool = False`.
- Produces: fallback PROTECTED snapshots with `window_filterable=false`.

- [ ] **Step 1: Add fallback builder RED tests**

Construct an off-display sensitive Edge window with `fallback_display_ids=frozenset({1})` and valid window ID. Assert:

```python
assert snapshot.state is ProtectionState.PROTECTED
assert snapshot.failure_reason is None
assert snapshot.protected_display_ids == frozenset({1})
assert snapshot.display_mapping_fallback_active is True
assert snapshot.window_filterable is False
assert [r.display_id for r in snapshot.display_reasons.reasons] == [1]
```

The same window without fallback must remain `FAILED/SENSITIVE_WINDOW_UNMAPPED`.

- [ ] **Step 2: Add actual-over-history and all-mode tests**

Use a window actually intersecting display 2 but carrying stale fallback `{1}`. Assert only display 2 is protected and fallback flag is false. Under `screenshot_monitor="all"`, fallback on display 1 must protect all active display IDs while still marking fallback active and non-filterable.

- [ ] **Step 3: Add active window/candidate fallback tests**

For one active window with no actual intersection and exactly one fallback ID, assert `active_display_id` is that ID. For multiple fallback IDs, assert `active_display_id is None` and `active_candidate_display_ids` contains all fallback IDs, so `ax_blocked` remains conservative.

- [ ] **Step 4: Add hard guard and filtered-boundary tests**

Build the same fallback inventory with `diagnostics_guard_invalid=True`; assert FAILED/BYPASS prerequisites remain and fallback does not weaken the hard guard. Assert protected window IDs/regions may remain diagnostic metadata, but `window_filterable` is false whenever any direct sensitive mapping used history.

- [ ] **Step 5: Run RED**

```bash
PYTHONPATH=src uv run pytest -q tests/test_protection.py
```

Expected: builder ignores fallback IDs and snapshot lacks the flag.

- [ ] **Step 6: Implement one mapping helper**

Add `_display_mapping_for_window(window, displays) -> tuple[frozenset[int], bool]`. Return actual positive intersections with `False`; only if empty, return valid fallback IDs with `True`.

Use it consistently for active window, active candidates, sensitive matching, unmapped detection, and reason display IDs. A single fallback active display becomes `active_display_id`; multiple become candidates. Track whether protection-relevant mapping used history and populate `display_mapping_fallback_active`.

Set `window_filterable` false when any direct matched window used fallback.

- [ ] **Step 7: Run regression/Ruff/mutations and commit**

```bash
PYTHONPATH=src uv run pytest -q tests/test_protection.py tests/test_protection_reason.py
uv run ruff check src/openchronicle/capture/protection.py tests/test_protection.py
```

Mutations: prefer fallback over actual; omit fallback flag; leave filterable true; let guard invalid become PROTECTED.

```bash
git add src/openchronicle/capture/protection.py tests/test_protection.py
git commit -m "feat(capture): resolve snapshots from display history"
```

---

### Task 3: Monitor and Scheduler Integration

**Files:**
- Modify: `src/openchronicle/capture/protection_monitor.py`
- Modify: `tests/test_protection_monitor.py`
- Modify: `tests/test_capture_scheduler_fts.py`

**Interfaces:**
- Consumes: `WindowDisplayHistory` and fallback snapshot flag.
- Adds constructor dependency: `window_display_history: WindowDisplayHistory | None = None`.
- Preserves one monitor thread/Event and current capture policy.

- [ ] **Step 1: Add mapped-to-unmapped monitor RED test**

Use the same valid window ID/owner in two inventories: first intersects display 1, second is off-display. With injected clock, call force refresh twice and assert the second decision:

```python
assert decision.raw_state is ProtectionState.PROTECTED
assert decision.snapshot.state is ProtectionState.PROTECTED
assert decision.snapshot.failure_reason is None
assert decision.snapshot.protected_display_ids == frozenset({1})
assert decision.snapshot.display_mapping_fallback_active is True
assert 2 not in decision.snapshot.protected_display_ids
assert decision.snapshot.window_filterable is False
```

- [ ] **Step 2: Add identity miss/TTL/error tests**

- missing window ID remains mapping FAILED;
- owner mismatch remains mapping FAILED;
- absent less than 5 seconds can reuse history on return;
- absent at 5 seconds cannot;
- injected `WindowDisplayHistoryError("private-marker")` resets history and publishes sanitized `PRESENTATION_STATE_INVALID`, with marker absent from logs;
- `stop()` resets history and performs no late callback.

- [ ] **Step 3: Add real monitor-to-scheduler per-display test**

Use `screenshot_monitor="separate"`, mapped then fallback inventory, and screenshot/AX fakes. Assert the protected monitor is not captured, the unrelated monitor is captured, AX follows the resolved active display, filtered-window helper is not called, and authorization revalidation rejects stale pre-fallback results.

- [ ] **Step 4: Run RED**

```bash
PYTHONPATH=src uv run pytest -q \
  tests/test_protection_monitor.py tests/test_capture_scheduler_fts.py
```

Expected: second inventory produces mapping FAILED/global blocking because history is not integrated.

- [ ] **Step 5: Integrate history before snapshot construction**

Monitor owns one history instance. After a successful inventory read and before `build_protection_snapshot`, call `resolve(inventory, now=now)` using the same injected monotonic value. Do this for normal and diagnostics-guard monitor paths with inventory.

Catch only `WindowDisplayHistoryError`: log fixed type name, reset history, and publish the existing sanitized `PRESENTATION_STATE_INVALID` hard failure. Never log exception text. Reset history during stop.

- [ ] **Step 6: Run regression/Ruff/mutations and commit**

```bash
PYTHONPATH=src uv run pytest -q \
  tests/test_window_display_history.py tests/test_protection.py \
  tests/test_protection_monitor.py tests/test_capture_scheduler_fts.py
uv run ruff check src/openchronicle/capture/protection_monitor.py \
  tests/test_protection_monitor.py tests/test_capture_scheduler_fts.py
```

Mutations: skip history resolve; reuse history after owner mismatch; allow filtered capture; log exception body.

```bash
git add src/openchronicle/capture/protection_monitor.py \
  tests/test_protection_monitor.py tests/test_capture_scheduler_fts.py
git commit -m "feat(capture): apply per-display history fallback"
```

---

### Task 4: Silent Mapping Presentation and Diagnostics

**Files:**
- Modify: `src/openchronicle/capture/protection_smoothing.py`
- Modify: `tests/test_protection_smoothing.py`
- Modify: `src/openchronicle/capture/privacy_diagnostics.py`
- Modify: `tests/test_privacy_diagnostics.py`
- Modify: Swift diagnostics model/tests
- Modify: `docs/capture.md`, `docs/macos-app.md`, design status

**Interfaces:**
- Adds phases `transient-mapping-fallback` and `sustained-mapping-fallback`.
- Adds diagnostics field `display_mapping_fallback_active` as optional/default-safe in Swift.
- Preserves native overlay protocol.

- [ ] **Step 1: Add presentation RED tests**

Assert normal mapped PROTECTED before 800ms remains `quiet-shield`. Assert PROTECTED with fallback flag becomes `transient-mapping-fallback`, style `off`, reasons false; at 800ms becomes `sustained-mapping-fallback` with configured style/reasons true.

Update mapping FAILED transient expectations from `quiet-shield` to `off`, preserving FAILED state, blocked policy, phase and deadline. Add normal/fallback/FAILED cross-state tests proving one shared timer.

- [ ] **Step 2: Add clear/off/hard-failure tests**

Clear-pending must hold the fallback flag and silent style when the episode is still transient. Configured style `off` remains off after promotion. Hard failures/paused remain immediate configured presentation and must not inherit a silent mapping phase.

- [ ] **Step 3: Add diagnostics RED tests**

Category payload for fallback decisions must include `display_mapping_fallback_active=true`, mapping-fallback phase, style and blocked booleans. Normal snapshots publish false. Place private markers in exact reasons and confirm category JSON remains marker-free. Swift old payload decodes nil/false-safe; new payload decodes true.

- [ ] **Step 4: Run RED**

```bash
PYTHONPATH=src uv run pytest -q \
  tests/test_protection_smoothing.py tests/test_privacy_diagnostics.py
```

- [ ] **Step 5: Implement silent mapping-only phases**

Define mapping uncertainty as allowlisted mapping FAILED or PROTECTED with fallback flag. Before promotion, effective style is `off` and reasons disabled. Normal PROTECTED keeps `quiet-shield`. After promotion, select mapping-fallback/mapping-failure/protected sustained phase with configured style.

Serialize the fallback flag as category-only. Update Swift optional decode without changing visible UI or schema version.

- [ ] **Step 6: Update docs/design status**

Document actual > history > failure precedence, separate/all behavior, no occlusion inference, mapping-only silence, cache-miss fallback, 5-second absence TTL, disabled window filtering, and diagnostics field. Set status `已实现，等待实机验证`.

- [ ] **Step 7: Run regressions/Ruff/Swift/mutations and commit**

```bash
PYTHONPATH=src uv run pytest -q \
  tests/test_protection_smoothing.py tests/test_protection_monitor.py \
  tests/test_privacy_diagnostics.py tests/test_capture_scheduler_fts.py
uv run ruff check src/openchronicle/capture/protection_smoothing.py \
  src/openchronicle/capture/privacy_diagnostics.py \
  tests/test_protection_smoothing.py tests/test_privacy_diagnostics.py
CLANG_MODULE_CACHE_PATH=/tmp/openchronicle-clang-module-cache \
SWIFTPM_MODULECACHE_OVERRIDE=/tmp/openchronicle-swiftpm-module-cache \
swift test --package-path macos/OpenChronicleApp
```

Mutations: fallback transient quiet instead of off; normal transient off; omit diagnostics flag; reset timer across fallback transitions.

```bash
git add src/openchronicle/capture/protection_smoothing.py \
  src/openchronicle/capture/privacy_diagnostics.py \
  tests/test_protection_smoothing.py tests/test_privacy_diagnostics.py \
  macos/OpenChronicleApp/Sources/OpenChronicleApp/Models/ProtectionDiagnostics.swift \
  macos/OpenChronicleApp/Tests/OpenChronicleAppTests/PrivacyDiagnosticsControllerTests.swift \
  docs/capture.md docs/macos-app.md \
  docs/superpowers/specs/2026-08-26-window-display-history-fallback-design.md
git commit -m "feat(capture): silence transient mapping uncertainty"
```

---

### Task 5: Full Gate, Install, and Fullscreen/Dual-Display Verification

**Files:**
- Modify after verification: design status/evidence only
- Verify: user config hashes, installed package, App signature/process chain

- [ ] **Step 1: Run complete automated gate**

```bash
git diff --check
PYTHONPATH=src uv run pytest -q
uv run ruff check src tests
swiftc -module-cache-path /tmp/openchronicle-swift-module-cache \
  resources/mac-privacy-overlay-reason.swift resources/mac-privacy-overlay-core.swift \
  tests/swift/MacPrivacyOverlayCoreTests.swift \
  -o /tmp/openchronicle-overlay-core-history -framework AppKit
/tmp/openchronicle-overlay-core-history
CLANG_MODULE_CACHE_PATH=/tmp/openchronicle-clang-module-cache \
SWIFTPM_MODULECACHE_OVERRIDE=/tmp/openchronicle-swiftpm-module-cache \
swift test --package-path macos/OpenChronicleApp
```

Full Python/native/Swift must pass; changed files Ruff clean. Record only existing unrelated full-Ruff findings.

- [ ] **Step 2: Preserve config and install both layers**

Record raw/canonical config SHA-256 without values. Quit the old App/process chain, install with:

```bash
bash install.sh --no-client-config
bash scripts/install-macos-app.sh
```

Hashes must match. Verify signatures and exactly one App -> daemon -> overlay + AX watcher chain.

- [ ] **Step 3: Fullscreen history-fallback live test**

Use only blank InPrivate:

1. Map it reliably on display 1 and record window ID/display protection.
2. Put a normal window fullscreen over it.
3. If current geometry becomes unmapped, require effective PROTECTED with fallback flag true, only display 1 blocked, display 2 inactive, and no global FAILED.
4. Verify display 2 continues capture structurally; do not decode content.
5. Verify fallback `window_filterable=false` and no filtered helper authorization.

- [ ] **Step 4: Visual transition live test**

Perform fast Space/F3 transitions. During mapping uncertainty before 800ms require style `off`, no overlay window IDs and no visible shield/pill. Reliably mapped visible InPrivate/Stage Manager thumbnail must still show normal quiet-shield. Persistent uncertainty after 800ms must show configured full warning/protection.

Repeat on secondary display and during cross-display movement. Record only category fields, source deadlines, blocked booleans, monitor IDs, fixed logs and process IDs.

- [ ] **Step 5: Capture artifact structural verification**

Confirm protected monitor omission and `ax_skipped=protected_display` where applicable, while unprotected monitor image presence continues. Never decode/display image, AX, title, URL, rule or exact content.

- [ ] **Step 6: Mark verified and run final gate**

Set design status `已实现并验证`, commit evidence, then rerun full Python, full Swift and git status. Invoke `superpowers:finishing-a-development-branch`; do not merge/push without user choice.
