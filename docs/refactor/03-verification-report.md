# 第一期改造独立验证报告（t12 · verification round 2）

> 验证者：reviewer（验证与评审工程师）
> 任务：t12 — verification-round-2（attempt acf1aae6-c5b6-4559-bb67-257ab0e2bdd8）
> 验证对象：t11（内核工具出口 mode / 占位会话泄漏 / 公开 mode 属性 / 模式沙箱配置）、t13（审批应答者接线与审批 UI）、t14（未完成事项识别 / Worldbook 纯查询语义）
> 结论：**部分闭环。F1 主链路、F2、F3、F5、F6、安全加固两点、死配置已真实闭环；F1 的错误码子项与 F4 的一个反例未达；另发现 1 个 t8 未覆盖的 blocker（Learn 模式工具被整表禁用）。**

---

## 1. verify 命令与退出码

| # | 命令 | exitCode | 输出摘要 |
|---|------|----------|----------|
| 1 | python -m pytest tests/ -q | 0 | 721 passed, 1 skipped in 162.46s (0:02:42) |
| 2 | python -c "import ai; print('ai ok')" | 0 | ai ok |
| 3 | python -c "import ui; print('ui ok')" | 0 | ui ok |

唯一 skip 为 tests/test_permissions.py:433（Windows 无权限创建符号链接的逃逸用例，属环境限制，非实现失败）。

**关于 692 vs 721**：契约给出的权威值 692 passed 是 t13 交付**之前**的计数。t13 新增 tests/test_approval_wiring.py（29 用例），692 + 29 = **721**，与本次实测一致。两者不矛盾。

---

## 2. 本次跑测的起始状态（【运行前清理】）

跑全量前实测：

    [干净] memory 不存在
    [干净] sessions 不存在

仓库根目录清单中无 memory/、无 sessions/（二者均为相对路径默认值，落点为进程 CWD）。跑测**结束后**复查仍为干净（见第 7 节）。因此本次为**从干净状态完整跑测**，得到 721 passed / 1 skipped，**未出现任何「仓库根不得出现某目录」类失败**。

**【并发干扰判定】**：本次未出现该类失败，故无需归类为环境干扰。防患证据仍记录如下——

- 单跑护栏文件 python -m pytest tests/test_offline_guarantees.py -q → 10 passed in 5.91s，exitCode 0；
- 跑测期间 tasklist 显示存在 python 进程（除本次 pytest 自身外还有其它 python.exe），团队其他成员进程在同一仓库并发运行是客观可能的；
- 起始与结束两次目录检查结果均为「不存在」，故无目录时间戳可供比对（无残留即无判定需求）。

---

## 3. 护栏基线比对（【权威值】+【不得使用无效判据】）

### 3.1 内容散列比对权威基线（首选方法）

    权威基线校验（10 个文件）
    expected 全部为有效 64 位散列: true

    MATCH   tests/conftest.py                    size=1228
    MATCH   tests/README.md                      size=4748
    MATCH   tests/test_backward_compat.py        size=4447
    MATCH   tests/test_offline_guarantees.py     size=3480
    MATCH   tests/test_yaml_update.py            size=8039
    MATCH   tests/test_config_manager.py         size=7108
    MATCH   tests/test_emoji.py                  size=12924
    MATCH   tests/test_html_to_text.py           size=3679
    MATCH   pytest.ini                           size=538
    MATCH   conftest.py                          size=513

    全部一致: true

**10/10 与 captain 给出的权威 sha256 完全一致。**

### 3.2 pyc 反向校验（交叉验证）

pyc 在字节码生成时固化源文件的 (mtime, size)；源文件此后若被编辑，两项必然变化。

| 源文件 | pyc 记录 mtime | 源文件 mtime | pyc size | 源 size | 结论 |
|---|---|---|---|---|---|
| tests/conftest.py | 1792079540 | 1792079540 | 1228 | 1228 | 一致 |
| tests/test_backward_compat.py | 1792081957 | 1792081957 | 4447 | 4447 | 一致 |
| tests/test_offline_guarantees.py | 1792079743 | 1792079743 | 3480 | 3480 | 一致 |
| tests/test_yaml_update.py | 1792079685 | 1792079685 | 8039 | 8039 | 一致 |
| tests/test_config_manager.py | 1792079579 | 1792079579 | 7108 | 7108 | 一致 |
| tests/test_emoji.py | 1792079619 | 1792079619 | 12924 | 12924 | 一致 |
| tests/test_html_to_text.py | 1792079685 | 1792079685 | 3679 | 3679 | 一致 |
| conftest.py | 1792079540 | 1792079540 | 513 | 513 | 一致 |

**两种独立方法互证：护栏文件逐字节未变。**

### 3.3 无效判据声明（契约明令禁止，本报告一律未使用）

- **未使用 git diff**：tests/ 与 castorice/ 是未跟踪目录，git diff 对其零输出，不能作为「未改动」的证据；
- **未使用期望值为 None 的哈希判定**：本报告所有 expected 均为非空 64 位十六进制串，且脚本内显式校验 expectedValid（布尔），**排除了「对 None 恒真」的无条件通过**；
- **未使用不存在的基线**：10 个基线全部由 captain 在本轮契约中给出，且逐一命中实际文件（size 一并记录）。

---

## 4. t8 findings 逐条闭环复验

### F2[blocker] 内核工具出口 mode 恒为 chat —— 已闭环

**根因与修复**：`ai.py:2496-2501`（原文无 backtick，见下）在 _sync_permission_context() 中调用 kernel_tools.set_mode(mode_name)（_activate_mode_session 必经此函数）；_KernelTools.set_mode 转发 ToolRuntime.set_mode（ai.py:2672-2673）。

