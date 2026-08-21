# Privacy Protection Reason Diagnostics

## Summary

OpenChronicle will explain why each display is protected, paused, or failed. The same
authoritative protection snapshot will drive screenshot selection, AX gating, the native
overlay, and a new in-app diagnostics page.

Three independent settings control where reasons appear, how much detail they contain, and
how overlay details are revealed. The approved defaults are a hybrid overlay plus diagnostics
view, exact values, and hover reveal.

## Goals

- Explain every visible privacy indicator using the decision that actually controls capture.
- Support overlay-only, diagnostics-only, and hybrid presentation.
- Support category-only, exact, and tiered detail levels.
- Support always-visible, hover, and click overlay reveal behavior.
- Keep exact app names, bundle identifiers, window titles, and rule text out of capture history,
  logs, memory, and model requests.
- Protect only the diagnostics window's display while exact diagnostics are visible, so other
  displays can continue capturing in `separate` mode.
- Preserve existing fail-closed behavior, visual styles, and multi-display semantics.

## Non-goals

- This feature does not inspect background AX trees, webpage contents, or pixels to discover new
  sensitive data.
- It does not add remote diagnostics, an MCP diagnostics tool, or a TCP/HTTP endpoint.
- It does not persist an exact-reason history.
- It does not redact a rectangular window region inside a screenshot. Protection remains
  display-scoped.
- It does not change what a denylist rule matches.

## Configuration

The `[capture]` section gains three settings:

```toml
privacy_reason_display = "hybrid"
privacy_reason_detail = "exact"
privacy_reason_trigger = "hover"
```

### Display mode

| Value | Behavior |
|---|---|
| `overlay` | Reasons appear with the native overlay only. |
| `diagnostics` | Reasons appear in the in-app diagnostics page only. |
| `hybrid` | Reasons appear in both places. This is the default. |

The existing indicator state remains visible whenever `privacy_indicator_style != "off"`.
Display mode controls only the additional reason. When indicator style is `off`, overlay reasons
are unavailable, but the diagnostics page still works.

### Detail mode

| Value | Behavior |
|---|---|
| `category` | Show fixed categories such as app rule, title rule, or manual pause. |
| `exact` | Show the available app name, bundle ID, window title, and matched rule. This is the default. |
| `tiered` | Show categories first; exact values require an explicit reveal action in diagnostics. Overlay reasons remain category-only. |

Exact values are sanitized for control characters and bounded in length before UI presentation.
Raw exception text and AX content are never reason details.

### Overlay trigger

| Value | Behavior |
|---|---|
| `always` | The reason remains expanded. |
| `hover` | The reason expands while the pointer is inside the indicator bounds and collapses on exit. This is the default. |
| `click` | A click inside the small indicator hit target toggles expansion. The click is consumed and does not reach the app underneath. |

In `hover` mode the panel remains mouse-through. The helper observes the global pointer position
without taking focus or receiving the underlying click. In `click` mode only the indicator hit
target receives mouse input; every other part of the display remains mouse-through. Panels remain
non-activating and can never become key or main windows.

## User-visible Reasons

### Protected

- app-name rule matched;
- bundle-ID rule matched;
- window-title rule matched;
- a title rule exists but the visible title could not be confirmed, so the display is protected
  conservatively;
- `all` mode inherited protection from a match on another display;
- exact diagnostics are visible, so the display containing the diagnostics window is protected.

### Paused

- indefinite manual pause;
- timed pause, including the effective resume time;
- timed pause is waiting for its warning/heartbeat safety gate.

### Failed

- pause state unavailable;
- window helper unavailable, exited, or returned malformed output;
- display inventory empty or invalid;
- multiple active windows were reported;
- active or sensitive window could not be mapped to a display;
- required overlay acknowledgement was not confirmed.

When a display has multiple reasons, the overlay shows the highest-priority reason plus `+N`.
The diagnostics page lists every reason. Priority is failed, paused, diagnostics self-protection,
direct rule match, conservative unknown-title protection, then inherited `all` protection.

## Authoritative Reason Model

`ProtectionSnapshot` gains immutable per-display reason records. Each record contains:

- a fixed `ProtectionReasonCode`;
- affected display ID and optional source display ID;
- state and priority;
- matched field type;
- an inherited flag;
- optional exact details already present in the top-level detection inventory: app name, bundle ID,
  window title, and matched rule.

Reason matching returns structured records instead of discarding the current app/bundle/title
match reason after display mapping. No additional AX traversal or screenshot analysis is used.
Screenshot gating, AX gating, overlay commands, logs, and diagnostics all consume this snapshot.

The diagnostics-self-protection reason is composed with the current privacy decision. It does not
replace a denylist, pause, or failure reason.

## Overlay Data Flow

The existing daemon-owned `mac-privacy-overlay` helper receives reason data in the same local
stdin/stdout protocol used for state and display geometry.

- Category mode sends only fixed reason codes and counts.
- Exact mode sends bounded exact fields only for displays already excluded by the snapshot.
- Tiered mode sends category data to the overlay.
- The helper never logs command bodies.
- A reason presentation failure falls back to the fixed category while capture protection remains
  unchanged.

