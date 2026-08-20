# 隐私保护标识实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在每块被隐私防护实际排除的显示器上显示可配置原生标识，并让同一份保护快照同时控制 AX gate、截图筛选和浮层确认。

**Architecture:** daemon 持有 `PrivacyProtectionMonitor`，统一枚举窗口与显示器、计算不可变 `ProtectionSnapshot`、监管独立 Swift 浮层 helper，并在截图前等待对应 generation 的显示确认。菜单栏应用只负责设置编辑；浮层 helper 的生命周期完全属于 daemon，因此菜单栏应用退出后标识仍然工作。

**Tech Stack:** Python 3.11+、Swift 5 / AppKit、CoreGraphics、macOS Accessibility API、pytest、Swift XCTest/编译型测试、TOML 配置编辑器。

**Design Specs:** `docs/superpowers/specs/2026-08-21-privacy-protection-indicators-design.md`；中文复核版为 `docs/superpowers/specs/2026-08-21-privacy-protection-indicators-design.zh-CN.md`。

## Global Constraints

- 支持 macOS 13+，同时编译 arm64 与 x86_64 target。
- 不新增 Python 第三方依赖。
- 样式配置值固定为 `off`、`border`、`shield`、`pill`、`quiet-shield`、`banner`，默认 `pill`。
- 浮层必须鼠标穿透、不能获取键盘焦点、不能成为 key/main window，并支持多 Space 与全屏应用。
- 浮层 IPC 和日志不得包含 denylist 值、窗口标题、应用名称、bundle identifier 或屏幕内容。
- 每次截图必须使用与浮层确认相同 generation 的保护快照；无法确认时 fail closed。
- 标记显示器上的活动窗口不得产生 AX Tree、focused value、visible text 或 URL；无法确定活动窗口所在显示器时保守地阻止 AX。
- 所有生产代码遵循 RED → GREEN → REFACTOR；每个任务先运行并观察预期失败测试。
- 不暂存或提交 `.superpowers/` 可视化会话目录。

---

## 文件结构

### 新建文件

- `src/openchronicle/capture/protection.py`：纯数据模型、显示器映射和保护快照构建。
- `src/openchronicle/capture/privacy_overlay.py`：Swift helper 路径解析、进程监管、NDJSON 命令与 generation 确认。
- `src/openchronicle/capture/protection_monitor.py`：watchdog、事件刷新、暂停检测、样式热加载和线程安全快照。
- `resources/mac-privacy-overlay-core.swift`：IPC 模型、视觉 presentation、不可激活 panel controller。
- `resources/mac-privacy-overlay.swift`：helper 主循环和 stdin/stdout 协议。
- `resources/build-mac-privacy-overlay.sh`：开发环境 helper 构建脚本。
- `tests/swift/MacPrivacyOverlayCoreTests.swift`：无敏感数据协议与 presentation 编译型测试。
- `tests/test_protection.py`：保护快照与多屏映射测试。
- `tests/test_privacy_overlay.py`：Python overlay client 测试。
- `tests/test_protection_monitor.py`：monitor 生命周期、状态和热加载测试。
- `macos/OpenChronicleApp/Sources/OpenChronicleApp/Models/PrivacyIndicatorStyle.swift`：设置页样式选项模型。
- `macos/OpenChronicleApp/Tests/OpenChronicleAppTests/PrivacyIndicatorStyleTests.swift`：样式选项模型测试。

### 修改文件

- `src/openchronicle/config.py`、`src/openchronicle/config_editor.py`：新增配置、校验和 secret-safe snapshot。
- `src/openchronicle/capture/privacy.py`、`resources/mac-window-list.swift`：返回显示器列表和活动窗口标记。
- `src/openchronicle/capture/scheduler.py`、`src/openchronicle/daemon.py`：接入保护 monitor、AX gate、截图 generation gate。
- `macos/OpenChronicleApp/Sources/OpenChronicleApp/Models/Configuration.swift`：设置 snapshot/draft/patch 数据链路。
- `macos/OpenChronicleApp/Sources/OpenChronicleApp/Views/SettingsView.swift`：六选一视觉选择器。
- `install.sh`、`pyproject.toml`、`.gitignore`：打包、编译和忽略 helper binary。
- `docs/config.md`、`docs/capture.md`、`docs/macos-app.md`：配置、安全语义和验收说明。

---

### Task 1: 配置值与配置编辑器数据链路

**Files:**
- Modify: `src/openchronicle/config.py:33-82`
- Modify: `src/openchronicle/config.py:270-290`
- Modify: `src/openchronicle/config_editor.py:43-74`
- Modify: `src/openchronicle/config_editor.py:165-221`
- Modify: `src/openchronicle/config_editor.py:351-370`
- Test: `tests/test_config.py`
- Test: `tests/test_cli_config_editor.py`

**Interfaces:**
- Produces: `PRIVACY_INDICATOR_STYLES: frozenset[str]`
- Produces: `CaptureConfig.privacy_indicator_style: str`
- Produces: secret-safe config snapshot field `values.capture.privacy_indicator_style`
- Consumes: existing `CaptureConfig.__post_init__` normalization and config-editor patch flow

- [ ] **Step 1: 写默认值和配置编辑器失败测试**

在 `tests/test_config.py` 添加：

```python
def test_capture_privacy_indicator_style_config(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[capture]\nprivacy_indicator_style = "SHIELD"\n')
    assert config.load(path).capture.privacy_indicator_style == "shield"

    path.write_text('[capture]\nprivacy_indicator_style = "unknown"\n')
    assert config.load(path).capture.privacy_indicator_style == "pill"
    assert config.load(tmp_path / "missing.toml").capture.privacy_indicator_style == "pill"
```

在 `tests/test_cli_config_editor.py` 添加：

```python
def test_config_indicator_style_is_editable_and_validated(ac_root: Path) -> None:
    path = ac_root / "config.toml"
    path.write_text('[capture]\nprivacy_indicator_style = "pill"\n')
    runner = CliRunner()
    _, snapshot = _invoke_json(runner, ["config", "--json"])
    assert snapshot["values"]["capture"]["privacy_indicator_style"] == "pill"

    result, payload = _invoke_json(
        runner,
        ["config", "--patch-json"],
        {
            "expected_sha256": snapshot["sha256"],
            "updates": {"capture.privacy_indicator_style": "border"},
        },
    )
    assert result.exit_code == 0, result.output
    assert tomllib.loads(path.read_text())["capture"]["privacy_indicator_style"] == "border"

    result, payload = _invoke_json(
        runner,
        ["config", "--validate-json"],
        {"content": '[capture]\nprivacy_indicator_style = "invalid"\n'},
    )
    assert result.exit_code == 2
    assert "privacy_indicator_style" in payload["error"]
```

- [ ] **Step 2: 运行测试并确认 RED**

Run:

```bash
uv run pytest tests/test_config.py::test_capture_privacy_indicator_style_config tests/test_cli_config_editor.py::test_config_indicator_style_is_editable_and_validated -v
```

Expected: 第一个测试因 `CaptureConfig` 没有 `privacy_indicator_style` 失败；第二个测试因 patch path 不可编辑或 snapshot 缺字段失败。

- [ ] **Step 3: 实现配置和严格校验**

在 `src/openchronicle/config.py` 定义并使用：