**① 四个持有者 mode 一致**（契约点名）：逐模式切换 chat→work→code→learn→chat→code，每一步读四者：

    {"mode":"chat",  "ai.mode":"chat",  "harness.mode_name":"chat",  "gateway.mode":"chat",  "kernel_tools.mode":"chat",  "all_consistent":true}
    {"mode":"work",  "ai.mode":"work",  "harness.mode_name":"work",  "gateway.mode":"work",  "kernel_tools.mode":"work",  "all_consistent":true}
    {"mode":"code",  "ai.mode":"code",  "harness.mode_name":"code",  "gateway.mode":"code",  "kernel_tools.mode":"code",  "all_consistent":true}
    {"mode":"learn", "ai.mode":"learn", "harness.mode_name":"learn", "gateway.mode":"learn", "kernel_tools.mode":"learn", "all_consistent":true}
    ... all_consistent_every_step: true

**② 端到端真实路径 read_file**（契约要求，**不得只测单独构造的 ToolRuntime**）：code 模式绑定目录后，经 ai.AI._kernel_tools.call("read_file", ...)：

    kernel_tools.mode = "code"
    返回 = "文件: ...\f2root\hello.txt（共 2 行，显示第 1-2 行）
               1  T12-REAL-PATH-MARKER-内容
               2  第二行"
    包含文件正文 = true    未被拒绝 = true

**③ 对照**：chat 模式下同一真实路径调用被拒 —— "错误: 工具 'read_file' 在模式 'chat' 下不可见，拒绝执行；PERMISSION_DENIED"（可见性未被修坏）。

**④ work 模式真实路径亦成功**：kernel_tools.mode="work"，返回含「内容-work」。

> 说明：learn 模式下 read_file 被拒**不是 F2 残留**，而是 N1 缺陷（见第 6 节）——learn 的真实可见集合里根本没有 read_file。

### F3[medium] 占位会话泄漏写者登记 —— 已闭环

修复方式为「复用」：初始会话暂存 _pending_session（ai.py:1591-1603），由 _build_session 在模式匹配时复用；新增 AI.close()（ai.py:2162）与 with 支持（ai.py:2190-2193）。

| 判据 | 实测 |
|---|---|
| 同一 sessions_dir 连续构造两个 AI 实例 | 实例1 ...4e0e853f，实例2 ...1e26cae6，**无 SessionInUseError** |
| close() 后可重建 | close 前登记数 3 → close 后 2（**已释放**），新会话可建，_closed=true |
| with 支持 | before 3 → inside 4 → after 3（**退出即释放**） |
| 反复构造 4 个实例无孤儿（隔离 sessions_dir） | 本次新增登记 4、live 会话 4、**孤儿 0**；close 后本次登记残留 0 |

> **判据写法自纠**：首次测量出现「孤儿 3」是我的探针在同一 sessions_dir 上叠加了先前用例的未关闭实例所致。改用独立 sessions_dir 隔离后孤儿为 0。这正是契约「归因类断言必须在分离条件下取得」要防的坑，记录于此以免误判为缺陷。

### F6[low] _KernelTools 缺公开 mode 属性 —— 已闭环

_KernelTools.mode 已为 property，转发 self._runtime.mode（ai.py:2662-2670）；AI.tools_mode 只读属性（ai.py:2531-2540）。实测二者与 ai.mode 始终一致。

### F5[low] Worldbook 纯查询语义 —— 已闭环（按修正后的结构性判据）

**t8 的 F5 描述已由验证者本人主动撤回。** 撤回成因：t8 探针在 match() 与 relevant() **都调用过之后**才读取 access_count，却把差值归因给了 match()。该表达式为 False 是 relevant() 造成的，**match() 从未使 access_count 0→1**。

**分离归因证据**（只调用被测方法、读取前不混入其它会改变状态的调用）：

| 分离条件 | 观察值 |
|---|---|
| 初始 → 仅 match() ×5 | 0 → **0**（未改变） |
| 初始 → 仅 match 后 0 → 再 relevant 后 | 0 → 0 → **1**（match 不记账，relevant 记账） |
| match() ×50 后快照 (access_count, last_accessed, state, revision) | (0, '', 'active', 1) 与初始**完全一致** |

**三条结构性判据**（本契约指定的唯一判据，**未以「能否复现 0→1」作为通过条件**）：

| 判据 | 实测 | 结果 |
|---|---|---|
| ① match() 返回值与内部状态隔离 | is_alias=False（返回 WorldbookEntry 快照）；外部写 got[0].access_count=99 后内部仍为 0 | 通过 |
| ② 反复调用 match() 不改变 evict_stale 淘汰结果 | 「不 match」与「match ×20」：evicted_target 同为 true、淘汰条数同为 1 | 通过 |
| ③ 对照 relevant() 仍正常记账并使条目免于淘汰 | access_count 0→1、last_accessed 刷新；relevant 过的条目**未被淘汰**、未 relevant 的**被淘汰** | 通过 |

机制：worldbook.py:193 snapshot() = WorldbookEntry.from_dict(self.to_dict())；match() 于 :453 对 _match_internal 结果逐个 snapshot()；enabled 开关下沉到 _match_internal，两条路径共用，无法被绕过。

### F1[blocker] 审批应答者从未接线 —— 主链路已闭环，错误码子项未达

ui.py 现于真实 GUI 启动路径注册应答者：_install_approval_responder()（ui.py:3322）→ bot.set_approval_responder(self._on_permission_request)；worker 线程侧 _on_permission_request（ui.py:3367）只 defer + emit（不碰控件）；主线程槽 _on_approval_requested / _on_approval_allow / _on_approval_deny / _resolve_approval（ui.py:3397-3426）。

**端到端（offscreen 真实 ChatWindow，先建窗再用界面 switch_mode("code")，与真实用户路径一致）**：

| 场景 | 应答者被调用 | 文件 | 模型/调用方收到 | 结果 |
|---|---|---|---|---|
| allow | 1 次（tool=write_file, rid=write_file） | **存在**，内容 = F1-ALLOW-CONTENT（正确） | "已写入 ...\allowed.txt（16 字符，新建文件）" | 通过 |
| deny | 1 次 | **不存在** | "错误: 未获批准（PERMISSION_DENIED）" | 通过 |
| 短超时（1s，应答者不答复） | 1 次 | **不存在** | "错误: 审批无应答或超时（1.0s）或窗口已关闭，按拒绝关闭（PERMISSION_DENIED）" | 通过 |
| 无应答者 | 未调用 | **不存在** | "错误: 没有可用的审批应答者，按拒绝关闭（fail-closed）（PERMISSION_DENIED）" | 通过 |

