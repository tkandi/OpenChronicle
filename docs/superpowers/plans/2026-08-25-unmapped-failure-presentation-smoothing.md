# Unmapped Failure Presentation Smoothing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep mapping failures immediately subject to the existing screenshot/AX failure policy while presenting only `active_window_unmapped` and `sensitive_window_unmapped` as a quiet shield for their first 800ms.

**Architecture:** Extend the pure `ProtectionPresentationSmoother` with an allowlisted mapping-failure risk classification. Normal PROTECTED and allowlisted FAILED snapshots share one episode timer and safe-clear state, while every effective snapshot retains its current raw state and reasons. The monitor, scheduler policy, native helper protocol, single-thread deadline scheduler, and legacy fail-open policy remain unchanged.

**Tech Stack:** Python 3.11+, dataclasses, StrEnum, pytest, Ruff, owner-only category diagnostics, Swift Codable tests, AppKit helper tests.

## Global Constraints

- Only `ProtectionFailureReason.ACTIVE_WINDOW_UNMAPPED` and `ProtectionFailureReason.SENSITIVE_WINDOW_UNMAPPED` are presentation-smoothed.
- Mapping failures remain effective `ProtectionState.FAILED`; never rewrite them as PROTECTED or INACTIVE.
- Screenshot/AX policy remains controlled by `failure_requires_fail_closed()` and the scheduler.
- Default/current fail-closed and filtered modes block on the first mapping-failure frame; legacy fail-open remains unchanged.
- Exact delays remain `PROTECTED_PROMOTION_SECONDS = 0.8` and `SAFE_CONFIRMATION_SECONDS = 0.2`.
- A non-`off` transient mapping failure renders `quiet-shield` with overlay reasons disabled.
- A sustained mapping failure renders the latest configured style with overlay reasons enabled.
- PROTECTED and allowlisted FAILED transitions share an episode and never restart the 800ms timer.
- Raw inactive after either risk state requires the existing 200ms second-safe confirmation.
- Paused and every non-allowlisted FAILED state bypass immediately with configured style and reasons enabled.
- Future failure reasons default to hard-failure bypass because the smoothed set is an allowlist.
- No new config, setting, thread, Timer, Mission Control detector, private API, matching rule, or native overlay field.
- Every publication retains strict generation, same-generation helper acknowledgement/window IDs, and authorization causality.
- Category diagnostics remain category-only and must not expose title, URL, app/bundle, rule, exact reason, screenshot, or AX content.
- Source specification: `docs/superpowers/specs/2026-08-25-unmapped-failure-presentation-smoothing-design.md`.

## File Map

- Modify `src/openchronicle/capture/protection_smoothing.py`: allowlist, mapping-failure phases, cross-state episode, held-risk invariant.
- Modify `tests/test_protection_smoothing.py`: virtual-time tests.
- Modify `tests/test_protection_monitor.py`: monitor, deadline, hard-failure, and legacy policy tests.
- Modify `tests/test_privacy_diagnostics.py`: category-safe payload tests.
- Modify `tests/test_capture_scheduler_fts.py`: unchanged failure-policy tests.
- Modify `docs/capture.md`, `docs/macos-app.md`, and the new design status.

---

### Task 1: Pure Mapping-Failure Risk Episodes

**Files:**
- Modify: `src/openchronicle/capture/protection_smoothing.py`
- Modify: `tests/test_protection_smoothing.py`

**Interfaces:**
- Consumes: `ProtectionSnapshot.failure_reason` and `ProtectionFailureReason`.
- Produces: `PRESENTATION_SMOOTHED_FAILURES: frozenset[ProtectionFailureReason]`.
- Produces: phases `transient-mapping-failure` and `sustained-mapping-failure`.
- Preserves: `resolve(raw_snapshot, *, now) -> ProtectionPresentationResult`.

- [ ] **Step 1: Add failure snapshots and exact 800ms RED tests**

In `tests/test_protection_smoothing.py`, import `ProtectionFailureReason` and add:

```python
MAPPING_FAILURES = (
    ProtectionFailureReason.ACTIVE_WINDOW_UNMAPPED,
    ProtectionFailureReason.SENSITIVE_WINDOW_UNMAPPED,
)


def _failure_snapshot(
    generation: int,
    reason: ProtectionFailureReason,
    *,
    style: str = "pill",
    now: float = 10.0,
) -> ProtectionSnapshot:
    return replace(
        _snapshot(generation, ProtectionState.FAILED, style=style, now=now),
        failure_reason=reason,
        display_reasons=DisplayProtectionReasons.from_reasons(
            [ProtectionReason(ProtectionReasonCode(reason.value), None)]
        ),
    )


@pytest.mark.parametrize("reason", MAPPING_FAILURES)
def test_mapping_failure_stays_failed_but_promotes_at_800ms(reason) -> None:
    smoother = ProtectionPresentationSmoother()
    first = smoother.resolve(_failure_snapshot(1, reason), now=10.0)
    before = smoother.resolve(_failure_snapshot(2, reason), now=10.799)
    promoted = smoother.resolve(_failure_snapshot(3, reason), now=10.8)

    assert first.snapshot.state is ProtectionState.FAILED
    assert first.snapshot.failure_reason is reason
    assert first.phase is ProtectionPresentationPhase.TRANSIENT_MAPPING_FAILURE
    assert first.snapshot.indicator_style == "quiet-shield"
    assert first.overlay_reasons_enabled is False
    assert first.next_deadline == pytest.approx(10.8)
    assert before.phase is ProtectionPresentationPhase.TRANSIENT_MAPPING_FAILURE
    assert promoted.snapshot.state is ProtectionState.FAILED
    assert promoted.phase is ProtectionPresentationPhase.SUSTAINED_MAPPING_FAILURE
    assert promoted.snapshot.indicator_style == "pill"
    assert promoted.overlay_reasons_enabled is True
    assert promoted.next_deadline is None
```

- [ ] **Step 2: Add cross-state episode RED tests**

```python
def test_mapping_failure_and_protected_share_one_episode_deadline() -> None:
    smoother = ProtectionPresentationSmoother()
    failed = smoother.resolve(
        _failure_snapshot(1, ProtectionFailureReason.ACTIVE_WINDOW_UNMAPPED),
        now=10.0,
    )
    protected = smoother.resolve(_snapshot(2, ProtectionState.PROTECTED), now=10.4)
    failed_again = smoother.resolve(
        _failure_snapshot(3, ProtectionFailureReason.SENSITIVE_WINDOW_UNMAPPED),
        now=10.799,
    )
    promoted = smoother.resolve(_snapshot(4, ProtectionState.PROTECTED), now=10.8)

    assert failed.phase is ProtectionPresentationPhase.TRANSIENT_MAPPING_FAILURE
    assert protected.phase is ProtectionPresentationPhase.TRANSIENT_PROTECTED
    assert protected.next_deadline == pytest.approx(10.8)
    assert failed_again.phase is ProtectionPresentationPhase.TRANSIENT_MAPPING_FAILURE
    assert failed_again.next_deadline == pytest.approx(10.8)
    assert promoted.phase is ProtectionPresentationPhase.SUSTAINED_PROTECTED


def test_sustained_protected_to_mapping_failure_stays_sustained() -> None:
    smoother = ProtectionPresentationSmoother()
    smoother.resolve(_snapshot(1, ProtectionState.PROTECTED), now=10.0)
    smoother.resolve(_snapshot(2, ProtectionState.PROTECTED), now=10.8)
    result = smoother.resolve(
        _failure_snapshot(3, ProtectionFailureReason.ACTIVE_WINDOW_UNMAPPED),
        now=10.9,
    )
    assert result.phase is ProtectionPresentationPhase.SUSTAINED_MAPPING_FAILURE
    assert result.snapshot.state is ProtectionState.FAILED
    assert result.snapshot.indicator_style == "pill"


def test_mapping_failure_reason_change_does_not_restart_episode() -> None:
    smoother = ProtectionPresentationSmoother()
    smoother.resolve(
        _failure_snapshot(1, ProtectionFailureReason.ACTIVE_WINDOW_UNMAPPED),
        now=10.0,
    )
    changed = smoother.resolve(
        _failure_snapshot(2, ProtectionFailureReason.SENSITIVE_WINDOW_UNMAPPED),
        now=10.4,
    )
    promoted = smoother.resolve(
        _failure_snapshot(3, ProtectionFailureReason.SENSITIVE_WINDOW_UNMAPPED),
        now=10.8,
    )
    assert changed.next_deadline == pytest.approx(10.8)
    assert promoted.phase is ProtectionPresentationPhase.SUSTAINED_MAPPING_FAILURE
```