```python
PRIVACY_INDICATOR_STYLES = frozenset(
    {"off", "border", "shield", "pill", "quiet-shield", "banner"}
)

@dataclass
class CaptureConfig:
    privacy_indicator_style: str = "pill"

    def __post_init__(self) -> None:
        indicator_style = str(self.privacy_indicator_style or "pill").strip().lower()
        self.privacy_indicator_style = (
            indicator_style if indicator_style in PRIVACY_INDICATOR_STYLES else "pill"
        )
```

同时在默认 TOML 的 `[capture]` 中加入：

```toml
privacy_indicator_style = "pill"       # off, border, shield, pill, quiet-shield, or banner
```

在 `src/openchronicle/config_editor.py`：

在 `EDITABLE_PATHS` 的 capture 项中插入：

```python
"capture.privacy_indicator_style",

indicator_style = _require_type(
    capture,
    "privacy_indicator_style",
    str,
    "capture.privacy_indicator_style",
)
if indicator_style is not None and indicator_style.lower() not in PRIVACY_INDICATOR_STYLES:
    raise ConfigEditorError(
        "capture.privacy_indicator_style must be off, border, shield, pill, quiet-shield, or banner"
    )
```

把 `cfg.capture.privacy_indicator_style` 加入 secret-safe snapshot 的 capture payload。

- [ ] **Step 4: 运行配置测试并确认 GREEN**

Run:

```bash
uv run pytest tests/test_config.py tests/test_cli_config_editor.py -q
```

Expected: 两个文件全部 PASS。

- [ ] **Step 5: 提交配置改动**

```bash
git add src/openchronicle/config.py src/openchronicle/config_editor.py tests/test_config.py tests/test_cli_config_editor.py
git commit -m "feat(config): add privacy indicator style"
```

---

### Task 2: 窗口 inventory 与权威保护快照

**Files:**
- Modify: `src/openchronicle/capture/privacy.py`
- Modify: `resources/mac-window-list.swift`
- Create: `src/openchronicle/capture/protection.py`
- Create: `tests/test_protection.py`
- Modify: `tests/test_capture_privacy.py`

**Interfaces:**
- Produces: `privacy.DisplayInfo(id: int, region: ScreenRegion, is_primary: bool)`
- Produces: `privacy.VisibleWindow(app_name, bundle_id, title, region, is_active=False)`
- Produces: `privacy.WindowInventory(windows: tuple[VisibleWindow, ...], displays: tuple[DisplayInfo, ...])`
- Produces: `privacy._run_window_list_helper() -> dict[str, Any] | None`
- Produces: `privacy.read_window_inventory() -> WindowInventory | None`
- Produces: `ProtectionState`, `ProtectionSnapshot`, `build_protection_snapshot(cfg, inventory, paused, generation, now)`
- Consumes: `CaptureConfig.screenshot_monitor`, app/bundle/title denylist fields

- [ ] **Step 1: 写多屏映射和 AX fail-closed 失败测试**

创建 `tests/test_protection.py`：

```python
from openchronicle.capture.privacy import DisplayInfo, ScreenRegion, VisibleWindow, WindowInventory
from openchronicle.capture.protection import ProtectionState, build_protection_snapshot
from openchronicle.config import CaptureConfig


LEFT = DisplayInfo(1, ScreenRegion(0, 0, 100, 100), True)
RIGHT = DisplayInfo(2, ScreenRegion(100, 0, 100, 100), False)


def test_separate_marks_only_sensitive_display_and_blocks_ax_there() -> None:
    cfg = CaptureConfig(
        screenshot_monitor="separate",
        deny_window_title_patterns=["InPrivate"],
    )
    inventory = WindowInventory(
        windows=(
            VisibleWindow(
                "Microsoft Edge", "com.microsoft.edgemac", "InPrivate",
                ScreenRegion(110, 0, 80, 90), False,
            ),
            VisibleWindow(
                "Cursor", "com.cursor.Cursor", "main.py",
                ScreenRegion(115, 5, 70, 80), True,
            ),
        ),
        displays=(LEFT, RIGHT),
    )

    snapshot = build_protection_snapshot(cfg, inventory, paused=False, generation=7, now=10.0)

    assert snapshot.state is ProtectionState.PROTECTED
    assert snapshot.protected_display_ids == frozenset({2})
    assert snapshot.active_display_id == 2
    assert snapshot.ax_blocked is True


def test_all_marks_every_display_and_unknown_active_display_blocks_ax() -> None:
    cfg = CaptureConfig(
        screenshot_monitor="all",
        deny_window_title_patterns=["Private"],
    )
    inventory = WindowInventory(
        windows=(
            VisibleWindow("Edge", "edge", "Private", ScreenRegion(110, 0, 80, 90), False),
        ),
        displays=(LEFT, RIGHT),
    )

    snapshot = build_protection_snapshot(cfg, inventory, paused=False, generation=8, now=11.0)

    assert snapshot.protected_display_ids == frozenset({1, 2})
    assert snapshot.active_display_id is None
    assert snapshot.ax_blocked is True
```

在 `tests/test_capture_privacy.py` 添加 helper JSON 解析测试，要求 `read_window_inventory()` 能解析 `displays` 和 `is_active`。

- [ ] **Step 2: 运行测试并确认 RED**

Run:

```bash
uv run pytest tests/test_protection.py tests/test_capture_privacy.py -q
```

Expected: collection 阶段因 `DisplayInfo`、`WindowInventory` 和 `protection` 模块不存在而失败。

- [ ] **Step 3: 扩展 Swift inventory 输出**

在 `resources/mac-window-list.swift` 增加：

```swift
struct DisplayRecord: Codable {
    let id: UInt32
    let left: Double
    let top: Double
    let width: Double
    let height: Double
    let is_primary: Bool
}

struct WindowRecord: Codable {
    let app_name: String
    let bundle_id: String
    let title: String
    let left: Double
    let top: Double
    let width: Double
    let height: Double
    let is_active: Bool
}

struct Output: Codable {
    let windows: [WindowRecord]
    let displays: [DisplayRecord]
}
```

使用 `CGGetActiveDisplayList`、`CGDisplayBounds` 和 `CGDisplayIsMain` 枚举显示器。使用
`NSWorkspace.shared.frontmostApplication?.processIdentifier` 与该 app 的
`kAXFocusedWindowAttribute`，只把真正前台的 focused window 标记为 `is_active = true`。
CoreGraphics-only 记录设为 `false`；AX 顶层窗口记录携带真实值。

- [ ] **Step 4: 实现 Python inventory 与纯快照构建器**

在 `privacy.py` 保留 `list_visible_windows()` 兼容包装，并新增：

```python
@dataclass(frozen=True)
class DisplayInfo:
    id: int
    region: ScreenRegion
    is_primary: bool = False


@dataclass(frozen=True)
class VisibleWindow:
    app_name: str
    bundle_id: str
    title: str
    region: ScreenRegion
    is_active: bool = False


@dataclass(frozen=True)
class WindowInventory:
    windows: tuple[VisibleWindow, ...]
    displays: tuple[DisplayInfo, ...]


def read_window_inventory() -> WindowInventory | None:
    raw = _run_window_list_helper()
    if raw is None:
        return None
    try:
        windows = tuple(_parse_visible_window(row) for row in raw["windows"])
        displays = tuple(_parse_display(row) for row in raw["displays"])
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning("invalid visible-window helper output: %s", exc)
        return None
    return WindowInventory(windows=windows, displays=displays)


def _parse_display(row: Any) -> DisplayInfo:
    if not isinstance(row, dict):
        raise TypeError("display is not an object")
    return DisplayInfo(
        id=int(row["id"]),
        region=ScreenRegion(
            left=float(row["left"]),
            top=float(row["top"]),
            width=float(row["width"]),
            height=float(row["height"]),
        ),
        is_primary=bool(row.get("is_primary")),
    )


def list_visible_windows() -> list[VisibleWindow] | None:
    inventory = read_window_inventory()
    return list(inventory.windows) if inventory is not None else None
```

