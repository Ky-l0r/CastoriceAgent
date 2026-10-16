# 06 · 主动聊天调度器与双投递（第二期 t4 沉淀）

> 任务来源：第二期 t4。对标 DSH 的 dsh-schedule（01-gap-matrix.md §9），
> 落点为 `castorice/scheduler.py`（调度内核）、`castorice/notify.py`
> （桌面投递抽象）与 `tests/test_scheduler_proactive.py`（29 用例）。

## 1. 架构位置与依赖

```text
Scheduler（castorice/scheduler.py）
    │ run_due(now)                    时钟只依赖本地系统时间；分钟级精度
    ├─ ScheduleStore（schedules.json，程序目录，不进发布包）
    ├─ bot.harness.run_stream(合成触发语, source="proactive")
    │      └─ harness 内部：kind=proactive 标记落盘 + pre-step/pre-request 检查点
    ├─ session.flush("proactive-delivery")     ← 约定②的强制屏障
    └─ 双投递（按 target_id 前缀路由）
         ├─ "desktop:*" → DesktopNotifier 协议（castorice/notify.py）
         └─ "qq:*"      → Channel.send(OutboundMessage)（t3 渠道，chat_id 过 normalize_target）
```

红线 D：调度器与 notify 抽象均不 import ui / PySide6 / ai。Qt 只在
`SystemTrayNotifier` 方法体内延迟导入；对 agent 的引用通过鸭子类型
（`bot.harness` / `bot.new_chat_session`）。

## 2. 两条 DSH 硬约定（t7 反例验证点）

| # | 约定 | 实现机制 | 测试 |
| :-: | :--- | :--- | :--- |
| ① | 只补发最近一次错过的时点 | `run_due` 对每个任务只取 `previous_due(now)` 的最近一个时点，且仅当 `due > last_sent_at` 时触发；投递成功才推进 `last_sent_at`（落盘），失败保留原值下轮重试 | `test_three_missed_daily_points_deliver_once`（3 错过时点只发 1 条且 due=最近时点）、`test_every_seconds_missed_burst_delivers_once`、`test_last_sent_at_prevents_redelivery`、`test_persisted_last_sent_survives_restart`（崩溃重启不重发）、`test_failed_delivery_retries_same_point` |
| ② | 投递在 flush 之后才提交 | `_fire`：agent 轮次结束（轮次内部已有 pre-step / pre-request 检查点）后，再显式 `session.flush(reason="proactive-delivery")`，**flush 抛异常即放弃全部投递**（fail-closed，红线 G 同源） | `test_flush_failure_means_no_delivery`（flush 失败桌面与 QQ 均零投递）、`test_delivery_happens_after_flush`（事件顺序断言 flush 先于一切投递） |

## 3. 主动消息的可区分性（验收③）

harness.run_stream 的合成触发判定值是 `source="proactive"`（harness.py
的既有实现，t3 冻结签名）：合成触发语以 `kind='user'` +
`payload.source='proactive'` 落盘——恢复会话按 payload.source 区分
主动触发与真实用户输入；`KIND_PROACTIVE`（session.py 预留）保留给
显式主动通知事件。调度器不重写该语义，只保证自己发出的轮次必带标记。

> 任务书原文写 `source='schedule'`；与内核既有判定值 `"proactive"`
> 对齐后语义一致（防恢复误判），且不改 harness 签名。

## 4. 双投递与投递抽象（Electron 裁决落地）

* `castorice/notify.py`：`DesktopNotifier` 协议（notify(title, body,
  target_id) -> DeliveryResult）+ `NotifierRegistry` 多实现注册表
  （按 target 前缀路由）+ `SystemTrayNotifier` 第一期默认实现
  （QSystemTrayIcon 气泡；托盘不可用时按 `fallback_log` 降级为仅记日志，
  可配置）。第三期 Electron 前端实现同一协议即可接入，调度器零改动。
* QQ 走 t3 渠道 `send(OutboundMessage)`；目标 id 一律过
  `normalize_target`（空目标在 validate 阶段即被拒）。
* 渠道离线/发送失败的降级按 target.fallback 可配置：
  - `"desktop"`：回退桌面投递（`test_desktop_strategy_falls_back`）；
  - `"skip"`：跳过并记日志（`test_skip_strategy_logs_and_skips`）。
  skip 下的 0 投递是策略性放弃，不触发重试；非策略性全败则触发失败、
  下轮重试同一时点。

## 5. schedules.json 约定（验收⑥）

* 位置：程序目录（与 ui_settings.json 同级，02-architecture.md Q4 裁决）。
* **不进发布包**：`scripts/打包.ps1` 按白名单复制（Image / prompts /
  example_config / 使用说明），schedules.json 天然不在白名单；
  `test_pack_script_whitelist_excludes_schedules` 将此固化为测试。
* 格式：`{"version": 1, "schedules": [...]}`；坏文件 fail-fast
  （`ScheduleError`），绝不静默清空；写回 .tmp + replace 原子替换。
* 触发器五选一（互斥）：after_seconds / at / every_seconds / daily /
  weekly（cron 不在第一期范围）；title 必填 ≤120 字符（对齐 dsh-schedule）。
* 时钟：只依赖本地系统时间（datetime.now）；精度为**分钟级**——
  daily / weekly 时点分钟对齐，秒字段忽略；测试全部注入 datetime，
  不 sleep、不依赖墙钟推进。

## 6. 运行期接线（装配方 / main.py 视角）

```python
from pathlib import Path
from castorice.scheduler import ScheduleStore, Scheduler, SchedulerConfig
from castorice.notify import SystemTrayNotifier

store = ScheduleStore(Path("schedules.json"))          # 程序目录
scheduler = Scheduler(
    bot=ai,                                            # ai.AI（harness / new_chat_session）
    store=store,
    desktop=SystemTrayNotifier(fallback_log=True),     # 桌面投递
    channels={"qq": napcat_channel},                   # t3 渠道（可选）
    config=SchedulerConfig(check_interval=10),
)
scheduler.start()   # 后台线程轮询；close() 幂等
```

触发一次的完整链路：`new_chat_session()` 召回冷会话 →
`harness.run_stream(合成触发语, source="proactive")`（模型生成主动
消息，事件落盘）→ `session.flush("proactive-delivery")` → 按目标
双投递 → 全部成功才把 `last_sent_at` 写回 schedules.json。

## 7. 测试与基线

* `tests/test_scheduler_proactive.py`：29 用例全离线（注入时钟、替身
  bot/渠道/通知，不 sleep 不联网）。
* 全量基线不跌破：738 passed / 1 skipped（见任务验收⑦，t4 交付时全量
  为 818 passed / 1 skipped，含本期新增）。
