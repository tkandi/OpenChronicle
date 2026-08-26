# Inventory Preflight and Title Uncertainty Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent invalid display inventories from mutating trusted window history and suppress the first 800ms of overlay presentation when protection is caused only by temporarily unavailable window titles.

**Architecture:** A shared pure preflight in `capture/privacy.py` becomes the single structural trust boundary used by the helper reader, monitor, history resolver, and snapshot builder. Presentation classifies `WINDOW_TITLE_UNKNOWN`-only PROTECTED snapshots independently from direct privacy matches, changing only overlay style/phase while leaving screenshot and AX policy untouched.

**Tech Stack:** Python 3.11+, pytest, Ruff, Swift diagnostics compatibility tests, macOS AppKit overlay helper, owner-only Unix-socket category diagnostics.

## Global Constraints

- Do not add Mission Control/F3/Space detection, private APIs, settings, timers, threads, or dependencies.
- Invalid inventory samples must not seed, overwrite, refresh, expire, clear, or migrate display history.
- Valid display IDs are non-boolean integers in `1...UInt32.max`; display bounds are finite with positive width and height.
- A legal history cache miss remains globally fail-closed; never guess a destination display.
- Title uncertainty changes presentation only. Screenshot and AX blocking begin on the first protected frame.
- Normal app, bundle, known-title, diagnostics, pause, and hard-failure matches retain their current presentation and policy.
- Category diagnostics must not expose window IDs, owners, titles, URLs, rules, bounds, cache timestamps, or exception text.
- Preserve schema version 1 and the native overlay protocol.
- Use TDD, manual mutation checks, one focused commit per task, and an independent review after each task.

---

### Task 1: Shared Inventory Preflight and Transactional History

**Files:**
- Modify: `src/openchronicle/capture/privacy.py`
- Modify: `src/openchronicle/capture/protection.py`
- Modify: `src/openchronicle/capture/protection_monitor.py`
- Modify: `src/openchronicle/capture/window_display_history.py`
- Modify: `tests/test_capture_privacy.py`
- Modify: `tests/test_protection.py`
- Modify: `tests/test_protection_monitor.py`
- Modify: `tests/test_window_display_history.py`

**Interfaces:**
- Produces: `inventory_structure_failure_reason(inventory: WindowInventory | None) -> ProtectionFailureReason | None` in `privacy.py`.
- Consumes: existing `InventoryReadResult`, `WindowInventory`, `ProtectionFailureReason`, and `WindowDisplayHistory.resolve(inventory, *, now)`.
- Guarantees: history entries change only after the shared preflight returns `None`.

- [ ] **Step 1: Add RED tests for the pure preflight and parser**

Add table-driven tests covering `None`, empty displays, duplicate IDs, `True`, `0`, negative and greater-than-`UInt32.max` IDs, NaN/infinite/non-positive bounds, multiple active windows, and a valid two-display inventory.

```python
@pytest.mark.parametrize(
    ("inventory", "expected"),
    [
        (None, ProtectionFailureReason.INVENTORY_UNAVAILABLE),
        (WindowInventory(windows=(), displays=()), ProtectionFailureReason.EMPTY_DISPLAYS),
        (
            WindowInventory(windows=(), displays=(DISPLAY_1, replace(DISPLAY_1))),
            ProtectionFailureReason.INVALID_DISPLAY_INVENTORY,
        ),
    ],
)
def test_inventory_structure_failure_reason(inventory, expected):
    assert privacy.inventory_structure_failure_reason(inventory) is expected
```

Patch helper output with duplicate display IDs and assert `read_window_inventory_result()` returns `inventory=None` and `INVALID_DISPLAY_INVENTORY`.

- [ ] **Step 2: Add RED stateful history/monitor regressions**

Use the exact adversarial sequence from final review:

```python
seed_display_1 = _history_inventory(ScreenRegion(10, 0, 80, 90))
invalid_display_2 = replace(
    _history_inventory(ScreenRegion(110, 0, 80, 90)),
    displays=(DISPLAY_2, DISPLAY_2),
)
unmapped = _history_inventory(ScreenRegion(5000, 0, 80, 90))

seed = monitor.decision_for_capture(force=True)
failed = monitor.decision_for_capture(force=True)
after = monitor.decision_for_capture(force=True)

assert seed.snapshot.protected_display_ids == frozenset({1})
assert failed.snapshot.failure_reason is ProtectionFailureReason.INVALID_DISPLAY_INVENTORY
assert after.snapshot.display_mapping_fallback_active is True
assert after.snapshot.protected_display_ids == frozenset({1})
```

Add separate tests proving invalid samples cannot seed, overwrite, refresh a 5-second absence TTL, expire, or clear existing history. Add direct-resolver tests for empty displays, invalid bounds and multiple-active inventories. Add a display-removal lifecycle assertion that the entry is deleted when its display-ID intersection becomes empty.

- [ ] **Step 3: Run RED selectors**

```bash
PYTHONPATH=src uv run pytest -q \
  tests/test_capture_privacy.py tests/test_window_display_history.py \
  tests/test_protection.py tests/test_protection_monitor.py \
  -k 'inventory_structure or invalid_inventory or display_removal'
```

Expected: failures because the shared function is missing and invalid samples currently overwrite history.

- [ ] **Step 4: Implement the shared preflight**

Add in `privacy.py`:

```python
def _valid_display_id(value: object) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 < value <= 0xFFFFFFFF
    )


def inventory_structure_failure_reason(
    inventory: WindowInventory | None,
) -> ProtectionFailureReason | None:
    if inventory is None:
        return ProtectionFailureReason.INVENTORY_UNAVAILABLE
    displays = inventory.displays
    if not displays:
        return ProtectionFailureReason.EMPTY_DISPLAYS
    ids = [display.id for display in displays]
    invalid_bounds = any(
        not all(
            math.isfinite(value)
            for value in (
                display.region.left,
                display.region.top,
                display.region.width,
                display.region.height,
            )
        )
        or display.region.width <= 0
        or display.region.height <= 0
        for display in displays
    )
    if (
        any(not _valid_display_id(display_id) for display_id in ids)
        or len(set(ids)) != len(ids)
        or invalid_bounds
    ):
        return ProtectionFailureReason.INVALID_DISPLAY_INVENTORY
    if sum(window.is_active for window in inventory.windows) > 1:
        return ProtectionFailureReason.MULTIPLE_ACTIVE_WINDOWS
    return None
```

After parsing helper rows, construct `WindowInventory`, call the function, and return `InventoryReadResult(None, reason)` on failure.
Before constructing `DisplayInfo`, `_parse_display()` must validate the raw JSON ID without coercion:

```python
raw_display_id = row["id"]
if (
    isinstance(raw_display_id, bool)
    or not isinstance(raw_display_id, int)
    or not 0 < raw_display_id <= 0xFFFFFFFF
):
    raise ValueError("display id is not a positive CoreGraphics display ID")
```

Use `raw_display_id` directly in `DisplayInfo`; do not accept strings, floats, booleans, zero, negatives, or out-of-range values through `int(...)` coercion.

- [ ] **Step 5: Wire monitor, resolver, and builder to the shared boundary**

Before monitor history resolution:

```python
if inventory is not None and failure_reason is None:
    structure_failure = privacy.inventory_structure_failure_reason(inventory)
    if structure_failure is not None:
        inventory = None
        failure_reason = structure_failure
```

At the start of `WindowDisplayHistory.resolve()`, preserve the monotonic rollback check, then reject structural failure without changing `_entries`:

```python
self._previous_now = now
if inventory_structure_failure_reason(inventory) is not None:
    return replace(
        inventory,
        windows=tuple(
            replace(window, fallback_display_ids=frozenset())
            for window in inventory.windows
        ),
    )
```

When intersecting cached display IDs with active IDs, omit entries whose resulting set is empty. In `build_protection_snapshot()`, replace duplicate structural checks with the shared function while preserving explicit `failure_reason` precedence and active/sensitive mapping checks after history resolution.

- [ ] **Step 6: Prove mutation reality**

Apply and restore each mutation separately:

1. remove monitor preflight;
2. let resolver mutate invalid inventories;
3. accept duplicate display IDs in parser;
4. retain an empty display-ID history entry;
5. change invalid sample to overwrite display 1 with display 2.

Each mutation must fail a named focused test. Record commands and failure assertions.

- [ ] **Step 7: Run scoped and full Task 1 gates**

```bash
PYTHONPATH=src uv run pytest -q \
  tests/test_capture_privacy.py tests/test_window_display_history.py \
  tests/test_protection.py tests/test_protection_monitor.py \
  tests/test_capture_scheduler_fts.py
uv run ruff check \
  src/openchronicle/capture/privacy.py \
  src/openchronicle/capture/protection.py \
  src/openchronicle/capture/protection_monitor.py \
  src/openchronicle/capture/window_display_history.py \
  tests/test_capture_privacy.py tests/test_window_display_history.py \
  tests/test_protection.py tests/test_protection_monitor.py
git diff --check
```

- [ ] **Step 8: Commit Task 1**

```bash
git add src/openchronicle/capture/privacy.py \
  src/openchronicle/capture/protection.py \
  src/openchronicle/capture/protection_monitor.py \
  src/openchronicle/capture/window_display_history.py \
  tests/test_capture_privacy.py tests/test_window_display_history.py \
  tests/test_protection.py tests/test_protection_monitor.py
git commit -m "fix(capture): reject invalid history samples"
```

---

### Task 2: Title-Uncertainty Presentation Without Policy Changes

**Files:**
- Modify: `src/openchronicle/capture/protection_smoothing.py`
- Modify: `tests/test_protection_smoothing.py`
- Modify: `tests/test_protection_monitor.py`
- Modify: `tests/test_capture_scheduler_fts.py`
- Modify: `tests/test_privacy_diagnostics.py`
- Modify: `docs/capture.md`
- Modify: `docs/macos-app.md`
- Modify: `docs/superpowers/specs/2026-08-26-window-display-history-fallback-design.md`
- Modify: `docs/superpowers/specs/2026-08-26-inventory-preflight-title-uncertainty-design.md`

**Interfaces:**
- Produces phases `transient-title-uncertainty` and `sustained-title-uncertainty`.
- Consumes `ProtectionSnapshot.display_reasons.reasons` and `ProtectionReasonCode`.
- Does not change `ProtectionSnapshot`, scheduler authorization, diagnostics schema, Swift model, or native overlay protocol.

- [ ] **Step 1: Add RED classifier and exact-boundary tests**

Extend the smoothing test helper to accept reason codes and assert:

```python
unknown = _snapshot(
    1,
    ProtectionState.PROTECTED,
    reason_codes=(ProtectionReasonCode.WINDOW_TITLE_UNKNOWN,),
)
first = smoother.resolve(unknown, now=10.0)
before = smoother.resolve(replace(unknown, generation=2), now=10.799)
promoted = smoother.resolve(replace(unknown, generation=3), now=10.8)

assert first.phase is ProtectionPresentationPhase.TRANSIENT_TITLE_UNCERTAINTY
assert first.snapshot.indicator_style == "off"
assert first.overlay_reasons_enabled is False
assert first.snapshot.state is ProtectionState.PROTECTED
assert first.snapshot.ax_blocked is True
assert before.snapshot.indicator_style == "off"
assert promoted.phase is ProtectionPresentationPhase.SUSTAINED_TITLE_UNCERTAINTY
assert promoted.snapshot.indicator_style == "pill"
```

Add unknown + `MODE_ALL_INHERITED`, unknown + each direct rule, fallback + unknown priority, configured `off`, clear-pending, returned risk, and one shared episode deadline tests.

- [ ] **Step 2: Add RED monitor/scheduler/diagnostics integration tests**

Build a real mapped unknown-title inventory with a title deny pattern. Assert first decision is PROTECTED with style off and the new phase, display-level screenshot/AX blocking is unchanged, `indicator_confirmed=true`, no indicator window IDs exist, and the unprotected display continues capture. Add a category payload test proving the new phase carries no exact marker.

- [ ] **Step 3: Run RED selectors**

