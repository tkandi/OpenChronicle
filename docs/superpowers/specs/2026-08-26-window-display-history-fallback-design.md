# 窗口显示器历史回退与映射阶段静默设计

日期：2026-08-26
状态：已确认，等待实现

## 背景

OpenChronicle 使用 WindowServer/CoreGraphics 窗口清单和 AX 元数据判断敏感窗口位于哪块显示器。
`optionOnScreenOnly` 表示窗口仍属于当前 WindowServer 场景，不等于其像素一定没有被更高层窗口完全
遮挡。进入全屏、切换 Space 或 Mission Control 时，macOS 还可能短暂返回不与任何活动显示器相交的
窗口边界。

当前行为是：只要敏感窗口匹配 denylist、但无法映射到显示器，就产生
`sensitive_window_unmapped` 或 `active_window_unmapped`。默认 fail-closed 会把该状态视为全局 FAILED，
导致两块显示器都暂停截图并显示完整“截图已暂停”。

这在以下场景不合理：

- InPrivate 原本可靠地位于主屏；
- 一个普通窗口在主屏全屏，InPrivate 被盖在下面；
- 副屏根本没有 InPrivate；
- macOS 暂时把 InPrivate 报告为 unmapped；
- 当前实现因此让两屏都 FAILED。

用户明确接受主屏继续保守地“已保护”，但不能接受无关副屏也全局暂停，也不希望切屏时轻量盾牌闪一下。

## 已选方案

采用**窗口最后可靠显示器历史回退**，不做像素遮挡推断：

1. 记录窗口最后一次通过真实边界可靠映射到的 display IDs；
2. 同一窗口暂时 unmapped 时，回退到该历史 display 集合；
3. 将其视为 per-display PROTECTED，而不是全局 FAILED；
4. history fallback 和 unmapped failure 的 transient presentation 完全静默；
5. 真正可见、当前边界可可靠映射的敏感窗口仍沿用现有 quiet-shield 行为。

用户已确认选择该方案和“只对映射不确定阶段静默”的范围。

## 未选方案

### 像素/窗口遮挡推断

根据 z-order、矩形边界和 alpha 判断下层敏感窗口是否被全屏窗口覆盖。该方案看似更精确，但标准窗口
可能有透明区域、圆角、阴影、子窗口和动画，WindowServer 元数据不能可靠证明所有敏感像素都不可见。
错误判断会直接造成截图泄漏，因此不采用。

### 仅隐藏全局 FAILED 提示

保持两屏 capture 暂停，只隐藏浮层。它没有解决副屏记录丢失，用户明确不接受。

### 所有保护事件前 800ms 静默

会让真正可见的 InPrivate 和台前调度缩略图也失去即时保护反馈。用户选择只静默映射不确定阶段。

## 目标

- 已有可靠历史时，unmapped 敏感窗口只保护历史 display IDs。
- `screenshot_monitor="separate"` 下，无关显示器继续截图。
- history fallback 的 effective state 为 PROTECTED，不显示全局 FAILED 文案。
- 当前全屏显示器可以保守地继续保护，即使 InPrivate 实际被完全遮挡。
- history fallback 和 allowlisted mapping FAILED 在前 800ms 不创建任何浮层。
- 映射不确定持续超过 800ms 后，恢复用户配置的完整样式和 category reason。
- 当前边界可靠映射的敏感窗口保持现有 transient quiet-shield。
- 无历史时继续使用现有全局 fail-closed；前 800ms 静默，持续后显示完整故障。
- 不改变 legacy fail-open、hard failure、diagnostics guard、helper acknowledgement 或 capture authorization。

## 非目标

- 不推断窗口像素是否被遮挡。
- 不保证 fullscreen 覆盖时主屏继续截图；主屏保守保护是允许的。
- 不改变 `screenshot_monitor="all"` 的整体截图语义；all 模式仍可能保护全部显示器。
- 不持久化窗口历史到磁盘。
- 不把窗口标题、URL、app/bundle 或 rule 写入日志或 diagnostics。
- 不新增设置项、Timer、线程、Mission Control/Space 检测或私有 API。
- 不允许 history fallback 进入 window-filtered capture。

## 数据模型

### WindowIdentity

历史键由以下稳定类别组成：

```text
window_id
owner_key = non-empty bundle_id, otherwise normalized app_name
```

无有效 `window_id` 的窗口不使用历史。owner key 不写日志、不进入 diagnostics、不持久化。

### HistoryEntry

```text
identity
display_ids: frozenset[int]
last_seen_monotonic: float
```

`display_ids` 只来自窗口真实矩形与活动显示器的正面积相交，绝不由最近显示器距离或鼠标位置猜测。

