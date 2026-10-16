# 01 · 九项目标差距矩阵与第一期落点

> 说明：九项目标能力集来自团队任务描述（对齐 Cyrene-Agent 的能力范式）。
> **本文件未直接审计 Cyrene-Agent 源码**（工作区内无该仓库），
> 因此「目标能力」一列是对任务描述的结构化展开，不是源码事实；
> 「DSH 参照」一列则是 `04-dsh-harness-notes.md` 中的已核实事实。
> 现状列取自 `00-current-state.md`，均带行号。

## 0. 总览

| # | 目标 | 第一期是否落地 | 主要落点 | 依赖 |
| :-: | :--- | :--- | :--- | :--- |
| 1 | Chat：不暴露任何工具 | ✅ 落地 | `modes.py` + `harness.py` | t3 |
| 2 | Work：工具串联 | ✅ 落地 | `tools/` + `permissions.py` | t3 → t4 |
| 3 | Code：可信目录 + 受限命令 | ✅ 落地 | `permissions.py` + `tools/command.py` | t4 |
| 4 | Learn：Obsidian Vault | ✅ 落地（只读+检索版） | `modes.py` + `tools/fs.py` | t4、t6 |
| 5 | 个性化记忆（三模式 + Worldbook + 生命周期） | ✅ 落地 | `castorice/memory/` | t2 → t6 |
| 6 | 工具生态 | ⚠️ 骨架落地，生态后置 | `tools/` 注册与扩展点 | t4 |
| 7 | 多模型厂商分级适配 | ⚠️ 能力表落地，差异化后置 | `castorice/providers/`（t4 内） | t4 |
| 8 | 多平台接入（飞书/微信/QQ） | ❌ 第一期不做，留接口 | `castorice/channels/`（仅接口约定） | 第二期 |
| 9 | 主动聊天 | ❌ 第一期不做，留钩子 | `harness.py` 的 idle hook | 第二期 |

---

## 1. Chat 模式：不暴露任何工具

**目标**：闲聊场景下模型**完全看不到工具**，不会被工具描述带偏语气，
也不产生「助手腔」；同时不承担工具 schema 的 token 成本。

**现状**：❌ 不存在。
`AI.get_response_stream` 在 `ai.py:1806` 无条件
`tool_specs = self.tool_registry.to_openai_specs()`，`ai.py:1813-1814` 无条件传
`tools=tool_specs`；system prompt 里还有 `TOOL_INSTRUCTIONS`（`ai.py:1484-1493`）
硬编码介绍两个工具。**任何一条会话都带工具。**

**DSH 参照**（`04-dsh-harness-notes.md` §1.2）：
`ctx.tools.restrict({deny})` 对单个 agent 施加掩码，`schemas(scope)` 只返回可见 schema；
`restrict({})` 与未知名在构造期抛错；限制在 `dispose` 时解除。

**差距**：
1. 工具可见性无策略层；
2. 可见性判定点与请求构造点耦合（同一处无条件执行）；
3. 无「掩码配置写错」的构造期校验。

**第一期落点**：
- `castorice/modes.py`：`ToolPolicy(allow, deny)`；
  `CHAT = Mode(name="chat", tool_policy=ToolPolicy(allow=frozenset(), …))`
  —— 但按 DSH 的约定，**物化空 allow 是错误配置**，所以 Chat 应表达为
  `ToolPolicy(allow=None, deny=ALL_KNOWN_TOOLS)`，或更简单：`ModeRegistry` 不为 chat
  注册任何工具，`visible_tools(chat) == []`。
- `castorice/harness.py`：`specs = registry.to_openai_specs(mode)`；
  `kwargs = {"model":…, "messages":…, "stream": True}`；
  `if specs: kwargs["tools"] = specs` —— **Chat 时 `tools` 键根本不出现**。
- `ai.py`：`_build_system_prompt` 的 `TOOL_INSTRUCTIONS` 改为**按模式条件拼接**
  （Chat 模式不追加，也不追加 `TIME_INSTRUCTIONS` 的第 3 条「也可以调用 get_current_time」）。

