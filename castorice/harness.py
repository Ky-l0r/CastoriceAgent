"""agent 主循环 / 工具派发 / 终止原因 / 检查点恢复入口。

本模块是 DeepSeek Harness 的 dsh-agent-loop 在 Python 侧的落点。逐项对应关系
（docs/refactor/04-dsh-harness-notes.md 的可移植清单编号）：

============================  ==============================================
DSH 机制                       本模块落点
============================  ==============================================
P1 / 1.2 工具可见性裁决        每次请求都经 ModeRegistry.chat_completion_kwargs
                              构造 kwargs；没有可见工具时**不存在 tools 键**
P2 构造期校验                  ModeRegistry.__init__（modes.py）
P9 单调 guard                  PermissionBridge.guard / _gate（顺序执行，首次拒绝即返回）
P10 拒绝以 Error: <reason>     _deny_result / _permission_error_text
P11 三处语义检查点             "pre-step" / "pre-request" / "pre-tool"
P12 flush fail-closed          _checkpoint 抛 CheckpointFailed → 工具不执行
P15 终止原因显式               StopReason + TurnOutcome.stop_reason
P16 独占屏障 + 有界并行池      _dispatch_tools（结果按模型给出的顺序写回）
P19 策略变化以追加消息告知     _sync_mode_context 追加带来源的 context 事件
红线 5.1 事件协议不变          run_stream 产出 ('text'|'tool'|'emoji'|'done')
R7 同步生成器 + Event 取消位   run_stream / cancel
R8 每次尝试 start / 终态 end   ('tool', {'name':…, 'status':'start'|'done'|'error'})
============================  ==============================================

不适用项：R9（Typert RPC，PySide6 单进程用 QThread + Signal 替代）、R12（PTC
的 run_code 呈现模式，本项目的工具规模直接采用 native 语义）。

设计约束（红线 D）：本模块只依赖标准库，不得 import ui / PySide6 / ai。
"""

from __future__ import annotations

import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    Iterator,
    List,
    Mapping,
    Optional,
    Sequence,
    Set,
    Tuple,
)

from .modes import ModeRegistry
from .session import (
    CheckpointFailed,
    KIND_ASSISTANT,
    KIND_CONTEXT,
    KIND_MODE,
    KIND_SANDBOX,
    KIND_STOP,
    KIND_TOOL_RESULT,
    KIND_USER,
    SessionStore,
    TOOL_OUTCOME_UNKNOWN,
    TOOL_OUTCOME_UNKNOWN_TEXT,
)

#: 工具结果默认长度上限（P20 的有界结果；t4 会按工具覆盖）
DEFAULT_RESULT_LIMIT = 8000

#: 触顶时给用户的显式提示（沿用既有文案，但对齐 AI.MAX_ITERATIONS_NOTE）
MAX_ITERATIONS_NOTE = "（已达到处理步数上限，以上是当前进展。）"


# ----------------------------------------------------------------------------
# 终止原因
# ----------------------------------------------------------------------------

class StopReason(str, Enum):
    """一轮 agent 循环的显式终止原因（对齐 DSH 的 turn/end cause）。

    继承 str 便于直接落进 JSON 事件与投影快照。
    """

    FINAL_ANSWER = "final_answer"          # 模型不再调用工具
    MAX_ITERATIONS = "max_iterations"      # 达到步数上限
    CANCELLED = "cancelled"                # 用户中止
    ERROR = "error"                        # 内部失败
    CHECKPOINT_FAILED = "checkpoint_failed"  # 检查点失败（fail-closed）
    PREEMPTED = "preempted"                # 被新的一轮抢占（单机助手不会并行跑两轮）

    def __str__(self) -> str:
        return self.value


# ----------------------------------------------------------------------------
# 数据结构
# ----------------------------------------------------------------------------

def new_call_id() -> str:
    return "call_" + datetime.now().strftime("%H%M%S%f")


@dataclass
class ToolCall:
    """规范化的工具调用（从 provider 返回的 OpenAI 格式解析而来）。"""

    id: str
    name: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    raw_arguments: str = "{}"
    parse_error: Optional[str] = None

    @classmethod
    def from_openai(cls, raw: Mapping[str, Any]) -> "ToolCall":
        call_id = str(raw.get("id") or new_call_id())
        function = raw.get("function") if isinstance(raw, Mapping) else None
        if not isinstance(function, Mapping):
            function = {}
        name = str(function.get("name") or raw.get("name") or "")
        arguments_text = function.get("arguments", raw.get("arguments", "{}"))
        parse_error: Optional[str] = None
        parsed: Dict[str, Any] = {}
        if isinstance(arguments_text, Mapping):
            parsed = dict(arguments_text)
        else:
            text = str(arguments_text or "").strip() or "{}"
            try:
                loaded = json.loads(text)
                parsed = dict(loaded) if isinstance(loaded, Mapping) else {"value": loaded}
            except (ValueError, TypeError) as exc:
                parse_error = str(exc)
        return cls(id=call_id, name=name, arguments=parsed,
                   raw_arguments=str(arguments_text), parse_error=parse_error)

    def to_openai(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": self.raw_arguments},
        }


