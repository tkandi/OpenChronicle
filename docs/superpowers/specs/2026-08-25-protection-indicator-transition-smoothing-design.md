# 保护标识过渡平滑设计

日期：2026-08-25
状态：已确认，等待书面规格复核

## 背景

OpenChronicle 通过 CoreGraphics 的 `optionOnScreenOnly` 窗口清单保护实际出现在屏幕合成结果中的
隐私窗口。Mission Control、F3 和触控板切换 Space 时，macOS 会暂时把相邻桌面窗口、窗口
缩略图或动画中的窗口报告为 on-screen。现有保护监控器因此立即进入 `protected`，按照配置显示
完整保护标识，并停止截图和 AX 抓取。

安全行为是正确的：切换动画可能真实包含 Edge InPrivate、密码应用或其他 denylisted 窗口，不能
因为处于 Mission Control 就绕过保护。问题在显示层：很短的 protected episode 会让“已保护”胶囊
闪一下；窗口清单在过渡结束附近短暂安全/保护抖动时，浮层也可能反复出现或延迟清除。

一次已观察到的实机事件在 `23:57:08` 同时保护两块显示器并跳过截图，约 1.1 秒后恢复正常。
本设计保留第一帧 fail-closed，只平滑短暂保护的视觉呈现和安全清除过程。

## 目标

- 第一帧 protected 时立即停止截图和 AX 抓取。
- 短暂 protected 立即显示轻量盾牌，不闪出醒目的完整样式。
- protected 连续达到 800ms 后升级为用户配置的最终标识样式。
- 第一次安全结果不立即恢复捕获；200ms 后快速复查，连续两次安全才清除保护。
- 每次显示阶段变化继续使用独立 generation、helper acknowledgement 和窗口 ID。
- 不依赖识别 Mission Control、Space 手势、F3 或私有 macOS API。
- paused、failed、off 和现有隐私边界保持明确且可测试。

## 非目标

- 不在 Mission Control 期间关闭隐私保护。
- 不忽略缩略图、动画窗口或相邻 Space 中真实可见的隐私像素。
- 不改变 denylist 匹配规则、窗口枚举、AX redaction 或截图过滤模式。
- 不让 Swift helper 自行决定延迟、阶段或清除时机。
- 不增加新的线程、独立 Timer、任意延迟设置或设置页控件。
- 不保证所有 macOS 窗口切换动画都完全没有任何视觉变化；即时轻量盾牌仍是用户可见的保护确认。

## 用户可见行为

### 短暂保护

当有效状态从非 protected 进入 protected：

1. 截图和 AX 抓取立即停止。
2. 若配置样式不是 `off`，立即显示 `quiet-shield`。
3. 记录本次 protected episode 的单调时钟开始时间。
4. 在 episode 连续保持 protected 的前 800ms 内，不显示完整胶囊、边缘框或状态条。

### 持续保护

protected episode 连续达到 800ms：

- 生成新 generation。
- 重新读取当前配置的最终标识样式与位置。
- helper 重新渲染并确认窗口 ID。
- 最终样式为 `quiet-shield` 时，盾牌外观不变，但仍生成新 generation 并重新渲染，以恢复
  用户配置的 hover/click 原因能力。

### 安全清除

protected 之后第一次得到安全窗口清单：

1. 进入 `clear-pending`。
2. 继续发布 protected 有效快照，保留当前轻量或完整样式、显示器集合、原因和窗口过滤授权。
3. 截图与 AX 继续停止。
4. 200ms 后进行一次快速 inventory 复查。
5. 第二次结果仍安全时，生成 inactive generation、取得清除 acknowledgement 并恢复捕获。
6. 复查前或复查时再次 protected，则取消 clear-pending，继续原 episode。

第一次安全与第二次安全之间无论发生多少普通捕获事件，都不得提前计为第二次安全；第二次安全
必须发生在 `clear_deadline` 到达之后。

### 其他状态

- `paused`：取消普通 protected 平滑，立即显示用户配置的暂停样式和原因。
- `failed`：取消普通 protected 平滑，立即显示现有 fail-closed 失败状态。
- `off`：不显示轻量或完整标识，但 protected 与 clear-pending 仍立即阻止截图和 AX。
- 最终样式为 `quiet-shield`：transient 与 sustained 外观一致。
- protected 显示器集合、原因或窗口 ID 在同一 episode 中变化时，不重置 800ms 计时；新增显示器跟随
  当前 episode 阶段，避免已有显示器再次降级闪烁。
- 从 paused/failed/inactive 进入 protected 时开始新的 episode。

## 状态模型

