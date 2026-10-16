# 02 · 目标架构与迁移路径（第一期）

> 设计约束：**贴合现有扁平单文件风格，不做全量重写**。
> `ai.py` / `ui.py` / `main.py` 保留为对外门面，新逻辑全部进 `castorice/` 包。
> 本文的每个模块都有明确的拥有者任务，见 §6 的写域表。

## 1. 分层与依赖方向

```text
┌──────────────────────────────────────────────────────────────┐
│  main.py            应用入口：环境准备、日志、首次引导        │   (t10 仅只读核查)
└───────────────────────────┬──────────────────────────────────┘
                            │ ChatWindow(bot, …)
┌───────────────────────────▼──────────────────────────────────┐
│  ui.py              Qt 前端：主窗口 / 设置 / 模式切换 / 面板  │   (t7)
│  └─ AIWorker(QThread) ──── 只认 ('text'|'tool'|'emoji'|'done')│
└───────────────────────────┬──────────────────────────────────┘
                            │ bot.get_response_stream(text, images)
┌───────────────────────────▼──────────────────────────────────┐
│  ai.py             兼容门面：AI / AIFactory / get_ai          │   (t3 定稿, t4 补工具)
│  ├─ ConfigManager（注释保持写回，红线 5.2）                    │
│  ├─ PromptManager（分层注入）                                  │
│  ├─ MemoryManager（委托 castorice.memory，旧接口不变）         │
│  └─ AI.get_response_stream = 记忆检索 + 前缀 + 委托 harness    │
└───────────────────────────┬──────────────────────────────────┘
                            │
┌───────────────────────────▼──────────────────────────────────┐
│  castorice/         新内核（纯 Python，不依赖 PySide6）       │
│                                                              │
│  app.py       组合根：一处装配全部依赖            (t10)       │
│  modes.py     Mode / ToolPolicy / ModeRegistry    (t3)       │
│  session.py   事件日志 / 检查点 flush / 投影      (t3)       │
│  harness.py   agent 主循环 / 工具派发 / 终止原因  (t3)       │
│  permissions.py 沙箱策略 + 审批网关               (t4)       │
│  tools/       工具注册表与内置工具                (t4)       │
│  memory/      wiki / summary / embedding          (t6)       │
│  channels/    IM 接入协议（仅接口）               (第二期)    │
└──────────────────────────────────────────────────────────────┘
```

**依赖规则（硬性）**：
1. `castorice/` **不得 import `ui`、`PySide6`**——保证可在无 Qt 环境（测试、未来的
   IM 前端、CLI）下驱动整个内核。
2. `castorice/` **不得直接 import `ai`**（避免循环）；需要配置时通过构造参数接收一个
   字典或一个最小的 `ConfigReader` 协议对象。
3. `ai.py` 单向依赖 `castorice`；`ui.py` 只依赖 `ai`（保持现状）。
4. `permissions.py` 不 import `tools/`；`tools/` 可 import `permissions` 的类型。

## 2. 关键模块设计

### 2.1 `castorice/modes.py`（t3）

```python
from dataclasses import dataclass, field
from typing import Callable

ALL_KNOWN_TOOLS = frozenset()     # 由 ModeRegistry 在构造期填满（见下）

@dataclass(frozen=True)
class ToolPolicy:
    allow: frozenset[str] | None = None       # None = 不施加 allow 掩码
    deny: frozenset[str] = frozenset()

    def permits(self, name: str) -> bool:
        if self.allow is not None and name not in self.allow:
            return False
        return name not in self.deny

@dataclass(frozen=True)
class WorkdirPolicy:
    bindable: bool = False
    default_sandbox: str = "read-only"        # 对齐 DSH 的故障安全默认
    command_allowlist: tuple[str, ...] = ()

@dataclass(frozen=True)
class Mode:
    name: str                                  # chat | work | code | learn
    display_name: str
    tool_policy: ToolPolicy
    prompt_sections: tuple[str, ...]           # 追加到基础提示词的 section 名
    workdir_policy: WorkdirPolicy
    supports_vision_input: bool = True

CHAT = Mode("chat", "聊天", ToolPolicy(allow=None, deny=ALL_TOOLS), (),
            WorkdirPolicy(bindable=False))
WORK = Mode("work", "工作", ToolPolicy(), ("work",), WorkdirPolicy(bindable=True,
            default_sandbox="workspace-write", command_allowlist=READONLY_CMDS))
CODE = Mode("code", "编程", ToolPolicy(deny=frozenset({"search_notes","write_note"})),
            ("code",), WorkdirPolicy(bindable=True, default_sandbox="workspace-write",
            command_allowlist=CODE_CMDS))
LEARN = Mode("learn", "学习", ToolPolicy(allow=NOTE_TOOLS), ("learn",),
             WorkdirPolicy(bindable=True, default_sandbox="workspace-write"))
```

