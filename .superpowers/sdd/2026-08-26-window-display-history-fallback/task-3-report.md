# Task 3 Report: Monitor and Scheduler Integration

## Scope

Changed only:

- `src/openchronicle/capture/protection_monitor.py`
- `tests/test_protection_monitor.py`
- `tests/test_capture_scheduler_fts.py`

The monitor constructor gained the planned injectable
`window_display_history` dependency. No scheduler production code, smoother,
diagnostics payload, documentation, installation path, or native helper was
changed.

## TDD Evidence

The monitor and real monitor-to-scheduler tests were added before production
integration. The RED runs showed the expected missing behavior:

- the injected constructor dependency was rejected;
- mapped then same-identity unmapped inventory became global mapping
  `FAILED` instead of per-display `PROTECTED`;
- the before-TTL fallback and diagnostics-guard-only path remained failed;
- the real scheduler returned no capture because the fallback inventory was
  `ACTIVE_WINDOW_UNMAPPED` and fail-closed.

The shared test factory initially passed the new keyword to every existing
monitor test, producing unrelated constructor failures. The fixture was
narrowed before implementation so only tests exercising the new dependency
used it. Active-window identity-miss expectations were also corrected to the
existing `ACTIVE_WINDOW_UNMAPPED` reason; the required invariant is that these
cases remain `FAILED`.

## Implementation

- Each `PrivacyProtectionMonitor` owns one `WindowDisplayHistory` unless a
  test injects one.
- A successful inventory is resolved before snapshot construction with the
  exact `now` value already sampled for that generation.
- The same path is used for normal monitoring and diagnostics-guard-only
  monitoring when an inventory is read.
- A small lock serializes history resolve/reset without adding a thread,
  timer, or Event.
- `stop()` resets the history. A refresh that races with stop rechecks the
  lifecycle after resolution, resets any late state, and cannot publish a
  late decision.

## Capture Behavior

The real monitor-to-scheduler regression uses `screenshot_monitor="separate"`
and `screenshot_privacy_mode="exclude-window"`:

- a mapped active InPrivate window on display 1 seeds history;
- the same window and owner later report off-display geometry;
- the real monitor publishes `PROTECTED` for display 1 only, with
  `display_mapping_fallback_active=True` and `window_filterable=False`;
- AX is blocked because the resolved active display is protected;
- the scheduler does not call the filtered-window helper for the fallback
  snapshot;
- MSS receives only display 1 as a blocked region and returns a display 2
  screenshot;
- when a filtered helper starts under mapped authorization and the inventory
  becomes fallback before completion, post-helper revalidation discards the
  stale filtered frame and captures only the safe display through the
  skip-monitor fallback.

Missing window IDs, owner mismatch, and exact 5-second absence expiry remain
mapping `FAILED`. Absence below 5 seconds can reuse history.

## Error and Privacy Boundary

Only `WindowDisplayHistoryError` is converted. The monitor logs the fixed type
name, resets history, removes the inventory and guard inputs from that
generation, and publishes a metadata-empty
`PRESENTATION_STATE_INVALID` hard failure. The exception body, window owner,
title, rule, IDs, and regions are absent from the failure snapshot and logs.
Other exception types are not caught by this integration.

The logging regression attaches its own temporary handler so it remains valid
after the full suite configures `openchronicle.capture` with
`propagate=False`.

## Verification

- Monitor/scheduler GREEN:
  `PYTHONPATH=src uv run pytest -q tests/test_protection_monitor.py tests/test_capture_scheduler_fts.py`
  - `151 passed`
- Task 3 focused regression:
  `PYTHONPATH=src uv run pytest -q tests/test_window_display_history.py tests/test_protection.py tests/test_protection_monitor.py tests/test_capture_scheduler_fts.py`
  - `234 passed`
- Full Python regression:
  `PYTHONPATH=src uv run pytest -q`
  - `649 passed in 53.96s`
- Ruff:
  `uv run ruff check src/openchronicle/capture/protection_monitor.py tests/test_protection_monitor.py tests/test_capture_scheduler_fts.py`
  - passed
- `git diff --check`
  - passed

## Mutation Checks

Each temporary mutation was applied with `apply_patch`, run against its focused
regression, and restored:

- Bypass monitor history resolution: killed by both mapped-to-unmapped monitor
  and real scheduler tests.
- Reuse a cached entry by window ID across owner mismatch: killed by the owner
  mismatch monitor test.
- Permit window-filtered capture while fallback is active: killed when the
  scheduler accepted `STALE-PRE-FALLBACK` instead of the display-safe MSS
  frame.