- [ ] **Step 3: Add held-FAILED safe-clear and cancellation RED tests**

```python
def test_mapping_failure_clear_pending_holds_failed_until_second_safe() -> None:
    smoother = ProtectionPresentationSmoother()
    failed = smoother.resolve(
        _failure_snapshot(1, ProtectionFailureReason.SENSITIVE_WINDOW_UNMAPPED),
        now=10.0,
    )
    first_safe = smoother.resolve(_snapshot(2, ProtectionState.INACTIVE), now=10.1)
    early_safe = smoother.resolve(_snapshot(3, ProtectionState.INACTIVE), now=10.299)
    confirmed = smoother.resolve(_snapshot(4, ProtectionState.INACTIVE), now=10.3)

    assert failed.snapshot.state is ProtectionState.FAILED
    for held in (first_safe, early_safe):
        assert held.phase is ProtectionPresentationPhase.CLEAR_PENDING
        assert held.snapshot.state is ProtectionState.FAILED
        assert held.snapshot.failure_reason is ProtectionFailureReason.SENSITIVE_WINDOW_UNMAPPED
    assert confirmed.phase is ProtectionPresentationPhase.INACTIVE


@pytest.mark.parametrize("returned", ["protected", "mapping-failure"])
def test_risk_return_cancels_failed_clear_pending(returned: str) -> None:
    smoother = ProtectionPresentationSmoother()
    smoother.resolve(
        _failure_snapshot(1, ProtectionFailureReason.ACTIVE_WINDOW_UNMAPPED),
        now=10.0,
    )
    smoother.resolve(_snapshot(2, ProtectionState.INACTIVE), now=10.1)
    raw = (
        _snapshot(3, ProtectionState.PROTECTED)
        if returned == "protected"
        else _failure_snapshot(3, ProtectionFailureReason.SENSITIVE_WINDOW_UNMAPPED)
    )
    result = smoother.resolve(raw, now=10.2)
    assert result.snapshot.state is raw.state
    assert result.phase in {
        ProtectionPresentationPhase.TRANSIENT_PROTECTED,
        ProtectionPresentationPhase.TRANSIENT_MAPPING_FAILURE,
    }
    assert result.next_deadline == pytest.approx(10.8)
```

- [ ] **Step 4: Parameterize hard failures and off behavior**

```python
HARD_FAILURES = tuple(
    reason for reason in ProtectionFailureReason if reason not in MAPPING_FAILURES
)


@pytest.mark.parametrize("reason", HARD_FAILURES)
def test_every_non_allowlisted_failure_bypasses_immediately(reason) -> None:
    smoother = ProtectionPresentationSmoother()
    result = smoother.resolve(_failure_snapshot(1, reason, style="banner"), now=10.0)
    assert result.phase is ProtectionPresentationPhase.BYPASS
    assert result.snapshot.indicator_style == "banner"
    assert result.overlay_reasons_enabled is True
    assert result.next_deadline is None


def test_mapping_failure_off_stays_off_through_promotion() -> None:
    smoother = ProtectionPresentationSmoother()
    first = smoother.resolve(
        _failure_snapshot(1, ProtectionFailureReason.ACTIVE_WINDOW_UNMAPPED, style="off"),
        now=10.0,
    )
    promoted = smoother.resolve(
        _failure_snapshot(2, ProtectionFailureReason.ACTIVE_WINDOW_UNMAPPED, style="off"),
        now=10.8,
    )
    assert first.snapshot.indicator_style == "off"
    assert first.overlay_reasons_enabled is False
    assert promoted.phase is ProtectionPresentationPhase.SUSTAINED_MAPPING_FAILURE
    assert promoted.snapshot.indicator_style == "off"
```

Keep a separate test that FAILED with `failure_reason=None` bypasses, so malformed/future inputs are not smoothed.

Update the existing private-invariant tests to use `_last_effective_risk`, then add:

```python
def test_hard_failure_cannot_be_held_as_episode_risk() -> None:
    smoother = ProtectionPresentationSmoother()
    smoother._episode_started_at = 10.0
    smoother._last_effective_risk = _failure_snapshot(
        1,
        ProtectionFailureReason.INVENTORY_UNAVAILABLE,
    )
    with pytest.raises(ProtectionSmoothingError, match="invalid risk snapshot"):
        smoother.resolve(_snapshot(2, ProtectionState.INACTIVE), now=10.1)
```

