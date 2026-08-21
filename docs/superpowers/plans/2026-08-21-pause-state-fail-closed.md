# 暂停状态读取失败强制关闭捕获实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 保留暂停状态读取失败的独立类型，并确保它在 `screenshot_privacy_fail_closed = false` 时仍阻止 AX、截图和 capture JSON。

**Architecture:** `ProtectionFailureReason` 新增 `pause_state_unavailable`，`protection.py` 提供唯一的失败策略谓词。monitor 的浮层与日志、scheduler 的 AX 前置和后置 gate 都消费该谓词；普通 inventory failure 继续遵循现有 fail-open 配置。

**Tech Stack:** Python 3.11+、pytest、Ruff、SwiftPM/XCTest、AppKit helper 现有协议、uv 打包工具。

**Design Specs:** `docs/superpowers/specs/2026-08-21-pause-state-fail-closed-design.md`；中文复核版为 `docs/superpowers/specs/2026-08-21-pause-state-fail-closed-design.zh-CN.md`。

## Global Constraints

- 专用失败原因的配置值固定为 `pause_state_unavailable`。
- 暂停状态未知时必须显示现有黄色 `failed` 标识，并在所有 capture gate 强制 fail closed。
- `screenshot_privacy_fail_closed = false` 仍只允许普通窗口/display inventory failure fail open。
- 不新增保护状态、Swift 浮层协议字段、第三方依赖或 `.paused` 文件格式。
- 日志不得包含暂停文件路径、内容或异常文本；只允许固定原因码和异常类型。
- 所有生产代码遵循 RED → GREEN；先观察新增测试在当前实现上失败。
- 不修改或删除用户现有 capture、memory、timeline、配置值和 denylist。

---

## 文件结构

- Modify: `src/openchronicle/capture/privacy.py`：定义 `PAUSE_STATE_UNAVAILABLE` 固定原因码。
- Modify: `src/openchronicle/capture/protection.py`：提供共享 `failure_requires_fail_closed()` 策略谓词。
- Modify: `src/openchronicle/capture/protection_monitor.py`：保留暂停读取失败类型，并让浮层与日志使用共享策略。
- Modify: `src/openchronicle/capture/scheduler.py`：让 AX 前置、AX 后置和截图 gate 使用共享策略。
- Modify: `tests/test_protection.py`：纯策略矩阵测试。
- Modify: `tests/test_protection_monitor.py`：异常类型、黄色浮层和脱敏日志测试。
- Modify: `tests/test_capture_scheduler_fts.py`：AX 前置与后置强制终止测试，以及普通 inventory fail-open 回归。
- Modify: `docs/capture.md`、`docs/config.md`、`docs/macos-app.md`：记录控制面失败与 inventory 失败的不同语义。
- Modify: `docs/superpowers/specs/2026-08-21-privacy-protection-indicators-design.md` 和中文版本：修正原始总设计中的 fail-open 边界。

---

### Task 1: 类型化暂停读取失败并统一捕获策略

**Files:**
- Modify: `src/openchronicle/capture/privacy.py:56-65`
- Modify: `src/openchronicle/capture/protection.py:22-59`
- Modify: `src/openchronicle/capture/protection_monitor.py:22-27, 184-260`
- Modify: `src/openchronicle/capture/scheduler.py:24-42`
- Test: `tests/test_protection.py`
- Test: `tests/test_protection_monitor.py`
- Test: `tests/test_capture_scheduler_fts.py`

**Interfaces:**
- Produces: `ProtectionFailureReason.PAUSE_STATE_UNAVAILABLE`
- Produces: `failure_requires_fail_closed(cfg: CaptureConfig, snapshot: ProtectionSnapshot) -> bool`
- Consumes: `CaptureConfig.screenshot_privacy_fail_closed`
- Preserves: `ProtectionState.FAILED` and the existing overlay NDJSON schema

- [ ] **Step 1: 写共享策略的失败测试**

在 `tests/test_protection.py` 的 import 中加入 `failure_requires_fail_closed`，并添加：

```python
@pytest.mark.parametrize(
    ("reason", "configured_fail_closed", "expected"),
    [
        (ProtectionFailureReason.PAUSE_STATE_UNAVAILABLE, False, True),
        (ProtectionFailureReason.PAUSE_STATE_UNAVAILABLE, True, True),
        (ProtectionFailureReason.INVENTORY_UNAVAILABLE, False, False),
        (ProtectionFailureReason.INVENTORY_UNAVAILABLE, True, True),
    ],
)
def test_failure_policy_distinguishes_pause_state_from_inventory(
    reason: ProtectionFailureReason,
    configured_fail_closed: bool,
    expected: bool,
) -> None:
    cfg = CaptureConfig(screenshot_privacy_fail_closed=configured_fail_closed)
    snapshot = build_protection_snapshot(
        cfg,
        None,
        paused=False,
        generation=90,
        now=20.0,
        failure_reason=reason,
    )

    assert snapshot.state is ProtectionState.FAILED
    assert failure_requires_fail_closed(cfg, snapshot) is expected
```

