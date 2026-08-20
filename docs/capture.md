# Capture

Capture is the only layer that touches the outside world. It produces one JSON file per observation into `~/.openchronicle/capture-buffer/`; nothing above it ever talks to macOS directly.

## Two signal sources

**`mac-ax-watcher`** (primary, event-driven). A vendored Swift binary that subscribes to AX notifications across all running apps: window focus, value changes (typing), title changes, app activation. It emits one JSON object per event on stdout. The Python side reads that stream line-by-line in `capture/watcher.py` → `capture/event_dispatcher.py`.

**Heartbeat timer** (fallback). Every `heartbeat_minutes` (default 10), the scheduler fires a capture even if no event arrived — so long idle periods leave a trail. Set `heartbeat_minutes = 0` to disable entirely (watcher-only); values `>0` are clamped to a 60-second floor.

Both funnel into `capture_once` in `capture/scheduler.py`, which runs:

1. `window_meta.active_window()` — app name, title, bundle_id via `NSRunningApplication`.
2. `ax_capture.capture_frontmost(focused_window_only=True)` — one-shot invocation of `mac-ax-helper` for the current window, pruned to `ax_depth` layers.
3. `s1_parser.enrich()` — extracts `focused_element`, `visible_text`, and `url` from the AX tree (see [S1 fields](#s1-fields) below).
4. `privacy.sensitive_window_regions()` — enumerate visible window metadata and locate denied windows when the screenshot privacy guard is enabled.
5. `screenshot.grab_many()` — unless `include_screenshot = false`; targets intersecting denied windows are omitted.
6. Write `{iso8601_safe}.json` to the buffer.

Privacy denylist checks can short-circuit this flow:

- `deny_app_names`, `deny_bundle_ids`, and `deny_window_title_patterns` run immediately after window metadata is available, before AX and screenshots.
- `deny_url_patterns` and `deny_text_patterns` run after S1 parsing, before screenshots and disk writes.
- Denied captures are not written to JSON, not inserted into `captures_fts`, not absorbed into timeline blocks, and not sent to any model stage.

With `screenshot_privacy_mode = "skip-monitor"`, the bundled `mac-window-list` helper uses CoreGraphics to inspect every on-screen window's owner, bundle ID, title, and bounds immediately before `mss` captures pixels. Because CoreGraphics can omit background browser titles, the helper falls back to top-level AX window title, position, and size metadata. This includes floating panels, but it never traverses background AX trees or reads their controls and contents. In `separate` mode, only monitors intersecting a denied window are skipped. In `all` mode, any denied window skips the full virtual-desktop screenshot. If Screen Recording permission is unavailable, or enumeration otherwise fails, `screenshot_privacy_fail_closed = true` suppresses that tick's screenshots while allowing the non-sensitive foreground AX/text record to be written.

This guard protects windows identifiable by app, bundle, or title metadata. It cannot classify sensitive content inside an otherwise allowed app, and there is a small unavoidable race if a window appears between enumeration and pixel capture. For high-risk workflows, keep password managers in the app/bundle denylist and pause capture before displaying secrets.

## Privacy protection indicators

When `privacy_indicator_style` is not `off`, the local `mac-privacy-overlay`
helper displays the protection state after the protection decision is confirmed.
The selectable styles are `off`, `border`, `shield`, `pill`, `quiet-shield`, and
`banner`.

- Green means that display has been excluded by the same-generation protection
  decision used for capture.
- Gray means capture is paused.
- Yellow means detection failed and screenshot capture is fail-closed.
- No indicator is not proof of protection: if the overlay helper itself fails,
  it cannot display the yellow failure state and screenshot capture remains
  stopped until the helper is confirmed again.

Protection detection locally inspects top-level visible-window metadata: owner
or app name, bundle identifier, window title, position, size, and active state;
on-screen/minimized state is handled where the platform exposes it. It does not
traverse background window controls or contents. The detection inventory is used
only for the protection decision and is not copied into capture JSON, FTS,
timeline, memory, or model requests.

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
