# 隐私保护标识设计

## 概要

当某块显示器因隐私原因被排除在采集范围之外时，OpenChronicle 会在该显示器上显示一个小型原生浮层。浮层与截图筛选使用同一份保护快照，因此它代表后端已经实际做出保护决定，而不是一套独立运行的视觉提示。

只要 OpenChronicle daemon 正在运行，标识就能够工作，不依赖菜单栏应用是否保持打开。

## 目标

- 让用户扫一眼就能确认：包含 denylist 隐私窗口的显示器已从截图和语义内容采集中排除。
- 日常标识足够克制，可以在处理隐私数据时持续显示而不过度干扰。
- 支持五种可选视觉样式，以及明确的关闭选项。
- 清晰区分隐私保护、手动暂停和 fail-closed 错误。
- 保持浮层状态与截图决策同步。
- 支持多显示器、多个 Space 和全屏应用。

## 非目标

- 浮层不会对截图中的局部区域进行打码；目标显示器只存在“采集”或“整屏跳过”两种状态。
- 浮层不会检查网页正文、表单字段或后台 AX Tree。
- 浮层不承诺完全不读取元数据。为了判断保护范围，系统仍会在本机读取窗口标题和几何信息。
- 本次改动不会让所有采集设置都支持热加载；只有标识样式和位置必须能够在不重启 daemon 的情况下更新。

## 用户可见行为

### 可配置样式

在 `[capture]` 中新增样式和位置设置：

```toml
privacy_indicator_style = "pill"
privacy_indicator_placement = "bottom-left-flush"
```

允许的值如下：

| 设置界面名称 | 配置值 | 外观 |
|---|---|---|
| 关闭 | `off` | 不显示浮层；现有隐私防护仍继续工作。 |
| A：边缘框 | `border` | 显示器边缘细框，加一个紧凑的状态标识。 |
| B1：盾牌 | `shield` | 在所选位置显示小型实心盾牌图标。 |
| B2：已保护 | `pill` | 在所选位置显示小盾牌和“已保护”胶囊。 |
| B3：轻量盾牌 | `quiet-shield` | 在所选位置显示小型半透明描边盾牌。 |
| C：状态条 | `banner` | 显示器顶部的窄状态条。 |

默认样式为 `pill`。已有安装中没有样式配置项时，配置加载器同样采用这个默认值。

位置可选 `bottom-left-flush`、`bottom-left-inset` 和 `bottom-right-work-area`。默认值为 `privacy_indicator_placement = "bottom-left-flush"`，标识紧贴物理屏幕左边缘和下边缘。`bottom-left-inset` 位于物理屏幕左下角并保留 12pt 边距；`bottom-right-work-area` 位于 `NSScreen.visibleFrame` 右下角并保留 12pt 边距，从而避开 Dock。紧凑的 `shield`、`pill` 和 `quiet-shield` 标识跟随所选位置。`border` 内的紧凑状态徽标和原因框也跟随位置，但全屏边框本身保持不动。`banner` 和 `off` 不受位置设置影响。

所有浮层都不会获取键盘焦点。`always` 和 `hover` 保持鼠标穿透；`hover` 只观察指针移动，不拦截底层应用输入。`click` 仅为有界的徽标/原因区域启用点击目标，并且只消费命中该目标的点击。

### 状态

| 状态 | 颜色 | 文字或图标 | 显示范围 |
|---|---|---|---|
| 隐私窗口已保护 | 绿色 | 盾牌；包含文字的样式显示“已保护” | 当前隐私决策排除的显示器 |
| 手动暂停采集 | 灰色 | 暂停图标；适用时显示“已暂停” | 所有显示器 |
| 隐私检测失败 | 黄色 | 警告图标；适用时显示“截图已停用” | 所有能够枚举到的显示器 |
| 没有激活保护 | 无 | 隐藏浮层 | 所有显示器 |

选中的样式决定所有状态的几何形态。例如，`border` 根据状态显示绿色、灰色或黄色边框；`shield` 则在盾牌、暂停和警告图标之间切换。

### 截图模式映射

