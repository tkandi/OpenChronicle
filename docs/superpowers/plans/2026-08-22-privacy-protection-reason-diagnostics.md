# 隐私保护原因与诊断实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为每块显示器生成与实际截图/AX gate 一致的保护原因，并通过可配置的浮层交互和应用诊断页安全地显示固定类别或具体值。

**Architecture:** daemon 在不可变 `ProtectionSnapshot` 中生成结构化每屏原因；overlay、scheduler 和诊断服务消费同一快照。具体值仅通过 daemon-owned overlay IPC 或 owner-only Unix Socket 存在于内存中；应用诊断页先取得显示器级保护租约再解除具体值遮盖。

**Tech Stack:** Python 3.11+、标准库 Unix domain sockets、pytest、Swift 5 / AppKit / SwiftUI、SwiftPM/XCTest、CoreGraphics、macOS Accessibility API、TOML 配置编辑器。

**Design Specs:** `docs/superpowers/specs/2026-08-22-privacy-protection-reason-diagnostics-design.md`；中文复核版为 `docs/superpowers/specs/2026-08-22-privacy-protection-reason-diagnostics-design.zh-CN.md`。

## Global Constraints

- 配置值固定为：`privacy_reason_display = overlay | diagnostics | hybrid`，默认 `hybrid`。
- 配置值固定为：`privacy_reason_detail = category | exact | tiered`，默认 `exact`。
- 配置值固定为：`privacy_reason_trigger = always | hover | click`，默认 `hover`。
- hover 模式必须保持完全鼠标穿透；click 模式只能拦截小标识 hit target，且浮层永远不能成为 key/main window。
- 具体值只允许包含现有顶层 detection inventory 已读取的 app name、bundle ID、window title 和命中规则；不得新增后台 AX Tree 或像素内容读取。
- 具体值不得进入 capture JSON、截图、日志、FTS、timeline、session、memory、模型请求、模型失败事件或 MCP 返回。
- 诊断页显示具体值前必须确认该窗口所在显示器已受保护；窗口移动时先遮盖，再保护新屏幕，最后释放旧屏幕。
- 诊断租约只能持久化 PID、nonce 和显示器 ID 等非敏感 guard 元数据；任何原因值不得落盘。
- Unix Socket 必须位于 owner-only runtime 目录，不监听 TCP/HTTP，不接入 MCP。
- `separate` 只排除受保护显示器；`all` 保留现有整张虚拟桌面跳过语义。
- 保持现有 foreground denylist、pause、fail-open/fail-closed、overlay acknowledgement 和 generation 因果校验语义。
- 支持 macOS 13+，同时验证 arm64 与 x86_64 helper 编译；不新增第三方 Python 或 Swift 依赖。
- 所有生产改动遵循 RED → GREEN；先运行并观察新增测试在当前实现上失败。
- 当前基线的全仓 Ruff 有 12 个无关旧问题；只要求本分支新增/修改的 Python 文件 Ruff clean，禁止顺手修改无关测试。
- `.superpowers/` 会话工件不得暂存或提交。

---

## 文件结构

### 新建 Python 文件

- `src/openchronicle/capture/protection_reason.py`：原因码、每屏原因模型、优先级、具体值清理和 wire payload。
- `src/openchronicle/capture/privacy_diagnostics_guard.py`：显示器级 reveal lease、非敏感 guard 原子持久化和进程存活策略。
- `src/openchronicle/capture/privacy_diagnostics.py`：owner-only Unix Socket、请求协议、snapshot stream 和 lease handshake。
- `tests/test_protection_reason.py`：原因模型与清理单元测试。
- `tests/test_privacy_diagnostics_guard.py`：租约/guard 生命周期测试。
- `tests/test_privacy_diagnostics.py`：Unix Socket、generation 与 exact 授权测试。

### 新建 Swift/helper 文件

- `resources/mac-privacy-overlay-reason.swift`：overlay reason wire model、展示文案和 always/hover/click 状态机。
- `macos/OpenChronicleApp/Sources/OpenChronicleApp/Models/PrivacyReasonOptions.swift`：三个配置选项模型。
- `macos/OpenChronicleApp/Sources/OpenChronicleApp/Models/ProtectionDiagnostics.swift`：诊断 wire model 与每屏展示模型。
- `macos/OpenChronicleApp/Sources/OpenChronicleApp/Services/PrivacyDiagnosticsClient.swift`：Unix Socket transport 与 NDJSON framing。
- `macos/OpenChronicleApp/Sources/OpenChronicleApp/Services/PrivacyDiagnosticsController.swift`：订阅、租约、跨屏移动、遮盖和重连状态机。
- `macos/OpenChronicleApp/Sources/OpenChronicleApp/Views/ProtectionDiagnosticsView.swift`：每屏诊断工作界面。
- `macos/OpenChronicleApp/Tests/OpenChronicleAppTests/PrivacyReasonOptionsTests.swift`
- `macos/OpenChronicleApp/Tests/OpenChronicleAppTests/PrivacyDiagnosticsControllerTests.swift`

### 主要修改文件

- `src/openchronicle/config.py`、`src/openchronicle/config_editor.py`：三个配置值、默认值、校验和 secret-safe snapshot。
- `src/openchronicle/capture/privacy.py`：返回全部结构化规则命中，而不是只返回首个字符串原因。
- `src/openchronicle/capture_pause.py`：提供暂停模式与有效恢复时间的 typed decision，同时保留 bool wrapper。
- `src/openchronicle/capture/protection.py`：把规则命中、暂停、失败、`all` 继承和诊断租约映射为每屏原因。
- `src/openchronicle/capture/protection_monitor.py`：读取 typed pause、组合租约、发布诊断 snapshot 和等待显示器保护确认。
- `src/openchronicle/capture/privacy_overlay.py`：按配置生成 category/exact overlay payload。
- `src/openchronicle/daemon.py`、`src/openchronicle/paths.py`：启动/停止 diagnostics runtime，确保 guard 在 capture 前加载。
- `resources/mac-privacy-overlay-core.swift`、`resources/mac-privacy-overlay.swift`、`resources/build-mac-privacy-overlay.sh`：reason UI 和鼠标交互。
- `pyproject.toml`、`tests/test_runtime_dependencies.py`：新 Swift helper source 的 wheel 映射。
- `macos/OpenChronicleApp/Sources/OpenChronicleApp/Models/Configuration.swift`：配置 snapshot/draft/patch 数据链。
- `macos/OpenChronicleApp/Sources/OpenChronicleApp/Models/MainWindowNavigation.swift`、`Views/MainWindowView.swift`、`OpenChronicleApp.swift`：诊断页导航与 controller 生命周期。
- `macos/OpenChronicleApp/Sources/OpenChronicleApp/Views/SettingsView.swift`：三组选择器。

---

### Task 1: 三组配置值与原生设置数据链

**Files:**
- Modify: `src/openchronicle/config.py:10-95, 285-305`
- Modify: `src/openchronicle/config_editor.py:50-65, 198-225, 365-385`
- Modify: `tests/test_config.py`
- Modify: `tests/test_cli_config_editor.py`
- Create: `macos/OpenChronicleApp/Sources/OpenChronicleApp/Models/PrivacyReasonOptions.swift`
- Modify: `macos/OpenChronicleApp/Sources/OpenChronicleApp/Models/Configuration.swift:53-90, 145-325`
- Modify: `macos/OpenChronicleApp/Tests/OpenChronicleAppTests/ConfigurationTests.swift`
- Create: `macos/OpenChronicleApp/Tests/OpenChronicleAppTests/PrivacyReasonOptionsTests.swift`

**Interfaces:**
- Produces: `PRIVACY_REASON_DISPLAY_MODES`, `PRIVACY_REASON_DETAIL_MODES`, `PRIVACY_REASON_TRIGGERS`
- Produces: `CaptureConfig.privacy_reason_display/detail/trigger: str`
- Produces: Swift `PrivacyReasonDisplayOption`, `PrivacyReasonDetailOption`, `PrivacyReasonTriggerOption`
- Produces: editable paths `capture.privacy_reason_display/detail/trigger`

- [ ] **Step 1: 写 Python 配置 RED 测试**

在 `tests/test_config.py` 添加：

```python
def test_privacy_reason_settings_default_and_normalize(tmp_path: Path) -> None:
    missing = config.load(tmp_path / "missing.toml").capture
    assert (
        missing.privacy_reason_display,
        missing.privacy_reason_detail,
        missing.privacy_reason_trigger,
    ) == ("hybrid", "exact", "hover")

    path = tmp_path / "config.toml"
    path.write_text(
        '[capture]\nprivacy_reason_display="OVERLAY"\n'
        'privacy_reason_detail="CATEGORY"\nprivacy_reason_trigger="CLICK"\n'
    )
    capture = config.load(path).capture
    assert (
        capture.privacy_reason_display,
        capture.privacy_reason_detail,
        capture.privacy_reason_trigger,
    ) == ("overlay", "category", "click")

    path.write_text(
        '[capture]\nprivacy_reason_display="bad"\n'
        'privacy_reason_detail="bad"\nprivacy_reason_trigger="bad"\n'
    )
    capture = config.load(path).capture
    assert (
        capture.privacy_reason_display,
        capture.privacy_reason_detail,
        capture.privacy_reason_trigger,
    ) == ("hybrid", "exact", "hover")
```

