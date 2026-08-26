# 保护标识静默防抖设计

日期：2026-08-26
状态：已批准，待实现

本设计取代以下既有结论：自动平滑的风险 episode 在前 800ms 显示
`quiet-shield`。相关历史设计仍保留用于记录演进：

- [保护标识过渡平滑设计](2026-08-25-protection-indicator-transition-smoothing-design.md)
- [unmapped failure presentation smoothing 设计](2026-08-25-unmapped-failure-presentation-smoothing-design.md)
- [window display history fallback 设计](2026-08-26-window-display-history-fallback-design.md)

其他既有结论，尤其第一帧 fail-closed、generation/acknowledgement、200ms 安全清除确认、
mapping fallback 和硬故障分类，保持有效。

## 背景

OpenChronicle 当前在普通 protected episode 的前 800ms 自动显示轻量 `quiet-shield`，持续风险
再升级为用户配置的完整样式。短暂窗口或 Space 过渡因此仍会让小盾牌闪一下。用户希望短暂风险
完全没有视觉闪烁；如果风险持续，则直接出现完整“已保护”标志。

这只是 presentation policy 调整。截图选择和 AX gate 必须继续从第一份 protected/allowlisted
failure 决策起立即 fail-closed，不能等待视觉防抖结束。

## 目标

- 自动平滑的风险 episode 前 1 秒完全不显示保护浮层。
- 风险连续达到 1 秒时，直接显示当前配置的最终样式；当前配置为 `pill`，即完整“已保护”胶囊。
- 截图与 AX 保护从第一帧立即生效。
- 保留现有 phase、deadline、generation、helper acknowledgement、诊断和安全清除语义。
- 保留 `quiet-shield` 作为用户可显式选择的最终样式，但不再自动把它用作过渡样式。

## 非目标

- 不改变 denylist、窗口映射、history fallback、标题不确定性或 failure policy。
- 不延迟截图排除、截图跳过或 AX 阻断。
- 不删除 `quiet-shield` 配置、SwiftUI 预览或 helper 渲染能力。
- 不增加 TOML 字段、设置页控件、线程或原生计时器。
- 不改变第一次安全结果后的 200ms 双确认清除策略。

## 用户可见行为

### 自动平滑风险

从 inactive、paused 或硬故障恢复后进入可平滑风险 episode 时：

1. 立即按 raw decision 阻止截图与 AX。
2. 记录 episode 的 monotonic 起点，并设置 `start + 1.0s` promotion deadline。
3. deadline 前发布原有 transient phase，但 effective `indicator_style` 固定为 `off`，且
   `overlay_reasons_enabled=false`。
4. 若风险在 1 秒内消失，则保持静默进入既有 clear-pending；200ms 安全确认通过后恢复捕获。
5. 若风险连续达到 1 秒，则发布新的 sustained generation，使用当时最新配置的最终样式和位置，
   并恢复既有原因展示能力。

本规则覆盖所有进入现有 presentation smoother 的风险：普通 mapped protected、history/mapping
fallback、仅标题不确定性，以及 allowlist 中可平滑的 mapping failure。状态在这些类别之间变化时
共享原 episode 起点，不重启 1 秒计时。

### 明确配置和旁路状态

- 最终样式为 `pill`、`border`、`shield` 或 `banner`：前 1 秒静默，满 1 秒直接显示该完整样式。
- 最终样式为 `quiet-shield`：前 1 秒静默，满 1 秒才显示用户明确选择的小盾牌。
- 最终样式为 `off`：transient 与 sustained 始终不可见，保护仍生效。
- `paused` 与不可平滑的硬 `failed`：继续绕过 presentation smoother，立即使用既有提示；1 秒延迟
  不适用。

### 安全清除

`SAFE_CONFIRMATION_SECONDS` 保持 0.2 秒：

- transient 风险后进入 clear-pending 时，保持上一份 effective protected 数据，其样式仍为 `off`；
- sustained 风险后进入 clear-pending 时，保持已显示的最终样式；
- deadline 后再次确认安全才清除并恢复捕获；风险返回则取消清除并延续原 episode。

## 状态与时间语义

| presentation phase | 捕获/AX | 有效样式 | 原因浮层 | deadline |
|---|---|---|---|---|
| `inactive` | 允许 | 配置值但不渲染 | 不适用 | 无 |
| 任意 `transient-*` | 阻止或沿用既有 failure policy | `off` | 禁用 | episode 起点 + 1.0s |
| 任意 `sustained-*` | 阻止或沿用既有 failure policy | 最新配置样式 | 非 `off` 时启用 | 无 |
| `clear-pending` | 阻止 | 保持上一有效样式 | 保持上一阶段策略 | 首次安全 + 0.2s |
| bypass pause/hard failure | 既有策略 | 既有立即样式 | 启用 | 无 |