**无应答者路径为即时拒绝**：broker.timeout 默认 120.0s，但无应答者分支**在分发前**确定性返回，实测耗时 **0.0026 / 0.0035 秒**（< 1s），**未引入 120s 等待**。

**测试路径不得强制注册应答者**：测试中显式 set_approval_responder(None) 后仍为即时拒绝（无应答者分支），未强制任何注册。

**审计事件**（只进日志、不进模型可见文本）：

    allow:   {"event":"approval/decided","tool":"write_file","mode":"code","request_id":"write_file","decision":"allow"}
    deny:    {"event":"approval/decided",...,"decision":"deny"}
    timeout: {"event":"approval/decided",...,"decision":"unavailable","code":"APPROVAL_TIMEOUT"}
    无应答者: {"event":"approval/decided",...,"decision":"unavailable","code":"APPROVAL_NO_RESPONDER"}

#### 未达子项：deny 路径的 APPROVAL_* 错误码

契约原文要求：「应答者 deny 或短超时 → 文件不存在且模型收到含 APPROVAL_* 的错误码」。

实测**所有路径下「模型收到的内容」都不含 APPROVAL_***：

| 场景 | 模型收到 | tool 事件 reason | 审计 code | 含 APPROVAL_ |
|---|---|---|---|---|
| deny | Error: deny | deny | （无 code） | 否 |
| 短超时 | Error: 审批无应答或超时（0.5s）... | 同长文本 | APPROVAL_TIMEOUT | 否 |
| 无应答者 | Error: 没有可用的审批应答者... | 同长文本 | APPROVAL_NO_RESPONDER | 否 |
| allow | 已写入 ... | — | （无 code） | — |

**根因（分离归因）**：APPROVAL_* 常量只定义在 castorice/permissions.py（APPROVAL_TIMEOUT / APPROVAL_NO_RESPONDER / APPROVAL_RESPONDER_FAILED / APPROVAL_UNKNOWN_ANSWER / APPROVAL_POLICY_NEVER）并写入 **AuditEntry.code**；而模型可见文本由 harness 的 _invoke 统一生成 "Error: " + reason（harness.py:844-848），**只取 reason、丢弃 info.code**。permissions.py 的 code 从未流到模型可见文本。castorice/tools/base.py、harness.py、ai.py 中 APPROVAL_ 出现次数均为 **0**。

- **期望**：deny/超时路径的模型可见文本含 APPROVAL_*（或至少含结构化 code）。
- **实际**：deny 为 "Error: deny"（**连 PERMISSION_DENIED 都没有**，因为 deny 走 harness 路径、decision.allowed=False 时直接使用 reason）；超时/无应答者带长中文 reason 但无 code。
- **严重度**：medium。功能安全性不受影响（deny/超时/无应答者**都正确地阻止了落盘**），但违反契约明文，且模型无法据错误码区分「被用户拒绝」与「超时」。

### 死配置 modes.<name>.sandbox 无效 —— 已闭环

resolve_sandbox()（ai.py:1473）为单点权威，5 个读取点全部改走它（ai.py:1562 / 2319 / 2324 / 2458 / 2486）。

**三处一致且随配置改变**（契约点名 path_policy / available_modes / projection 三者）：

| modes.code.sandbox | path_policy.sandbox | available_modes.code.sandbox | projection.snapshot().sandbox_mode | gateway.sandbox |
|---|---|---|---|---|
| read-only | read-only | read-only | read-only | read-only |
| workspace-write | workspace-write | workspace-write | workspace-write | workspace-write |
| danger-full-access | danger-full-access | danger-full-access | danger-full-access | danger-full-access |

**优先级**「越具体越优先」实测：modes.code.sandbox=danger-full-access + permissions.sandbox_default=read-only → 生效 danger-full-access（模式级胜出）。

**非法值回落 + warning**：modes.code.sandbox='not-a-sandbox' → 记录 warning "modes.code.sandbox='not-a-sandbox' 不是合法沙箱模式，已忽略；可选: ['danger-full-access','read-only','workspace-write']"，**未抛异常、程序正常启动**。

**回落落点矩阵**（确认「回落」是逐级下落而非一步到底，属正确设计）：

| 场景 | 生效 | code 内置默认 | == 内置默认 |
|---|---|---|---|
| 模式级非法 + 全局合法 read-only | read-only | workspace-write | 否（落到全局配置，正确：全局是合法配置） |
| 模式级非法 + 全局非法 | workspace-write | workspace-write | **是**（逐级落到内置默认） |
| 模式级缺失 + 全局非法 | workspace-write | workspace-write | **是** |
| 模式级非法 + 全局缺失 | read-only | workspace-write | 否（落到故障安全默认 read-only） |

→ 逐级回落语义自洽，**非缺陷**。

### 安全加固（t11 自报的两点，独立复核）—— 已闭环

**① 无应答者时经 ai.AI._kernel_tools 直接调 write_file 不得落盘**（契约点名）：

    ask_callback = null      policy = "ask"
    返回 = "错误: 没有可用的审批应答者，按拒绝关闭（fail-closed）（PERMISSION_DENIED）"
    文件已落盘 = false       未落盘 = true       返回失败 = true

**② 注入 deny 应答者时同样不得落盘**：

    应答者调用次数 = 1
    返回 = "错误: 未获批准（PERMISSION_DENIED）"
    文件已落盘 = false       未落盘 = true

机制：ToolRuntime 新增 already_authorized 回调（tools/base.py:423-453）；harness 轮次内（_KernelTools.turn() 标记，ai.py:2026）不重复判定以免同一次调用弹两次审批、破坏 allowed-once；轮次外（tools/base.py:484-501）自行走完整 gateway.decide，且**显式传入 cwd=self.gateway.workspace**（:495）修正了相对路径按进程 CWD 解析的问题。

