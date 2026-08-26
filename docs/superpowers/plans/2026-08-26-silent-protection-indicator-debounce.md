# Silent Protection Indicator Debounce Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep every automatically smoothed privacy-risk presentation invisible for its first 1.0 second, then show the latest configured full indicator directly, without delaying capture or AX protection.

**Architecture:** Preserve the existing raw-decision, presentation-smoother, monitor, overlay acknowledgement, scheduler, AX gate, and diagnostics pipeline. Change only the smoother's fixed promotion duration and transient effective style; retain transient phases and deadlines so downstream policy remains immediate and observable.

**Tech Stack:** Python 3.11+, frozen dataclasses, monotonic time, pytest, Ruff, Swift/AppKit helper regression tests, SwiftPM/XCTest, signed macOS app installer, owner-only category diagnostics.

## Global Constraints

- `PROTECTED_PROMOTION_SECONDS` becomes exactly `1.0`; `SAFE_CONFIRMATION_SECONDS` remains exactly `0.2`.
- Every smoothed `transient-*` result uses effective `indicator_style = "off"` and `overlay_reasons_enabled = false`.
- Ordinary `protected` capture and AX policy remains fail-closed from the first decision; allowlisted mapping failures immediately retain their existing failure policy.
- `paused` and non-allowlisted hard `failed` decisions continue to bypass smoothing immediately.
- The selectable `quiet-shield` style remains supported as an explicit sustained style; no Swift protocol or settings schema changes.
- Do not print window titles, URLs, matching rules, configuration values, or real private content during live verification.
- Use `PYTHONPATH=/Users/tkandi/Desktop/Codex/OpenChronicle/src` for repository pytest commands to avoid the unrelated installed namespace package.

---

## File Structure

- `src/openchronicle/capture/protection_smoothing.py`: owns the fixed promotion delay and pure transient/sustained presentation policy.
- `tests/test_protection_smoothing.py`: owns exact monotonic boundary, state continuity, explicit-style, clear-pending, and bypass coverage.
- `tests/test_protection_monitor.py`: owns worker deadline wake-up, overlay acknowledgement, listener, hot-reload, and failure-policy integration coverage.
- `docs/capture.md`: current user-facing capture/indicator behavior; historical design documents remain unchanged.
- `docs/superpowers/specs/2026-08-26-silent-protection-indicator-debounce-design.md`: approved design and final implementation/verification status.

### Task 1: Change the Pure Presentation Policy Test-First

**Files:**
- Modify: `tests/test_protection_smoothing.py:101-704`
- Modify: `src/openchronicle/capture/protection_smoothing.py:10-198`

**Interfaces:**
- Consumes: `ProtectionPresentationSmoother.resolve(raw_snapshot: ProtectionSnapshot, *, now: float) -> ProtectionPresentationResult`.
- Produces: unchanged interface with a `1.0` default promotion deadline and `off` for every unpromoted smoothed risk.

- [ ] **Step 1: Read the repository test-quality rules**

Read `/Users/tkandi/.codex/skills/test-driven-development/writing-good-tests.md` completely before editing tests.

- [ ] **Step 2: Write the first failing boundary test**

Replace the existing first smoothing test with this exact behavior:

```python
def test_short_protection_stays_silent_then_promotes_at_1s() -> None:
    smoother = ProtectionPresentationSmoother()
    first = smoother.resolve(_snapshot(1, ProtectionState.PROTECTED), now=10.0)
    before = smoother.resolve(_snapshot(2, ProtectionState.PROTECTED), now=10.999)
    promoted = smoother.resolve(_snapshot(3, ProtectionState.PROTECTED), now=11.0)

    assert first.phase is ProtectionPresentationPhase.TRANSIENT_PROTECTED
    assert first.snapshot.indicator_style == "off"
    assert first.snapshot.display_reasons.reasons == (REASON,)
    assert first.snapshot.ax_blocked is True
    assert first.overlay_reasons_enabled is False
    assert first.next_deadline == pytest.approx(11.0)
    assert before.snapshot.indicator_style == "off"
    assert before.phase is ProtectionPresentationPhase.TRANSIENT_PROTECTED
    assert promoted.phase is ProtectionPresentationPhase.SUSTAINED_PROTECTED
    assert promoted.snapshot.indicator_style == "pill"
    assert promoted.overlay_reasons_enabled is True
    assert promoted.next_deadline is None
```

