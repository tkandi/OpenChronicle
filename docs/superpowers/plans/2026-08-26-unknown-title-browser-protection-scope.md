# Unknown-Title Browser Protection Scope Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Protect an unavailable window title only for configured browser Bundle IDs, defaulting to Edge, Chrome, and Firefox, while preserving every existing direct match and fail-closed boundary.

**Architecture:** Add one keyword-only capture configuration list and use it only in the `WINDOW_TITLE_UNKNOWN` branch of visible-window matching. Carry that private list through the existing privacy-only CLI snapshot/mutation path and Swift privacy editor, leaving reliable title matching, snapshot construction, diagnostics, display mapping, screenshot/AX enforcement, and failure policy unchanged.

**Tech Stack:** Python 3.12, pytest, TOML config editor, Swift 5/SwiftUI, XCTest, owner-only Unix diagnostics, macOS signed app installer.

## Global Constraints

- Default exact Bundle IDs are `com.microsoft.edgemac`, `com.google.Chrome`, and `org.mozilla.firefox`.
- Bundle matching is exact and case-insensitive; app-name fallback is not permitted.
- An explicit empty list disables only `window_title_unknown` protection.
- Reliable title matches and direct app/bundle matches remain global.
- Helper, inventory, display-mapping, diagnostics, pause, and presentation failures keep their current fail-closed behavior.
- Normal config JSON exposes only a count; values remain available only through the explicit privacy snapshot.
- No window title, rule, or inventory value is added to logs, captures, or model inputs.
- The installed product and live backend must be verified, not only source tests.

---

### Task 1: Python Configuration and Matching Policy

**Files:**
- Modify: `src/openchronicle/config.py:47-128`
- Modify: `src/openchronicle/config.py:329-345`
- Modify: `src/openchronicle/capture/privacy.py:226-268`
- Modify: `tests/test_config.py:1-170`
- Modify: `tests/test_capture_privacy.py:1-210`
- Modify: `tests/test_protection.py` title-unavailable fixtures that intentionally expect protection

**Interfaces:**
- Produces: `CaptureConfig.protect_unknown_title_bundle_ids: list[str]`, keyword-only.
- Produces: `DEFAULT_PROTECT_UNKNOWN_TITLE_BUNDLE_IDS: tuple[str, ...]`.
- Consumes: existing `_exact_matching_rules(value, patterns)` and `VisibleWindow.bundle_id`.
- Preserves: `visible_window_rule_matches(cfg, window) -> tuple[VisibleWindowRuleMatch, ...]`.

- [ ] **Step 1: Write failing configuration tests**

Add to `tests/test_config.py`:

```python
def test_unknown_title_protection_bundle_defaults_and_override(tmp_path: Path) -> None:
    missing = config.load(tmp_path / "missing.toml").capture
    assert missing.protect_unknown_title_bundle_ids == [
        "com.microsoft.edgemac",
        "com.google.Chrome",
        "org.mozilla.firefox",
    ]

    path = tmp_path / "config.toml"
    path.write_text(
        '[capture]\nprotect_unknown_title_bundle_ids = ["com.example.browser"]\n'
    )
    assert config.load(path).capture.protect_unknown_title_bundle_ids == [
        "com.example.browser"
    ]

    path.write_text('[capture]\nprotect_unknown_title_bundle_ids = []\n')
    assert config.load(path).capture.protect_unknown_title_bundle_ids == []
```

Extend `test_capture_config_preserves_pre_indicator_placement_positional_signature` to assert the new field has the three defaults without adding a positional argument.

- [ ] **Step 2: Write failing matching tests**

Replace the generic unknown-title expectation in `tests/test_capture_privacy.py` with explicit policy cases:

```python
@pytest.mark.parametrize(
    "bundle",
    ["com.microsoft.edgemac", "com.google.Chrome", "org.mozilla.firefox"],
)
def test_unknown_browser_title_is_protected_by_default(bundle: str) -> None:
    cfg = CaptureConfig(deny_window_title_patterns=["InPrivate"])
    matches = privacy.visible_window_rule_matches(
        cfg,
        _window(app="Browser", bundle=bundle, title="", title_available=False),
    )
    assert [match.kind.value for match in matches] == ["window_title_unknown"]


def test_unknown_feishu_title_is_not_protected() -> None:
    cfg = CaptureConfig(deny_window_title_patterns=["InPrivate"])
    matches = privacy.visible_window_rule_matches(
        cfg,
        _window(
            app="飞书会议",
            bundle="com.electron.lark.iron",
            title="",
            title_available=False,
        ),
    )
    assert matches == ()


def test_unknown_title_bundle_scope_is_case_insensitive() -> None:
    cfg = CaptureConfig(
        deny_window_title_patterns=["InPrivate"],
        protect_unknown_title_bundle_ids=["COM.MICROSOFT.EDGEMAC"],
    )
    matches = privacy.visible_window_rule_matches(
        cfg,
        _window(bundle="com.microsoft.edgemac", title_available=False),
    )
    assert [match.kind.value for match in matches] == ["window_title_unknown"]


def test_empty_unknown_title_bundle_scope_disables_only_unknown_branch() -> None:
    cfg = CaptureConfig(
        deny_window_title_patterns=["InPrivate"],
        protect_unknown_title_bundle_ids=[],
    )
    assert privacy.visible_window_rule_matches(
        cfg,
        _window(bundle="com.microsoft.edgemac", title_available=False),
    ) == ()
    known = privacy.visible_window_rule_matches(
        cfg,
        _window(bundle="com.electron.lark.iron", title="New InPrivate Window"),
    )
    assert [match.kind.value for match in known] == ["window_title_rule"]
```

Add one case proving a direct `deny_bundle_ids=["com.electron.lark.iron"]` match still protects an unknown Feishu title.

- [ ] **Step 3: Run focused tests and verify RED**

Run:

```bash
PYTHONPATH=/Users/tkandi/Desktop/Codex/OpenChronicle/src uv run pytest -q \
  tests/test_config.py \
  tests/test_capture_privacy.py \
  tests/test_protection.py
```

Expected: failures report the missing `protect_unknown_title_bundle_ids` field and the old global unknown-title behavior.

- [ ] **Step 4: Implement the minimal configuration field**

In `src/openchronicle/config.py`, add:

```python
DEFAULT_PROTECT_UNKNOWN_TITLE_BUNDLE_IDS = (
    "com.microsoft.edgemac",
    "com.google.Chrome",
    "org.mozilla.firefox",
)
```

Add the field without changing the legacy positional constructor:

```python
protect_unknown_title_bundle_ids: list[str] = field(
    default_factory=lambda: list(DEFAULT_PROTECT_UNKNOWN_TITLE_BUNDLE_IDS),
    kw_only=True,
)
```

Normalize it in `__post_init__` with `_str_list`, and add the three-value TOML array to `DEFAULT_CONFIG` beside the other privacy fields.

- [ ] **Step 5: Implement the minimal matching condition**

Change only the unknown-title condition in `visible_window_rule_matches`:

```python
if (
    not window.title_available
    and any(cfg.deny_window_title_patterns)
    and exact_match(window.bundle_id, cfg.protect_unknown_title_bundle_ids)
):
```

Do not gate the reliable-title branch or direct app/bundle branches. Update existing tests whose purpose is title uncertainty to use an in-scope browser Bundle ID or an explicit protection list; keep unrelated fixtures unchanged.

- [ ] **Step 6: Run focused tests and verify GREEN**

Run the Task 1 command again. Expected: all selected tests pass.

- [ ] **Step 7: Commit the core policy**