- [ ] **Step 5: Run tests and verify RED**

```bash
PYTHONPATH=src uv run pytest -q tests/test_protection_smoothing.py
```

Expected: new phase enum members are missing and mapping failures return BYPASS.

- [ ] **Step 6: Implement the allowlisted risk path**

In `protection_smoothing.py`, import `ProtectionFailureReason` and add:

```python
PRESENTATION_SMOOTHED_FAILURES = frozenset(
    {
        ProtectionFailureReason.ACTIVE_WINDOW_UNMAPPED,
        ProtectionFailureReason.SENSITIVE_WINDOW_UNMAPPED,
    }
)


def _is_smoothed_mapping_failure(snapshot: ProtectionSnapshot) -> bool:
    return (
        snapshot.state is ProtectionState.FAILED
        and snapshot.failure_reason in PRESENTATION_SMOOTHED_FAILURES
    )
```

Add both phase enum members. Rename `_last_effective_protected` to `_last_effective_risk` in code/tests. Classify inputs as:

```python
mapping_failure = _is_smoothed_mapping_failure(raw_snapshot)
risk_active = raw_snapshot.state is ProtectionState.PROTECTED or mapping_failure
hard_bypass = raw_snapshot.state is ProtectionState.PAUSED or (
    raw_snapshot.state is ProtectionState.FAILED and not mapping_failure
)
```

Hard bypass keeps existing behavior. Risk-active snapshots reuse the existing timer/style/reason logic and retain all current raw fields except `indicator_style`. Select transient/sustained PROTECTED or MAPPING_FAILURE phase from current raw state.

The inactive branch holds `_last_effective_risk`. Validate the held state is PROTECTED or
`_is_smoothed_mapping_failure(self._last_effective_risk)`; otherwise raise
`ProtectionSmoothingError("episode has invalid risk snapshot")`.

- [ ] **Step 7: Run focused tests, Ruff, and mutation checks**

```bash
PYTHONPATH=src uv run pytest -q tests/test_protection_smoothing.py
uv run ruff check src/openchronicle/capture/protection_smoothing.py tests/test_protection_smoothing.py
```

Mutations that must fail tests:

1. Remove `SENSITIVE_WINDOW_UNMAPPED` from the allowlist.
2. Reset the timer on FAILED -> PROTECTED.
3. Hold raw inactive instead of the last risk snapshot.
4. Smooth every FAILED reason.

- [ ] **Step 8: Commit Task 1**

```bash
git add src/openchronicle/capture/protection_smoothing.py tests/test_protection_smoothing.py
git commit -m "feat(capture): smooth transient mapping failures"
```

---

### Task 2: Monitor, Diagnostics, Policy, and Docs

**Files:**
- Modify: `tests/test_protection_monitor.py`
- Modify: `tests/test_privacy_diagnostics.py`
- Modify: `tests/test_capture_scheduler_fts.py`
- Modify: `docs/capture.md`
- Modify: `docs/macos-app.md`
- Modify: `docs/superpowers/specs/2026-08-25-unmapped-failure-presentation-smoothing-design.md`

**Interfaces:**
- Consumes: Task 1 allowlist and phases.
- Preserves: monitor constructor, one worker/Event, acknowledgement, diagnostics schema v1, and scheduler failure policy.

- [ ] **Step 1: Add monitor first-frame/promotion RED tests**

```python
MAPPING_FAILURES = (
    ProtectionFailureReason.ACTIVE_WINDOW_UNMAPPED,
    ProtectionFailureReason.SENSITIVE_WINDOW_UNMAPPED,
)


@pytest.mark.parametrize("reason", MAPPING_FAILURES)
def test_monitor_smooths_mapping_failure_without_changing_failed_state(
    reason, inventory, fake_overlay
) -> None:
    clock = FakeMonotonic()
    monitor = make_monitor(
        inventory=inventory,
        inventory_reader=lambda: InventoryReadResult(inventory, reason),
        overlay=fake_overlay,
        monotonic=clock,
    )
    transient = monitor.decision_for_capture(force=True)
    clock.advance(0.8)
    sustained = monitor.decision_for_capture(force=True)

    assert transient.raw_state is ProtectionState.FAILED
    assert transient.snapshot.state is ProtectionState.FAILED
    assert transient.snapshot.failure_reason is reason
    assert transient.presentation_phase is ProtectionPresentationPhase.TRANSIENT_MAPPING_FAILURE
    assert transient.snapshot.indicator_style == "quiet-shield"
    assert transient.overlay_reasons_enabled is False
    assert transient.indicator_confirmed is True
    assert fake_overlay.reason_visibility[:2] == [False, True]
    assert sustained.presentation_phase is ProtectionPresentationPhase.SUSTAINED_MAPPING_FAILURE
    assert sustained.snapshot.indicator_style == "pill"
```