- [ ] **Step 3: Run the focused test and verify RED**

Run:

```bash
env PYTHONPATH=/Users/tkandi/Desktop/Codex/OpenChronicle/src uv run pytest -q \
  tests/test_protection_smoothing.py::test_short_protection_stays_silent_then_promotes_at_1s
```

Expected: assertion failure because the current first result is `quiet-shield` and the current promotion deadline is `10.8`.

- [ ] **Step 4: Implement the minimal pure-policy change**

Change the constant and transient branch to:

```python
PROTECTED_PROMOTION_SECONDS: float = 1.0
SAFE_CONFIRMATION_SECONDS: float = 0.2
```

```python
effective_style = raw_snapshot.indicator_style
reasons_enabled = promoted and effective_style != "off"
if not promoted:
    effective_style = "off"
```

Keep `mapping_fallback`, `mapping_failure`, and `title_uncertainty` for phase selection. Do not change `risk_active`, hard bypass, raw/effective state, reasons, protected IDs, clear-pending, or deadline ownership.

- [ ] **Step 5: Run the focused test and verify GREEN**

Run the command from Step 3 again.

Expected: `1 passed`.

- [ ] **Step 6: Run the whole pure-smoother file and record expected regression failures**

Run:

```bash
env PYTHONPATH=/Users/tkandi/Desktop/Codex/OpenChronicle/src uv run pytest -q \
  tests/test_protection_smoothing.py
```

Expected before test alignment: failures only from obsolete `800ms`, `10.8`/`20.8`, and automatic `quiet-shield` expectations.

- [ ] **Step 7: Align every pure-state regression with the approved policy**

Make these semantic updates throughout `tests/test_protection_smoothing.py`:

- rename `*_at_800ms*`/`*_800ms_*` tests to `*_at_1s*`/`*_1s_*`;
- for episodes starting at `10.0`, use `10.999` immediately before promotion and `11.0` at promotion; assert deadline `11.0`;
- for an episode restarting at `20.0`, assert deadline `21.0`;
- change automatic transient protected expectations from `quiet-shield` to `off`;
- preserve explicit final `quiet-shield` coverage, but assert its transient result is `off` and its `11.0` sustained result is `quiet-shield`;
- keep all mapping fallback, title uncertainty, and mapping failure transient styles `off` while moving their exact boundary to 1 second;
- keep the 0.2-second safe-confirmation assertions unchanged;
- update the negative-delay parameter from `(0.8, -0.001)` to `(1.0, -0.001)` so the current default policy is represented.

The explicit-style test must contain these assertions:

```python
@pytest.mark.parametrize("style", ["quiet-shield", "off"])
def test_quiet_and_off_promote_without_inventing_another_visual_style(style: str) -> None:
    smoother = ProtectionPresentationSmoother()
    first = smoother.resolve(_snapshot(1, ProtectionState.PROTECTED, style=style), now=10.0)
    promoted = smoother.resolve(_snapshot(2, ProtectionState.PROTECTED, style=style), now=11.0)
    assert first.snapshot.indicator_style == "off"
    assert promoted.snapshot.indicator_style == style
    assert promoted.phase is ProtectionPresentationPhase.SUSTAINED_PROTECTED
    assert promoted.snapshot.generation == 2
```

- [ ] **Step 8: Verify the pure state machine**

Run:

```bash
env PYTHONPATH=/Users/tkandi/Desktop/Codex/OpenChronicle/src uv run pytest -q \
  tests/test_protection_smoothing.py
env PYTHONPATH=/Users/tkandi/Desktop/Codex/OpenChronicle/src uv run ruff check \
  src/openchronicle/capture/protection_smoothing.py tests/test_protection_smoothing.py
```

Expected: all smoothing tests pass and Ruff reports no errors.

- [ ] **Step 9: Commit the pure-policy change**

```bash
git add src/openchronicle/capture/protection_smoothing.py tests/test_protection_smoothing.py
git commit -m "fix(capture): silence transient protection indicators"
```

### Task 2: Align Monitor Integration and Active Documentation

**Files:**
- Modify: `tests/test_protection_monitor.py:750-1208`
- Modify: `tests/test_protection_monitor.py:1633-1663`
- Modify: `docs/capture.md:182-245`

**Interfaces:**
- Consumes: the unchanged `ProtectionDecision` fields `presentation_phase`, `indicator_style`, `overlay_reasons_enabled`, `presentation_deadline_monotonic`, `indicator_confirmed`, and `indicator_window_ids`.
- Produces: monitor behavior that publishes confirmed `off` transient decisions, wakes at the 1-second deadline, and then publishes the configured sustained style.

- [ ] **Step 1: Run monitor integration tests before aligning expectations**

Run:

```bash
env PYTHONPATH=/Users/tkandi/Desktop/Codex/OpenChronicle/src uv run pytest -q \
  tests/test_protection_monitor.py
```

Expected: failures are limited to old 0.8-second timing and automatic `quiet-shield` expectations; failures prove the integration suite observes the production change.

- [ ] **Step 2: Update default-duration monitor tests**

Apply these exact behavioral changes without touching the unrelated `drain_bound * 0.8` timing tolerance near the end of the file:

- rename `test_monitor_publishes_quiet_then_configured_style_with_new_generations` to `test_monitor_publishes_silent_then_configured_style_with_new_generations`;
- advance the default clock by `1.0` for sustained promotion;
- expect default transient style `off`, sustained style `pill`, reasons `[False, True]`, and both decisions confirmed;
- expect protected clear-pending and protection-return transient style `off`; while a transient episode is silent,
  assert `indicator_window_ids == ()` even though `indicator_confirmed is True` and capture/AX policy remains protected;
- change the mapping-failure-to-protected deadline sequence to `10.0 -> 10.4 -> 11.0`, with both transient deadlines `11.0` and promotion time `11.0`;
- advance mapping-failure smoothing by `1.0` before expecting sustained `pill`;
- expect listener-published transient style `off` while retaining structured reasons and display-protection notification;
- in hot reload, keep the first and mid-episode styles `off`, advance `0.4` then `0.6`, and expect `border` at `11.0` without resetting the deadline.

- [ ] **Step 3: Update custom-deadline worker tests**

Keep their injected `promotion_seconds=0.03` and `0.05`, but change the visual sequence from `quiet-shield -> pill` to `off -> pill`. The fake overlay records `off` render commands, so assert:

```python
assert [snapshot.indicator_style for snapshot in fake_overlay.snapshots][:2] == [
    "off",
    "pill",
]
```

For external publication, assert the first decision is `off`, then wait for a later `pill`. Keep the one-thread and bounded-inventory-read assertions.

- [ ] **Step 4: Verify monitor and downstream privacy boundaries**

Run:

```bash
env PYTHONPATH=/Users/tkandi/Desktop/Codex/OpenChronicle/src uv run pytest -q \
  tests/test_protection_monitor.py \
  tests/test_capture_scheduler_fts.py \
  tests/test_privacy_diagnostics.py \
  tests/test_privacy_overlay.py
```

Expected: all selected tests pass. Explicit `quiet-shield` helper/payload tests remain because the style is still supported when deliberately configured.

- [ ] **Step 5: Rewrite the active capture documentation**

