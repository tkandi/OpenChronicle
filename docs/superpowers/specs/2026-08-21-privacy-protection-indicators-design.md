# Privacy Protection Indicators

## Summary

OpenChronicle will show a small, per-display native overlay whenever that display is
excluded from capture for privacy reasons. The overlay is driven by the same protection
snapshot that controls screenshot selection, so it is evidence of an active backend
decision rather than an independent visual guess.

The indicator works whenever the OpenChronicle daemon is running. It does not depend on
the menu bar application remaining open.

## Goals

- Let a user confirm at a glance that a display containing a denied privacy window is
  excluded from screenshots and semantic content capture.
- Keep the normal indicator unobtrusive enough to remain visible during privacy work.
- Support five selectable visual styles plus an explicit off state.
- Represent privacy protection, manual pause, and fail-closed errors distinctly.
- Keep overlay state and screenshot decisions synchronized.
- Work across multiple displays, Spaces, and full-screen applications.

## Non-goals

- The overlay does not redact individual regions inside a screenshot. A target display is
  either captured or omitted.
- The overlay does not inspect webpage contents, form fields, or background AX trees.
- The overlay does not claim that no metadata is read. Window title and geometry metadata
  are still read locally to determine protection boundaries.
- This change does not generalize all capture settings to hot reload. Only the indicator
  style is required to update without a daemon restart.

## User-visible behavior

### Configurable styles

The `[capture]` section gains one setting:

```toml
privacy_indicator_style = "pill"
```

Allowed values are:

| UI label | Config value | Appearance |
|---|---|---|
| Off | `off` | No overlay; the existing privacy guard still operates. |
| A: Border | `border` | Thin display-edge border with a compact state badge. |
| B1: Shield | `shield` | Small solid shield icon in the lower-right corner. |
| B2: Protected | `pill` | Small shield plus `已保护` pill in the lower-right corner. |
| B3: Quiet shield | `quiet-shield` | Small translucent outlined shield in the lower-right corner. |
| C: Banner | `banner` | Narrow state banner at the top of the display. |

The default is `pill`. Existing installations that do not contain the new key receive the
same default when configuration is loaded.

Lower-right indicators are inset from `NSScreen.visibleFrame` so they avoid the Dock.
Border and banner styles use the full screen frame. Overlays never accept keyboard focus or
mouse events.

### States

| State | Color | Text/icon | Displays |
|---|---|---|---|
| Privacy window protected | Green | Shield, with `已保护` where the style includes text | Displays excluded by the current privacy decision |
| Capture manually paused | Gray | Pause, with `已暂停` where applicable | All displays |
| Privacy detection failure | Yellow | Warning, with `截图已停用` where applicable | All displays that can be enumerated |
| No protection active | None | Overlay hidden | All displays |

The selected style controls geometry in every state. For example, `border` uses a green,
gray, or yellow border, while `shield` swaps the shield for a pause or warning symbol.

### Screenshot mode mapping

- `separate`: only displays intersecting a denied visible window are marked and omitted.
- `all`: one denied visible window omits the complete virtual desktop, so all displays are
  marked.
- `primary`: the primary display is marked when it is blocked. A secondary display that
  contains a denied window is also marked because that display is not part of the configured
  screenshot target and its sensitive window content is not traversed.

When a denied window closes, minimizes, or moves to another display, the next protection
snapshot removes or relocates the indicator. A stale snapshot may briefly over-protect a
display, but it must never permit a screenshot that a newer decision would block.

`screenshot_privacy_mode = "off"` retains the foreground app, bundle, title, URL, and text
denylist but disables the background window inventory and indicator. With the background guard
enabled, `screenshot_privacy_fail_closed = false` permits an unprotected capture after a genuine
inventory failure; stale indicators are cleared and the decision is never presented as visually
confirmed. `screenshot_privacy_fail_closed = false` applies only to window/display inventory
failures. If the pause state cannot be read, OpenChronicle shows the yellow failed indicator and
aborts the complete capture regardless of this setting. The approved default remains
`skip-monitor` with fail-closed enabled.

## Security semantics

The indicator means:

- the marked display is not included in a screenshot under the current capture mode; and
- OpenChronicle does not traverse an active window's AX contents while that window is on a
  marked display.