在 `tests/test_cli_config_editor.py` 添加一个 patch/validation 测试，断言三个路径出现在普通
config snapshot、可独立 patch，并且 `diagnostics + click` 可以保存但 UI 后续禁用无效控件；
非法字符串必须返回 exit code 2 和精确字段名。

- [ ] **Step 2: 运行 Python 配置测试并确认 RED**

Run:

```bash
PYTHONPATH=src uv run pytest -q \
  tests/test_config.py::test_privacy_reason_settings_default_and_normalize \
  tests/test_cli_config_editor.py::test_privacy_reason_settings_patch_and_validate
```

Expected: `CaptureConfig` 和 editable paths 尚无这些字段，测试失败。

- [ ] **Step 3: 实现 Python 配置与严格编辑器校验**

在 `src/openchronicle/config.py` 定义并使用：

```python
PRIVACY_REASON_DISPLAY_MODES = frozenset({"overlay", "diagnostics", "hybrid"})
PRIVACY_REASON_DETAIL_MODES = frozenset({"category", "exact", "tiered"})
PRIVACY_REASON_TRIGGERS = frozenset({"always", "hover", "click"})

@dataclass
class CaptureConfig:
    privacy_indicator_style: str = "pill"
    privacy_reason_display: str = "hybrid"
    privacy_reason_detail: str = "exact"
    privacy_reason_trigger: str = "hover"
```

在 `__post_init__` 对三个值 `strip().lower()`，非法值分别回退 `hybrid/exact/hover`。默认 TOML、
`EDITABLE_PATHS`、`validate_config_text()` 和普通 secret-safe snapshot 必须包含三个字段；
privacy-only snapshot 不需要重复这些非敏感值。

- [ ] **Step 4: 写 Swift option/draft RED 测试**

在 `PrivacyReasonOptionsTests.swift` 添加：

```swift
func testReasonOptionsAndDefaultsAreStable() {
  XCTAssertEqual(PrivacyReasonDisplayOption.allCases.map(\.rawValue), [
    "overlay", "diagnostics", "hybrid",
  ])
  XCTAssertEqual(PrivacyReasonDisplayOption.defaultValue, .hybrid)
  XCTAssertEqual(PrivacyReasonDetailOption.defaultValue, .exact)
  XCTAssertEqual(PrivacyReasonTriggerOption.defaultValue, .hover)
}
```

在 `ConfigurationTests.swift` 的 snapshot JSON 中加入三个字段，并断言
`ConfigurationDraft.updates(comparedTo:)` 只输出发生变化的三个路径；另加 missing/unknown
字段回退默认值测试。

- [ ] **Step 5: 运行 Swift 测试并确认 RED**

Run:

```bash
swift test --package-path macos/OpenChronicleApp \
  --filter 'PrivacyReasonOptionsTests|ConfigurationTests'
```

Expected: option types和 draft 字段尚不存在，编译失败。

- [ ] **Step 6: 实现 Swift 配置模型**

在 `PrivacyReasonOptions.swift` 定义三个 `String, CaseIterable, Identifiable` enum，包含
`title`、`detail`、`systemImage` 和上述 `defaultValue`。在 `CaptureConfigurationValue` 使用可选
wire 字段保证旧 backend 兼容；`ConfigurationDraft` 将 missing/unknown 值归一化，并在 updates
中产生精确 TOML 路径。

- [ ] **Step 7: 运行 Task 1 全套验证并提交**

Run:

```bash
PYTHONPATH=src uv run pytest -q tests/test_config.py tests/test_cli_config_editor.py
swift test --package-path macos/OpenChronicleApp
uv run ruff check src/openchronicle/config.py src/openchronicle/config_editor.py \
  tests/test_config.py tests/test_cli_config_editor.py
```

Expected: 全部 PASS。

Commit:

```bash
git add src/openchronicle/config.py src/openchronicle/config_editor.py \
  tests/test_config.py tests/test_cli_config_editor.py \
  macos/OpenChronicleApp/Sources/OpenChronicleApp/Models/PrivacyReasonOptions.swift \
  macos/OpenChronicleApp/Sources/OpenChronicleApp/Models/Configuration.swift \
  macos/OpenChronicleApp/Tests/OpenChronicleAppTests/ConfigurationTests.swift \
  macos/OpenChronicleApp/Tests/OpenChronicleAppTests/PrivacyReasonOptionsTests.swift
git commit -m "feat(config): add privacy reason settings"
```

---

### Task 2: 结构化每屏原因与 typed pause 信息

**Files:**
- Create: `src/openchronicle/capture/protection_reason.py`
- Modify: `src/openchronicle/capture/privacy.py:79-176`
- Modify: `src/openchronicle/capture_pause.py:30-130`
- Modify: `src/openchronicle/capture/protection.py`
- Modify: `src/openchronicle/capture/protection_monitor.py`
- Create: `tests/test_protection_reason.py`
- Modify: `tests/test_capture_privacy.py`
- Modify: `tests/test_capture_pause.py`
- Modify: `tests/test_protection.py`
- Modify: `tests/test_protection_monitor.py`

**Interfaces:**
- Produces: `ProtectionReasonCode`, `ProtectionReason`, `DisplayProtectionReasons`
- Produces: `sanitize_reason_value(value: str, limit: int = 160) -> str`
- Produces: `VisibleWindowRuleMatch(kind, rule, app_name, bundle_id, window_title)`
- Produces: `CapturePauseDecision(paused, kind, effective_resume_at)`
- Produces: `pause_reason_from_decision(decision) -> ProtectionReason | None`
- Produces: `ProtectionSnapshot.display_reasons` and `reasons_for_display(display_id)`
- Produces: `build_protection_snapshot(cfg, inventory, *, paused, generation, now, failure_reason=None, pause_reason=None, diagnostic_display_ids=frozenset())`

- [ ] **Step 1: 写原因模型与清理 RED 测试**

创建 `tests/test_protection_reason.py`：

```python
def test_reason_values_are_bounded_and_control_char_free() -> None:
    raw = "private\nwindow\t" + "x" * 300
    cleaned = sanitize_reason_value(raw)
    assert cleaned == "private window " + "x" * 144 + "…"
    assert len(cleaned) == 160


def test_category_payload_never_contains_exact_values() -> None:
    reason = ProtectionReason(
        code=ProtectionReasonCode.WINDOW_TITLE_RULE,
        display_id=2,
        app_name="Private Browser",
        window_title="Secret Account",
        rule="InPrivate",
    )
    payload = reason.to_payload(detail="category")
    assert payload == {"code": "window_title_rule", "display_id": 2}
    assert "Secret" not in repr(payload)
```

同时测试固定 priority、`+N` 主原因选择、全局 `display_id=None` 失败原因和最多 8 条原因限制。

- [ ] **Step 2: 写规则匹配与每屏映射 RED 测试**

在 `tests/test_capture_privacy.py` 添加测试，要求同一个窗口可同时返回 app、bundle 和 title
三个 `VisibleWindowRuleMatch`，并保存具体命中规则；未知标题只能返回
`WINDOW_TITLE_UNKNOWN`，不能伪造具体标题或规则命中。

在 `tests/test_protection.py` 添加：

```python
def test_all_mode_records_direct_and_inherited_display_reasons() -> None:
    snapshot = build_protection_snapshot(
        CaptureConfig(
            screenshot_monitor="all",
            deny_window_title_patterns=["InPrivate"],
        ),
        WindowInventory(
            windows=(VisibleWindow("Edge", "edge", "InPrivate", RIGHT.region),),
            displays=(LEFT, RIGHT),
        ),
        paused=False,
        generation=40,
        now=1.0,
    )
    assert [r.code for r in snapshot.reasons_for_display(2)] == [
        ProtectionReasonCode.WINDOW_TITLE_RULE,
    ]
    inherited = snapshot.reasons_for_display(1)
    assert inherited[0].code is ProtectionReasonCode.MODE_ALL_INHERITED
    assert inherited[0].source_display_id == 2
```

另加 diagnostic display ID 与直接规则 reason 组合而非覆盖的测试。

- [ ] **Step 3: 写 typed pause RED 测试**

在 `tests/test_capture_pause.py` 添加：

