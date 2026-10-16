# 05 · 渠道层接口约定与 sessions/ 落盘目录约定（t20 沉淀）

> 面向第二期：接入飞书 / 微信 iLink / QQ 时**不需要改动**
> `castorice/harness.py` 的核心签名。本文是契约，不是实现——
> 第一期的落点只有 `castorice/channels/base.py`（纯接口）与
> `castorice/app.py`（目录约定）。

## 1. 渠道层在分层中的位置

```text
飞书 / 微信 iLink / QQ  ←平台 SDK→  castorice/channels/<name>.py（第二期）
                                        │ 实现 Channel 协议
                                        │ 消费 ai.AI 冻结接口
                              ai.AI（get_response_stream / set_mode /
                                    set_approval_responder / approval_broker）
                                        │
                              castorice/harness.py（HarnessLoop，签名不变）
```

依赖规则（继承 02-architecture.md §1 的红线 D）：

1. `castorice/channels/` 不 import `ui` / `PySide6` / `ai`；
   对 AI 的引用通过 `ChannelContext.bot` 以鸭子类型传入。
2. 渠道实现放在 `castorice/channels/<name>.py`，由装配方（第二期的
   `castorice/app.py` 扩展或独立启动脚本）注册，渠道不自己构造内核。
3. 渠道层是 `get_response_stream` 的**另一个消费方**，与 `ui.AIWorker`
   平级——事件协议完全一致，不发明第三种协议。

## 2. Channel 协议（`castorice/channels/base.py`）

| 成员 | 签名 | 约定 |
| :--- | :--- | :--- |
| `capabilities` | `ChannelCapabilities`（类属性） | name / display_name / supports_markdown / supports_image_input / supports_emoji / supports_proactive |
| `start()` | `-> None` | 连接平台并开始接收。重复 start 幂等；失败抛 `ChannelError`（fail-fast，不静默降级） |
| `close()` | `-> None` | 幂等；close 后再 close 不抛 |
| `on_message(msg)` | `ChannelMessage -> Iterator[tuple]` | 产出与 `ai.AI.get_response_stream` 同构的事件流（见 §3） |
| `send(msg)` | `OutboundMessage -> str` | 发送出站消息，返回平台消息 id；分段 / 限速 / 重试由实现负责 |

数据类型：

* `ChannelContext(bot, sessions_root, data_dir, options)`——组合根注入；
  `sessions_root` 取 `castorice.app.AppPaths.sessions`。
* `ChannelMessage(channel, sender_id, chat_id, text, message_id, attachments)`；
  `message_id` 用于幂等去重：**同 id 重复投递必须被渠道丢弃**。
* `OutboundMessage(chat_id, text, emoji_tag, reply_to)`。
* `normalize_target(value)`：配置里的 chat_id / allowed_senders 统一过这一关，
  空值抛 `ChannelError`——杜绝「空串匹配一切」的静默放行。

## 3. 事件协议（与 ui.AIWorker 消费的形态一致，t19 已冻结）

```text
(kind, payload)  恒 2 元组：
  ('text',  str)              增量文本
  ('tool',  dict)             工具事件（name / status / …，error 是 status 的合法值）
  ('emoji', (tag, path))      表情包
  ('done',  str)              终止事件：恰好一次、最后一条
```

渠道实现的责任：

1. 消费迭代器并把 `text` 事件按平台节奏分段发送（消息长度 / 频率限制是
   渠道职责）；`tool` 事件折叠成「正在执行 xx…」提示或直接吞掉；
2. 迭代器抛异常时翻译成 `('done', 错误说明文本)`，不让平台侧看到裸异常栈；
3. `done` 之后不再发送任何事件。

## 4. 审批在渠道侧的纪律（对齐 t13 的线程纪律）

* 渠道侧通过 `bot.set_approval_responder(callback)` 接审批，callback 运行在
  **agent 工作线程**：只允许 `broker.defer(request_id)` + 投递到渠道自己的
  用户界面 + `return None`；
* 用户的 allow / deny 通过 `broker.resolve(request_id, decision)` 唤醒等待；
* 超时 / 无应答者 / 异常一律按拒绝关闭（fail-closed，红线 F）；
* 渠道可以像 `ui.ChatWindow._apply_approval_timeout` 一样调整
  `broker.timeout`，但缩短超时不能改成「超时即放行」。

