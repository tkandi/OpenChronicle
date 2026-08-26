# Inventory 事务校验与标题不确定瞬态静默设计

日期：2026-08-26
状态：已实现并验证

## 背景

窗口显示器历史回退的首轮实现已经通过自动化并完成部分实机验证，但最终整合审查发现两个阻断问题：

1. 一个 display ID 重复的无效 inventory 会先改写历史缓存，随后才在 snapshot builder 中被判为
   `invalid_display_inventory`。下一代 unmapped 窗口可能错误回退到失败样本提供的显示器。
2. 无 InPrivate 窗口时，300ms 的系统级 F3 往返会让普通 Edge 窗口标题短暂不可读，产生仅含
   `window_title_unknown` 的 PROTECTED 决策，并立即显示 `quiet-shield`。这与“切屏不闪轻量盾牌”的用户验收
   要求冲突。

本设计补充
`docs/superpowers/specs/2026-08-26-window-display-history-fallback-design.md`，不替换其中已经成立的
per-display history fallback、capture/AX fail-closed 和诊断隐私边界。

## 已选方案

### 1. 单一 inventory 结构 preflight

在 `privacy.py` 提供一个无副作用的结构校验函数，供 helper reader、monitor/history 边界和 snapshot builder 共同
使用。校验顺序和固定 failure reason 为：

1. inventory 为 `None`：`inventory_unavailable`；
2. displays 为空：`empty_displays`；
3. display ID 非法或重复、bounds 非有限值、宽高非正：`invalid_display_inventory`；
4. active window 超过一个：`multiple_active_windows`；
5. 其余：结构有效。

有效 display ID 必须是非布尔正整数，且不超过 `UInt32.max`。结构 preflight 不执行隐私规则匹配，也不判断 active
或 sensitive window 是否映射到显示器；后两者仍由 snapshot builder 在 history resolve 后处理。

### 2. 历史更新是事务性的

monitor 只有在 reader 没有显式 failure 且共享 preflight 通过时，才调用 `WindowDisplayHistory.resolve()`。无效样本：

- 不 seed、overwrite、refresh、expire、clear 或迁移任何 history entry；
- 不接受样本携带的 fallback IDs；
- 发布现有固定 failure reason，并按原策略 fail-closed；
- 已有可信 history 原样保留，供下一次有效样本重新验证 identity、display 和 TTL。

`WindowDisplayHistory.resolve()` 自身也做同一防御性 preflight，覆盖直接调用和未来调用者。它可以推进单调时钟水位，
但不得改变映射 entries。返回给失败路径的窗口必须清空外部注入的 `fallback_display_ids`。

当有效 inventory 的活动显示器集合不再包含 entry 的任何 display ID 时，直接删除该 entry，不保留可无限刷新的空
entry。

### 3. 纯标题元数据不确定性

新增 presentation-only 分类 `title_uncertainty_only`。一个 snapshot 只有同时满足以下条件才属于该分类：

- effective state 为 `PROTECTED`；
- 至少有一个 `WINDOW_TITLE_UNKNOWN` reason；
- 所有保护 reason 只允许 `WINDOW_TITLE_UNKNOWN`，以及由同一保护派生的可选
  `MODE_ALL_INHERITED`；
- `display_mapping_fallback_active` 为 false。

只要存在 `APP_RULE`、`BUNDLE_RULE`、`WINDOW_TITLE_RULE`、`DIAGNOSTICS_REVEAL` 或任何其他直接/硬 reason，就不是
纯标题不确定性，继续使用普通 PROTECTED 的即时 `quiet-shield`。

### 4. Presentation 行为

新增 phases：

```text
transient-title-uncertainty
sustained-title-uncertainty
```

纯标题不确定性在风险 episode 前 800ms：

- effective `indicator_style="off"`；
- `overlay_reasons_enabled=false`；
- 不创建 overlay window IDs；
- state、protected displays、reason objects、window filtering、capture confirmation、screenshot blocked 和 AX blocked
  均保持不变。

到达 800ms 后使用最新配置样式；配置样式为 `off` 时继续关闭 reasons。normal PROTECTED、history fallback、mapping
FAILED 和 title uncertainty 继续共享既有风险 episode 与 200ms clear-pending。phase 优先级为：

1. history fallback；
2. title uncertainty only；
3. allowlisted mapping failure；
4. normal protected。

因此真正可见且已知标题匹配的 InPrivate 仍在第一帧显示 `quiet-shield`；F3 中普通窗口仅因标题暂时不可读而产生的
短保护不再闪盾牌。