固定策略改为：

```text
PROTECTED_PROMOTION_SECONDS = 1.0
SAFE_CONFIRMATION_SECONDS = 0.2
```

两者继续使用 monotonic clock，不受 wall clock、时区或时间同步影响。

## 实现边界

只在 Python presentation smoother 中改变策略：

- 将默认 promotion 从 0.8 秒改为 1.0 秒；
- 未 promotion 的所有可平滑风险都把 effective style 设为 `off`；
- 保留 raw/effective state、protected display/window IDs、结构化原因、failure reason 和 AX policy；
- 保留 transient/sustained phase 及 promotion generation，使 diagnostics、monitor deadline、配置热加载
  和因果授权继续可观测。

不在 Swift helper 中特殊隐藏 `quiet-shield`。否则 helper 画面会与 Python diagnostics 的
`indicator_style` 不一致。不停止发布 transient decision；scheduler 与 AX gate 仍需使用它立即阻断，
monitor 也需要 deadline 和 acknowledgement 语义。

## 配置热加载

- transient 中修改最终样式或 placement：保持视觉 `off`，不重置 episode；promotion 时使用最新值。
- transient 中改为 `off`：promotion 后继续不可见，原因保持禁用。
- transient 中从 `off` 改为可见样式：仍等到原 1 秒 deadline，随后显示新样式。
- sustained 中修改样式或 placement：沿用现有下一 generation 生效逻辑。

## 错误处理

- diagnostics guard invalid、pause state unavailable 和其他不可平滑硬故障继续立即 bypass；不能为了
  消除闪烁而隐藏安全故障。
- helper 启动、写入、解码或 acknowledgement 失败继续 fail-closed。
- 非递增 generation、非法内部状态和 stop/drain 行为保持既有处理。
- 日志与 category-only diagnostics 只需记录 phase、style、deadline、generation 和保护类别；实机验证
  不读取或输出窗口标题、URL、规则内容或真实隐私数据。

## 测试

### 状态机

- 普通 protected 在 0.000s 与 0.999s 均为 `transient-protected`、style `off`、原因禁用、AX blocked。
- 精确 1.000s 进入 `sustained-protected`，直接使用 `pill`，原因启用，deadline 清空。
- history/mapping fallback、title uncertainty 和 allowlisted mapping failure 使用相同 1 秒边界，
  transient 均为 `off`。
- 风险类别、display/window/reason、最终样式或 placement 变化不重启 deadline。
- 显式 `quiet-shield` 在 transient 不显示，满 1 秒才显示。
- 显式 `off` 始终不显示。
- transient 风险不足 1 秒后进入 clear-pending，全程 style `off`，但捕获继续阻止至 200ms 确认完成。
- paused 和不可平滑 failed 继续立即 bypass。

### 监控器与下游

- worker 在 1 秒 promotion deadline 主动唤醒，不依赖新的 inventory 事件。
- transient decision 仍发布给 scheduler、AX gate、listeners 和 category diagnostics。
- style `off` 时不创建保护浮层窗口，但 decision 可确认且保护策略立即生效。
- promotion 产生 fresh generation，并使用最新配置、helper acknowledgement 和授权 fingerprint。
- clear-pending、stop/drain、外部 deadline publication 和配置 reload 回归测试全部通过。
- 更新所有写死 800ms/0.8、`quiet-shield` transient 预期的 Python 测试与用户文档。
- Swift helper 与 macOS App 协议不变，但仍运行其完整回归测试。

## 实机验收

只使用空白、无真实隐私内容的测试窗口：

1. 触发短于 1 秒的保护 episode：画面无浮层，category-only diagnostics 显示 transient/style off，
   截图和 AX 从首帧被阻止。
2. 持续保护：前 1 秒无浮层，精确 promotion 后直接出现完整“已保护”胶囊，不出现小盾牌中间态。
3. 退出保护：维持既有约 200ms 安全确认后清除并恢复捕获。
4. 验证主屏、副屏、Space/Mission Control 过渡、mapping fallback 和普通 mapped protected。
5. 运行 Python、Swift helper 和 macOS App 测试，重新安装 backend/helper 与 App。
6. 安装后确认唯一 `OpenChronicle.app -> openchronicle start --foreground -> mac-ax-watcher`
   进程链、active/healthy 状态和 category-only live diagnostics。

## 验收标准

- 任何自动 transient 风险都不再显示或闪过小盾牌。
- 风险持续满 1 秒后直接显示当前配置的完整标志。
- 视觉静默期间截图与 AX 仍从第一帧 fail-closed。
- 200ms 安全清除、旁路故障、配置热加载、generation/acknowledgement 和授权因果关系不回退。
- 自动化与空白窗口实机验收通过，安装版本与仓库实现一致。