- [ ] **Step 2: 写 monitor 的失败测试**

在 `tests/test_protection_monitor.py` 添加：

```python
def test_pause_reader_failure_stays_closed_when_inventory_policy_is_fail_open(
    inventory, fake_overlay, caplog,
) -> None:
    marker = "private-pause-marker-path"
    pause_available = False
    safe_inventory = WindowInventory(windows=(), displays=inventory.displays)

    def read_pause() -> bool:
        if not pause_available:
            raise OSError(marker)
        return False

    cfg = CaptureConfig(
        privacy_indicator_style="pill",
        screenshot_privacy_fail_closed=False,
    )
    monitor = PrivacyProtectionMonitor(
        cfg,
        config_path=Path("/nonexistent/config.toml"),
        overlay=fake_overlay,
        inventory_reader=lambda: safe_inventory,
        pause_reader=read_pause,
    )

    with caplog.at_level(logging.WARNING, logger="openchronicle.capture"):
        decision = monitor.decision_for_capture(force=True)

    assert decision.snapshot.state is ProtectionState.FAILED
    assert (
        decision.snapshot.failure_reason
        is ProtectionFailureReason.PAUSE_STATE_UNAVAILABLE
    )
    assert decision.indicator_confirmed is True
    assert fake_overlay.render_calls == 1
    assert fake_overlay.clear_calls == 0
    assert "privacy protection failed closed: reason=pause_state_unavailable" in caplog.text
    assert marker not in caplog.text

    pause_available = True
    recovered = monitor.decision_for_capture(force=True)

    assert recovered.snapshot.state is ProtectionState.INACTIVE
    assert fake_overlay.clear_calls == 1
```

- [ ] **Step 3: 写 scheduler 前置和后置 gate 的失败测试**

在 `tests/test_capture_scheduler_fts.py`：

1. import `ProtectionFailureReason`；
2. 让 `_protection_decision()` 接受 `failure_reason: ProtectionFailureReason | None = None` 并传给 `ProtectionSnapshot`；
3. 让 `_failed_decision()` 默认使用 `INVENTORY_UNAVAILABLE`，同时接受 `reason` 和 `generation`；
4. 添加以下两个测试：

```python
def test_pause_state_failure_blocks_before_ax_even_when_inventory_is_fail_open(
    ac_root: Path, monkeypatch,
) -> None:
    provider = _FakeProvider(raw_json=_edge_ax_tree("https://safe.example"))
    monitor = _FakeProtectionMonitor(
        _failed_decision(reason=ProtectionFailureReason.PAUSE_STATE_UNAVAILABLE)
    )
    monkeypatch.setattr(
        scheduler_mod.window_meta,
        "active_window",
        lambda: window_meta.WindowMeta(app_name="Cursor", title="main.py", bundle_id="cursor"),
    )
    monkeypatch.setattr(
        scheduler_mod.screenshot,
        "grab_many",
        lambda **_: (_ for _ in ()).throw(AssertionError("screenshot must not run")),
    )

    out = scheduler_mod._build_capture(
        CaptureConfig(screenshot_privacy_fail_closed=False),
        provider,
        None,
        protection_monitor=monitor,
    )

    assert out is None
    assert provider.calls == 0
    assert monitor.force_calls == [True]


def test_pause_state_failure_during_ax_discards_when_inventory_is_fail_open(
    ac_root: Path, monkeypatch,
) -> None:
    provider = _FakeProvider(raw_json=_edge_ax_tree("https://safe.example"))
    monitor = _FakeProtectionMonitor(
        _protection_decision(
            generation=70,
            active_display_id=1,
            protected_ids={2},
            confirmed=True,
        ),
        _failed_decision(
            reason=ProtectionFailureReason.PAUSE_STATE_UNAVAILABLE,
            generation=71,
        ),
    )
    monkeypatch.setattr(
        scheduler_mod.window_meta,
        "active_window",
        lambda: window_meta.WindowMeta(app_name="Cursor", title="main.py", bundle_id="cursor"),
    )

    out = scheduler_mod._build_capture(
        CaptureConfig(
            include_screenshot=False,
            screenshot_privacy_fail_closed=False,
        ),
        provider,
        None,
        protection_monitor=monitor,
    )

    assert out is None
    assert provider.calls == 1
    assert monitor.force_calls == [True, False]
```

- [ ] **Step 4: 运行新增测试并确认 RED**

Run:

```bash
PYTHONPATH=src uv run pytest -q \
  tests/test_protection.py::test_failure_policy_distinguishes_pause_state_from_inventory \
  tests/test_protection_monitor.py::test_pause_reader_failure_stays_closed_when_inventory_policy_is_fail_open \
  tests/test_capture_scheduler_fts.py::test_pause_state_failure_blocks_before_ax_even_when_inventory_is_fail_open \
  tests/test_capture_scheduler_fts.py::test_pause_state_failure_during_ax_discards_when_inventory_is_fail_open
```

Expected: collection 或断言失败，因为专用原因码和共享策略尚不存在，当前 probe 仍产生
`state=failed reason=inventory_unavailable terminal=False ax_blocked=False`。

- [ ] **Step 5: 实现专用原因码和共享策略**

在 `src/openchronicle/capture/privacy.py` 的 `ProtectionFailureReason` 中加入：

```python
PAUSE_STATE_UNAVAILABLE = "pause_state_unavailable"
```

在 `src/openchronicle/capture/protection.py` 加入：

```python
def failure_requires_fail_closed(
    cfg: CaptureConfig,
    snapshot: ProtectionSnapshot,
) -> bool:
    return snapshot.state is ProtectionState.FAILED and (
        cfg.screenshot_privacy_fail_closed
        or snapshot.failure_reason is ProtectionFailureReason.PAUSE_STATE_UNAVAILABLE
    )
```

- [ ] **Step 6: 让 monitor 保留类型并统一浮层与日志策略**

在 `src/openchronicle/capture/protection_monitor.py` import
`failure_requires_fail_closed`，并把 pause reader exception 分支改为：

```python
except Exception as exc:  # A pause-read failure must not allow capture.
    logger.warning("privacy protection pause read failed: %s", type(exc).__name__)
    return False, None, ProtectionFailureReason.PAUSE_STATE_UNAVAILABLE
```

在 `_render()` 中只对真正允许 fail-open 的 failure 清除浮层：

```python
if (
    snapshot.state is ProtectionState.FAILED
    and not failure_requires_fail_closed(self._cfg, snapshot)
):
    if snapshot.indicator_style == "off":
        self._overlay.render(snapshot)
    else:
        self._overlay.clear(snapshot.generation)
    return False
```

在 `_log_failure_transition()` 中计算：

```python
requires_fail_closed = failure_requires_fail_closed(self._cfg, snapshot)
key = (snapshot.failure_reason, requires_fail_closed)
```

并使用 `requires_fail_closed` 选择固定的 `failed closed` 或 `policy=fail_open` 日志。

- [ ] **Step 7: 让 scheduler 两道 gate 使用共享策略**

在 `src/openchronicle/capture/scheduler.py` import
`failure_requires_fail_closed`，并改为：

```python
def _decision_is_terminal(cfg: CaptureConfig, decision: ProtectionDecision) -> bool:
    snapshot = decision.snapshot
    return snapshot.state is ProtectionState.PAUSED or failure_requires_fail_closed(
        cfg, snapshot
    )


def _decision_blocks_ax(cfg: CaptureConfig, decision: ProtectionDecision) -> bool:
    snapshot = decision.snapshot
    return (
        snapshot.state is not ProtectionState.FAILED
        or failure_requires_fail_closed(cfg, snapshot)
    ) and snapshot.ax_blocked
```

现有 `_build_capture()` 的前置和后置调用点不增加第二套条件。

- [ ] **Step 8: 运行聚焦测试并确认 GREEN**

Run:

```bash
PYTHONPATH=src uv run pytest -q \
  tests/test_protection.py \
  tests/test_protection_monitor.py \
  tests/test_capture_scheduler_fts.py
```

Expected: 三个文件全部 PASS，现有
`test_inventory_failure_is_fail_open_only_when_configured` 继续证明普通 inventory failure
未被扩大成强制 fail-closed。

- [ ] **Step 9: 运行 Ruff 并提交代码**

Run:

```bash
uv run ruff check \
  src/openchronicle/capture/privacy.py \
  src/openchronicle/capture/protection.py \
  src/openchronicle/capture/protection_monitor.py \
  src/openchronicle/capture/scheduler.py \
  tests/test_protection.py \
  tests/test_protection_monitor.py \
  tests/test_capture_scheduler_fts.py
```

Expected: PASS。

Commit:

```bash
git add \
  src/openchronicle/capture/privacy.py \
  src/openchronicle/capture/protection.py \
  src/openchronicle/capture/protection_monitor.py \
  src/openchronicle/capture/scheduler.py \
  tests/test_protection.py \
  tests/test_protection_monitor.py \
  tests/test_capture_scheduler_fts.py
git commit -m "fix(capture): fail closed when pause state is unavailable"
```

---

### Task 2: 文档、完整验证与本机部署

