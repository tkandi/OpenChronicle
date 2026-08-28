# Capture Stability Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove ordinary-window privacy false positives, restore protected metadata capture continuity, and prevent false empty-display outages.

**Architecture:** Narrow only the uncertainty-derived browser rule, split pause preflight from the full protection snapshot, and add a deterministic AppKit display-ID fallback behind a pure Swift selector. Direct privacy rules and fail-closed boundaries remain unchanged.

**Tech Stack:** Python 3.11+, pytest, Swift 5/AppKit/CoreGraphics, SwiftPM, shell install/signing scripts.

## Global Constraints

- Work on the existing `develop` checkout and preserve unrelated untracked files.
- Use TDD for every production behavior change.
- Do not expose privacy rule values, captured titles, or configuration contents.
- Do not weaken pause, direct-rule, late-AX, invalid-inventory, or diagnostics-lease fail-closed behavior.

---

### Task 1: Ignore unknown-title browser auxiliary layers

**Files:**
- Modify: `tests/test_capture_privacy.py`
- Modify: `src/openchronicle/capture/privacy.py`
- Modify: `docs/capture.md`

**Interfaces:**
- Consumes: `VisibleWindow.layer`, `visible_window_rule_matches()`.
- Produces: uncertainty-only matches restricted to `layer == 0`; direct matches remain layer-independent.

- [ ] **Step 1: Write the failing test**

Add `test_non_layer_unknown_browser_title_does_not_match()` with a layer-3 Edge window, `title_available=False`, and an `InPrivate` title rule. Assert an empty match tuple. Keep the existing layer-zero browser and direct non-layer panel tests unchanged.

- [ ] **Step 2: Run test to verify it fails**

Run: `env PYTHONPATH=$PWD/src .venv/bin/pytest -q tests/test_capture_privacy.py::test_non_layer_unknown_browser_title_does_not_match`

Expected: FAIL because current code returns `window_title_unknown`.

- [ ] **Step 3: Write minimal implementation**

Add `window.layer == 0` to the uncertainty branch in `visible_window_rule_matches()`; do not add the condition to direct app, bundle, or reliable title matching.

- [ ] **Step 4: Run focused tests**

Run the new test, the default unknown-browser test, and `test_non_layer_privacy_panel_protects_only_its_display`. Expected: PASS.

### Task 2: Restore stable complete-decision ordering

**Files:**
- Modify: `tests/test_protection_monitor.py`
- Modify: `tests/test_capture_scheduler_fts.py`
- Modify: `src/openchronicle/capture/protection_monitor.py`
- Modify: `src/openchronicle/capture/scheduler.py`

**Interfaces:**
- Produces: `PrivacyProtectionMonitor.capture_metadata_preflight() -> ProtectionDecision | None`.
- Consumes: strict injected pause reader and existing full `decision_for_capture()`.

- [ ] **Step 1: Write failing tests**

Add tests proving the preflight publishes the exact pause or pause-read failure without rereading pause state, reusing a safe decision, or reading inventory. Add a scheduler integration test where foreground metadata changes the inventory from safe to protected; expect a preserved record with `ax_skipped="protected_display"` and zero AX provider calls.

- [ ] **Step 2: Run tests to verify RED**

Run the new monitor and scheduler tests. Expected: the monitor test fails because the method is absent, and the scheduler test returns `None` under the current ordering.

- [ ] **Step 3: Write minimal implementation**

Implement `capture_metadata_preflight()` using one `_read_pause_input()` result injected into `_refresh()`. Terminal preflight refreshes must not reuse another generation or read inventory. Call it before metadata. Move the normal complete forced decision to its parent-commit position after foreground metadata and the first direct denylist check.

- [ ] **Step 4: Run focused safety tests**

Run the new tests plus paused capture, event-during-AX invalidation, diagnostics lease invalidation, unconfirmed clear, and filtered fallback tests. Expected: PASS, with late AX changes still discarded.

### Task 3: Fall back to AppKit display IDs

**Files:**
- Modify: `tests/swift/MacWindowListCoreTests.swift`
- Modify: `resources/mac-window-list-core.swift`
- Modify: `resources/mac-window-list.swift`

**Interfaces:**
- Produces: `selectDisplayIDs(activeDisplayIDs:appKitDisplayIDs:) -> [UInt32]`.
- Consumes: CoreGraphics active IDs and `NSScreenNumber` IDs.

- [ ] **Step 1: Write the failing native test**

Assert that every nonempty active list is returned unchanged for downstream validation, empty active IDs fall back to positive unique AppKit IDs in order, and two empty sources remain empty.

- [ ] **Step 2: Compile to verify RED**

Run `swiftc -DTESTING resources/mac-window-list-core.swift resources/mac-window-list.swift tests/swift/MacWindowListCoreTests.swift ...` with a writable module cache. Expected: compile failure because `selectDisplayIDs` is absent.

- [ ] **Step 3: Write minimal implementation**

Add the pure selector to the core file. Refactor the main helper to read active IDs without exiting on an empty/error result, lazily read `NSScreenNumber` IDs only for an empty active result, select IDs, and build display records exactly as before.

- [ ] **Step 4: Compile and run native tests**

Expected: `MacWindowListCoreTests passed`.

### Task 4: Full verification and deployment

**Files:**
- Verify all modified source, tests, and documentation.

**Interfaces:**
- Consumes: repository tests and install scripts.
- Produces: installed signed app with unchanged user configuration and one managed process chain.

- [ ] **Step 1: Run focused and full suites**

Run complete Python tests with repository `PYTHONPATH`, SwiftPM tests, and freshly compiled window-list, privacy-overlay, and screen-capture native tests.

- [ ] **Step 2: Review and scan the diff**

Confirm only intended paths changed. Search added lines for private absolute paths, credentials, tokens, captured titles, and privacy rule values; report only redacted findings.

- [ ] **Step 3: Install without rewriting configuration**

Record the configuration SHA-256, run `bash install.sh --no-client-config`, run `bash scripts/install-macos-app.sh`, then stop any confirmed orphan backend so the new App owns the replacement process.

- [ ] **Step 4: Verify installed behavior**

Run strict deep code-sign verification, compare source/installed hashes, rerun deterministic tests against the installed package, require unchanged configuration SHA-256, `active/healthy`, a fresh capture, and exactly one `OpenChronicle.app -> openchronicle start --foreground -> mac-ax-watcher` chain.

- [ ] **Step 5: Commit and synchronize**

Stage only intended tracked files, commit on `develop`, verify no private or unrelated untracked files are included, and push `develop` to `origin`.