`ModeRegistry` 的职责与**构造期校验**（复刻 `dsh-tools` 的 restrict 校验）：

```python
class ModeRegistry:
    def __init__(self, tools: list[ToolSpec], modes: list[Mode] | None = None):
        known = {t.name for t in tools}
        # 1) allow 里出现未注册名 → ValueError（列出已知工具）
        # 2) allow 为空 frozenset → ValueError（物化空配置 bug）
        # 3) mode 名含未知模式 → ValueError
        ...
    def all_modes(self) -> list[Mode]: ...
    def get(self, name: str) -> Mode: ...
    def visible_tools(self, mode: Mode) -> list[ToolSpec]: ...
    def to_openai_specs(self, mode: Mode) -> list[dict]:
        """chat 必须返回 []。"""
```

**内置模式一览（第一期）**

| 模式 | 可见工具 | 目录绑定 | 默认沙箱 |
| :--- | :--- | :---: | :--- |
| chat | **无**（`[]`） | 否 | read-only |
| work | 全部通用工具（web_search / web_fetch / fs / command / make_document / get_current_time） | 可选 | workspace-write |
| code | 通用工具 − 笔记工具 | 是（代码目录） | workspace-write |
| learn | get_current_time / read_file / list_dir / glob / grep / search_notes / read_note / write_note | 是（Vault） | workspace-write |

### 2.2 `castorice/session.py`（t3）

```python
@dataclass(frozen=True)
class SessionHeader:
    session_id: str
    mode: str
    cwd: str | None            # 会话不可变的工作目录（对齐 DSH 的 SessionHeader.cwd）
    created_at: str
    format_version: int = 1

@dataclass(frozen=True)
class SessionEvent:
    seq: int
    kind: str                  # "user"|"assistant"|"tool_call"|"tool_result"|"context"|"sandbox"|"mode"|"proactive"
    payload: dict
    at: str

class SessionStore:
    def __init__(self, root: Path, mode: str, session_id: str | None = None): ...
    def append(self, kind: str, payload: dict) -> int: ...       # 返回 seq
    def flush(self) -> None: ...                                  # 持久性屏障；失败即抛
    def events(self) -> Iterator[SessionEvent]: ...               # 冷读，容忍撕裂尾部
    def derive_messages(self, system_prompt: str, mode: Mode) -> list[dict]: ...
    def checkpoint(self, reason: str) -> None: ...                # flush + 审计事件
```

- **落盘**：`sessions/<mode>/<session_id>.jsonl`，每行一个 JSON 对象（含 `seq`）。
  `flush()` 用 `os.replace` 原子替换（先写 `.tmp`，`fsync`，再替换）。
- **写者唯一性**：同 `session_id` 只允许一个 `SessionStore` 实例（进程内用模块级
  `set` 记录，重复创建抛 `SessionInUseError`）——对齐 `dsh-session-persistence`
  的「每个后端实例内每个会话只允许一个写入方」。
- **恢复**：`load()` 逐行解析；最后一行 JSON 不完整时**丢弃该行**并截断文件；
  检查是否存在「有 `tool_call` 无 `tool_result`」的调用 →
  在内存里补 `TOOL_OUTCOME_UNKNOWN` 结果（不写回文件，避免二次污染）。
- **投影**：`SessionProjection.snapshot() -> dict`（`revision` / `mode` / `cwd` /
  `sandbox_mode` / `tools_in_flight` / `stop_reason` / `message_count`）；
  `subscribe(callback)` 供 UI 订阅。**UI 不重放事件日志**（P18）。

### 2.3 `castorice/harness.py`（t3）

