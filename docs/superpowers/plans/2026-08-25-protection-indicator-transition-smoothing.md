# Protection Indicator Transition Smoothing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep screenshot and AX protection immediate while presenting short protected episodes as a quiet shield, promoting sustained protection after 800ms, and clearing only after a second safe inventory at least 200ms later.

**Architecture:** A new pure Python `ProtectionPresentationSmoother` transforms raw protection snapshots into effective snapshots without reading files, spawning threads, or calling the native helper. `PrivacyProtectionMonitor` remains the sole owner of inventory reads, generations, helper acknowledgements, overlay window IDs, scheduling, freshness checks, and published decisions; it folds smoother deadlines into its existing `threading.Event` watchdog and uses one injected monotonic clock for snapshot construction, smoothing, cache freshness, and deadline arithmetic. `PrivacyOverlayClient` receives an internal reason-visibility flag, suppressing transient overlay reasons without changing the native NDJSON schema or removing structured reasons from diagnostics.

**Tech Stack:** Python 3.11+, dataclasses, `time.monotonic`, `threading.Event`, pytest, Ruff, Swift/AppKit helper regression tests, SwiftUI App tests.

## Global Constraints

- Raw privacy detection, denylist matching, screenshots, AX gating, and Mission Control window enumeration remain unchanged.
- Screenshot and AX blocking begins on the first raw `protected` snapshot.
- Exact constants: `PROTECTED_PROMOTION_SECONDS = 0.8` and `SAFE_CONFIRMATION_SECONDS = 0.2`.
- A transient visible indicator is `quiet-shield`; configured `off` remains visually off.
- A sustained protected episode uses the latest configured final style and placement.
- The first safe inventory after protection does not restore capture; a second safe inventory at or after the 200ms deadline is required.
- Capture events before the clear deadline may refresh state but cannot count as the second safe confirmation.
- `paused` and `failed` bypass smoothing and render immediately; configured `off` never creates an indicator.
- Protected display/reason/window-set changes do not reset the episode timer.
- Every transient, promotion, clear-pending, and inactive publication keeps monotonic generation, helper acknowledgement, overlay window-ID, and scheduler authorization causality.
- Transient overlay commands contain no reasons; effective snapshots retain complete structured reasons for diagnostics and policy.
- The native overlay protocol gains no new field and the Swift helper owns no promotion or clear timer.
- No additional thread, `threading.Timer`, private Mission Control API, TOML setting, or SwiftUI control is introduced.
- Invalid smoother generation/order state must fail closed even when `screenshot_privacy_fail_closed = false`.
- Logs may include phase, generation, display IDs, and confirmation status, but never exact titles, URLs, rules, or reason values.
- Source specification: `docs/superpowers/specs/2026-08-25-protection-indicator-transition-smoothing-design.md`.

## File Map

- Create `src/openchronicle/capture/protection_smoothing.py`: pure presentation state machine and result types.
- Create `tests/test_protection_smoothing.py`: deterministic virtual-time state-machine tests.
- Modify `src/openchronicle/capture/privacy_overlay.py`: internal overlay-reason suppression flag; no wire schema change.
- Modify `tests/test_privacy_overlay.py`: transient/sustained command payload tests.
- Modify `src/openchronicle/capture/privacy.py`: internal smoothing failure reason.
- Modify `src/openchronicle/capture/protection_reason.py`: matching bounded failure reason code.
- Modify `src/openchronicle/capture/protection.py`: force smoothing invariant failures closed.
- Modify `src/openchronicle/capture/protection_monitor.py`: smoother integration, deadline-aware watchdog, effective-decision publication, and phase logging.
- Modify monitor-interface fakes in `tests/test_protection_monitor.py`, `tests/test_daemon_protection.py`, `tests/test_privacy_diagnostics.py`, and `tests/test_capture_scheduler_fts.py`.
- Modify `docs/capture.md`, `docs/macos-app.md`, and the approved design status after implementation.

---

### Task 1: Pure Protection Presentation Smoother

**Files:**
- Create: `src/openchronicle/capture/protection_smoothing.py`
- Create: `tests/test_protection_smoothing.py`

**Interfaces:**
- Consumes: `ProtectionSnapshot`, `ProtectionState` from `openchronicle.capture.protection`.
- Produces: `PROTECTED_PROMOTION_SECONDS: float = 0.8`.
- Produces: `SAFE_CONFIRMATION_SECONDS: float = 0.2`.
- Produces: `ProtectionPresentationPhase(StrEnum)` with exact values `inactive`, `transient-protected`, `sustained-protected`, `clear-pending`, and `bypass`.
- Produces: `ProtectionPresentationResult(snapshot, phase, next_deadline, overlay_reasons_enabled)`.
- Produces: `ProtectionSmoothingError(RuntimeError)` for non-increasing generations or inconsistent internal state.
- Produces: `ProtectionPresentationSmoother.resolve(raw_snapshot, *, now) -> ProtectionPresentationResult` and `reset() -> None`.

- [ ] **Step 1: Write the failing state-machine tests**

Create `tests/test_protection_smoothing.py` with a snapshot factory that carries real protected displays, reasons, protected window IDs, placement, and freshness:

```python
from __future__ import annotations

from dataclasses import replace

import pytest

from openchronicle.capture.privacy import DisplayInfo, ScreenRegion
from openchronicle.capture.protection import ProtectionSnapshot, ProtectionState
from openchronicle.capture.protection_reason import (
    DisplayProtectionReasons,
    ProtectionReason,
    ProtectionReasonCode,
)
from openchronicle.capture.protection_smoothing import (
    ProtectionPresentationPhase,
    ProtectionPresentationSmoother,
    ProtectionSmoothingError,
)


DISPLAY = DisplayInfo(1, ScreenRegion(0, 0, 100, 100), True)
REASON = ProtectionReason(
    ProtectionReasonCode.WINDOW_TITLE_RULE,
    display_id=1,
    app_name="Edge",
    window_title="InPrivate",
)


def _snapshot(
    generation: int,
    state: ProtectionState,
    *,
    style: str = "pill",
    now: float = 10.0,
    protected_ids: frozenset[int] = frozenset({1}),
) -> ProtectionSnapshot:
    protected = state is ProtectionState.PROTECTED
    return ProtectionSnapshot(
        generation=generation,
        state=state,
        capture_mode="separate",
        indicator_style=style,
        displays=(DISPLAY,),
        protected_display_ids=protected_ids if protected else frozenset(),
        active_display_id=1,
        created_monotonic=now,
        fresh_until=now + 0.25,
        display_reasons=DisplayProtectionReasons.from_reasons([REASON]) if protected else (
            DisplayProtectionReasons()
        ),
        protected_window_ids=frozenset({41}) if protected else frozenset(),
        protected_window_regions=(ScreenRegion(0, 0, 50, 50),) if protected else (),
        window_filterable=protected,
    )
```

