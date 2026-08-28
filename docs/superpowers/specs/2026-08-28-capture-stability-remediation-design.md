# Capture Stability Remediation Design

## Goal

Restore the pre-remediation capture continuity without weakening the privacy fixes that protect paused capture, reliable non-layer privacy panels, or unknown-title browser content windows.

## Confirmed regressions

1. `mac-window-list` now emits every CoreGraphics layer. An untitled non-zero-layer browser helper window is therefore treated as `window_title_unknown`, even when the normal browser content window has a safe title.
2. The scheduler asks for a complete protection decision before reading foreground metadata. A protection refresh during that metadata read can invalidate the entire capture; the parent commit instead made its complete decision after metadata and preserved a protected metadata-only record.
3. macOS can report zero displays through `CGGetActiveDisplayList` while `NSScreen.screens` still exposes the active desktop. The empty active list currently becomes an `empty_displays` fail-closed outage.

## Design

### Unknown-title scope

Continue enumerating non-zero-layer windows so direct app, bundle, and reliable title rules still protect privacy panels. Restrict the uncertainty-only `window_title_unknown` branch to `layer == 0`. This preserves browser unknown-title protection for content windows while ignoring untitled menus, tooltips, and auxiliary panels.

### Capture ordering

Add a pause-only metadata preflight to `PrivacyProtectionMonitor`. A terminal preflight publishes a decision built from that exact pause observation without rereading pause state, reusing a safe generation, or reading inventory. The scheduler runs it before any foreground metadata read and stops on either a pause or a pause-read failure. It then reads foreground metadata and asks for the complete protection decision before AX or screenshot I/O. A later protection change during AX remains terminal because already-read AX data may be sensitive.

### Display selection

Keep `CGGetActiveDisplayList` as the preferred source and preserve every nonempty result unchanged so downstream structural validation can reject zero or duplicate IDs. When it returns an empty list or fails, lazily use positive, unique `NSScreenNumber` display IDs from `NSScreen.screens`; continue deriving bounds and primary state through CoreGraphics. If both sources are empty, preserve the existing fail-closed `empty_displays` behavior.

## Safety invariants

- Paused capture and pause-read failures perform no foreground metadata, AX, or screenshot reads.
- Layer-zero approved browsers with an unavailable title remain protected.
- Non-zero-layer direct app, bundle, and reliable title matches remain protected.
- Protection appearing during AX still discards the in-memory capture.
- Invalid or genuinely empty display inventories still fail closed.
- Existing user configuration is neither rewritten nor printed during deployment.

## Verification

- Python regression tests cover auxiliary-layer false positives, pause-only preflight, metadata-transition ordering, and late AX invalidation.
- Native Swift tests cover active-display preference, AppKit fallback, filtering, and empty-source behavior.
- Full Python, SwiftPM, and native helper suites pass.
- The installed app passes strict deep code-sign verification, installed/source hashes match, configuration hash is unchanged, status is `active/healthy`, and one app-owned process chain exists.