在 `protection.py` 实现：

```python
class ProtectionState(StrEnum):
    INACTIVE = "inactive"
    PROTECTED = "protected"
    PAUSED = "paused"
    FAILED = "failed"


@dataclass(frozen=True)
class ProtectionSnapshot:
    generation: int
    state: ProtectionState
    capture_mode: str
    indicator_style: str
    displays: tuple[DisplayInfo, ...]
    protected_display_ids: frozenset[int]
    active_display_id: int | None
    created_monotonic: float
    fresh_until: float

    @property
    def protected_regions(self) -> list[ScreenRegion]:
        return [
            display.region
            for display in self.displays
            if display.id in self.protected_display_ids
        ]

    @property
    def ax_blocked(self) -> bool:
        if self.state is ProtectionState.FAILED:
            return True
        if not self.protected_display_ids:
            return False
        return (
            self.active_display_id is None
            or self.active_display_id in self.protected_display_ids
        )
```

`build_protection_snapshot()` 对窗口与显示器做严格矩形相交，核心分支按以下代码实现；`_display_for_active_window` 在跨屏时选择相交面积最大的显示器：

```python
SNAPSHOT_FRESH_SECONDS = 0.25


def _intersection_area(left: ScreenRegion, right: ScreenRegion) -> float:
    width = max(
        0.0,
        min(left.left + left.width, right.left + right.width) - max(left.left, right.left),
    )
    height = max(
        0.0,
        min(left.top + left.height, right.top + right.height) - max(left.top, right.top),
    )
    return width * height


def _regions_intersect(left: ScreenRegion, right: ScreenRegion) -> bool:
    return _intersection_area(left, right) > 0


def _display_for_active_window(
    window: VisibleWindow | None,
    displays: tuple[DisplayInfo, ...],
) -> int | None:
    if window is None:
        return None
    areas = [(_intersection_area(window.region, display.region), display.id) for display in displays]
    area, display_id = max(areas, default=(0.0, -1))
    return display_id if area > 0 else None


def build_protection_snapshot(
    cfg: CaptureConfig,
    inventory: WindowInventory | None,
    *,
    paused: bool,
    generation: int,
    now: float,
) -> ProtectionSnapshot:
    displays = inventory.displays if inventory is not None else ()
    all_ids = frozenset(display.id for display in displays)
    active_window = (
        next((window for window in inventory.windows if window.is_active), None)
        if inventory is not None
        else None
    )
    active_display_id = _display_for_active_window(active_window, displays)

    if paused:
        state = ProtectionState.PAUSED
        protected_ids = all_ids
    elif inventory is None:
        state = ProtectionState.FAILED
        protected_ids = frozenset()
    else:
        sensitive_regions = [
            window.region
            for window in inventory.windows
            if privacy.visible_window_denylist_reason(cfg, window) is not None
        ]
        matched_ids = frozenset(
            display.id
            for display in displays
            if any(_regions_intersect(display.region, region) for region in sensitive_regions)
        )
        state = ProtectionState.PROTECTED if matched_ids else ProtectionState.INACTIVE
        protected_ids = all_ids if matched_ids and cfg.screenshot_monitor == "all" else matched_ids

    return ProtectionSnapshot(
        generation=generation,
        state=state,
        capture_mode=cfg.screenshot_monitor,
        indicator_style=cfg.privacy_indicator_style,
        displays=displays,
        protected_display_ids=protected_ids,
        active_display_id=active_display_id,
        created_monotonic=now,
        fresh_until=now + SNAPSHOT_FRESH_SECONDS,
    )
```

- [ ] **Step 5: 编译 window helper 并运行测试**

Run:

```bash
bash resources/build-mac-window-list.sh
uv run pytest tests/test_protection.py tests/test_capture_privacy.py -q
```

Expected: Swift helper 编译成功，pytest 全部 PASS。

- [ ] **Step 6: 提交 inventory 与快照模型**

```bash
git add resources/mac-window-list.swift src/openchronicle/capture/privacy.py src/openchronicle/capture/protection.py tests/test_capture_privacy.py tests/test_protection.py
git commit -m "feat(capture): add authoritative protection snapshots"
```

---

### Task 3: 原生浮层 helper 与视觉 presentation

**Files:**
- Create: `resources/mac-privacy-overlay-core.swift`
- Create: `resources/mac-privacy-overlay.swift`
- Create: `resources/build-mac-privacy-overlay.sh`
- Create: `tests/swift/MacPrivacyOverlayCoreTests.swift`

**Interfaces:**
- Consumes NDJSON: `OverlayCommand(generation, state, style, displays, all_displays)`
- Produces NDJSON: `OverlayAcknowledgement(generation, rendered, error)`
- Produces process: `resources/mac-privacy-overlay`
- Does not consume any window title, app name, bundle ID, denylist value, or screen pixel

- [ ] **Step 1: 写 Swift core 失败测试**

创建 `tests/swift/MacPrivacyOverlayCoreTests.swift`，使用 `@main` 避免多文件顶层代码：

```swift
import AppKit
import Foundation

@main
enum MacPrivacyOverlayCoreTests {
    static func main() throws {
        let raw = Data(#"{"generation":9,"state":"protected","style":"pill","displays":[],"all_displays":false}"#.utf8)
        let command = try JSONDecoder().decode(OverlayCommand.self, from: raw)
        precondition(command.generation == 9)
        precondition(command.style == .pill)

        let protectedPresentation = IndicatorPresentation.make(state: .protected, style: .pill)
        precondition(protectedPresentation.text == "已保护")
        precondition(protectedPresentation.symbolName == "checkmark.shield.fill")

        let paused = IndicatorPresentation.make(state: .paused, style: .shield)
        precondition(paused.text == nil)
        precondition(paused.symbolName == "pause.fill")

        let panel = PrivacyOverlayPanel(contentRect: .zero)
        precondition(panel.ignoresMouseEvents)
        precondition(!panel.canBecomeKey)
        precondition(!panel.canBecomeMain)
        print("MacPrivacyOverlayCoreTests passed")
    }
}
```

- [ ] **Step 2: 编译测试并确认 RED**

Run:

```bash
swiftc resources/mac-privacy-overlay-core.swift tests/swift/MacPrivacyOverlayCoreTests.swift -o /tmp/openchronicle-overlay-core-tests -framework AppKit
```

Expected: FAIL，提示 `resources/mac-privacy-overlay-core.swift` 不存在或测试类型未定义。

- [ ] **Step 3: 实现协议、presentation 和 panel controller**

在 `mac-privacy-overlay-core.swift` 定义：