**Files:**
- Modify: `docs/capture.md:58-78`
- Modify: `docs/config.md:120-123`
- Modify: `docs/macos-app.md:181-217`
- Modify: `docs/superpowers/specs/2026-08-21-privacy-protection-indicators-design.md:87-90, 192-217, 254-262`
- Modify: `docs/superpowers/specs/2026-08-21-privacy-protection-indicators-design.zh-CN.md:69, 140-153, 179-186`

**Interfaces:**
- Consumes: `ProtectionFailureReason.PAUSE_STATE_UNAVAILABLE`
- Consumes: `failure_requires_fail_closed(cfg, snapshot)`
- Produces: user-facing distinction between control-plane uncertainty and inventory fail-open

- [ ] **Step 1: 更新用户文档和原始总设计**

在所有列出的文档中明确写出：

```text
`screenshot_privacy_fail_closed = false` applies only to window/display inventory
failures. If the pause state cannot be read, OpenChronicle shows the yellow failed
indicator and aborts the complete capture regardless of this setting.
```

中文版本使用：

```text
`screenshot_privacy_fail_closed = false` 只适用于窗口/display inventory 失败。
暂停状态不可读时，无论该设置为何值，OpenChronicle 都显示黄色失败标识并终止整次捕获。
```

不得把黄色状态描述成灰色手动暂停，也不得声称所有 inventory failure 都强制关闭。

- [ ] **Step 2: 运行完整自动化验证**

Run:

```bash
PYTHONPATH=src uv run pytest -q
```

Expected: 当前 235 个测试加新增测试全部 PASS。

Run:

```bash
swift test --package-path macos/OpenChronicleApp
```

Expected: 26 tests，0 failures。

Run:

```bash
uv build
bash scripts/build-macos-app.sh
git diff --check
```

Expected: wheel/sdist、签名 app 构建和 whitespace 检查全部成功。

- [ ] **Step 3: 提交文档**

```bash
git add \
  docs/capture.md \
  docs/config.md \
  docs/macos-app.md \
  docs/superpowers/specs/2026-08-21-privacy-protection-indicators-design.md \
  docs/superpowers/specs/2026-08-21-privacy-protection-indicators-design.zh-CN.md
git commit -m "docs: clarify pause-state fail-closed behavior"
```

- [ ] **Step 4: 安装经过验证的 backend 和 macOS app**

先正常退出 `/Applications/OpenChronicle.app`，确认旧 daemon、AX watcher 和 overlay helper
全部退出，再执行：

```bash
bash install.sh --no-client-config
bash scripts/install-macos-app.sh
```

安装不修改 `~/.openchronicle/config.toml`、capture-buffer、index、timeline 或 memory。

- [ ] **Step 5: 核对安装版本与唯一进程链**

Run:

```bash
openchronicle status --json --no-model-checks
```

Expected: daemon `running=true`、capture `active`、health `healthy`。

比较源码和 site-packages 中 `protection_monitor.py` 的 SHA-256，并核对进程树。Expected:

- 两个 SHA-256 完全一致；
- 一条 `OpenChronicle.app -> openchronicle start --foreground` 链；
- 该 daemon 下恰好一个 `mac-ax-watcher` 和一个 `mac-privacy-overlay`。

- [ ] **Step 6: 运行不含真实隐私数据的多屏黑盒验收**

1. 保持 `screenshot_monitor = "separate"`、`privacy_indicator_style = "pill"`；不得把
   `screenshot_privacy_fail_closed` 改成 `false`。
2. 在屏幕 1 临时打开空白 Edge InPrivate 窗口，在屏幕 2 激活安全应用。
3. 确认屏幕 1 出现绿色保护标识；检查 capture 日志只报告固定原因，不出现窗口标题。
4. 解析新 capture JSON 的结构字段，不输出 AX 内容或图片：必须没有受保护屏幕的截图，
   且受保护活动屏幕不得含 AX Tree。
5. 关闭临时 InPrivate 窗口，确认下一次安全捕获恢复原有 monitor 集合和 AX 行为。

由于 pause-reader exception 是内部错误路径，其强制终止行为由 Task 1 的确定性 monitor 与
scheduler 测试验证；验收期间不得破坏真实 `.paused` 文件权限来制造故障。

- [ ] **Step 7: 最终独立代码复审**

对 Task 1 和 Task 2 的提交范围执行独立 review。合并门槛：

- `pause_state_unavailable` 在 `fail_closed=false` 时仍终止前置和后置 gate；
- ordinary inventory failure 的显式 fail-open 测试仍通过；
- overlay、日志和 scheduler 使用同一策略谓词；
- 没有敏感日志、协议变更或无关重构；
- 完整测试、构建、安装与黑盒证据齐全。