```bash
PYTHONPATH=src uv run pytest -q \
  tests/test_protection_smoothing.py tests/test_protection_monitor.py \
  tests/test_capture_scheduler_fts.py tests/test_privacy_diagnostics.py \
  -k 'title_uncertainty or unknown_only'
```

Expected: phase enum missing and unknown-only snapshots still use `quiet-shield`.

- [ ] **Step 4: Implement the classifier and phases**

In `protection_smoothing.py`:

```python
_TITLE_UNCERTAINTY_CODES = frozenset(
    {
        ProtectionReasonCode.WINDOW_TITLE_UNKNOWN,
        ProtectionReasonCode.MODE_ALL_INHERITED,
    }
)


def _is_title_uncertainty_only(snapshot: ProtectionSnapshot) -> bool:
    if (
        snapshot.state is not ProtectionState.PROTECTED
        or snapshot.display_mapping_fallback_active
    ):
        return False
    codes = {reason.code for reason in snapshot.display_reasons.reasons}
    return (
        ProtectionReasonCode.WINDOW_TITLE_UNKNOWN in codes
        and codes <= _TITLE_UNCERTAINTY_CODES
    )
```

Add enum values and pass `title_uncertainty` to `_risk_phase()`. For unpromoted risk use style `off` when mapping fallback, title uncertainty, or mapping failure; otherwise retain `quiet-shield`. Keep promoted, bypass, clear-pending and state/policy code unchanged.

- [ ] **Step 5: Prove policy and mutation boundaries**

Apply and restore each mutation:

1. unknown-only returns `quiet-shield`;
2. unknown + direct title rule is incorrectly silent;
3. all-mode inherited disables title uncertainty;
4. fallback + unknown selects the title phase instead of mapping-fallback;
5. style off changes screenshot/AX authorization;
6. title uncertainty restarts the shared episode timer.

Each mutation must fail a focused test.

- [ ] **Step 6: Update documentation and design status**

Document the distinction between actual privacy matches and metadata uncertainty, fixed phase strings, first-frame blocking, and the absence of Mission Control detection. Set the supplemental design status to `已实现，等待实机验证`; keep the parent design at the same status until Task 3 finishes.

- [ ] **Step 7: Run Task 2 gates**

```bash
PYTHONPATH=src uv run pytest -q \
  tests/test_protection_smoothing.py tests/test_protection_monitor.py \
  tests/test_capture_scheduler_fts.py tests/test_privacy_diagnostics.py \
  tests/test_privacy_overlay.py
uv run ruff check \
  src/openchronicle/capture/protection_smoothing.py \
  tests/test_protection_smoothing.py tests/test_protection_monitor.py \
  tests/test_capture_scheduler_fts.py tests/test_privacy_diagnostics.py
CLANG_MODULE_CACHE_PATH=/tmp/openchronicle-clang-module-cache \
SWIFTPM_MODULECACHE_OVERRIDE=/tmp/openchronicle-swiftpm-module-cache \
swift test --package-path macos/OpenChronicleApp
git diff --check
```

- [ ] **Step 8: Commit Task 2**

```bash
git add src/openchronicle/capture/protection_smoothing.py \
  tests/test_protection_smoothing.py tests/test_protection_monitor.py \
  tests/test_capture_scheduler_fts.py tests/test_privacy_diagnostics.py \
  docs/capture.md docs/macos-app.md \
  docs/superpowers/specs/2026-08-26-window-display-history-fallback-design.md \
  docs/superpowers/specs/2026-08-26-inventory-preflight-title-uncertainty-design.md
git commit -m "fix(capture): silence transient title uncertainty"
```

---

### Task 3: Full Gate, Reinstall, Live Verification, and Branch Acceptance

**Files:**
- Modify after successful live verification: `docs/superpowers/specs/2026-08-26-window-display-history-fallback-design.md`
- Modify after successful live verification: `docs/superpowers/specs/2026-08-26-inventory-preflight-title-uncertainty-design.md`
- Verify only: `/Users/tkandi/.openchronicle/config.toml`, installed site-packages, `/Applications/OpenChronicle.app`, runtime process chain, category diagnostics, capture JSON structure.