- `separate`：只标记并跳过与 denylist 可见窗口相交的显示器。
- `all`：任意 denylist 窗口都会导致整张虚拟桌面截图被跳过，因此所有显示器都显示标识。
- `primary`：主显示器被阻止时显示标识。若副显示器包含 denylist 窗口，也会显示标识，因为该显示器本来就不在当前截图目标中，并且不会遍历其中隐私窗口的内容。

denylist 窗口关闭、最小化或移动到另一块显示器后，下一份保护快照会撤下或移动标识。旧快照可以在很短时间内造成额外保护，但绝不能允许一张会被较新决策阻止的截图通过。

`screenshot_privacy_mode = "off"` 保留前台 app、bundle、标题、URL 和文本 denylist，但关闭后台窗口 inventory 与标识。后台保护启用时，`screenshot_privacy_fail_closed = false` 允许在真实 inventory 故障后执行一次无后台保护的采集；系统会清除旧标识，并且不会把该决策声明为已获得视觉确认。`screenshot_privacy_fail_closed = false` 只适用于窗口/display inventory 失败。暂停状态不可读时，无论该设置为何值，OpenChronicle 都显示黄色失败标识并终止整次捕获。批准的默认值仍为 `skip-monitor` 加 fail closed。

## 安全语义

看到标识意味着：

- 在当前截图模式下，被标记的显示器不会进入截图；
- 当活动窗口位于被标记的显示器时，OpenChronicle 不会遍历该窗口的 AX 内容。

标识并不表示完全不读取窗口元数据。隐私检测器仍会在本机读取顶层应用名称、bundle identifier、标题、位置、尺寸和最小化状态。这些仅用于检测的值不会写入 capture JSON、FTS、timeline、memory，也不会进入模型请求。

如果活动窗口本身命中 denylist，现有前台保护会继续在 AX 遍历前跳过整次采集。如果另一个活动窗口位于被标记的显示器，OpenChronicle 可以保留其顶层窗口元数据，但会省略 AX Tree、focused value、visible text 和 URL。如果 denylist 窗口位于后台，则只读取它的顶层检测元数据。

## 架构

### 权威保护快照

由 daemon 持有一个新的 `PrivacyProtectionMonitor`，负责生成不可变保护快照。每份快照包含：

- 单调递增的 generation 编号；
- 状态：`inactive`、`protected`、`paused` 或 `failed`；
- 截图模式；
- 当前选择的标识样式；
- 显示器边界和被阻止的显示器 identifier；
- 能够确定时，活动窗口所在的显示器；
- 无法精确确定 focused window 时的活动候选显示器；
- 已发布决策覆盖的刷新请求 epoch；
- 创建时间和新鲜度截止时间。

每个窗口事件都会递增单调 request epoch 并请求立即刷新。已发布决策记录读取 inventory 前观察到的 epoch。非强制校验只有在新鲜决策覆盖校验时已观察到的全部请求时才能复用缓存；刷新期间到达的新请求会触发下一次同步刷新。每秒一次的 watchdog 用于兜底不能可靠发出 AX 通知的应用；每次采集在 AX 前都会强制刷新。

monitor 会统一完成 denylist 窗口区域、活动窗口与物理显示器之间的映射。浮层、AX gate 和截图选择器共同使用这份映射。

### 部分窗口身份与显式不确定性

inventory 从 CoreGraphics `optionOnScreenOnly` 结果开始，只保留 alpha 大于零、尺寸为正的 layer-0 窗口。非空 CG 标题标记为可用。空标题只有在同 PID、`CGWindowID` 全局唯一且精确匹配时才允许读取对应 AX 标题；几何信息永远不能授权身份。如果精确匹配或标题读取不可用，helper 仍输出该 CG 记录并设置 `title_available = false`，不会因为一个无关窗口而使整个 inventory 失败。