The indicator does not mean that no window metadata is read. The privacy detector reads
top-level app name, bundle identifier, title, position, size, and minimized state locally.
Those detection-only values are not added to capture JSON, FTS, timeline, memory, or model
requests.

If the active window itself matches the denylist, the existing foreground guard still skips
the entire capture before AX traversal. If a different active window is on a marked display,
OpenChronicle may retain its top-level window metadata but omits its AX tree, focused value,
visible text, and URL. If the denied window is in the background, only its top-level detection
metadata is read.

## Architecture

### Authoritative protection snapshot

A new `PrivacyProtectionMonitor` owned by the daemon produces immutable snapshots. Each
snapshot contains:

- a monotonically increasing generation number;
- state: `inactive`, `protected`, `paused`, or `failed`;
- capture mode;
- selected indicator style;
- display frames and blocked display identifiers;
- the display containing the active window, when it can be determined;
- candidate active displays when exact focused-window identity is unavailable;
- the refresh-request epoch covered by the published decision;
- creation timestamp and freshness deadline.

Window events increment a monotonic request epoch and request an immediate refresh. Each
published decision records the epoch captured before its inventory read. A non-forced
validation may reuse a fresh decision only when it covers every request observed at the
validation point; requests arriving during a refresh cause another synchronous refresh. A
one-second watchdog catches apps that do not emit reliable AX notifications. Every capture
attempt forces a fresh refresh before AX work.

The monitor maps denied window regions and the active window to physical displays once. The
overlay, AX gate, and screenshot selector consume this same mapping.

### Partial window identity

The inventory begins with CoreGraphics
`optionOnScreenOnly` records and retains only alpha-positive, positive-size layer-0 windows.
A nonblank CoreGraphics title is marked available. For a blank title, AX may supply a title only
after a globally unique exact match on the same PID and `CGWindowID`; geometry never authorizes
identity. If that exact match or title read is unavailable, the CoreGraphics record is still
emitted with `title_available = false` instead of failing unrelated windows.

When title deny patterns exist, an unknown-title window conservatively protects only the
display regions it intersects. Exact app and bundle rules continue to match normally. If the
frontmost PID's focused AX window cannot be matched exactly, every on-screen layer-0 window for
that PID is emitted as an active candidate. AX is blocked only when a candidate display
intersects a protected display; uncertainty on a separate display does not become a global AX
failure. Helper exit, malformed output, and invalid or absent display inventory remain
fixed-code `failed` states. These certainty flags and detected metadata remain local and are
never sent to the overlay IPC.

### Native overlay helper

A bundled Swift executable, `mac-privacy-overlay`, is started and supervised by the daemon.
It runs as an accessory AppKit process without a Dock icon. One borderless, non-activating
`NSPanel` is created for each indicated display with these characteristics:

- `ignoresMouseEvents = true`;
- never becomes key or main;
- visible across Spaces;
- allowed alongside full-screen applications;
- positioned from CoreGraphics display bounds and `NSScreen` geometry;
- rendered above ordinary application windows.

Python sends newline-delimited JSON commands over stdin. The helper acknowledges a generation
on stdout only after the corresponding panels have been updated on the main thread. A command
contains no sensitive title or app text, only state, style, generation, and display geometry.

Example command:

```json
{"generation":42,"state":"protected","style":"pill","displays":[{"id":2,"left":1920,"top":0,"width":1920,"height":1080}]}
```

Example acknowledgement:

```json
{"generation":42,"rendered":true}
```

### Capture gates

Before any AX traversal:

1. Force a fresh privacy scan and build the next snapshot.
2. Send the snapshot to the overlay helper.
3. If the active window is on a marked display, omit AX traversal and all derived S1 content.
4. If the snapshot is `paused`, omit the complete capture regardless of inventory or capture
   options. If the pause state cannot be read, treat it as a yellow failed state and omit the
   complete capture regardless of `screenshot_privacy_fail_closed`.
5. If the snapshot is `failed` and fail-closed is enabled, omit both AX and screenshot capture.

Before each screenshot attempt, refresh again if the snapshot is no longer fresh. If a
privacy state is active and indicators are enabled, wait for the matching generation
acknowledgement, then capture only targets allowed by that exact snapshot.

If the helper fails to start, exits, or misses the acknowledgement deadline while an enabled
indicator is required, screenshots fail closed. The AX display gate and foreground denylist
remain active independently. With `privacy_indicator_style = "off"`, overlay availability
does not affect the existing privacy guard.