新增纯 Python 状态机 `ProtectionPresentationSmoother`，维护以下内部 phase：

| phase | 原始状态 | 对外有效状态 | 捕获 | 标识 |
|---|---|---|---|---|
| `inactive` | inactive | inactive | 允许 | 清除 |
| `transient-protected` | protected | protected | 阻止 | quiet-shield 或 off |
| `sustained-protected` | protected | protected | 阻止 | 配置最终样式 |
| `clear-pending` | inactive | protected | 阻止 | 保持上一阶段样式 |
| bypass | paused/failed | 原始状态 | 按现有规则 | 立即配置样式 |

状态机至少保存：

- episode 开始的 monotonic 时间；
- 当前 phase；
- promotion deadline；
- clear confirmation deadline；
- 上一份有效 protected snapshot；
- 下一次必须唤醒监控器的 deadline。

固定常量：

```text
PROTECTED_PROMOTION_SECONDS = 0.8
SAFE_CONFIRMATION_SECONDS = 0.2
```

不使用 wall clock，睡眠、时间同步或时区变化不得影响状态机。

## 组件与数据流

```text
WindowInventory / pause / diagnostics guard
                ↓
      build_protection_snapshot
                ↓ raw snapshot
  ProtectionPresentationSmoother
                ↓ effective snapshot
   overlay render + acknowledgement
                ↓ ProtectionDecision
 scheduler / AX gate / diagnostics listeners
```

### 原始快照

`build_protection_snapshot` 保持纯函数和现有匹配语义，产生 raw snapshot。raw snapshot 表示本次
窗口清单的即时判断，不直接交给 scheduler 或浮层。

### 平滑器

平滑器消费 raw snapshot、当前配置最终样式和 monotonic `now`，返回
`ProtectionPresentationResult`：

- effective snapshot；
- 当前 presentation phase（不写入原生 wire）；
- 可选的 next deadline。

effective snapshot 是系统唯一发布给下游的权威状态。transient 阶段将
`indicator_style` 替换为 `quiet-shield`；clear-pending 使用新的 generation/time 重新发布上一份
protected 数据，确保 snapshot freshness、helper acknowledgement 和 scheduler 授权都有效。

平滑器作为单独的小模块存在，不把计时分支继续堆入已经较大的 `protection_monitor.py`。它不读
文件、不启动线程、不调用 helper，也不记录日志，便于用虚拟时间穷举状态转换。

### 保护监控器

`PrivacyProtectionMonitor` 继续拥有：

- inventory/pause/diagnostics 输入读取；
- generation 分配；
- helper render/clear acknowledgement；
- confirmed overlay window IDs；
- effective decision 发布。

现有固定 watchdog 等待改为：

```text
min(watchdog deadline, smoother promotion deadline, smoother clear deadline)
```

使用现有 `threading.Event.wait(timeout)`，不增加 Timer 或第二个监控线程。捕获事件触发的强制
refresh 可以提前采样，但不得绕过 promotion/clear deadline。

### Swift helper

原生 helper 不增加计时器或 presentation policy。monitor 将 result 中的 phase 连同 effective
snapshot 交给 `PrivacyOverlayClient`；phase 只用于构造命令，不新增原生协议字段。Python 继续发送
现有 `style` 字段：transient 发送 `quiet-shield`，sustained 发送配置样式。helper 按每个
generation 正常重排、返回 acknowledgement 和窗口 ID。

`PrivacyOverlayClient` 在 transient phase 中发送空的 overlay reason 列表，但 effective snapshot
仍保留完整结构化原因供 diagnostics 和 scheduler 使用；不能通过删除 snapshot reason 来实现视觉
抑制。升级到 sustained 后重新发送用户配置的 reason display/detail/trigger 和原因列表。因此最终
样式本来就是 `quiet-shield` 时也必须产生 promotion generation。现有 placement、hover/click
reason 展开和 input hit panel 行为除此之外不变。

## Generation 与隐私授权

- 每次 raw inventory 采样仍分配新 generation。
- transient、promotion、clear-pending 和 inactive 都必须经过现有 helper acknowledgement。
- `indicator_confirmed=false` 时保持现有 fail-closed。
- clear-pending 重新确认的 overlay window IDs 进入 `ProtectionDecision.indicator_window_ids`。
- scheduler 只使用 effective decision，因此第一次安全不会提前恢复截图。
- filtered screenshot authorization fingerprint 继续包含 effective style、保护窗口/显示器和 overlay IDs。
- phase 改变导致 effective style 改变时，授权 fingerprint 必须变化，旧截图结果不可在新阶段写入。