- Log the `WindowDisplayHistoryError` exception object: killed by the private
  marker log assertion.

## Deferred Scope

Task 3 intentionally does not alter presentation smoothing or diagnostics.
Transient fallback phases, diagnostics category publication, documentation,
installation, and live dual-display acceptance remain owned by later tasks in
the SDD plan.

## Fix Round 1

Addressed both Important findings from `task-3-review.md` with a test-first
change limited to the Task 3 monitor boundary and its monitor tests.

### Trusted Inventory Gate

`WindowDisplayHistory.resolve()` now runs only when both conditions hold:

- the inventory is present; and
- `failure_reason is None`.

An `InventoryReadResult` carrying an explicit failure remains fail-closed but
cannot seed, refresh, expire, overwrite, or otherwise mutate trusted history.
An existing trusted mapping survives that failed generation unchanged.

The RED run demonstrated both defects in the prior implementation:

- a failed mapped sample seeded display 1, causing the next successful
  same-identity unmapped sample to become fallback `PROTECTED` instead of
  `ACTIVE_WINDOW_UNMAPPED`;
- a failed mapped sample on display 2 replaced an existing trusted display-1
  mapping.

After the one-condition production fix, the failed seed remains mapping
`FAILED`, while the trusted display-1 entry survives a failed display-2 sample.

### Mutation-Real Integration Coverage

- An advancing call-count clock drives `_refresh()` directly. Each generation
  samples one `now`, and history receives exactly the snapshot creation time.
- `WindowDisplayHistoryError` still maps to sanitized
  `PRESENTATION_STATE_INVALID`; a distinct `ValueError` escapes without reset,
  overlay activity, or conversion.
- The stop-race test blocks inside `resolve()` before the history write while
  the monitor history lock is held. Concurrent `stop()` must wait; after
  release, no listener callback is published and the late history state and
  clock are reset.
- A real config mtime/style/placement reload between mapped and same-identity
  off-display samples preserves the trusted display-1 fallback while applying
  the new presentation settings.

### Verification

- Baseline:
  `PYTHONPATH=src uv run pytest -q tests/test_protection_monitor.py tests/test_capture_scheduler_fts.py`
  - `151 passed`
- I1 RED plus I2 initial run:
  `PYTHONPATH=src uv run pytest -q tests/test_protection_monitor.py -k 'same_clock or failed_inventory_with_mapped_window or failed_inventory_cannot_replace or unexpected_history_exception or inflight_history_resolve or hot_reload_preserves'`
  - `2 failed, 4 passed, 62 deselected`; both failures were the I1 trust
    violations.
- New/strengthened GREEN:
  - `6 passed, 62 deselected`
- Task 3 focused regression:
  `PYTHONPATH=src uv run pytest -q tests/test_window_display_history.py tests/test_protection.py tests/test_protection_monitor.py tests/test_capture_scheduler_fts.py`
  - `239 passed in 2.08s`
- Full Python regression:
  `PYTHONPATH=src uv run pytest -q`
  - `654 passed in 59.24s`
- Ruff:
  `uv run ruff check src/openchronicle/capture/protection_monitor.py tests/test_protection_monitor.py tests/test_capture_scheduler_fts.py`
  - passed
- `git diff --check`
  - passed

### Fix-Round Mutation Checks

Each mutation was applied with `apply_patch`, failed its named test, and was
restored before the final regression:

- Restore the unsafe inventory-only resolver gate: both failed-sample trust
  tests failed.
- Resample monotonic time for history instead of reusing generation `now`: the
  advancing-clock test failed with different history/snapshot times and extra
  calls.
- Broaden `WindowDisplayHistoryError` handling to `Exception`: the unexpected
  `ValueError` propagation test failed.
- Remove the monitor lock around history resolve: the stop-race test observed
  `stop()` finish while resolve remained blocked.
- Reset history during indicator hot reload: the reload generation became
  `ACTIVE_WINDOW_UNMAPPED` instead of display-1 fallback `PROTECTED`.

### Commit and Files

- Commit: `fix(capture): trust only successful display history samples`
  (this fix-round commit; SHA is reported in the final handoff).
- Modified: `src/openchronicle/capture/protection_monitor.py`.
- Modified: `tests/test_protection_monitor.py`.
- Modified: `.superpowers/sdd/2026-08-26-window-display-history-fallback/task-3-report.md`.

No Task 4 smoother, diagnostics, documentation, installation, scheduler, or
native-helper production file was changed.