```python
def test_pause_decision_reports_timed_wait_and_effective_resume(tmp_path: Path) -> None:
    # 使用现有 _state() fixture 写入已过 resume_at、未满足 heartbeat 的 timed pause。
    decision = capture_pause_decision_strict(pause_path=tmp_path / ".paused", now=now)
    assert decision.paused is True
    assert decision.kind is CapturePauseKind.TIMED_WAITING
    assert decision.effective_resume_at is not None
```

同时覆盖无文件、legacy/indefinite、timed active、timed waiting 和安全 auto-resume；现有
`capture_is_paused_strict()` 与 `capture_is_paused()` bool 行为必须保持。

- [ ] **Step 4: 运行新增测试并确认 RED**

Run:

```bash
PYTHONPATH=src uv run pytest -q \
  tests/test_protection_reason.py \
  tests/test_capture_privacy.py \
  tests/test_capture_pause.py \
  tests/test_protection.py
```

Expected: 新类型、structured matches 和 pause decision 尚不存在，collection/断言失败。

- [ ] **Step 5: 实现原因模型与全部规则匹配**

在 `protection_reason.py` 定义至少这些固定码：

```python
class ProtectionReasonCode(StrEnum):
    APP_RULE = "app_rule"
    BUNDLE_RULE = "bundle_rule"
    WINDOW_TITLE_RULE = "window_title_rule"
    WINDOW_TITLE_UNKNOWN = "window_title_unknown"
    MODE_ALL_INHERITED = "mode_all_inherited"
    DIAGNOSTICS_REVEAL = "diagnostics_reveal"
    DIAGNOSTICS_GUARD_INVALID = "diagnostics_guard_invalid"
    MANUAL_PAUSE = "manual_pause"
    TIMED_PAUSE = "timed_pause"
    TIMED_PAUSE_WAITING = "timed_pause_waiting"
    PAUSE_STATE_UNAVAILABLE = "pause_state_unavailable"
    INVENTORY_UNAVAILABLE = "inventory_unavailable"
    HELPER_EXIT = "helper_exit"
    HELPER_PARSE = "helper_parse"
    EMPTY_DISPLAYS = "empty_displays"
    INVALID_DISPLAY_INVENTORY = "invalid_display_inventory"
    MULTIPLE_ACTIVE_WINDOWS = "multiple_active_windows"
    ACTIVE_WINDOW_UNMAPPED = "active_window_unmapped"
    SENSITIVE_WINDOW_UNMAPPED = "sensitive_window_unmapped"
    INDICATOR_UNCONFIRMED = "indicator_unconfirmed"
```

`ProtectionReason` 是 frozen dataclass；`to_payload(detail)` 是唯一 category/exact 序列化入口。
具体字符串在构造时统一清理和截断。`privacy.visible_window_rule_matches()` 返回全部去重命中，
但保留现有 `visible_window_denylist_reason()` 作为首个固定类别兼容 wrapper。

- [ ] **Step 6: 实现 typed pause decision 并保持 wrapper 兼容**

在 `capture_pause.py` 定义：

```python
class CapturePauseKind(StrEnum):
    NOT_PAUSED = "not_paused"
    INDEFINITE = "indefinite"
    TIMED = "timed"
    TIMED_WAITING = "timed_waiting"

@dataclass(frozen=True)
class CapturePauseDecision:
    paused: bool
    kind: CapturePauseKind
    effective_resume_at: datetime | None = None
```

`capture_pause_decision_strict()` 复用现有 race-check 和 unlink 流程；
`capture_is_paused_strict()` 只返回 `.paused`；`capture_is_paused()` 继续在 OSError 时脱敏并返回
True。`pause_reason_from_decision()` 将 indefinite/timed/waiting 精确映射为固定原因和有效恢复时间。
monitor 的 `pause_reader` 改为 typed decision，但仍允许测试注入 bool 并归一化。

- [ ] **Step 7: 实现每屏原因映射**

`build_protection_snapshot()` 增加可选 `pause_reason` 和 `diagnostic_display_ids`，并在 snapshot
中保存 reason display/detail/trigger 配置与 `DisplayProtectionReasons`。直接命中的显示器保存
全部 match；`all` 的其他显示器添加带 `source_display_id` 的继承原因；pause/failure 无法枚举
显示器时保存 `display_id=None` 的全局原因。诊断租约 ID 与现有规则原因取并集。

- [ ] **Step 8: 运行 Task 2 验证并提交**

Run:

```bash
PYTHONPATH=src uv run pytest -q \
  tests/test_protection_reason.py tests/test_capture_privacy.py \
  tests/test_capture_pause.py tests/test_protection.py tests/test_protection_monitor.py
uv run ruff check src/openchronicle/capture/protection_reason.py \
  src/openchronicle/capture/privacy.py src/openchronicle/capture_pause.py \
  src/openchronicle/capture/protection.py src/openchronicle/capture/protection_monitor.py \
  tests/test_protection_reason.py tests/test_capture_privacy.py \
  tests/test_capture_pause.py tests/test_protection.py tests/test_protection_monitor.py
```

Expected: 全部 PASS，原有 243 个测试不减少。

Commit:

```bash
git add src/openchronicle/capture/protection_reason.py \
  src/openchronicle/capture/privacy.py src/openchronicle/capture_pause.py \
  src/openchronicle/capture/protection.py src/openchronicle/capture/protection_monitor.py \
  tests/test_protection_reason.py tests/test_capture_privacy.py \
  tests/test_capture_pause.py tests/test_protection.py tests/test_protection_monitor.py
git commit -m "feat(capture): add structured protection reasons"
```

---

### Task 3: 浮层原因协议与 always/hover/click 交互

**Files:**
- Modify: `src/openchronicle/capture/privacy_overlay.py`
- Modify: `tests/test_privacy_overlay.py`
- Create: `resources/mac-privacy-overlay-reason.swift`
- Modify: `resources/mac-privacy-overlay-core.swift`
- Modify: `resources/mac-privacy-overlay.swift`
- Modify: `resources/build-mac-privacy-overlay.sh`
- Modify: `tests/swift/MacPrivacyOverlayCoreTests.swift`
- Modify: `tests/swift/MacPrivacyOverlayProtocolTests.swift`
- Modify: `pyproject.toml`
- Modify: `tests/test_runtime_dependencies.py`

**Interfaces:**
- Produces: Python `_reason_payloads_for_display(snapshot, display_id) -> list[dict]`
- Produces: optional overlay wire fields `reason_display`, `reason_detail`, `reason_trigger`, `reasons`
- Produces: Swift `OverlayReason`, `OverlayReasonTrigger`, `ReasonRevealState`
- Preserves: old overlay command decoding, generation acknowledgement, nonactivation and capture gates

- [ ] **Step 1: 写 Python overlay payload RED 测试**

在 `tests/test_privacy_overlay.py` 添加 category/exact/tiered/display-mode matrix：

先在该测试文件定义 `_private_title_reason(display_id: int) -> ProtectionReason`，固定返回
Edge/InPrivate exact fixture；定义 `_protected_snapshot(**overrides) -> ProtectionSnapshot`，使用
两个 `DisplayInfo`、默认 protected display `{2}`，并只允许 overrides 覆盖三个 reason config 和
扁平 `reasons` fixture（helper 内部转换为 `DisplayProtectionReasons`）。不得从生产配置或本机窗口
读取 fixture。

```python
def test_overlay_exact_reason_is_sent_only_for_protected_display() -> None:
    snapshot = _protected_snapshot(
        reason_display="hybrid",
        reason_detail="exact",
        reason_trigger="hover",
        reasons=(_private_title_reason(display_id=2),),
    )
    command = PrivacyOverlayClient._render_command(snapshot)
    by_id = {row["id"]: row for row in command["displays"]}
    assert by_id[2]["reasons"][0] == {
        "code": "window_title_rule",
        "display_id": 2,
        "app_name": "Edge",
        "bundle_id": "com.microsoft.edgemac",
        "window_title": "InPrivate",
        "rule": "InPrivate",
    }
    assert 1 not in by_id


def test_diagnostics_only_overlay_payload_contains_no_reason_values() -> None:
    snapshot = _protected_snapshot(
        reason_display="diagnostics",
        reason_detail="exact",
        reasons=(_private_title_reason(display_id=2),),
    )
    raw = json.dumps(PrivacyOverlayClient._render_command(snapshot))
    assert '"reasons":[]' in raw
    assert "InPrivate" not in raw
```

另断言 `category` 和 `tiered` payload 只有固定码、旧 snapshot 无 reason 字段仍可 render、每屏
最多 8 条、主原因按 priority 排序。

- [ ] **Step 2: 运行 Python overlay 测试并确认 RED**

Run:

```bash
PYTHONPATH=src uv run pytest -q tests/test_privacy_overlay.py
```

Expected: snapshot/wire 尚无 reason 配置和 payload，新增断言失败。