Do not import the production allowlist into test expectations.

- [ ] **Step 2: Add cross-state and clear-cancellation monitor tests**

```python
def test_monitor_mapping_failure_to_protected_keeps_episode_deadline(
    inventory, fake_overlay
) -> None:
    readings = iter(
        [
            InventoryReadResult(
                inventory,
                ProtectionFailureReason.ACTIVE_WINDOW_UNMAPPED,
            ),
            inventory,
            inventory,
        ]
    )
    clock = FakeMonotonic()
    monitor = make_monitor(
        inventory=inventory,
        inventory_reader=lambda: next(readings),
        overlay=fake_overlay,
        monotonic=clock,
    )
    failed = monitor.decision_for_capture(force=True)
    clock.advance(0.4)
    protected = monitor.decision_for_capture(force=True)
    clock.advance(0.4)
    promoted = monitor.decision_for_capture(force=True)

    assert failed.presentation_phase is ProtectionPresentationPhase.TRANSIENT_MAPPING_FAILURE
    assert protected.presentation_phase is ProtectionPresentationPhase.TRANSIENT_PROTECTED
    assert promoted.presentation_phase is ProtectionPresentationPhase.SUSTAINED_PROTECTED
```

Add the cancellation test:

```python
def test_monitor_protected_return_cancels_failed_clear_pending(
    inventory, fake_overlay
) -> None:
    safe = WindowInventory(windows=(), displays=inventory.displays)
    readings = iter(
        [
            InventoryReadResult(
                inventory,
                ProtectionFailureReason.SENSITIVE_WINDOW_UNMAPPED,
            ),
            safe,
            inventory,
        ]
    )
    published: list[ProtectionDecision] = []
    clock = FakeMonotonic()
    monitor = make_monitor(
        inventory=inventory,
        inventory_reader=lambda: next(readings),
        overlay=fake_overlay,
        monotonic=clock,
        decision_listener=published.append,
    )
    monitor.decision_for_capture(force=True)
    clock.advance(0.1)
    held = monitor.decision_for_capture(force=True)
    clock.advance(0.1)
    returned = monitor.decision_for_capture(force=True)

    assert held.raw_state is ProtectionState.INACTIVE
    assert held.snapshot.state is ProtectionState.FAILED
    assert held.presentation_phase is ProtectionPresentationPhase.CLEAR_PENDING
    assert returned.raw_state is ProtectionState.PROTECTED
    assert returned.snapshot.state is ProtectionState.PROTECTED
    assert returned.presentation_phase is ProtectionPresentationPhase.TRANSIENT_PROTECTED
    assert all(item.snapshot.state is not ProtectionState.INACTIVE for item in published)
```

- [ ] **Step 3: Lock hard-failure and legacy fail-open policy**

Parameterize hard failure inputs:

```python
@pytest.mark.parametrize(
    "reason",
    [
        ProtectionFailureReason.INVENTORY_UNAVAILABLE,
        ProtectionFailureReason.EMPTY_DISPLAYS,
        ProtectionFailureReason.PAUSE_STATE_UNAVAILABLE,
        ProtectionFailureReason.PRESENTATION_STATE_INVALID,
    ],
)
def test_monitor_hard_failures_still_bypass_smoothing(
    reason, inventory, fake_overlay
) -> None:
    monitor = make_monitor(
        inventory=inventory,
        inventory_reader=lambda: InventoryReadResult(inventory, reason),
        overlay=fake_overlay,
    )
    decision = monitor.decision_for_capture(force=True)
    assert decision.snapshot.state is ProtectionState.FAILED
    assert decision.presentation_phase is ProtectionPresentationPhase.BYPASS
    assert decision.snapshot.indicator_style == "pill"
    assert decision.overlay_reasons_enabled is True
    assert monitor._next_smoothing_deadline is None
```