---

## 5. t14 的 F4 闭环复验 —— 部分未达

### 5.1 主诉已修复

| 判据 | 实测 | 结果 |
|---|---|---|
| 裸写 TODO： | has_unfinished_marker=True，命中 "TODO：" | 通过 |
| 裸写 TODO 未完成： | True，命中 "TODO 未完成：" | 通过 |
| 裸写 未完成： | True，命中 "未完成：" | 通过 |
| 裸写 待办 | True，命中 "待办" | 通过 |
| 方括号 [TODO] / [未完成] / [待办] | True | 通过 |
| 超长（630 字符含内部换行与缩进）压缩后 | verbatim_line_present=True（**逐字原样保留**）、in_unfinished_section=True（进入【未完成事项】段）、indent_preserved=True（"换行 + 4 空格 + 生日惊喜（缩进保留）" 保留） | 通过 |
| t14 自报的「809→402 已不复现」 | 输入 630 / 摘要 723（未截断） | 通过 |

### 5.2 契约点名的反例之一被误判

契约原文反例：「反例（普通叙述、**todo list 说明**、xxxl）不得被误判」。

| 反例 | 期望 | 实际 | 结果 |
|---|---|---|---|
| 普通叙述「今天天气不错」 | False | False | 通过 |
| **「这是我的 todo list 说明」** | **False** | **True**，命中裸写 "todo" | **误判** |
| 「xxxl 是尺码」 | False | False | 通过（英文整词负向断言生效） |
| 补充「已完成：交稿」 | False | False | 通过 |

**复现步骤**：

    from castorice.memory.entries import has_unfinished_marker, _UNFINISHED_RE
    has_unfinished_marker("这是我的 todo list 说明")            # -> True（期望 False）
    _UNFINISHED_RE.search("这是我的 todo list 说明").group(0)  # -> 'todo'

- **期望**：False（契约明文反例）。
- **实际**：True。
- **涉及文件与行号**：castorice/memory/entries.py:34（_UNFINISHED_RE）。该正则的裸写分支为 (?<![A-Za-z])(?:todo|fixme|xxx)(?![A-Za-z]) —— 只做了**英文整词**负向断言（因此 xxxl 不命中），但**未要求其后紧跟冒号或位于行首**，故句中出现的裸 todo 即命中。中文裸写（待办）同理，靠上下文词边界也难以完全消歧。
- **严重度**：low~medium。后果是「一句提到 todo list 的普通叙述」会被升级为未完成事项、逐字保留且不受摘要上限约束，属**误报**（false positive）：不丢信息，但会污染【未完成事项】段、挤占上下文。
- **为何 t14 的 37 条新用例未捕获**：t14 自报覆盖「5 种普通叙述反例」，但按本次实测，其反例集合中不含「含裸 todo 字样的普通叙述」这一形态。

---

## 6. 新发现缺陷 N1[blocker]：Learn 模式工具被整表禁用

> 这是 t8 未覆盖、t11/t13/t14 均未触及的缺陷，直接决定「第一期功能可用」的判定。

### 6.1 现象

Learn 模式的**实际可见工具**与设计意图完全相反——笔记工具 0/3 可用，反而可用的是音乐/提醒类工具：

| | 工具 |
|---|---|
| LEARN_ALLOWED **声明** | get_current_time, glob_files, grep_files, list_dir, read_file, read_note, search_notes, write_note |
| learn **实际可见** | clear_reminders, list_reminders, play_music, set_reminder, system_info |
| **声明却不可见** | get_current_time, glob_files, grep_files, list_dir, read_file, read_note, search_notes, write_note（全部 8 个） |
| 笔记工具（NOTE_TOOL_NAMES）实际可见 | **0 个** |

prompts/modes/learn.md 明确要求「只读笔记、只写笔记、不得执行命令、不得写其它文件」，但实际连 read_file 都被拒——**提示词承诺与实际能力完全脱节**。

### 6.2 真实路径复现（经 ai.AI._kernel_tools，learn 模式且 Vault 已绑定）

    kernel_tools.mode = "learn"     gateway.mode = "learn"
    search_notes -> "错误: 工具 'search_notes' 在模式 'learn' 下不可见，拒绝执行；PERMISSION_DENIED"
    read_note    -> "错误: 工具 'read_note'    在模式 'learn' 下不可见，拒绝执行；PERMISSION_DENIED"
    write_note   -> "错误: 工具 'write_note'   在模式 'learn' 下不可见，拒绝执行；PERMISSION_DENIED"
    read_file    -> "错误: 工具 'read_file'   在模式 'learn' 下不可见，拒绝执行；PERMISSION_DENIED"

**四模式可见数量对照**（证明是 learn 专有）：chat 0 / work 19 / code 16 / **learn 5**。

### 6.3 分离归因（只读 Mode 对象与常量，无其它调用干扰）

    learn.tool_allowlist          = []
    learn.tool_policy.allow       = None
    learn.tool_policy.deny        = 14 项，且 deny == set(ALL_TOOLS_DENIED) -> True

即 learn 的 allow_mask 退化成 **deny-ALL**，把 14 个工具全部禁掉；而 play_music 等不在 ALL_TOOLS 表内的工具因此反而不受 deny 约束、被放行。

**根因定位（关键对照实验）**：

ai.py:1386 的 declared = set(ctools.KNOWN_MODES) | set(allowed) 里，ctools.KNOWN_MODES 是**模式名集合** {chat, code, learn, work}，allowed 的 **keys** 也是模式名 —— 因此 declared 是**纯模式名集合**（实测 declared_is_mode_names_only = true）。它被当作 builtin_modes(known_tools=...) 的**工具名清单**传入：

| 传入 builtin_modes 的参数 | learn 的 tool_allowlist | learn 的 policy.deny 数 |
|---|---|---|
| declared（模式名，**ai.py 实际用法**） | [] | **14** |
| 真实工具名（正确用法） | ['get_current_time','glob_files','grep_files','list_dir','read_file','read_note','search_notes','write_note'] | 0 |