### VisibleWindow 扩展

新增 keyword-only 内部字段：

```python
fallback_display_ids: frozenset[int] = frozenset()
```

该字段仅由 history resolver 填充。原生 helper wire 不新增字段。

### ProtectionSnapshot 扩展

新增 keyword-only category 字段：

```python
display_mapping_fallback_active: bool = False
```

它不包含窗口身份或 exact 数据，用于 presentation 和 owner-only diagnostics。

## WindowDisplayHistory

新增纯 Python 单元 `WindowDisplayHistory`，由 protection monitor 独占，不启动线程、不读写文件、不记录日志。

### 实际映射

每次 inventory：

1. 计算每个窗口与当前活动显示器的正面积交集；
2. 若至少命中一块显示器且 window ID 有效，覆盖更新 history entry；
3. 输出窗口的 `fallback_display_ids` 为空，明确优先使用实际边界。

跨屏窗口可以保存多个 display IDs。

### 当前样本 unmapped

窗口当前没有任何真实显示器交集时：

- identity 与 history entry 完全一致；
- entry 中至少一个 display ID 仍属于当前活动显示器；
- 则把仍有效的历史 IDs 写入 `fallback_display_ids`；
- 更新 `last_seen_monotonic`，因此窗口只要持续出现在 inventory 中，历史不会因全屏持续时间而过期。

### 窗口暂时消失

窗口不在当前 inventory 时，entry 最多保留 5 秒：

```text
WINDOW_DISPLAY_HISTORY_ABSENCE_SECONDS = 5.0
```

这覆盖 Space 动画中的短暂消失。超过 5 秒删除，防止 window ID 被新窗口复用。窗口在 absence 后以不同
owner key 出现时立即拒绝旧 entry。

### 显示器变化

每次 resolve 都将 entry display IDs 与当前活动 display IDs 相交：

- 仍有有效 ID：只使用有效子集；
- 全部失效：不使用历史并删除 entry。

### 重复/非法身份

- `window_id <= 0`、布尔值、超过 UInt32 或缺失：不缓存；
- 同一 inventory 中重复 window ID：该 ID 本次和已有 entry 均视为不可信并删除；
- owner key 为空：不缓存；
- monotonic 非递增：抛固定内部错误，由 monitor 走现有 fail-closed presentation-state 路径。

## ProtectionSnapshot 构建

新增统一 `_display_ids_for_window(window, displays)`：

1. 有实际正面积交集：返回实际 IDs；
2. 否则返回 `fallback_display_ids ∩ active_display_ids`；
3. 两者都没有：返回空集合。

该函数统一用于：

- active window display；
- active candidate displays；
- sensitive window matched displays；
- `has_unmapped_sensitive_window`；
- reason display IDs。

### history fallback 结果

只要本次保护判断实际使用了 fallback display IDs：

- `state = PROTECTED`，而不是 mapping FAILED；
- `protected_display_ids` 按现有 separate/all 语义计算；
- `display_mapping_fallback_active = true`；
- direct denylist reasons 仍归属历史 display IDs；
- `window_filterable = false`；
- 不允许使用已经无效的窗口 region/ID 做 mask-window 或 exclude-window authorization。

`skip-monitor` 或 filtered fallback 只能跳过/遮蔽整个受保护显示器，另一显示器可以继续。

### 无历史

敏感窗口仍无实际/历史 display IDs 时，继续产生 `sensitive_window_unmapped`。active window 同理继续产生
`active_window_unmapped`。现有 failure policy 不变。

### diagnostics guard

`diagnostics_guard_invalid` 等 hard failure 始终优先，history fallback 不得覆盖或降级它们。

## Presentation

### 新 phases

```text
transient-mapping-fallback
sustained-mapping-fallback
```

### transient 样式

当风险 episode 未满 800ms：

| raw/effective 情况 | transient style | overlay reasons |
|---|---|---|
| 正常可靠 PROTECTED | quiet-shield | false |
| PROTECTED + history fallback | off | false |
| allowlisted mapping FAILED | off | false |

style `off` 在这里代表“presentation 静默”，不改变 effective state 或 capture policy。

### sustained 样式

满 800ms 后均使用最新用户配置样式和 reasons：

- history fallback PROTECTED：`sustained-mapping-fallback`；
- mapping FAILED：`sustained-mapping-failure`；
- 正常 PROTECTED：`sustained-protected`。

### episode continuity

以下风险状态互转不重置 timer：

- normal PROTECTED；
- history fallback PROTECTED；
- allowlisted mapping FAILED。

因此已持续超过 800ms 的 InPrivate 被普通全屏窗口覆盖后，可以继续显示完整“已保护”；用户已接受这一
保守行为。只有新开始、短暂的映射不确定阶段完全静默。