The collapsed status remains the existing protected, paused, or failed indicator. Trigger mode
changes only expansion of the reason content. A lightweight pointer-location timer runs only while
an overlay is visible; it does not require a global event tap for hover.

## In-app Diagnostics Data Flow

The daemon exposes a dedicated Unix-domain socket at
`~/.openchronicle/runtime/privacy-diagnostics.sock`. The runtime directory is owner-only and the
socket accepts only the same user. It is not part of MCP and is never bound to a network address.

The socket streams generation-numbered diagnostic snapshots to the native app. Category records
can be read directly. Exact records require an acknowledged display-protection lease:

1. The diagnostics page identifies the display containing its window.
2. It requests exact reveal for that display.
3. The daemon adds a diagnostics-protection reason for the display and publishes a new protection
   generation.
4. The app waits until the overlay and capture policy acknowledge that generation.
5. Only then does the daemon send exact values and the app reveal them.

The guard persists only non-sensitive metadata: lease nonce, app PID, and protected display IDs.
No reason value is written to disk. This metadata lets a restarting daemon remain fail-closed while
an exact diagnostics window may still exist.

When the window moves, the app hides exact values first. The daemon protects the new display before
releasing the old display, then the app reveals again. When leaving the page, the app hides exact
values before releasing the lease. A pre-existing manual or timed pause is independent and is never
released by diagnostics.

If the app connection fails or its state is uncertain, exact values are hidden and the lease stays
protected. The lease is removed automatically only after the app process is confirmed gone. If the
diagnostics display is protected while screenshot mode is `all`, the existing all-mode rule still
omits the complete virtual-desktop screenshot.

## Native App

The Capture settings page adds three compact pickers for reason display, detail, and overlay
trigger. Overlay trigger controls are disabled when display mode is `diagnostics` or indicator style
is `off`.

A new **Protection Diagnostics** page shows one row per display:

- display label and primary status;
- inactive, protected, paused, or failed state;
- all reason categories and exact details allowed by the selected detail mode;
- whether screenshots and active-window AX are currently blocked;
- snapshot generation, acknowledgement state, and update age.

In exact mode the page obtains its display-protection lease automatically before rendering values.
In tiered mode the page starts with categories and provides an explicit exact-reveal action.

## Logging and Privacy Boundary

Transition logs may contain generation, display IDs, fixed reason codes, mode, and confirmation
state. They must never contain exact app names, bundle IDs, titles, rule text, socket payloads,
lease nonces, or raw exception text.

Exact reason values must not be copied into capture JSON, screenshots retained by OpenChronicle,
FTS, timeline, sessions, memory Markdown, model prompts, model-failure events, or MCP responses.
They exist only in the daemon snapshot, local overlay command, and protected diagnostics UI.

This protects OpenChronicle from capturing its own exact diagnostics. It does not prevent another
screen-sharing or recording product from seeing values intentionally displayed on screen.

## Failure Handling

- Socket unavailable: diagnostics shows category-safe unavailable state; exact fields remain hidden.
- Lease acknowledgement timeout: exact fields remain hidden and the target display remains
  protected until a safe release can be confirmed.
- App crash or disconnect: keep the lease while the app process may exist; clear only after process
  death is confirmed.
- Daemon restart: load non-sensitive guard metadata before capture starts and require a fresh app
  handshake before revealing values.
- Overlay helper failure: retain existing capture fail-closed behavior; reason UI failure never
  weakens screenshot or AX gates.
- Pointer tracking failure: collapse reason content and preserve status/capture behavior.
- Invalid configuration values: normalize to `hybrid`, `exact`, and `hover`.

## Performance

- Reuse existing top-level window metadata and denylist checks; do not add background AX traversal.
- Compute reason records during the existing snapshot build.
- Push diagnostics only when generation changes.
- Poll pointer position at a bounded rate only while overlay panels are visible.
- Bound reason counts and exact string lengths in every IPC payload.

## Testing and Acceptance

- Config defaults, validation, config-editor snapshots, and Swift draft/patch round trips.
- Pure per-display reason mapping for app, bundle, title, unknown title, inherited `all`, pause,
  failure, and diagnostics self-protection.
- Multiple reasons, priority, `+N`, sanitization, and bounded exact values.
- Proof that exact values never enter logs, capture JSON, FTS, timeline, memory, MCP, or model input.
- Overlay protocol compatibility, category/exact/tiered presentation, always/hover/click behavior,
  pointer pass-through, click hit testing, non-activation, Spaces, and full-screen behavior.
- Unix-socket ownership, reconnect, generation ordering, stale commands, malformed payloads, and
  same-user enforcement.
- Diagnostics lease acquire, acknowledgement, move between displays, release ordering, daemon
  restart, app crash, and interaction with existing manual/timed pause.
- `separate`, `primary`, and `all` screenshot modes plus active-window AX gating.
- Live safe acceptance with blank InPrivate windows on multiple displays; no real private content.
- Installed app/backend ownership, helper count, source/install hashes, Python/Swift suites, package
  builds, signing, and privacy-only log scans.

## Compatibility

Existing configurations receive the approved defaults. Existing indicator styles and privacy
rules continue to work. Optional reason fields are added compatibly to the overlay protocol, and
missing fields decode as no reason. Category diagnostics remain usable when exact reveal is
unavailable.