In `docs/capture.md`, replace the first-800ms mixed quiet/silent description with:

```markdown
For the first 1 second, every smoothed visual risk episode uses presentation
style `off` and suppresses overlay reasons. Protection policy still applies
from the first inventory frame. At exactly 1 second, a new confirmed generation
promotes directly to the latest configured sustained style and reason setting.
```

Update all active references in that section from `800ms` to `1 second`, replace “transient quiet shield” with “silent transient”, and state that an explicitly configured `quiet-shield` appears only after sustained promotion. Do not rewrite historical files under `docs/superpowers/specs/` or `docs/superpowers/plans/`.

- [ ] **Step 6: Scan for stale active-policy text**

Run:

```bash
rg -n "800ms|immediate `quiet-shield`|transient quiet shield|10\.8|20\.8" \
  src/openchronicle/capture/protection_smoothing.py \
  tests/test_protection_smoothing.py \
  tests/test_protection_monitor.py \
  docs/capture.md
```

Expected: no stale policy matches. Separately inspect every remaining `quiet-shield` occurrence in tests and confirm it is an explicit configured-style/helper capability test, never an automatic transient expectation.

- [ ] **Step 7: Commit monitor tests and active docs**

```bash
git add tests/test_protection_monitor.py docs/capture.md
git commit -m "test(capture): cover one-second silent promotion"
```

### Task 3: Run Full Regression and Finalize Tracked Status

**Files:**
- Modify: `docs/superpowers/specs/2026-08-26-silent-protection-indicator-debounce-design.md:1-4`

**Interfaces:**
- Consumes: the complete repository test/build surface.
- Produces: fresh proof that Python, native helper, SwiftUI app, packaging, and documentation agree before installation.

- [ ] **Step 1: Run formatting and static checks**

```bash
git diff --check
env PYTHONPATH=/Users/tkandi/Desktop/Codex/OpenChronicle/src uv run ruff check \
  src/openchronicle/capture/protection_smoothing.py \
  tests/test_protection_smoothing.py \
  tests/test_protection_monitor.py
```

Expected: no output from `git diff --check`; Ruff reports success.

- [ ] **Step 2: Run the full Python suite**

```bash
env PYTHONPATH=/Users/tkandi/Desktop/Codex/OpenChronicle/src uv run pytest -q
```

Expected: zero failures and zero collection errors.

- [ ] **Step 3: Run native helper and macOS app suites**

```bash
swiftc resources/mac-privacy-overlay-reason.swift \
  resources/mac-privacy-overlay-core.swift \
  tests/swift/MacPrivacyOverlayCoreTests.swift \
  -o /private/tmp/openchronicle-overlay-core-tests \
  -framework AppKit
/private/tmp/openchronicle-overlay-core-tests
swift test --package-path macos/OpenChronicleApp
bash scripts/build-macos-app.sh
```

Expected: native core prints `MacPrivacyOverlayCoreTests passed`, SwiftPM reports zero failures, and the signed archive is built at `dist/OpenChronicle.app.zip`.

- [ ] **Step 4: Mark the design implemented only after verification**

Change the design header to:

```markdown
状态：已实现并验证
```

Append a concise verification note with the exact Python/Swift pass counts and build result observed in Steps 1-3; do not predict counts before seeing output.

- [ ] **Step 5: Commit verification status**

```bash
git add docs/superpowers/specs/2026-08-26-silent-protection-indicator-debounce-design.md
git commit -m "docs: verify silent protection debounce"
```

### Task 4: Install and Black-Box Verify the Current Product

**Files:**
- Install: `/Users/tkandi/.openchronicle/venv/lib/python3.12/site-packages/openchronicle/capture/protection_smoothing.py`
- Install: `/Applications/OpenChronicle.app`
- Verify only: `/Users/tkandi/.openchronicle/config.toml`, runtime process chain, installed source hash, owner-only category diagnostics.