- [ ] **Step 3: 写 Swift reason/reveal RED 测试**

扩展 `MacPrivacyOverlayCoreTests.swift`：

```swift
let oldCommand = try JSONDecoder().decode(
  OverlayCommand.self,
  from: Data(#"{"generation":1,"state":"protected","style":"pill","displays":[],"all_displays":false}"#.utf8)
)
precondition(oldCommand.reasonTrigger == .hover)
precondition(oldCommand.displays.allSatisfy { ($0.reasons ?? []).isEmpty })

var hover = ReasonRevealState(trigger: .hover)
hover.update(pointerInside: true)
precondition(hover.isExpanded)
hover.update(pointerInside: false)
precondition(!hover.isExpanded)

var click = ReasonRevealState(trigger: .click)
click.click()
precondition(click.isExpanded)
click.click()
precondition(!click.isExpanded)
```

新增 controller 测试：hover 前后 `panel.ignoresMouseEvents == true`；click 只有 cursor 位于
indicator hit target 时为 false；panel 始终不能成为 key/main。

- [ ] **Step 4: 编译 Swift 测试并确认 RED**

Run:

```bash
swiftc resources/mac-privacy-overlay-reason.swift \
  resources/mac-privacy-overlay-core.swift \
  tests/swift/MacPrivacyOverlayCoreTests.swift \
  -o /tmp/openchronicle-overlay-reason-tests -framework AppKit
```

Expected: 新 source/type 尚不存在，编译失败。

- [ ] **Step 5: 实现 Python reason payload**

`PrivacyOverlayClient._render_command()` 给每个输出 display 添加 `reasons`。只有
`reason_display in {"overlay", "hybrid"}` 才序列化原因；`exact` 调用
`ProtectionReason.to_payload("exact")`，`category/tiered` 调用 category。全局 pause/failure
原因复制到 helper 将自行枚举的全部显示器命令中，但具体值只允许在 snapshot 已阻止截图时
出现。clear 命令显式发送空 reason 字段以清除旧展开内容。

- [ ] **Step 6: 实现 Swift wire model 和纯 reveal 状态机**

在 `mac-privacy-overlay-reason.swift` 定义：

```swift
enum OverlayReasonTrigger: String, Codable { case always, hover, click }

struct OverlayReason: Codable, Equatable {
    let code: String
    let displayID: UInt32?
    let sourceDisplayID: UInt32?
    let appName: String?
    let bundleID: String?
    let windowTitle: String?
    let rule: String?
}

struct ReasonRevealState {
    let trigger: OverlayReasonTrigger
    private(set) var isExpanded: Bool
    mutating func update(pointerInside: Bool)
    mutating func click()
}
```

`OverlayCommand` 和 `OverlayDisplay` 使用自定义 decode，让缺失字段回退 trigger `hover`、空原因。
reason presentation 使用固定中文 category 文案；exact 只拼接 payload 已给出的值，禁止输出
未知 JSON 或 raw error。

- [ ] **Step 7: 实现 panel 布局和鼠标行为**

把 reason 展示加入 `IndicatorView`，收起尺寸保持现有稳定尺寸，展开尺寸使用固定最大宽度和
最多 3 行，超出显示 `+N`。`PrivacyOverlayController` 注入
`pointerProvider: () -> NSPoint` 和 timer factory 便于测试：

```swift
private let pointerPollInterval: TimeInterval = 0.08

// hover: only update expanded state; never change ignoresMouseEvents.
// click: set ignoresMouseEvents=false only while pointer is in the compact/expanded hit target.
// always: expanded=true and ignoresMouseEvents=true.
```

click handler只切换展开状态并消费事件；`acceptsFirstMouse` 不得激活 app。border/banner 样式的
hit target 是角落 badge，不是整块全屏 panel，避免阻断整屏鼠标。

- [ ] **Step 8: 更新 helper 编译与 wheel source 映射**

`build-mac-privacy-overlay.sh`、Python `_sources_are_fresh/_maybe_compile_overlay` 和 swiftc 命令
都按 `[reason, core, main]` 三个 source 判断 freshness/编译。`pyproject.toml` 添加：

```toml
"resources/mac-privacy-overlay-reason.swift" = "openchronicle/_bundled/mac-privacy-overlay-reason.swift"
```

`tests/test_runtime_dependencies.py` 解析 TOML 并断言 exact mapping；缺任一 source 时 helper
解析必须 fail closed，不能复用旧 binary。

- [ ] **Step 9: 运行 Task 3 验证并提交**

Run:

```bash
PYTHONPATH=src uv run pytest -q tests/test_privacy_overlay.py tests/test_runtime_dependencies.py
swiftc resources/mac-privacy-overlay-reason.swift \
  resources/mac-privacy-overlay-core.swift \
  tests/swift/MacPrivacyOverlayCoreTests.swift \
  -o /tmp/openchronicle-overlay-reason-tests -framework AppKit
/tmp/openchronicle-overlay-reason-tests
bash resources/build-mac-privacy-overlay.sh
swiftc tests/swift/MacPrivacyOverlayProtocolTests.swift \
  -o /tmp/openchronicle-overlay-protocol-tests
/tmp/openchronicle-overlay-protocol-tests resources/mac-privacy-overlay
uv run ruff check src/openchronicle/capture/privacy_overlay.py \
  tests/test_privacy_overlay.py tests/test_runtime_dependencies.py
```

Expected: Python/Swift/protocol/build 全部 PASS。

Commit:

```bash
git add src/openchronicle/capture/privacy_overlay.py tests/test_privacy_overlay.py \
  resources/mac-privacy-overlay-reason.swift resources/mac-privacy-overlay-core.swift \
  resources/mac-privacy-overlay.swift resources/build-mac-privacy-overlay.sh \
  tests/swift/MacPrivacyOverlayCoreTests.swift \
  tests/swift/MacPrivacyOverlayProtocolTests.swift \
  pyproject.toml tests/test_runtime_dependencies.py
git commit -m "feat(macos): add interactive protection reasons"
```

---

### Task 4: 显示器级诊断租约与非敏感 guard

**Files:**
- Modify: `src/openchronicle/paths.py`
- Create: `src/openchronicle/capture/privacy_diagnostics_guard.py`
- Create: `tests/test_privacy_diagnostics_guard.py`

**Interfaces:**
- Produces: `paths.runtime_dir()`, `privacy_diagnostics_socket()`, `privacy_diagnostics_guard()`
- Produces: `DiagnosticsRevealLease(lease_id, pid, display_ids)`
- Produces: `DiagnosticsGuardSnapshot(display_ids, fail_closed_all)`
- Produces: `DiagnosticsLeaseManager.load/acquire/begin_move/commit_move/release/snapshot/prune_dead`

- [ ] **Step 1: 写 lease/guard RED 测试**

创建 `tests/test_privacy_diagnostics_guard.py`：

```python
def test_guard_contains_only_non_sensitive_metadata(tmp_path: Path) -> None:
    marker = "private-window-title"
    manager = DiagnosticsLeaseManager(
        tmp_path / "privacy-reveal.guard",
        process_alive=lambda _pid: True,
    )
    lease = manager.acquire(pid=123, display_id=2)
    raw = (tmp_path / "privacy-reveal.guard").read_text()
    payload = json.loads(raw)
    assert payload == {
        "schema_version": 1,
        "lease_id": lease.lease_id,
        "pid": 123,
        "display_ids": [2],
    }
    assert marker not in raw
    assert stat.S_IMODE((tmp_path / "privacy-reveal.guard").stat().st_mode) == 0o600


def test_move_protects_old_and_new_until_commit(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    lease = manager.acquire(pid=123, display_id=1)
    transition = manager.begin_move(lease.lease_id, pid=123, new_display_id=2)
    assert manager.snapshot().display_ids == frozenset({1, 2})
    manager.commit_move(transition.transition_id)
    assert manager.snapshot().display_ids == frozenset({2})
```

另覆盖错误 PID/lease ID、clean release、app PID 仍存活时 daemon restart 恢复、PID 已退出时
清理、process 状态不确定时保持 guard、非法/截断 guard 时 `fail_closed_all=True`、并发 acquire
与原子 replace。

- [ ] **Step 2: 运行测试并确认 RED**

Run:

```bash
PYTHONPATH=src uv run pytest -q tests/test_privacy_diagnostics_guard.py
```

Expected: paths/API 尚不存在，collection 失败。

- [ ] **Step 3: 实现 runtime paths 与权限**

在 `paths.py` 添加：

```python
def runtime_dir() -> Path:
    return root() / "runtime"

def privacy_diagnostics_socket() -> Path:
    return runtime_dir() / "privacy-diagnostics.sock"

def privacy_diagnostics_guard() -> Path:
    return runtime_dir() / "privacy-reveal.guard"
```