Add:

```python
def test_mapping_failure_smoothing_does_not_override_legacy_fail_open(
    inventory, fake_overlay
) -> None:
    monitor = make_monitor(
        inventory=inventory,
        inventory_reader=lambda: InventoryReadResult(
            inventory,
            ProtectionFailureReason.SENSITIVE_WINDOW_UNMAPPED,
        ),
        overlay=fake_overlay,
        fail_closed=False,
    )
    decision = monitor.decision_for_capture(force=True)
    assert decision.snapshot.state is ProtectionState.FAILED
    assert decision.presentation_phase is ProtectionPresentationPhase.TRANSIENT_MAPPING_FAILURE
    assert failure_requires_fail_closed(
        CaptureConfig(
            screenshot_monitor="separate",
            screenshot_privacy_fail_closed=False,
        ),
        decision.snapshot,
    ) is False
    assert fake_overlay.render_calls == 0
    assert fake_overlay.clear_calls == 1
    assert decision.indicator_confirmed is False
```

- [ ] **Step 4: Add category diagnostics privacy tests**

Serialize transient/sustained mapping-failure decisions through the real diagnostics serializer. Assert:

```python
assert transient["raw_state"] == "failed"
assert transient["state"] == "failed"
assert transient["presentation_phase"] == "transient-mapping-failure"
assert transient["indicator_style"] == "quiet-shield"
assert transient["overlay_reasons_enabled"] is False
assert sustained["presentation_phase"] == "sustained-mapping-failure"
assert sustained["indicator_style"] == "pill"
assert sustained["overlay_reasons_enabled"] is True
```

Insert unique private markers into app/bundle/title/alternate-title/rule fields and assert none appear in category JSON.

- [ ] **Step 5: Add scheduler policy regression tests**

Use the existing `_failed_decision` helper and keep state FAILED:

```python
def test_transient_mapping_failure_keeps_existing_scheduler_failure_policy() -> None:
    base = _failed_decision(reason=ProtectionFailureReason.ACTIVE_WINDOW_UNMAPPED)
    decision = replace(
        base,
        snapshot=replace(base.snapshot, indicator_style="quiet-shield"),
        presentation_phase=ProtectionPresentationPhase.TRANSIENT_MAPPING_FAILURE,
        overlay_reasons_enabled=False,
    )
    closed_cfg = CaptureConfig(
        screenshot_monitor="separate",
        screenshot_privacy_fail_closed=True,
    )
    open_cfg = CaptureConfig(
        screenshot_monitor="separate",
        screenshot_privacy_fail_closed=False,
    )
    assert scheduler_mod._decision_is_terminal(closed_cfg, decision) is True
    assert scheduler_mod._decision_blocks_ax(closed_cfg, decision) is True
    assert scheduler_mod._decision_is_terminal(open_cfg, decision) is False
    assert scheduler_mod._decision_blocks_ax(open_cfg, decision) is False
    assert scheduler_mod._filtered_capture_is_eligible(closed_cfg, decision) is False
```

The existing scheduler terminal-decision I/O tests remain in the focused suite and continue proving
that a terminal decision returns before AX, filtered helper, or MSS calls.

- [ ] **Step 6: Run integrated tests**

```bash
PYTHONPATH=src uv run pytest -q \
  tests/test_protection_smoothing.py \
  tests/test_protection_monitor.py \
  tests/test_privacy_diagnostics.py \
  tests/test_capture_scheduler_fts.py
```

Expected after Task 1: all pass with no warning or private marker.

- [ ] **Step 7: Update docs and design status**

Document the two-reason allowlist, unchanged effective FAILED/capture policy, 800ms quiet-to-configured presentation, cross-state timer, hard-failure bypass, legacy fail-open, and category phase fields. Set design status to `已实现，等待实机验证`.

- [ ] **Step 8: Run regression, Ruff, and mutations**

```bash
uv run ruff check \
  src/openchronicle/capture/protection_smoothing.py \
  tests/test_protection_smoothing.py \
  tests/test_protection_monitor.py \
  tests/test_privacy_diagnostics.py \
  tests/test_capture_scheduler_fts.py
PYTHONPATH=src uv run pytest -q \
  tests/test_protection.py tests/test_protection_reason.py \
  tests/test_protection_smoothing.py tests/test_privacy_overlay.py \
  tests/test_protection_monitor.py tests/test_daemon_protection.py \
  tests/test_privacy_diagnostics.py tests/test_capture_scheduler_fts.py
```

