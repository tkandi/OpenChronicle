# Pause-State Read Fail-Closed

## Summary

OpenChronicle must never capture when it cannot determine whether capture is manually
paused. A pause-state read failure is a control-plane uncertainty, not a screenshot
inventory failure, so `screenshot_privacy_fail_closed = false` must not allow it to pass.

## Root Cause

`PrivacyProtectionMonitor._read_protection_inputs()` currently maps an exception from the
pause reader to `inventory_unavailable`. The scheduler intentionally permits that generic
failure when screenshot inventory fail-open is configured. The original failure type is
therefore lost before the capture policy is evaluated.

## Decision

Add the typed failure reason `pause_state_unavailable`. A protection snapshot carrying this
reason remains in the existing yellow `failed` visual state, but it is unconditionally
terminal for capture.

One shared policy predicate will calculate effective fail-closed behavior:

- `pause_state_unavailable` always requires fail-closed behavior;
- other `failed` reasons follow `screenshot_privacy_fail_closed`;
- `paused` remains unconditionally terminal;
- `inactive` and `protected` retain their existing behavior.

The scheduler's pre-AX gate, post-AX validation gate, overlay render/clear choice, and
sanitized failure log must all use this policy. This prevents those consumers from deriving
different behavior from the same snapshot.

## Runtime Behavior

When the pause reader raises an exception:

1. The monitor records `pause_state_unavailable` without logging paths, marker contents, or
   exception text.
2. The monitor publishes a `failed` snapshot and requests the yellow failure indicator on
   all displays. If display inventory is unavailable, the overlay helper resolves all
   displays locally.
3. The pre-AX gate prevents AX traversal. If a newly unreadable pause state is first
   discovered by post-AX validation, the scheduler discards the already-read in-memory AX
   before screenshots, capture JSON persistence, FTS indexing, timeline, memory, or model
   processing.
4. No screenshot or downstream artifact is produced for that attempt.
5. The monitor continues polling. A successful pause read automatically restores the normal
   state derived from the current window inventory.

An ordinary window-inventory failure remains fail-open when explicitly configured. This
patch does not broaden fail-closed behavior beyond pause-state uncertainty.

## Tests

- A monitor test proves that a pause-reader exception produces
  `pause_state_unavailable`, renders rather than clears the failed overlay, and remains
  unconfirmed until acknowledged.
- Scheduler tests prove that the pre-AX gate does not call the AX provider, while the post-AX
  gate records one provider call before discarding the already-read in-memory AX. Neither path
  reaches screenshots, persistence, FTS, timeline, memory, or model processing.
- Existing inventory fail-open tests remain unchanged and must continue to pass.
- The full Python and Swift suites, package builds, installed process ownership, and a safe
  blank InPrivate multi-display check are rerun before completion.

## Non-Goals

- No new overlay protocol state or Swift UI style is introduced.
- The `.paused` file format and pause/resume controls do not change.
- Screenshot inventory failures do not become universally fail-closed.
- No unrelated refactoring is included.
