# 保护标识位置预设设计

日期：2026-08-24
状态：已实现，等待实机验证

## 背景

OpenChronicle 当前把 `shield`、`pill` 和 `quiet-shield` 保护标识放在每块显示器
`NSScreen.visibleFrame` 的右下角，并保留 12pt 边距。主屏 Dock 位于底部时，标识因此显示在
Dock 上方，而不是物理屏幕的右下角。

用户的主屏右下角用于 macOS 快速备忘录，左下角没有触发角、按钮或其他常用交互。保护标识
需要提供两个完整屏幕左下角位置，同时保留现有右下角行为作为可选兼容预设。

## 目标

- 设置中提供三个明确的位置预设。
- 默认使用完整屏幕左下角贴边。
- 左下角标识展开原因时向右、向上增长，徽标本身不移动。
- 保留当前避开 Dock 的右下角位置。
- 配置修改能够热加载，不需要重启 daemon。
- 位置变化不得削弱现有浮层确认、窗口 ID 确认或 fail-closed 隐私边界。
- 旧版 Python 与新版 helper 短暂交错运行时保持兼容。

## 非目标

- 不提供任意像素边距、拖拽定位或自定义坐标。
- 不自动探测或修改 macOS 触发角设置。
- 不根据 Dock 图标数量、放大动画或窗口内容动态移动标识。
- 不提供按显示器分别配置的位置。
- 不改变 `banner` 的顶部布局或保护判定逻辑。

## 配置

在 `[capture]` 下新增：

```toml
privacy_indicator_placement = "bottom-left-flush"
```

允许值：

| 配置值 | 设置页名称 | 定位范围 | 边距 |
|---|---|---|---|
| `bottom-left-flush` | 左下角贴边 | `NSScreen.frame` 左下角 | 0pt |
| `bottom-left-inset` | 左下角留白 | `NSScreen.frame` 左下角 | 12pt |
| `bottom-right-work-area` | 右下角避开 Dock | `NSScreen.visibleFrame` 右下角 | 12pt |

缺少、为空或无法识别的配置值统一归一化为 `bottom-left-flush`。配置编辑器拒绝不在上述
集合中的值，不写入部分配置。

## 样式行为

### 紧凑样式

`shield`、`pill` 和 `quiet-shield` 完整使用所选位置预设：

- 左侧预设固定面板左边缘，面板在原因展开时向右增长。
- 右侧预设固定面板右边缘，面板在原因展开时向左增长。
- 三种预设均固定面板底边，原因框只向上增长。
- 收起和展开状态切换时，状态徽标的屏幕坐标保持不变。

### 边缘框

`border` 的边框继续覆盖完整 `NSScreen.frame`。其中的紧凑状态徽标和原因框使用所选位置
预设：左侧预设从面板左下角开始，右侧预设继续使用可用区域右下角。边框本身不移动。

### 状态条与关闭

- `banner` 始终占据显示器顶部，位置设置不影响它。
- `off` 不创建浮层，位置设置只被保存，不参与渲染。

macOS 设置页在选择 `banner` 或 `off` 时禁用位置菜单，但不覆盖用户已经保存的位置值。

## 原生布局模型

新增原生枚举 `IndicatorPlacement`，值与 TOML 配置一致。`OverlayCommand` 增加可选
`placement` 字段。

helper 解码命令时采用以下兼容规则：

- 字段存在且合法：使用指定位置。
- 字段缺失：使用 `bottom-right-work-area`，复现旧协议行为。
- 字段存在但无法解码：拒绝命令并返回失败确认，不猜测位置。

`IndicatorView` 同时持有样式、位置和当前显示器的局部锚定矩形：

- 完整屏幕锚定矩形由 `display.frame` 转换到面板局部坐标。
- 工作区锚定矩形由 `display.visibleFrame` 相对 `display.frame` 的偏移得到。
- 左侧状态矩形从锚定矩形 `minX` 开始，右侧状态矩形以 `maxX` 对齐。
- 左侧原因框与状态矩形左边缘对齐；右侧原因框与状态矩形右边缘对齐。

紧凑样式的 `NSPanel` 仍只覆盖徽标和原因框需要的范围。`border` 与 `banner` 继续使用
完整显示器面板。输入命中面板继续从最终视觉面板与 `hitTargetRect` 推导，不新增独立坐标源。