Mutations that must fail: publish raw FAILED instead of effective; enable transient reasons; override legacy fail-open; reset the cross-state timer.

- [ ] **Step 9: Commit Task 2**

```bash
git add tests/test_protection_monitor.py tests/test_privacy_diagnostics.py \
  tests/test_capture_scheduler_fts.py docs/capture.md docs/macos-app.md \
  docs/superpowers/specs/2026-08-25-unmapped-failure-presentation-smoothing-design.md
git commit -m "test(capture): lock mapping failure presentation boundaries"
```

---

### Task 3: Full Gate, Install, and Live Verification

**Files:**
- Modify after verification: `docs/superpowers/specs/2026-08-25-unmapped-failure-presentation-smoothing-design.md`
- Verify only: `/Users/tkandi/.openchronicle/config.toml`
- Install: backend/helpers and `/Applications/OpenChronicle.app`

- [ ] **Step 1: Run the complete automated gate**

```bash
git diff --check
PYTHONPATH=src uv run pytest -q
uv run ruff check src tests
swiftc -module-cache-path /tmp/openchronicle-swift-module-cache \
  resources/mac-privacy-overlay-reason.swift resources/mac-privacy-overlay-core.swift \
  tests/swift/MacPrivacyOverlayCoreTests.swift \
  -o /tmp/openchronicle-overlay-core-tests-unmapped-smoothing -framework AppKit
/tmp/openchronicle-overlay-core-tests-unmapped-smoothing
CLANG_MODULE_CACHE_PATH=/tmp/openchronicle-clang-module-cache \
SWIFTPM_MODULECACHE_OVERRIDE=/tmp/openchronicle-swiftpm-module-cache \
swift test --package-path macos/OpenChronicleApp
```

Full Python/native/Swift must pass. Record existing unrelated full-Ruff findings; changed files must be clean.

- [ ] **Step 2: Preserve config and install both layers**

Record raw/canonical config SHA-256 without values, quit and verify the old process chain exits, then run:

```bash
bash install.sh --no-client-config
bash scripts/install-macos-app.sh
```

Hashes must remain identical. Verify codesign and one App -> daemon -> overlay + AX watcher chain.

- [ ] **Step 3: Run category-only blank-InPrivate verification**

Required confirmed evidence:

1. Normal mapped first frame is transient protected/quiet shield/reasons false and blocks its display.
2. Space/F3 mapping failure is raw/effective FAILED with `transient-mapping-failure`, quiet shield, reasons false, and unchanged blocking policy.
3. No transient mapping-failure generation uses configured pill/banner/border.
4. A mapping failure lasting 800ms becomes `sustained-mapping-failure`, configured style, reasons true.
5. Mapping failure -> PROTECTED does not restart the episode.
6. Raw inactive enters clear-pending and second-safe clear; deterministic cancellation test remains authoritative if macOS cannot induce sub-200ms reversal.
7. Repeat on secondary display and cross-display movement.

The user-visible acceptance is that quick Space switching may show a quiet shield but no longer flashes full “截图已停用”.

- [ ] **Step 4: Verify capture artifacts structurally**

Use fixed log categories and JSON key/presence checks only: protected monitor skipped, `ax_skipped=protected_display`, and only unprotected monitor IDs contain image presence. Never decode/display image/text/title/URL/rule content.

- [ ] **Step 5: Mark verified and commit**

Set design status to `已实现并验证` only after gates pass:

```bash
git add docs/superpowers/specs/2026-08-25-unmapped-failure-presentation-smoothing-design.md
git commit -m "docs: verify unmapped failure presentation smoothing"
```

- [ ] **Step 6: Final regression and branch finish**

```bash
PYTHONPATH=src uv run pytest -q
CLANG_MODULE_CACHE_PATH=/tmp/openchronicle-clang-module-cache \
SWIFTPM_MODULECACHE_OVERRIDE=/tmp/openchronicle-swiftpm-module-cache \
swift test --package-path macos/OpenChronicleApp
git status --short --branch
```

Invoke `superpowers:finishing-a-development-branch`; do not merge or push until the user chooses.
