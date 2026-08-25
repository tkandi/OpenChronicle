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

## Fix Round 1

Addressed all three Important findings from `task-1-review.md` with test-first
changes limited to the pure resolver and its focused test module.

### State and Identity Corrections

- `_HistoryEntry` now carries `absence_observed`. An absent entry is marked on
  inventory omission, and an entry whose observed absence reaches 5 seconds is
  evicted before a later fallback lookup. A present fallback refreshes the
  observation time and clears the absence state.
- Empty normalized owner keys are non-identifiable: they cannot seed or consume
  history, and a unique blank-owner observation clears every cached entry for
  that window ID.
- For every current unique valid ID, cached entries with a different owner are
  removed before window processing. Duplicate IDs continue to clear all cached
  entries for that ID.

### TDD and Regression Evidence

The new stateful test set was added before production changes. The initial
focused run failed in four expected behaviors: expired observed absence,
owner-recycled ID reuse, blank-owner seeding, and blank-owner cache clearing.
After the minimal resolver update:

- `PYTHONPATH=src uv run pytest -q tests/test_window_display_history.py tests/test_capture_privacy.py`
  - `44 passed`
- `uv run ruff check src/openchronicle/capture/privacy.py src/openchronicle/capture/window_display_history.py tests/test_window_display_history.py`
  - passed
- `git diff --check`
  - passed

### Revised Mutation Checks

The focused suite killed each stateful mutation:

- Remove fallback `last_seen_monotonic` refresh: the later brief-absence reuse
  test failed.
- Accept window ID `0`: the seed-then-unmapped invalid-ID test failed.
- Retain a cached entry through duplicate-ID observation: the later single-ID
  fallback test failed.
- Preserve an input fallback during actual geometry: the actual-map clearing
  test failed.
- Treat a blank owner as identifiable: the blank-owner seed/use test failed.
- Retain entries across an owner change or blank-owner observation: the
  recycled-ID tests failed.
- Disable observed-absence expiry: both exact-boundary and delayed-reappearance
  tests failed.
