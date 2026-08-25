# macOS menu bar app

`OpenChronicle.app` is the native lifecycle and privacy-permission host for the
existing OpenChronicle backend. It runs as a menu bar app, starts the Python
backend as a foreground child process, and stops that child when the app quits.
The backend therefore stays in the app's responsibility chain instead of
double-forking away from the terminal.

## What the app provides

- Accessibility, Screen Recording, and optional Input Monitoring onboarding and live status
- start, stop, timed or indefinite privacy pauses, resume, and safe takeover of
  an already-running CLI daemon
- launch at login through `SMAppService.mainApp`
- current PID, ownership, health, uptime, last-capture app, storage/memory
  statistics, model diagnostics, logs, and data-folder shortcuts
- native macOS alerts when a background model call ultimately fails, with a
  15-minute per-stage cooldown and a test button under Runtime & Storage
- validated configuration editing with automatic backups and backend restart
- one unified sidebar window for controls, permissions, status, and settings
- a stable bundle identifier: `com.openchronicle.desktop`

The app reuses `~/.openchronicle/` in place. It does not migrate, rewrite, or
duplicate the capture buffer, SQLite index, memory files, logs, or config.

## Build and install

The current release expects the Python backend to have been installed once:

```bash
bash install.sh
bash scripts/install-macos-app.sh
```

The second command builds `dist/OpenChronicle.app.zip`, extracts it to
`/Applications/OpenChronicle.app`, verifies its signature, and opens it.
The signing stage runs in a temporary non-FileProvider directory so Desktop or
iCloud metadata cannot inject `FinderInfo` into the bundle while `codesign` is
working. The verified bundle is archived before entering `dist/`, then extracted
without resource forks or extended attributes during installation.

For a build artifact without installation:

```bash
bash scripts/build-macos-app.sh
```

The app locates the backend in this order:

1. `OPENCHRONICLE_BIN`
2. a future app-bundled `Contents/Resources/backend/bin/openchronicle`
3. `~/.local/bin/openchronicle`
4. `~/.openchronicle/venv/bin/openchronicle`

## First launch

Grant these permissions to **OpenChronicle** in System Settings:

1. Accessibility — AX Tree capture and AX event observation
2. Screen Recording — screenshots and visible-window privacy checks
3. Input Monitoring (optional) — more precise interaction timing for click and
   text-input events

OpenChronicle does not store raw key presses. The watcher uses key events only
as a debounce signal, then reads the final focused-element value through AX;
secure fields are redacted in the native helper.

If a daemon started from Terminal is already running, the app leaves it alone
and displays **Started outside the app**. Use **Take Over in App** to send that
daemon `SIGTERM`, wait for its normal shutdown, and restart it as the app's
foreground child. Existing data and active configuration are retained.

After takeover, quitting OpenChronicle also stops its managed backend. This is
intentional: leaving the child orphaned would defeat the app-owned permission
and lifecycle model.

## Privacy pause reminders

The menu bar offers three explicit pause choices: **30 Minutes**, **1 Hour**,
and **Until I Resume**. While paused, the menu-bar status shows the remaining
time and provides **Resume Capture** plus a duration-change menu.

One minute before a timed pause can end, the app posts a native notification
with **Resume Now**, **Extend 30 Minutes**, and **Keep Paused** actions. Automatic
resume is fail-closed: the backend requires both a successfully submitted
warning and a recent app heartbeat. If notification permission is denied, the
app is sleeping, or the app stops responding, capture remains paused. Waking
after a missed deadline produces a fresh one-minute warning instead of silently
resuming.

An indefinite pause sends its first reminder after one hour and repeats every
two hours. Relaunching the app or waking the Mac immediately delivers any
overdue reminder. Existing timestamp-only `.paused` files are treated as
indefinite pauses for backward-compatible privacy.

## Detailed status

The unified window's **Runtime & Storage** page loads local runtime and storage
details through the same status collector used by the CLI. Normal refreshes
skip model calls:

```bash
openchronicle status --json --no-model-checks
```

This reports version, root, PID, uptime, health, last capture and application,
buffer size, session counts, memory counts, timeline progress, and configured
model names. While a control page is visible, it refreshes once per minute; it
also refreshes after lifecycle state changes or when **Refresh Status** is
clicked.

Model availability is checked only after clicking **Run Model Diagnostics**.
Each distinct model configuration receives one small real request, matching the
deduplicated checks performed by the regular `openchronicle status` command.

