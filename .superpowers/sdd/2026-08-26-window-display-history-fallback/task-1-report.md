# Task 1 Report: Pure Window Display History

## Scope

Implemented only the `VisibleWindow` fallback field, the pure in-memory
`WindowDisplayHistory` resolver, and focused tests. No snapshot builder,
monitor, smoother, installation path, or existing design/plan documentation
was changed.

## TDD Evidence

The complete new test module was written before production code. Its initial
run failed during collection with `ModuleNotFoundError` for
`openchronicle.capture.window_display_history`, as expected. The minimal
resolver and keyword-only `fallback_display_ids` field were then added.

## Behavior Covered

- Actual positive-area display intersections seed and replace history; fallback
  is only emitted when geometry maps to no active display.
- A continuously present unmapped window refreshes its in-memory observation
  time and does not expire.
- Entries observed absent are retained below 5 seconds and removed at exactly
  5 seconds.
- Invalid IDs, duplicate valid IDs, owner changes, and removed displays do not
  reuse a mapping. A window spanning two displays records both, while later
  actual geometry on display 2 replaces that mapping.
- Equal monotonic timestamps are allowed, rollback raises
  `WindowDisplayHistoryError`, and `reset()` clears mappings and the clock.

## Privacy Boundary

The resolver performs no I/O or persistence, and emits neither owner nor title
in its public result or logs. Its private, process-local comparison identity
uses only a normalized bundle ID, falling back to a normalized app name, plus
a validated CoreGraphics window ID. The public result exposes display IDs only
through `fallback_display_ids`.

## Verification

- `PYTHONPATH=src uv run pytest -q tests/test_window_display_history.py tests/test_capture_privacy.py`
  - `38 passed`
- `uv run ruff check src/openchronicle/capture/privacy.py src/openchronicle/capture/window_display_history.py tests/test_window_display_history.py`
  - passed
- `git diff --check`
  - passed

## Mutation Checks

All required mutations were killed by the focused test module:

- Let cached history take precedence over actual mapping: killed by the
  spanning-display overwrite test.
- Expire a continuously present fallback window: killed by the continuous
  presence test.
- Permit duplicate valid window IDs: killed by the duplicate-ID test.
- Change exact 5-second expiry from `>=` to `>`: killed by the exact-boundary
  expiry test.

## Residual Concern

This task intentionally does not wire the resolver into snapshot construction
or runtime capture. Integration behavior belongs to the follow-up task that
owns those components.