Add these exact behavioral tests:

```python
def test_short_protection_stays_quiet_then_promotes_at_800ms() -> None:
    smoother = ProtectionPresentationSmoother()
    first = smoother.resolve(_snapshot(1, ProtectionState.PROTECTED), now=10.0)
    before = smoother.resolve(_snapshot(2, ProtectionState.PROTECTED), now=10.799)
    promoted = smoother.resolve(_snapshot(3, ProtectionState.PROTECTED), now=10.8)

    assert first.phase is ProtectionPresentationPhase.TRANSIENT_PROTECTED
    assert first.snapshot.indicator_style == "quiet-shield"
    assert first.snapshot.display_reasons.reasons == (REASON,)
    assert first.snapshot.ax_blocked is True
    assert first.overlay_reasons_enabled is False
    assert first.next_deadline == pytest.approx(10.8)
    assert before.snapshot.indicator_style == "quiet-shield"
    assert promoted.phase is ProtectionPresentationPhase.SUSTAINED_PROTECTED
    assert promoted.snapshot.indicator_style == "pill"
    assert promoted.overlay_reasons_enabled is True
    assert promoted.next_deadline is None


def test_safe_clear_requires_second_sample_after_200ms() -> None:
    smoother = ProtectionPresentationSmoother()
    protected = smoother.resolve(_snapshot(1, ProtectionState.PROTECTED), now=10.0)
    first_safe = smoother.resolve(_snapshot(2, ProtectionState.INACTIVE), now=10.1)
    early_safe = smoother.resolve(_snapshot(3, ProtectionState.INACTIVE), now=10.299)
    confirmed_safe = smoother.resolve(_snapshot(4, ProtectionState.INACTIVE), now=10.3)

    assert protected.snapshot.state is ProtectionState.PROTECTED
    assert first_safe.phase is ProtectionPresentationPhase.CLEAR_PENDING
    assert first_safe.snapshot.state is ProtectionState.PROTECTED
    assert first_safe.snapshot.ax_blocked is True
    assert first_safe.next_deadline == pytest.approx(10.3)
    assert early_safe.snapshot.state is ProtectionState.PROTECTED
    assert confirmed_safe.phase is ProtectionPresentationPhase.INACTIVE
    assert confirmed_safe.snapshot.state is ProtectionState.INACTIVE
```

Also add independent tests for:

```python
def test_protection_returning_during_clear_pending_keeps_original_episode() -> None:
    smoother = ProtectionPresentationSmoother()
    smoother.resolve(_snapshot(1, ProtectionState.PROTECTED), now=10.0)
    smoother.resolve(_snapshot(2, ProtectionState.INACTIVE), now=10.1)
    returned = smoother.resolve(_snapshot(3, ProtectionState.PROTECTED), now=10.2)
    promoted = smoother.resolve(_snapshot(4, ProtectionState.PROTECTED), now=10.8)

    assert returned.phase is ProtectionPresentationPhase.TRANSIENT_PROTECTED
    assert returned.next_deadline == pytest.approx(10.8)
    assert promoted.phase is ProtectionPresentationPhase.SUSTAINED_PROTECTED


def test_protected_data_changes_do_not_restart_episode() -> None:
    smoother = ProtectionPresentationSmoother()
    smoother.resolve(_snapshot(1, ProtectionState.PROTECTED), now=10.0)
    changed = replace(
        _snapshot(2, ProtectionState.PROTECTED),
        protected_display_ids=frozenset({1, 2}),
        protected_window_ids=frozenset({41, 42}),
    )
    result = smoother.resolve(changed, now=10.8)
    assert result.phase is ProtectionPresentationPhase.SUSTAINED_PROTECTED


@pytest.mark.parametrize("style", ["quiet-shield", "off"])
def test_quiet_and_off_promote_without_inventing_another_visual_style(style: str) -> None:
    smoother = ProtectionPresentationSmoother()
    first = smoother.resolve(_snapshot(1, ProtectionState.PROTECTED, style=style), now=10.0)
    promoted = smoother.resolve(_snapshot(2, ProtectionState.PROTECTED, style=style), now=10.8)
    expected_transient = "off" if style == "off" else "quiet-shield"
    assert first.snapshot.indicator_style == expected_transient
    assert promoted.snapshot.indicator_style == style
    assert promoted.phase is ProtectionPresentationPhase.SUSTAINED_PROTECTED
    assert promoted.snapshot.generation == 2
```

Add these additional tests:

```python
@pytest.mark.parametrize("state", [ProtectionState.PAUSED, ProtectionState.FAILED])
def test_pause_and_failure_bypass_smoothing(state: ProtectionState) -> None:
    smoother = ProtectionPresentationSmoother()
    result = smoother.resolve(_snapshot(1, state, style="banner"), now=10.0)
    assert result.phase is ProtectionPresentationPhase.BYPASS
    assert result.snapshot.indicator_style == "banner"
    assert result.overlay_reasons_enabled is True
    assert result.next_deadline is None


def test_transient_style_and_placement_reload_without_resetting_episode() -> None:
    smoother = ProtectionPresentationSmoother()
    smoother.resolve(_snapshot(1, ProtectionState.PROTECTED), now=10.0)
    changed = replace(
        _snapshot(2, ProtectionState.PROTECTED, style="border"),
        indicator_placement="bottom-right-work-area",
    )
    transient = smoother.resolve(changed, now=10.4)
    promoted = smoother.resolve(replace(changed, generation=3), now=10.8)
    assert transient.snapshot.indicator_style == "quiet-shield"
    assert transient.snapshot.indicator_placement == "bottom-right-work-area"
    assert transient.next_deadline == pytest.approx(10.8)
    assert promoted.snapshot.indicator_style == "border"


def test_clear_pending_holds_protected_data_with_fresh_generation_and_position() -> None:
    smoother = ProtectionPresentationSmoother()
    protected = smoother.resolve(_snapshot(1, ProtectionState.PROTECTED), now=10.0)
    safe = replace(
        _snapshot(2, ProtectionState.INACTIVE, now=10.1),
        indicator_placement="bottom-left-inset",
    )
    held = smoother.resolve(safe, now=10.1)
    assert held.snapshot.generation == 2
    assert held.snapshot.created_monotonic == 10.1
    assert held.snapshot.protected_display_ids == protected.snapshot.protected_display_ids
    assert held.snapshot.protected_window_ids == protected.snapshot.protected_window_ids
    assert held.snapshot.display_reasons == protected.snapshot.display_reasons
    assert held.snapshot.indicator_placement == "bottom-left-inset"


def test_reset_clears_deadlines_and_generation_memory() -> None:
    smoother = ProtectionPresentationSmoother()
    smoother.resolve(_snapshot(5, ProtectionState.PROTECTED), now=10.0)
    smoother.reset()
    restarted = smoother.resolve(_snapshot(1, ProtectionState.PROTECTED), now=20.0)
    assert restarted.phase is ProtectionPresentationPhase.TRANSIENT_PROTECTED
    assert restarted.next_deadline == pytest.approx(20.8)


def test_non_increasing_generation_is_rejected() -> None:
    smoother = ProtectionPresentationSmoother()
    smoother.resolve(_snapshot(5, ProtectionState.PROTECTED), now=10.0)
    with pytest.raises(ProtectionSmoothingError, match="strictly increase"):
        smoother.resolve(_snapshot(5, ProtectionState.PROTECTED), now=10.1)


@pytest.mark.parametrize(
    ("promotion_seconds", "safe_confirmation_seconds"),
    [(-0.001, 0.2), (0.8, -0.001)],
)
def test_negative_delays_are_rejected(
    promotion_seconds: float,
    safe_confirmation_seconds: float,
) -> None:
    with pytest.raises(ValueError, match="non-negative"):
        ProtectionPresentationSmoother(
            promotion_seconds=promotion_seconds,
            safe_confirmation_seconds=safe_confirmation_seconds,
        )


def test_impossible_held_state_is_rejected() -> None:
    smoother = ProtectionPresentationSmoother()
    smoother._episode_started_at = 10.0
    with pytest.raises(ProtectionSmoothingError, match="protected snapshot"):
        smoother.resolve(_snapshot(1, ProtectionState.INACTIVE), now=10.1)
```

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```bash
PYTHONPATH=src uv run pytest -q tests/test_protection_smoothing.py
```