## Model failure notifications

The Python backend appends a compact event to
`~/.openchronicle/events/model-failures.jsonl` only when a normal background
LiteLLM request raises after provider retries are exhausted. The event contains
the stage, configured model, exception class, and a short sanitized first line;
prompts, responses, and API keys are never included. `OpenChronicle.app` reads
new events and posts native notifications. Matching failures are limited to one
notification per stage and model every 15 minutes.

After macOS accepts a notification request, the app stores only its event ID
and delivery timestamp in the app's local preferences. This powers the **Last
alert** status and provides a diagnostic acknowledgement without retaining any
additional error text.

Notifications are enabled by default and require the standard macOS notification
permission. They can be disabled or tested from **Runtime & Storage → Model
Failure Notifications**. Manual **Run Model Diagnostics** failures remain in the
diagnostics UI and do not create background failure alerts.

## Configuration settings

Choose **Open OpenChronicle…** from the menu bar to open the unified window. It
returns to the last selected page; the first open defaults to **Overview**, and
permission onboarding opens **Permissions** directly. Sidebar navigation keeps
configuration drafts alive while moving between these pages:

- default and per-stage model names, provider base URL, and API-key environment
  variable name
- capture timing, screenshot mode, privacy behavior, retention, and quality
- privacy denylists for app names, bundle IDs, window titles, URLs, and text
- timeline/session processing, reducer/classifier cadence, memory, and search
- embedded MCP transport, host, port, and automatic startup

The common form never receives, displays, or writes the value of a direct
`api_key`. If the TOML already contains one, the configuration service returns
only a boolean warning; environment variables remain the recommended secret
mechanism. The Advanced editor is hidden behind an explicit button because it
displays the complete file and may therefore reveal a direct key during
screenshots or screen sharing.

The Capture page loads only denylist counts during its normal configuration
refresh. Choose **Manage Privacy Denylists…** to make a separate, explicit local
request for the actual rule values. App names and bundle IDs are exact,
case-insensitive matches; window-title, URL, and text rules use Python regular
expressions with case-insensitive matching. Empty rules are rejected in the UI,
and Python validates every expression before any file is written.

Every save is strictly validated, checks that the file has not changed since it
was loaded, creates a timestamped `config.toml.backup-*`, and atomically replaces
the original. Unknown keys and comments survive common-form updates. **Save**
leaves the running backend unchanged; **Apply & Restart** restarts an app-managed
backend so the new settings take effect. An externally started backend must be
taken over before the app can restart it.

Changing a denylist replaces only that TOML array. Comments before and after
the field remain intact, while comments embedded inside the changed array are
replaced along with the array. Unchanged denylist fields retain their original
formatting.

## Protection Diagnostics

The **Diagnostics** sidebar item opens **Protection Diagnostics**. It shows one
row per display with protection state, screenshot and AX blocking, primary
reason, additional-reason count, generation age, and indicator confirmation.
Selecting a display shows its bounded reason list.

The Capture page controls where reasons appear (`overlay`, `diagnostics`, or
`hybrid`), their detail (`category`, `exact`, or `tiered`), and overlay reveal
(`always`, `hover`, or `click`). Defaults are `hybrid`, `exact`, and `hover`.
Exact mode automatically requests a protected-display lease when the page is
visible. Tiered mode starts with categories and exposes **Show Exact Values** /
**Hide Exact Values** explicitly. Category mode never requests exact fields.

Exact fields remain concealed until the diagnostics window's display is
protected by a newer confirmed generation. Moving the window first conceals
exact text, protects both old and new displays, confirms the destination, and
only then releases the old display and reveals again. Leaving the page or
choosing hide releases normally. A stale release cannot clear a newer lease.

`hover` observes pointer movement without making the overlay consume mouse
events, so hover and clicks continue to reach the underlying application.
`click` enables only the small reason hit target; the rest of the overlay stays
click-through. Overlay panels remain non-activating and never take keyboard
focus. With indicator style `off` or reason location `diagnostics`, the saved
reveal trigger has no overlay effect.

On diagnostics socket or overlay disconnection, the app synchronously discards
exact-bearing snapshots and publishes only category-safe data while reconnecting.
The daemon retains the non-sensitive guard and its screenshot/AX protection
until a valid release or confirmed process death. The diagnostics service is an
owner-only local Unix socket and is not an MCP or network diagnostics surface.