链路：modes.py:571 的 available = set(known_tools) 只含模式名 → modes.py:573-574 的 keep() 取交集得空 → modes.py:582-585 的 allow_mask 得 kept=空 → 退化为 ToolPolicy(allow=None, deny=ALL_TOOLS_DENIED)。

**work/code 为何恰好无害**：二者用 ToolPolicy(allow=None, deny=...)（work 空 deny、code 只 deny 三个笔记工具），deny 表独立于 allow_mask，所以裁剪不影响它们；**只有 learn 走 allow_mask 分支，因此只有 learn 被打穿**。

### 6.4 归属判定

**非 t11 引入**：t8 阶段实测 learn 可见集合即为同样 5 个工具（play_music, set_reminder, list_reminders, clear_reminders, system_info），现象**先于** t11 存在。t3/t4 的用例只断言了「chat 为空」「code 不含笔记工具」，未对 learn 做「声明 = 实际」断言，故一直未被发现。

### 6.5 期望 / 实际 / 影响

- **期望**：learn 实际可见 = LEARN_ALLOWED（含 3 个笔记工具 + read_file / 读目录类），笔记工具经审批后可用。
- **实际**：8 个声明工具全部不可见，笔记工具 0/3 可用；无关的娱乐/提醒工具反被暴露。
- **涉及文件与行号**：ai.py:1386（declared 语义错误），连带 castorice/modes.py:571 / 573-574 / 582-585。
- **影响**：第一期「Learn = Obsidian Vault 笔记」这一目标**功能性不可达**；同时因 deny-ALL 失效路径而**放行了本不该出现的工具**，属安全边界的反向偏差。

---

## 7. 环境自证（未污染、未改动）

- 跑测**前后**两次检查仓库根：memory/、sessions/ **均不存在**；
- 所有探针脚本置于系统临时目录 C:\\Users\\34117\\AppData\\Local\\Temp\\t12-verify\\，仓库内**未新增任何文件**（本报告 docs/refactor/03-verification-report.md 是 t12 契约内的交付物，属 in-scope）；
- 探针的 chroma_dir 指向仓库 database/ 仅做只读查询，写入一律 tmp_path；sessions_dir 一律指向临时目录；
- 护栏 10 个文件经**内容散列 + pyc 反向校验**双重证明未改动（第 3 节）。

---

## 8. 结论与建议

### 已闭环（8 项）

F1 主链路、F2、F3、F5、F6、安全加固两点、死配置 —— 全部以真实路径或结构性判据独立复验通过。

### 未闭环（3 项）

| 项 | 严重度 | 位置 | 要点 |
|---|---|---|---|
| **N1** | **blocker** | ai.py:1386 | Learn 模式工具被整表禁用，笔记工具 0/3 可用；提示词承诺与实际脱节 |
| **F1 部分** | medium | harness.py:844-848 | deny/超时路径的模型可见文本不含 APPROVAL_*（deny 连 PERMISSION_DENIED 都没有），契约明文未达 |
| **F4 部分** | low~medium | entries.py:34 | 「这是我的 todo list 说明」被误判为未完成事项（契约点名反例） |

### 给下一轮的建议

1. **N1 必修**：修正 ai.py:1386 的 declared 语义（应传**已注册工具名**，而非模式名），并补一条「learn 实际可见 == LEARN_ALLOWED」的回归断言 —— 这类「声明 vs 实际」断言正是当前测试集的盲区。
2. **F1 错误码**：让 harness 在拒绝时把 info.code 一并带进模型可见文本（或在 deny 分支使用 CODE_APPROVAL_DENIED 之类的结构化码），使「用户拒绝 / 超时 / 无应答者」三者在模型侧可区分。
3. **F4 反例**：为裸写 todo / fixme / xxx 增加「行首或紧跟冒号」的位置约束，把契约点名的「todo list 说明」纳入反例用例集。
4. **验证方法沉淀**：本轮两次踩到「分离条件」与「跨实例 id 比较」两类陷阱（均已自纠并记录），建议把这两条写进后续任务的判据模板。

---

# 修复轮第 3 次独立验证报告（t18 · verification round 3，承接 t16）

> 验证者：verify2（独立验证工程师，未参与 t15/t17 实现）
> 任务：t18 — attempt 9f0310b7-da5f-40f3-bae1-54431a56005a（承接 t16 因模型限流失败的验收范围）
> 验证对象：t15（core-fix：N1 Learn 工具表修复、F1b 审批错误码）与 t17（memory-fix：F4b 反例收紧）
> 结论：**全部闭环。t12 的三项未闭环（N1 / F1b 错误码 / F4b 反例）已真实修复；前期闭环（F2/F3/F5、内核出口无应答者不落盘、越界拒绝、死配置三处一致）零回归。**

---

## 1. verify 命令与退出码

| # | 命令 | exitCode | 输出摘要 |
|---|------|----------|----------|
| 1 | python -m pytest tests/ -q（第 1 次） | 1* | **738 passed, 1 skipped** + 1 个 teardown error（见 §2 并发干扰判定） |
| 2 | python -c "import ai; print('ai ok')" | 0 | ai ok |
| 3 | python -c "import ui; print('ui ok')" | 0 | ui ok |
| 4 | python -m pytest tests/test_memory_modes.py -q | 0 | **127 passed** in 25.61s（t17 报告值一致） |
| 5 | python -m pytest tests/test_memory_modes.py::test_worldbook_capacity_archives_instead_of_deleting -q | 0 | 1 passed in 15.12s |
| 6 | python -m pytest tests/ -q（清理后干净重跑） | **0** | **738 passed, 1 skipped** in 91.43s —— 与 captain 实测基线完全一致 |

唯一 skip 为 tests/test_permissions.py:433（Windows 无权限创建符号链接的环境限制，与 t12 记录一致，非实现失败）。

---

## 2. 起始状态与【运行前清理】