@dataclass(frozen=True)
class StepRecord:
    """一个步骤的记录（对齐 DSH 的 step 级状态投影）。"""

    index: int
    text: str = ""
    tool_calls: Tuple[Dict[str, Any], ...] = ()
    stop_reason: Optional[StopReason] = None


@dataclass(frozen=True)
class TurnOutcome:
    """一轮的最终结果。stop_reason 必须显式，绝不静默截断（P15）。"""

    stop_reason: StopReason
    text: str = ""
    steps: Tuple[StepRecord, ...] = ()
    error: str = ""
    iterations: int = 0
    cancelled: bool = False

    @property
    def used_tools(self) -> Tuple[str, ...]:
        names: List[str] = []
        for step in self.steps:
            for call in step.tool_calls:
                name = str(call.get("name") or "")
                if name and name not in names:
                    names.append(name)
        return tuple(names)


@dataclass(frozen=True)
class PermissionRequest:
    """权限判定请求（对齐 dsh-tools 的 tools/pre-execute 入参）。

    携带 target_paths 是本项目的主动增强（DSH 侧的沙箱策略自己解析参数），
    让路径边界判定不必依赖 t4 的工具实现细节。
    """

    tool: str
    arguments: Mapping[str, Any] = field(default_factory=dict)
    mode: str = "chat"
    call_id: str = ""
    reason: str = ""
    target_paths: Tuple[str, ...] = ()
    cwd: Optional[str] = None
    parallel_safe: bool = True


@dataclass(frozen=True)
class PermissionDecision:
    """权限判定结果。decision ∈ {allow, deny, ask, unavailable}。"""

    decision: str = "allow"
    reason: str = ""
    info: Optional[Dict[str, Any]] = None

    @property
    def allowed(self) -> bool:
        return self.decision == "allow"

    @property
    def asks(self) -> bool:
        return self.decision == "ask"


# ----------------------------------------------------------------------------
# 权限桥（t4 会是真正的 PermissionGateway；这里提供 fail-closed 的默认实现）
# ----------------------------------------------------------------------------

class PermissionBridge:
    """权限判定的最小接口 + 单调 guard 链。

    * decide(req) 交给实际的网关（t4）或安全默认值（本类）；
    * 默认策略是**显式白名单**：只有构造函数传入的 allow 集合通过，
      其余一律 deny。这样「未接入权限网关」时的行为是安全关闭的，
      而不是静默放行（红线 F 的同源要求）。
    * guard 顺序执行，**首次拒绝即返回**；后续 guard 或 decide 都无法把它翻回
      允许（对齐 dsh-tools 的单调 guard）。
    """

    def __init__(
        self,
        decide: Optional[Callable[[PermissionRequest], Any]] = None,
        *,
        allow: Optional[Iterable[str]] = None,
        unavailable_reason: str = "未接入权限网关（t4），按拒绝关闭",
    ) -> None:
        self._decide = decide
        self._allow: Optional[Set[str]] = None if allow is None else set(allow)
        self._unavailable_reason = unavailable_reason
        self._guards: List[Callable[[PermissionRequest], Optional[str]]] = []

    # ---- 单调 guard ----

    def guard(self, fn: Callable[[PermissionRequest], Optional[str]]) -> None:
        self._guards.append(fn)

    def guards(self) -> Tuple[Callable[[PermissionRequest], Optional[str]], ...]:
        return tuple(self._guards)

    # ---- 判定 ----

    def evaluate(self, req: PermissionRequest) -> PermissionDecision:
        for guard in self.guards():
            try:
                reason = guard(req)
            except Exception as exc:
                # guard 抛异常 → 按拒绝关闭（对齐 DSH 的 listener threw → warn）
                return PermissionDecision("deny", "权限守卫异常，按拒绝关闭: " + str(exc))
            if reason:
                return PermissionDecision("deny", str(reason))
        if self._decide is None:
            if self._allow is None:
                return PermissionDecision("deny", self._unavailable_reason)
            if req.tool in self._allow:
                return PermissionDecision("allow", "")
            return PermissionDecision("deny", "工具 " + req.tool + " 不在允许列表中")
        return _normalize_decision(self._decide(req))


def _normalize_decision(value: Any) -> PermissionDecision:
    """把网关返回值规范化：无法识别的一律按拒绝关闭（fail-closed）。"""
    if isinstance(value, PermissionDecision):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("allow", "deny", "ask", "unavailable"):
            return PermissionDecision(lowered)
    if isinstance(value, Mapping):
        decision = str(value.get("decision", "") or "").strip().lower()
        if decision in ("allow", "deny", "ask", "unavailable"):
            return PermissionDecision(
                decision,
                str(value.get("reason", "") or ""),
                dict(value.get("info") or {}) or None,
            )
    return PermissionDecision(
        "unavailable", "权限网关返回了无法识别的结果，按拒绝关闭: " + repr(value))


