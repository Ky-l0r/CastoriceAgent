# 04 · DeepSeek Harness 范式抽取与 Python 侧落点

> 本文件是 t3（四模式内核 + harness 层）、t4（权限审批网关与工具集）、
> t6（记忆引擎）、t7（界面模式化）的**直接实现依据**。
> 由 architect 在 t1 中产出，基于对两个代码库的实际阅读，不是泛泛概述。

## 0. 审计范围、版本与证据约定

### 0.1 审计对象

| 对象 | 路径 | 版本 / 规模 |
| :--- | :--- | :--- |
| 本仓库 | `D:\Subject\CastoriceAgent` | `ai.py` 2137 行 / `ui.py` 3763 行 / `main.py` 287 行，git 工作区 |
| DSH Desktop 外壳 | `D:\Tool\Deepseek Harness\DSH Desktop\resources\app\` | `package.json` → `dsh-plugin-desktop@2.0.17` |
| DSH 内核 | `…\app\node_modules\@deepseek-ai\` | `@deepseek-ai/dsh@0.2.0-rc.2`，约 260 个包 |

**只读声明**：DSH 目录在本次审计中**只读**，未创建、修改或删除其中任何文件；
所有引用均来自现有文件内容。后续任务若需再次参考，同样必须只读。

### 0.2 证据约定

- 【事实】：直接读到的代码/文档内容，标注包名与文件。
- 【建议】：基于事实给出的改造方案，尚未实现。
- DSH 各包随包附带中文 `README.zh.md`（含「概述 / 使用本包 / 理解实现 / 源码地图 /
  模型体验 / 已知限制」结构），本文件的 DSH 事实主要来自这些文档与 `lib/*.js` 源码。

---

## 1. DSH harness 层台账

按任务要求的六个主题逐项展开。每条给出：DSH 事实 → 可移植性判定 → castorice/ 落点 →
Python 侧写法。

### 1.1 agent 主循环与工具派发

【事实】`@deepseek-ai/dsh-agent-loop` 是 `Agent` 约定的唯一具象实现，
在 `ctx.agents` 上把自己注册为 `AgentFactory`（`lib/index.js`：`AgentLoop` 服务、
`ReactLoopAgent` 驱动器）。要点：

1. **轮次（turn）/ 步骤（step）两级状态机**。轮次边界「先打开持久轮次，再原子领取待处理
   输入与一条排队提示词」；步骤之间只领取 next-step 输入。
2. **每个步骤固定做四件事**：组装提示词与工具 → 投影运行时上下文 → 跑
   `agent/pre-step`（可被拒绝）→ 记录 `step/start`、跑 `agent/request` waterfall、
   `prepareCall()`、发起流式请求。
3. **工具派发有独占屏障与有界并行池**：`maxParallelToolCalls`（默认 10）限制并行安全的
   调用重叠数；**独占调用单独运行并构成排序屏障**；策略、持久结果与结果上下文
   **保持模型顺序**。
4. **失败即补全结果**：关闭失败步骤前，为每个尚无结果的 assistant 工具调用记录错误结果——
   已有 `tool/call` 记录但无结果的给 `TOOL_OUTCOME_UNKNOWN`，没有调用记录的给
   `TOOL_NOT_STARTED`；取消后未分发的调用给合成 `ABORTED_BEFORE_DISPATCH`。
   这些结果让后续请求拿到**配对完整**的工具历史，而**不自动重试**结果不明的操作。
5. **取消是协作式的**：`agent.cancel()` 中止当前活动；被取消的流**终结已送达用户的文本**
   （追加 `interrupted: true` 的锚点，携带已交付前缀）。
6. **模型尝试的事件三件套**：每次尝试发出一个进程本地 `start`，每个 chunk 在匹配的持久
   assistant-frame 结算后才发出，并恰好一个终态 `end`；失败以 `aborted` 结算，
   持久 `assistant/message` 之后才是 `committed`。
7. **请求纠错入口**：`agent/request-error` 的监听器返回 `{ kind: 'retry' }` 且不调用
   `next()` 即重试，未被处理即终态。

【事实】现有 `ai.AI.get_response_stream`（`ai.py:1755-1883`）已经是**同构的简化版
ReAct 循环**：`MAX_ITERATIONS = 8`，`for _ in range(...)` 里调用
`self._provider.chat_completion(self._messages, stream=True, tools=tool_specs)`，
有 tool_calls 就追加 assistant 消息、逐个 `tool_registry.call`、把结果作为 `tool`
消息放回；`for…else` 分支在触顶时追加提示文本。**没有**：终止原因、步骤事件、
并行/独占区分、失败步骤结果补全、取消语义。

**判定：可移植（需大幅改写）** → 落点 `castorice/harness.py`。

【建议】Python 侧结构：

```python
# castorice/harness.py

class StopReason(str, Enum):
    FINAL_ANSWER = "final_answer"          # 模型不再调用工具
    MAX_ITERATIONS = "max_iterations"      # 达到步数上限
    CANCELLED = "cancelled"                # 用户中止
    ERROR = "error"                        # 内部失败，附 reason 字符串

@dataclass(frozen=True)
class StepRecord:
    index: int
    text: str
    tool_calls: tuple[dict, ...]            # 规范化后的调用
    stop_reason: StopReason | None = None

class HarnessLoop:
    def __init__(self, session, provider, tools, permissions,
                 max_iterations=8, max_parallel_tool_calls=4): ...

    def run_stream(self, user_message) -> Iterator[tuple[str, Any]]:
        """产出与旧实现完全一致的 ('text'|'tool'|'emoji'|'done') 事件元组。"""
```

- **终止原因显式化**：`run()` 返回/记录 `StopReason`，并作为 `('done', payload)` 之外的
  内部状态暴露（`session.last_stop_reason`）。旧的触顶文案保留，但由
  `StopReason.MAX_ITERATIONS` 驱动，不再靠 `for…else` 隐式判断。
- **并行安全 / 独占**：`Tool.parallel_safe: bool`（默认 `True`；`write_file`、
  `run_command`、`memory.update` 等	extbf{显式设为 False}）。派发时把连续并行安全的调用放进
  有界线程池（`concurrent.futures.ThreadPoolExecutor(max_workers=max_parallel_tool_calls)`），
  遇到独占调用先 `wait` 当前一批、再单跑它，**结果按模型给出的顺序写回消息历史**。
- **失败步骤结果补全**：循环体 `try/except` 中，为任何没有结果的 `tool_call` 追加
  `{"role":"tool","tool_call_id":…,"content":"错误: 工具结果未知（TOOL_OUTCOME_UNKNOWN）"}`，
  保证下一轮的消息历史**配对完整**（OpenAI 接口在有孤立 tool_calls 时会直接 400）。
- **取消**：`self._cancel = threading.Event()`；每个步骤开始处检查；
  `AIWorker.stop()`（`ui.py:844`）改为置位该事件并停止产出，**已流出的文本保留**
  （与 DSH 的「取消保留已送达文本」一致）。
- **不自动重试**结果不明的工具调用——这是 DSH 明确的安全约定，直接采纳。

### 1.2 工具可见性策略（tool policy）

【事实】`@deepseek-ai/dsh-tools` 提供 `ctx.tools`（`ToolRuntime` 服务），核心是三件事：

1. **注册**：`defineTool({name, description, parameters, output, async execute})`；
   注册即对模型可见，注册表把 schema 送入系统提示词组装。
2. **按 agent 限制可见性**：`ctx.tools.restrict(filter)` 对单个 agent 继承的全局工具应用
   **allow / deny 掩码**，`filter` 形如 `{ allow?: Set, deny?: Set }`；
   **掩码取交集**；作用域注册保持可见；限制在 `dispose()` 时解除。
   `schemas(scope)` 返回该作用域可见的 schema（**不含 `execute`**）。
   源码校验（`lib/index.js:2893-2909`）：
   - 无作用域的 `restrict()` 直接抛错（`requires a scoped context`）；
   - `restrict({})` 是 no-op 也抛错（空过滤器几乎总是「配置被物化成空」的 bug）；
   - 点名未注册的工具抛错，并在错误信息里列出已知全局工具；
   - 不能限制保留的 PTC 传输名 `run_code`。
3. **呈现模式**：`mode: native | ptc | both`；`ptc` 下请求里**不再包含任何原生 schema**，
   只暴露一个 `run_code` 加一份按运行时语言生成的 SDK；纯 `ptc` 下模型直呼其他工具
   在策略之前就解析为 `UNKNOWN_TOOL`；单 agent 可用 `presentAs(mode)` 覆盖默认值。

【事实】本仓库现状：`ai.ToolRegistry`（`ai.py:658-702`）只有 `register` /
`to_openai_specs` / `call`。`to_openai_specs()` **无条件**返回全部工具；
`AI.get_response_stream` 在 `ai.py:1806` 无条件
`tool_specs = self.tool_registry.to_openai_specs()`，并在 `1813-1814` 无条件
`tools=tool_specs`。**没有任何按模式/按会话的可见性裁剪**，也没有未知工具的前置校验
（`ToolRegistry.call` 在 `ai.py:695-697` 只在执行时返回错误字符串）。

**判定：可移植（核心）** → 落点 `castorice/modes.py`（策略定义）+
`castorice/harness.py`（投影时求值）。

【建议】Python 侧结构：

```python
# castorice/modes.py

@dataclass(frozen=True)
class ToolPolicy:
    allow: frozenset[str] | None = None      # None = 不施加 allow 掩码
    deny: frozenset[str] = frozenset()

    def permits(self, name: str) -> bool:
        if self.allow is not None and name not in self.allow:
            return False
        return name not in self.deny

@dataclass(frozen=True)
class Mode:
    name: str                      # chat | work | code | learn
    display_name: str
    tool_policy: ToolPolicy
    system_prompt_sections: tuple[str, ...]
    workdir_policy: WorkdirPolicy

class ModeRegistry:
    def visible_tools(self, mode: Mode) -> list[Tool]:
        """按 mode.tool_policy 过滤；并像 DSH 一样对未注册名做前置校验。"""
    def to_openai_specs(self, mode: Mode) -> list[dict]:
        """Chat 模式必须返回严格空列表 []。"""
```

**硬性约定（t3 验收条款，必须逐字实现）**：

1. `ModeRegistry.to_openai_specs(Mode("chat", …))` 返回 `[]`，
   **且**`harness.py` 在 `tools` 为空时必须构造**不含 `tools` 键**的请求体
   （不能传 `tools=[]` 也不能传 `tools=None`，两条路径都要有测试断言）。
2. 未注册的工具名出现在 `allow` 里 → 构造期直接抛错（复刻 DSH 的
   `names unknown global tool`），避免「配置写错但静默不可见」。
3. `allow=frozenset()`（物化空配置）也抛错（复刻 DSH 的 `restrict({})` no-op 检查），
   Chat 模式改用 `allow=None, deny=ALL` 或直接不注册任何工具来表达。
4. 可见性判定必须发生在**构造请求之前**，并且异常路径（重试、工具调用失败后的续轮）
   不得绕过它——t9 评审核查项明确点名「含异常与重试路径」。
5. **不引入 PTC / `run_code`**：DSH 的 `ptc` 模式是其多语言代码执行运行时
   （`dsh-ptc-runtime-node`）的产物；本项目的工具规模不需要「只暴露一个 run_code」，
   直接采用 `native` 语义（每个可见工具一个 schema）。

### 1.3 审批与拒绝语义

【事实】DSH 把「权限」拆成三个独立 owning 位置，这是最值得移植的部分：

| 包 | 拥有什么 | 关键接口 |
| :--- | :--- | :--- |
| `dsh-sandbox-policy` | 逐调用文件操作策略（模式 + 工作区根） | `resolve({session, mode})` |
| `dsh-user-approval` | 一次性审批 seam（策略 + 请求分发 + 审计） | `request(req)`、`setPolicy` |
| `dsh-permission-presets` | 把沙箱与审批**捆绑**成具名预设 | `presets` 配置表、`/permission` |

**沙箱模式词汇**（封闭集合）：`read-only` | `workspace-write` | `danger-full-access`；
**故障安全默认是 `read-only`**。解析优先级：
`effective = explicit grant ?? fold(events) ?? deployment default`；
工作区根取会话**不可变的 `cwd`**（创建时记录），无 cwd 才回退到配置的 `workspaceRoot`；
运行时切换是往会话日志追加一条 `sandbox/mode` 事件（**只有事件，没有带外状态**），
回放即持久化，**两个会话绝不共享模式**。
无法强制执行所请求的模式时以 `SANDBOX_UNAVAILABLE` 失败，**绝不不受限地运行**。

**审批语义**（`dsh-user-approval/README.zh.md`）：

- 策略只有两个值：`ask`（默认，委托给已组合的应答者）与 `never`
  （**在交互式分发之前**确定性拒绝，后续 `prepend` 监听器也无法绕过）。
- 应答者缺失、抛异常、或返回词汇外的值时，一律规范化为 `unavailable` 并
  **以拒绝方式关闭（fail-closed）**，service 自身绝不提示人类。
- 批准只适用于**对应那一次请求**（`allowed-once`）；无 `allow-always`、无规则记忆、
  无吊销、无授权存储。
- 请求必须处于**未结束的轮次**内，否则在审计前抛异常。
- 审计事件 `approval/asked` / `approval/decided` **只写日志**；模型只看到最终工具结果
  与运行时上下文快照里的当前策略，**看不到人类权限 UI 与审计事件**。

**工具流水线中的决策点**（`dsh-tools/lib/index.js:3225-3260`）：

```text
tools/pre-execute (waterfall)  →  allow | deny{reason, info} | ask | cancel
   ↓ ask 时交给审批 seam 解析
已注册的单调 guard              →  返回理由即拒绝，后续监听器不能把它翻回允许
tools/execute (waterfall)       →  包裹分发，可加超时/重试，仅此处可替换必填 signal
tools/post-execute (waterfall)  →  block{feedback} | 替换 value / content
finalizeContent (定义持有)      →  策略之后的最终内容处理
tools/result                    →  只观测
```

拒绝对模型的表现是一条 `Error: <denialReason>` 的 `isError` 结果（`lib/index.js:3245-3256`）；
**结构化错误详情不会混进这条消息**。默认允许分支是 `() => Promise.resolve({kind:'allow'})`。

**判定：可移植（核心）** → 落点 `castorice/permissions.py`。

【建议】Python 侧结构：

```python
# castorice/permissions.py

class Decision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"
    UNAVAILABLE = "unavailable"     # ask 时无应答者/应答者失败 → 关闭

@dataclass(frozen=True)
class PermissionRequest:
    tool: str
    arguments: dict
    mode: str
    target_paths: tuple[str, ...]   # 供路径边界判定，DSH 侧不携带参数，这里主动带上
    call_id: str
    reason: str

@dataclass(frozen=True)
class PermissionResult:
    decision: Decision
    reason: str = ""
    info: dict | None = None        # 结构化错误，不进模型可见文本

class PermissionGateway:
    """对齐 dsh-tools 的流水线：pre_execute → 单调 guard → execute → post_execute。"""
    def pre_execute(self, req) -> PermissionResult: ...
    def guard(self, fn: Callable[[PermissionRequest], str | None]) -> None:
        """注册单调守卫：返回非空理由即拒绝，且不可被后续守卫翻回允许。"""
    def evaluate(self, req) -> PermissionResult:
        """pre_execute → 若是 ASK 则走 ApprovalBroker → 汇总为最终判定。"""

class PathPolicy:
    """对齐 dsh-sandbox-policy / dsh-fs-sandbox 的逐调用策略解析。"""
    def resolve(self, session, explicit: str | None = None) -> str:
        """explicit ?? 会话日志最后一条 sandbox/mode ?? 部署默认（'read-only'）。"""
    def check_write(self, session, path: str, mode: str) -> None:
        """workspace-write：目标必须在会话工作区内，否则抛 SandboxDenied。"""

class ApprovalBroker:
    """对齐 dsh-user-approval 的应答者 waterfall（fail-closed）。"""
    def request(self, req: PermissionRequest, timeout: float = 120.0) -> PermissionResult:
        """无应答者 / 超时 / 应答者抛异常 → UNAVAILABLE（一律按拒绝处理）。"""
```

**Python 侧必须处理的两件事**（DSH 用 Cordis 能力解决，这里要手写）：

1. **跨线程审批**：harness 在 `QThread`（`AIWorker`）里跑，弹窗必须在 GUI 主线程。
   写法：worker 内 `broker.request()` 通过 `pyqtSignal` 抛给主线程 →
   主线程 `QMessageBox`/`PermissionBar` 收集选择 → 调
   `broker.resolve(request_id, decision)` → worker 侧 `threading.Event.wait(timeout)` 醒来。
   超时与「窗口已关闭」都必须落到 `UNAVAILABLE`（拒绝），**绝不默认放行**。
   离线测试时用 `AutoResponder`（同步返回），不依赖 Qt。
2. **单调 guard 的顺序语义**：DSH 的 guard 不可被后续监听器翻回允许。Python 里把 guard
   实现为**顺序执行、首次拒绝即返回**的列表，而不是「收集所有投票再表决」。

### 1.4 任务 / 尝试契约

【事实】DSH 的「尝试」概念有三层，彼此正交：

1. **模型尝试（attempt）**：步骤内的重试复用同一份已渲染的组装结果，
   **不重复组装、不重复跑 `agent/pre-step`、不重复准入用户消息**；
   每次尝试发一个 `start`、恰好一个终态 `end`（`committed` / `aborted`）。
2. **授权尝试（authorization attempt）**：`ctx.authorization.begin(key)` 每个凭据
   **同时只允许一次**；已有尝试时抛 `ALREADY_IN_FLIGHT`；`run()` 未提交凭据就返回则抛
   `NOT_COMMITTED`；人的「不」是 `cancelled`（**结果，不是故障**）；其余失败是错误。
3. **团队任务契约（`dsh-experimental-agent-team`）**：任务有 owner、依赖 DAG、
   **每次变更都是 compare-and-set**（基于过期副本的更新被拒绝）、
   revision 随每次变更递增；删除后保留在历史中；文件触及提示冲突只告警不阻塞。
   上限：`maxMembers=16`、`maxTasks=256`、`maxPendingMessagesPerMember=64`、
   `maxMessageBytes=65_536`、`disposalTimeoutMs=5_000`。
   相关：`dsh-goal` 的 goal 也走 CAS，陈旧视图被拒；Round 上限默认 256。
   `dsh-tool-todo` 每次更新**整体替换**列表，跨轮持久。

【事实】本仓库现状：**没有任何尝试/版本/CAS 概念**。
`MemoryManager.add_memory`（`ai.py:365-397`）用 `str(self._collection.count() + 1)`
当 id——这是**已知的并发/删除隐患**（清空后重建会重号，重复写入会撞 id）。
`AI._messages` 是唯一的历史持有者，切模式/切提供商即丢弃。

**判定：可移植（精简版）** → 落点 `castorice/session.py`（会话与事件序列）、
`castorice/memory/`（条目的 revision / CAS）。

【建议】Python 侧：

- `session.py` 定义 `SessionEvent`（`kind` / `seq` / `payload` / `at`）与 append-only 事件列表；
  `Session.append()` 返回新 `seq`；`Session.revision` 供 UI 做 compare-and-set 判断
  （UI 拿着旧 revision 提交的模式切换/面板状态更新必须被拒绝）。
- 记忆条目带 `revision: int` 与 `updated_at`；更新走
  `update_entry(entry_id, expected_revision, patch)`，revision 不匹配抛
  `StaleEntryError`。**这是对现有`count()+1` 取 id 的明确修复**。
- 不引入 `dsh-authorization` 的凭据 flow 全套（那是给 OAuth/一次性码用的），
  但**保留其错误词汇**：`ALREADY_IN_FLIGHT` / `NOT_COMMITTED` / `UNKNOWN_METHOD`
  作为「同一次审批只能有一次在跑」的语义依据。

### 1.5 检查点与恢复

【事实】`@deepseek-ai/dsh-session-checkpoint-policy`（`lib/index.js`，全文 78 行）是
**全 DSH 中最容易完整移植的一段**，它只做三件事：

```js
ctx.on("llm/stream", (options, next) => {        // 模型请求前
  if (options.sessionId === undefined) return next();
  const session = ctx.sessions.get(options.sessionId);
  return session === undefined ? next() : afterCheckpoint(ctx, session, next);
});
ctx.on("tools/execute", async (exec, next) => {  // 顶层工具的外部副作用前
  if (exec.agent === undefined || exec.parent !== undefined) return next();
  await ctx.sessions.flush(exec.agent.session);  // 嵌套调用复用外层已持久化的调用
  if (exec.signal.aborted) return abortedBeforeDispatchResult();
  return next();
});
ctx.on("agent/pre-step", async ({ agent }, next) => {   // 下一个 agent 步骤前
  await ctx.sessions.flush(agent.session);
  return next();
});
```

三条要点：

1. **检查点是「语义」检查点，不是定时快照**：模型请求前 / 顶层工具可能产生外部副作用前 /
   下一 agent 步骤前。
2. **失败即阻止（fail-closed）**：持久写入成功前，模型适配器或顶层工具正文**不会运行**。
3. **嵌套调用复用外层的持久化**（`exec.parent !== undefined` 直接 `next()`），
   避免每个子调用都 flush。

配套：`dsh-session-persistence` 的「只有完成 `flush` 才构成持久性屏障」、
「每个后端实例内每个会话只允许一个写入方」、「读取方不会收到撕裂尾部或无效记录」；
`dsh-session` 的 append-only 日志 + 从日志**派生**模型历史（压缩只遮蔽，不删除）。

【事实】本仓库现状：`AI._messages` **完全在内存**，进程退出即丢失；
只有记忆（ChromaDB）落盘。`ui_settings.json` 只存主题。没有任何会话恢复能力。

**判定：可移植（高价值，成本低）** → 落点 `castorice/session.py`。

【建议】Python 侧：

```python
# castorice/session.py
class SessionStore:
    """JSONL append-only，每会话单写者，flush 才返回。"""
    def __init__(self, root: Path, mode: str, session_id: str): ...
    def append(self, event: SessionEvent) -> int: ...      # 返回 seq
    def flush(self) -> None:
        """单调递增 seq + 原子替换；返回即表示已落盘（fsync）。
        抛异常 = 检查点失败 → 调用方必须阻止后续副作用。"""
    def events(self) -> Iterator[SessionEvent]: ...        # 冷读，容忍撕裂尾部
    def derive_messages(self, system_prompt: str) -> list[dict]:
        """从事件派生 OpenAI messages（对齐 dsh-session 的「派生历史」）。"""
```

- 文件布局 `sessions/<mode>/<session_id>.jsonl`，**按模式隔离**（t7 的会话隔离直接依赖它）。
- `checkpoint(reason)` 在 harness 的三处调用：模型请求前、顶层工具执行前、每个步骤前；
  失败时抛 `CheckpointFailed`，harness 捕获后**不执行下游副作用**并进入
  `StopReason.ERROR`（复刻 fail-closed）。
- 恢复：`load()` 读 JSONL，遇到撕裂的最后一行则**截断到最后一条完整记录**
  （不抛异常、不丢已完整记录）。
- 启动时把上次崩溃留下的「有 `tool/call` 但无结果」的调用补上
  `TOOL_OUTCOME_UNKNOWN` 结果（对齐 DSH 的 `interruptedTurnClosers`）。

### 1.6 事件流与前端投影

【事实】DSH 的链是：**append-only 会话日志** → **同步投影** → **变更通知** → 客户端。

- `dsh-session-projection`：领域根据已提交的会话事件定义**同步投影**；客户端拿到
  「经过 schema 校验的完整 JSON 值」的**快照 + 变更通知**；快照标明所有返回值共同反映到的
  **最后一个事件**（载体可把状态与对应的历史切面配对）；host-only 投影不暴露给客户端。
- `dsh-api-gateway` / `dsh-api-session-controller`：Host 侧 `ctx.typertGateway`、
  Client 侧 `ctx.remote`，同一份生成的 `InvocationDescriptor` 约定。
- `dsh-client-ui-*`（React）：由原始调用参数、结果内容、失败状态与持久 metadata
  **派生** card props（客户端派生展示，transport 拆分）。
- 模型可见文本由 `dsh-sandbox-policy` 的 `sandbox:policy`、`dsh-user-approval` 的
  `approval:policy` 等**贡献到运行时上下文快照**，循环把它记录为一条**带来源的
  `user/message`**——即「策略变化」是通过追加消息告知模型的，不改写请求头。

【事实】本仓库现状：`AI.get_response_stream` 的 `('text'|'tool'|'emoji'|'done')` 元组
已经是事件流雏形；`AIWorker`（`ui.py:815-895`）把它**翻译成四个 Qt Signal**：
`chunk_received(str)` / `tool_event(str,str)` / `emoji_event(str,str)` /
`finished()` + `error(str)`。`ChatWindow` 侧消费：`_on_chunk_received`（打字机）、
`_on_tool_event`（`ui.py:3353` 显示「🔧 正在调用 X …」气泡，done 时移除）、
`_on_emoji_event`、`_on_stream_finished`。

**判定：需改写** → 落点 `castorice/harness.py` 的事件投影 +
`ui.py` 的 `AIWorker`（保持 Signal 签名不变）。

【建议】Python 侧：

- **不引入 RPC/JSON 投影层**（`dsh-api-gateway` + Typert 是 Electron 主/渲染进程边界、
  跨进程/跨语言客户端产物）。PySide6 是单进程，等价物就是
  **QThread + Signal/Slot**：harness 跑在 worker 线程，UI 在主线程，跨边界只传
  **不可变值**（str / dict / dataclass）。
- **投影类**：`castorice/session.py` 提供 `SessionProjection`——`snapshot()` 返回
  `{"revision": int, "stop_reason": str, "tools_in_flight": [...], "mode": str,
  "workdir": str}`；`changed` 信号（无 Qt 时用回调列表）通知 UI。
  UI 的「模式 / 绑定目录 / 待办」面板读它，**不自行重放事件**（复刻 DSH 的
  「客户端不应自行重放原始事件日志」）。
- **策略变化用追加消息告知模型**：模式切换、权限策略变更、工作目录变更都追加一条
  `带来源的 user 消息`（如 `[系统] 当前模式：Code；工作目录：D:\proj`），
  **不改写 system prompt**——这样 KV Cache 前缀稳定，也避免每轮重建 prompt。
- **事件词汇要冻结在上文 1.1 的元组协议上**：UI 侧零改动是 t3 的验收条款之一。

---

## 2. 可移植清单（Portable）

编号供后续任务交叉引用。`落点` 一律相对仓库根。

| 编号 | DSH 机制 | 事实来源 | castorice/ 落点 | 验收锚点 |
| :--- | :--- | :--- | :--- | :--- |
| P1 | 模式 → 工具可见性 allow/deny 掩码，投影前求值 | `dsh-tools` `restrict`/`schemas(scope)` | `modes.py: ToolPolicy, ModeRegistry.visible_tools` | Chat 的 `to_openai_specs` 返回 `[]` 且请求体无 `tools` 键 |
| P2 | 未注册工具名 / 物化空过滤器在构造期抛错 | `dsh-tools/lib/index.js:2895-2909` | `modes.py: ModeRegistry.__init__` 校验 | 传入 `allow={"不存在的工具"}` 抛 `ValueError` |
| P3 | 逐调用沙箱策略解析：`explicit ?? fold(events) ?? default` | `dsh-sandbox-policy` | `permissions.py: PathPolicy.resolve` | 默认 `read-only`；会话切换后新调用生效、两会话互不影响 |
| P4 | 模式词汇 `read-only`/`workspace-write`/`danger-full-access`，故障安全默认 read-only | `dsh-sandbox` | `permissions.py` 常量 + `Mode.workdir_policy` | 未显式配置时写文件被拒 |
| P5 | 工作区根 = 会话不可变 cwd | `dsh-sandbox-policy` | `session.py: SessionHeader.cwd`；`permissions.py: PathPolicy` | 创建会话时固定，之后不可改 |
| P6 | 审批策略 `ask`/`never`，`never` 在分发前确定性拒绝 | `dsh-user-approval` | `permissions.py: ApprovalBroker.policy` | `never` 下任何 ASK 直接 DENY，且不调用应答者 |
| P7 | 应答者缺失/异常/超时 → `unavailable` → **按拒绝关闭** | `dsh-user-approval` 概述与分发说明 | `ApprovalBroker.request` | 无应答者时返回 DENY，铁定为不放行 |
| P8 | 一次性授权（`allowed-once`），同一次审批只允许一个在跑 | `dsh-user-approval`、`dsh-authorization` | `ApprovalBroker` 内部 `_in_flight: set[str]` | 第二次同 key 请求抛/拒（`ALREADY_IN_FLIGHT` 语义） |
| P9 | 工具流水线 pre→guard→execute→post，guard 单调不可翻回 | `dsh-tools` 设计理念与源码 | `permissions.py: PermissionGateway.evaluate` | 两个 guard 时先拒绝者胜出 |
| P10 | 拒绝以 `Error: <reason>` 作为普通工具结果返回（不抛异常、不中断轮次） | `dsh-tools/lib/index.js:3245-3256` | `harness.py` 的 tool 结果包装 | 拒绝后循环继续，模型基于错误文本作答 |
| P11 | 语义检查点：模型请求前 / 顶层工具副作用前 / 步骤前 | `dsh-session-checkpoint-policy/lib/index.js` | `session.py: SessionStore.flush` + `harness.py` 三处调用 | 写入失败时下游不执行 |
| P12 | `flush` 是持久性屏障，失败即阻止（fail-closed） | `dsh-session-persistence` | `SessionStore.flush` | monkeypatch 让 flush 抛错，断言工具未执行 |
| P13 | append-only 日志 + 从日志派生模型历史；压缩只遮蔽不删除 | `dsh-session` | `session.py: events() / derive_messages()` | 恢复会话后历史与崩溃前一致 |
| P14 | 崩溃后补全未完成的工具调用结果（`TOOL_OUTCOME_UNKNOWN`），不自动重试 | `dsh-agent-loop` 失败与取消 | `session.py: load()` 的收尾 | 人为截断 JSONL 后恢复，历史配对完整 |
| P15 | 终止原因显式（`turn/end` 携带 cause） | `dsh-agent-loop` | `harness.py: StopReason` | 触顶时返回 `MAX_ITERATIONS` 而非静默截断 |
| P16 | 独占调用构成排序屏障，并行安全调用有界并行，结果按模型顺序写回 | `dsh-agent-loop/README.zh.md` | `harness.py: HarnessLoop._dispatch_tools` | 混合批次的结果顺序等于模型给出的顺序 |
| P17 | 变更走 compare-and-set，陈旧 revision 被拒 | `dsh-experimental-agent-team`（任务板）、`dsh-goal` | `session.py: revision`；`memory/` 条目 revision | 用旧 revision 提交被拒 |
| P18 | 客户端读投影快照 + 变更通知，不重放日志 | `dsh-session-projection` | `session.py: SessionProjection` | UI 面板只读 `snapshot()` |
| P19 | 策略/上下文变化以**追加消息**告知模型，不改写请求头 | `dsh-sandbox-policy`、`dsh-user-approval` 的模型体验节 | `harness.py` 注入 `带来源的 user 消息` | 切换模式不重建 system prompt |
| P20 | 工具结果有界 + 溢出可恢复 | `dsh-output-retention`、`dsh-spill` | `tools/` 的返回包装 | 超长结果带省略说明与落盘路径 |
| P21 | 重复调用同一工具同参数无进展 → 温和提醒（3/5/8 次） | `dsh-repeat-tool-reminder` | `harness.py: RepeatReminder` | 不阻塞合理重复，只追加提示文本 |
| P22 | 工具调用协作式超时，超时以清晰错误返回 | `dsh-tool-call-timeout-policy` | `tools/` 定义上的 `timeout_s` | `run_command` 超时返回结构化结果 |
| P23 | 托管环境变量 `DSH_*`（`DSH_SESSION_ID` 等） | `dsh-shell-env` | `tools/command.py` 注入 `ASTER_*`（或沿用自有前缀） | 受限命令环境中可见会话 id 与模式 |

## 3. 需改写项（Rewrite）

DSH 里靠 Cordis / Electron / TypeScript 特性实现、在 Python/PySide6 里必须换写法的部分。

| 编号 | DSH 机制 | DSH 依赖 | Python 侧替代写法 | 落点 |
| :--- | :--- | :--- | :--- | :--- |
| R1 | 依赖注入 + 服务注册 + 生命周期（`ctx.inject`、`Service`、fiber dispose） | Cordis `@deepseek-ai/cordis` | 显式构造 + **组合根** `castorice/app.py`（t10）：一处 `build_app(config)` 按序 new 出 SessionStore → MemoryEngine → ToolRegistry → PermissionGateway → HarnessLoop，依赖以构造参数传入；释放用 `contextlib.ExitStack` | `castorice/app.py` |
| R2 | 事件 waterfall（`ctx.waterfall(name, payload, next)`，监听器可拦截/改道） | Cordis events | 普通函数链：`def run_chain(hooks, payload, terminal)`，每个 hook 签名 `(payload, next) -> Decision`；**同步**执行，异常就地捕获并降级（对齐 DSH 的「listener threw → warn 并继续」） | `permissions.py`、`harness.py` |
| R3 | 作用域上下文（`agent.ctx`、scope 层限制、`scopeTarget` 载体） | Cordis scope | 显式把 `session`/`mode` 作为参数传递；需要隐式上下文时用 `contextvars.ContextVar("castorice_session")`，**只做归因，不做鉴权**（对齐 DSH「发起方归因仅存在于进程内、不是授权」） | `session.py`、`harness.py` |
| R4 | `AsyncLocalStorage` 承载 initiator | Node async_hooks | `threading.local()` 或 `contextvars`（QThread 内单次运行，用局部变量即可） | `harness.py` |
| R5 | 弱引用观察表（`WeakMap` by owner → target） | `dsh-fs-observation-policy` | `weakref.WeakKeyDictionary` 以 session 对象为键，记录 `{绝对路径: (mtime, size, sha1)}`；读缺失路径记 `absent` 授权创建，读存在路径记版本供 CAS | `tools/fs.py` |
| R6 | 「编辑前必须读取」策略 + 版本守卫 | `dsh-fs-observation-policy`（`FS_NOT_OBSERVED` / `FS_NOT_FOUND`） | **原样移植语义**：`edit_file` 未读先改 → 拒；读后被外部改动 → 拒并要求重读。错误码用同样的字符串常量便于测试 | `tools/fs.py`、`permissions.py` |
| R7 | 异步流（AsyncGenerator + AbortSignal） | TS / Node | 同步生成器 `Iterator[tuple[str, Any]]` + `threading.Event` 取消位（现有 `get_response_stream` 已是生成器，保持形态） | `harness.py` |
| R8 | 每次尝试一个 `start` / 恰好一个终态 `end` 的事件三元组 | `dsh-agent-loop` | 映射到现有元组：`('tool', {'name':…, 'status':'start'|'done'})` 已是同构物；**补充** `('tool', {'status':'error', 'reason':…})` 作为 done 的细分（**新增 status 值不破坏旧消费方**，`ui.py:3368-3373` 对非 start 一律按完成处理） | `harness.py` |
| R9 | Typert RPC + Remote namespace（主/渲染进程边界） | `dsh-api-gateway`、`dsh-api-session-controller` | **不需要**：PySide6 单进程。等价物 = QThread（worker）→ `Signal` → 主线程 slot。跨边界只传不可变值 | `ui.py: AIWorker` |
| R10 | React 客户端派生 tool card props | `dsh-client-ui-tool` | `ChatWindow._on_tool_event` 现有的 QLabel 气泡即等价物；增强时按 `(tool_name, status)` 查一张 **展示映射表**（纯数据），不把渲染逻辑塞进 harness | `ui.py` |
| R11 | schema DSL + 类型推导（`defineTool` + `InferArgs`） | `dsh-tools/types/schema.js` | 沿用现有 `Tool.parameters` 的**手写 JSON Schema dict**（已足够）；增加一个构造期校验：`parameters` 必须是合法 JSON Schema 子集（`type/properties/required/additionalProperties`） | `ai.py: Tool`（只读）、`tools/` |
| R12 | PTC 模式（`run_code` + 生成 TS/Python SDK） | `dsh-tools` `mode: ptc` + `dsh-ptc-runtime-node` | **不引入**。工具数量小，直接 native 呈现 | — |
| R13 | 内核级沙箱（Windows ACL runner、`koffi` 原生调用） | `dsh-sandbox-windows-acl`、`dsh-win32-process` | **降级为进程内策略**：`pathlib.Path.resolve()` 归一化（含大小写、`..`、符号链接）→ `os.path.commonpath` 判父目录 → 白名单命令 + `subprocess.run(timeout=…)` + 输出上限。**明确记录为「策略级隔离，不是内核级隔离」**，并在 README 风险节申明 | `permissions.py`、`tools/command.py` |
| R14 | `DSH_*` 托管环境变量注册表（插件可注册事实、重复所有权报错） | `dsh-shell-env` | 简单常量表 + 构造期重复键检查 | `tools/command.py` |
| R15 | JSONL 持久化后端（`dsh-session-persistence-jsonl`） | Node fs + 原子替换 | `tempfile.NamedTemporaryFile(dir=same_dir)` → `os.replace` 原子替换；`os.fsync` 保证 flush 语义 | `session.py` |
| R16 | 会话格式版本迁移（`dsh-session-format-v0-to-v1` … `v3-to-v4`） | 独立迁移包 | 每个事件带 `"v": 1`；读到未知 `v` 时**拒绝加载并给出可读错误**（不做静默降级） | `session.py` |
| R17 | MCP 客户端（`mcp__<server>__<tool>` 命名） | `dsh-mcp-client` | Python 侧用 `mcp` 官方 SDK（后续期次）；t4 只需要**接入点**：`tools/mcp.py` 里的注册钩子与命名约定 | `tools/mcp.py`（t4 的扩展点） |
| R18 | `web_search`/`web_fetch` 的提供方抽象与「外部内容标记为不可信」 | `dsh-tool-web` | 现有 6 后端 `SearchManager`（`ai.py:942-1030`）**整体保留**，只补：结果文本前加「以下为外部不可信数据」前缀 + 抓取上限 | `tools/web.py` |

## 4. 不适用项（Not applicable）

| 编号 | DSH 机制 | 为什么不适用 |
| :--- | :--- | :--- |
| N1 | Cordis 插件/HMR/profile 组合（`cordis.yml`、`dsh-hmr`、`dsh-plugin-manager`） | 本项目是绿色便携 exe，无插件市场诉求；组合根 R1 已替代 |
| N2 | `dsh-authorization` 的凭据 flow 全套（OAuth、一次性码、`commit` 确认） | 本项目凭据只有一个 API Key，直接写 `config.yaml` 即可；只借其错误词汇（见 1.4） |
| N3 | `dsh-permission-presets` 的客户端目录/预设选择器 | 四模式本身就是用户可见的预设，再叠一层 `/permission` 是冗余；把 `sandbox` 与 `approval` 直接写进 `Mode` 即可 |
| N4 | `dsh-api-gateway`/`dsh-web`/`dsh-web-frontend`/`dsh-api-*` | Web 端与 RPC 层是 Electron 架构产物；桌面单进程不需要 |
| N5 | `dsh-terminal`/`dsh-tmux-context` 的常驻 shell 与 `dsh-tool-bash-persistent` | 便携桌面工具不做常驻终端；每次调用全新 shell 即可（与现 `subprocess` 语义一致） |
| N6 | PTC `run_code` 与其 worker 内存上限约束 | 见 R12 |
| N7 | `dsh-experimental-agent-team` 的完整团队/任务板 | 本项目是**单用户单机陪伴助手**，不是多 agent 编排器。只借「CAS 更新」与「revision 递增」两条契约（P17） |
| N8 | `dsh-hooks-claude-code`/`dsh-hooks-codex`、`dsh-hook-protocol` | 兼容外部 IDE 钩子的生态诉求，与本项目无关 |
| N9 | `dsh-otel`/`dsh-session-telemetry`/`dsh-client-product-analytics` | 便携软件不应上报遥测 |
| N10 | `dsh-compaction*` 的压缩后端族 | 现阶段单会话轮次短；先在 `memory/` 的 summary 模式里实现滚动摘要（t6），不引入完整压缩管线 |
| N11 | `dsh-ptc-runtime-*`、`dsh-office-to-pdf`、`libreoffice-kit` | 系统级/重型依赖，与 336MB 的发布包预算冲突 |
| N12 | Windows ACL runner / `windows-pwsh-sandbox` 的原生隔离 | 打包为纯 Python 分发会引入原生二进制与提权路径，风险大于收益（见 R13 的降级说明） |

## 5. 兼容红线（不可破坏项）

以下三条是**硬约束**，任何后续任务违反即视为回归。

### 5.1 `AI.get_response_stream` 的事件协议

**契约**（`ai.py:1755-1883`，消费方 `ui.py:848-895`）：

```text
('text',  str)                      流式文本增量
('tool',  {'name': str, 'status': str})   status ∈ {'start','done'}（可新增细分值）
('emoji', {'name': str, 'path': str})     额外发送一张贴纸
('done',  str)                        完整回复
```

铁律：

1. 元组长度恒为 2；`kind` 取值必须落在上述四个之内（**新增 status 值可以，新增 kind 不行**）。
2. `('done', …)` 必须恰好出现一次，且**在最后**（`AIWorker.finished` 依赖流自然结束）。
3. `AIWorker.run` 的 `isinstance(chunk, tuple) and len(chunk) == 2` 分支与
   `else: self.chunk_received.emit(chunk)` 的旧式纯文本兼容**必须继续可用**
   （`ui.py` 的 `MockBot` 也走这条路，见 `ui.py:3728-3739`）。
4. 异常路径必须仍然 `yield ('text', 错误文案)` + `('done', …)`，不得静默中断流
   （`ui.py:3476-3487` 依赖能收到 `finished` 才能恢复输入框）。
5. `AI.get_response(user_input)` 的「只收集 `text`」语义不变（`ai.py:1885-1899`）。

**落点要求**：`castorice/harness.py` 直接产出这四种元组；
`ai.AI.get_response_stream` 退化为**薄委托**（`yield from self._harness.run_stream(...)`），
并在委托前完成记忆检索与 `【当前时间】`/`【相关记忆】` 前缀的实际拼装位置保持不变。

### 5.2 `SettingsDialog` 写回 `config.yaml` 的注释保持逻辑

**契约**：`ui.SettingsDialog._on_save`（`ui.py:2215-2252`）→
`ConfigManager.set_values`（`ai.py:229-244`）→ `update_yaml_scalars`（`ai.py:95-158`）。

铁律：

1. `update_yaml_scalars` **只改目标键所在行**，**行尾注释原样保留**；
   键不存在时只在父块末尾追加，找不到父块则**跳过该键**（不擅自创造结构）。
2. 缩进、空行、键顺序、`#` 注释文本一律不动——README 第 127 行明确承诺
   「保存时会写回配置文件，**原有注释和排版一字不动**」。
3. t2 回归测试必须**逐字节**覆盖：给定带注释的 YAML 文本 → 更新一个键 →
   断言「除目标行外全文不变」且「目标行的注释仍在」。
4. 新增配置节（`modes:`、`permissions:`、`memory:`）时，
   **不要求 SettingsDialog 一定能写**——`example_config.yaml` 需要**预先包含**这些节，
   让 `_locate_yaml_key` 能找到父块。
5. 记忆/模式配置若走 `set_values`，键路径必须完整（`("modes","learn","vault_path")`），
   否则会命中「找不到父块则跳过」的静默分支。

### 5.3 `scripts/打包.ps1` 是否受影响

**结论：脚本本身不需要修改，但需要一次核查**。

事实：`scripts/打包.ps1` 的 PyInstaller 调用形如
`python -m PyInstaller … --collect-all chromadb --collect-all onnxruntime
--collect-all tokenizers … main.py`。它**从 `main.py` 出发做静态分析**，
`ai.py`/`ui.py` 都是被 `main.py` import 的本地模块，会被自动收集；
新增的 `castorice/` 包只要**经由 `main.py` → `ai.py` 的导入链可达**，同样会被收集。

t10 的核查清单：

1. `python -c "import main"` 在干净环境成功 → 说明导入链完整。
2. `castorice/` 内**不得**出现只在运行期用字符串动态 import 的模块
   （PyInstaller 静态分析会漏掉）——若有，必须改为顶层 import 或在
   `打包.ps1` 里加 `--hidden-import`。
3. 新增第三方依赖（若有）必须写进 `requirements.txt`，并评估是否像
   `torch` 一样需要 `--exclude-module`。
4. 发布包资源清单：`打包.ps1` 复制 `Image` 与 `prompts`；
   若新增 `prompts/modes/`，**已被 `Copy-Item prompts -Recurse` 覆盖**，无需改动。
   若新增 `sessions/` 落盘目录，**不要**复制进发布包（让用户全新启动），
   与现有「不放 database / logs / ui_settings.json」的策略一致。
5. `--windowed` 下没有控制台：新代码的异常必须走 `logging`
   （`main.setup_file_logging` 已把日志落到 `logs/app.log`），
   不能只 `print`。

---

## 6. 给下游任务的实现清单（可直接照做）

| 任务 | 必须落实的条目 |
| :--- | :--- |
| **t3** 四模式内核 + harness | P1 P2 P9 P10 P11 P12 P15 P16 P19；R1 R2 R3 R4 R7 R8；红线 5.1 |
| **t4** 权限网关与工具集 | P3 P4 P5 P6 P7 P8 P9 P10 P20 P22 P23；R5 R6 R13 R14 R17 R18 |
| **t6** 记忆引擎 | P17 P18；R15 R16；与 `MemoryManager` 旧接口并存（`ai.py:330-458` 语义保留） |
| **t7** 界面模式化 | P18；R9 R10；红线 5.1、5.2（设置窗口新增项必须预置在 `example_config.yaml`） |
| **t10** 集成收口 | R1（`castorice/app.py` 组合根）；红线 5.3 的 5 条核查 |

### 6.1 三个必须写进测试的负面用例

1. Chat 模式：断言请求 kwargs **不含** `tools` 键（不是断言 `tools == []`）。
2. 检查点失败：monkeypatch `SessionStore.flush` 抛错 → 断言工具函数**未被调用**。
3. 无应答者审批：`ApprovalBroker` 无注册应答者时返回拒绝 → 断言副作用未发生。

---

## 附：本文件的事实清单（便于复核）

- DSH 只读审计路径：`D:\Tool\Deepseek Harness\DSH Desktop\resources\app\`，
  内核包位于其 `node_modules\@deepseek-ai\`（约 260 个包，每包附 `README.zh.md`）。
- 直接读取过的 DSH 源码文件：
  `dsh-agent/lib/types/dispatch.js`、`dsh-agent/lib/types/index.js`、
  `dsh-tools/lib/index.js`（第 3200-3260、2893-2909、2960-3030 行）、
  `dsh-session-checkpoint-policy/lib/index.js`（全文）、
  `dsh-fs-observation-policy/lib/index.js`（前 80 行）、
  `dsh-tools/lib/types/py-types.js`（前 40 行）、
  `dsh-experimental-agent-team/README.zh.md`、
  `dsh-user-approval/README.zh.md`、`dsh-tools/README.zh.md`、
  `dsh-agent-loop/README.zh.md`、`dsh-sandbox-policy/README.zh.md`、
  `dsh-permission-presets/README.zh.md`、`dsh-authorization/README.zh.md`、
  `dsh-schedule/README.zh.md` 等约 25 个包的 `README.zh.md`。
- 本仓库直接读取过的文件：`ai.py`（全）、`ui.py`（关键段）、`main.py`（全）、
  `config.yaml`、`example_config.yaml`、`requirements.txt`、`README.md`、
  `docs/使用说明.txt`、`scripts/打包.ps1`、`prompts/`（目录与代表性片段）。
