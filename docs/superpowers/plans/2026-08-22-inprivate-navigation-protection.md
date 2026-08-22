# InPrivate Navigation Protection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve private-browser protection when navigation gives a window a non-empty CG title.

**Architecture:** Safely attach an alternate top-level AX title only after unique PID and Window ID
matching, serialize it in the existing window inventory, and match denylist rules against either
title. CG bounds remain authoritative for screen mapping.

**Tech Stack:** Swift/AppKit/CoreGraphics/Accessibility, Python dataclasses, pytest, standalone Swift
test harnesses.

## Global Constraints

- Never reintroduce geometry-only AX-to-CG matching or duplicate AX window records.
- Never read AX content trees; only the matched top-level window title.
- Never authorize AX reads for missing or ambiguous identities.
- Preserve fail-closed screenshot and AX behavior.
- Exact reason values remain memory-only and bounded by existing serializers.

---

### Task 1: Preserve Safely Matched AX Titles

**Files:**
- Modify: `resources/mac-window-list-core.swift`
- Modify: `resources/mac-window-list.swift`
- Test: `tests/swift/MacWindowListCoreTests.swift`

**Interfaces:**
- Produces: `ResolvedWindowMetadata.alternateTitle: String?`
- Produces: JSON `alternate_title` on `WindowRecord`

- [ ] **Step 1: Write the failing Swift core tests**

Add a case with CG title `Google`, a unique matched AX title
`Google - Microsoft Edge (InPrivate)`, and assert one AX read plus the alternate title. Extend the
existing rejected-identity table to assert zero AX reads for non-empty CG titles too.

- [ ] **Step 2: Run the Swift harness and verify RED**

Run:

```bash
swiftc resources/mac-window-list-core.swift tests/swift/MacWindowListCoreTests.swift \
  -o /tmp/mac-window-list-core-tests
/tmp/mac-window-list-core-tests
```

Expected: compile or assertion failure because alternate titles do not exist and titled windows skip
AX reads.

- [ ] **Step 3: Implement minimal safe title attachment**

Read the AX title only when `resolveAXWindowMatches` provides a unique identity. Preserve AX fallback
as the primary title for empty CG titles; otherwise return a distinct alternate title. Add
`alternate_title` to the encoded window record.

- [ ] **Step 4: Run the Swift harness and verify GREEN**

Expected: all `MacWindowListCoreTests` pass.

### Task 2: Match Either Title In Python

**Files:**
- Modify: `src/openchronicle/capture/privacy.py`
- Test: `tests/test_capture_privacy.py`
- Test: `tests/test_protection.py`

**Interfaces:**
- Consumes: JSON `alternate_title`
- Produces: `VisibleWindow.alternate_title: str`

- [ ] **Step 1: Write failing parser and rule-match tests**

Cover primary-only, alternate-only, the same rule matching both titles without duplication, and exact
reason attribution to the title that matched.

- [ ] **Step 2: Run focused pytest and verify RED**

Run:

```bash
PYTHONPATH=src uv run pytest -q tests/test_capture_privacy.py tests/test_protection.py
```

Expected: failures because `alternate_title` is ignored.

- [ ] **Step 3: Implement parsing and stable de-duplicated matching**

Parse missing `alternate_title` as an empty string for backward compatibility. Match primary then
alternate title and suppress repeated rule identities.

- [ ] **Step 4: Run focused pytest and verify GREEN**

Expected: focused suites pass.

### Task 3: Verify, Package, Install, And Reproduce

**Files:**
- Modify: `docs/capture.md` only if the title-source behavior is not already accurately documented

- [ ] **Step 1: Run full automated verification**

```bash
PYTHONPATH=src uv run pytest -q
uv run ruff check src/openchronicle/capture/privacy.py tests/test_capture_privacy.py tests/test_protection.py
swift test --package-path macos/OpenChronicleApp
bash resources/build-mac-window-list.sh
swiftc resources/mac-window-list-core.swift tests/swift/MacWindowListCoreTests.swift \
  -o /tmp/mac-window-list-core-tests
/tmp/mac-window-list-core-tests
git diff --check
```

- [ ] **Step 2: Verify arm64/x86_64 helper builds, wheel contents, and signed App build**

Compile `mac-window-list` for both architectures, run `uv build`, verify isolated wheel helper
resolution, and run `bash scripts/build-macos-app.sh`.

- [ ] **Step 3: Commit and install**

Commit the implementation, stop the installed App cleanly, run `bash install.sh --no-client-config`
and `bash scripts/install-macos-app.sh`, then confirm one healthy App-owned process chain.

- [ ] **Step 4: Run live black-box acceptance**

Use a fresh no-content Edge InPrivate window. Verify protection for `about:blank`, Google, the same
window in the background, and release after closing it. Query category-only diagnostics and inspect
only structural capture fields.