**验收（t3）**：
- `to_openai_specs(CHAT) == []`；
- 断言构造出的请求 kwargs **不含 `tools` 键**（不是断言 `== []`）；
- 断言 Chat 模式的 system prompt 不含「get_current_time」「web_search」字样；
- 异常路径与重试路径同样不带 `tools`（t9 评审核查项）。

---

## 2. Work 模式：工具串联

**目标**：多步任务，模型可串联搜索 → 抓取 → 读文件 → 写文件 → 生成文档；
工具间结果互相引用；有副作用操作经过审批。

**现状**：⚠️ 有 agent 循环骨架，但**只有 2 个工具**且无审批。
`ai.py:1069-1091` 只注册 `get_current_time` 与 `web_search`；
循环顺序执行、无并行、无超时、无结果上限、无权限判定。

**DSH 参照**：
- 工具流水线 `pre-execute → guard → execute → post-execute → finalizeContent → result`（§1.3）；
- 并行安全调用有界并行、独占调用构成屏障、结果按模型顺序写回（§1.1）；
- 结果有界 + spill 可恢复（P20）；协作式超时（P22）；
- 重复调用提醒（P21）。

**差距**：工具种类、审批、调度、结果治理四项全缺。

**第一期落点**（t4，`castorice/tools/`）：

| 工具 | 说明 | parallel_safe | 需审批 |
| :--- | :--- | :---: | :---: |
| `web_search` | 迁移现有 6 后端实现（`ai.py:942-1060` 整体搬到 `tools/web.py`） | ✅ | 否 |
| `web_fetch` | 抓取单页正文（复用 `_fetch_content` + `_html_to_text`） | ✅ | 否 |
| `read_file` | 带行号、带 offset/limit、结果上限 | ✅ | 否 |
| `write_file` | 原子写（temp + `os.replace`），受目录边界约束 | ❌ | 是 |
| `edit_file` | 字面量替换，**必须先读后改**（复刻 `FS_NOT_OBSERVED`） | ❌ | 是 |
| `list_dir` | 列目录 | ✅ | 否 |
| `glob_files` / `grep_files` | 文件发现与内容搜索（纯 Python，不依赖 rg） | ✅ | 否 |
| `run_command` | 白名单命令、超时、输出上限、非零退出码结构化返回 | ❌ | 是 |
| `get_current_time` | 保留 | ✅ | 否 |
| `make_document` | 生成 .md/.txt/.docx（第一期先 .md/.txt 写盘） | ❌ | 是 |

**验收（t4）**：见任务书；额外要求「拒绝以普通工具结果返回而非抛异常」（P10），
使轮次继续而不中断。

---

## 3. Code 模式：可信目录 + 受限命令

**目标**：在用户显式指定的代码目录内读写与运行命令；
命令默认拒绝，只在已授权时按白名单运行；目录外一律拒绝。

**现状**：❌ 无目录概念、无命令工具、无沙箱。

**DSH 参照**：
- 模式词汇 `read-only` | `workspace-write` | `danger-full-access`，
  **故障安全默认 `read-only`**，无法强制时 `SANDBOX_UNAVAILABLE` 失败而**绝不受限运行**（§1.3）；
- 工作区根 = 会话**不可变 `cwd`**；解析优先级
  `explicit ?? fold(events) ?? default`；模式切换是**会话日志里的一条事件**（可回放持久化，
  两个会话互不影响）；
- 被拒操作返回 `FS_SANDBOX_DENIED` 并显示**同轮次升级提示**；
  更宽模式的升级需要**非空 `justification`** 且只对**那一次**执行生效。

**差距**：四件套全缺（模式解析、路径边界、命令白名单、升级审批）。

**第一期落点**（t4，`permissions.py`）：

