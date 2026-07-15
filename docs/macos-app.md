# macOS menu bar app

`OpenChronicle.app` is the native lifecycle and privacy-permission host for the
existing OpenChronicle backend. It runs as a menu bar app, starts the Python
backend as a foreground child process, and stops that child when the app quits.
The backend therefore stays in the app's responsibility chain instead of
double-forking away from the terminal.

## What the app provides

- Accessibility, Screen Recording, and Input Monitoring onboarding and live status
- start, stop, pause, resume, and safe takeover of an already-running CLI daemon
- launch at login through `SMAppService.mainApp`
- current PID, ownership, last-capture time, logs, and data-folder shortcuts
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
3. Input Monitoring — interaction timing for click and text-input events

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