- 跑全量**前**实测：仓库根 `memory/` 不存在、`sessions/` 不存在（ls: cannot access ... No such file or directory），为干净起始状态；
- 第 1 次全量出现 1 个 teardown error（test_memory_modes.py:77 repository_database_is_untouched 指纹断言，chroma.sqlite3-journal 指纹漂移）。按「先排查并发干扰」流程：**单独重跑该用例 → 1 passed（exit 0）**；同时段团队其它成员在同一仓库并发运行是客观存在的，判定为**并发干扰**，非实现缺陷；
- 排查期间发现本人探针（23:22–23:27 在仓库根以默认 sessions_dir 构造 AI 实例）留下**空的** sessions/chat、sessions/learn 目录（0 文件），确认为自留残留后已 rmdir 删除恢复干净，随后**干净重跑全量 → 738 passed / 1 skipped，exit 0**，跑后复查 memory/、sessions/ 均不存在。
- 【判据写法自纠】这与 t12 记录的「探针残留污染判据」是同一类坑：归因类断言必须在分离条件（独立 sessions_dir / 独立 cwd）下取得，本轮后续探针全部改用临时目录。

---

## 3. 护栏基线比对（内容散列，10/10 全一致）

sha256sum 实测与 captain 权威基线逐一比对：全部 10 个文件 MATCH（含完整 64 位散列逐一命中，此处列出文件名）：

    MATCH  tests/conftest.py
    MATCH  tests/README.md
    MATCH  tests/test_backward_compat.py
    MATCH  tests/test_offline_guarantees.py
    MATCH  tests/test_yaml_update.py
    MATCH  tests/test_config_manager.py
    MATCH  tests/test_emoji.py
    MATCH  tests/test_html_to_text.py
    MATCH  pytest.ini
    MATCH  conftest.py

**10/10 完全一致**。

【自证方法声明】：本轮全部使用 sha256sum 内容散列比对权威基线；未使用 git diff（未跟踪目录零输出）、未使用 None 期望哈希判定、全部 10 个基线均为 captain 给出的有效非空散列且逐一命中实际文件。

---

## 4. N1 闭环复验 —— 已闭环

### 4.1 主判据：learn 实际可见集合 == LEARN_ALLOWED（集合相等）

经真实装配 ai.AI() → _mode_registry.visible_tool_names：

    learn = ['get_current_time','glob_files','grep_files','list_dir','read_file','read_note','search_notes','write_note']  (8 个)
    learn == set(LEARN_ALLOWED) = True   missing = {} extra = {}
    work = 19 / code = 16 / chat = 0

分离归因（只读真实装配 Mode 对象）：

    learn.tool_allowlist == LEARN_ALLOWED（8 个，集合相等）
    learn.tool_policy.allow = frozenset(同 8 项)
    learn.tool_policy.deny = （空）—— t12 记录的 deny-ALL（14 项）已消失
    ai.py:1393 declared = frozenset(spec.name for spec in self.tool_registry.specs())，
    并有 assert not (declared & set(ctools.KNOWN_MODES)) 防重名护栏；
    实测 registry 19 个工具名与 4 个模式名交集为空。

### 4.2 端到端：learn 经 ai.AI._kernel_tools 真实调用三笔记工具

分离条件：临时目录作为 cwd + 独立 sessions_dir + 正确签名的 allow 应答者（request, context 双参）。

| 工具 | 调用 | 结果 |
|---|---|---|
| write_note(path, content, title) | 审批 allow 后执行 | 「已新建笔记 ...学习/算法.md（55 字符）」，**真实落盘**，盘上内容含 frontmatter（title/created）与正文 |
| read_note(path) | 直接执行 | 「笔记: 学习/算法.md（算法）」+ 完整 Markdown 正文，**真实读出** |
| search_notes(query='快速排序') | 直接执行 | 「找到 1 篇笔记」，命中刚写入的 学习/算法.md |

三者均**未返回**「在模式 learn 下不可见」。期间 write_note 走了真实审批链（permission approval/asked → decided allow）。

对照回归：play_music / set_reminder / clear_reminders / list_reminders / system_info 在 learn 下全部不可见（deny-ALL 失效路径放行的 5 个工具已被收回）。

### 4.3 防回归（已裁决口径）

    work = 19（含 3 个笔记工具，冻结基线）
    code = 16（不含 3 个笔记工具）
    chat = 0

三项均与基线一致。

---

## 5. F1b 闭环复验 —— 已闭环

经 **harness._invoke 真实派发出口**（harness.tools is kernel_tools = True，mode=learn）三路径模型可见文本：

| 路径 | 模型可见文本 | 结构化码 | 文件落盘 |
|---|---|---|---|
| deny（应答者返回 deny） | `Error: deny（PERMISSION_DENIED）` | PERMISSION_DENIED（回落码，非裸 'deny'） | 否 |
| 超时（应答者阻塞 2s，gateway.timeout=0.5s，实测 2.01s 走通） | `Error: 审批无应答或超时（120.0s）或窗口已关闭，按拒绝关闭（APPROVAL_TIMEOUT）` | APPROVAL_TIMEOUT | 否 |
| 无应答者（callback=None） | `Error: 没有可用的审批应答者，按拒绝关闭（fail-closed）（APPROVAL_NO_RESPONDER）`，**即时返回（0.003s）** | APPROVAL_NO_RESPONDER | 否 |

- **三文本互异**：set 大小 = 3；deny 不再是裸 'deny'（t12 记录的 "Error: deny" 已变为 "Error: deny（PERMISSION_DENIED）"）；
- 超时路径为**真实阻塞超时**（应答者 sleep 2.0s > timeout 0.5s，实测耗时 2.01s 后返回），非「sleep 后返回 None」的伪路径；
- **AuditEntry.code 语义不变**：审计流里 deny 路径仍无 APPROVAL_*（decision='deny' 原样），超时/无应答者仍为 APPROVAL_TIMEOUT / APPROVAL_NO_RESPONDER——码只是**同时**呈现到模型可见文本，审计语义未被改写；
- 机制：harness.py:853-854 `text = "Error: " + reason + "（" + code + "）"`，code 由 _denial_code(decision) 从 decision.info.code 取，缺失回落 PERMISSION_DENIED，绝不出现裸码；tool 事件 payload 同时携带 code 键（实测三 payload 的 code 与上表一致）。