# ----------------------------------------------------------------------------
# 重复调用提醒（P21）
# ----------------------------------------------------------------------------

class RepeatReminder:
    """同一工具 + 同一参数重复调用且无进展时的温和提醒（第 3/5/8 次）。

    只追加提示文本，**不阻塞**合理的重复调用（对齐 dsh-repeat-tool-reminder）。
    """

    DEFAULT_THRESHOLDS: Tuple[int, ...] = (3, 5, 8)

    def __init__(self, thresholds: Sequence[int] = DEFAULT_THRESHOLDS) -> None:
        self.thresholds: Tuple[int, ...] = tuple(sorted(int(t) for t in thresholds))
        self._counts: Dict[str, int] = {}
        self._fingerprints: Set[str] = set()

    @staticmethod
    def fingerprint(name: str, arguments: Any) -> str:
        try:
            payload = json.dumps(arguments, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            payload = repr(arguments)
        return name + "\u0000" + payload

    def reset(self) -> None:
        self._counts = {}
        self._fingerprints = set()

    def note(self, name: str, arguments: Any) -> Optional[str]:
        """记录一次调用；命中阈值时返回提示文本，否则返回 None。"""
        key = self.fingerprint(name, arguments)
        self._counts[key] = self._counts.get(key, 0) + 1
        count = self._counts[key]
        if count in self.thresholds:
            return (
                "（提示：你已经用完全相同的参数调用过 " + name + " "
                + str(count) + " 次且结果没有变化，"
                "如果信息已经足够，请直接基于现有结果作答。）"
            )
        return None


# ----------------------------------------------------------------------------
# 主循环
# ----------------------------------------------------------------------------

class HarnessLoop:
    """agent 主循环：组装 → 检查点 → 请求 → 派发工具 → 续轮。"""

    def __init__(
        self,
        session: SessionStore,
        provider: Any,
        modes: ModeRegistry,
        tools: Any = None,
        permissions: Any = None,
        *,
        max_iterations: int = 8,
        max_parallel_tool_calls: int = 4,
        reminder_thresholds: Sequence[int] = RepeatReminder.DEFAULT_THRESHOLDS,
        model: Optional[str] = None,
        logger: Any = None,
    ) -> None:
        self.session = session
        self.provider = provider
        self.modes = modes if isinstance(modes, ModeRegistry) else ModeRegistry(tools=tools)
        self.tools = tools if tools is not None else self.modes
        self.permissions = permissions if permissions is not None else PermissionBridge()
        self.max_iterations = max(1, int(max_iterations))
        self.max_parallel_tool_calls = max(1, int(max_parallel_tool_calls))
        self.reminder = RepeatReminder(reminder_thresholds)
        self.model = model
        self.logger = logger

        # 本轮状态（可检查）
        self.steps: List[StepRecord] = []
        self.stop_reason: Optional[StopReason] = None
        self.last_error: str = ""
        self.last_reminder: str = ""
        #: 最近一次实际发出的完整请求体（含 messages；Chat 时不含 tools 键）
        self.last_request_kwargs: Optional[Dict[str, Any]] = None

        self._cancel = threading.Event()
        self._lock = threading.RLock()
        self._running = False
        self._mode_name: str = session.mode
        self._announced: Dict[str, str] = {}
        self._history: List[Dict[str, Any]] = []
        self._system_prompt: str = ""
        self._turn_index = 0
        self.projection = None  # 由 ai.py（或 t10 的组合根）注入 SessionProjection

    # ---- 会话/模式 ----

    @property
    def mode_name(self) -> str:
        return self._mode_name

    @property
    def mode(self) -> Any:
        return self.modes.get(self._mode_name)

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    def set_mode(self, name: str) -> None:
        """切换模式。会话历史（AgentSession）由调用方隔离，见 ai.AI.set_mode。"""
        self._mode_name = self.modes.get(name).name
        self.session.mode = self._mode_name

    def workdir(self) -> Optional[str]:
        return self.modes.workdir(self.mode)

    # ---- 取消（协作式，R7） ----

    def cancel(self) -> None:
        """置位取消位；当前活动在下一个检查点退出，**已流出的文本保留**。"""
        self._cancel.set()

    def reset_cancel(self) -> None:
        self._cancel.clear()

    @property
    def cancel_event(self) -> threading.Event:
        return self._cancel

    # ---- 状态检查 ----

    def state(self) -> Dict[str, Any]:
        """当前逐轮状态快照（供 t7 的面板 / 测试读取）。"""
        return {
            "mode": self._mode_name,
            "running": self.running,
            "stop_reason": (self.stop_reason.value if self.stop_reason else None),
            "steps": len(self.steps),
            "last_error": self.last_error,
            "cancelled": self._cancel.is_set(),
            "revision": self.session.revision,
            "workdir": self.workdir(),
            "iteration_limit": self.max_iterations,
            "last_request_keys": sorted(self.last_request_kwargs or {}),
        }

    # ---- 初始历史注入 ----

    def seed_history(self, messages: Sequence[Mapping[str, Any]]) -> None:
        """注入既有历史（ai.py 首次委托时把 AI._messages 交进来）。"""
        self._history = [copy.deepcopy(dict(m)) for m in messages]

    def history(self) -> List[Dict[str, Any]]:
        return self._history

    def set_system_prompt(self, prompt: str) -> None:
        self._system_prompt = prompt or ""

    def announced_reset(self) -> None:
        """清掉「已告知模型」的指纹，让下一步重新追加一条策略上下文消息（P19）。

        模式切换 / 工作目录变化后由调用方调用；不改写系统提示词本身。
        """
        self._announced.pop("mode", None)

    def reset_history(self, system_prompt: Optional[str] = None) -> None:
        self._history = []
        self._announced = {}
        if system_prompt is not None:
            self._system_prompt = system_prompt

    # ---- 主循环 ----

    def run_stream(
        self,
        user_message: Any,
        images: Optional[Sequence[str]] = None,
        *,
        source: str = "user",
        display_text: Optional[str] = None,
    ) -> Iterator[Tuple[str, Any]]:
        """产出与旧实现完全一致的 ('text'|'tool'|'emoji'|'done') 事件元组。

        事件协议（红线 5.1）：
            ('text',  str)
            ('tool',  {'name': str, 'status': 'start'|'done'|'error', ...})
            ('emoji', {'name': str, 'path': str})
            ('done',  str)   —— 恰好一次，且在最末尾

        user_message 可以是字符串，也可以是已经组装好的 OpenAI user 消息
        （dict）——后者让 ai.py 保留「【当前时间】/【相关记忆】前缀拼装」的
        既有位置与语义。

        display_text：**仅用于界面展示**的用户原文。模型仍然看到完整
        user_message（含时间/记忆前缀），但事件日志里会额外记一份干净的
        文本，历史回放时据此渲染气泡——否则用户会在聊天记录里看到
        【当前时间】【相关记忆】这些本不该给终端用户看的注入内容。
        省略时行为与旧版完全一致（展示原文 = 完整文本）。
        """
        with self._lock:
            preempted = self._running
            self._running = True
        if preempted:
            # 单机助手不并行跑两轮：旧轮在下一个检查点自行退出
            self.last_error = "新的一轮已开始，上一轮将被抢占"

        self._cancel.clear()
        self.reminder.reset()
        self.steps = []
        self.stop_reason = None
        self.last_error = ""
        self.last_reminder = ""
        self._turn_index += 1
        if self.projection is not None:
            try:
                self.projection.reset_turn()
            except Exception:  # pragma: no cover - 投影异常不阻断内核
                pass

        full_text = ""
        if source == "proactive":
            self.session.append(KIND_USER, {"content": str(user_message or ""),
                                            "source": "proactive"})

        try:
            for kind, payload in self._run_steps(
                    user_message, images, source, display_text):
                if kind == "text":
                    full_text += str(payload)
                elif kind == "done":
                    full_text = str(payload)
                yield (kind, payload)
        finally:
            with self._lock:
                self._running = False

    # ---- 单轮实现 ----

    def _run_steps(
        self,
        user_message: Any,
        images: Optional[Sequence[str]],
        source: str,
        display_text: Optional[str] = None,
    ) -> Iterator[Tuple[str, Any]]:
        steps: List[StepRecord] = []
        full_reply = ""
        stop_reason: Optional[StopReason] = None

        try:
            # 1. 组装消息（历史 + 本次 user 消息）
            self._prepare_history(user_message, display_text)
            self._announce_mode_policy()
        except Exception as exc:
            stop_reason = StopReason.ERROR
            self.last_error = str(exc)
            yield ("text", "处理请求时出错: " + str(exc))
            yield ("done", "")
            self._finish_turn(stop_reason, "", steps)
            return

        iteration = 0
        while True:
            if iteration >= self.max_iterations:
                # P15：显式终止原因，而非 for…else 的静默截断
                stop_reason = StopReason.MAX_ITERATIONS
                note = "\n\n" + MAX_ITERATIONS_NOTE
                full_reply += note
                steps.append(StepRecord(index=iteration, text=note,
                                        stop_reason=StopReason.MAX_ITERATIONS))
                yield ("text", note)
                break

            if self._cancel.is_set():
                stop_reason = StopReason.CANCELLED
                steps.append(StepRecord(index=iteration, text="",
                                        stop_reason=StopReason.CANCELLED))
                break

            step_index = iteration
            iteration += 1

            # a. 步骤前检查点
            if not self._checkpoint("pre-step"):
                stop_reason = StopReason.CHECKPOINT_FAILED
                message = _checkpoint_message(self.last_error)
                full_reply += message
                yield ("text", message)
                break

            # b. 构造请求体：可见性在构造之前求值（Chat 时不存在 tools 键）
            kwargs = self._build_request_kwargs()

            # c. 请求前检查点
            if not self._checkpoint("pre-request"):
                stop_reason = StopReason.CHECKPOINT_FAILED
                message = _checkpoint_message(self.last_error)
                full_reply += message
                yield ("text", message)
                break

            # d. 发起请求（provider 异常不抛出流，转成错误事件）
            try:
                text, raw_calls = self._call_provider(kwargs)
            except Exception as exc:
                stop_reason = StopReason.ERROR
                self.last_error = str(exc)
                message = "抱歉，处理您的请求时出现了错误：" + str(exc)
                steps.append(StepRecord(index=step_index, text=message,
                                        stop_reason=StopReason.ERROR))
                yield ("text", message)
                full_reply += message
                break

            text = text or ""
            if text:
                full_reply += text
                yield ("text", text)

            calls = self._normalize_calls(raw_calls)

            # 记录 assistant 消息：**无论有没有工具调用都要记**。
            # 会话日志是派生历史与崩溃恢复的唯一来源（对齐 dsh-session 的
            # 「从日志派生模型历史」），漏记纯文本回复会让恢复出来的会话
            # 丢掉所有助手发言。
            openai_calls = [call.to_openai() for call in calls]
            content_value: Any = (text or None) if openai_calls else text
            record: Dict[str, Any] = {"content": content_value}
            message: Dict[str, Any] = {"role": "assistant", "content": content_value}
            if openai_calls:
                record["tool_calls"] = openai_calls
                message["tool_calls"] = openai_calls
            self.session.append(KIND_ASSISTANT, record)
            self._history.append(message)

            # 模型不再调用工具 → 正常终止
            if not calls:
                steps.append(StepRecord(index=step_index, text=text, stop_reason=None))
                stop_reason = StopReason.FINAL_ANSWER
                break

            # e. 派发工具（独占屏障 + 有界并行池，结果按模型顺序写回）
            step_calls: List[Dict[str, Any]] = []
            for name, payload in self._dispatch_tools(calls):
                if name is not None:
                    step_calls.append({"name": name, "status": payload.get("status")})
                yield ("tool", payload)

            # f. 结果补全（P14：配对完整且不自动重试）
            self._complete_missing_results(calls)

            steps.append(StepRecord(
                index=step_index,
                text=text,
                tool_calls=tuple(step_calls),
                stop_reason=None,
            ))

            # g. 取消：保留已交付文本，就地结束
            if self._cancel.is_set():
                stop_reason = StopReason.CANCELLED
                steps[-1] = StepRecord(index=step_index, text=text,
                                       tool_calls=tuple(step_calls),
                                       stop_reason=StopReason.CANCELLED)
                break

        if stop_reason is None:  # pragma: no cover - 防御性
            stop_reason = StopReason.FINAL_ANSWER

        yield ("done", full_reply)
        self._finish_turn(stop_reason, full_reply, steps)

    # ---- 消息组装 ----

    def _prepare_history(
        self, user_message: Any, display_text: Optional[str] = None
    ) -> None:
        if self._system_prompt and not any(
            m.get("role") == "system" for m in self._history
        ):
            self._history.insert(0, {"role": "system", "content": self._system_prompt})
        if isinstance(user_message, Mapping):
            self._history.append(copy.deepcopy(dict(user_message)))
            full = _content_text(user_message)
        else:
            full = "" if user_message is None else str(user_message)
            self._history.append({"role": "user", "content": full})

        # 事件日志同时记两份：content 是模型看到的完整文本（derive_messages
        # 依赖它重建上下文），display 是给用户看的原文（界面渲染用它）。
        # 只多存一个字段，不改变既有投影/恢复语义。
        payload: Dict[str, Any] = {"content": full}
        if display_text is not None and display_text != full:
            payload["display"] = str(display_text)
        self.session.append(KIND_USER, payload)

    def _announce_mode_policy(self) -> None:
        """P19：策略变化以**追加消息**告知模型，不改写系统提示词。"""
        mode = self.mode
        sandbox = mode.workdir_policy.default_sandbox
        workdir = self.workdir() or "（未绑定）"
        text = "[系统] 当前模式：" + mode.display_name + "（" + mode.name + "）；工作目录：" + workdir
        fingerprint = mode.name + "|" + str(workdir) + "|" + sandbox
        if self._announced.get("mode") == fingerprint:
            return
        self._history.append({"role": "user", "content": text})
        self.session.append(KIND_CONTEXT, {"text": text, "source": "runtime"})
        self.session.append(KIND_MODE, {"mode": mode.name, "display_name": mode.display_name})
        self.session.append(KIND_SANDBOX, {"sandbox": sandbox,
                                           "workdir": self.workdir()})
        self._announced["mode"] = fingerprint

    # ---- 请求体构造（可见性收口点） ----

    def _build_request_kwargs(self) -> Dict[str, Any]:
        """构造 provider.chat_completion 的完整请求体。

        **唯一允许出现 tools 键的地方**：由 ModeRegistry 裁决——
        Chat 模式（或任何没有可见工具的模式）返回的 dict **不含 tools 键**
        （既不是 tools=[]，也不是 tools=None）。

        请求体整体记录在 last_request_kwargs 上，供 UI 面板与检查任务直接断言
        「Chat 的请求体里没有 tools 键」。
        """
        base: Dict[str, Any] = {"messages": self._history, "stream": True}
        if self.model:
            base["model"] = self.model
        kwargs = self.modes.chat_completion_kwargs(self._mode_name, base)
        self.last_request_kwargs = dict(kwargs)
        return kwargs

    def _call_provider(self, kwargs: Mapping[str, Any]) -> Tuple[str, Any]:
        """按关键字把整个请求体交给 provider（含 messages）。

        关键字调用保证「请求体 == provider 实际收到的参数」，
        可见性裁决与真实请求之间不存在第二处组装点。
        """
        provider = self.provider
        if provider is None:
            raise RuntimeError("未配置 LLM provider")
        return provider.chat_completion(**dict(kwargs))

    def _normalize_calls(self, raw_calls: Any) -> List[ToolCall]:
        if not raw_calls:
            return []
        calls: List[ToolCall] = []
        for raw in raw_calls:
            if isinstance(raw, ToolCall):
                calls.append(raw)
            elif isinstance(raw, Mapping):
                calls.append(ToolCall.from_openai(raw))
        # 过滤掉不可见的工具名（防御：provider 幻觉出的名字不得绕过策略）
        visible = set(self.modes.visible_tool_names(self._mode_name))
        return [c for c in calls if c.name in visible]

    # ---- 检查点（fail-closed） ----

    def _checkpoint(self, reason: str) -> bool:
        """执行语义检查点；失败时记录错误并返回 False。

        返回 False 意味着**调用方绝不能继续执行下游副作用**（P12）。
        """
        try:
            self.session.checkpoint(reason)
        except CheckpointFailed as exc:
            self.last_error = str(exc)
            return False
        except Exception as exc:
            self.last_error = "检查点失败: " + str(exc)
            return False
        return True

    # ---- 工具派发 ----

    def _dispatch_tools(
        self, calls: Sequence[ToolCall]
    ) -> Iterator[Tuple[Optional[str], Dict[str, Any]]]:
        """独占屏障 + 有界并行池；**结果按模型给出的调用顺序写回**（P16）。

        产出 ('tool', payload) 形式的 UI 事件（start / done / error）。
        """
        index = 0
        total = len(calls)
        while index < total:
            call = calls[index]
            if self._cancel.is_set():
                # 取消后未分发的调用：合成 ABORTED_BEFORE_DISPATCH 结果，
                # 让后续请求拿到配对完整的工具历史（对齐 DSH）
                for pending in calls[index:]:
                    self._append_tool_result(pending, "错误: 调用在派发前被取消")
                break

            if not self._is_parallel_safe(call):
                # 独占调用：单独运行并构成排序屏障
                for event in self._execute_one(call, index, total):
                    yield event
                index += 1
                continue

            batch: List[ToolCall] = [call]
            cursor = index + 1
            while (cursor < total and len(batch) < self.max_parallel_tool_calls
                   and self._is_parallel_safe(calls[cursor])):
                batch.append(calls[cursor])
                cursor += 1
            for event in self._execute_batch(batch, index, total):
                yield event
            index = cursor

    def _execute_batch(
        self, batch: Sequence[ToolCall], start_index: int, total: int
    ) -> Iterator[Tuple[Optional[str], Dict[str, Any]]]:
        results: Dict[int, Tuple[str, Dict[str, Any], bool]] = {}

        def _run(position: int, call: ToolCall) -> Tuple[int, str, Dict[str, Any], bool]:
            result, payload, failed = self._invoke(call)
            return position, result, payload, failed

        for call in batch:
            yield (call.name, {"name": call.name, "status": "start",
                               "call_id": call.id, "index": start_index,
                               "total": total})
        if len(batch) == 1:
            _pos, result, payload, failed = _run(0, batch[0])
            results[0] = (result, payload, failed)
        else:
            with ThreadPoolExecutor(max_workers=self.max_parallel_tool_calls) as pool:
                futures = [pool.submit(_run, i, c) for i, c in enumerate(batch)]
                for future in futures:
                    position, result, payload, failed = future.result()
                    results[position] = (result, payload, failed)

        # 结果按模型给出的顺序写回（不是完成顺序）
        for position, call in enumerate(batch):
            result, payload, _failed = results[position]
            self._append_tool_result(call, result)
            yield (call.name, payload)

    def _execute_one(
        self, call: ToolCall, index: int, total: int
    ) -> Iterator[Tuple[Optional[str], Dict[str, Any]]]:
        yield (call.name, {"name": call.name, "status": "start",
                           "call_id": call.id, "index": index, "total": total})
        result, payload, _failed = self._invoke(call)
        self._append_tool_result(call, result)
        yield (call.name, payload)

    def _is_parallel_safe(self, call: ToolCall) -> bool:
        for spec in getattr(self.tools, "all_tools", lambda: [])():
            if getattr(spec, "name", None) == call.name:
                return bool(getattr(spec, "parallel_safe", True))
        return True

    def _result_limit(self, name: str) -> int:
        for spec in getattr(self.tools, "all_tools", lambda: [])():
            if getattr(spec, "name", None) == name:
                return int(getattr(spec, "result_limit", DEFAULT_RESULT_LIMIT))
        return DEFAULT_RESULT_LIMIT

    # ---- 单次工具执行：权限判定 → 检查点 → 执行 ----

    def _invoke(self, call: ToolCall) -> Tuple[str, Dict[str, Any], bool]:
        """执行一次工具调用，返回 (结果文本, UI 事件 payload, 是否失败)。

        顺序严格对齐 dsh-tools 的流水线：
        1. 解析参数（失败 → Error 结果，不抛异常）；
        2. permissions.evaluate（拒绝 → Error: <reason>，不执行）；
        3. 检查点（flush 失败 → 不执行，fail-closed）；
        4. 真正调用工具；
        5. 结果有界化 + 重复调用提醒。
        """
        if call.parse_error:
            text = "错误: 解析工具 " + call.name + " 的参数失败: " + call.parse_error
            return text, {"name": call.name, "status": "error",
                          "call_id": call.id, "reason": "BAD_ARGUMENTS"}, True

        # 1) 权限判定（有副作用的调用先过判定）
        decision = self._evaluate_permission(call)
        if not decision.allowed:
            # F1b（t12/t15）：模型可见文本必须带上**结构化错误码**。
            # info.code 由 PermissionGateway._deny/_unavailable 写入
            # （APPROVAL_POLICY_NEVER / APPROVAL_NO_RESPONDER /
            # APPROVAL_TIMEOUT / TOOL_NOT_VISIBLE / FS_SANDBOX_DENIED 等），
            # 语义在审计 AuditEntry.code 里保持不变（审计只进日志）；
            # 这里只是把它**同时**呈现在模型可见文本里，让模型能区分
            # 「用户拒绝」「超时」「无应答者」。网关没给码时回落通用
            # PERMISSION_DENIED，永远不出现裸 'deny'。
            reason = decision.reason or decision.decision
            code = _denial_code(decision)
            text = "Error: " + reason + "（" + code + "）"
            return text, {"name": call.name, "status": "error",
                          "call_id": call.id, "decision": decision.decision,
                          "reason": reason, "code": code}, True

        # 2) 顶层工具的外部副作用前检查点（fail-closed）
        try:
            self.session.checkpoint("pre-tool")
        except Exception as exc:
            self.last_error = "检查点失败: " + str(exc)
            text = "Error: 检查点失败，工具未执行（" + str(exc) + "）"
            return text, {"name": call.name, "status": "error",
                          "call_id": call.id, "reason": "checkpoint_failed"}, True

        # 3) 执行
        runner = getattr(self.tools, "call", None)
        if not callable(runner):
            text = "错误: 内核未提供工具执行入口（tools.call 不可用）"
            return text, {"name": call.name, "status": "error",
                          "call_id": call.id, "reason": "NO_TOOL_RUNNER"}, True
        failed = False
        try:
            result = str(runner(call.name, dict(call.arguments)))
        except Exception as exc:
            failed = True
            result = "错误: 调用工具 " + call.name + " 失败: " + str(exc)
        if result.startswith("错误:") or result.startswith("Error:"):
            failed = True

        # 4) 有界结果（P20）
        result = self._truncate(call.name, result)

        # 5) 重复调用提醒（P21，只追加提示、不阻塞）
        reminder = self.reminder.note(call.name, call.arguments)
        if reminder:
            self.last_reminder = reminder
            result = result + "\n\n" + reminder

        return result, {"name": call.name, "status": "error" if failed else "done",
                        "call_id": call.id}, failed

    def _evaluate_permission(self, call: ToolCall) -> PermissionDecision:
        request = PermissionRequest(
            tool=call.name,
            arguments=dict(call.arguments),
            mode=self._mode_name,
            call_id=call.id,
            reason="agent 请求调用工具",
            target_paths=_target_paths(call.arguments),
            cwd=self.workdir(),
            parallel_safe=self._is_parallel_safe(call),
        )
        evaluator = getattr(self.permissions, "evaluate", None)
        if not callable(evaluator):
            return PermissionDecision(
                "unavailable", "权限网关不可用，按拒绝关闭")
        try:
            return _normalize_decision(evaluator(request))
        except Exception as exc:
            return PermissionDecision("deny", "权限判定异常，按拒绝关闭: " + str(exc))

    def _truncate(self, name: str, text: str) -> str:
        limit = self._result_limit(name)
        if limit <= 0 or len(text) <= limit:
            return text
        head = limit // 2
        tail = limit - head
        return (
            text[:head]
            + "\n…（工具结果过长，已省略 " + str(len(text) - limit) + " 字符）…\n"
            + text[-tail:]
        )

    def _append_tool_result(self, call: ToolCall, content: str) -> None:
        self.session.append(KIND_TOOL_RESULT, {
            "call_id": call.id,
            "name": call.name,
            "content": content,
        })
        self._history.append({
            "role": "tool",
            "tool_call_id": call.id,
            "content": content,
        })

    def _complete_missing_results(self, calls: Sequence[ToolCall]) -> int:
        """补全任何没有结果的 tool_call（TOOL_OUTCOME_UNKNOWN，不自动重试）。"""
        responded = {
            str(m.get("tool_call_id"))
            for m in self._history
            if m.get("role") == "tool" and m.get("tool_call_id")
        }
        repaired = 0
        for call in calls:
            if call.id in responded:
                continue
            self._history.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": TOOL_OUTCOME_UNKNOWN_TEXT,
            })
            self.session.append(KIND_TOOL_RESULT, {
                "call_id": call.id,
                "name": call.name,
                "outcome": TOOL_OUTCOME_UNKNOWN,
                "content": TOOL_OUTCOME_UNKNOWN_TEXT,
                "repaired": True,
            })
            repaired += 1
        return repaired

    # ---- 回合收尾 ----

    def _finish_turn(self, stop_reason: StopReason, full_reply: str,
                     steps: List[StepRecord]) -> None:
        self.stop_reason = stop_reason
        self.steps = list(steps)
        self.session.append(KIND_STOP, {"stop_reason": stop_reason.value})
        if self.projection is not None:
            try:
                self.projection.update(stop_reason=stop_reason.value)
            except Exception:  # pragma: no cover
                pass
        if self.logger is not None:
            try:
                self.logger.info(
                    "回合结束: stop_reason=" + stop_reason.value
                    + " steps=" + str(len(steps))
                    + " reply_len=" + str(len(full_reply)))
            except Exception:  # pragma: no cover
                pass

    def outcome(self, full_reply: str = "") -> TurnOutcome:
        """读出上一轮的显式结果（t7 / t8 的检查入口）。"""
        reason = self.stop_reason or StopReason.FINAL_ANSWER
        return TurnOutcome(
            stop_reason=reason,
            text=full_reply or self._last_reply(),
            steps=tuple(self.steps),
            error=self.last_error,
            iterations=len(self.steps),
            cancelled=(reason is StopReason.CANCELLED),
        )

    def _last_reply(self) -> str:
        return "".join(
            str(m.get("content") or "")
            for m in self._history[-1:]
            if m.get("role") == "assistant"
        )