Expected: collection fails because `openchronicle.capture.protection_smoothing` does not exist.

- [ ] **Step 3: Implement the pure module**

Create `src/openchronicle/capture/protection_smoothing.py` with no logger, file, thread, or helper imports:

```python
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

from .protection import ProtectionSnapshot, ProtectionState

PROTECTED_PROMOTION_SECONDS = 0.8
SAFE_CONFIRMATION_SECONDS = 0.2


class ProtectionPresentationPhase(StrEnum):
    INACTIVE = "inactive"
    TRANSIENT_PROTECTED = "transient-protected"
    SUSTAINED_PROTECTED = "sustained-protected"
    CLEAR_PENDING = "clear-pending"
    BYPASS = "bypass"


class ProtectionSmoothingError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProtectionPresentationResult:
    snapshot: ProtectionSnapshot
    phase: ProtectionPresentationPhase
    next_deadline: float | None
    overlay_reasons_enabled: bool
```

Implement `ProtectionPresentationSmoother` with these private fields:

```python
self._episode_started_at: float | None = None
self._clear_deadline: float | None = None
self._last_effective_protected: ProtectionSnapshot | None = None
self._last_generation: int | None = None
self._last_overlay_reasons_enabled = False
```

Constructor keyword-only delay arguments default to the two module constants and reject negative
values. `resolve` must first require `raw_snapshot.generation > self._last_generation`.

Implement a private `_reset_episode()` that clears episode/deadline/held-snapshot state but preserves
`_last_generation`. Public `reset()` clears both episode state and generation memory and is reserved
for explicit recovery after a caught invariant error and isolated tests. Normal inactive completion
and paused/failed bypass call `_reset_episode()`, so generation validation remains monotonic.

For `PAUSED` and `FAILED`, reset the episode but retain `_last_generation`, then return the raw
snapshot with phase `BYPASS`, no deadline, and reasons enabled. For `PROTECTED`, start the episode only
when `_episode_started_at is None`, cancel `_clear_deadline`, and use:

```python
promoted = now >= self._episode_started_at + self._promotion_seconds
effective_style = raw_snapshot.indicator_style
reasons_enabled = promoted and effective_style != "off"
if not promoted and effective_style != "off":
    effective_style = "quiet-shield"
effective = replace(raw_snapshot, indicator_style=effective_style)
```

For the first `INACTIVE` after protection, set `_clear_deadline = now + self._safe_confirmation_seconds`
and return a held copy of `_last_effective_protected`. On every pre-deadline inactive refresh, update
only these fields from the raw snapshot:

```python
generation
created_monotonic
fresh_until
indicator_placement
```

Keep protected displays, reasons, protected window IDs/regions, effective style, and prior
`overlay_reasons_enabled`. At or after the deadline, reset the episode, retain the new generation,
and return raw inactive. Plain inactive without an episode returns raw inactive immediately with
reasons enabled and no deadline. If an episode exists without a held protected snapshot, raise
`ProtectionSmoothingError` rather than constructing a permissive result.

- [ ] **Step 4: Run tests, mutation checks, and Ruff**

Run:

```bash
PYTHONPATH=src uv run pytest -q tests/test_protection_smoothing.py
uv run ruff check src/openchronicle/capture/protection_smoothing.py tests/test_protection_smoothing.py
```

Expected: all tests pass and Ruff is clean.

Mutation checks:

1. Change promotion comparison from `>=` to `>`; the exact 800ms test must fail.
2. Remove the clear-deadline comparison; the 199ms test must fail.
3. Reset `_episode_started_at` when protected data changes; the set-change test must fail.

Restore each mutation and rerun the focused suite.

- [ ] **Step 5: Commit Task 1**

```bash
git add src/openchronicle/capture/protection_smoothing.py tests/test_protection_smoothing.py
git commit -m "feat(capture): add protection presentation smoother"
```

---

### Task 2: Transient Overlay Reason Suppression

**Files:**
- Modify: `src/openchronicle/capture/privacy_overlay.py:476-520,632-672`
- Modify: `tests/test_privacy_overlay.py`

**Interfaces:**
- Consumes: `ProtectionPresentationResult.overlay_reasons_enabled` from Task 1.
- Produces: `PrivacyOverlayClient.render(snapshot, timeout=0.5, *, overlay_reasons_enabled=True) -> bool`.
- Produces: `_render_command(snapshot, *, overlay_reasons_enabled=True) -> dict[str, Any]`.
- Preserves: exact native render-command schema, placement, style, reason policy fields, acknowledgement schema, generation handling, and clear commands.

- [ ] **Step 1: Add failing render-command tests**

In `tests/test_privacy_overlay.py`, add:

```python
def test_transient_command_suppresses_only_overlay_reason_payloads() -> None:
    reason = _private_title_reason(2)
    snapshot = _protected_snapshot(reasons=(reason,))

    command = PrivacyOverlayClient._render_command(
        replace(snapshot, indicator_style="quiet-shield"),
        overlay_reasons_enabled=False,
    )

    assert command["style"] == "quiet-shield"
    assert command["reason_display"] == snapshot.reason_display
    assert command["reason_detail"] == snapshot.reason_detail
    assert command["reason_trigger"] == snapshot.reason_trigger
    assert command["displays"][0]["reasons"] == []
    assert command["reasons"] == []
    assert snapshot.display_reasons.reasons == (reason,)


def test_sustained_quiet_shield_restores_configured_reason_payloads() -> None:
    reason = _private_title_reason(2)
    snapshot = replace(
        _protected_snapshot(reasons=(reason,)),
        indicator_style="quiet-shield",
    )
    command = PrivacyOverlayClient._render_command(
        snapshot,
        overlay_reasons_enabled=True,
    )
    assert command["displays"][0]["reasons"][0]["code"] == "window_title_rule"
```

Add this transport-level test:

```python
def test_transient_render_keeps_acknowledgement_and_window_ids(
    fake_transport: FakeTransport,
) -> None:
    fake_transport.responses = [True]
    client = PrivacyOverlayClient(transport_factory=lambda: fake_transport)
    snapshot = _protected_snapshot(reasons=(_private_title_reason(2),))

    assert client.render(snapshot, overlay_reasons_enabled=False) is True

    command = json.loads(fake_transport.writes[-1])
    assert command["displays"][0]["reasons"] == []
    assert command["reasons"] == []
    assert client.confirmed_window_ids(snapshot.generation) == ()
```

Keep the existing exact-acknowledgement test with non-empty `(7, 4294967295)` IDs and invoke its
render call with `overlay_reasons_enabled=False`; its existing sorted-ID assertion must remain green.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
PYTHONPATH=src uv run pytest -q \
  tests/test_privacy_overlay.py::test_transient_command_suppresses_only_overlay_reason_payloads \
  tests/test_privacy_overlay.py::test_sustained_quiet_shield_restores_configured_reason_payloads
```

Expected: `TypeError` because `_render_command` does not accept `overlay_reasons_enabled`.

- [ ] **Step 3: Implement the keyword-only flag**

Preserve positional timeout compatibility:

```python
def render(
    self,
    snapshot: ProtectionSnapshot,
    timeout: float = 0.5,
    *,
    overlay_reasons_enabled: bool = True,
) -> bool:
```

Pass the flag to `_render_command`. Extend `_render_command` with the same keyword-only default. When
false, serialize `reasons: []` for every display and for the all-displays fallback; leave
`reason_display`, `reason_detail`, and `reason_trigger` unchanged. Do not modify `snapshot` or
`DisplayProtectionReasons`.

- [ ] **Step 4: Run overlay and boundary tests**

Run:

```bash
PYTHONPATH=src uv run pytest -q tests/test_privacy_overlay.py tests/test_privacy_reason_boundaries.py
uv run ruff check src/openchronicle/capture/privacy_overlay.py tests/test_privacy_overlay.py
```

Expected: all tests pass and no private marker appears in transient command payloads.

Mutation check: force `_render_command` to serialize the original reasons when the flag is false;
the transient payload test must fail. Restore and rerun.

- [ ] **Step 5: Commit Task 2**

```bash
git add src/openchronicle/capture/privacy_overlay.py tests/test_privacy_overlay.py
git commit -m "feat(capture): suppress transient overlay reasons"
```

---

### Task 3: Monitor Integration, Deadlines, and Fail-Closed Boundaries

**Files:**
- Modify: `src/openchronicle/capture/privacy.py:59-70`
- Modify: `src/openchronicle/capture/protection_reason.py:13-55`
- Modify: `src/openchronicle/capture/protection.py:43-54`
- Modify: `src/openchronicle/capture/protection_monitor.py:1-470`
- Modify: `tests/test_protection.py`
- Modify: `tests/test_protection_reason.py`
- Modify: `tests/test_protection_monitor.py`
- Modify: `tests/test_daemon_protection.py`
- Modify: `tests/test_privacy_diagnostics.py`
- Modify: `tests/test_capture_scheduler_fts.py`
- Modify: `macos/OpenChronicleApp/Sources/OpenChronicleApp/Models/ProtectionDiagnostics.swift`
- Modify: `macos/OpenChronicleApp/Sources/OpenChronicleApp/Views/ProtectionDiagnosticsView.swift`
- Modify: `macos/OpenChronicleApp/Tests/OpenChronicleAppTests/ConfigurationTests.swift`

**Interfaces:**
- Consumes: `ProtectionPresentationSmoother`, `ProtectionPresentationResult`, and phase enum from Task 1.
- Consumes: `PrivacyOverlayClient.render(snapshot, timeout=0.5, *, overlay_reasons_enabled=True)` from Task 2.
- Produces: keyword-only `ProtectionDecision.presentation_phase` and `overlay_reasons_enabled` defaults, preserving existing positional constructors.
- Produces: deadline-aware monitor wait using the existing single monitor thread.
- Produces: one injected monotonic time domain for raw/effective snapshot freshness and smoothing deadlines; external blocking API timeouts remain wall-clock based.
- Produces: `ProtectionFailureReason.PRESENTATION_STATE_INVALID` and matching `ProtectionReasonCode`.

- [ ] **Step 1: Update fake overlay interfaces before monitor RED tests**

In every test fake listed in the Files section, preserve timeout compatibility and add:

```python
def render(
    self,
    snapshot: ProtectionSnapshot,
    timeout: float = 0.5,
    *,
    overlay_reasons_enabled: bool = True,
) -> bool:
```

Recording fakes must store `(snapshot, overlay_reasons_enabled)` so integration tests can assert the
transient and sustained commands. Do not weaken fakes to unrestricted `**kwargs` because that would
hide interface regressions.

For `tests/test_protection_monitor.py`, extend `FakeOverlay` exactly with:

```python
self.reason_visibility: list[bool] = []
self.window_ids_by_generation: dict[int, tuple[int, ...]] = {}

def render(
    self,
    snapshot: ProtectionSnapshot,
    timeout: float = 0.5,
    *,
    overlay_reasons_enabled: bool = True,
) -> bool:
    self.render_calls += 1
    if self.terminal_marked.is_set():
        return False
    self.snapshots.append(snapshot)
    self.reason_visibility.append(overlay_reasons_enabled)
    if self.render_result and snapshot.indicator_style != "off":
        self.window_ids_by_generation[snapshot.generation] = (7, 41)
    return self.render_result