`ensure_dirs()` 创建 runtime 后强制 mode `0700`；不得改变用户现有 root/config/data 权限。

- [ ] **Step 4: 实现 thread-safe lease manager**

在 `privacy_diagnostics_guard.py` 使用 `threading.RLock`。一个 app PID 可持有一个 active lease；
`begin_move()` 先写 `{old,new}`，`commit_move()` 再写 `{new}`。所有写入使用同目录临时文件、
`chmod(0600)`、`fsync` 和 `os.replace`。guard JSON 只允许上述四个 key；未知 key、非法 display
ID、非法 PID 或非法 schema 进入 `fail_closed_all`，不得默默删除。

`prune_dead()` 只有 `process_alive(pid) is False` 才可清理；True 或异常/None 都继续保护。
release 必须同时匹配 lease ID 和 PID，防止旧页面释放新租约。

默认 process probe 使用 `os.kill(pid, 0)`：成功为 True，`ProcessLookupError` 为 False，
`PermissionError` 或其他 OSError 为 None。不得使用进程名称猜测或在 uncertain 时清理 guard。

- [ ] **Step 5: 运行 Task 4 验证并提交**

Run:

```bash
PYTHONPATH=src uv run pytest -q tests/test_privacy_diagnostics_guard.py
uv run ruff check src/openchronicle/paths.py \
  src/openchronicle/capture/privacy_diagnostics_guard.py \
  tests/test_privacy_diagnostics_guard.py
```

Expected: 全部 PASS，测试临时目录之外无文件变化。

Commit:

```bash
git add src/openchronicle/paths.py \
  src/openchronicle/capture/privacy_diagnostics_guard.py \
  tests/test_privacy_diagnostics_guard.py
git commit -m "feat(capture): add diagnostics display leases"
```

---

### Task 5: Unix Socket 诊断服务与 daemon/monitor/capture 集成

**Files:**
- Create: `src/openchronicle/capture/privacy_diagnostics.py`
- Modify: `src/openchronicle/capture/protection.py`
- Modify: `src/openchronicle/capture/protection_monitor.py`
- Modify: `src/openchronicle/capture/scheduler.py`
- Modify: `src/openchronicle/daemon.py`
- Create: `tests/test_privacy_diagnostics.py`
- Modify: `tests/test_protection.py`
- Modify: `tests/test_protection_monitor.py`
- Modify: `tests/test_capture_scheduler_fts.py`
- Modify: `tests/test_daemon_protection.py`

**Interfaces:**
- Produces: `PrivacyDiagnosticsServer.start/stop/publish`
- Produces: NDJSON request actions `subscribe`, `acquire_exact`, `move_exact`, `release_exact`
- Produces: response types `snapshot`, `lease`, `error`
- Produces: `PrivacyProtectionMonitor.wait_for_display_protection(display_id, after_generation, timeout)`
- Consumes: `DiagnosticsLeaseManager`, `ProtectionDecision`, `ProtectionReason.to_payload()`

- [ ] **Step 1: 写 Unix Socket wire/authorization RED 测试**

创建 `tests/test_privacy_diagnostics.py`，使用临时 owner-only 目录和真实 AF_UNIX client：

测试文件必须定义以下本地 helper：`FakeProtectionCallbacks` 固定 confirmed generation 42 并记录
refresh 次数和 wait display ID；`_round_trip()` 连接一个 AF_UNIX client、写一行 compact JSON、
读取一行 bounded JSON object 后关闭；`_private_decision()` 返回双屏 snapshot，其中 display 2 有
一条 exact title-rule reason；`_start_test_server()` 只能使用 `tmp_path` socket/guard 和上述 fake
callbacks。每个测试结束必须调用 `server.stop()` 并断言 server thread 已退出、socket 已删除。

```python
def test_category_subscription_never_contains_exact_values(tmp_path: Path) -> None:
    marker = "private-window-title"
    server = _start_test_server(tmp_path, decision=_private_decision(marker))
    response = _round_trip(server.socket_path, {"schema_version": 1, "action": "subscribe"})
    assert response["type"] == "snapshot"
    assert response["displays"][0]["reasons"] == [
        {"code": "window_title_rule", "display_id": 2}
    ]
    assert marker not in json.dumps(response)


def test_exact_response_requires_confirmed_display_lease(tmp_path: Path) -> None:
    callbacks = FakeProtectionCallbacks()
    server = _start_test_server(tmp_path, callbacks=callbacks, decision=_private_decision())
    denied = _round_trip(server.socket_path, {
        "schema_version": 1,
        "action": "subscribe",
        "detail": "exact",
    })
    assert denied == {"schema_version": 1, "type": "error", "code": "lease_required"}

    lease = _round_trip(server.socket_path, {
        "schema_version": 1,
        "action": "acquire_exact",
        "pid": os.getpid(),
        "display_id": 2,
    })
    assert callbacks.refresh_requests == 1
    assert callbacks.waited_display_ids == [2]
    assert lease["type"] == "lease"
    assert lease["protected_generation"] == callbacks.confirmed_generation
```

另覆盖 socket/目录 mode、超长 line、非法 JSON/unknown action 固定错误且不 echo 输入、stale
generation、不带租约 exact、错误 PID/lease、disconnect 保持 guard、stop/unlink、多个 subscriber
仅在 generation 变化时收到 push。

- [ ] **Step 2: 写 monitor/daemon/scheduler RED 集成测试**

在 `tests/test_protection_monitor.py`：

```python
def test_diagnostics_guard_is_published_and_waitable(inventory, fake_overlay) -> None:
    guard = MutableGuard(display_ids=frozenset({1}))
    published: list[ProtectionDecision] = []
    monitor = make_monitor(
        inventory=inventory,
        overlay=fake_overlay,
        diagnostics_guard_reader=guard.snapshot,
        decision_listener=published.append,
    )
    decision = monitor.decision_for_capture(force=True)
    assert 1 in decision.snapshot.protected_display_ids
    assert decision.snapshot.reasons_for_display(1)[0].code.value == "diagnostics_reveal"
    assert monitor.wait_for_display_protection(1, after_generation=0, timeout=0.1) == (
        decision.snapshot.generation
    )
    assert published == [decision]
```

在 `tests/test_capture_scheduler_fts.py` 使用真实 monitor + guard，断言 active diagnostics display
不读取 AX、截图只保留另一显示器，且具体 marker 不出现在返回 capture dict。增加 `all` 模式整次
截图跳过测试。

在 `tests/test_daemon_protection.py` 注入 fake server/manager，断言顺序为：load guard → start
monitor → start diagnostics → start capture；shutdown 时先 stop diagnostics，再 stop monitor，socket
和 PID 文件均清理。

- [ ] **Step 3: 运行集成测试并确认 RED**

Run:

```bash
PYTHONPATH=src uv run pytest -q \
  tests/test_privacy_diagnostics.py tests/test_protection_monitor.py \
  tests/test_capture_scheduler_fts.py tests/test_daemon_protection.py
```

Expected: server/callback/wait API 尚不存在，测试失败。

- [ ] **Step 4: 实现 bounded owner-only NDJSON server**

`PrivacyDiagnosticsServer` 使用单独 daemon thread 和 AF_UNIX socket；bind 后 chmod `0600`，
accept 前验证 runtime dir `0700`。每行最多 64 KiB、每个 client 有 bounded send queue；JSON
响应统一包含 `schema_version=1`。任何 parse/protocol 错误只返回固定 code，不记录或 echo body。

协议固定为：

```json
{"schema_version":1,"action":"subscribe"}
{"schema_version":1,"action":"acquire_exact","pid":123,"display_id":2}
{"schema_version":1,"action":"move_exact","pid":123,"lease_id":"lease-test","display_id":1}
{"schema_version":1,"action":"release_exact","pid":123,"lease_id":"lease-test"}
```

snapshot 包含 generation、state、indicator_confirmed、created_at、每屏 screenshot/AX blocked 和
reasons。未确认 lease 的连接永远只能拿 category payload。server 不暴露 TCP、HTTP、MCP tool。

- [ ] **Step 5: 实现 monitor generation handshake**

`PrivacyProtectionMonitor` 新增 guard reader、decision listeners 和 `threading.Condition`。
refresh 在 decision 原子发布并完成 overlay acknowledgement 后通知 condition，再在锁外调用
listeners。`wait_for_display_protection()` 只有在 generation 大于基线、display ID 已受保护、
且 indicator 已确认（style `off` 由 overlay client 视为已确认）时返回；timeout 返回 None，租约
保持不释放。

guard `fail_closed_all=True` 生成
`ProtectionReasonCode.DIAGNOSTICS_GUARD_INVALID` 和全局保护；正常 display IDs 通过 Task 2 的
`diagnostic_display_ids` 组合到 snapshot。listener 异常只记录类型，不能停止 monitor 或包含
payload。