```python
class StopReason(str, Enum):
    FINAL_ANSWER   = "final_answer"
    MAX_ITERATIONS = "max_iterations"
    CANCELLED      = "cancelled"
    ERROR          = "error"
    CHECKPOINT_FAILED = "checkpoint_failed"

class HarnessLoop:
    def __init__(self, session, provider, modes, tools, permissions,
                 max_iterations=8, max_parallel_tool_calls=4,
                 repeat_reminder=(3, 5, 8)): ...

    def run_stream(self, user_text: str, images=None,
                   source: str = "user") -> Iterator[tuple[str, Any]]:
        # 0. 取消位与重复计数复位；投影标记 step 开始
        # 1. 模式 + 可见工具：specs = self.modes.to_openai_specs(mode)（chat → []）
        # 2. 组装 messages（system 由调用方给，user 侧前缀已由 ai.py 拼好）
        # 3. for step in range(max_iterations):
        #      a. session.checkpoint("pre-step")            ← 失败则 StopReason.CHECKPOINT_FAILED
        #      b. kwargs = {model, messages, stream: True}
        #         if specs: kwargs["tools"] = specs         ← chat 时键不存在
        #      c. session.checkpoint("pre-request")
        #      d. text, calls = provider.chat_completion(**kwargs)
        #      e. yield ('text', text) （若有）
        #      f. if not calls: stop(FINAL_ANSWER); break
        #      g. for name, status in self._dispatch(calls, specs): yield ('tool', {...})
        #      h. 补全缺失的工具结果（TOOL_OUTCOME_UNKNOWN）
        #    else: stop(MAX_ITERATIONS)
        # 4. yield ('done', full_reply)
```

**工具派发 `_dispatch(calls, specs)`**（对齐 P16）：

1. 逐个调用先经 `permissions.evaluate(PermissionRequest(...))`：
   - `ALLOW` → 执行；
   - `DENY` / `UNAVAILABLE` → **不执行**，结果文本为 `Error: <reason>`（P10）；
   - `ASK` → 交给 `ApprovalBroker`（Python 侧：worker 线程发信号 → 主线程弹窗 →
     `resolve()`；超时/无应答者 → 按 `UNAVAILABLE` 关闭）。
2. 分组：连续的 `parallel_safe=True` 调用放进 `ThreadPoolExecutor`（上限
   `max_parallel_tool_calls`）；遇到 `parallel_safe=False` 先等待当前批次完成。
3. **结果按模型给出的调用顺序写回消息历史**（不是完成顺序）。
4. 每个调用前后 `yield ('tool', {'name': n, 'status': 'start'|'done'|'error'})`
   —— `'error'` 是新增的细分值，`ui.py:3368-3373` 对非 `'start'` 一律按完成处理，
   因此**不破坏旧消费方**。
5. 结果长度超过 `ToolSpec.result_limit` → head/tail 截断 + 省略说明 +
   （可选）落盘到 `logs/tool_results/` 并给出路径（P20）。

**取消**：`cancel()` 置位 `threading.Event`；每个步骤与每个工具派发前检查；
取消时**保留已 yield 的文本**，就地 `yield ('done', 已累积文本)`。

### 2.4 `castorice/permissions.py`（t4）

见 `01-gap-matrix.md` §3。补充两点：

- **审批的线程模型**：`ApprovalBroker` 内部维护
  `_pending: dict[str, threading.Event]` 与 `_results: dict[str, PermissionResult]`；
  `request()` 注册 `request_id` → 调用 `self._ask_callback(req)`（默认为
  「直接返回 unavailable」）→ `event.wait(timeout)` → 超时或未决一律返回
  `UNAVAILABLE`。UI 侧通过 `broker.set_ask_callback(fn)` 注入一个
  用 `Signal` 投递到主线程的实现。
- **同一次审批只允许一个在跑**：`_in_flight: set[str]`，
  同一 `call_id` 二次 `request` → `ALREADY_IN_FLIGHT`（语义来自 `dsh-authorization`）。

### 2.5 `castorice/tools/`（t4）

```text
tools/
├── __init__.py      ToolSpec、ToolRegistry、构造期校验、build_default_registry()
├── fs.py            read_file / write_file / edit_file / list_dir（+ 观察记录 R5/R6）
├── search_fs.py     glob_files / grep_files（纯 Python，不依赖 rg）
├── command.py       run_command（白名单 + 超时 + 输出上限 + ASTER_* 环境变量）
├── web.py           web_search（迁移 6 后端）/ web_fetch
├── document.py      make_document（.md / .txt；.docx 留第二期）
├── notes.py         search_notes / read_note / write_note（Learn 专属，保留 frontmatter）
└── mcp.py           register_mcp_server 接入点 + mcp__<server>__<tool> 命名
```