def confirmed_window_ids(self, generation: int) -> tuple[int, ...]:
    return self.window_ids_by_generation.get(generation, ())
```

Its `clear` method continues returning no IDs. Other fakes may record the boolean but return empty IDs
when their test is not about filtered-capture eligibility.

- [ ] **Step 2: Add failing monitor integration tests**

Extend the `make_monitor` helper with:

```python
smoother: ProtectionPresentationSmoother | None = None,
monotonic: Callable[[], float] = time.monotonic,
```

Forward both keyword arguments to `PrivacyProtectionMonitor`. Add a small mutable clock:

```python
class FakeMonotonic:
    def __init__(self, value: float = 10.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds
```

Lock the cache freshness comparison to that injected clock rather than the module-level clock:

```python
def test_monitor_uses_injected_clock_for_snapshot_and_cache_freshness(
    tmp_path, inventory, fake_overlay, monkeypatch
) -> None:
    clock = FakeMonotonic()
    monitor = make_monitor(
        config_path=tmp_path / "config.toml",
        inventory=inventory,
        overlay=fake_overlay,
        monotonic=clock,
    )

    def unexpected_wall_clock() -> float:
        raise AssertionError("decision freshness used the module-level monotonic clock")

    monkeypatch.setattr(
        "openchronicle.capture.protection_monitor.time.monotonic",
        unexpected_wall_clock,
    )
    decision = monitor.decision_for_capture(force=True)
    assert decision.snapshot.created_monotonic == 10.0
    assert decision.snapshot.fresh_until == pytest.approx(10.25)
```

Add tests that force monitor refreshes at exact times:

```python
def test_monitor_publishes_quiet_then_configured_style_with_new_generations(
    tmp_path, inventory, fake_overlay
) -> None:
    clock = FakeMonotonic()
    monitor = make_monitor(
        config_path=tmp_path / "config.toml",
        inventory=inventory,
        overlay=fake_overlay,
        monotonic=clock,
    )
    transient = monitor.decision_for_capture(force=True)
    clock.advance(0.8)
    sustained = monitor.decision_for_capture(force=True)

    assert transient.snapshot.state is ProtectionState.PROTECTED
    assert transient.snapshot.indicator_style == "quiet-shield"
    assert transient.overlay_reasons_enabled is False
    assert sustained.snapshot.indicator_style == "pill"
    assert sustained.overlay_reasons_enabled is True
    assert sustained.snapshot.generation > transient.snapshot.generation
    assert transient.indicator_confirmed and sustained.indicator_confirmed
```

Add this inventory-sequence test. Extend `FakeOverlay` so successful renders expose stable confirmed
IDs `(7, 41)` through `confirmed_window_ids(generation)` exactly like the real client:

```python
def test_monitor_holds_capture_until_safe_confirmation_deadline(
    tmp_path: Path,
    inventory: WindowInventory,
    fake_overlay: FakeOverlay,
) -> None:
    safe_inventory = WindowInventory(windows=(), displays=inventory.displays)
    readings = iter([inventory, safe_inventory, safe_inventory, safe_inventory])
    clock = FakeMonotonic()
    monitor = make_monitor(
        config_path=tmp_path / "config.toml",
        inventory=inventory,
        inventory_reader=lambda: next(readings),
        overlay=fake_overlay,
        monotonic=clock,
    )

    protected = monitor.decision_for_capture(force=True)
    clock.advance(0.1)
    first_safe = monitor.decision_for_capture(force=True)
    clock.advance(0.199)
    early_safe = monitor.decision_for_capture(force=True)
    clock.advance(0.001)
    confirmed_safe = monitor.decision_for_capture(force=True)

    assert protected.snapshot.indicator_style == "quiet-shield"
    for held in (first_safe, early_safe):
        assert held.snapshot.state is ProtectionState.PROTECTED
        assert held.snapshot.protected_display_ids == frozenset({2})
        assert held.snapshot.protected_window_ids
        assert held.indicator_confirmed is True
        assert held.indicator_window_ids == (7, 41)
        assert held.presentation_phase is ProtectionPresentationPhase.CLEAR_PENDING
    assert confirmed_safe.snapshot.state is ProtectionState.INACTIVE
    assert confirmed_safe.snapshot.generation > early_safe.snapshot.generation
```

Add this return-during-clear test with readings `[inventory, safe_inventory, inventory]`:

```python
def test_monitor_cancels_clear_when_protection_returns(
    tmp_path, inventory, fake_overlay
) -> None:
    safe_inventory = WindowInventory(windows=(), displays=inventory.displays)
    readings = iter([inventory, safe_inventory, inventory])
    clock = FakeMonotonic()
    monitor = make_monitor(
        config_path=tmp_path / "config.toml",
        inventory=inventory,
        inventory_reader=lambda: next(readings),
        overlay=fake_overlay,
        monotonic=clock,
    )
    monitor.decision_for_capture(force=True)
    clock.advance(0.1)
    monitor.decision_for_capture(force=True)
    clock.advance(0.1)
    returned = monitor.decision_for_capture(force=True)
    assert returned.snapshot.state is ProtectionState.PROTECTED
    assert returned.presentation_phase is ProtectionPresentationPhase.TRANSIENT_PROTECTED
    assert returned.snapshot.indicator_style == "quiet-shield"
```

Add a real-thread test with a `ProtectionPresentationSmoother(promotion_seconds=0.03)`. Give
`FakeOverlay` a condition/event set after every render:

```python
def test_worker_wakes_at_promotion_deadline_without_another_timer(
    tmp_path, inventory, fake_overlay
) -> None:
    before = {thread.ident for thread in threading.enumerate()}
    monitor = make_monitor(
        config_path=tmp_path / "config.toml",
        inventory=inventory,
        overlay=fake_overlay,
        smoother=ProtectionPresentationSmoother(promotion_seconds=0.03),
        watchdog_seconds=10.0,
    )
    monitor.start()
    try:
        deadline = time.monotonic() + 0.3
        while time.monotonic() < deadline:
            if [snapshot.indicator_style for snapshot in fake_overlay.snapshots][:2] == [
                "quiet-shield",
                "pill",
            ]:
                break
            time.sleep(0.005)
        assert [snapshot.indicator_style for snapshot in fake_overlay.snapshots][:2] == [
            "quiet-shield",
            "pill",
        ]
        created = [
            thread
            for thread in threading.enumerate()
            if thread.ident not in before and thread.name == "privacy-protection-monitor"
        ]
        assert len(created) == 1
    finally:
        renders_before_stop = fake_overlay.render_calls
        monitor.stop()
        time.sleep(0.05)
    assert fake_overlay.render_calls == renders_before_stop
```

Add an atomic config-reload test derived from the existing style/placement test:

```python
def test_transient_and_sustained_use_latest_hot_loaded_style_and_position(
    tmp_path, inventory, fake_overlay
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[capture]\nprivacy_indicator_style="pill"\n'
        'privacy_indicator_placement="bottom-left-flush"\n'
    )
    clock = FakeMonotonic()
    monitor = make_monitor(
        config_path=config_path,
        inventory=inventory,
        overlay=fake_overlay,
        monotonic=clock,
    )
    first = monitor.decision_for_capture(force=True)
    old_mtime = config_path.stat().st_mtime_ns
    config_path.write_text(
        '[capture]\nprivacy_indicator_style="border"\n'
        'privacy_indicator_placement="bottom-right-work-area"\n'
    )
    os.utime(config_path, ns=(old_mtime + 1, old_mtime + 1))
    clock.advance(0.4)
    transient = monitor.decision_for_capture(force=True)
    clock.advance(0.4)
    sustained = monitor.decision_for_capture(force=True)
    assert first.snapshot.indicator_style == "quiet-shield"
    assert transient.snapshot.indicator_style == "quiet-shield"
    assert transient.snapshot.indicator_placement == "bottom-right-work-area"
    assert sustained.snapshot.indicator_style == "border"
```

Add these boundary tests:

```python
def test_off_keeps_effective_protection_without_overlay_ids(inventory, fake_overlay) -> None:
    monitor = make_monitor(inventory=inventory, overlay=fake_overlay, style="off")
    decision = monitor.decision_for_capture(force=True)
    assert decision.snapshot.state is ProtectionState.PROTECTED
    assert decision.snapshot.indicator_style == "off"
    assert decision.indicator_confirmed is True
    assert decision.indicator_window_ids == ()


def test_pause_and_inventory_failure_bypass_smoothing(inventory, fake_overlay) -> None:
    paused = make_monitor(
        inventory=inventory,
        overlay=fake_overlay,
        pause_reader=lambda: CapturePauseDecision(
            paused=True,
            kind=CapturePauseKind.INDEFINITE,
        ),
    ).decision_for_capture(force=True)
    assert paused.snapshot.state is ProtectionState.PAUSED
    assert paused.presentation_phase is ProtectionPresentationPhase.BYPASS
    assert paused.snapshot.indicator_style == "pill"

    failed = make_monitor(
        inventory=inventory,
        overlay=FakeOverlay(),
        inventory_reader=lambda: InventoryReadResult(
            None,
            ProtectionFailureReason.INVENTORY_UNAVAILABLE,
        ),
    ).decision_for_capture(force=True)
    assert failed.snapshot.state is ProtectionState.FAILED
    assert failed.presentation_phase is ProtectionPresentationPhase.BYPASS
    assert failed.snapshot.indicator_style == "pill"


def test_listener_and_wait_use_acknowledged_effective_decision(inventory, fake_overlay) -> None:
    published: list[ProtectionDecision] = []
    monitor = make_monitor(
        inventory=inventory,
        overlay=fake_overlay,
        decision_listener=published.append,
    )
    transient = monitor.decision_for_capture(force=True)
    assert published == [transient]
    assert transient.snapshot.indicator_style == "quiet-shield"
    assert transient.snapshot.display_reasons.reasons
    assert monitor.wait_for_display_protection(
        2,
        after_generation=0,
        timeout=0.1,
    ) == transient.snapshot.generation

    fake_overlay.render_result = False
    unconfirmed = monitor.decision_for_capture(force=True)
    assert unconfirmed.indicator_confirmed is False
    assert unconfirmed.indicator_window_ids == ()
    assert monitor.wait_for_display_protection(
        2,
        after_generation=transient.snapshot.generation,
        timeout=0.01,
    ) is None
```

In `tests/test_capture_scheduler_fts.py`, lock the authorization boundary to the effective
presentation style and newly acknowledged helper IDs:

```python
def test_filtered_authorization_changes_across_protection_promotion() -> None:
    transient = _filtered_decision(
        generation=20,
        indicator_style="quiet-shield",
        indicator_window_ids=(7, 41),
    )
    sustained = _filtered_decision(
        generation=21,
        indicator_style="pill",
        indicator_window_ids=(8, 41),
    )
    assert scheduler_mod._filtered_authorization_key(transient) != (
        scheduler_mod._filtered_authorization_key(sustained)
    )
```

- [ ] **Step 3: Add failing invariant-error tests**

Add `PRESENTATION_STATE_INVALID = "presentation_state_invalid"` to expected reason lists in tests,
then test:

```python
def test_presentation_state_failure_is_always_fail_closed() -> None:
    cfg = CaptureConfig(screenshot_privacy_fail_closed=False)
    snapshot = build_protection_snapshot(
        cfg,
        None,
        paused=False,
        generation=92,
        now=22.0,
        failure_reason=ProtectionFailureReason.PRESENTATION_STATE_INVALID,
    )
    assert snapshot.state is ProtectionState.FAILED
    assert failure_requires_fail_closed(cfg, snapshot) is True
```

Inject this smoother and assert bounded failure publication:

```python
class RaisingSmoother:
    def __init__(self) -> None:
        self.reset_calls = 0

    def resolve(self, _snapshot, *, now):
        raise ProtectionSmoothingError("private-value-that-must-not-be-logged")

    def reset(self) -> None:
        self.reset_calls += 1


def test_smoothing_invariant_failure_publishes_sanitized_fail_closed_decision(
    inventory, fake_overlay, caplog
) -> None:
    smoother = RaisingSmoother()
    monitor = make_monitor(
        inventory=inventory,
        overlay=fake_overlay,
        smoother=smoother,
        fail_closed=False,
    )
    with caplog.at_level(logging.WARNING, logger="openchronicle.capture"):
        decision = monitor.decision_for_capture(force=True)
    assert decision.snapshot.state is ProtectionState.FAILED
    assert decision.snapshot.failure_reason is ProtectionFailureReason.PRESENTATION_STATE_INVALID
    assert failure_requires_fail_closed(
        CaptureConfig(screenshot_privacy_fail_closed=False),
        decision.snapshot,
    ) is True
    assert [r.code for r in decision.snapshot.reasons_for_display(None)] == [
        ProtectionReasonCode.PRESENTATION_STATE_INVALID
    ]
    assert smoother.reset_calls == 1
    rendered = "\n".join(record.getMessage() for record in caplog.records)
    assert "ProtectionSmoothingError" in rendered
    assert "private-value-that-must-not-be-logged" not in rendered
```

Add the fixed reason to the Swift diagnostics model rather than allowing it to become `.unknown`:

```swift
case presentationStateInvalid = "presentation_state_invalid"
```

Map it in `ProtectionDiagnosticsView` to title `Protection state invalid` and
`exclamationmark.triangle.fill`. Extend `ProtectionDiagnosticsWireTests` with:

```swift
func testPresentationStateInvalidDecodesAsFixedFailureCategory() throws {
  let data = Data(
    #"{"code":"presentation_state_invalid","display_id":null}"#.utf8
  )
  let reason = try JSONDecoder().decode(ProtectionReasonDiagnostic.self, from: data)
  XCTAssertEqual(reason.code, .presentationStateInvalid)
  XCTAssertNil(reason.appName)
  XCTAssertNil(reason.windowTitle)
  XCTAssertNil(reason.rule)
}
```

- [ ] **Step 4: Run monitor tests and verify RED**

Run:

```bash
PYTHONPATH=src uv run pytest -q \
  tests/test_protection.py \
  tests/test_protection_reason.py \
  tests/test_protection_monitor.py \
  tests/test_daemon_protection.py \
  tests/test_privacy_diagnostics.py \
  tests/test_capture_scheduler_fts.py
```

Expected: failures identify missing smoother constructor dependencies, effective decision fields,
deadline scheduling, and invariant failure reason.

- [ ] **Step 5: Implement monitor integration**

Add keyword-only fields to preserve `ProtectionDecision` positional compatibility:

```python
presentation_phase: ProtectionPresentationPhase = field(
    default=ProtectionPresentationPhase.BYPASS,
    kw_only=True,
)
overlay_reasons_enabled: bool = field(default=True, kw_only=True)
```

Add constructor dependencies:

```python
smoother: ProtectionPresentationSmoother | None = None,
monotonic: Callable[[], float] = time.monotonic,
```

Store `self._next_smoothing_deadline` under the existing state lock. In `_refresh`, build `raw_snapshot`
with `now = self._monotonic()` passed to `build_protection_snapshot`, then call the smoother with the
same `now` value:

```python
now = self._monotonic()
raw_snapshot = build_protection_snapshot(
    snapshot_cfg,
    inventory,
    paused=paused,
    generation=generation,
    now=now,
    failure_reason=failure_reason,
    pause_reason=pause_reason,
    diagnostic_display_ids=diagnostics_guard.display_ids,
    diagnostics_guard_invalid=diagnostics_guard.fail_closed_all,
)
result = self._smoother.resolve(raw_snapshot, now=now)
snapshot = result.snapshot
```

Apply the smoother after both raw-snapshot branches, including the manual inactive snapshot used by
`diagnostics_guard_only`; do not bypass smoothing when the diagnostics lease disappears. In
`decision_for_capture`, compare `current.snapshot.fresh_until` against `self._monotonic()` so injected
virtual-time tests and production deadline arithmetic use one time domain. Keep
`wait_for_display_protection` on real `time.monotonic()` because its timeout bounds caller blocking,
not protection-state progression.

Render with `overlay_reasons_enabled=result.overlay_reasons_enabled`, obtain same-generation window
IDs, and publish the phase/flag in `ProtectionDecision`. In the same condition-locked publication
that stores the generation and decision, store `self._next_smoothing_deadline = result.next_deadline`.

Change `_run` from a fixed wait to:

```python
timeout = self._watchdog_seconds
with self._state_lock:
    deadline = self._next_smoothing_deadline
if deadline is not None:
    timeout = min(timeout, max(0.0, deadline - self._monotonic()))
self._wake.wait(timeout)
```

The worker must perform a forced fresh inventory read at a smoothing deadline. `request_refresh`
continues to wake the same event. `stop` clears the deadline before joining and marks the overlay
terminal before any blocked render can resume.

- [ ] **Step 6: Implement invariant failure mapping**

Add `PRESENTATION_STATE_INVALID` to `ProtectionFailureReason`, `ProtectionReasonCode`, `_FAILED_CODES`,
and priority tables. Update `failure_requires_fail_closed` so this reason is always closed regardless
of config.

Catch `ProtectionSmoothingError` inside `_refresh`; reset the smoother and replace the raw snapshot
with a bounded failed snapshot:

```python
snapshot = replace(
    raw_snapshot,
    state=ProtectionState.FAILED,
    failure_reason=ProtectionFailureReason.PRESENTATION_STATE_INVALID,
    protected_display_ids=frozenset(),
    active_candidate_display_ids=frozenset(),
    display_reasons=DisplayProtectionReasons.from_reasons(
        [ProtectionReason(ProtectionReasonCode.PRESENTATION_STATE_INVALID, None)]
    ),
    protected_window_ids=frozenset(),
    protected_window_regions=(),
    window_filterable=False,
)
```

Then set the local presentation values explicitly:

```python
phase = ProtectionPresentationPhase.BYPASS
overlay_reasons_enabled = True
next_smoothing_deadline = None
```

On the normal path, take those three values from `result`. Render the failed snapshot immediately as
bypass with reasons enabled and publish a cleared smoothing deadline. Move
`_log_failure_transition(snapshot)` after smoothing/error conversion so the fixed fail-closed reason
is logged through the normal transition logger. Separately log only
`privacy protection smoothing failed: ProtectionSmoothingError`; never interpolate the exception.

- [ ] **Step 7: Run Task 3 verification and mutation checks**

Run:

```bash
PYTHONPATH=src uv run pytest -q \
  tests/test_protection_smoothing.py \
  tests/test_protection.py \
  tests/test_protection_reason.py \
  tests/test_privacy_overlay.py \
  tests/test_protection_monitor.py \
  tests/test_daemon_protection.py \
  tests/test_privacy_diagnostics.py \
  tests/test_capture_scheduler_fts.py
uv run ruff check \
  src/openchronicle/capture/protection_smoothing.py \
  src/openchronicle/capture/privacy.py \
  src/openchronicle/capture/protection_reason.py \
  src/openchronicle/capture/protection.py \
  src/openchronicle/capture/privacy_overlay.py \
  src/openchronicle/capture/protection_monitor.py \
  tests/test_protection_smoothing.py \
  tests/test_protection_monitor.py
CLANG_MODULE_CACHE_PATH=/tmp/openchronicle-clang-module-cache \
SWIFTPM_MODULECACHE_OVERRIDE=/tmp/openchronicle-swiftpm-module-cache \
swift test --package-path macos/OpenChronicleApp --filter ProtectionDiagnosticsWireTests
```

Mutation checks:

1. Ignore smoother deadlines in `_run`; the 30ms real-thread promotion test must fail.
2. Publish raw instead of effective snapshot during clear-pending; scheduler/AX hold tests must fail.
3. Omit `overlay_reasons_enabled` when rendering; transient reason test must fail.
4. Remove unconditional fail-closed handling for `PRESENTATION_STATE_INVALID`; the policy test must fail.

Restore each mutation and rerun the focused verification.

- [ ] **Step 8: Commit Task 3**

```bash
git add src/openchronicle/capture/privacy.py \
  src/openchronicle/capture/protection_reason.py \
  src/openchronicle/capture/protection.py \
  src/openchronicle/capture/protection_monitor.py \
  tests/test_protection.py tests/test_protection_reason.py \
  tests/test_protection_monitor.py tests/test_daemon_protection.py \
  tests/test_privacy_diagnostics.py tests/test_capture_scheduler_fts.py \
  macos/OpenChronicleApp/Sources/OpenChronicleApp/Models/ProtectionDiagnostics.swift \
  macos/OpenChronicleApp/Sources/OpenChronicleApp/Views/ProtectionDiagnosticsView.swift \
  macos/OpenChronicleApp/Tests/OpenChronicleAppTests/ConfigurationTests.swift
git commit -m "feat(capture): smooth protection indicator transitions"
```

---

### Task 4: Documentation, Full Verification, and Installed Mission Control Test

**Files:**
- Modify: `docs/capture.md`
- Modify: `docs/macos-app.md`
- Modify: `docs/superpowers/specs/2026-08-25-protection-indicator-transition-smoothing-design.md`
- Verify: `/Users/tkandi/.openchronicle/config.toml` by hash and structured diff only.
- Verify: `/Applications/OpenChronicle.app` and App-owned daemon/helper chain.

**Interfaces:**
- Consumes: completed behavior from Tasks 1–3.
- Produces: installed backend/helper and signed SwiftUI App with no config-value changes.
- Produces: safe black-box evidence for fast/slow Space switching, Mission Control hold, clear confirmation, generation acknowledgement, and first-frame screenshot/AX blocking.

- [ ] **Step 1: Update user and architecture documentation**

Document in both user docs:

- immediate first-frame protection;
- transient quiet shield for 800ms;
- sustained configured style;
- two safe inventories at least 200ms apart;
- no Mission Control bypass;
- paused/failed/off behavior;
- fixed internal constants and no new settings;
- transient reason suppression affects only overlay presentation, not diagnostics/policy reasons.

Update the design's component interface so `ProtectionPresentationResult` explicitly lists
`overlay_reasons_enabled`; this resolves the clear-pending distinction between transient quiet
shield and sustained configured quiet shield without adding a native wire field.

Change the design status to `已实现，等待实机验证` before automated verification.

- [ ] **Step 2: Run the complete automated gate**

Run:

```bash
git diff --check
PYTHONPATH=src uv run pytest -q
uv run ruff check src tests
swiftc -module-cache-path /tmp/openchronicle-swift-module-cache \
  resources/mac-privacy-overlay-reason.swift \
  resources/mac-privacy-overlay-core.swift \
  tests/swift/MacPrivacyOverlayCoreTests.swift \
  -o /tmp/openchronicle-overlay-core-tests-smoothing \
  -framework AppKit
/tmp/openchronicle-overlay-core-tests-smoothing
CLANG_MODULE_CACHE_PATH=/tmp/openchronicle-clang-module-cache \
SWIFTPM_MODULECACHE_OVERRIDE=/tmp/openchronicle-swiftpm-module-cache \
swift test --package-path macos/OpenChronicleApp
```

Expected: no whitespace errors, complete Python suite passes, changed-source Ruff is clean, native
core prints its pass marker, and the complete App suite has zero failures. If full-repo Ruff exposes
pre-existing unrelated findings, record them and rerun the exact changed-file list rather than
editing unrelated files.

- [ ] **Step 3: Commit documentation before host installation**

```bash
git add docs/capture.md docs/macos-app.md \
  docs/superpowers/specs/2026-08-25-protection-indicator-transition-smoothing-design.md
git commit -m "docs: document protection indicator smoothing"
```

- [ ] **Step 4: Preserve config and install both product layers**

Record the config SHA-256 and a secret-safe structured snapshot. Stop the App-owned process chain and
verify App, daemon, watcher, and overlay exited. Then run:

```bash
bash install.sh --no-client-config
bash scripts/install-macos-app.sh
```

Recompute the config hash and structured diff. Expected: no config value changes. Verify the signed
App starts one daemon, one AX watcher, and one overlay helper.

- [ ] **Step 5: Perform the safe Mission Control/Space live test**

Use a new Edge InPrivate window with only `about:blank`. Do not show real passwords, tokens, chats,
files, or private pages.

Test sequence:

1. Put the blank InPrivate window on an adjacent Space.
2. Swipe quickly between Spaces five times. Expected: immediate quiet shield only; no full pill.
3. Swipe slowly and hold the transition. Expected: quiet shield first, configured full style after
   at least 800ms.
4. Open Mission Control with F3 and hold it. Expected: protection remains while the private thumbnail
   is composited and promotes after 800ms.
5. Exit to a safe Space. Expected: effective decision remains protected after first safe inventory,
   then clears only after a second safe inventory at least 200ms later.
6. During clear-pending, swipe back toward the private Space. Expected: clear is cancelled and no
   screenshot/AX resumes.
7. Repeat on the secondary display if connected.

Collect only category-safe evidence:

- diagnostics `generation`, `state`, and `indicator_confirmed`, plus phase from sanitized monitor logs;
- capture-log timestamps showing screenshot/AX blocked from the first protected frame;
- capture JSON field presence/absence and monitor IDs, without displaying screenshot payloads or
  captured text;
- process IDs proving no restart during smoothing;
- no helper crash, unconfirmed generation, or fail-open transition.

- [ ] **Step 6: Mark verified, commit, and run final regression**

After all live assertions pass, change design status to `已实现并验证` and commit:

```bash
git add docs/superpowers/specs/2026-08-25-protection-indicator-transition-smoothing-design.md
git commit -m "docs: mark protection indicator smoothing verified"
```

Then rerun:

```bash
PYTHONPATH=src uv run pytest -q
CLANG_MODULE_CACHE_PATH=/tmp/openchronicle-clang-module-cache \
SWIFTPM_MODULECACHE_OVERRIDE=/tmp/openchronicle-swiftpm-module-cache \
swift test --package-path macos/OpenChronicleApp
git status --short --branch
```

Expected: complete tests pass and the implementation branch is clean.

- [ ] **Step 7: Finish the implementation branch**

Invoke `superpowers:finishing-a-development-branch`. Present local merge, PR, and keep-branch choices.
Do not merge or push until the user chooses.