```python
SANDBOX_MODES = ("read-only", "workspace-write", "danger-full-access")
DEFAULT_SANDBOX_MODE = "read-only"          # 故障安全

class WorkdirPolicy:                        # 挂在 Mode 上
    bindable: bool                          # Code/Learn 可绑定，Chat 不可
    default_mode: str                       # Code/Learn = "workspace-write"
    command_allowlist: tuple[str, ...]      # 见下
```

- **路径判定顺序**（必须按此顺序，且逐项有测试）：
  `Path(p).expanduser()` → 若是相对路径则拼 `session.cwd` →
  `resolve(strict=False)`（消 `..` 与符号链接）→ `os.path.commonpath([root, target]) == root`
  → 在 **Windows 上 `os.path.normcase` 归一化后比较**（大小写不敏感，
  t9 评审核查项明确点名大小写差异）。任一失败 → `SandboxDenied`。
- **符号链接**：`resolve()` 已跟随；**额外**对 root 自身 `resolve()`，
  防止「root 本身是链接」绕过。
- **命令白名单**（第一期）：
  `git status|diff|log|show`、`python -m pytest`、`python -m compileall`、
  `dir`/`ls`、`type`/`cat`、`findstr`/`grep`（只读子集）。
  **明确不包含** `rm`/`del`/`Remove-Item`/`pip install`/`curl`/`Invoke-WebRequest`/`powershell`。
- **执行**：`subprocess.run(..., shell=False, cwd=session.cwd, timeout=…)`；
  **禁止 `shell=True`**；输出 head/tail 截断 + 明确省略说明（P20）；
  非零退出码**作为结果返回**（不抛异常）。

**必须写进 README 的风险声明**：这是**策略级隔离**，不是内核级沙箱；
不保证进程可见性、网络访问与文件系统重定向（对齐 `dsh-bash-sandbox` 的
「网络访问和进程可见性不在其保证范围内」）。

**验收（t4）**：越界绝对路径被拒；`..` 逃逸被拒；符号链接逃逸被拒；
大小写变形（Windows）被拒；未被白名单命中的命令被拒；命令超时返回结构化结果。

---

## 4. Learn 模式：Obsidian Vault

**目标**：把用户的 Obsidian Vault 作为知识库——检索笔记、按标题定位、
把新内容写成符合 Vault 规范的 Markdown（含 `[[双链]]`、`#tag`、frontmatter）。

**现状**：❌ 无。仅有 ChromaDB 向量检索（`ai.py:330-458`），与文件系统无关。

**DSH 参照**：
- 工作目录绑定 + `workspace-write` 边界（§1.3）；
- 「编辑前必须读取」+ 版本守卫（R6），避免覆盖用户手工维护的笔记；
- 文件发现（`glob`/`grep`）不依赖外部 `rg`（`dsh-tool-fs-search`）；
- 客户端读投影而非重放日志（P18）——Learn 的侧栏展示笔记列表时用投影。

**第一期落点**：
- `mode.workdir_policy`：Learn 绑定 `vault_path`，默认模式 `workspace-write`；
- 复用 t4 的 `tools/fs.py`，**Learn 专属增量**：
  - `search_notes(query)`：先按文件名/标题/正文做字面匹配，
    再落到 t6 的 embedding 记忆检索（Vault 文件按段落索引）；
  - `read_note(path)` / `write_note(path, content)`：写入时**保留既有 frontmatter**，
    只替换正文（除非显式要求改 frontmatter）；
  - **只在这三个工具内**做 Markdown 规范化，不污染通用 `write_file`；
- 绑定目录的**识别**：存在 `.obsidian/` 子目录即认为是 Vault（给出提示但不强制）；
- 未绑定时给出可读提示（t7 验收条款）。

**验收（t7 + t6）**：Vault 未绑定时提示可读；绑定后可检索到笔记；
写入保留 frontmatter；Vault 外写入被拒。

---

## 5. 个性化记忆：wiki / summary / embedding + Worldbook + 生命周期