```bash
git add src/openchronicle/config.py src/openchronicle/capture/privacy.py \
  tests/test_config.py tests/test_capture_privacy.py tests/test_protection.py
git commit -m "feat(capture): scope unknown titles to browser bundles"
```

---

### Task 2: Privacy-Safe CLI Configuration Editing

**Files:**
- Modify: `src/openchronicle/config_editor.py:31-68`
- Modify: `src/openchronicle/config_editor.py:286-299`
- Modify: `src/openchronicle/config_editor.py:430-503`
- Modify: `tests/test_cli_config_editor.py`
- Modify: `tests/test_privacy_reason_boundaries.py` if an exact privacy-field set is asserted

**Interfaces:**
- Consumes: `CaptureConfig.protect_unknown_title_bundle_ids` from Task 1.
- Produces: privacy field name `protect_unknown_title_bundle_ids` in private snapshots, counts, validation, and atomic patching.
- Preserves: ordinary `config --json` value secrecy and SHA-bound mutation semantics.

- [ ] **Step 1: Write failing CLI privacy tests**

Add a test that writes no new field and asserts:

```python
_, normal = _invoke_json(CliRunner(), ["config", "--json"])
assert normal["values"]["capture"]["privacy_counts"][
    "protect_unknown_title_bundle_ids"
] == 3
assert "com.microsoft.edgemac" not in json.dumps(normal)

_, private = _invoke_json(CliRunner(), ["config", "--privacy-json"])
assert private["values"]["protect_unknown_title_bundle_ids"] == [
    "com.microsoft.edgemac",
    "com.google.Chrome",
    "org.mozilla.firefox",
]
```

Add a SHA-bound patch test using:

```python
updates = {
    "capture.protect_unknown_title_bundle_ids": ["com.microsoft.edgemac"]
}
```

Assert the resulting TOML contains that exact array, preserves adjacent comments, and a blank/non-string value fails validation without changing the file.

- [ ] **Step 2: Run the focused CLI tests and verify RED**

Run:

```bash
PYTHONPATH=/Users/tkandi/Desktop/Codex/OpenChronicle/src uv run pytest -q \
  tests/test_cli_config_editor.py \
  tests/test_privacy_reason_boundaries.py
```

Expected: the new count, private value, and editable path are missing.

- [ ] **Step 3: Add the field to the privacy editor boundary**

Add `protect_unknown_title_bundle_ids` to `PRIVACY_FIELDS`. Extend the string-list validation loop to include it; regex compilation remains restricted to names ending in `_patterns`. Because `PRIVACY_PATHS`, normal counts, privacy values, and editable paths derive from `PRIVACY_FIELDS`, keep those comprehensions as the single source of truth.

- [ ] **Step 4: Run focused CLI tests and verify GREEN**

Run the Task 2 command again. Expected: all selected tests pass and normal JSON contains no Bundle ID values.

- [ ] **Step 5: Commit the CLI/config boundary**

```bash
git add src/openchronicle/config_editor.py tests/test_cli_config_editor.py \
  tests/test_privacy_reason_boundaries.py
git commit -m "feat(config): edit unknown-title browser scope"
```

---

### Task 3: Native macOS Privacy Editor

**Files:**
- Modify: `macos/OpenChronicleApp/Sources/OpenChronicleApp/Models/Configuration.swift:460-530`
- Modify: `macos/OpenChronicleApp/Sources/OpenChronicleApp/Views/SettingsView.swift:406-490`
- Modify: `macos/OpenChronicleApp/Tests/OpenChronicleAppTests/ConfigurationTests.swift`

**Interfaces:**
- Consumes: privacy JSON key `protect_unknown_title_bundle_ids` and count with the same key.
- Produces: `PrivacyConfigurationValues.protectUnknownTitleBundleIDs: [String]`.
- Produces: `PrivacyConfigurationDraft.protectUnknownTitleBundleIDs: [String]`.
- Produces: update path `capture.protect_unknown_title_bundle_ids`.