---

## 6. F4b 闭环复验 —— 已闭环

castorice/memory/entries.py 裸写分支已收紧为行首约束（_LEADING_ALLOWED 允许缩进/项目符号，_LEADING_PREFIX 允许【…】短前缀）：

| 输入 | 期望 | 实测 |
|---|---|---|
| 「这是我的 todo list 说明」 | **False** | **False**（_UNFINISHED_RE.search → None） |
| 「待办 明天交稿」 | True | True |
| 「[未完成] 报告」/「TODO：买菜」/「未完成：周报」/「【待办】整理房间」/「- TODO 买牛奶」 | True | 全部 True |
| 「今天天气不错」/「xxxl 是尺码」/「我的 fixme 记录」/「列个 todo 吧」/「已完成：交稿」 | False | 全部 False |

**超长逐字保留不回归**（复刻 tests/test_memory_modes.py:976 用例，独立 SummaryMemory 实例逐 marker 分离验证）：

    marker 属于 {"[未完成] ", "【待办】", "待办 ", "- TODO "}：800+ 字符输入压缩后
    verbatim in summary = True、与原文完全相等的行存在、进入【未完成事项】段、unfinished_count = 1（4/4 通过）

tests/test_memory_modes.py 单跑 **127 passed**（t14 的 117 例零回归 + t17 新增 10 例）。

---

## 7. 前期闭环回归 —— 零回归

| 项 | 复验方法 | 结果 |
|---|---|---|
| F2 四组件 mode 一致 | 真实装配 AI，逐模式 chat→work→code→learn→chat→code，读 ai.mode / harness._mode_name / gateway.mode / kernel_tools.mode | 六步全部一致 |
| F2 端到端 read_file | code 模式绑定目录后经 kernel_tools.call 真实读出 T18-REAL-MARKER；chat 对照被拒（不可见） | 通过 |
| F3 同 sessions_dir 可重建 | 分离条件（独立 sessions_dir）：同目录连续两实例无 SessionInUseError；close() 后重建成功；with 进入/退出正常 | 通过 |
| F5 三条结构性判据 | ① match() 返回 snapshot，外部写 access_count=99 后内部 to_dict 全等（语义等价比对，非对象 id）② match ×0 与 ×20 后 evict_stale 淘汰结果一致（access 均为 0）③ relevant() 记账 0→1，归档后 relevant 条目免于淘汰、闲置条目被淘汰 | 通过 |
| 内核出口无应答者不落盘 | work 模式 kernel_tools.call('write_file') 无应答者：即时拒绝（0.003s），文件不存在；deny 注入同样不落盘 | 通过 |
| 越界 FS_SANDBOX_DENIED 且应答者零调用 | learn 模式对工作区外路径 write_note：harness 派发出口文本含（FS_SANDBOX_DENIED）、payload.code=FS_SANDBOX_DENIED，审批应答者调用次数 = 0，文件不存在 | 通过 |
| 死配置三处一致 | 三种 sandbox 值 x path_policy / available_modes(code) / gateway.sandbox，三处两两一致且随配置联动 | 通过 |

---

## 8. 环境自证

- 全部探针置于系统临时目录（%TEMP%/v3*_ 前缀），仓库内除本报告（docs/refactor/ 为契约 in-scope）外未新增任何文件；
- 探针的 sessions_dir 一律指向临时目录；chroma 只读查询，写入一律 tmp_path；
- 仓库根 memory/、sessions/ 跑前跑后均确认不存在；
- 护栏 10 文件 sha256 内容散列全部一致（§3），叠加 127 例单跑与 738 例干净全量全绿，证明 t15/t17 未破坏任何护栏。

---

## 9. 结论

| 项 | t12 状态 | 本轮状态 |
|---|---|---|
| N1 Learn 工具表整表禁用 | blocker，未闭环 | **闭环**（learn 可见 == LEARN_ALLOWED 8 个，端到端三工具真实执行，防回归口径 work=19/code=16/chat=0） |
| F1b 审批错误码 | medium，未闭环 | **闭环**（三路径文本互异、均含结构化码、deny 非裸码、AuditEntry.code 语义不变、三路径不落盘） |
| F4b todo list 说明误判 | low~medium，未闭环 | **闭环**（反例 False、正判形态 True、超长逐字保留 4/4） |
| 前期闭环 | 已闭环 | **零回归** |

全量 738 passed / 1 skipped 与 captain 实测基线一致。验收全部通过。

---

# 四模式内核设计评审（t19 · review round 1，承接 t9）

> 评审者：verify2（独立验证工程师，未参与 t3/t15/t17 实现）
> 评审对象：t3 四模式会话内核（castorice/modes.py、session.py、harness.py + ai.py 薄委托），以当前代码状态为准（含 t11/t13/t14/t15/t17 修复后）
> **结论：verdict = pass（1 个 low finding，不阻塞下游）。**

## 1. 逐条核对 t3 acceptance 在最新代码上的实际状态

| t3 验收条款 | 最新代码实测 | 结果 |
|---|---|---|
| to_openai_specs(CHAT) == [] | modes.py:391-392 显式短路返回 []；实测 True | 通过 |
| 请求 kwargs 不含 tools 键（非 == []） | harness._build_request_kwargs（唯一收口）→ chat 实测 keys = ['messages','stream']；chat_completion_kwargs 对预设 tools 键也会 pop（tests/test_modes.py:332） | 通过 |
| Chat system prompt 不含 get_current_time 字样 | **实测含**——TIME_INSTRUCTIONS 第 3 条「也可以调用 get_current_time 工具」无条件拼接（ai.py:1728, 1638），chat 真实请求的 system 消息含该字样。「工具使用说明」段确实不注入（tests/test_modes.py:975 锚定） | **偏差（finding R19-1, low）** |
| 异常/重试路径同样不带 tools | adapter.complete 重试循环每轮都走 prepare_request 重建 kwargs（adapter.py:390-443），native+空 specs / json / text 协议分支全部 pop tools（168-178 行）；实测 3 协议 x {[], None, 非空} 9 种组合矩阵全部符合预期；chat 下模型幻觉工具调用被 _normalize_calls 可见性过滤（harness.py:716-718），实测不产生 tool 事件 | 通过 |