**目标**：三种可切换的记忆形态 + 类 Worldbook 的条目化设定 + 生命周期（创建/更新/归档/淘汰）
+ 检索时按**相关性 + 时间**注入。

**现状**：⚠️ 单一 embedding 集合，可用但有缺陷：
- `MemoryManager`（`ai.py:330-458`）只有 `add_memory`/`search_memories`/`clear`；
- id 用 `count()+1`（`ai.py:384`）→ 删除后重建会重号；
- 每轮把「用户: …\n助理: …」整段写一条（`ai.py:1869`），**无摘要、无去重、无归档、
  无访问计数**；检索固定 top-5，无时间衰减；
- **没有 Worldbook**：`prompts/` 里虽有「触发词」标注，但代码不解析（`00-current-state.md` §2.2）。

**DSH 参照**：
- append-only 日志 + **派生**视图（`dsh-session`）→ summary 模式即「日志 + 派生摘要」；
- 压缩**只遮蔽不删除**（`dsh-compaction`）→ 归档而非物理删除；
- CAS + revision（P17，`dsh-experimental-agent-team`/`dsh-goal`）→ 条目更新防覆盖；
- 客户端读投影（P18）→ 记忆管理面板读快照。

**第一期落点**（t6，`castorice/memory/` 纯文件级写域）：

```text
castorice/memory/
├── __init__.py        build_memory_engine(config) 工厂
├── base.py            MemoryEngine 接口 + MemoryEntry dataclass（含 revision）
├── embedding.py       ChromaDB 实现（沿用现有 database/ 与 [时间] 前缀语义）
├── wiki.py            结构化条目：标题/标签/正文/来源，JSONL 或 SQLite 落盘
├── summary.py         滚动摘要：超阈值时确定性压缩，保留「未完成事项」标记
└── worldbook.py       触发词条目 + 生命周期（active/archived/evicted）+ 访问计数
```

**硬约束**：
1. `MemoryEngine` 默认实现必须与现状等价（t6 验收条款）；
2. 旧 `MemoryManager` 的 5 个方法（`add_memory` / `search_memories` / `clear` /
   `collection` / `count`）语义保留，`ai.py` 侧改为委托；
3. 既有 `database/` 数据**必须仍可读出**且带 `[时间]` 前缀；
4. `summary` 压缩必须是**确定性**的（同输入同输出，可测试）；
5. 测试用 `tmp_path`，**不污染仓库 `database/`**。

**检索注入顺序**（相关性 + 时间）：
`Worldbook 命中条目（按优先级）` → `近期 summary` → `embedding top-k（按相似度×时间衰减）`，
整体仍走 `【相关记忆】` 前缀注入到 **user 消息**（保持 `ai.py:1796-1804` 的省 token 设计）。

**验收（t6）**：见任务书六条。

---

## 6. 工具生态

**目标**：外部可扩展——第三方或高级用户能加工具；支持 MCP 这类标准扩展；
有统一的注册、校验、可见性、审批、呈现链路。

**现状**：❌ 注册表存在但生态为零（2 个内置工具，无扩展点，无命名规范，
无 schema 校验，无按工具的元数据）。

**DSH 参照**：`defineTool` 的声明式定义（`parameters` / `output` / `timeoutMs` /
`parallel_safe` / `presentCall` / `presentResult`）；MCP 的
`mcp__<server>__<tool>` 命名与「调用方作用域为空时不添加 MCP 工具或提示词文本」
（`dsh-mcp-client`）；工具 schema 变化会让提示词前言失效的 KV Cache 说明。

**第一期落点**：
- `castorice/tools/__init__.py` 暴露 `ToolSpec` dataclass（在 `ai.Tool` 基础上加元数据）：

```python
@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict
    handler: Callable[..., str]
    parallel_safe: bool = True
    timeout_s: float | None = None
    requires_approval: bool = False
    modes: frozenset[str] = frozenset({"work", "code", "learn"})
    result_limit: int = 20_000          # 字符上限，超过走 head/tail + 省略说明
    source: str = "builtin"             # builtin | mcp | user
```

