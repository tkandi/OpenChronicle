# 映射失败保护标识平滑设计

日期：2026-08-25
状态：已实现，等待最后跨状态实机复验

## 背景

现有保护标识平滑已经覆盖正常 raw `protected` episode：第一帧立即阻止截图和 AX，前 800ms
显示 `quiet-shield`，持续风险再升级为配置样式，恢复安全后经过 200ms 双确认才清除。

实机复验表明，macOS 的 Mission Control 和 Space 动画经常不产生连续的正常 `protected`，而是短暂
报告以下 raw `failed`：

- `active_window_unmapped`
- `sensitive_window_unmapped`

当前状态机对所有 `failed` 都立即 `bypass`。因此截图和 AX 正确地第一帧 fail-closed，但浮层也立即
显示完整“截图已停用”故障提示。正常 `protected` 的视觉平滑已经生效，用户切屏时仍看到的完整提示
来自这条 FAILED bypass 路径。

本机 category-only 实机记录直接观察到了：

- 正常映射时 `transient-protected / quiet-shield` 会升级为 `sustained-protected / pill`；
- 切屏动画时会在 `active_window_unmapped`、`sensitive_window_unmapped` 的 `failed / bypass` 与正常
  `protected` 之间切换；
- FAILED 期间两屏继续 fail-closed，没有截图或 AX fail-open。

## 已选方案

只把上述两类映射失败纳入**视觉风险 episode**。检测、effective FAILED 状态和 capture policy 不变；
只让它们在前 800ms 使用轻量盾牌，持续后才显示完整故障提示。

用户已批准这一选择。

未选方案：

1. 平滑所有 FAILED：会延迟 helper、inventory、pause、diagnostics 和内部状态损坏等真实故障的完整警告，
   范围过大。
2. 延迟或忽略 unmapped 检测：可能在 Mission Control 缩略图或 Space 动画包含敏感像素时 fail-open，
   不可接受。
3. 保持现状：安全但不能解决实际切屏时完整“截图已停用”闪烁。

## 目标

- 两类 unmapped raw FAILED 仍在第一帧执行既有截图/AX failure policy。
- 默认/current fail-closed 和 filtered modes 下，第一帧仍全局阻止截图和 AX。
- 前 800ms 只显示 `quiet-shield`，不显示完整故障胶囊、banner 或 border。
- 持续达到 800ms 后显示用户配置样式和 failure category reason。
- unmapped FAILED 与正常 PROTECTED 互相转换时保持同一风险 episode，不重新计时。
- episode 恢复 raw inactive 后仍要求 200ms 双安全确认。
- 其他 FAILED、paused、off、helper acknowledgement 和 legacy fail-open 语义不变。

## 非目标

- 不把 unmapped 状态降级为 PROTECTED 或 INACTIVE。
- 不改变 `failure_requires_fail_closed()`。
- 不改变 denylist、CG/AX 窗口映射、ScreenCaptureKit 或 screenshot filtering。
- 不增加 Mission Control 检测、私有 API、线程、Timer、配置项或设置页控件。
- 不隐藏持续映射故障；超过 800ms 后必须显示完整失败提示。
- 不平滑 inventory/helper/empty-display/invalid-display/pause/diagnostics/presentation-state 等硬故障。

## 分类

新增固定集合 `PRESENTATION_SMOOTHED_FAILURES`，只包含：

```text
active_window_unmapped
sensitive_window_unmapped
```

以下均为硬故障并继续立即 bypass，列表不穷举未来新增项：

- inventory/helper unavailable、timeout、exit、invalid output；
- empty/invalid display inventory；
- multiple active windows；
- pause state unavailable；
- diagnostics guard invalid；
- presentation state invalid；
- 任何不在固定集合中的当前或未来 failure reason。

采用 allowlist 而不是 denylist，确保新增故障默认保持立即警告。

## 状态与用户可见行为

### 正常进入

raw `protected` 或 allowlisted raw `failed` 从无 episode 状态进入时：

1. effective state 保持 raw state；FAILED 绝不伪装为 PROTECTED。
2. capture policy 立即使用 effective state 和既有 failure policy。
3. 非 `off` 样式替换为 `quiet-shield`。
4. overlay reasons 暂时不发送，但 effective snapshot reasons 保留。
5. 记录共享 episode 起点和 800ms deadline。

### 持续 800ms

- raw `protected` 发布 `sustained-protected`；
- allowlisted raw `failed` 发布 `sustained-mapping-failure`；
- 两者均恢复最新配置样式与 overlay reasons；
- 每次 promotion 使用新 generation 和同代 helper acknowledgement/window IDs。

