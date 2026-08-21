# Final Fix Report - Pause-State Production Reader

## Status

PASS. The Important production-reader finding and Minor bilingual post-AX wording finding are
implemented, verified, self-reviewed, and committed on `codex/privacy-indicators`.

- Starting HEAD: `04108ce947bfbeebcf7e31f47630115552c60efc`
- Implementation/spec commit: `8a8baed33d8c10ffee53e4e9605aa7e241a81433`
- Report commit: created after this report; its hash is returned in the controller handoff because
  a commit cannot embed its own hash.
- Installed runtime: not modified or inspected, as required.

## Changed Files

- `src/openchronicle/capture_pause.py`
- `src/openchronicle/capture/protection_monitor.py`
- `src/openchronicle/capture/scheduler.py`
- `tests/test_capture_pause.py`
- `tests/test_protection_monitor.py`
- `tests/test_capture_scheduler_fts.py`
- `docs/superpowers/specs/2026-08-21-pause-state-fail-closed-design.md`
- `docs/superpowers/specs/2026-08-21-pause-state-fail-closed-design.zh-CN.md`
- `.superpowers/sdd/2026-08-21-pause-state-fail-closed/final-fix-report.md` (this report)

No user configuration/data, `~/.openchronicle`, `/Applications`, overlay protocol source, Swift
source, or unrelated repository file was changed.

## RED Evidence

The focused pre-change baseline was green:

```text
$ PYTHONPATH=src uv run pytest -q tests/test_capture_pause.py tests/test_protection.py tests/test_protection_monitor.py tests/test_capture_scheduler_fts.py
........................................................................ [ 97%]
..                                                                       [100%]
74 passed in 0.75s
```

Tests were then changed before production code. The monitor and scheduler regressions left the
`PrivacyProtectionMonitor` constructor's `pause_reader` at its production default and made the
real `Path.read_bytes()` boundary fail with `OSError("private-pause-marker-path")`. No raising
pause callable was injected.

RED command:

```text
$ PYTHONPATH=src uv run pytest -q tests/test_capture_pause.py::test_capture_is_paused_fails_closed_with_sanitized_log tests/test_protection_monitor.py::test_pause_reader_failure_stays_closed_when_inventory_policy_is_fail_open tests/test_capture_scheduler_fts.py::test_pause_state_failure_blocks_before_ax_even_when_inventory_is_fail_open
FFF                                                                      [100%]
```

Exact relevant failures and summary:

```text
E       AssertionError: assert ['could not r...-marker-path'] == ['capture pau...sed: OSError']
E         At index 0 diff: 'could not read capture pause state; remaining paused: private-pause-marker-path' != 'capture pause state unavailable; remaining paused: OSError'

E       AssertionError: assert <ProtectionState.PAUSED: 'paused'> is <ProtectionState.FAILED: 'failed'>

E       assert 0 == 1
E        +  where 0 = len([])

FAILED tests/test_capture_pause.py::test_capture_is_paused_fails_closed_with_sanitized_log
FAILED tests/test_protection_monitor.py::test_pause_reader_failure_stays_closed_when_inventory_policy_is_fail_open
FAILED tests/test_capture_scheduler_fts.py::test_pause_state_failure_blocks_before_ax_even_when_inventory_is_fail_open
3 failed in 0.28s
```

These failures reproduced all three review mechanisms: private exception text leaked from the
compatibility wrapper, the monitor observed `PAUSED` instead of a typed failure, and the scheduler
returned before the monitor could render/publish a decision.

## Implementation

1. Extracted `capture_is_paused_strict()` from the existing pause evaluator. It retains legacy,
   indefinite, timed-pause, warning-arm, heartbeat, race-check, and safe auto-resume behavior,
   while propagating initial and race-check pause-file read `OSError`s.
2. Kept `capture_is_paused()` as the monitorless compatibility wrapper. It catches strict-reader
   `OSError`s, returns `True`, and logs only fixed copy plus the exception type. Expired-pause unlink
   failures still remain paused and now also log only the exception type.
3. Changed `PrivacyProtectionMonitor`'s production default to `capture_is_paused_strict`. Existing
   monitor handling maps failures to `pause_state_unavailable`, publishes yellow `FAILED`, renders
   rather than clears, and logs only `OSError` plus the fixed reason.
4. Changed `_build_capture()` to call the compatibility wrapper only when no protection monitor is
   present. With a monitor, both pause policy and pause-read uncertainty come from the monitor.
5. Replaced the fake pre-AX pause-failure scheduler regression with a real monitor/default-reader
   integration regression. It proves `FAILED`, fixed reason, render path, terminal policy,
   AX-blocking policy, zero provider calls, zero screenshot calls, and marker-free logs.
6. Corrected both pause-state specs: the pre-AX gate prevents AX traversal, while post-AX validation
   records one provider call and discards the already-read in-memory AX before screenshots, JSON,
   FTS, timeline, memory, or model processing.

## GREEN Evidence

The same three-test command passed after the minimal production changes:

```text
...                                                                      [100%]
3 passed in 0.04s
```

## Required Verification

### Focused pause/protection/scheduler tests