```swift
enum IndicatorState: String, Codable { case inactive, protected, paused, failed }
enum IndicatorStyle: String, Codable { case off, border, shield, pill, quietShield = "quiet-shield", banner }

struct OverlayDisplay: Codable, Hashable {
    let id: UInt32
    let left: Double
    let top: Double
    let width: Double
    let height: Double
}

struct OverlayCommand: Codable {
    let generation: Int
    let state: IndicatorState
    let style: IndicatorStyle
    let displays: [OverlayDisplay]
    let allDisplays: Bool
    enum CodingKeys: String, CodingKey {
        case generation, state, style, displays
        case allDisplays = "all_displays"
    }
}

struct OverlayAcknowledgement: Codable {
    let generation: Int
    let rendered: Bool
    let error: String?
}
```

`IndicatorPresentation.make` 固定映射：protected 为绿色、paused 为灰色、failed 为黄色；含文字样式分别使用“已保护”“已暂停”“截图已停用”。实现 `PrivacyOverlayPanel` 覆盖 `canBecomeKey`、`canBecomeMain` 返回 `false`，初始化时设置 `ignoresMouseEvents = true`、`.nonactivatingPanel`、`.canJoinAllSpaces`、`.fullScreenAuxiliary`、透明背景和合适 window level。

`PrivacyOverlayController.apply(_:)` 必须：

- 根据 display ID 创建/更新/移除 panel；
- `all_displays = true` 且 command 无 displays 时使用 `NSScreen.screens`；
- B1/B2/B3 锚定 `visibleFrame` 右下角；A/C 使用完整 frame；
- inactive/off 清除全部 panel；
- 完成主线程更新后才调用 acknowledgement closure。

- [ ] **Step 4: 实现 helper 主循环和构建脚本**

`mac-privacy-overlay.swift`：

```swift
NSApplication.shared.setActivationPolicy(.accessory)
let controller = PrivacyOverlayController()

DispatchQueue.global(qos: .userInitiated).async {
    while let line = readLine() {
        do {
            let command = try JSONDecoder().decode(OverlayCommand.self, from: Data(line.utf8))
            DispatchQueue.main.async {
                controller.apply(command) {
                    writeAcknowledgement(.init(generation: command.generation, rendered: true, error: nil))
                }
            }
        } catch {
            writeAcknowledgement(.init(generation: -1, rendered: false, error: "invalid-command"))
        }
    }
    DispatchQueue.main.async { NSApp.terminate(nil) }
}
NSApp.run()
```

构建脚本使用与其他 helper 相同的 macOS 12 target 下限：

```bash
swiftc \
  "${SCRIPT_DIR}/mac-privacy-overlay-core.swift" \
  "${SCRIPT_DIR}/mac-privacy-overlay.swift" \
  -o "${SCRIPT_DIR}/mac-privacy-overlay" \
  -O -target "${TARGET}" -swift-version 5 -framework AppKit
```

- [ ] **Step 5: 运行 Swift 测试和 helper 构建**

Run:

```bash
swiftc resources/mac-privacy-overlay-core.swift tests/swift/MacPrivacyOverlayCoreTests.swift -o /tmp/openchronicle-overlay-core-tests -framework AppKit
/tmp/openchronicle-overlay-core-tests
bash resources/build-mac-privacy-overlay.sh
```

Expected: 输出 `MacPrivacyOverlayCoreTests passed`，helper 编译成功。

- [ ] **Step 6: 提交原生 helper**

```bash
git add resources/mac-privacy-overlay-core.swift resources/mac-privacy-overlay.swift resources/build-mac-privacy-overlay.sh tests/swift/MacPrivacyOverlayCoreTests.swift
git commit -m "feat(macos): add privacy overlay helper"
```

---

### Task 4: Python 浮层 client 与 generation 确认

**Files:**
- Create: `src/openchronicle/capture/privacy_overlay.py`
- Create: `tests/test_privacy_overlay.py`

**Interfaces:**
- Consumes: `ProtectionSnapshot`
- Produces: `PrivacyOverlayClient.render(snapshot, timeout=0.5) -> bool`
- Produces: `PrivacyOverlayClient.clear(generation: int, timeout=0.5) -> bool`
- Produces: `PrivacyOverlayClient.close() -> None`
- Produces: `_resolve_overlay_path() -> Path | None`

- [ ] **Step 1: 写序列化、确认和超时失败测试**

创建 `tests/test_privacy_overlay.py`，通过注入 fake transport 测试真实 client 状态机：

```python
class FakeTransport:
    def __init__(self) -> None:
        self.writes: list[str] = []
        self.acknowledged: set[int] = set()
        self.closed = False

    def acknowledge(self, *, generation: int) -> None:
        self.acknowledged.add(generation)

    def write_line(self, line: str) -> None:
        self.writes.append(line)

    def wait_for_generation(self, generation: int, timeout: float) -> bool:
        return generation in self.acknowledged

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def snapshot() -> ProtectionSnapshot:
    right = DisplayInfo(2, ScreenRegion(100, 0, 100, 100), False)
    return ProtectionSnapshot(
        generation=12,
        state=ProtectionState.PROTECTED,
        capture_mode="separate",
        indicator_style="pill",
        displays=(right,),
        protected_display_ids=frozenset({2}),
        active_display_id=1,
        created_monotonic=1.0,
        fresh_until=1.25,
    )


@pytest.fixture
def fake_transport() -> FakeTransport:
    return FakeTransport()


def test_overlay_command_contains_only_geometry_and_state(snapshot, fake_transport) -> None:
    client = PrivacyOverlayClient(transport_factory=lambda: fake_transport)
    fake_transport.acknowledge(generation=snapshot.generation)

    assert client.render(snapshot, timeout=0.1) is True
    payload = json.loads(fake_transport.writes[-1])
    assert payload == {
        "generation": snapshot.generation,
        "state": "protected",
        "style": "pill",
        "displays": [
            {"id": 2, "left": 100, "top": 0, "width": 100, "height": 100}
        ],
        "all_displays": False,
    }
    serialized = fake_transport.writes[-1]
    assert "InPrivate" not in serialized
    assert "Microsoft Edge" not in serialized


def test_wrong_generation_or_timeout_is_not_confirmed(snapshot, fake_transport) -> None:
    client = PrivacyOverlayClient(transport_factory=lambda: fake_transport)
    fake_transport.acknowledge(generation=snapshot.generation - 1)
    assert client.render(snapshot, timeout=0.01) is False
```

还要覆盖 malformed acknowledgement、child exit、`off` 不启动进程、`close()` 清理 reader thread。

- [ ] **Step 2: 运行测试并确认 RED**

Run:

```bash
uv run pytest tests/test_privacy_overlay.py -v
```

Expected: import 阶段因 `privacy_overlay.py` 不存在而失败。

- [ ] **Step 3: 实现 helper resolver 和 client**

实现以下边界：

```python
class OverlayTransport(Protocol):
    def write_line(self, line: str) -> None:
        raise NotImplementedError

    def wait_for_generation(self, generation: int, timeout: float) -> bool:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError
```

`PrivacyOverlayClient` 的 public 方法固定为 `render(snapshot, timeout=0.5) -> bool`、`clear(generation, timeout=0.5) -> bool`、`close() -> None`。默认 transport 使用 `subprocess.Popen([str(helper_path)], stdin=PIPE, stdout=PIPE, text=True, bufsize=1)`；单独 reader thread 解析 acknowledgement 并通过 `Condition` 唤醒等待者。只接受 `rendered=true` 且 generation 精确匹配。进程退出、write error、malformed JSON 和 timeout 都返回 `False`，并清理 transport；下一次调用按 1s、2s、4s、最高 30s 的有界退避重启。