- **注册期校验**（复刻 DSH 的构造期报错）：重名 → 抛错；`parameters` 不是合法
  JSON Schema 子集（只允许 `type/properties/required/additionalProperties/
  description/enum/items`）→ 抛错；`modes` 含未知模式 → 抛错。
- **MCP 接入点**（第一期只留接口，不实现传输）：`tools/mcp.py` 定义
  `register_mcp_server(name, tools)` 与命名函数
  `f"mcp__{server}__{tool}"`，以及「无 server 时不产生任何工具与提示词文本」的短路。
- **用户扩展**：`tools/user/` 目录按 `*.py` 发现，每个文件暴露 `register(registry)`；
  加载失败只记录警告**不影响启动**（对齐 `dsh-session-projection` 的
  「来源不可用时保留可用结果」）。

**验收（t4）**：重名/坏 schema/未知模式三种配置错误在构造期抛错；
无 MCP server 时工具列表与提示词与现状一致。

---

## 7. 多模型厂商分级适配

**目标**：不止「都走 OpenAI 兼容」——按厂商声明能力（视觉、工具调用、流式工具增量、
推理强度、上下文窗口、价格档），并据此决定功能可用性与参数裁剪；配置错误**启动即报错**。

**现状**：⚠️ `LLMProviderFactory.create_provider`（`ai.py:613-634`）无分支，
全部返回 `OpenAICompatibleProvider`；唯一能力是 `vision`，靠正则猜模型名
（`ai.py:1567-1572`）；无重试、无超时、无上下文窗口概念、无能力协商。

**DSH 参照**：
- `dsh-llm`：通过适配器流式调用、**发现模型**、解析**模型能力与调用默认值**；
  每个流只尝试一次（重试由独立包 `dsh-llm-retry` 负责）；**流始终以终止结果结束**；
- `dsh-agent-loop` 的 `prepareCall()` 在活跃轮次信号下**校验适配器持有的字段**、
  解析推理强度与输出 token 默认值；
- 请求头记录 `request/context`（provider、model、contextWindow、
  `systemPromptUpdate` 模式），**仅在与最新快照不同时记录**；
- `dsh-agent-default-model` 提供共享默认 provider/model/reasoningEffort。

**第一期落点**（放 t4，`castorice/providers/`；t4 的 inScope 含 `castorice/tools/`，
若写域冲突则并入 t10 或由 captain 追加文件级 inScope）：

```python
# castorice/providers/capabilities.py
@dataclass(frozen=True)
class ModelCapabilities:
    context_window: int = 32_000
    supports_tools: bool = True
    supports_stream_tool_calls: bool = True    # 决定是否需要非流式回退
    supports_vision: bool = False
    supports_reasoning_effort: bool = False
    supports_parallel_tool_calls: bool = False

TIERS = {
    "deepseek":  ModelCapabilities(context_window=64_000, supports_vision=False),
    "openai":    ModelCapabilities(context_window=128_000, supports_vision=True),
    "aliyun":    ModelCapabilities(context_window=131_072, supports_vision=True),
    "custom":    ModelCapabilities(),
}
def resolve_capabilities(provider_config) -> ModelCapabilities:
    """显式配置 > 厂商表 > 默认值；与现有 supports_vision 的解析顺序保持一致
       （配置优先、名称/表兜底），保证不回归。"""
```

**关键不改动项**：`AI.supports_vision`（`ai.py:1577-1600`）的**外部行为**
（配置优先 → 正则兜底）保持；新能力表作为**额外**信息层，
`config.yaml` 里已有的 `vision: true` 仍然生效。

**厂商分区与写域提示**：当前 `LLMProvider` 抽象（`ai.py:465-498`）已经足够承载，
第一期**不新增 provider 子类**，只加能力表 + 请求参数裁剪
（`max_tokens`、`temperature` 的缺省解析，对齐 `prepareCall()`）；
真正的差异化（如 DeepSeek 推理强度字段）留到第二期。