**web_search 迁移原则**：把 `ai.py:737-1060` 的
`SearchBackend` 家族 / `SearchManager` / `format_search_results` /
`_html_to_text` **整体搬进 `tools/web.py`**，`ai.py` 侧保留
`from castorice.tools.web import …` 的再导出（保证
`ai.SearchManager` 等旧引用不失效）。**6 个后端名与 `auto` 优先级一字不改**
（红线 5.1 的同类约束：既有行为不回归）。

### 2.6 `castorice/memory/`（t6）

```python
class MemoryEngine(Protocol):
    def remember(self, entry: MemoryEntry) -> str: ...
    def recall(self, query: str, k: int = 5) -> list[MemoryEntry]: ...
    def update(self, entry_id: str, expected_revision: int, patch: dict) -> MemoryEntry: ...
    def archive(self, entry_id: str) -> None: ...
    def count(self) -> int: ...
```

- `embedding.py`：包住现有 `MemoryManager`（`ai.py:330-458`），
  **id 从 `count()+1` 改为 `uuid4().hex`**；`metadata` 结构不变
  （`timestamp/date/time/weekday`）；`recall` 返回时仍拼 `[timestamp]` 前缀。
- `wiki.py`：结构化条目（`id/title/tags/body/source/revision/created_at/updated_at`），
  JSONL 落盘。
- `summary.py`：滚动摘要。触发阈值（条数 / 字符数）→ 取最旧的 N 条 →
  用**确定性规则**压缩（拼接 + 去重 + 头尾保留 + 未完成事项标记
  `[未完成]`/`[TODO]` 原样保留），不调用模型（第一期）；
  第二期可换成 LLM 摘要但需保留确定性回退。
- `worldbook.py`：`trigger_words / priority / state(active|archived|evicted) /
  access_count / last_accessed`；`match(text)` 按触发词命中 + priority 排序；
  淘汰策略：`state=archived` 且 `last_accessed` 超过 N 天且 `access_count < M` → `evicted`。
  **淘汰是标记而非删除**（对齐「压缩只遮蔽不删除」）。

### 2.7 `castorice/app.py`（t10）

```python
def build_app(root: Path, config: dict) -> App:
    """组合根：唯一一处把所有部件按序装配起来。"""
    with ExitStack() as stack: ...
    store    = SessionStore(root / "sessions", mode=config_mode)
    engine   = build_memory_engine(config.get("memory", {}))
    registry = build_default_registry(config)                 # tools
    modes    = ModeRegistry(tools=registry.all(), modes=None)  # 构造期校验在此触发
    perms    = PermissionGateway(config.get("permissions", {}),
                                 path_policy=PathPolicy(root))
    loop     = HarnessLoop(store, provider, modes, registry, perms, **limits)
    return App(loop=loop, session=store, modes=modes, memory=engine)
```

`ai.AI.__init__` 在 t3 中改为调用 `build_app`（或接受注入的 `App`），
`AI.get_response_stream` 变成「记忆检索 + 前缀拼装 + `yield from loop.run_stream(...)`」。

## 3. 数据落盘布局

| 路径 | 内容 | 是否进发布包 |
| :--- | :--- | :---: |
| `config.yaml` | 用户配置（含 Key），注释保持 | 用 example 占位 ✅ |
| `example_config.yaml` | 新增 `mode` / `memory` / `permissions` 节 | ✅ |
| `sessions/<mode>/<id>.jsonl` | 会话事件日志 | ❌（用户全新启动） |
| `sessions/<mode>/index.json` | 会话列表（标题、时间、消息数） | ❌ |
| `database/` | ChromaDB（沿用） | ❌ |
| `memory/wiki.jsonl`、`memory/summary.jsonl`、`memory/worldbook.json` | t6 新增 | ❌ |
| `ui_settings.json` | 主题（沿用） | ❌ |
| `logs/app.log` | 运行日志（沿用） | ❌ |
| `logs/tool_results/` | 超长工具结果（P20） | ❌ |

**清理语义**（README 需要更新）：
- 「清空记忆」= 删 `database/` + `memory/`；
- 「清空聊天记录」= 删 `sessions/`。

## 4. 配置扩展（`example_config.yaml` 必须预置）