存在标题 deny pattern 时，未知标题窗口只保守保护其几何范围相交的显示器；精确 app/bundle deny 仍正常生效。如果前台 PID 的 focused AX window 无法精确匹配，该 PID 的所有 on-screen layer-0 CG 窗口都会标记为活动候选。只有候选显示器与受保护显示器相交时才阻止 AX；另一块显示器上的不确定性不会升级为全局 AX 故障。helper 退出、输出解析失败以及缺失或非法 display inventory 仍产生固定原因码的 `failed` 状态。这些确定性标志和检测值只留在本机隐私子系统内。后续原因诊断设计允许用户批准的 `exact` 浮层只向同一快照已经保护的显示器发送有长度上限的 app、bundle、标题或规则字段；category/tiered 命令和未受保护显示器不得收到具体字段。

### 原生浮层 helper

新增一个随包提供的 Swift 可执行文件 `mac-privacy-overlay`，由 daemon 启动并监管。它以 accessory AppKit 进程运行，不显示 Dock 图标。每块需要标识的显示器对应一个无边框、非激活的 `NSPanel`，具有以下特性：

- `always` 和 `hover` 的展示 panel 保持鼠标穿透，hover 跟踪不会消费底层指针输入；
- `click` 只为有界的徽标/原因 panel 或 hit target 启用输入，显示器其他区域继续穿透；
- 永远不会成为 key window 或 main window；
- 跨 Space 可见；
- 可以与全屏应用共同显示；
- 根据 CoreGraphics 显示器边界和 `NSScreen` 几何信息定位；
- 显示在普通应用窗口之上。

Python 通过 stdin 发送逐行 JSON 命令。只有在主线程完成对应 panel 更新后，helper 才会通过 stdout 确认该 generation。命令通常只包含状态、样式、generation、显示器几何信息和固定原因码。用户批准 `exact` 详情后，命令可以额外包含有长度上限的具体原因字段，但只能放入同一 generation 已经排除的显示器 payload。

命令示例：

```json
{"generation":42,"state":"protected","style":"pill","displays":[{"id":2,"left":1920,"top":0,"width":1920,"height":1080}]}
```

确认示例：

```json
{"generation":42,"rendered":true}
```

### 采集 gate

在任何 AX 遍历之前：

1. 强制执行一次新的隐私扫描并生成下一份快照。
2. 将快照发送给浮层 helper。
3. 如果活动窗口位于被标记的显示器，省略 AX 遍历和所有派生 S1 内容。
4. 如果快照状态为 `paused`，无论 inventory、截图或 AX 是否可用，都省略整次采集。暂停状态不可读时，将其视为黄色失败状态；无论 `screenshot_privacy_fail_closed` 为何值，都省略整次采集。
5. 如果快照状态为 `failed` 且启用 fail closed，同时省略 AX 和截图采集。

每次截图前，如果快照已经不够新，则再次刷新。隐私状态激活且标识已启用时，等待对应 generation 的确认，然后只采集该快照明确允许的目标。

启用标识且需要显示时，如果 helper 启动失败、退出或未在截止时间内确认，截图会 fail closed。AX 显示器 gate 和前台 denylist 不依赖浮层，仍然继续工作。设置 `privacy_indicator_style = "off"` 时，浮层是否可用不会影响现有隐私防护。

inventory 失败且 `screenshot_privacy_fail_closed = false` 时，monitor 会清除旧浮层，以 `indicator_confirmed = false` 发布固定故障原因，scheduler 随后执行无后台保护的采集。此时不得显示黄色“截图已停用”状态。

### 暂停与故障处理

monitor 独立于采集调度器检查结构化暂停状态。因此即使正常采集任务已经跳过，暂停标识仍然保持可见。

隐私枚举失败时生成 `failed` 快照。默认 fail-closed 策略会隐藏过期绿色浮层、显示黄色状态并终止整次采集；如果无法枚举显示器，helper 会在自身能够发现的每个 `NSScreen` 上显示警告。显式 fail-open 策略则清除旧浮层并允许无视觉确认的采集。`screenshot_privacy_fail_closed = false` 只适用于窗口/display inventory 失败。暂停状态不可读时，无论该设置为何值，OpenChronicle 都显示黄色失败标识并终止整次捕获。