- [ ] **Step 1: Write failing Swift model tests**

Extend privacy snapshot JSON fixtures with:

```json
"protect_unknown_title_bundle_ids": [
  "com.microsoft.edgemac",
  "com.google.Chrome",
  "org.mozilla.firefox"
]
```

Assert the decoded draft contains those values. Mutate the draft to `["com.microsoft.edgemac"]` and assert:

```swift
XCTAssertEqual(
  updates["capture.protect_unknown_title_bundle_ids"] as? [String],
  ["com.microsoft.edgemac"]
)
```

Add a blank-entry case and require the validation error to name `Unknown-title protected Bundle IDs`.

- [ ] **Step 2: Run Swift configuration tests and verify RED**

Run:

```bash
swift test --package-path macos/OpenChronicleApp --filter ConfigurationTests
```

Expected: decoding/model assertions fail because the new property is absent.

- [ ] **Step 3: Extend Swift privacy models**

Add the property and coding key to `PrivacyConfigurationValues`; add the mutable property, initialization assignment, validation group, and update generation to `PrivacyConfigurationDraft`. Keep it non-optional because the Python private snapshot always supplies the effective default.

- [ ] **Step 4: Extend the SwiftUI editor**

In the revealed privacy editor, add:

```swift
PrivacyRuleList(
  title: "Unknown-title Protected Bundle IDs",
  detail: "Exact Bundle IDs whose windows stay protected when a reliable title cannot be read.",
  placeholder: "com.microsoft.edgemac",
  values: privacyBinding(\.protectUnknownTitleBundleIDs, fallback: [])
)
```

Add the concealed count row:

```swift
privacyCountRow(
  "Unknown-title protected bundles",
  field: "protect_unknown_title_bundle_ids"
)
```

Place the new list between general Bundle IDs and window-title patterns so its relationship to both is visible.

- [ ] **Step 5: Run Swift tests and build**

Run:

```bash
swift test --package-path macos/OpenChronicleApp --filter ConfigurationTests
swift test --package-path macos/OpenChronicleApp
bash scripts/build-macos-app.sh
```

Expected: all Swift tests pass and `dist/OpenChronicle.app.zip` builds and signs successfully.

- [ ] **Step 6: Commit the native editor**

```bash
git add macos/OpenChronicleApp/Sources/OpenChronicleApp/Models/Configuration.swift \
  macos/OpenChronicleApp/Sources/OpenChronicleApp/Views/SettingsView.swift \
  macos/OpenChronicleApp/Tests/OpenChronicleAppTests/ConfigurationTests.swift
git commit -m "feat(macos): edit unknown-title browser scope"
```

---

### Task 4: Documentation and Full Verification

**Files:**
- Modify: `docs/config.md:100-145`
- Modify: `docs/capture.md:55-74`
- Modify: `docs/macos-app.md:176-245`
- Modify: `README.md` only if it enumerates privacy fields

**Interfaces:**
- Consumes: final behavior and field name from Tasks 1-3.
- Produces: user-facing configuration, runtime semantics, UI explanation, and security limits.

- [ ] **Step 1: Update documentation**

Document the default three-value array, explicit-empty semantics, exact case-insensitive Bundle matching, and this decision table:

| Title state | Bundle in protection list | Direct app/bundle match | Result |
|---|---:|---:|---|
| reliable and title regex matches | either | either | protected |
| unavailable | yes | no | protected: `window_title_unknown` |
| unavailable | no | no | not protected |
| unavailable | either | yes | protected by direct rule |

State explicitly that whole-inventory/helper failures remain fail-closed and that this is not a browser private-mode API.

- [ ] **Step 2: Run the complete source gates**

Run:

```bash
PYTHONPATH=/Users/tkandi/Desktop/Codex/OpenChronicle/src uv run pytest -q
swift test --package-path macos/OpenChronicleApp
bash scripts/build-macos-app.sh
git diff --check
```