## 5. sessions/ 落盘目录约定

### 5.1 布局

```text
sessions/                       ← sessions_dir（ai.AI 参数，相对路径落点为进程 CWD）
├── chat/
│   ├── <session_id>.jsonl      会话事件日志（append-only，每行一个 JSON 对象）
│   └── index.json              会话列表（标题、时间、消息数）
├── work/ …  ├── code/ …  └── learn/     按模式隔离（t7 的会话隔离依赖它）
```

### 5.2 关键语义（全部由 `castorice/session.py` 实现，渠道层不得绕开）

| 语义 | 约定 |
| :--- | :--- |
| 写者唯一性 | 同一 session 文件全进程只允许一个 `SessionStore` 写者；重复创建抛 `SessionInUseError` |
| 原子性 | `flush()` 先写 `.tmp`，再原子替换；失败抛 `CheckpointFailed`，调用方必须阻止下游副作用（fail-closed，红线 G） |
| 格式版本 | 每行带 `"v": 1`；读到未知版本拒绝加载并给出可读错误，不静默降级（R16） |
| 崩溃恢复 | 最后一行不完整则丢弃该行并截断；有 `tool_call` 无 `tool_result` 的调用补 `TOOL_OUTCOME_UNKNOWN`（只进内存，不写回文件） |
| 前端投影 | UI / 渠道读 `SessionProjection` 快照，**不重放事件日志**（P18） |

### 5.3 落点漂移问题（相对路径的根因与第一期处置）

* `ai.AI` 的 `sessions_dir` 默认值 `"sessions"` 与 `castorice.memory` 的
  `DEFAULT_ROOT = "memory"` 都是**相对路径，落点为进程 CWD**——从仓库根
  启动 `python main.py` 时会在仓库根创建 `sessions/` 与 `memory/`。
* 第一期处置（不改动 ai.py 的签名默认值）：
  1. `main.py` 的 `__main__` 把 CWD 统一到程序目录（既有行为）；
  2. `main.py` 构造 `AI` 时**显式传 `sessions_dir=str(root / "sessions")`**，
     落点不随启动 CWD 漂移；
  3. `castorice/app.py` 的 `AppPaths` 把这一目录约定显式化；
  4. 开发期从仓库根直接构造 `AI()` 时产生的 `sessions/`、`memory/`
     属于运行期产物，已加入 `.gitignore`。
* 第二期渠道接入时必须复用同一约定：`ChannelContext.sessions_root`
  直接取 `AppPaths.sessions`，**不要**为渠道另起一套目录。

## 6. 数据落盘总表（含渠道扩展点）

| 路径 | 内容 | 进发布包 | 说明 |
| :--- | :--- | :---: | :--- |
| `config.yaml` | 用户配置（含 Key） | example 占位 ✅ | 注释保持写回 |
| `sessions/` | 会话事件日志（按模式隔离） | ❌ | §5；运行期产物 |
| `database/` | ChromaDB 记忆库 | ❌ | 既有 |
| `memory/` | wiki / summary / worldbook 落盘 | ❌ | t6；运行期产物 |
| `logs/` | app.log 与 logs/tool_results/ | ❌ | 既有 |
| `channels/<name>/` | 渠道自己的数据（第二期） | ❌ | `ChannelContext.data_dir` |

「清空记忆」= 删 `database/` + `memory/`；「清空聊天记录」= 删 `sessions/`。

## 7. 第二期接入清单（预测核点）

1. 实现 `Channel` 协议 + `capabilities`，事件流语义对齐 §3；
2. 审批接线对齐 §4（worker 侧 defer + 投递，主线程 resolve）；
3. 会话落盘复用 `AppPaths.sessions`，模式隔离沿用 `sessions/<mode>/`；
4. 渠道配置放 `config.yaml` 的 `channels.<name>` 节，目标 id 过
   `normalize_target`；
5. 主动聊天（supports_proactive）的定时触发事件走 `KIND_PROACTIVE`，
   落盘位置沿用程序目录（02-architecture.md Q4）；
6. 全程不修改 `castorice/harness.py` 的签名；如确需扩展，先在
   `docs/refactor/` 立新设计文档评审。