多显示器坐标允许负数；所有定位都基于对应 `NSScreen` 的全局 `frame` 和
`visibleFrame`，不得假设主屏原点为 `(0, 0)`。

## Python 数据流

1. `CaptureConfig` 定义并归一化 `privacy_indicator_placement`。
2. `ProtectionMonitor` 从同一次配置加载中原子更新标识样式和位置。
3. `ProtectionSnapshot` 携带不可变的 `indicator_placement`。
4. `PrivacyOverlayClient` 在同一代 render 命令中发送样式、位置、原因和显示器集合。
5. helper 使用指定位置布局所有目标显示器并返回该代对应的窗口 ID。
6. scheduler 继续只接受当前代、已确认的浮层窗口 ID。

位置热加载与样式热加载共用配置 mtime 机制。保护监控器在下一次 watchdog refresh 检测配置
变化、产生新 generation，并重新取得浮层确认。不得只在 helper 内部保存一个脱离 snapshot
generation 的位置状态。

## macOS 设置页

在现有“Privacy indicator”样式选择器之后增加“Indicator position”菜单，包含：

- 左下角贴边
- 左下角留白
- 右下角避开 Dock

菜单使用目标 macOS 版本上可用的左下、留白和右下方向 SF Symbols，并显示当前选项。配置
快照、草稿、差异更新和保存结果都包含
`capture.privacy_indicator_placement`。

缺失或未知快照值在 UI 中显示“左下角贴边”。设置页保存时只写用户实际修改的字段。

## 错误与隐私边界

- Python 手工配置中的未知值归一化为默认值；设置页提交未知值由配置编辑器拒绝。
- helper 无法解析显式位置值时返回失败确认。
- 标识启用时，位置重绘未在截止时间内得到同代确认，截图继续按现有规则 fail closed。
- 位置变化不得绕过 diagnostics guard、pause、denylist 或 screenshot privacy mode。
- 浮层窗口 ID 仍必须被 window-filtered screenshot 模式排除。
- 日志只记录位置枚举，不记录窗口标题、规则内容或其他隐私原因详情。

## 测试

### Python

- 配置默认值、三个合法值和未知值归一化。
- 配置编辑器验证、JSON 快照和 TOML 原子更新。
- monitor 在一次 mtime 更新中同时热加载样式与位置。
- snapshot 与 overlay render 命令携带同代位置。
- helper 失败或确认超时时保持现有 fail-closed 行为。

### Swift helper

- 三种预设在紧凑和展开状态下的面板坐标。
- 左侧展开保持徽标坐标不变并向右、向上增长。
- 右侧展开保持徽标坐标不变并向左、向上增长。
- `border` 徽标跟随位置而边框保持全屏。
- `banner` 和 `off` 不受位置影响。
- Dock 位于底部、左侧和右侧时的 `visibleFrame` 几何。
- 负坐标副屏与不同缩放显示器。
- 缺失协议字段回退旧右下角；显式未知值拒绝。
- hover/click 输入命中区域在三种位置下与视觉区域一致。

### macOS App

- 三个选项的 raw value、标题、图标和默认值。
- 缺失与未知快照值的默认显示。
- 草稿差异只写实际修改的位置字段。
- `banner`/`off` 禁用菜单但保留值，其余样式可编辑。

### 集成与实机

- 使用空白 Edge InPrivate 窗口，不放真实隐私内容。
- 逐项切换三个预设，确认一个 watchdog 周期内热更新。
- 分别验证收起、hover 展开和 click 展开。
- 验证主屏与副屏、Dock 上方现有预设以及完整屏幕左下角预设。
- 检查 capture JSON、日志和截图中没有浮层或测试隐私标记。
- 确认 App、daemon、AX watcher 和 overlay helper 仍为单一 App-owned 进程链。

## 验收标准

- 新安装和缺少该键的现有配置默认显示完整屏幕左下角贴边。
- 设置页可在三个预设间切换并持久化。
- 位置在运行中热更新，无需重启 daemon。
- 左侧原因展开不移动徽标，也不超出屏幕。
- 当前右下角避开 Dock 的行为可完整恢复。
- 所有自动化测试、原生构建和空白隐私窗口实机验证通过。
- 配置文件中的既有模型、隐私规则和其他捕获设置保持不变。