浮层进程断开连接后，daemon 会使上一次确认失效，并采用有上限的退避策略重启它。需要显示但尚未获得确认时，截图继续被阻止。由于已经失败的浮层进程无法显示自己的警告，这种情况下不会出现标识；标识缺失表示保护状态没有得到视觉确认。

## 设置集成

原生设置界面的 Capture 区域新增一个带预览的单选选择器，包含关闭、A、B1、B2、B3 和 C。该选项进入现有配置 snapshot、draft、校验和 patch 流程。

daemon 监控配置文件的修改时间，把 `capture.privacy_indicator_style` 和 `capture.privacy_indicator_placement` 热加载到 monitor。有效的样式或位置改动会在一个 watchdog 周期内更新当前浮层，无需重启采集。无效值由配置编辑器拒绝；常规配置加载器还会将无效样式归一化为 `pill`，将无效位置归一化为 `bottom-left-flush`，作为最后一道兜底。

## 打包与生命周期

- 将 Swift 源码和构建脚本加入 wheel 资源。
- 扩展 `install.sh`，与其他 macOS helper 一起编译并验证 `mac-privacy-overlay`。
- 仅在 daemon 运行且配置样式不是 `off` 时启动 helper。启用标识后，暂停和失败状态会显示在所有显示器上。
- `screenshot_privacy_mode = "off"` 时不启动后台 monitor 或 helper。
- daemon 关闭时终止 helper 并移除所有 panel。
- helper 的关闭不与菜单栏应用的退出绑定。

## 测试

### Python 测试

- 覆盖六个配置值的解析、归一化、配置编辑器校验、snapshot 和 patch。
- 覆盖 `primary`、`separate` 和 `all` 的显示器映射。
- 覆盖未知标题以及同屏/异屏活动候选窗口。
- 覆盖 `inactive`、`protected`、`paused` 和 `failed` 状态转换。
- 活动窗口位于被标记显示器时，不产生 AX Tree 或派生 S1 内容；在 `all` 模式下，任意隐私命中都会阻止所有显示器上的 AX 遍历。
- 截图使用与浮层确认一致的 generation。
- 浮层确认缺失、进程崩溃、响应格式错误或超时后，系统 fail closed。
- `off` 不要求浮层存在，同时保留现有隐私行为。
- 配置热加载同时更新标识样式和位置。
- 刷新期间和 AX 期间到达的事件不能复用事件前决策。
- `off` 模式与显式 inventory fail-open 保留原有控制语义。
- 暂停状态不可读时，即使 `screenshot_privacy_fail_closed = false` 也保持 fail closed，并显示黄色失败标识。

### Swift 测试与构建检查

- 样式与状态 presentation model 能选出预期的图标、文字、颜色、尺寸和锚点。
- AppKit panel controller 能按显示器创建、更新、移动和移除 panel。
- panel 不会激活；`always`/`hover` 保持点击穿透，`click` 只消费有界徽标/原因 hit target 内的输入。
- NDJSON 命令解码和确认编码正确。
- 随包 helper 能够针对受支持的 arm64 和 x86_64 macOS target 编译。

### 集成与人工验收

- 在副显示器打开空白 Edge InPrivate 窗口，确认该显示器出现选中的绿色标识，并且 `separate` 模式只保存安全显示器截图。
- 在显示器之间移动窗口，确认标识随之移动。
- 确认 `all` 会标记所有显示器并且不保存截图。
- 确认前台 InPrivate 不产生 AX Tree 或 capture JSON。
- 确认所有显示器上的暂停和 fail-closed 标识。
- 退出菜单栏应用，确认 daemon 运行期间标识仍继续工作。
- 终止浮层 helper，确认在重新获得确认之前截图始终停止。
- 在设置中切换所有样式，确认无需重启 daemon。

## 隐私与日志

日志可以记录 generation 编号、状态名称、样式名称、显示器 identifier 和 helper 错误，但不得包含 denylist 窗口标题、应用名称、bundle identifier 或屏幕内容。有长度上限的具体值只能通过本地浮层 IPC 发送给已经受保护的显示器；不得记录、持久化、写入确认消息或发送到未受保护显示器。