### PROTECTED 与 mapping FAILED 互转

- `protected -> allowlisted failed` 不重置 episode；
- `allowlisted failed -> protected` 不重置 episode；
- 两种 allowlisted failure reason 互转不重置 episode；
- 当前时刻若未满 800ms，继续 quiet shield；已经持续满 800ms，立即使用配置样式；
- effective state、protected display 集合和 reasons 始终来自本次 raw snapshot，不能沿用错误类型。

### 安全清除

raw inactive 到达时进入现有 `clear-pending`：

- 保留上一份 effective 风险 snapshot 的 state 和数据；上一份可能是 PROTECTED，也可能是 FAILED；
- capture policy 继续按该 held snapshot 执行；
- 200ms 后 fresh inventory 再次 raw inactive 才发布 inactive；
- deadline 前重新出现 raw protected 或 allowlisted raw failed 时，取消 clear-pending，并继续原 episode；
- 返回风险时不得短暂发布 inactive generation。

### 硬故障与暂停

任意硬故障或 paused：

- 立即终止视觉 episode 和 clear-pending；
- 立即使用现有 configured failure/pause presentation；
- reasons enabled；
- 不等待 800ms 或 200ms。

硬故障结束后若进入 protected 或 allowlisted mapping failure，开始新的 episode。

### Off 与 legacy fail-open

- `privacy_indicator_style = "off"` 时仍不创建浮层；capture policy 不变。
- `screenshot_privacy_fail_closed = false` 的 legacy `skip-monitor`/`off` 失败策略继续由
  `failure_requires_fail_closed()` 决定；本修订不把它改成 fail-closed。
- 即使 mapping FAILED 的 presentation result 为 quiet shield，monitor 的既有 fail-open 分支仍清除浮层
  并按旧策略执行。

## 状态机接口

在 `ProtectionPresentationPhase` 增加：

```text
transient-mapping-failure
sustained-mapping-failure
```

`ProtectionPresentationSmoother.resolve()` 继续返回：

- effective `ProtectionSnapshot`；
- presentation phase；
- next deadline；
- `overlay_reasons_enabled`。

内部保存的 `_last_effective_protected` 重命名为描述风险语义的 `_last_effective_risk`。它可以保存
PROTECTED 或 allowlisted FAILED，但不能保存 paused、硬故障或 inactive。

平滑器仍是纯函数式状态容器：不读配置文件、不读 inventory、不调用 helper、不记录日志、不启动线程。

## Monitor 与 diagnostics

- monitor 仍只发布 smoother 返回的 effective decision。
- deadline 继续使用现有单 monitor thread/Event。
- raw/effective state、phase、style、reason visibility 继续通过 owner-only category diagnostics 发布。
- schema-v1 snapshot 加性发布 `snapshot_created_monotonic`（snapshot 的创建 monotonic）和
  `presentation_deadline_monotonic`（当前 smoothing deadline 或 null）。两者只接受有限数值；
  非有限异常值序列化为 null，且不影响既有 fail-closed 或 overlay 路径。
- mapping failure transient 的 diagnostics 必须明确显示：
  `raw_state=failed`、`state=failed`、`phase=transient-mapping-failure`、
  `style=quiet-shield`、`overlay_reasons_enabled=false`。
- sustained 后必须显示 `phase=sustained-mapping-failure` 和配置样式。
- 不新增窗口标题、URL、app/bundle、rule、exact reason、截图或 AX 内容。

## 错误与不变量

- generation 必须严格递增。
- episode 存在时必须有上一份 effective risk snapshot。
- held risk snapshot 只能是 PROTECTED 或 allowlisted FAILED。
- 非 allowlisted FAILED 若进入 risk state machine，必须视为内部错误并由 monitor 映射为
  `presentation_state_invalid` fail-closed，而不是静默平滑。
- smoother 自身错误仍立即 bypass，不能被本修订重新平滑。
- 日志只允许 state、phase、failure category、generation、display IDs 和 confirmation，不允许 exact 值。

## 测试

### 纯状态机

- 两种 mapping failure 首帧均为 FAILED + transient-mapping-failure + quiet-shield；
- 精确 799ms/800ms promotion；
- failure reason 互转不重置 deadline；
- protected 与 mapping failure 双向互转不重置 deadline；
- sustained failure 恢复 protected 后保持 sustained；
- mapping failure -> raw inactive 进入 clear-pending，200ms 双确认；
- clear-pending -> mapping failure/protected 取消清除且无 inactive；
- 每一种硬故障参数化验证立即 bypass；
- off、paused、legacy fail-open monitor policy；
- orphan/非法 held risk 状态继续抛 `ProtectionSmoothingError`。

