# Unknown-Title Browser Protection Scope

**Date:** 2026-08-26
**Status:** Approved design

## Problem

OpenChronicle currently treats every visible window with an unavailable title as
sensitive whenever any `deny_window_title_patterns` rule is configured. The
policy is intentionally fail-closed, but its scope is too broad: a transiently
untitled Feishu Meeting window is protected even though the configured title
rules target private-browser windows.

Unknown-title protection is a property of the risk domain, not of every
application. It should apply only to explicitly selected browser bundles.

## Goals

- Restrict `window_title_unknown` protection to an editable exact Bundle ID
  list.
- Default that list to the browsers installed and approved on this Mac:
  Microsoft Edge, Google Chrome, and Firefox.
- Preserve all protection for reliable title matches and direct app or bundle
  denylist matches.
- Preserve fail-closed handling for helper, inventory, display-mapping,
  diagnostics-guard, and presentation failures.
- Expose the list in the existing privacy-rule editor without exposing it in
  ordinary configuration snapshots.
- Install the completed change and verify the live application and backend.

## Non-goals

- Detect private browsing through browser-specific APIs or extensions.
- Infer that an arbitrary application is a browser.
- Change window-to-display mapping, screenshot modes, AX blocking, smoothing,
  diagnostics reason codes, or foreground URL/text matching.
- Add app-name fallback matching for unknown titles. Bundle IDs are the stable
  identity boundary for this policy.

## Configuration

Add the capture setting:

```toml
protect_unknown_title_bundle_ids = [
  "com.microsoft.edgemac",
  "com.google.Chrome",
  "org.mozilla.firefox",
]
```

The Python default is the same three-value list. An existing config that omits
the setting therefore receives the new effective default without a migration.
An explicitly empty list disables per-window unknown-title protection. Matching
is exact and case-insensitive, consistent with existing bundle denylist
matching. Blank entries are never matches and the native editor rejects them.

The field joins the privacy-specific snapshot and mutation path. Normal config
JSON continues to expose only its count, not its values. Privacy edits retain
the existing SHA conflict check, validation, backup, atomic replacement, and
targeted TOML-array rewrite behavior.

## Matching Semantics

For each visible normal window:

1. Direct `deny_app_names` and `deny_bundle_ids` matches are evaluated exactly
   as today.
2. If a reliable CoreGraphics or identity-authorized AX title exists, both the
   primary and alternate title continue to be matched against every
   `deny_window_title_patterns` expression. A real match produces
   `window_title_rule` for any application, including an application outside
   the new list.
3. If the title is unavailable, produce `window_title_unknown` only when:
   - at least one non-empty title pattern is configured; and
   - the window Bundle ID exactly matches
     `protect_unknown_title_bundle_ids`, case-insensitively.
4. A missing, blank, or unlisted Bundle ID does not produce
   `window_title_unknown`. It can still be protected by a direct app/bundle
   rule or another existing failure boundary.

Only the third branch changes. Protection snapshot construction, display
attribution, `separate` monitor behavior, screenshot skipping, AX suppression,
reason ordering, and the 800 ms title-uncertainty presentation smoothing remain
unchanged.

## Native Settings UI

Extend **Privacy Denylists** with a sixth `PrivacyRuleList`:

- Title: `Unknown-title Protected Bundle IDs`
- Detail: `Exact Bundle IDs whose windows stay protected when a reliable title cannot be read.`
- Placeholder: `com.microsoft.edgemac`

The concealed view shows only `Unknown-title protected bundles: N`. The list is
loaded only after the user opens the privacy editor, follows the same blank-row
validation as the other exact lists, participates in unsaved-change detection,
and is saved through the existing privacy mutation flow.

## Error and Security Boundaries

- A complete helper or inventory failure remains `failed` and follows the
  configured/global fail-closed policy.
- An invalid diagnostics guard, pause-state failure, invalid presentation, or
  unmapped active/sensitive window keeps its existing behavior.
- Exact reason values remain gated by the diagnostics lease protocol.
- No title, rule, or window inventory is added to logs, capture JSON, normal
  config snapshots, or downstream model inputs by this feature.
- Removing a browser from the list is an explicit privacy reduction limited to
  unknown titles; reliable title and direct denylist matches still apply.

## Testing

Python tests must prove:

- unknown-title Edge, Chrome, and Firefox windows are protected by default;
- an unknown-title Feishu Meeting window is not protected;
- Bundle ID matching is case-insensitive;
- an explicit empty protection list disables only the unknown-title branch;
- reliable title matches remain global;
- direct app and bundle matches remain global even when the title is unknown;
- privacy snapshots/counts, validation, targeted mutation, and config defaults
  include the new field without leaking values into normal snapshots;
- display mapping, screenshot/AX decisions, and failure behavior do not regress.

Swift tests must cover decoding, draft editing, validation, change generation,
count presentation, and the new privacy-list editor binding.

Run the full Python and Swift suites, build the signed application, install it,
restart into exactly one app-owned backend chain, and verify the live config and
category diagnostics. Live window-title availability is timing-dependent, so
deterministic policy proof comes from automated inventory fixtures; the live
check verifies installation, process ownership, config propagation, and absence
of residual diagnostic guards.

## Delivery

The completed delivery includes source, tests, documentation, generated/default
configuration, the explicit effective three-browser list in the installed
configuration path, an updated installed app/backend, and a concise report of
live verification and any timing-dependent limitation.