## 不采用的方案

### 静默所有 PROTECTED

会隐藏真实 InPrivate、密码应用或 app/bundle 直接命中的即时反馈，用户已经拒绝。

### 检测 Mission Control、F3 或 Space

依赖窗口管理器状态和可能变化的系统行为，不能覆盖拖屏、全屏动画或其他标题暂时不可读场景。

### 放宽 unknown-title capture 策略

直接把 `WINDOW_TITLE_UNKNOWN` 当安全窗口会造成隐私 fail-open。本设计仅关闭短暂浮层，截图和 AX 仍从第一帧阻断。

### 对合法 cache miss 猜测显示器

window ID/owner 变化时，没有足够证据确定目标显示器。合法 cache miss 继续使用现有全局 fail-closed，不用 UX 修复
换取隐私风险。

## 数据流

```text
helper output
  -> parse
  -> shared inventory structure preflight
  -> explicit failure: publish FAILED, skip history mutation
  -> valid inventory: history resolve
  -> snapshot builder (active/sensitive mapping and policy)
  -> presentation classifier
       fallback -> mapping-fallback phase
       unknown-only -> title-uncertainty phase
       mapping failed -> mapping-failure phase
       direct match -> normal protected phase
  -> overlay presentation + unchanged scheduler/AX policy
```

## 错误与隐私边界

- preflight 只返回固定 failure code，不记录 display bounds、window ID、owner、标题或规则。
- category diagnostics 只新增 phase 字符串；不新增 exact 字段或 schema version。
- Swift 已将 phase 作为可选字符串解码，无需修改 wire model。
- title uncertainty 的 style `off` 必须主动清理已有 overlay transport，确认 generation 后再允许 scheduler 依据原状态
  行为。
- smoothing 内部错误仍映射为 `presentation_state_invalid` 并全局 fail-closed。

## 自动化验收

### Inventory preflight/history

- reader 拒绝空 displays、重复/非法 display ID、非法 bounds、multiple active；
- builder 与 reader 对相同输入返回相同固定 reason；
- 正常 display 1 seed -> 无效重复 display 2 样本 -> 有效 unmapped，最终只能回退 display 1；
- 无效样本不能 seed、overwrite、refresh、expire 或 clear history；
- resolver 直接收到无效 inventory 也不修改 entries；
- display 全部移除后 entry 被删除；
- 单调时钟回退仍抛固定 `WindowDisplayHistoryError`。

### Title uncertainty

- unknown-only 在 0ms/799ms style off、reasons false，并使用新 transient phase；
- 800ms 使用 configured style 和 sustained phase；
- unknown + `MODE_ALL_INHERITED` 仍是 uncertainty-only；
- unknown + app/bundle/known-title/diagnostics reason 仍是 normal protected/quiet-shield；
- history fallback + unknown 优先使用 mapping-fallback phase；
- clear-pending 保留 off snapshot；返回风险不重置 episode；
- screenshot/AX 从第一帧 blocked，style off 不改变 authorization；
- visible -> off 的 client transport 清理测试继续通过；
- diagnostics payload 不含 exact marker，Swift 旧 payload 继续兼容。

## 实机验收

只使用空白 InPrivate 和 category-only diagnostics：

1. 无 InPrivate 时执行 300ms F3 往返，不得出现 overlay window、quiet shield、pill 或 banner；capture/AX 可以保守阻断；
2. 打开可靠映射的 `about:blank` InPrivate，第一帧必须是 normal transient quiet shield；
3. 普通窗口全屏覆盖后，只保护实际/历史显示器，另一显示器继续结构化截图；
4. title uncertainty 持续超过 800ms 时，必须恢复 configured full style；
5. 主屏、副屏和跨屏移动重复；合法 cache miss 可以全局 fail-closed，但必须是固定 category，不能泄漏 identity；
6. 只记录 generation、state、phase、style、display IDs、blocked booleans、固定 reason code、deadline 和 PID；不读取截图、
   AX 文本、标题、URL、owner 或规则值。

## 完成标准

- 两个最终整合 Important 和 display-removal Minor 全部关闭；
- 完整 Python、Swift、native overlay、changed-file Ruff 和 `git diff --check` 通过；
- 重新安装后 source/site-packages 哈希、配置哈希、签名和唯一进程链通过；
- F3 300ms 无盾牌闪烁，真实 known-title InPrivate 仍即时 quiet shield；
- 原始全屏覆盖和 per-display capture/AX 行为继续成立；
- 最终独立审查无 Critical/Important，设计状态改为 `已实现并验证`。