序列化时只发送 `snapshot.protected_display_ids` 对应的几何信息；`PAUSED` 和 `FAILED` 需要覆盖全部显示器。当 inventory 失败导致 display 列表为空时，发送 `all_displays=true`，由 Swift helper 使用自身的 `NSScreen.screens` 显示黄色状态。

`_resolve_overlay_path()` 依次检查 `OPENCHRONICLE_PRIVACY_OVERLAY_HELPER`、wheel `_bundled/mac-privacy-overlay`、开发 `resources/mac-privacy-overlay`，缺失或源码更新时编译 core + main。

- [ ] **Step 4: 运行 client 测试并确认 GREEN**

Run:

```bash
uv run pytest tests/test_privacy_overlay.py -q
```

Expected: 全部 PASS；测试输出和日志中没有敏感窗口字段。

- [ ] **Step 5: 提交 Python client**

```bash
git add src/openchronicle/capture/privacy_overlay.py tests/test_privacy_overlay.py
git commit -m "feat(capture): supervise privacy overlay helper"
```

---

### Task 5: daemon 保护 monitor、暂停状态和样式热加载

**Files:**
- Create: `src/openchronicle/capture/protection_monitor.py`
- Create: `tests/test_protection_monitor.py`

**Interfaces:**
- Consumes: `CaptureConfig`, `privacy.read_window_inventory`, `capture_is_paused`, `PrivacyOverlayClient`
- Produces: `ProtectionDecision(snapshot: ProtectionSnapshot, indicator_confirmed: bool)`
- Produces: `PrivacyProtectionMonitor(cfg, *, config_path, overlay, inventory_reader=read_window_inventory, pause_reader=capture_is_paused, watchdog_seconds=1.0)`
- Produces: `PrivacyProtectionMonitor.start()`, `stop()`, `request_refresh()`, `decision_for_capture(force=True)`
- Produces: one-second watchdog and config-mtime style reload

- [ ] **Step 1: 写状态转换、热加载和 helper failure 测试**

创建 `tests/test_protection_monitor.py`：

```python
class FakeOverlay:
    def __init__(self) -> None:
        self.render_result = True
        self.snapshots: list[ProtectionSnapshot] = []
        self.closed = False

    def render(self, snapshot: ProtectionSnapshot, timeout: float = 0.5) -> bool:
        self.snapshots.append(snapshot)
        return self.render_result

    def clear(self, generation: int, timeout: float = 0.5) -> bool:
        return self.render_result

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def inventory() -> WindowInventory:
    displays = (
        DisplayInfo(1, ScreenRegion(0, 0, 100, 100), True),
        DisplayInfo(2, ScreenRegion(100, 0, 100, 100), False),
    )
    windows = (
        VisibleWindow("Edge", "edge", "InPrivate", ScreenRegion(110, 0, 80, 90), False),
    )
    return WindowInventory(windows=windows, displays=displays)


@pytest.fixture
def fake_overlay() -> FakeOverlay:
    return FakeOverlay()


def make_monitor(
    *,
    inventory: WindowInventory,
    overlay: FakeOverlay,
    style: str = "pill",
    config_path: Path | None = None,
) -> PrivacyProtectionMonitor:
    cfg = CaptureConfig(
        screenshot_monitor="separate",
        privacy_indicator_style=style,
        deny_window_title_patterns=["InPrivate"],
    )
    return PrivacyProtectionMonitor(
        cfg,
        config_path=config_path or Path("/nonexistent/config.toml"),
        overlay=overlay,
        inventory_reader=lambda: inventory,
        pause_reader=lambda: False,
        watchdog_seconds=0.01,
    )


def test_monitor_renders_pause_on_all_displays(tmp_path, inventory, fake_overlay) -> None:
    cfg = CaptureConfig(privacy_indicator_style="pill")
    monitor = PrivacyProtectionMonitor(
        cfg,
        config_path=tmp_path / "config.toml",
        overlay=fake_overlay,
        inventory_reader=lambda: inventory,
        pause_reader=lambda: True,
        watchdog_seconds=0.01,
    )

    decision = monitor.decision_for_capture(force=True)

    assert decision.snapshot.state is ProtectionState.PAUSED
    assert decision.snapshot.protected_display_ids == frozenset({1, 2})
    assert decision.indicator_confirmed is True


def test_required_overlay_timeout_is_unconfirmed(inventory, fake_overlay) -> None:
    fake_overlay.render_result = False
    monitor = make_monitor(inventory=inventory, overlay=fake_overlay, style="pill")
    decision = monitor.decision_for_capture(force=True)
    assert decision.snapshot.state is ProtectionState.PROTECTED
    assert decision.indicator_confirmed is False


def test_style_hot_reload_changes_only_indicator_style(tmp_path, inventory, fake_overlay) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('[capture]\nprivacy_indicator_style = "shield"\n')
    monitor = make_monitor(config_path=config_path, inventory=inventory, overlay=fake_overlay)
    first = monitor.decision_for_capture(force=True)
    config_path.write_text('[capture]\nprivacy_indicator_style = "banner"\n')
    second = monitor.decision_for_capture(force=True)
    assert first.snapshot.indicator_style == "shield"
    assert second.snapshot.indicator_style == "banner"
    assert second.snapshot.capture_mode == first.snapshot.capture_mode
```

另加 start/stop 线程、`request_refresh()` 唤醒、inactive clear、failed yellow command、日志不包含窗口标题的测试。

- [ ] **Step 2: 运行测试并确认 RED**

Run:

```bash
uv run pytest tests/test_protection_monitor.py -v
```

Expected: import 阶段因 `protection_monitor.py` 不存在而失败。

- [ ] **Step 3: 实现线程安全 monitor**

实现固定接口：

```python
@dataclass(frozen=True)
class ProtectionDecision:
    snapshot: ProtectionSnapshot
    indicator_confirmed: bool


class PrivacyProtectionMonitor:
    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        self._thread.join(timeout=2.0)
        self._overlay.close()

    def request_refresh(self) -> None:
        self._wake.set()

    def decision_for_capture(self, *, force: bool = True) -> ProtectionDecision:
        if force:
            return self._refresh()
        with self._state_lock:
            current = self._decision
        if current is None or current.snapshot.fresh_until < time.monotonic():
            return self._refresh()
        return current
```

使用一个 daemon `threading.Thread`、一个 refresh `Lock` 和一个 wake `Event`。watchdog 每 1.0s 刷新，窗口事件调用 `request_refresh()`。`decision_for_capture(force=True)` 同步刷新并返回该次实际 render 的确认结果；generation 只在新快照发布时递增。

每次刷新顺序固定为：检查配置 mtime → 读取 pause → 枚举 inventory → 构建快照 → 调用 overlay。样式为 `off` 时，无论 protection state 是什么，都不启动 helper，并把 indicator acknowledgement 视为成功；AX 和截图防护仍按 snapshot 执行。日志只记录 generation、state、style、display IDs 和错误类型。

- [ ] **Step 4: 运行 monitor 测试并确认 GREEN**

Run:

```bash
uv run pytest tests/test_protection_monitor.py tests/test_protection.py tests/test_privacy_overlay.py -q
```

Expected: 全部 PASS，无线程泄漏或测试结束卡住。

- [ ] **Step 5: 提交 monitor**

```bash
git add src/openchronicle/capture/protection_monitor.py tests/test_protection_monitor.py
git commit -m "feat(capture): monitor privacy protection state"
```

---

### Task 6: AX gate、截图 gate 与 daemon 生命周期接入

**Files:**
- Modify: `src/openchronicle/capture/scheduler.py:35-112`
- Modify: `src/openchronicle/capture/scheduler.py:320-390`
- Modify: `src/openchronicle/daemon.py:51-120`
- Modify: `tests/test_capture_scheduler_fts.py`
- Create: `tests/test_daemon_protection.py`

**Interfaces:**
- Consumes: `PrivacyProtectionMonitor.decision_for_capture()` and `request_refresh()`
- Produces: `_build_capture(..., protection_monitor: PrivacyProtectionMonitor | None = None)`
- Produces: `run_forever(..., protection_monitor: PrivacyProtectionMonitor | None = None)`
- Produces: `daemon._build_protection_monitor(cfg: Config) -> PrivacyProtectionMonitor`
- Preserves: CLI/manual calls without monitor use the existing one-shot screenshot privacy guard

- [ ] **Step 1: 写 AX、截图和 lifecycle 失败测试**

在 `tests/test_capture_scheduler_fts.py` 添加：

```python
class _FakeProtectionMonitor:
    def __init__(self, decision: ProtectionDecision) -> None:
        self.decision = decision
        self.snapshot = decision.snapshot
        self.refresh_requests = 0

    def decision_for_capture(self, *, force: bool = True) -> ProtectionDecision:
        return self.decision

    def request_refresh(self) -> None:
        self.refresh_requests += 1


def _protected_decision(
    *,
    active_display_id: int | None,
    protected_ids: set[int],
    confirmed: bool,
) -> ProtectionDecision:
    displays = (
        DisplayInfo(1, ScreenRegion(0, 0, 100, 100), True),
        DisplayInfo(2, ScreenRegion(100, 0, 100, 100), False),
    )
    snapshot = ProtectionSnapshot(
        generation=20,
        state=ProtectionState.PROTECTED,
        capture_mode="separate",
        indicator_style="pill",
        displays=displays,
        protected_display_ids=frozenset(protected_ids),
        active_display_id=active_display_id,
        created_monotonic=time.monotonic(),
        fresh_until=time.monotonic() + 1.0,
    )
    return ProtectionDecision(snapshot=snapshot, indicator_confirmed=confirmed)


def _failed_decision() -> ProtectionDecision:
    snapshot = ProtectionSnapshot(
        generation=21,
        state=ProtectionState.FAILED,
        capture_mode="separate",
        indicator_style="pill",
        displays=(),
        protected_display_ids=frozenset(),
        active_display_id=None,
        created_monotonic=time.monotonic(),
        fresh_until=time.monotonic() + 1.0,
    )
    return ProtectionDecision(snapshot=snapshot, indicator_confirmed=True)


def test_protected_active_display_skips_ax_but_captures_safe_monitor(
    ac_root: Path, monkeypatch,
) -> None:
    provider = _FakeProvider(raw_json=_edge_ax_tree("https://safe.example"))
    monitor = _FakeProtectionMonitor(
        decision=_protected_decision(active_display_id=2, protected_ids={2}, confirmed=True)
    )
    screenshot_calls: list[dict] = []
    monkeypatch.setattr(
        scheduler_mod.window_meta,
        "active_window",
        lambda: window_meta.WindowMeta(app_name="Cursor", title="main.py", bundle_id="cursor"),
    )
    monkeypatch.setattr(
        scheduler_mod.screenshot,
        "grab_many",
        lambda **kwargs: screenshot_calls.append(kwargs) or [],
    )

    out = scheduler_mod._build_capture(
        CaptureConfig(screenshot_monitor="separate"),
        provider,
        {"event_type": "manual"},
        protection_monitor=monitor,
    )

    assert out is not None
    assert provider.calls == 0
    assert "ax_tree" not in out
    assert "visible_text" not in out
    assert out["ax_skipped"] == "protected_display"
    assert screenshot_calls[0]["blocked_regions"] == monitor.snapshot.protected_regions


def test_unconfirmed_indicator_fails_screenshot_closed(ac_root: Path, monkeypatch) -> None:
    monitor = _FakeProtectionMonitor(
        decision=_protected_decision(active_display_id=1, protected_ids={2}, confirmed=False)
    )
    monkeypatch.setattr(
        scheduler_mod.screenshot,
        "grab_many",
        lambda **_: (_ for _ in ()).throw(AssertionError("screenshot must not run")),
    )
    out = scheduler_mod._build_capture(
        CaptureConfig(), _FakeProvider(raw_json=None), None,
        protection_monitor=monitor,
    )
    assert out is not None
    assert "screenshot" not in out


def test_failed_protection_snapshot_writes_nothing(ac_root: Path) -> None:
    monitor = _FakeProtectionMonitor(decision=_failed_decision())
    out = scheduler_mod._build_capture(
        CaptureConfig(), _FakeProvider(raw_json=None), None,
        protection_monitor=monitor,
    )
    assert out is None
```

创建 `tests/test_daemon_protection.py`，断言 daemon 启动 monitor、传给 scheduler，并在 task 结束时调用 `stop()`：

```python
class FakeMonitor:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True


class FakeSessionManager:
    def on_event(self, _event) -> None:
        return None

    def force_end(self, *, reason: str) -> None:
        return None


@pytest.mark.asyncio
async def test_daemon_owns_protection_monitor_lifecycle(ac_root: Path, monkeypatch) -> None:
    monitor = FakeMonitor()
    seen_monitor = None

    async def capture_once_then_return(*_args, protection_monitor=None, **_kwargs) -> None:
        nonlocal seen_monitor
        seen_monitor = protection_monitor

    async def park_forever(*_args, **_kwargs) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(daemon_mod, "_build_protection_monitor", lambda _cfg: monitor)
    monkeypatch.setattr(daemon_mod.session_tick, "build_manager", lambda _cfg: FakeSessionManager())
    monkeypatch.setattr(daemon_mod.capture_scheduler, "run_forever", capture_once_then_return)
    monkeypatch.setattr(daemon_mod.session_tick, "run_check_cuts", park_forever)
    monkeypatch.setattr(daemon_mod.session_tick, "run_daily_safety_net", park_forever)
    cfg = Config()
    cfg.mcp.auto_start = False

    await daemon_mod._run(cfg, capture_only=True)

    assert monitor.started is True
    assert seen_monitor is monitor
    assert monitor.stopped is True
```

- [ ] **Step 2: 运行测试并确认 RED**

Run:

```bash
uv run pytest tests/test_capture_scheduler_fts.py tests/test_daemon_protection.py -q
```

Expected: `_build_capture`/`run_forever` 不接受 `protection_monitor`，daemon 也未创建 monitor，因此失败。

- [ ] **Step 3: 接入 scheduler 的两道 gate**

修改签名：

```python
def _build_capture(
    cfg: CaptureConfig,
    provider: ax_capture.AXProvider,
    trigger: dict[str, Any] | None,
    *,
    protection_monitor: PrivacyProtectionMonitor | None = None,
) -> dict[str, Any] | None:
```

