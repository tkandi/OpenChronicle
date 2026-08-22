# InPrivate Navigation Protection Design

## Goal

Keep a browser private window protected after navigation changes the CoreGraphics window title,
without restoring the old duplicate-window or geometry-only AX fallback behavior.

## Observed Failure

Edge exposes `Google` through `kCGWindowName`, while the safely matched top-level AX window exposes
`Google - Microsoft Edge (InPrivate)`. The current helper reads the AX title only when the CG title
is empty, so the `InPrivate` denylist rule disappears as soon as a page supplies a non-empty CG
title.

## Design

For each layer-zero CG window, retain the existing unique `(ownerPID, windowID)` match to a single
AX window. When that identity match is valid, read the top-level AX title even if the CG title is
non-empty.

- If the CG title is empty, preserve current behavior: use the AX title as the primary title.
- If the CG title is non-empty and the AX title is distinct, retain it as `alternate_title`.
- If the identity is missing, duplicated, cross-PID, cross-Space, or otherwise ambiguous, do not
  read or attach an AX title.
- Never traverse AX window contents; only read `kAXTitleAttribute` from the top-level matched window.

The Python `VisibleWindow` model carries the optional alternate title. Window-title denylist rules
match the primary and alternate titles in stable order and de-duplicate rules. Exact diagnostics
show the title that actually matched.

## Safety And Performance

- CG geometry remains authoritative for display mapping and screenshot exclusion.
- PID plus globally unique Window ID remains authoritative for AX metadata attachment.
- Ambiguity never authorizes an AX title read.
- The extra work is one bounded top-level AX title read per safely matched visible layer-zero window
  per inventory refresh.
- App, bundle, pause, diagnostics guard, and fail-closed behavior remain unchanged.

## Verification

- Swift core test: CG title `Google`, AX title containing `InPrivate`, unique identity produces an
  alternate title and reads AX exactly once.
- Swift negative tests: duplicate/missing/cross-PID identities never read AX titles.
- Python parser/matcher tests: either title can trigger a rule, duplicate rule hits collapse, and the
  matched exact title is retained.
- Full Python, Swift, helper protocol, arm64/x86_64, wheel, and signed App verification.
- Live Edge test: blank InPrivate -> Google -> another app foreground -> close window. Protection
  must persist until close, then clear.