> **为什么必须预置**：`update_yaml_scalars`（`ai.py:95-158`）在
> **「父块找不到」时会静默跳过该键**。若设置窗口要写 `modes.learn.vault_path`
> 而文件里没有 `modes:` 节，保存会**静默失败**——这是红线 5.2 的直接推论。

```yaml
# 当前模式：chat | work | code | learn
mode: "chat"

# 各模式的工作目录绑定（Code=可信代码目录，Learn=Obsidian Vault）
modes:
  code:
    workdir: ""            # 留空表示未绑定，界面会提示
    sandbox: "workspace-write"
  learn:
    vault: ""              # Obsidian Vault 根目录
    sandbox: "workspace-write"

# 权限与审批
permissions:
  sandbox_default: "read-only"   # read-only | workspace-write | danger-full-access
  approval_policy: "ask"         # ask | never
  command_allowlist: []          # 追加到内置白名单的命令

# 记忆引擎
memory:
  engine: "embedding"            # embedding | wiki | summary
  summary_threshold: 40          # 累积多少条后压缩
  worldbook_enabled: true
```

**向后兼容**：三个新节全部**可选**；缺失时走代码内默认值
（`chat` / `read-only` / `ask` / `embedding`），旧 `config.yaml` 不改也能跑
（t10 验收条款）。

## 5. `ai.py` 的接缝（t3 定稿，t4 只补工具）

**t3 必须定稿的对外形态**（写完即冻结）：

```python
class AI:
    # 既有（签名不变）
    def get_response_stream(self, user_input, images=None): ...
    def get_response(self, user_input) -> str: ...
    def change_provider(self, provider_name, reload_config=False, keep_history=False): ...
    def clear_history(self) -> None: ...
    def reload_emojis(self, image_dir=None) -> int: ...
    def clear_memories(self) -> None: ...
    def get_history(self) -> list[dict]: ...
    def get_memory_count(self) -> int: ...
    def supports_vision(self, provider_name=None) -> bool: ...
    @property messages / provider_name / config_manager / prompt_manager / memory_manager
    # 新增（t3）
    @property app: App                 # 组合根，供 t7 读 mode/session/projection
    def set_mode(self, mode: str) -> None: ...
    @property mode: str
```

**t4 只允许往里加**：`tool_registry` 的内容（通过 `build_default_registry` 的配置项）
与 `permissions` 的配置读取；**不改 `get_response_stream` 的签名与事件语义**。

## 6. 写域表（避免并行任务互相踩）

| 任务 | 独占写域 |
| :--- | :--- |
| t2 | `tests/`、`pytest.ini` 或 `pyproject.toml` |
| t3 | `castorice/__init__.py`、`castorice/modes.py`、`castorice/session.py`、`castorice/harness.py`、`prompts/modes/`、`ai.py`、`tests/test_modes.py` |
| t4 | `castorice/permissions.py`、`castorice/tools/`、`ai.py`、`tests/test_permissions.py`、`tests/test_tools.py` |
| t6 | `castorice/memory/`、`tests/test_memory_modes.py` |
| t7 | `ui.py`、`tests/test_ui_panels.py` |
| t10 | `castorice/app.py`、`main.py`、`README.md`、`docs/`、`example_config.yaml`、`scripts/` |

**冲突提示**：
1. `ai.py` 同时属于 t3 与 t4，靠**依赖顺序**（t3 → t4）串行化，不能并行。
2. `castorice/providers/`（目标 7）**不在任何任务的 inScope 中**，
   需要 captain 裁决：建议并入 t4 的 inScope，或退到第二期。
3. `castorice/channels/base.py` 与 `sessions/` 的落盘约定
   目前无归属，建议并入 t10 的 `castorice/app.py` 写域或由 captain 追加。

## 7. 测试策略（t2 建立，后续任务沿用）

```text
tests/
├── conftest.py            把仓库根加入 sys.path；提供 tmp_config / tmp_session fixtures
├── test_yaml_roundtrip.py update_yaml_scalars 的注释与排版保持（红线 5.2）
├── test_config.py         ConfigManager.get_provider_config / set_active_provider
├── test_emoji.py          EmojiCatalog.match_by_mood + EmojiSession 概率/冷却
├── test_html.py           _html_to_text
├── test_modes.py          (t3) 模式可见性、Chat 零工具、请求体无 tools 键
├── test_harness.py        (t3) 终止原因、并行顺序、检查点 fail-closed
├── test_permissions.py    (t4) 目录边界、大小写、符号链接、审批 fail-closed
├── test_tools.py          (t4) 命令白名单、超时、结果上限、web_search 迁移不回归
├── test_memory_modes.py   (t6) 三模式 + 生命周期 + 旧数据可读
└── test_ui_panels.py      (t7) 模式切换、会话隔离（**不启动 QApplication 主循环**）
```