Expected: every command succeeds with no test failures or whitespace errors.

- [ ] **Step 3: Commit documentation and any test-only compatibility updates**

```bash
git add docs/config.md docs/capture.md docs/macos-app.md README.md
git commit -m "docs: explain unknown-title browser scope"
```

Omit `README.md` from `git add` when unchanged.

---

### Task 5: Install and Verify the Live Product

**Files:**
- Mutate through supported tools: `/Users/tkandi/.openchronicle/config.toml`
- Install: `/Users/tkandi/.openchronicle/venv/lib/python3.12/site-packages/openchronicle`
- Install: `/Applications/OpenChronicle.app`
- Verify: `/Users/tkandi/.openchronicle/runtime/privacy-diagnostics.sock`

**Interfaces:**
- Consumes: committed source, built app, config editor CLI.
- Produces: explicit current config list, signed installed app, one owned backend chain, category-safe live evidence.

- [ ] **Step 1: Record pre-install state without values**

Record the config SHA-256, running App/backend/watcher PIDs, installed app version/signature, and source/install hashes for changed Python files. Do not print the rest of the privacy configuration.

- [ ] **Step 2: Stop the installed chain cleanly and install both layers**

Quit `/Applications/OpenChronicle.app`, wait until its backend and watcher exit, then run:

```bash
bash install.sh --no-client-config
bash scripts/install-macos-app.sh
```

Expected: the backend package and signed app install successfully and the app relaunches.

- [ ] **Step 3: Persist the explicit browser list atomically**

Use the newly installed CLI and its SHA-bound patch workflow:

```bash
oc_cli=/Users/tkandi/.openchronicle/venv/bin/openchronicle
oc_snapshot="$($oc_cli config --json)"
oc_expected_sha="$(printf '%s' "$oc_snapshot" | /usr/bin/python3 -c \
  'import json,sys; print(json.load(sys.stdin)["sha256"])')"
printf '%s\n' "$(printf \
  '{\"expected_sha256\":\"%s\",\"updates\":{\"capture.protect_unknown_title_bundle_ids\":[\"com.microsoft.edgemac\",\"com.google.Chrome\",\"org.mozilla.firefox\"]}}' \
  "$oc_expected_sha")" | "$oc_cli" config --patch-json
```

Expected: the CLI validates the list, writes a backup, atomically patches only
the new array, and returns `ok=true`. Restart through the native app after the
patch.

- [ ] **Step 4: Verify installed source and process ownership**

Require:

- source and installed hashes match for `config.py`, `config_editor.py`, and `capture/privacy.py`;
- `codesign --verify --deep --strict /Applications/OpenChronicle.app` succeeds;
- exactly one `OpenChronicle.app -> openchronicle start --foreground -> mac-ax-watcher` chain exists;
- the normal config snapshot reports unknown-title bundle count `3` without values;
- the explicit privacy snapshot returns exactly the approved three Bundle IDs;
- no `privacy-reveal.guard` remains after diagnostics.

- [ ] **Step 5: Verify deterministic installed policy and live category state**

With the installed Python package, construct synthetic `VisibleWindow` fixtures and assert Edge/Chrome/Firefox unknown titles produce `window_title_unknown`, while Feishu does not; assert a reliable Feishu title matching `InPrivate` still produces `window_title_rule`.

Subscribe to the live diagnostics socket at `category` detail only and record generation, state, display IDs, blocked booleans, and reason codes. Do not print titles, apps, bundles, or rules. If the desktop is currently safe, require `diagnostics_guard_active=false`; report live title availability as timing-dependent rather than claiming an exhaustive app list.

- [ ] **Step 6: Run final repository acceptance**

Run:

```bash
git status --short
git log -6 --oneline --decorate
```

Expected: no uncommitted implementation changes remain, all feature commits are visible, and `develop` contains the design, plan, source, tests, and docs.