- [ ] **Step 6: 实现 acquire/move/release 顺序**

- acquire：记录 lease → request refresh → 等待目标显示器受保护 → 返回 generation；超时返回固定
  error 并保留 guard。
- move：`begin_move` 保护 old+new → refresh/等待 new → `commit_move` 只保留 new → 再 refresh。
- release：客户端承诺先遮盖；server 校验 PID/lease 后删除 guard 并 refresh。
- disconnect/crash：不自动 release；watchdog 只有确认 PID 死亡才 prune 并 refresh。

任何一步失败都不得发送 exact snapshot。

- [ ] **Step 7: 接入 daemon 生命周期和 capture gate**

daemon 启动时先 `lease_manager.load()`，再构造 monitor/server，完成 callback 双向绑定；guard
必须参与首次 monitor decision，capture task 最后启动。shutdown 先关闭 socket 让 app 立即遮盖，
再停止 capture/monitor；非敏感 guard 按 lease 状态保留或清理。

scheduler 不新增独立原因判断，只继续消费 snapshot 的 `protected_display_ids`、
`protected_regions` 和 `ax_blocked`，确保自保护与现有 denylist 使用同一 gate。

- [ ] **Step 8: 运行 Task 5 验证并提交**

Run:

```bash
PYTHONPATH=src uv run pytest -q \
  tests/test_privacy_diagnostics.py tests/test_privacy_diagnostics_guard.py \
  tests/test_protection.py tests/test_protection_monitor.py \
  tests/test_capture_scheduler_fts.py tests/test_daemon_protection.py
uv run ruff check src/openchronicle/capture/privacy_diagnostics.py \
  src/openchronicle/capture/privacy_diagnostics_guard.py \
  src/openchronicle/capture/protection.py \
  src/openchronicle/capture/protection_monitor.py \
  src/openchronicle/capture/scheduler.py src/openchronicle/daemon.py \
  tests/test_privacy_diagnostics.py tests/test_privacy_diagnostics_guard.py \
  tests/test_protection.py tests/test_protection_monitor.py \
  tests/test_capture_scheduler_fts.py tests/test_daemon_protection.py
```

Expected: 全部 PASS，无线程或 socket 残留。

Commit:

```bash
git add src/openchronicle/capture/privacy_diagnostics.py \
  src/openchronicle/capture/protection.py \
  src/openchronicle/capture/protection_monitor.py \
  src/openchronicle/capture/scheduler.py src/openchronicle/daemon.py \
  tests/test_privacy_diagnostics.py tests/test_protection.py \
  tests/test_protection_monitor.py tests/test_capture_scheduler_fts.py \
  tests/test_daemon_protection.py
git commit -m "feat(capture): serve protection diagnostics"
```

---

### Task 6: 原生应用 Unix Socket client 与租约状态机

**Files:**
- Create: `macos/OpenChronicleApp/Sources/OpenChronicleApp/Models/ProtectionDiagnostics.swift`
- Create: `macos/OpenChronicleApp/Sources/OpenChronicleApp/Services/PrivacyDiagnosticsClient.swift`
- Create: `macos/OpenChronicleApp/Sources/OpenChronicleApp/Services/PrivacyDiagnosticsController.swift`
- Modify: `macos/OpenChronicleApp/Sources/OpenChronicleApp/Models/RuntimeStatus.swift:39-60`
- Create: `macos/OpenChronicleApp/Tests/OpenChronicleAppTests/PrivacyDiagnosticsControllerTests.swift`

**Interfaces:**
- Produces: Codable `ProtectionDiagnosticsWireMessage`, `ProtectionDiagnosticsSnapshot`, `ProtectionDisplayDiagnostic`, `ProtectionReasonDiagnostic`
- Produces: `PrivacyDiagnosticsTransport` protocol and `UnixPrivacyDiagnosticsTransport`
- Produces: `PrivacyDiagnosticsController.setPageVisible/setDisplay/revealExact/hideExact/shutdown`
- Consumes: Task 1 reason options and Task 5 NDJSON protocol

- [ ] **Step 1: 写 wire decode 与 controller RED 测试**

创建 `PrivacyDiagnosticsControllerTests.swift`，使用 `FakePrivacyDiagnosticsTransport`：

fake transport 必须记录按顺序发送的 request，并提供同步 `deliverLease`、`deliverSnapshot`、
`disconnect` 方法；不得启动真实 socket。`makeExactController(confirmedOn:)` 创建已获得指定显示器
lease 和 matching generation snapshot 的 controller，供 move/release 测试复用。

```swift
func testExactValuesRemainHiddenUntilLeaseAndGenerationAreConfirmed() async {
  let transport = FakePrivacyDiagnosticsTransport()
  let controller = PrivacyDiagnosticsController(
    transportFactory: { transport },
    displayModeProvider: { .hybrid },
    detailProvider: { .exact },
    pidProvider: { 123 }
  )
  controller.setDisplay(2)
  controller.setPageVisible(true)
  XCTAssertEqual(transport.sent.last?.action, .acquireExact)
  XCTAssertFalse(controller.showsExactValues)

  transport.deliverLease(id: "lease-1", protectedGeneration: 42)
  transport.deliverSnapshot(generation: 41, exact: true)
  XCTAssertFalse(controller.showsExactValues)
  transport.deliverSnapshot(generation: 42, exact: true)
  XCTAssertTrue(controller.showsExactValues)
}
```

另覆盖 category 不申请 lease、tiered 需 `revealExact()`、disconnect 立即遮盖但不 release、页面
离开先遮盖再发送 release、display mode `overlay` 不订阅/不申请 lease、stale lease ack 拒绝、
display ID 不可用时保持遮盖、错误 lease ID、malformed message、reconnect backoff。

- [ ] **Step 2: 写跨屏移动与 shutdown RED 测试**

```swift
func testMoveHidesBeforeProtectingNewDisplay() async {
  let (controller, transport) = makeExactController(confirmedOn: 1)
  XCTAssertTrue(controller.showsExactValues)
  controller.setDisplay(2)
  XCTAssertFalse(controller.showsExactValues)
  XCTAssertEqual(transport.sent.last?.action, .moveExact)
  XCTAssertEqual(transport.sent.last?.displayID, 2)
  transport.deliverLease(id: "lease-1", protectedGeneration: 50)
  transport.deliverSnapshot(generation: 50, exact: true)
  XCTAssertTrue(controller.showsExactValues)
}
```

`shutdown()` 必须先清空 published exact display models，再尝试 release/close；release 失败不能重新
显示值。

- [ ] **Step 3: 运行 Swift 测试并确认 RED**

Run:

```bash
swift test --package-path macos/OpenChronicleApp \
  --filter PrivacyDiagnosticsControllerTests
```

Expected: 新 model/client/controller 尚不存在，编译失败。

- [ ] **Step 4: 实现 Codable wire model**

`ProtectionDiagnostics.swift` 严格映射 schema v1；unknown reason code 保留固定
`unknown` category，但 exact unknown fields 丢弃。具体值在 controller 发布给 View 前再次限制
160 字符和控制字符。display model 包含：ID、primary、state、screenshotBlocked、axBlocked、
indicatorConfirmed、reasons、generation 和 updatedAt。

- [ ] **Step 5: 实现 POSIX Unix Socket transport**

定义可注入 protocol：

```swift
protocol PrivacyDiagnosticsTransport: AnyObject {
  var onMessage: ((ProtectionDiagnosticsWireMessage) -> Void)? { get set }
  var onDisconnect: ((Error?) -> Void)? { get set }
  func connect() throws
  func send(_ request: PrivacyDiagnosticsRequest) throws
  func close()
}
```

`UnixPrivacyDiagnosticsTransport` 使用 AF_UNIX/SOCK_STREAM，socket path 来自
`RuntimePaths.privacyDiagnosticsSocket`；后台 serial queue 做 bounded NDJSON read/write，每行
上限 64 KiB，所有 callback 回到 MainActor。禁止打印/描述 message body；错误只使用固定 code
和类型。close 必须幂等并终止 reader。

- [ ] **Step 6: 实现 MainActor controller 状态机**

controller 保存 connection、latest category snapshot、lease ID、protected generation、display ID
和 `showsExactValues`。只有以下条件同时满足才发布 exact reasons：页面可见、detail exact/tiered
已授权、lease ID 匹配、snapshot generation 不旧于 protected generation、snapshot display 已
标记 diagnostics self-protection。

display mode 为 `overlay` 时 controller 不订阅 reason，也不申请 lease；切换到 diagnostics/hybrid
才连接并遵循 detail mode。

mode/detail provider 必须读取 backend 已保存并加载的 configuration snapshot，不得让未保存的
Settings draft 提前改变诊断安全策略；Apply & Restart 后才切换 active policy。

