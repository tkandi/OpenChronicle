# 暂停状态读取失败时强制关闭捕获

## 摘要

当 OpenChronicle 无法确认当前是否处于手动暂停状态时，必须禁止捕获。暂停状态读取失败属于
控制面的不确定性，不是普通的屏幕窗口清单失败，因此即使
`screenshot_privacy_fail_closed = false` 也不能放行。

## 根因

`PrivacyProtectionMonitor._read_protection_inputs()` 目前把暂停读取器抛出的异常编码成
`inventory_unavailable`。调度器在用户明确配置 inventory fail-open 时会放行这种普通失败，
导致暂停读取失败的原始类型在策略判断前丢失。

## 设计决定

新增专用失败原因 `pause_state_unavailable`。携带该原因的保护快照继续使用现有黄色
`failed` 视觉状态，但在捕获策略中始终属于终止状态。

所有消费者统一使用一个策略谓词计算有效的 fail-closed 行为：

- `pause_state_unavailable` 始终强制 fail-closed；
- 其他 `failed` 原因继续遵循 `screenshot_privacy_fail_closed`；
- `paused` 始终终止捕获；
- `inactive` 和 `protected` 行为不变。

调度器的 AX 前置检查、AX 后置复核、覆盖层的 render/clear 选择和脱敏失败日志都必须使用
同一策略，避免同一快照在不同组件中得到相互矛盾的处理。

## 运行行为

暂停读取器抛出异常时：

1. 监控器记录 `pause_state_unavailable`，日志只保留异常类型，不记录路径、标记文件内容或
   异常文本。
2. 发布黄色 `failed` 快照并要求所有屏幕显示失败标识；没有屏幕清单时，由覆盖层 helper
   在本机解析全部屏幕。
3. AX 前置 gate 会阻止 AX 遍历；如果暂停状态是在 AX 后置复核时才被发现为新近不可读，
   调度器会丢弃已经读取到内存的 AX，且不会继续截图、持久化 capture JSON、写入 FTS，
   或进入 timeline、memory 与模型处理。
4. 本次不会产生截图或任何下游制品。
5. 监控器继续轮询；暂停状态恢复可读后，自动依据当前窗口清单恢复正常状态。

普通窗口清单失败在明确配置时仍可 fail-open。本补丁不会扩大其他错误的 fail-closed 范围。

## 测试

- 监控器测试证明暂停读取异常产生 `pause_state_unavailable`，并在 fail-open 配置下仍渲染
  黄色失败覆盖层而不是清除标识。
- 调度器测试证明 AX 前置 gate 不调用 AX provider，而 AX 后置 gate 会先记录一次 provider
  调用，再丢弃已经读取到内存的 AX；两条路径都不会进入截图、持久化、FTS、timeline、
  memory 或模型处理。
- 现有 inventory fail-open 回归测试必须继续通过。
- 完成前重新运行完整 Python/Swift 测试、打包检查、安装后进程链检查和空白 InPrivate
  多屏黑盒测试。

## 非目标

- 不新增覆盖层协议状态或 Swift UI 样式。
- 不改变 `.paused` 文件格式和暂停/恢复操作。
- 不把所有截图 inventory 失败都改成强制 fail-closed。
- 不包含无关重构。