**硬性约定**：
- 全部测试**不联网、不需要 API Key**（t2 验收条款）；网络类工具用手工 stub。
- 需要文件系统的测试统一用 `tmp_path`，**绝不写仓库的 `database/`、`sessions/`、`memory/`**。
- Qt 相关测试只在 `QT_QPA_PLATFORM=offscreen` 下跑，并 `pytest.importorskip("PySide6")`。

## 8. 迁移顺序（含每步的回滚点）

| 步 | 任务 | 完成标志 | 回滚点 |
| :-: | :--- | :--- | :--- |
| 1 | t2 | `python -m pytest -q` 全绿且覆盖 6 个纯逻辑函数 | 删除 `tests/` 与配置，零影响 |
| 2 | t3 | Chat 无 tools、事件协议不变、会话隔离 | `ai.AI.get_response_stream` 恢复直连 provider |
| 3 | t4 | 权限三态判定 + 工具集可用、越界被拒 | `build_default_registry` 回退到两个内置工具 |
| 4 | t6 | 三模式记忆 + Worldbook 生命周期 | `memory.engine=embedding` 且旧 `MemoryManager` 直连 |
| 5 | t7 | 四模式切换、会话隔离、Chat 隐藏工具 UI | UI 隐藏模式切换条，固定 `chat` |
| 6 | t8/t9 | 独立验证 + 评审结论 | 无（只读验证） |
| 7 | t10 | 文档/配置/打包核查收口 | 文档回退 |

**关键路径**：t2 → t3 → t4 → t6 → t7 → t8 → t9 → t10；
t4 与 t6 可并行（写域不重叠）。

## 9. 未决问题（需 captain 或用户裁决）

| # | 问题 | 影响 | 建议 |
| :-: | :--- | :--- | :--- |
| Q1 | `castorice/providers/`（目标 7 多厂商分级适配）没有 inScope | 目标 7 无人负责 | 并入 t4，或明确推迟到第二期 |
| Q2 | `castorice/channels/base.py`（目标 8）与 `sessions/` 落盘约定无归属 | 第二期要改 `harness.py` 签名 | 并入 t10 的 `castorice/app.py` 写域 |
| Q3 | `prompts/` 的「触发词」标注与实现不符（当前全量拼接 76KB） | 用户文档与实现一致性 | t3 顺带实现分层注入，或先修正 README 表述 |
| Q4 | 主动聊天（目标 9）的 `schedules.json` 落盘位置 | 数据目录约定 | 程序目录，与 `ui_settings.json` 同级 |
| Q5 | `python -m pytest` 需要 `pytest` 进入 `requirements.txt` 与否 | 用户安装体验 | 放进 `requirements-dev.txt` 或 requirements 的可选注释段 |

## 10. 红线速查卡（贴在每个任务的开工处）

| 红线 | 一句话 | 违反后果 |
| :--- | :--- | :--- |
| A | `AI.get_response_stream` 的元组协议 `('text'|'tool'|'emoji'|'done')` 不变 | `ui.py` 的 `AIWorker` 直接失效，界面全黑 |
| B | `update_yaml_scalars` 的注释与排版保持；新配置节必须预置在 `example_config.yaml` | 用户设置被静默丢弃 / 注释被抹 |
| C | `scripts/打包.ps1` 无需改动，但导入链必须静态可达 | 打包后缺模块，用户侧启动崩溃 |
| D | `castorice/` 不 import `PySide6` / `ui` / `ai` | 内核无法被测试与未来的 IM 前端复用 |
| E | Chat 模式的请求体**不含 `tools` 键**（不是 `tools=[]`） | 零工具目标失效，闲聊被工具描述带偏 |
| F | 审批「无应答者 / 超时 / 异常」一律**按拒绝关闭** | 静默放行有副作用的操作 |
| G | 检查点 `flush` 失败时**不执行下游副作用** | 崩溃后状态与已执行副作用不一致 |
| H | `.agent-teams/` 与 DSH 目录一律**只读** | 破坏团队状态或第三方程序 |