`setDisplay()` 在 display 改变时同步遮盖并发送 move；`setPageVisible(false)` 同步遮盖后 release；
disconnect 遮盖并指数 backoff 重连，不推断 release 成功。所有 stale callback 以 connection/
lease generation 拒绝。

- [ ] **Step 7: 扩展 RuntimePaths 并运行 Task 6 验证**

在 Swift `RuntimePaths` 添加：

```swift
var runtimeDirectory: URL { root.appendingPathComponent("runtime", isDirectory: true) }
var privacyDiagnosticsSocket: URL {
  runtimeDirectory.appendingPathComponent("privacy-diagnostics.sock")
}
```

Run:

```bash
swift test --package-path macos/OpenChronicleApp
```

Expected: 全部 PASS，现有 26 个测试不减少。

- [ ] **Step 8: 提交 Swift client/controller**

```bash
git add macos/OpenChronicleApp/Sources/OpenChronicleApp/Models/RuntimeStatus.swift \
  macos/OpenChronicleApp/Sources/OpenChronicleApp/Models/ProtectionDiagnostics.swift \
  macos/OpenChronicleApp/Sources/OpenChronicleApp/Services/PrivacyDiagnosticsClient.swift \
  macos/OpenChronicleApp/Sources/OpenChronicleApp/Services/PrivacyDiagnosticsController.swift \
  macos/OpenChronicleApp/Tests/OpenChronicleAppTests/PrivacyDiagnosticsControllerTests.swift
git commit -m "feat(macos): add privacy diagnostics client"
```

---

### Task 7: 设置选择器与 Protection Diagnostics 页面

**Files:**
- Modify: `macos/OpenChronicleApp/Sources/OpenChronicleApp/Models/MainWindowNavigation.swift`
- Modify: `macos/OpenChronicleApp/Sources/OpenChronicleApp/Views/MainWindowView.swift`
- Modify: `macos/OpenChronicleApp/Sources/OpenChronicleApp/Views/SettingsView.swift`
- Create: `macos/OpenChronicleApp/Sources/OpenChronicleApp/Views/ProtectionDiagnosticsView.swift`
- Modify: `macos/OpenChronicleApp/Sources/OpenChronicleApp/OpenChronicleApp.swift`
- Modify: `macos/OpenChronicleApp/Tests/OpenChronicleAppTests/MainWindowNavigationTests.swift`
- Modify: `macos/OpenChronicleApp/Tests/OpenChronicleAppTests/ConfigurationTests.swift`

**Interfaces:**
- Produces: `MainWindowSection.protectionDiagnostics`
- Produces: compact three-picker Capture settings UI
- Produces: `ProtectionDiagnosticsView` and window-screen observer
- Consumes: `PrivacyDiagnosticsController` and Task 1 option enums

- [ ] **Step 1: 写 navigation/default control-state RED 测试**

在 `MainWindowNavigationTests.swift` 断言 `.protectionDiagnostics` 恰好出现一次、属于 Control、
不是 configuration section，并有固定 title/subtitle/systemImage。

在 `ConfigurationTests.swift` 添加纯 UI-state helper 测试：display `diagnostics` 或 indicator style
`off` 时 trigger picker disabled；`overlay/hybrid` 且 style 非 off 时 enabled。不要在 View 测试中
复制这一条件。

- [ ] **Step 2: 运行 Swift 测试并确认 RED**

Run:

```bash
swift test --package-path macos/OpenChronicleApp \
  --filter 'MainWindowNavigationTests|ConfigurationTests'
```

Expected: 新 section/helper 尚不存在，测试失败。

- [ ] **Step 3: 实现三个紧凑选择器**

在 Capture 的 Screenshots section 中，保留现有 indicator style picker，并添加三个带 SF Symbol
的 `Picker`：Reason location、Detail、Overlay reveal。显示位置和详细度始终可编辑；trigger 在
diagnostics-only 或 style off 时 disabled，但保存值不被静默改写。使用 option enum 提供 label，
不在 View 复制 raw strings。

- [ ] **Step 4: 实现诊断导航和 controller 生命周期**

把 `.protectionDiagnostics` 加入 Control。`OpenChronicleDesktopApp` 创建一个
`PrivacyDiagnosticsController` StateObject，并传给 AppDelegate/MainWindowView；application
terminate 先 `controller.shutdown()` 再停止 backend。

`MainWindowView` 对诊断 section 渲染 `ProtectionDiagnosticsView`，其他 section 注入保持不变。

- [ ] **Step 5: 实现工作型诊断页面**

`ProtectionDiagnosticsView` 使用紧凑 `Table`/unframed layout，而不是营销卡片。每屏一行：显示器、
状态、截图、AX、主原因、`+N`、generation/age、confirmation。选择一行后在下方展示全部原因；
exact 被遮盖时显示固定 placeholder，不泄漏 cached value。

页面 `onAppear/onDisappear` 调用 controller visibility。一个小型 `NSViewRepresentable` 观察
`NSWindow.didChangeScreenNotification`，从 `NSScreenNumber` 得到 UInt32 display ID 并调用
`setDisplay()`。tiered 模式提供明确的“显示具体值/隐藏具体值”命令；exact 模式自动申请，
category 不显示该命令。

- [ ] **Step 6: 验证 narrow/long text 与非重叠布局**

为 reason presentation 提供最长 160 字符 fixture 和 8 reasons fixture；Swift model 测试断言
行数/截断 descriptor 稳定。构建 release app 后用 Computer Use 截图检查 900×680 最小窗口和
宽窗口：文字不遮挡、选择器不溢出、Table 可滚动、具体值遮盖状态清晰。

- [ ] **Step 7: 运行 Task 7 测试并提交**

Run:

```bash
swift test --package-path macos/OpenChronicleApp
bash scripts/build-macos-app.sh
```

Expected: Swift 全套与 release build/signing PASS。

Commit:

```bash
git add macos/OpenChronicleApp/Sources/OpenChronicleApp/Models/MainWindowNavigation.swift \
  macos/OpenChronicleApp/Sources/OpenChronicleApp/Views/MainWindowView.swift \
  macos/OpenChronicleApp/Sources/OpenChronicleApp/Views/SettingsView.swift \
  macos/OpenChronicleApp/Sources/OpenChronicleApp/Views/ProtectionDiagnosticsView.swift \
  macos/OpenChronicleApp/Sources/OpenChronicleApp/OpenChronicleApp.swift \
  macos/OpenChronicleApp/Tests/OpenChronicleAppTests/MainWindowNavigationTests.swift \
  macos/OpenChronicleApp/Tests/OpenChronicleAppTests/ConfigurationTests.swift
git commit -m "feat(macos): add protection diagnostics UI"
```

---

### Task 8: 隐私边界、文档、打包与安装验收

**Files:**
- Create: `tests/test_privacy_reason_boundaries.py`
- Modify: `tests/test_capture_scheduler_fts.py`
- Modify: `tests/test_privacy_diagnostics.py`
- Modify: `tests/test_daemon_protection.py`
- Modify: `docs/config.md`
- Modify: `docs/capture.md`
- Modify: `docs/macos-app.md`
- Modify: `docs/superpowers/specs/2026-08-21-privacy-protection-indicators-design.md`
- Modify: `docs/superpowers/specs/2026-08-21-privacy-protection-indicators-design.zh-CN.md`
- Modify: `install.sh` only if the new helper source is not already covered by Task 3 runtime compilation

**Interfaces:**
- Consumes: all Tasks 1-7 interfaces
- Produces: end-to-end proof that exact reason values remain diagnostics-only
- Produces: installed signed app/backend with defaults `hybrid/exact/hover`

- [ ] **Step 1: 写跨边界泄漏 RED 测试**

创建 `tests/test_privacy_reason_boundaries.py`，使用唯一 marker：

该文件定义本地 `_SafeAXProvider`（只返回固定 safe AX JSON）、`_StaticProtectionMonitor`（按顺序
返回给定 decision）、`_private_other_display_decision(marker)`（display 1 active safe、display 2
protected 且 reason exact 含 marker）和 `_search_capture_fts(query)`（通过 `fts.cursor()` 调用
`search_captures`）。不得 import 其他测试模块的 private helper。

```python
def test_exact_reason_never_enters_capture_or_fts(ac_root: Path, monkeypatch) -> None:
    marker = "private-reason-marker"
    monitor = _StaticProtectionMonitor(_private_other_display_decision(marker))
    monkeypatch.setattr(scheduler.screenshot, "grab_many", lambda **_kwargs: [])
    out = scheduler._build_capture(
        CaptureConfig(
            screenshot_monitor="separate",
            privacy_reason_display="hybrid",
            privacy_reason_detail="exact",
        ),
        _SafeAXProvider(),
        None,
        protection_monitor=monitor,
    )
    assert out is not None
    assert marker not in json.dumps(out, ensure_ascii=False)
    path = scheduler._write_capture(out)
    assert marker not in path.read_text()
    assert _search_capture_fts(marker) == []
```