When inventory fails and `screenshot_privacy_fail_closed = false`, the monitor clears any stale
overlay, publishes the fixed failure reason with `indicator_confirmed = false`, and the
scheduler performs an unprotected capture. It must not show the yellow disabled-capture state.

### Pause and failure handling

The monitor checks the structured pause state independently of the capture scheduler. A pause
therefore remains visible even though normal capture work is skipped.

Privacy enumeration failure produces a `failed` snapshot. Under the default fail-closed policy
it hides stale green overlays, displays the yellow state, and aborts the complete capture. If
display enumeration is unavailable, the helper shows the warning on every `NSScreen` it can
discover itself. Under explicit fail-open policy it instead clears stale overlays and permits
capture without visual confirmation. `screenshot_privacy_fail_closed = false` applies only to
window/display inventory failures. If the pause state cannot be read, OpenChronicle shows the
yellow failed indicator and aborts the complete capture regardless of this setting.

If the overlay process disconnects, the daemon invalidates the last acknowledgement and
restarts it with bounded backoff. A required but unacknowledged overlay continues to block
screenshots. Because a failed overlay process cannot render its own warning, this case has no
indicator; the missing marker tells the user that protection is not visually confirmed.

## Settings integration

The native Settings capture section adds a visual single-choice picker with previews for Off,
A, B1, B2, B3, and C. The selection is included in the existing configuration snapshot,
draft, validation, and patch flow.

The daemon watches the config file modification time and reloads only
`capture.privacy_indicator_style` into the monitor. A valid style change updates active panels
within one watchdog interval without restarting capture. Invalid values are rejected by the
config editor and normalized to `pill` by the regular config loader as a final fallback.

## Packaging and lifecycle

- Add the Swift source and build script to wheel resources.
- Extend `install.sh` to compile and verify `mac-privacy-overlay` with the other macOS helpers.
- Start the helper only when the daemon runs and the configured style is not `off`. Paused and
  failed states are shown on all displays when indicators are enabled.
- Do not start the background monitor or helper when `screenshot_privacy_mode = "off"`.
- Terminate the helper and remove every panel during daemon shutdown.
- Do not tie helper shutdown to menu bar application shutdown.

## Testing

### Python tests

- Config parsing, normalization, config-editor validation, snapshots, and patches for all six
  values.
- Display mapping for `primary`, `separate`, and `all`.
- Unknown-title windows and same-display versus other-display active candidates.
- Snapshot transitions for inactive, protected, paused, and failed states.
- An active window on a marked display produces no AX tree or derived S1 content; in `all`
  mode, a privacy match suppresses AX traversal on every display.
- Screenshot uses the same acknowledged generation as the overlay.
- Missing, crashed, malformed, and timed-out overlay acknowledgements fail closed.
- `off` preserves existing privacy behavior without requiring the overlay.
- Config hot reload updates only the indicator style.
- Event-during-AX and event-during-refresh epochs cannot reuse a pre-event decision.
- Privacy mode `off` and explicit inventory fail-open preserve their legacy control semantics.
- An unreadable pause state remains fail-closed when
  `screenshot_privacy_fail_closed = false`, with a yellow failed indicator.

### Swift tests and build checks

- Style/state presentation model selects the expected symbol, text, color, size, and anchor.
- AppKit panel controller creates, updates, relocates, and removes per-display panels.
- Panels are non-activating and click-through.
- NDJSON command decoding and acknowledgement encoding.
- The bundled helper compiles for supported arm64 and x86_64 macOS targets.

### Integration and manual acceptance

- Open a blank Edge InPrivate window on a secondary display and verify the chosen green
  indicator appears there while only the safe display is captured in `separate` mode.
- Move the window between displays and verify the marker follows it.
- Verify `all` marks every display and stores no screenshot.
- Verify foreground InPrivate produces no AX tree or capture JSON.
- Verify pause and fail-closed indicators on every display.
- Quit the menu bar application and verify indicators continue while the daemon runs.
- Kill the overlay helper and verify screenshots stop until it is acknowledged again.
- Switch all styles in Settings without restarting the daemon.

## Privacy and logging

Logs may contain generation numbers, state names, style names, display identifiers, and helper
errors. They must not contain denied window titles, application names, bundle identifiers, or
screen contents. Overlay commands likewise contain no denylist values or detected window text.