## Privacy protection indicators

The Capture settings page can select `off`, `border`, `shield`, `pill`,
`quiet-shield`, or `banner` for `capture.privacy_indicator_style`. Green means a
display is excluded by the same protection-decision generation used by capture;
gray means capture is paused; yellow means detection failed and the capture tick
is fail-closed. The overlay helper is a separate process, so no visible
indicator is not a protection confirmation: a failed helper cannot render the
yellow state, and capture stays stopped until a later helper confirmation.

On the first protected inventory frame, OpenChronicle immediately blocks
screenshots and AX capture and renders a transient `quiet-shield` for 800ms.
If protection persists, a newly acknowledged generation promotes to the
configured sustained style. The 800ms promotion and 200ms safe-confirmation
interval are fixed internal constants; they do not add a configuration field or
settings control. Mission Control, F3, Space gestures, transition thumbnails,
and animations never bypass protection when a privacy window is reported
on-screen.

The transient/sustained sequence applies only to a normal raw `protected`
decision. `active_window_unmapped` and `sensitive_window_unmapped` are global
raw `failed` decisions: they immediately keep screenshots and AX fail-closed,
use the existing failure presentation, skip the transient `quiet-shield`, and
do not execute normal 800ms promotion while failed. A Mission Control sequence
that remains unmapped therefore confirms fail-closed handling, not normal
protected smoothing.

After the first safe inventory, the app remains in clear-pending: it retains
the effective protected decision and continues blocking screenshots and AX. It
clears only after a second safe inventory at least 200ms later; renewed
protection cancels the pending clear. `paused` and `failed` immediately use
their existing paused/fail-closed presentations rather than ordinary protected
smoothing. With indicator style `off`, no overlay is drawn, while protected and
clear-pending still block capture.

Transient suppression hides reasons only in the overlay presentation. It does
not remove reasons from the effective snapshot, diagnostics, capture policy,
or filtering authorization. A sustained generation restores the configured
overlay reason behavior, including when its configured style remains
`quiet-shield`.

### Filtered screenshots

`capture.screenshot_privacy_mode` offers four capture policies. `off` keeps the
ordinary display capture path and disables the background window monitor and
indicator. `skip-monitor` uses the display-level fallback and omits a display
that intersects a protected window. `mask-window` and `exclude-window` require
macOS 14 or later: their ScreenCaptureKit helper source-excludes protected
windows and every confirmed OpenChronicle indicator or input-panel window by
resolving them to unique owning applications and excluding each complete app.
This intentionally removes all normal and auxiliary windows from those apps.
`mask-window` then paints the protected window
bounds gray; `exclude-window` leaves the pixels behind those excluded windows
visible.

The indicator executable runs from a generated
`~/.openchronicle/runtime/helpers/OpenChroniclePrivacyOverlay.app` helper
bundle. Its stable bundle identity
allows ScreenCaptureKit to resolve and exclude the complete indicator
application; an unresolved owner forces `skip-monitor` fallback.