**Interfaces:**
- Consumes all Task 1/2 behavior and the existing owner-only diagnostics socket.
- Produces final status `已实现并验证` and a review-clean branch ready for the user's merge/push choice.

- [ ] **Step 1: Run the complete automated gate**

```bash
git diff --check
PYTHONPATH=src uv run pytest -q
uv run ruff check src tests
swiftc -module-cache-path /tmp/openchronicle-swift-module-cache \
  resources/mac-privacy-overlay-reason.swift resources/mac-privacy-overlay-core.swift \
  tests/swift/MacPrivacyOverlayCoreTests.swift \
  -o /tmp/openchronicle-overlay-core-title-uncertainty -framework AppKit
/tmp/openchronicle-overlay-core-title-uncertainty
CLANG_MODULE_CACHE_PATH=/tmp/openchronicle-clang-module-cache \
SWIFTPM_MODULECACHE_OVERRIDE=/tmp/openchronicle-swiftpm-module-cache \
swift test --package-path macos/OpenChronicleApp
```

Record the existing 12 unrelated full-Ruff findings separately; every changed Python file must be clean.

- [ ] **Step 2: Preserve config and reinstall both layers**

Record raw and canonical parsed-TOML SHA-256 without outputting values. Quit the old App and verify its App/daemon/overlay/watcher PIDs exit. Run:

```bash
bash install.sh --no-client-config
bash scripts/install-macos-app.sh
```

Recompute hashes, verify host-side `codesign --verify --deep --strict`, compare source/site-package SHA-256 for every changed Python module, and require exactly one App -> daemon -> overlay + AX watcher chain.

- [ ] **Step 3: Repeat category-only live F3 acceptance**

With no InPrivate window, run a 300ms system-level F3 round trip. Required first protected frame, if any:

```text
raw/effective=protected
phase=transient-title-uncertainty
style=off
overlay_reasons_enabled=false
indicator_window_ids=[]
screenshot/AX blocked according to the same protected display
```

The transition must clear through the existing 200ms confirmation without any quiet shield, pill, banner, or border.

- [ ] **Step 4: Repeat known-title and fullscreen dual-display acceptance**

Use only `about:blank` InPrivate. Require normal mapped first frame `transient-protected/quiet-shield`; after 800ms require configured style. Put a normal Edge window fullscreen over it and require only its actual/history display protected while the other display continues capture. If history fallback occurs, require `window_filterable=false` and mapping-fallback phase precedence.

- [ ] **Step 5: Verify capture artifacts structurally**

Read only key presence and monitor metadata. Require protected monitor image omission, unprotected monitor image presence, and `ax_skipped=protected_display` when the active display is protected. Never decode or display screenshots, AX, titles, URLs, app/bundle names, rules or exact reasons.

- [ ] **Step 6: Controlled cross-display repeat**

Move the blank InPrivate between displays with both displays continuously online and no process restart. Actual geometry or valid history must remain per-display. A legitimate identity/cache miss may remain globally fail-closed; record only its fixed category and do not weaken policy or invent a display mapping.

- [ ] **Step 7: Mark both designs verified and commit evidence status**

After every live assertion passes, change both design statuses to `已实现并验证` and commit:

```bash
git add \
  docs/superpowers/specs/2026-08-26-window-display-history-fallback-design.md \
  docs/superpowers/specs/2026-08-26-inventory-preflight-title-uncertainty-design.md
git commit -m "docs: verify inventory and title uncertainty hardening"
```

- [ ] **Step 8: Independent final integration review**

Review from `421f618` through final HEAD against both designs and live evidence. Acceptance requires Critical 0, Important 0; address all safety-relevant findings with new tests and rerun review.

- [ ] **Step 9: Final regression and branch handoff**

```bash
PYTHONPATH=src uv run pytest -q
CLANG_MODULE_CACHE_PATH=/tmp/openchronicle-clang-module-cache \
SWIFTPM_MODULECACHE_OVERRIDE=/tmp/openchronicle-swiftpm-module-cache \
swift test --package-path macos/OpenChronicleApp
git status --short --branch
```

Invoke `superpowers:finishing-a-development-branch`. Do not merge or push until the user chooses.