**验收（t4）**：未知 provider 名 → 启动即报错并给出可用名列表；
`context_window` 超限时**明确拒绝**而不是静默截断；
`vision` 现有行为回归测试通过（t2 基线）。

---

## 8. 多平台接入（飞书 / 微信 / QQ）

**目标**：把助手接到 IM 平台，用户在微信/QQ/飞书里跟它说话。

**现状**：❌ 完全没有。只有本地 Qt 窗口；所有交互都绑在 `ChatWindow` 上
（`ui.py:2944` 的 `_send_message` 直接持有 `AIWorker`）。

**DSH 参照**：**没有可直接照搬的 IM 适配器**。相关可借用的只有边界约定：
- `dsh-acp` / `dsh-sdk-*` 展示的「同一 harness 内核，多种前端」的分层：
  内核只认「一条进消息、一串事件出」，前端各自渲染；
- `dsh-webhook`：`ctx.webhookRuntime.register(rule)` + `dispatch(delivery)`，
  「**提供方身份验证属于适配器包**」，宿主侧只负责把外部事件变成会话；
- `dsh-session-projection` 的快照 + 变更通知。

**第一期落点**：**不做实现，只留接口**（避免吞掉过度工期）：

```python
# castorice/channels/base.py  —— 第一期只定义协议，不实现任何平台
class Channel(Protocol):
    name: str                      # "feishu" | "wechat" | "qq"
    def start(self, dispatch: Callable[[InboundMessage], None]) -> None: ...
    def send(self, target: str, payload: OutboundPayload) -> None: ...

@dataclass(frozen=True)
class InboundMessage:
    channel: str; target: str; user_id: str
    text: str; attachments: tuple[str, ...]; raw: dict

class OutboundPayload:              # 与现有事件协议同构
    text: str
    emoji_path: str | None = None   # 直传图片路径，IM 适配器自行上传
    tool_notes: tuple[str, ...] = ()
```

**必须写清的两条约束**（否则第二期会返工）：
1. **模式是会话属性，不是界面属性**：`Channel` 只能选择 `mode`，
   不能绕过 `ModeRegistry` 直接给模型塞工具——否则 Chat 的「零工具」红线在 IM 侧失效。
2. **审批在 IM 里是异步的**：`ApprovalBroker` 的应答者接口必须支持
   「发出问题 → 返回未来/回调」，而不是阻塞等待。第一期实现时用
   `broker.request(timeout=…)` 的同步版即可，但**接口要留 async 形态**
   （Python 侧用 `concurrent.futures.Future` 或回调注册），避免第二期改签名。

**验收（第一期）**：`castorice/channels/base.py` 存在且 `import` 无异常；
有一个 fake channel 的单测证明「同一 harness 可被非 Qt 前端驱动」。

---

## 9. 主动聊天

**目标**：助手能在无用户输入时主动发起对话（定时问候、事件提醒、长时间未联系时的一条消息）。

**现状**：❌ 完全没有。所有产出都由 `_send_message` → `AIWorker` 驱动。

**DSH 参照**（这是 DSH 里最贴近、也最值得照搬的一块）：
- `dsh-schedule`：宿主级持久提醒——一次性 / 固定周期 / 每日 / 每周 / **cron**；
  **宿主重启后任务仍然可用**；**每个重复任务只补发最近一次错过的发生时点**
  （不刷屏）；任务到期时**恢复冷会话**；投递需要 Session 确认 `session/flush` 之后才提交；
  六个选择器互斥（`after_seconds` / `at` / `every_seconds` / `daily` / `weekly` / `cron`），
  `title` 必填且 ≤ 120 字符；`schedule_create/list/delete/update` 四个工具。
- `dsh-goal`：跨轮次/恢复/fork/重启的长目标，CAS 更新，Round 上限（默认 256）。

**第一期落点**：**不做实现，只留钩子**：