### Monitor、overlay 与 scheduler

- transient mapping FAILED 保持 `failure_requires_fail_closed` 结果不变；
- default/current fail-closed 下两屏 screenshot/AX 第一帧 blocked；
- transient command 不含 overlay reasons，snapshot/diagnostics reasons 完整；
- 800ms deadline 会 fresh inventory 并产生新 acknowledged generation；
- FAILED -> PROTECTED -> FAILED 快速切换无 full pill 和无 inactive；
- helper unconfirmed、window ID 异常、stop race 和 authorization fingerprint 回归；
- hard failure 不经过 quiet shield；
- category diagnostics raw/effective/phase/style 字段正确且不含 private marker。

### 实机

只用空白 Edge InPrivate：

1. 快速 Space/F3 动画出现 allowlisted FAILED 时，两屏立即 blocked，但前 800ms 只显示 quiet shield；
2. FAILED 持续超过 800ms 时升级完整故障提示；
3. mapping 恢复为 PROTECTED 时 episode 不重置；
4. raw inactive 后观察 clear-pending，快速返回风险不发布 inactive；
5. 主屏、副屏和跨屏移动均验证；
6. 只记录 category diagnostics、固定日志、capture field presence 和 PIDs。

## 验证证据

- 状态标记后的完整门禁：Python `603 passed in 56.99s`；Swift App `89 tests, 0 failures`（3.794s）；证据文档编辑前 tracked worktree clean。
- gen114-116 的 observer receipt 流仍证明 transient/恢复/升级 category 行为，但 observer 时间不再用于主张 FAILED -> PROTECTED 的 source-deadline continuity；该最后跨状态实机复验仍待完成。
- 快速 Space 的五个独立 episode（gen152、171、176、182、196）均在 transient mapping failure 时保持 quiet/reasons false，未使用配置 pill；当前安装的跨屏移动中，gen1404 同时保护 display1/display4，gen1405 仅 display4 保护且 AX blocked，gen1414 在 display4 clear-pending，gen1416 两屏 inactive。
- raw inactive clear-pending 后，重复实机尝试未能在 200ms 内诱发任何 raw risk state；自动化 monitor 测试覆盖返回 PROTECTED 与返回 allowlisted mapping FAILED 两种有效取消路径。
- 当前安装的两个 capture JSON 仅以字段存在性检查：`2026-08-25T20-43-42p08-00.json`、`2026-08-25T20-46-17p08-00.json` 均为 `ax_skipped=protected_display`，且仅 monitor index 2 有 image presence；未解码或显示 base64/text/content。
- 本轮为 deadline 的直接 source-time 可观测性增加 schema-v1 category 字段；自动化已覆盖 transient、FAILED -> PROTECTED 保持 deadline、promotion null、旧/新 Swift payload 兼容和非有限值 null，最终安装/实机复验待完成。
- 直接 source-time 已确认三次 +0.8s transient deadline 与 deadline 后 promotion：gen534 为 530175.144708625 -> 530175.9447086251（+0.8000000001s），gen535 在 530175.945136791 后 deadline null；gen630 为 530279.504487416 -> 530280.304487416（+0.8s），gen631 在 530280.306086166 后 promotion；gen677 为 530330.034808083 -> 530330.834808083（+0.8s），gen680 在 530330.840057 后 promotion。gen539 clear-pending 为 530180.380178958 -> 530180.5801789579（+0.2s），gen540 在 530180.585507833 后 inactive。仅记录 monotonic/deadline/category 字段；synthetic TextEdit 仍为 `sensitive_window_unmapped`，故不主张跨状态 source continuity。

## 验收标准

- 正常 PROTECTED 与两类 mapping FAILED 的短暂切屏 episode 都不再闪完整“截图已停用”。
- 所有路径截图/AX policy 与修订前完全一致，不出现 fail-open 回归。
- mapping FAILED 持续 800ms 后完整故障提示可靠出现。
- 硬故障仍第一帧显示完整提示。
- 200ms 双确认和 clear cancellation 同时适用于 PROTECTED/mapping FAILED 混合 episode。
- diagnostics 能直接区分 transient/sustained mapping failure，且不泄漏 exact 数据。
- 自动化测试、完整安装与空白 InPrivate 实机验证通过。