**Interfaces:**
- Consumes: verified repository source and existing user configuration/data.
- Produces: one signed running app-owned backend whose installed smoother matches repository source and exhibits `off -> pill` at the exact 1-second boundary.

- [ ] **Step 1: Record non-sensitive pre-install state**

Run only hash/status/process metadata; do not print configuration content:

```bash
shasum -a 256 /Users/tkandi/.openchronicle/config.toml
openchronicle status --json --no-model-checks
ps -axo pid=,ppid=,etime=,command= | rg -i '[O]penChronicle|[o]penchronicle|mac-ax-watcher'
```

- [ ] **Step 2: Install backend/helpers and the signed app**

```bash
bash install.sh --no-client-config
bash scripts/install-macos-app.sh
```

Expected: the existing config/data remain in place, backend helpers compile, `/Applications/OpenChronicle.app` verifies its signature, and the app opens.

- [ ] **Step 3: Verify source/install identity and live ownership**

```bash
shasum -a 256 \
  src/openchronicle/capture/protection_smoothing.py \
  /Users/tkandi/.openchronicle/venv/lib/python3.12/site-packages/openchronicle/capture/protection_smoothing.py
codesign --verify --deep --strict --verbose=2 /Applications/OpenChronicle.app
openchronicle status --json --no-model-checks
ps -axo pid=,ppid=,etime=,command= | rg -i '[O]penChronicle|[o]penchronicle|mac-ax-watcher'
```

Expected: the two smoother hashes match; one App-owned backend/watcher chain is active and healthy; the overlay helper is either absent while inactive or a single child of that backend.

- [ ] **Step 4: Run deterministic behavior against the installed package**

```bash
/Users/tkandi/.openchronicle/venv/bin/python - <<'PY'
from openchronicle.capture.privacy import DisplayInfo, ScreenRegion
from openchronicle.capture.protection import ProtectionSnapshot, ProtectionState
from openchronicle.capture.protection_smoothing import ProtectionPresentationSmoother

display = DisplayInfo(1, ScreenRegion(0, 0, 100, 100), True)

def snapshot(generation: int, now: float) -> ProtectionSnapshot:
    return ProtectionSnapshot(
        generation=generation,
        state=ProtectionState.PROTECTED,
        capture_mode="separate",
        indicator_style="pill",
        displays=(display,),
        protected_display_ids=frozenset({1}),
        active_display_id=1,
        created_monotonic=now,
        fresh_until=now + 0.25,
    )

smoother = ProtectionPresentationSmoother()
first = smoother.resolve(snapshot(1, 10.0), now=10.0)
before = smoother.resolve(snapshot(2, 10.999), now=10.999)
promoted = smoother.resolve(snapshot(3, 11.0), now=11.0)
assert first.snapshot.indicator_style == "off"
assert before.snapshot.indicator_style == "off"
assert first.next_deadline == 11.0
assert promoted.snapshot.indicator_style == "pill"
assert promoted.next_deadline is None
assert first.snapshot.ax_blocked is True
print("installed silent debounce: off -> off -> pill at 1.0s")
PY
```

Expected: prints the single success line and no private data.

- [ ] **Step 5: Read category-only live diagnostics**

Subscribe to `/Users/tkandi/.openchronicle/runtime/privacy-diagnostics.sock` with detail `category`, and print only `generation`, `state`, `raw_state`, `presentation_phase`, `indicator_style`, `indicator_confirmed`, and the monotonic deadline fields. Do not request or print exact detail.

Expected: a valid schema-v1 snapshot from the newly installed running backend. If the screen is inactive at observation time, report `inactive` without manufacturing a live transition; Step 4 is the deterministic installed timing proof.

- [ ] **Step 6: Final repository and runtime audit**

```bash
git status --short --branch
git log -6 --oneline --decorate
openchronicle status --json --no-model-checks
```

Expected: only the intended commits, no uncommitted source/test/doc changes, and active/healthy installed runtime.