另加断言：transition/capture logs 无 marker；category socket snapshot 无 marker；无 lease exact 请求
被拒绝；model-failure event、MCP server tool/resource 列表和普通 status JSON 不含 reason exact 字段；
diagnostics guard JSON 无 marker。

- [ ] **Step 2: 写 generation/race RED 集成测试**

覆盖这些顺序：

- privacy window 在 initial decision 后出现，post-AX refresh 丢弃 capture 且最新 reasons 正确；
- diagnostics lease 在 AX 期间保护 active display，整份 in-memory AX 丢弃；
- window move 期间 old+new 两屏同时受保护；
- stale release 不能释放新 lease；
- config `all` 下任何 diagnostics lease 都不产生 screenshot；
- indicator acknowledgement failure 进入固定 `indicator_unconfirmed` 诊断，日志不含 exact values。

- [ ] **Step 3: 运行边界测试并修正任何真实缺口**

Run:

```bash
PYTHONPATH=src uv run pytest -q \
  tests/test_privacy_reason_boundaries.py \
  tests/test_capture_scheduler_fts.py \
  tests/test_privacy_diagnostics.py \
  tests/test_daemon_protection.py
```

Expected: 在实现完成前新增端到端断言失败；修正只能发生在拥有该边界的模块，禁止以删除 marker
断言或 broad mock 让测试通过。

- [ ] **Step 4: 更新用户文档与旧安全约束**

在 `docs/config.md` 记录三个值、默认值、`style=off`/diagnostics-only 交互和 invalid fallback。
在 `docs/capture.md` 记录原因来源、每屏组合、Unix Socket、租约顺序以及 specific values 不进入
pipeline。在 `docs/macos-app.md` 记录 Protection Diagnostics 页面、hover 穿透、click hit target、
exact/tiered 行为和断连保护。

旧 indicator 中英文 design 目前声明 overlay IPC 永远没有 app/title；改成：默认 exact 时可在用户
批准的受保护显示器 IPC 中包含 bounded exact fields，但不得记录、持久化或发送到未受保护显示器。
不得削弱其他既有隐私声明。

- [ ] **Step 5: 运行完整自动化验证**

Run:

```bash
PYTHONPATH=src uv run pytest -q
git diff --name-only "$(git merge-base main HEAD)"..HEAD -- '*.py' \
  | xargs uv run ruff check
swift test --package-path macos/OpenChronicleApp
swiftc resources/mac-privacy-overlay-reason.swift \
  resources/mac-privacy-overlay-core.swift \
  tests/swift/MacPrivacyOverlayCoreTests.swift \
  -o /tmp/openchronicle-overlay-reason-tests -framework AppKit
/tmp/openchronicle-overlay-reason-tests
bash resources/build-mac-window-list.sh
bash resources/build-mac-privacy-overlay.sh
swiftc tests/swift/MacPrivacyOverlayProtocolTests.swift \
  -o /tmp/openchronicle-overlay-protocol-tests
/tmp/openchronicle-overlay-protocol-tests resources/mac-privacy-overlay
uv build
bash scripts/build-macos-app.sh
git diff --check
```

Expected: Python/Swift/helper/protocol/wheel/sdist/signed app/whitespace 全部 PASS。

双架构验证使用：

```bash
swiftc resources/mac-window-list-core.swift resources/mac-window-list.swift \
  -target arm64-apple-macos12.0 -framework AppKit -o /tmp/mac-window-list-arm64
swiftc resources/mac-window-list-core.swift resources/mac-window-list.swift \
  -target x86_64-apple-macos12.0 -framework AppKit -o /tmp/mac-window-list-x86_64
swiftc resources/mac-privacy-overlay-reason.swift \
  resources/mac-privacy-overlay-core.swift resources/mac-privacy-overlay.swift \
  -target arm64-apple-macos12.0 -framework AppKit -o /tmp/mac-privacy-overlay-arm64
swiftc resources/mac-privacy-overlay-reason.swift \
  resources/mac-privacy-overlay-core.swift resources/mac-privacy-overlay.swift \
  -target x86_64-apple-macos12.0 -framework AppKit -o /tmp/mac-privacy-overlay-x86_64
```

wheel 隔离安装验证使用：

```bash
VERIFY_DIR="$(mktemp -d)"
python3 -m zipfile -l dist/openchronicle-0.1.0-py3-none-any.whl \
  | rg 'mac-privacy-overlay-reason.swift|privacy_diagnostics.py|privacy_diagnostics_guard.py|build-mac-privacy-overlay.sh'
uv venv "$VERIFY_DIR/venv" --python 3.12
uv pip install --python "$VERIFY_DIR/venv/bin/python" dist/openchronicle-0.1.0-py3-none-any.whl
cd /private/tmp
OPENCHRONICLE_ROOT="$VERIFY_DIR/root" "$VERIFY_DIR/venv/bin/python" -c '
from openchronicle.capture.privacy import _resolve_window_list_path
from openchronicle.capture.privacy_overlay import _resolve_overlay_path
assert _resolve_window_list_path() is not None
assert _resolve_overlay_path() is not None
'
```

从 `/private/tmp` 运行可证明 resolver 没有从 repo source fallback。wheel 列表必须包含 reason Swift
source、diagnostics/guard Python modules 和 build script。

- [ ] **Step 6: 提交边界测试和文档**

```bash
git add tests/test_privacy_reason_boundaries.py \
  tests/test_capture_scheduler_fts.py tests/test_privacy_diagnostics.py \
  tests/test_daemon_protection.py docs/config.md docs/capture.md docs/macos-app.md \
  docs/superpowers/specs/2026-08-21-privacy-protection-indicators-design.md \
  docs/superpowers/specs/2026-08-21-privacy-protection-indicators-design.zh-CN.md \
  install.sh
git commit -m "docs: ship privacy protection diagnostics"
```

如果 `install.sh` 未修改，不得为了匹配命令制造空白 churn；从 `git add` 中省略即可。

- [ ] **Step 7: 安装 final backend 和签名 app**

正常退出 OpenChronicle.app，确认旧 app/daemon/watcher/overlay/socket 全部退出，再执行：

```bash
bash install.sh --no-client-config
bash scripts/install-macos-app.sh
```

验证 source/site-packages 关键 Python 与 Swift source SHA-256 一致；进程树必须恰好一条
`OpenChronicle.app -> daemon -> one AX watcher + one overlay helper`；socket 位于 mode 0700 runtime
目录且自身 0600；status 为 active/healthy。

- [ ] **Step 8: 执行不含真实隐私数据的多屏视觉验收**

只使用空白 Edge InPrivate 窗口和测试 marker：

1. 默认 `hybrid/exact/hover`：状态常驻，鼠标移入后显示具体 title/rule，移出隐藏；浮层保持
   穿透，底层 hover/click 行为不变。
2. 切换 click：只有小 hit target 消费点击，屏幕其他区域穿透；窗口不激活、不抢焦点。
3. 切换 always/category/tiered 与 overlay/diagnostics/hybrid，确认所有组合和 style off fallback。
4. 诊断页显示 exact 前保护其所在屏幕；`separate` 仍保存另一屏。把诊断窗口移到另一屏，确认
   先遮盖、old+new 过渡保护、再显示；关闭页面后恢复。
5. `all` 模式下诊断租约省略完整截图；恢复 `separate`。
6. 临时终止 diagnostics socket/overlay helper，确认 exact 遮盖、guard 保留、helper 重启和安全恢复。
7. 解析 capture JSON 只输出结构字段，证明受保护屏幕和 AX 被排除且 marker 不存在；恢复后双屏
   与 AX 正常。
8. 扫描 privacy-only logs、capture-buffer、index、timeline、memory 和 model-failure events，marker
   计数必须为 0。

测试后关闭所有临时 InPrivate 窗口，删除测试 guard/lease（只能通过正常 release），恢复默认
`hybrid/exact/hover`、`separate`、`skip-monitor`、fail-closed true。不得使用真实密码、token 或
私人窗口进行验收。

- [ ] **Step 9: 最终独立审查门**

最终 review 必须特别检查：

- 原因与 screenshot/AX gate 来源相同；
- exact 只发送到已保护 overlay display 或有确认 lease 的诊断 client；
- move/release/disconnect/daemon restart 不产生 fail-open 窗口；
- hover 保持穿透，click 只拦截 hit target，panel 不激活；
- 任何日志/持久化/model/MCP 路径都没有 exact values；
- 默认值和 UI 组合严格为 `hybrid/exact/hover`；
- 完整测试、双架构、打包、签名、安装和 live evidence 齐全。