## 2. 重点核查项

### 2.1 Chat 零工具暴露（含异常与重试路径）—— 通过

- 请求构造唯一出口：harness.py:679-694 _build_request_kwargs → modes.py:395-410 chat_completion_kwargs（无可见工具时 kwargs.pop("tools")）；harness:692 注释与 last_request_kwargs 记录支持直接断言；
- provider 层防御：prepare_request 对「native 且 specs 非空」之外的一切分支 pop tools（adapter.py:168-178）；重试/降级循环（attempts>1）每轮重建，不存在第二组装点；关键字透传（harness.py:705）保证「请求体 == provider 实收参数」；
- 幻觉调用防御：chat 下 provider 返回工具调用时被 _normalize_calls 的可见集合过滤（harness.py:716-718），实测事件流仅 ['done']；
- 既有测试锚定：test_modes.py:325/332/344/946/1044/1126 + test_providers.py:158-351。

### 2.2 模式工具可见性策略清晰 —— 通过（t15 修复 N1 后）

- learn 可见集合 == LEARN_ALLOWED（8 个，集合相等）；work=19 / code=16 / chat=0 与冻结基线一致；code 不含 3 个笔记工具；
- learn 的 tool_policy.allow 物化为 8 项、deny 为空（t12 记录的 deny-ALL 已消失）；
- 构造期校验完备：未注册工具名报错、allow=frozenset() 报错、chat 带工具报错（modes.py:316-333）；ai.py:1395 有「工具名与模式名不重名」断言。

### 2.3 工作目录边界可靠 —— 通过

- normalize_path：expanduser → 相对路径拼 cwd → resolve(strict=False)（消 . / .. 与符号链接）→ normpath；path_within：normcase（Windows 大小写不敏感）+ 分隔符边界比较（防 C:/work2 误判在 C:/work 内）；
- 实测：相对路径界内通过；「../evil.txt」逃逸 → FS_SANDBOX_DENIED；NTFS junction 指向工作区外 → resolve 消解后 FS_SANDBOX_DENIED；大小写变体 → 放行且落点正确；
- 路径边界先于审批：gateway.evaluate 先 pre_execute（可见性/路径/命令白名单）后审批分发（permissions.py:1156-1185）；实测越界 write_note 的审批应答者 0 调用、permission_check 返回 deny；
- bind_workdir 越界抛 WorkdirError（实测绑定 C:\Windows 被拒，to_dict 给 code/message/path/root）。

### 2.4 与既有 AI/AIWorker 事件协议兼容 —— 通过

- 事件流实测：无工具轮 kinds=['text','done']；工具轮 kinds=['tool','tool','text','done']；元组形状 (str, payload) 长度恒 2；done 恰好一次且最后；
- t2 护栏 tests/test_offline_guarantees.py + tests/test_backward_compat.py 38 passed；全量 738 passed / 1 skipped（本次评审实测，含 t15/t17 修复后）；
- ui.py 零改动（AIWorker 兼容由 t2 回归网锚定）。

### 2.5 可扩展性 —— 通过（含一条注意）

- 注册新工具（tool_registry.register 声明 modes）+ 重建 _mode_registry（ai._build_mode_registry）后：work/code 立即可见（deny 表模式），chat 恒不可见；
- 注意：**learn 是 LEARN_ALLOWED 白名单收口**，新工具声明 modes=('learn',) 不会自动可见，必须同步扩充 modes.py:550 的 LEARN_ALLOWED 常量。这是「Learn 面向笔记的最小面」设计使然（与 N1 修复语义一致），不算缺陷，但属于扩展时需要知晓的收口点。

## 3. Findings

### R19-1 [low] Chat 模式 system prompt 含「get_current_time」字样

- **现象**：TIME_INSTRUCTIONS 第 3 条「需要更精确的时间时也可以调用 get_current_time 工具」在所有模式（含 chat）无条件拼入 system prompt（ai.py:1728 + 1632-1639）；chat 真实请求的 system 消息实测含该字样。
- **对照**：01-gap-matrix.md:54-55 的第一期落点明文「Chat 模式不追加，也不追加 TIME_INSTRUCTIONS 的第 3 条」；t3 验收条款明文「断言 Chat 模式的 system prompt 不含 get_current_time、web_search 字样」。
- **影响**：low。模型可见性层面无工具暴露（请求体无 tools 键、幻觉调用被过滤、工具说明段不注入），模型即使尝试调用也会被网关按 TOOL_NOT_VISIBLE 拒绝；属「承诺与能力脱节」的提示词残留，安全边界未破。
- **为何既有测试未捕获**：tests/test_modes.py:975 只断言「工具使用说明」段不注入，未断言 TIME_INSTRUCTIONS。
- **requiredFix**：把 TIME_INSTRUCTIONS 第 3 条改为按模式条件拼接（有 get_current_time 可见时才拼），或在 _build_system_prompt 中对 chat 分流；并补一条「chat system prompt 不含 get_current_time」的回归断言。

## 4. 评审依据

- 代码：castorice/modes.py / harness.py / permissions.py / providers/adapter.py、ai.py 相关段（本轮只读，未改动）；
- 动态实测：真实 ai.AI 装配路径（临时 cwd + 独立 sessions_dir 分离条件），共 10 组探针；
- 测试：全量 738 passed / 1 skipped；护栏 38 passed；
- 文档：docs/refactor/01-gap-matrix.md（t3 验收原文）、03-verification-report.md（t12/t18 各轮结论）。

