# Capture

Capture is the only layer that touches the outside world. It produces one JSON file per observation into `~/.openchronicle/capture-buffer/`; nothing above it ever talks to macOS directly.

## Two signal sources

**`mac-ax-watcher`** (primary, event-driven). A vendored Swift binary that subscribes to AX notifications across all running apps: window focus, value changes (typing), title changes, app activation. It emits one JSON object per event on stdout. The Python side reads that stream line-by-line in `capture/watcher.py` → `capture/event_dispatcher.py`.

**Heartbeat timer** (fallback). Every `heartbeat_minutes` (default 10), the scheduler fires a capture even if no event arrived — so long idle periods leave a trail. Set `heartbeat_minutes = 0` to disable entirely (watcher-only); values `>0` are clamped to a 60-second floor.

Both funnel into `capture_once` in `capture/scheduler.py`, which runs:

1. `window_meta.active_window()` — app name, title, bundle_id via `NSRunningApplication`.
2. Force a protection-monitor decision when the background guard is enabled.
3. `ax_capture.capture_frontmost(focused_window_only=True)` — one-shot invocation of `mac-ax-helper` for the current window, pruned to `ax_depth` layers, unless the decision blocks AX.
4. `s1_parser.enrich()` — extracts `focused_element`, `visible_text`, and `url` from an allowed AX tree (see [S1 fields](#s1-fields) below).
5. Validate again against every refresh request observed during AX work; a newly protected or paused decision discards the complete in-memory capture.
6. Capture screenshots unless `include_screenshot = false`. Depending on the
   privacy mode, this uses source-filtered ScreenCaptureKit capture or the
   display-level `mss` fallback described below.
7. Write `{iso8601_safe}.json` to the buffer.

Privacy denylist checks can short-circuit this flow:

- `deny_app_names`, `deny_bundle_ids`, and `deny_window_title_patterns` run immediately after window metadata is available, before AX and screenshots.
- `deny_url_patterns` and `deny_text_patterns` run after S1 parsing, before screenshots and disk writes.
- Denied captures are not written to JSON, not inserted into `captures_fts`, not absorbed into timeline blocks, and not sent to any model stage.

### Screenshot privacy modes

`screenshot_privacy_mode` has four values:

- **`off`** disables the background window monitor and protection indicator.
  Foreground app, bundle, title, URL, and text deny rules remain active.
- **`skip-monitor`** uses `mss` and omits every target display intersecting a
  protected window. In `all` mode, one protected display omits the complete
  virtual desktop image.
- **`mask-window`** uses the macOS 14 or newer ScreenCaptureKit helper to
  resolve protected windows and OpenChronicle's own indicator windows to unique
  owning applications, exclude those complete applications at the source, then
  paint the protected window bounds gray in the returned image.
- **`exclude-window`** uses the same source exclusion but leaves the pixels
  behind the excluded window visible instead of adding a gray mask.

Application-level exclusion intentionally removes every window owned by a
protected or overlay application, including otherwise normal windows, sheets,
menus, popovers, and floating panels. If any requested window has no unique,
valid owning application, filtered capture returns a fixed error and uses the
safe fallback; it never weakens the filter to window-ID-only exclusion.

The daemon runs the normal protection monitor for `skip-monitor`,
`mask-window`, and `exclude-window`. It starts an `off`-mode monitor only when
an existing Protection Diagnostics guard must remain fail-closed.

With the three monitored modes, the bundled `mac-window-list`
helper inventories only alpha-positive, positive-size windows returned by
CoreGraphics as on-screen at normal layer 0. It records their owner, bundle ID,
CoreGraphics title, and CoreGraphics bounds immediately before `mss` captures
pixels. When one of those windows has a blank CoreGraphics title, the helper
first collects top-level AX element and window-ID metadata without reading the
AX title, then requires a globally unique, exact same-PID `CGWindowID` match
before reading that accepted element's title. AX position, size, and geometry
never authorize or locate a fallback. A blank title that cannot be resolved is
still emitted with `title_available = false`; it does not invalidate unrelated
windows. If any title deny rule is enabled, that unknown-title window is treated
as sensitive only on the displays its CoreGraphics bounds intersect.

The focused AX window is marked active only through the same exact identity. If
that identity is unavailable, every on-screen layer-0 window owned by the
frontmost PID is emitted as an active candidate. AX is then suppressed only when
a candidate intersects a protected display. This avoids a global outage when
the uncertain foreground candidates are confined to a different display, while
never treating an unknown title as allowed. Genuine helper exit, JSON parse, or
display-inventory failures still produce a fixed-code `failed` decision.

Menus, popovers, and non-layer-0 floating panels are not independently treated
as protected full-display windows by their titles. Once a normal protected
window resolves to an owning application, however, filtered capture excludes
all of that application's auxiliary windows at the source. Protection can also
apply when an app or bundle denylist independently matches an inventoried
normal window or the foreground window. The helper never traverses background AX trees
or reads their controls and contents. In `separate` mode, only monitors
intersecting a denied inventoried window are skipped. In `all` mode, any denied
inventoried window skips the full virtual-desktop screenshot when the
skip-monitor fallback is used. Each watcher event
increments a request epoch; pre-capture and post-AX validation return only a
decision that covers every request observed at validation time.

Window-filtered capture is used only for a protected, complete window decision:
the display inventory and protected window IDs/bounds must be filterable, the
same-generation indicator acknowledgement must be confirmed, and a visible
indicator must report every indicator and input-panel window ID. Indicator
style `off` needs no overlay IDs. Diagnostics protection, unknown titles,
missing or duplicate protected IDs, an unavailable helper, or another
non-indicator incomplete decision falls back to `skip-monitor` using the latest
protected display regions. An unconfirmed non-`off` indicator, including a
failed inactive clear, stops before `mss`. For `mask-window` and
`exclude-window` fallback, the monitor is forced
to publish a fresh, non-terminal decision with complete protected display
regions and a confirmed non-`off` indicator before `mss`. After `mss`, another
forced decision must still confirm the indicator and have identical
authorization semantics or the fallback frames are discarded. Native
`skip-monitor` and diagnostics guard-only `off` capture preserve their legacy
current-decision behavior and do not compare window-filtering-only fields. The
protected display is never captured by unblocked `mss`.

The Swift helper fingerprints all shareable displays and every on-screen window
(window ID, owner PID, finite frame, and title) before capture, reloads the
complete shareable inventory after all display captures, and compares the two
before PNG encoding or stdout. Any change returns a fixed `content_changed`
error, so no PNG bytes cross the helper boundary. After a successful helper
response, OpenChronicle also forces a fresh protection decision before keeping
the image. A change to protected windows or bounds,
display IDs or bounds, filtering eligibility, or overlay IDs discards the
filtered frames and applies the latest skip-monitor decision. A terminal
decision keeps no stale screenshot or capture.

On a genuine inventory failure, `screenshot_privacy_fail_closed = true` aborts
the complete tick. Setting it to `false` preserves the legacy fail-open policy
for `skip-monitor` and `off`, but never for `mask-window` or `exclude-window`:
those two modes always render the yellow failed indicator and fail closed. A
direct non-`off` `capture-once` without a background monitor runs the
visible-window check and can use only the skip-monitor path; if enumeration
fails, it does not take a screenshot. Direct `off` capture preserves the legacy
unblocked screenshot behavior after foreground denylist checks.

`screenshot_privacy_fail_closed = false` applies only to window/display inventory
failures. If the pause state cannot be read, OpenChronicle shows the yellow failed
indicator and aborts the complete capture regardless of this setting.

Protected and overlay window IDs are transient capture authorization data. They
remain in memory and are never added to capture JSON, diagnostics payloads,
logs, FTS, timeline or memory files, model requests, or MCP surfaces. Helper
acknowledgement payloads, stderr, and private failure details are not logged.

This guard protects windows identifiable by app, bundle, or title metadata. It
cannot classify sensitive content inside an otherwise allowed app. The double
inventory snapshot detects persistent additions, removals, owner/frame changes,
and title-classification changes, while application filters also exclude new
windows from an app already classified as protected. The OS APIs still cannot
prove absence of a different application's privacy window that appears and
disappears entirely between the two snapshots. For high-risk workflows, keep
password managers in the app/bundle denylist and pause capture before displaying
secrets.

## Privacy protection indicators

When `privacy_indicator_style` is not `off`, the local `mac-privacy-overlay`
helper displays the protection state after the protection decision is confirmed.
The selectable styles are `off`, `border`, `shield`, `pill`, `quiet-shield`, and
`banner`.

The helper acknowledges a non-`off` generation only when every visible
indicator and input panel has a distinct positive UInt32 window number. If any
number is unavailable or duplicated, the panels remain visible but the helper
returns an unconfirmed acknowledgement with no IDs; screenshot capture remains
stopped until a later generation is confirmed.

- Green means that display has been excluded by the same-generation protection
  decision used for capture.
- Gray means capture is paused.
- Yellow means detection failed and screenshot capture is fail-closed.
- No indicator is not proof of protection: if the overlay helper itself fails,
  it cannot display the yellow failure state and screenshot capture remains
  stopped until the helper is confirmed again.

### Protection reasons and diagnostics

Protection reasons are derived from the same immutable snapshot that drives the
screenshot and active-window AX gates. They include app, bundle, and title-rule
matches; conservative unknown-title protection; `all`-mode inheritance;
diagnostics self-protection; pause state; fixed inventory failures; and an
`indicator_unconfirmed` code when a required overlay generation is not
acknowledged. Reasons from every matching window are combined per display,
deduplicated, priority ordered, and bounded to eight. A global pause or failure
reason applies to every display.

Category payloads contain only fixed reason codes and display IDs. Bounded exact
fields can contain an app name, bundle ID, window title, matched rule, source
display, or effective resume time, but they remain inside the privacy subsystem.
The overlay receives exact fields only for a display already excluded by that
same protection snapshot. Category and tiered overlay modes never receive them.

Protection Diagnostics uses
`~/.openchronicle/runtime/privacy-diagnostics.sock`, an owner-only Unix-domain
socket in a mode `0700` runtime directory; the socket itself is mode `0600`.
It is not exposed over TCP, HTTP, or MCP. Category snapshots need no lease.
Exact reveal is ordered as follows:

1. The app identifies the display containing the diagnostics window and writes
   a guard containing only a lease nonce, app PID, and display ID.
2. The monitor refreshes, protects that display, and waits for a newer confirmed
   generation. With indicator style `off`, capture-policy publication is the
   confirmation boundary.
3. Only then may the socket send exact fields for that lease. During a move, the
   old and new displays stay protected until the destination is confirmed.
4. A matching release removes the guard. A stale release is rejected, and a
   disconnect retains protection until process death is confirmed.

Any diagnostics lease in `all` mode suppresses the complete virtual-desktop
screenshot. In `separate` mode, safe displays may still be captured. Exact
reason values are never added to capture JSON, screenshots, capture or privacy
logs, FTS, timeline, session or memory files, model requests, model-failure
events, normal status JSON, MCP tools/resources, or the on-disk diagnostics
guard.

Protection detection locally inspects the normal layer-0 on-screen CoreGraphics
inventory: owner or app name, bundle identifier, title, CoreGraphics position
and size, and AX-derived active state. AX can supply a blank CoreGraphics title
only after the exact same-PID `CGWindowID` resolution described above; AX
geometry is never authorization. It does not traverse background window
controls or contents. The detection inventory is used only for the protection
decision and is not copied into capture JSON, FTS, timeline, memory, or model
requests.

An unresolved blank title is represented as unknown rather than dropping the
whole inventory. With title rules configured, its intersecting display is
conservatively protected. When exact active-window identity is unavailable,
frontmost-PID layer-0 windows are active candidates; AX is blocked only where a
candidate display and a protected display overlap.

In `separate` and `primary` modes, a capture JSON for a safe display may still
be written. The protected window's content and derived AX/S1 fields are not
captured when the protection gate marks the active display as protected; the
same decision suppresses the protected screenshot region. A foreground window
that directly matches the denylist can instead skip the entire capture before
it is written, indexed, sent to timeline or memory processing, or sent to a
model.

The filename is ISO-8601 with `:` → `-` and `+` → `p` / `-` → `m` for the TZ offset. Example: `2026-04-21T17-07-32p08-00.json`.

The same capture scheduler also invokes `SessionManager.on_event` (wired as a `pre_capture_hook` in `daemon.py`), so the session cutter sees every capture-worthy event without a separate subscription path.

## Debounce / dedup / gap

Four time-based knobs throttle the event firehose (`capture/event_dispatcher.py`):

| Knob | Default | What it does |
|---|---|---|
| `debounce_seconds` | 3.0 | `AXValueChanged` events within this window collapse — only the last triggers a capture. Prevents one-capture-per-keystroke during typing. |
| `dedup_interval_seconds` | 1.0 | Same `(event_type, app)` pair within this window is dropped outright. |
| `min_capture_gap_seconds` | 2.0 | Hard floor between consecutive `capture_once` calls, regardless of event reason. |
| `same_window_dedup_seconds` | 5.0 | Non-focus-change events in the same `(bundle_id, title)` pair collapse within this window. Focus changes always bypass it. |

Tune these if you see `capture.log` flooded; the defaults produce a few hundred captures per work-day, comfortably under the buffer retention.

### Content dedup (no time window)

On top of the time-based knobs, the scheduler compares each built capture against the previous one by a content fingerprint (`hash(bundle + title + focused_element.value + visible_text + url)`, in `capture/scheduler.py`). If the fingerprint matches, the capture is **not** written and the session manager's `pre_capture_hook` is **not** fired.

This catches the case the time knobs can't: a screen that doesn't change (lock screen overnight, a paused video, an idle IDE) keeps generating AX events with the same content indefinitely. Without content-dedup those would both fill the buffer and keep the current session from ever idling out. Timestamps, triggers, and screenshots are excluded from the fingerprint so only meaningful changes count.

## AX depth — the #1 footgun

AX Trees for native Cocoa apps are shallow (5–15 layers). Electron apps (Claude Desktop, VS Code, Slack, Notion) nest user content 20–60 layers deep under chrome.

**Default `ax_depth = 100`** was chosen after diagnosing silent capture misses: a 90-second Claude Desktop conversation about an interview at 18:00 was producing captures where "18:00" appeared at character 5639 of the tree — past any reasonable prune limit. At depth 8, the tree contained only window chrome and sidebar headers; at depth 100, the full conversation was there.

If you're running on limited hardware and only care about native apps, lowering to 30 is safe. Don't go below 20.

Diagnostic:

```bash
./resources/mac-ax-helper --app-name Claude --depth 30 --raw | wc -c
# vs.
./resources/mac-ax-helper --app-name Claude --depth 100 --raw | wc -c
```

A 10×+ ratio means there's content past depth 30 you'd miss.

## What's in a capture file

```json
{
  "timestamp": "2026-04-21T17:07:32+08:00",
  "schema_version": 2,
  "trigger": { "event_type": "window_focus_changed", "app": "Claude", ... },
  "window_meta": {
    "app_name": "Claude",
    "bundle_id": "com.anthropic.claudefordesktop",
    "title": "New conversation — Claude"
  },
  "focused_element": {
    "role": "AXTextArea",
    "title": "Message composer",
    "value": "I have an interview at 18:00",
    "is_editable": true,
    "value_length": 30
  },
  "visible_text": "### New conversation — Claude\n...",
  "url": null,
  "ax_tree": { ... pruned tree with roles, titles, values ... },
  "ax_metadata": { ... },
  "screenshot": {
    "image_base64": "iVBORw0KGgoAAAANS...",
    "mime_type": "image/jpeg",
    "width": 1920,
    "height": 1200,
    "monitor": {
      "index": 1,
      "left": 0,
      "top": 0,
      "width": 1920,
      "height": 1200
    }
  },
  "screenshots": [
    {
      "image_base64": "iVBORw0KGgoAAAANS...",
      "mime_type": "image/jpeg",
      "width": 1920,
      "height": 1200,
      "monitor": { "index": 1, "left": 0, "top": 0, "width": 1920, "height": 1200 }
    }
  ]
}
```

`trigger` is `{"event_type": "heartbeat"}` for timer captures and `{"event_type": "manual"}` for `capture-once`. Screenshot fields are omitted entirely when `include_screenshot = false`.

`screenshots[]` is present only when `screenshot_monitor = "separate"`.

`screenshot_monitor` controls the image shape: `primary` keeps one legacy `screenshot`, `all` stores one `screenshot` of the all-monitors virtual desktop, and `separate` stores one entry per physical monitor in `screenshots[]` while also copying the first image into `screenshot` for older readers.

Secure fields (password inputs) are replaced with `"[REDACTED]"` at the helper level — the Python side never sees them.

## S1 fields

Ported from Einsia-Partner's `s1_collector`. These are what downstream LLM stages consume — the raw `ax_tree` is kept only for future vision-model support and debugging.

- **`focused_element`** — `{role, title, value, is_editable, value_length}` for the currently focused AX element. This is the user's cursor context: what they're typing into, which sidebar row is selected, etc.
- **`visible_text`** — a length-capped markdown rendering of the AX tree (up to ~10 k chars). What the user is currently reading on screen.
- **`url`** — regex-extracted from `visible_text` when present; `null` otherwise.

Screenshots live in the capture JSON but are **not** passed to the timeline / reducer / classifier prompts. They exist for future vision-model paths and for debugging.

## Buffer hygiene — tiered retention

Captures are pruned by the timeline tick, not the writer. After each timeline scan, `capture_scheduler.cleanup_buffer` applies three passes (oldest-safe-first), all gated on "this file has already been absorbed by a closed timeline block" so un-absorbed trailing captures are never touched:

| Pass | Condition | Action |
|---|---|---|
| **Delete** | mtime older than `buffer_retention_hours` (default **168** = 7 days) | Whole JSON removed |
| **Strip screenshot** | mtime older than `screenshot_retention_hours` (default **24**) | Rewrite JSON without `screenshot` / `screenshots` fields; sets `screenshot_stripped: true`. The AX tree, `visible_text`, `focused_element`, and `url` stay |
| **Evict by size** | Total buffer > `buffer_max_mb` (default **2000**, i.e. 2 GB; `0` disables) | Delete oldest absorbed files until under the cap |

Why tiered: screenshot base64 is most of each capture's bytes but nothing downstream consumes it today (it's kept for future vision stages + debugging). Stripping it at 24h drops each stale capture to a fraction of its original size, which is what makes a 7-day window affordable. Typical steady-state footprint is in the 100s of MB.

To wipe manually:

```bash
openchronicle clean captures
```

## Search index — `captures_fts`

Every successful capture write is also indexed into an FTS5 virtual table (`captures_fts`, backed by a `captures` content table — see `src/openchronicle/store/fts.py`). This is what powers the MCP `search_captures` and `current_context` tools, which let LLM clients reach the raw screen content directly without having to scan JSON files on disk.

**Lifecycle.**

| Event | Effect on index |
|---|---|
| `_write_capture` (write-through) | Upsert one row into `captures` (`INSERT OR REPLACE` on the file stem). Triggers keep `captures_fts` in sync. |
| `cleanup_buffer` time-based delete | Each removed JSON file → `delete_capture(stem)` → trigger drops the FTS row. |
| `cleanup_buffer` size-based eviction | Same — each evicted file is also removed from FTS. |
| Screenshot strip | **Untouched.** Strip only removes the base64 image; the indexed text (`visible_text`, `focused_value`, `window_title`, `app_name`, `url`) is unchanged. |
| `openchronicle rebuild-captures-index` | Backfill from `~/.openchronicle/capture-buffer/*.json`. Idempotent (`INSERT OR REPLACE`). Run once after upgrading onto a populated buffer, or any time the index drifts. |

**Indexed columns.** Only the searchable text is in FTS: `app_name`, `window_title`, `focused_value`, `visible_text`, `url`. Filterable metadata (timestamp, bundle_id, focused_role) lives on the `captures` table for `WHERE`-clause filtering. Screenshots are deliberately not duplicated — the JSON file on disk stays the authoritative copy of the raw image bytes.

**Tokenizer.** `unicode61 remove_diacritics 2` — case-insensitive, accent-folded, Unicode-aware. Same setup as the compressed-memory `entries` index.

If `captures_fts` falls out of sync (e.g. capture worker crashed mid-write, or the daemon was killed during cleanup), the index is recoverable in one shot:

```bash
openchronicle rebuild-captures-index
```

## Pause

```bash
openchronicle pause
```

Drops a `~/.openchronicle/.paused` sentinel. The watcher keeps streaming but `capture_once` short-circuits on sentinel presence. `resume` removes the sentinel.

CLI-created timestamp-only sentinels are indefinite and require an explicit
`openchronicle resume`. The native macOS app can instead store a structured
timed pause in the same file. A timed pause resumes only after the app has
successfully posted its one-minute warning and maintained a recent heartbeat;
otherwise the scheduler fails closed and continues skipping capture.

## Smoke test

```bash
openchronicle capture-once
```

Writes one capture immediately, prints its path. Good for confirming Accessibility permission is granted and the helper compiled correctly.