- `castorice/harness.py` 的 `HarnessLoop.run_stream` 增加可选参数
  `source: Literal["user", "schedule"] = "user"`；`source="schedule"` 时
  跳过记忆检索里的「用户原话」字段改用一个合成的 `ProactiveTrigger` 对象。
- `castorice/session.py` 的 `SessionEvent` 预留 `kind="proactive"`，
  使主动消息与用户消息在日志里**可区分**（否则恢复会话时会误判）。
- 落盘位置约定：`schedules.json` 放在程序目录（与 `ui_settings.json` 同级，
  且**不进发布包**，与现有「不放 database / logs / ui_settings.json」的策略一致）。

**两条从 DSH 抄来的硬约定**（第二期实现时必须遵守）：
1. **只补发最近一次错过的时点**——不要在一次启动时把错过的 20 条提醒全发出来；
2. **投递在 `flush` 之后才提交**——主动消息也要走检查点，否则崩溃后会重复发。

**验收（第一期）**：仅有钩子与文档，无行为变化。

---

## 10. 迁移顺序与依赖

```text
t2（pytest 基线，无依赖）
  └─ 锁定 6 个纯逻辑函数，为后续所有改动提供回归网
       ↓
t3（四模式内核 + harness）  ← t1 的设计 + t2 的回归网
  ├─ 目标 1（Chat 零工具）+ 目标 2 的调度骨架
       ↓
t4（权限网关 + 工具集）      ← t3 的可见性机制
  ├─ 目标 2（工具串联）+ 目标 3（Code 边界）+ 目标 6（生态骨架）+ 目标 7（能力表）
       ↓
t6（记忆引擎）               ← t2 的 tmp_path 约定 + t3 的会话事件
  └─ 目标 5（三模式 + Worldbook + 生命周期），并为目标 4 提供检索
       ↓
t7（界面模式化）             ← t3 的会话隔离 + P18 投影
  └─ 目标 4 的 Vault 绑定入口 + 四模式切换 UI
       ↓
t8（独立验证） → t9（评审） → t10（集成收口 + 文档 + 打包核查）
```

**关键路径**：t2 → t3 → t4 → t6 → t7 → t8 → t9 → t10。
t6 与 t4 无相互依赖，**可并行**（写域不重叠：`castorice/memory/` vs
`castorice/permissions.py` + `castorice/tools/`）。

**风险点**：
1. `ai.py` 同时是 t3 与 t4 的 inScope —— **必须由 t3 先定稿委托形态**，
   t4 只在已定的接缝上补工具，否则两者会互相踩。t3 完成后建议 captain
   冻结 `ai.py` 的对外接口清单再放行 t4。
2. `castorice/providers/`（目标 7）未出现在任何任务的 inScope 中 ——
   建议并入 t4 或由 t10 收口，需 captain 明确。
3. 第一期不做目标 8/9，但**接口必须先落**（`channels/base.py`、`source="schedule"`），
   否则第二期要改 `harness.py` 的核心签名。

## 11. 第一期不做的事（明确排除，防止范围蔓延）

| 项 | 理由 |
| :--- | :--- |
| PTC / `run_code` 单工具呈现模式 | 工具数量小，native 呈现已够（`04-*.md` R12） |
| 内核级沙箱（Windows ACL / job object） | 与纯 Python 便携分发冲突（R13、N12） |
| Cordis 风格插件市场与 HMR | 无生态诉求（N1） |
| 压缩后端族（`dsh-compaction-*`） | 单会话轮次短，summary 模式已覆盖（N10） |
| 多 agent 团队编排 | 单用户单机助手，只借 CAS 契约（N7） |
| 遥测 / 产品分析 | 便携软件不应上报（N9） |
| 常驻 shell / tmux | 每次调用全新 shell 已够（N5） |
| 飞书/微信/QQ 的实际适配器 | 只留接口，第二期实现（§8） |
| 主动聊天的调度实现 | 只留钩子，第二期实现（§9） |