读取 active-window 顶层元数据后立即获取 decision，然后才执行 active-window denylist 检查和 AX provider。这样前台隐私窗口即使马上被 denylist 返回，也会先推动对应浮层更新。行为固定为：

```python
decision = protection_monitor.decision_for_capture(force=True) if protection_monitor else None
if decision and decision.snapshot.state is ProtectionState.FAILED:
    logger.warning("capture skipped: privacy protection failed closed")
    return None

if decision and decision.snapshot.ax_blocked:
    out["ax_skipped"] = "protected_display"
else:
    if provider.available:
        result = provider.capture_frontmost(focused_window_only=True)
        if result is not None:
            out["ax_tree"] = result.raw_json
            out["ax_metadata"] = result.metadata
    else:
        out["ax_unavailable"] = True
    s1_parser.enrich(out)
```

截图前，如果 snapshot 已过 `fresh_until`，再次请求 decision；失败状态返回 `None`。若新 generation 已把活动窗口所在显示器标记为 protected，而旧 generation 已产生 AX 数据，则丢弃整次 capture 并返回 `None`，不能把旧 AX 写盘。启用样式且 `indicator_confirmed=False` 时保留确认安全的 AX 结果但不调用 screenshot。确认成功时把同一 snapshot 的 `protected_regions` 传给 `grab_many`。monitor 为 `None` 时保留当前 `sensitive_window_regions()` fallback。

在 watcher `_on_capture` 中先 `protection_monitor.request_refresh()`，再排队 capture，使标识尽快出现。

- [ ] **Step 4: 接入 daemon 生命周期**

在 `daemon.py` 增加可测试 factory，并在 `_run` 调用：

```python
def _build_protection_monitor(cfg: Config) -> PrivacyProtectionMonitor:
    return PrivacyProtectionMonitor(
        cfg.capture,
        config_path=paths.config_file(),
        overlay=PrivacyOverlayClient(),
    )


protection_monitor = _build_protection_monitor(cfg)
protection_monitor.start()
```

把 monitor 传给 `capture_scheduler.run_forever`。在 daemon 的最外层 `finally` 中执行 `protection_monitor.stop()`；monitor 拥有 overlay，并在 `stop()` 内清屏、关闭 transport 和退出 helper。菜单栏应用 lifecycle 不参与该流程。

- [ ] **Step 5: 运行 capture 与 daemon 测试**

Run:

```bash
uv run pytest tests/test_capture_scheduler_fts.py tests/test_daemon_protection.py tests/test_capture_privacy.py tests/test_capture_pause.py -q
```

Expected: 全部 PASS；旧的 no-monitor 测试继续覆盖 CLI fallback。

- [ ] **Step 6: 提交 capture gate**

```bash
git add src/openchronicle/capture/scheduler.py src/openchronicle/daemon.py tests/test_capture_scheduler_fts.py tests/test_daemon_protection.py
git commit -m "feat(capture): gate AX and screenshots with protection state"
```

---

### Task 7: 原生设置页六选一视觉选择器

**Files:**
- Modify: `macos/OpenChronicleApp/Sources/OpenChronicleApp/Models/Configuration.swift:53-80`
- Modify: `macos/OpenChronicleApp/Sources/OpenChronicleApp/Models/Configuration.swift:145-312`
- Create: `macos/OpenChronicleApp/Sources/OpenChronicleApp/Models/PrivacyIndicatorStyle.swift`
- Modify: `macos/OpenChronicleApp/Sources/OpenChronicleApp/Views/SettingsView.swift:127-175`
- Modify: `macos/OpenChronicleApp/Tests/OpenChronicleAppTests/ConfigurationTests.swift`
- Create: `macos/OpenChronicleApp/Tests/OpenChronicleAppTests/PrivacyIndicatorStyleTests.swift`

**Interfaces:**
- Consumes: config snapshot `privacy_indicator_style`
- Produces: `ConfigurationDraft.privacyIndicatorStyle: String`
- Produces patch: `capture.privacy_indicator_style`
- Produces: `PrivacyIndicatorStyleOption: CaseIterable`

- [ ] **Step 1: 写 Swift model 和 patch 失败测试**

在 configuration fixture 的 capture JSON 加入：

```json
"privacy_indicator_style": "pill"
```

在 `ConfigurationTests.swift` 原测试中加入：

```swift
var edited = original
edited.privacyIndicatorStyle = "border"
let updates = edited.updates(comparedTo: original)
XCTAssertEqual(updates["capture.privacy_indicator_style"] as? String, "border")
```

现有 `testConfigurationDraftEmitsOnlyChangesAndRemovesOverride` 同时修改 heartbeat、model override 和 indicator style，因此把 `updates.count` 的期望从 2 改为 3。

创建 `PrivacyIndicatorStyleTests.swift`：

```swift
final class PrivacyIndicatorStyleTests: XCTestCase {
  func testAllConfigValuesAndDefaultAreStable() {
    XCTAssertEqual(
      PrivacyIndicatorStyleOption.allCases.map(\.rawValue),
      ["off", "border", "shield", "pill", "quiet-shield", "banner"]
    )
    XCTAssertEqual(PrivacyIndicatorStyleOption.defaultStyle, .pill)
    XCTAssertEqual(PrivacyIndicatorStyleOption.pill.title, "B2 · 已保护")
  }
}
```

- [ ] **Step 2: 运行 Swift 测试并确认 RED**

Run:

```bash
swift test --package-path macos/OpenChronicleApp --filter 'ConfigurationTests|PrivacyIndicatorStyleTests'
```

Expected: 编译失败，缺少 `privacyIndicatorStyle` 和 `PrivacyIndicatorStyleOption`。

- [ ] **Step 3: 实现 Swift 配置链路和选项模型**

在 `CaptureConfigurationValue`、CodingKeys、`ConfigurationDraft`、snapshot init 和 `updates` 中加入 `privacyIndicatorStyle`。选项模型定义：

```swift
enum PrivacyIndicatorStyleOption: String, CaseIterable, Identifiable {
  case off, border, shield, pill
  case quietShield = "quiet-shield"
  case banner

  static let defaultStyle: Self = .pill
  var id: String { rawValue }
  var title: String {
    switch self {
    case .off: return "关闭"
    case .border: return "A · 边缘框"
    case .shield: return "B1 · 盾牌"
    case .pill: return "B2 · 已保护"
    case .quietShield: return "B3 · 轻量盾牌"
    case .banner: return "C · 状态条"
    }
  }
  var systemImage: String {
    switch self {
    case .off: return "xmark.circle"
    case .border: return "rectangle.inset.filled"
    case .shield: return "shield.fill"
    case .pill: return "checkmark.shield.fill"
    case .quietShield: return "shield"
    case .banner: return "rectangle.topthird.inset.filled"
    }
  }
  var sampleText: String? { self == .pill ? "已保护" : nil }
}
```

标题固定为“关闭”“A · 边缘框”“B1 · 盾牌”“B2 · 已保护”“B3 · 轻量盾牌”“C · 状态条”。

- [ ] **Step 4: 实现设置页视觉单选器**

在 Screenshots section 的 privacy mode 下面加入：

```swift
PrivacyIndicatorStylePicker(
  selection: binding(\.privacyIndicatorStyle, fallback: "pill")
)
```