### clear pending

继续使用现有 200ms 双安全确认。held snapshot 保留其 state、fallback flag、protected displays 和 reasons。
deadline 前风险返回取消清除，不发布 inactive。

## Capture 与 AX 语义

- history fallback PROTECTED display 在第一帧立即阻止该显示器截图；
- `separate` 下其他显示器继续截图；
- active/frontmost AX 位于 protected display 时 AX blocked，否则沿用现有 per-display active-window policy；
- history fallback 强制 `window_filterable=false`，避免局部窗口过滤；
- transient style off 仍由 state/policy 阻断，不依赖浮层窗口存在；
- unconfirmed clear、legacy fail-open、off 和 diagnostics booleans 继续使用已统一的 resolved policy。

## Diagnostics 与日志

owner-only category snapshot 新增：

```text
display_mapping_fallback_active: boolean
```

继续发布 raw/effective state、phase、style、reason visibility、source monotonic/deadline 和 blocked booleans。
不得发布 history identity、owner key、title、URL、rule 或 cached timestamp。

日志只允许：

- generation；
- phase/state；
- fallback used boolean；
- protected display IDs；
- confirmation。

## 错误处理

- history resolver 内部错误映射为现有 `presentation_state_invalid`，无 exception 正文日志；
- cache miss 不属于内部错误，继续现有 mapping FAILED；
- invalid/duplicate ID 只禁用该窗口历史，不信任旧映射；
- hard failure 会清除 presentation episode，但 history cache 可以保留；恢复 inventory 后仍按 identity/TTL 验证；
- monitor stop 后丢弃内存历史，不产生 callback 或持久文件。

## 测试

### WindowDisplayHistory 纯测试

- 单屏/跨屏实际映射写入；
- 当前 unmapped 使用同 identity 历史；
- 持续 present 的 unmapped 窗口长期保留；
- absent 4.999 秒可复用、5.0 秒后拒绝；
- owner mismatch、invalid ID、duplicate ID、display removal；
- mapped 数据永远覆盖旧 fallback；
- generation/monotonic invariant。

### Snapshot builder

- sensitive history fallback 只生成对应 display PROTECTED；
- 副屏不受保护；
- active display 使用 fallback；
- direct reasons 归属 fallback display；
- `window_filterable=false`；
- no-history 仍 mapping FAILED；
- all 模式仍全显示器保护；
- diagnostics guard invalid 仍 hard failure。

### Presentation

- normal PROTECTED transient 仍 quiet-shield；
- history fallback transient style off + fallback phase；
- mapping FAILED transient style off；
- 799ms/800ms 精确 promotion；
- normal/fallback/FAILED 互转不重置；
- clear-pending 保留 fallback flag；
- hard failure、paused 和 configured off。

### Monitor/scheduler/diagnostics

- mapped InPrivate → same window unmapped，history 命中后只保护原 display；
- unrelated display screenshot helper 继续运行；
- protected display 和 AX 正确 blocked；
- fallback 决策不进入 window-filtered capture；
- cache miss 仍全局 failure policy；
- category diagnostics 字段正确且不含 exact marker；
- stop、reload、helper ack、late ack 和 unconfirmed clear 回归。

## 实机验收

只使用空白 InPrivate：

1. 先让 InPrivate 在主屏可靠映射；
2. 用普通窗口在主屏全屏覆盖；
3. 若 macOS 报告 unmapped，category diagnostics 必须显示 history fallback PROTECTED，仅主屏 blocked；
4. 副屏保持 inactive 并继续产生结构化 screenshot presence；
5. 不出现两屏全局“截图已暂停”；
6. 快速 Space/F3 映射阶段前 800ms style off，不出现轻量盾牌闪烁；
7. 真正可见的 InPrivate/台前调度缩略图仍显示正常保护标识；
8. 持续 mapping uncertainty 超过 800ms 后显示完整配置提示；
9. 主屏、副屏和跨屏移动重复；
10. 只记录 category fields、固定日志、monitor IDs、presence booleans 和 PIDs。

## 验收标准

- 全屏遮挡导致的 temporary unmapped 不再让无关显示器全局 FAILED。
- separate 模式只保护历史 display，另一屏继续 capture。
- current full-screen display 可以保守保持 PROTECTED，不要求判断像素遮挡。
- mapping uncertainty 的短暂 episode 完全无浮层闪烁。
- 正常可见隐私窗口仍有即时 quiet-shield。
- cache miss、hard failure 和 legacy policy 保持安全边界。
- history fallback 不使用 stale window filtering。
- 自动化、完整安装、配置哈希、进程链和双屏实机验收全部通过。
