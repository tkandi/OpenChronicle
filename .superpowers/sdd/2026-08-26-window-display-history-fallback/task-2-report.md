# Task 2 Report: Snapshot Builder History Fallback

## Scope

Changed only `src/openchronicle/capture/protection.py` and
`tests/test_protection.py` for production behavior and regression coverage.
This report records the required SDD evidence.

## TDD Evidence

The fallback builder tests were added before changing snapshot construction.
The initial focused run produced six expected failures: unmapped sensitive
history was still `FAILED`, the snapshot flag did not exist, active and
candidate mappings ignored history, and all-mode history did not protect.

The minimal production change introduces `_display_mapping_for_window()`. It
returns positive-area geometry mappings first and only returns valid current
display history when geometry maps nowhere.

## Behavior Covered

- A sensitive window with no geometry and history for display 1 becomes
  `PROTECTED` on display 1, attributes its direct reason to display 1, exposes
  `display_mapping_fallback_active=True`, and cannot be window-filtered.
- A sensitive unmapped window without history remains
  `SENSITIVE_WINDOW_UNMAPPED` / `FAILED`.
- Actual geometry on display 2 overrides stale history for display 1.
- In `screenshot_monitor="all"`, a history-mapped sensitive window protects
  every current display while retaining the fallback flag and disabled window
  filtering.
- A single active fallback selects that display. Multiple active-candidate
  fallback displays remain candidates, preserving conservative `ax_blocked`.
- `diagnostics_guard_invalid` remains `FAILED` and protects all displays;
  history never weakens that guard.

## Verification

- RED: `PYTHONPATH=src uv run pytest -q tests/test_protection.py`
  - `6 failed, 53 passed`, with failures attributable to the missing fallback
    behavior and snapshot field.
- GREEN: `PYTHONPATH=src uv run pytest -q tests/test_protection.py`
  - `59 passed`.
- Focused regression: `PYTHONPATH=src uv run pytest -q tests/test_protection.py tests/test_protection_reason.py`
  - `65 passed`.
- Final suite: `PYTHONPATH=src uv run pytest -q`
  - completed successfully.
- Lint: `uv run ruff check src/openchronicle/capture/protection.py tests/test_protection.py`
  - passed.
- Diff integrity: `git diff --check`
  - passed.

## Mutation Checks

Each temporary mutation was run against its focused regression and then
restored with `apply_patch`.

- Prefer history over actual geometry: killed by the stale-history geometry
  test, which observed display 1 instead of display 2.
- Omit `display_mapping_fallback_active`: killed by the sensitive history test.
- Permit window filtering for history-mapped direct windows: killed by the
  sensitive history test.
- Change an invalid diagnostics guard from `FAILED` to `PROTECTED`: killed by
  the invalid-guard history test.

## Privacy Boundary

History is used solely as a per-current-display fallback after geometry has no
positive intersection. It neither turns no-history windows into protection nor
authorizes window-level filtering from historical positions.

## Fix Round 1

Addressed both Important findings from `task-2-review.md` with test-first
changes limited to the snapshot builder and its focused test module.

### Fallback Category and Filtering Boundaries

- `display_mapping_fallback_active` now aggregates fallback use across every
  sensitive mapping, including `WINDOW_TITLE_UNKNOWN`; direct-window matches
  remain limited to rule types that can safely produce window IDs, regions, and
  filtered-capture eligibility.
- A title-unavailable window protected from display history now reports its
  `WINDOW_TITLE_UNKNOWN` reason on the historical display, sets the fallback
  flag, and remains non-filterable.
- An active window with multi-display history contributes all historical
  displays as candidates when no actual intersection exists, preserving
  conservative AX blocking.
- A mixed actual/direct and history/direct sensitive inventory disables
  filtering for the entire snapshot, while the existing actual-only direct
  regression continues to permit filtering where the prior policy allows it.

### TDD and Verification

The three new behavioral tests were added before the production correction.
The initial focused run failed only at the intended title-unknown boundary:
`1 failed, 61 passed`, because the category flag was falsely `False`.
After the minimal aggregation change:

- `PYTHONPATH=src uv run pytest -q tests/test_protection.py`
  - `62 passed`.
- `PYTHONPATH=src uv run pytest -q tests/test_protection.py tests/test_protection_reason.py`
  - `68 passed`.
- `uv run ruff check src/openchronicle/capture/protection.py tests/test_protection.py`
  - passed.
- `git diff --check`
  - passed.

### Revised Mutation Checks

Each temporary mutation failed its named behavioral test and was restored with
`apply_patch`:

- Revert fallback-category aggregation to direct-window matches: killed by the
  title-unknown history regression.
- Delete the multi-display active fallback candidate assignment: killed by the
  active-window conservative-candidate regression.
- Replace fallback aggregation `any(...)` with `all(...)`: killed by the mixed
  actual/history direct-window filtering regression.