Filtered capture is authorized only when the window inventory, protected window
IDs and regions, and indicator acknowledgement are complete and current. An
unavailable helper, unsupported macOS version, missing or duplicate IDs, or a
changed protection decision discards the filtered frame and uses a fresh
`skip-monitor` decision instead. `mask-window` and `exclude-window` never
fall back to an unfiltered screenshot; they remain screenshot-fail-closed when
the fallback cannot safely omit the protected display. See
[Capture](capture.md#screenshot-privacy-modes) for the complete fallback and
multi-display behavior.

A non-`off` indicator that is not confirmed stops before `mss`, including an
inactive state whose clear acknowledgement failed. Any fallback frame is
discarded if indicator confirmation or authorization changes during `mss`.

The helper fingerprints the requested shareable displays and on-screen windows
owned by the protected or overlay applications before capture, then reloads
that scoped inventory after all display captures but before PNG encoding or
stdout. Any included ID, owner, finite-frame, or title change rejects every
frame. This also excludes newly created windows from an app already classified
as protected. Python forces a fresh post-helper protection decision, which
drops persistent privacy changes from other applications; a different app's
privacy window that appears and disappears entirely between the OS and Python
snapshots remains a residual race.

The decision inventory is limited to alpha-positive, positive-size, normal
layer-0 windows returned by CoreGraphics as on-screen. It locally inspects owner
or app name, bundle identifier, title, CoreGraphics position and size, and
AX-derived active state. AX supplies only a missing title, and only after a
globally unique exact same-PID `CGWindowID` match; AX geometry never authorizes
fallback. A title that remains unavailable is emitted as an unknown-title record
instead of failing unrelated windows; configured title rules conservatively
protect only its intersecting displays. If exact focused-window identity is
unavailable, frontmost-PID layer-0 windows become active candidates, and AX is
blocked only when a candidate display is protected. Menus,
popovers, and non-layer-0 floating panels are not independently protected as
full-display windows by title, although an app or bundle denylist can still
protect the capture when it independently matches an inventoried normal window
or the foreground window.

This detection inventory is not copied into capture JSON, FTS, timeline,
memory, or model requests. In `separate` and `primary` modes, a safe-display
capture JSON may still be written. When the gate protects the active display,
the protected window's content and derived AX/S1 fields are suppressed; a
foreground denylist match can instead skip the entire capture before it is
written or processed.

With `screenshot_privacy_mode = "off"`, the daemon does not start the background
inventory monitor or indicator, but foreground app, bundle, title, URL, and text
rules still skip matching captures. With `screenshot_privacy_fail_closed =
false`, only legacy `skip-monitor` and `off` may allow an unprotected capture
after an ordinary inventory failure. `mask-window` and `exclude-window` remain
fail-closed regardless of that setting; the default `true` policy aborts the
complete tick and shows yellow when the helper can render it.

`screenshot_privacy_fail_closed = false` applies only to window/display inventory
failures. If the pause state cannot be read, OpenChronicle shows the yellow failed
indicator and aborts the complete capture regardless of this setting.

Before relying on the setting in daily use, perform this manual acceptance on
empty privacy windows after an explicit reinstall: verify `separate` and `all`
across two displays; move a privacy window between displays; pause and resume;
terminate and recover the overlay helper; quit the menu-bar app while the
backend continues; and switch every style plus `off` without restarting the
daemon. Confirm capture logs include only generation, state, style, and display
IDs, never private window titles. This checklist is manual and remains pending
until it is performed by the controller.

The same local interface is available for diagnostics and automation:

```bash
openchronicle config --json
openchronicle config --privacy-json   # explicitly includes private rule values
openchronicle config --validate-json  # JSON request on stdin
openchronicle config --patch-json     # JSON request on stdin
openchronicle config --write-json     # JSON request on stdin
```

Treat `--privacy-json` output as sensitive: it omits API keys, but its rule
values can identify private applications, sites, and text.

## Signing and permission stability

The build script uses an ad-hoc signature only when it cannot find a stable
identity. An ad-hoc identity contains the current binary hash, so an existing
Accessibility/Input Monitoring row can remain visibly enabled while no longer
matching the rebuilt app.

The preferred identity is an Apple Development or Developer ID certificate.
Create one through Xcode's Accounts settings, then pass its name explicitly:

```bash
CODE_SIGN_IDENTITY="Apple Development: Your Name (TEAMID)" \
  bash scripts/install-macos-app.sh
```

If no Apple signing identity is available, a personal, local-only installation
can create a dedicated identity in the login keychain once:

```bash
bash scripts/create-local-signing-identity.sh
bash scripts/install-macos-app.sh
```

This creates a local certificate chain anchored by a self-signed root that is
trusted only for code signing in the current user's trust settings. The root
private key is discarded; the leaf identity's private key is non-exportable and
available to `/usr/bin/codesign`. The build script automatically selects the
exact leaf identity named `OpenChronicle Local Development`; it does not select
unrelated identities. This is a persistent Keychain/trust change, so review the
script and use it only on a personal development Mac.

For distribution, use a Developer ID Application identity and notarize the
result. The app is intentionally not sandboxed: its primary purpose is to read
AX context from other applications and maintain a local backend and MCP endpoint.

After changing from an ad-hoc to a stable identity, remove each old
OpenChronicle entry from Accessibility, Screen Recording, and Input Monitoring,
add `/Applications/OpenChronicle.app` again, and restart the app. This one-time
rebind is required because the old TCC records contain the previous binary hash.

## Development tests

```bash
swift test --package-path macos/OpenChronicleApp
```

The Swift tests use temporary directories and never touch the live
`~/.openchronicle` data.