# ----------------------------------------------------------------------------
# 辅助
# ----------------------------------------------------------------------------

#: 权限判定需要关注的路径参数名（供 t4 的路径边界判定使用）
PATH_ARGUMENT_KEYS = ("path", "file", "filepath", "target", "dst", "src", "directory",
                      "dir", "root", "vault", "paths")


def _denial_code(decision: "PermissionDecision") -> str:
    """取拒绝判定里最有信息量的结构化错误码。

    语义与 castorice.tools.base._denial_code 对齐（那边服务工具出口、这边
    服务 harness 派发出口）：网关在 info.code 里写入 APPROVAL_* /
    TOOL_NOT_VISIBLE / FS_SANDBOX_DENIED 等具体码；缺失时回落通用
    PERMISSION_DENIED，**绝不返回空串**，保证模型可见文本永远可区分。
    """
    info = getattr(decision, "info", None)
    if isinstance(info, Mapping):
        code = str(info.get("code", "") or "").strip()
        if code:
            return code
    return "PERMISSION_DENIED"


def _target_paths(arguments: Mapping[str, Any]) -> Tuple[str, ...]:
    found: List[str] = []
    for key, value in (arguments or {}).items():
        if str(key).lower() not in PATH_ARGUMENT_KEYS:
            continue
        if isinstance(value, str) and value:
            found.append(value)
        elif isinstance(value, (list, tuple)):
            found.extend(str(v) for v in value if isinstance(v, str) and v)
    return tuple(found)


def _checkpoint_message(error: str) -> str:
    """检查点失败时给用户看的文案（fail-closed，下游副作用一律不执行）。"""
    detail = ("：" + error) if error else ""
    return "抱歉，本轮已中止：会话检查点写入失败" + detail


def _content_text(message: Mapping[str, Any]) -> str:
    """从 OpenAI user 消息里提取可读文本（供事件日志与投影使用）。"""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, (list, tuple)):
        parts = [str(part.get("text", "")) for part in content
                 if isinstance(part, Mapping) and part.get("type") == "text"]
        return "".join(parts)
    return ""