```text
$ PYTHONPATH=src uv run pytest -q tests/test_capture_pause.py tests/test_protection.py tests/test_protection_monitor.py tests/test_capture_scheduler_fts.py tests/test_daemon_protection.py
........................................................................ [ 86%]
...........                                                              [100%]
83 passed in 0.80s
```

This includes timed/indefinite pause and auto-resume tests, production-default monitor failures,
pre/post-AX gates, ordinary inventory fail-open, and daemon monitor compatibility.

### Complete Python suite

```text
$ PYTHONPATH=src uv run pytest -q
........................................................................ [ 29%]
........................................................................ [ 59%]
........................................................................ [ 88%]
...........................                                              [100%]
243 passed in 3.06s
```

### Scoped Ruff

```text
$ uv run ruff check src/openchronicle/capture_pause.py src/openchronicle/capture/protection_monitor.py src/openchronicle/capture/scheduler.py tests/test_capture_pause.py tests/test_protection_monitor.py tests/test_capture_scheduler_fts.py
All checks passed!
```

### SwiftPM

```text
$ swift test --package-path macos/OpenChronicleApp
Test Suite 'OpenChronicleAppPackageTests.xctest' passed at 2026-08-21 21:20:18.389.
	 Executed 26 tests, with 0 failures (0 unexpected) in 2.974 (2.977) seconds
Test Suite 'All tests' passed at 2026-08-21 21:20:18.389.
	 Executed 26 tests, with 0 failures (0 unexpected) in 2.974 (2.979) seconds
◇ Test run started.
↳ Testing Library Version: 1902
↳ Target Platform: arm64e-apple-macos14.0
✔ Test run with 0 tests in 0 suites passed after 0.001 seconds.
```

### Wheel and sdist

```text
$ uv build
Building source distribution...
Building wheel from source distribution...
Successfully built dist/openchronicle-0.1.0.tar.gz
Successfully built dist/openchronicle-0.1.0-py3-none-any.whl
```

### Signed macOS app build

```text
$ bash scripts/build-macos-app.sh
[0/1] Planning build
Building for production...
[0/2] Write swift-version--58304C5D6DBC2206.txt
Build complete! (0.13s)
/var/folders/dx/b869s_xd5c5d92fb6_t7gpp00000gn/T//openchronicle-app-signing.69h0qK/OpenChronicle.app: replacing existing signature
/var/folders/dx/b869s_xd5c5d92fb6_t7gpp00000gn/T//openchronicle-app-signing.69h0qK/OpenChronicle.app: valid on disk
/var/folders/dx/b869s_xd5c5d92fb6_t7gpp00000gn/T//openchronicle-app-signing.69h0qK/OpenChronicle.app: satisfies its Designated Requirement
/var/folders/dx/b869s_xd5c5d92fb6_t7gpp00000gn/T//openchronicle-app-signing.69h0qK/verify/OpenChronicle.app: valid on disk
/var/folders/dx/b869s_xd5c5d92fb6_t7gpp00000gn/T//openchronicle-app-signing.69h0qK/verify/OpenChronicle.app: satisfies its Designated Requirement
Built /Users/tkandi/Desktop/Codex/OpenChronicle/dist/OpenChronicle.app.zip
Signing: 7BC965F18933D3ADC1C0FB915404643C319F5046
```

### Whitespace

```text
$ git diff --check
(no output; exit 0)
```

### Sandbox-only retries

The first sandboxed baseline/Swift/app commands could not access user-level compiler/package
caches (`Operation not permitted`) and the nested Swift sandbox reported
`sandbox-exec: sandbox_apply: Operation not permitted`. Each required command was rerun with
approved host access and produced the successful outputs above. No dependency, source, config,
installed app, or runtime state was altered to address those runner-only failures.

## Self-Review

- Production default verified: both monitor and scheduler regressions omit `pause_reader`; the
  failure is introduced below the production default at `Path.read_bytes()`.
- Typed failure/render verified: actual snapshots are `FAILED` with
  `pause_state_unavailable`; overlay render is called once and clear is not called.
- Scheduler policy verified: the actual snapshot satisfies both terminal and AX-blocking policy;
  pre-AX provider calls remain zero, and the existing post-AX regression records one call.
- Sanitization verified: the synthetic private marker is absent from monitor, scheduler, and
  compatibility logs; logs contain only fixed copy, reason, and exception type.
- Compatibility verified: no-monitor read errors still return paused; ordinary inventory failures
  remain fail-open when configured; timed and indefinite pause semantics remain green.
- Protocol/state verified: no overlay Python/Swift protocol source changed; existing `FAILED`
  state and yellow presentation remain in use; Python and Swift suites pass.
- Persistence boundary verified: terminal returns occur before screenshots and before
  `_write_capture()`, so JSON, FTS, timeline, memory, and model stages receive no result.
- Scope verified: staged implementation contained exactly the eight listed production/test/spec
  files. Generated `dist/` and `.build/` artifacts are ignored and were not committed.

## Concerns

No code or specification concerns remain. Installed-runtime and live black-box checks were
intentionally not run because the findings contract assigns deployment and installed verification
to the controller. The signed archive was built and verified locally without installation.