## 配置热加载

- transient 期间修改最终样式：保持 quiet-shield，到 promotion 时使用最新样式。
- sustained 期间修改样式：下一 generation 立即使用新样式。
- 任意阶段修改 placement：下一 generation 立即使用新位置，不重置 episode 计时。
- 修改为 `off`：下一 generation 清除视觉标识，但 effective protected 状态继续阻止捕获。
- 从 `off` 改为可见样式：若仍 transient 则显示 quiet-shield；若已 sustained 则显示最终样式。

800ms 与 200ms 为内部固定策略，不增加 TOML 字段或 SwiftUI 控件。

## 错误处理

- inventory unavailable、active window unmapped、pause state unavailable 和 diagnostics guard failure
  继续产生 failed raw snapshot，并立即绕过平滑。
- helper 启动、写入、解码或 acknowledgement 失败继续沿用现有 fail-closed。
- 平滑器内部状态不完整或输入 generation 非递增时，返回立即 failed 的安全错误，而不是 inactive。
- monitor 停止时清除所有 deadline，不能在 shutdown 后再次渲染。
- 系统睡眠期间 monotonic deadline 自然暂停或推进都不得产生短暂 fail-open；唤醒后的第一次 refresh
  必须重新读取 inventory，再决定继续、升级或清除。
- 日志只记录 phase、generation、受保护 display IDs、是否 confirmed；不记录窗口标题、URL、规则或
  exact reason。

## 测试

### 纯状态机测试

使用可注入 monotonic 时间，不使用真实 `sleep`：

- protected 少于 800ms 始终 transient；
- 800ms 边界前后精确升级；
- 第一次安全保持 protected；
- 200ms 前的重复安全不清除；
- 200ms 后第二次安全才 inactive；
- clear-pending 中重新 protected 取消清除；
- protected display/reason/window 集合变化不重置 episode；
- quiet-shield、off、paused、failed；
- transient overlay reason 为空，但 effective snapshot 和 diagnostics reason 完整；
- 最终样式为 quiet-shield 时，800ms 后仍产生 promotion generation 并恢复原因交互；
- transient/sustained 中样式与 placement 热加载；
- 非递增 generation 或非法内部状态 fail closed。

### 监控器与边界测试

- deadline 会缩短 watchdog wait，但不启动额外线程。
- promotion 与 clear 都产生新的、被确认的 generation。
- transient phase 不向 overlay 发送原因，但 diagnostics 仍能读取 effective snapshot 的完整原因。
- clear-pending 期间 scheduler 和 AX gate 仍阻止捕获。
- helper 未确认、窗口 ID 缺失/重复/非法时保持 fail-closed。
- authorization fingerprint 在 transient → sustained 与 clear 时正确变化。
- listener 和 diagnostics 只收到 effective decision。
- monitor stop 后没有延迟回调或 helper 写入。

### 原生与 App 回归

协议不新增字段，但完整运行现有：

- `MacPrivacyOverlayCoreTests`；
- macOS App Swift Package tests；
- placement、reason hover/click、窗口 ID acknowledgement 测试。

## 实机验收

只使用空白 Edge InPrivate 窗口，不放真实隐私内容：

1. 快速触控板切换 Space：第一帧阻止截图/AX，只出现短暂轻量盾牌。
2. 缓慢切换或保持 Mission Control：800ms 后升级为完整配置样式。
3. 退出 Mission Control：第一次安全后仍保护，快速复查通过后约 200ms 清除。
4. clear-pending 中重新滑回隐私 Space：不得清除或恢复截图。
5. 主屏与副屏分别验证，包含相邻 Space 和 Mission Control 缩略图。
6. 读取 category-only diagnostics generation、capture log 和脱敏 capture JSON，确认第一帧已经阻止
   截图/AX，且没有 private window 内容写入。
7. 确认 App、daemon、AX watcher 和 overlay helper 仍为单一 App-owned 进程链。
8. 完整运行 Python、Swift helper、macOS App 测试，并执行 backend/helper 与 SwiftUI App 两层安装。

## 验收标准

- 快速切屏不再闪出完整“已保护”胶囊，只显示轻量盾牌。
- 持续隐私风险在 800ms 后可靠升级完整样式。
- 安全画面需要两次、相隔至少 200ms 的确认才恢复捕获。
- 任何过渡、错误或 helper 未确认都不存在截图/AX fail-open。
- paused/failed/off 和配置热加载行为符合本规格。
- generation、窗口 ID 与 filtered screenshot 授权保持严格因果关系。
- 所有自动化与空白隐私窗口实机验收通过。