`PrivacyIndicatorStylePicker` 使用两列 `LazyVGrid`，每个按钮包含 96×44 的屏幕预览、标题和选中 checkmark。按钮采用 `.buttonStyle(.plain)`，通过描边与 checkmark 同时表达选中状态，不能只依赖绿色。预览尺寸固定，文字可换行，不改变 grid 高度；每个按钮提供包含完整样式名称的 accessibility label。

- [ ] **Step 5: 运行 Swift 测试和构建**

Run:

```bash
swift test --package-path macos/OpenChronicleApp
bash scripts/build-macos-app.sh
```

Expected: 所有 Swift 测试 PASS，`OpenChronicle.app` 构建成功，设置页无编译警告。

- [ ] **Step 6: 提交设置 UI**

```bash
git add macos/OpenChronicleApp/Sources/OpenChronicleApp/Models/Configuration.swift macos/OpenChronicleApp/Sources/OpenChronicleApp/Models/PrivacyIndicatorStyle.swift macos/OpenChronicleApp/Sources/OpenChronicleApp/Views/SettingsView.swift macos/OpenChronicleApp/Tests/OpenChronicleAppTests/ConfigurationTests.swift macos/OpenChronicleApp/Tests/OpenChronicleAppTests/PrivacyIndicatorStyleTests.swift
git commit -m "feat(macos): add privacy indicator setting"
```

---

### Task 8: 安装打包、文档与端到端验证

**Files:**
- Modify: `pyproject.toml:49-56`
- Modify: `install.sh:204-224`
- Modify: `.gitignore`
- Modify: `docs/config.md`
- Modify: `docs/capture.md`
- Modify: `docs/macos-app.md`
- Modify: `tests/test_runtime_dependencies.py`

**Interfaces:**
- Produces wheel resources: overlay core、main、build script
- Produces installer verification: `_resolve_overlay_path()` returns executable
- Documents user-facing style values, status colors, security semantics and manual test

- [ ] **Step 1: 写 wheel resource 失败测试**

在 `tests/test_runtime_dependencies.py` 添加 source-tree smoke test：

```python
from pathlib import Path


def test_privacy_overlay_sources_are_declared_for_wheel() -> None:
    pyproject = Path("pyproject.toml").read_text()
    assert 'resources/mac-privacy-overlay-core.swift' in pyproject
    assert 'resources/mac-privacy-overlay.swift' in pyproject
    assert 'resources/build-mac-privacy-overlay.sh' in pyproject
```

- [ ] **Step 2: 运行测试并确认 RED**

Run:

```bash
uv run pytest tests/test_runtime_dependencies.py::test_privacy_overlay_sources_are_declared_for_wheel -v
```

Expected: FAIL，因为 `pyproject.toml` 尚未声明这些资源。

- [ ] **Step 3: 完成 wheel 与 installer 集成**

在 `pyproject.toml` 添加：

```toml
"resources/mac-privacy-overlay-core.swift" = "openchronicle/_bundled/mac-privacy-overlay-core.swift"
"resources/mac-privacy-overlay.swift" = "openchronicle/_bundled/mac-privacy-overlay.swift"
"resources/build-mac-privacy-overlay.sh" = "openchronicle/_bundled/build-mac-privacy-overlay.sh"
```

在 `install.sh` 的 Python verification block 中调用 `_resolve_overlay_path()`，缺失时安装失败，并打印 `privacy_overlay=<path>`。在 `.gitignore` 添加 `resources/mac-privacy-overlay`。

- [ ] **Step 4: 更新用户文档**

文档必须明确列出：

```toml
[capture]
privacy_indicator_style = "pill" # off | border | shield | pill | quiet-shield | banner
```

并说明：绿色代表显示器已经由同 generation 的防护决策排除；灰色代表暂停；黄色代表检测失败并 fail closed；浮层 helper 自身失败时无法显示黄色，因此“没有标识”不构成保护确认。说明检测仍会在本机读取窗口标题和几何元数据，但不写 capture、不建索引、不进 memory、不发模型。

- [ ] **Step 5: 运行完整自动验证**

按顺序运行，避免多个 `uv run` 并发修改同一个环境：

```bash
uv run ruff check src/openchronicle/config.py src/openchronicle/config_editor.py src/openchronicle/capture/privacy.py src/openchronicle/capture/protection.py src/openchronicle/capture/privacy_overlay.py src/openchronicle/capture/protection_monitor.py src/openchronicle/capture/scheduler.py src/openchronicle/daemon.py tests/test_config.py tests/test_cli_config_editor.py tests/test_capture_privacy.py tests/test_protection.py tests/test_privacy_overlay.py tests/test_protection_monitor.py tests/test_capture_scheduler_fts.py tests/test_daemon_protection.py tests/test_runtime_dependencies.py
uv run pytest -q
swift test --package-path macos/OpenChronicleApp
swiftc resources/mac-privacy-overlay-core.swift tests/swift/MacPrivacyOverlayCoreTests.swift -o /tmp/openchronicle-overlay-core-tests -framework AppKit
/tmp/openchronicle-overlay-core-tests
bash resources/build-mac-window-list.sh
bash resources/build-mac-privacy-overlay.sh
bash scripts/build-macos-app.sh
uv build
unzip -l dist/openchronicle-0.1.0-py3-none-any.whl | rg 'mac-privacy-overlay|capture/(protection|protection_monitor|privacy_overlay)\.py'
git diff --check
```

Expected: Ruff 无本次改动错误；pytest 和 Swift tests 全部 PASS；两个 helper 与 macOS app 构建成功；wheel 列出三个 overlay resource 和三个 Python 模块；`git diff --check` 无输出。

- [ ] **Step 6: 在空白隐私窗口上做双屏人工验收**

重新安装并启动：

```bash
openchronicle stop
bash install.sh --no-client-config
openchronicle start
```

只使用空白 Edge InPrivate 窗口，依次验证：

1. `separate`：隐私屏出现所选绿色标识，最新 JSON 只有安全显示器截图。
2. 窗口跨屏：标识在一个 watchdog 周期内移动，旧屏短暂过保护但不会被错误截图。
3. `all`：两块显示器都有标识，最新 JSON 无 screenshot 字段，AX provider 未遍历活动窗口。
4. `openchronicle pause`：两块显示器显示灰色暂停状态；resume 后撤下。
5. 终止 `mac-privacy-overlay`：标识消失且 screenshot 停止；helper 重启并确认后恢复。
6. 退出菜单栏 app：daemon 和标识继续工作。
7. 设置页逐个选择 A/B1/B2/B3/C/关闭：一个 watchdog 周期内热切换，无 daemon 重启。

检查日志只能出现 generation/state/style/display IDs，不得出现 InPrivate 标题内容：

```bash
tail -n 100 ~/.openchronicle/logs/capture.log
```

- [ ] **Step 7: 提交打包与文档**

```bash
git add pyproject.toml install.sh .gitignore docs/config.md docs/capture.md docs/macos-app.md tests/test_runtime_dependencies.py
git commit -m "docs: ship and verify privacy protection indicators"
```

- [ ] **Step 8: 最终工作区核查**

Run:

```bash
git status --short
git log --oneline -8
```

Expected: 除未跟踪的 `.superpowers/` 可视化会话外没有未提交实现文件；最近提交按 Task 1–8 的检查点排列。
