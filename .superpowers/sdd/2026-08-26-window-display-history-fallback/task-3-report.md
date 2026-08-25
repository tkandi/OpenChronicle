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
