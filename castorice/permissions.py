"""统一权限审批网关（按模式 / 工具 / 路径判定 allow | ask | deny）。

本模块是 DeepSeek Harness 三块权限机制在 Python 侧的落点，逐条对应
docs/refactor/04-dsh-harness-notes.md 的 1.3 节与可移植清单 P3-P10：

* dsh-sandbox-policy（逐调用沙箱策略解析 explicit / fold(events) / default）
  -> PathPolicy.resolve / check（P3 / P4 / P5）；
* dsh-user-approval（ask / never、应答者 waterfall、allowed-once、审计只写日志）
  -> ApprovalPolicy / ApprovalBroker / AutoResponder（P6 / P7 / P8）；
* dsh-tools 的工具流水线（pre-execute -> 单调 guard -> execute -> post-execute，
  拒绝以 Error: 理由 作为普通工具结果返回）
  -> PermissionGateway.pre_execute / guard / evaluate（P9，拒绝文本由
  castorice.harness 包装，见 P10）。

三条铁律（与 DSH 一致，违反即为回归）：

1. **fail-closed**：应答者缺失 / 抛异常 / 超时 / 返回词汇外的值，一律规范化为
   unavailable 并**按拒绝关闭**；网关自身绝不提示人类，也绝不静默放行。
2. **单调 guard**：guard 顺序执行、首次返回理由即拒绝，后续 guard 无法把它翻回允许。
3. **一次性授权**：批准只对**对应那一次**请求生效，不缓存、无 allow-always，
   同一次审批只允许一个在跑（第二次同 key 请求返回 ALREADY_IN_FLIGHT）。

设计约束（红线 D）：本模块只依赖标准库与同包的 modes / harness，
**不得** import ui / PySide6 / ai，也**不得** import castorice.tools
（工具侧只允许单向 import 本模块的类型）。
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    FrozenSet,
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)

from .harness import PermissionDecision, PermissionRequest
from .modes import (
    SANDBOX_DANGER_FULL_ACCESS,
    SANDBOX_MODES,
    SANDBOX_READ_ONLY,
    SANDBOX_WORKSPACE_WRITE,
)


# ----------------------------------------------------------------------------
# 决策词汇（与 castorice.harness.PermissionDecision.decision 一致）
# ----------------------------------------------------------------------------

DECISION_ALLOW = "allow"
DECISION_DENY = "deny"
DECISION_ASK = "ask"
DECISION_UNAVAILABLE = "unavailable"

DECISIONS: FrozenSet[str] = frozenset({
    DECISION_ALLOW, DECISION_DENY, DECISION_ASK, DECISION_UNAVAILABLE,
})

#: 审批策略只有两个值（对齐 dsh-user-approval：ask | never）
APPROVAL_ASK = "ask"
APPROVAL_NEVER = "never"
APPROVAL_POLICIES: FrozenSet[str] = frozenset({APPROVAL_ASK, APPROVAL_NEVER})

DEFAULT_APPROVAL_POLICY = APPROVAL_ASK
DEFAULT_APPROVAL_TIMEOUT = 120.0

#: 结构化错误码（对齐 DSH 的错误词汇，便于测试与 UI 分支）
CODE_UNKNOWN_TOOL = "UNKNOWN_TOOL"
CODE_TOOL_NOT_VISIBLE = "TOOL_NOT_VISIBLE"
CODE_SANDBOX_DENIED = "FS_SANDBOX_DENIED"
CODE_SANDBOX_READ_ONLY = "SANDBOX_READ_ONLY"
CODE_SANDBOX_UNAVAILABLE = "SANDBOX_UNAVAILABLE"
CODE_COMMAND_DENIED = "COMMAND_DENIED"
CODE_COMMAND_MODE_DENIED = "COMMAND_MODE_DENIED"
CODE_APPROVAL_POLICY_NEVER = "APPROVAL_POLICY_NEVER"
CODE_APPROVAL_NO_RESPONDER = "APPROVAL_NO_RESPONDER"
CODE_APPROVAL_TIMEOUT = "APPROVAL_TIMEOUT"
CODE_APPROVAL_RESPONDER_FAILED = "APPROVAL_RESPONDER_FAILED"
CODE_APPROVAL_UNKNOWN_ANSWER = "APPROVAL_UNKNOWN_ANSWER"
CODE_ALREADY_IN_FLIGHT = "ALREADY_IN_FLIGHT"
CODE_GUARD_DENIED = "GUARD_DENIED"
CODE_BAD_ARGUMENTS = "BAD_ARGUMENTS"

#: run_command 的工具名（命令白名单判定的触发点）
COMMAND_TOOL_NAMES: FrozenSet[str] = frozenset({"run_command"})


# ----------------------------------------------------------------------------
# 命令白名单
# ----------------------------------------------------------------------------

#: 内置只读命令白名单（对齐 01-gap-matrix.md 第 3 节的清单，与 modes.READONLY_COMMANDS 取并集）
DEFAULT_COMMAND_ALLOWLIST: Tuple[str, ...] = (
    "dir", "ls", "type", "cat", "head", "tail", "findstr", "grep",
    "git status", "git diff", "git log", "git show",
    "python -m pytest", "python -m compileall",
)

#: 明确拒绝的命令（即使被配置追加进白名单也拒绝，防御性）。
#: 对齐 01-gap-matrix.md 第 3 节：「明确不包含 rm/del/Remove-Item/pip install/
#: curl/Invoke-WebRequest/powershell」。
DENIED_COMMANDS: FrozenSet[str] = frozenset({
    "rm", "rmdir", "del", "erase", "rd",
    "mv", "move", "copy", "xcopy", "robocopy", "rename", "ren",
    "format", "diskpart", "shutdown", "restart-computer", "stop-computer",
    "reg", "regedit", "sc", "net", "netsh", "taskkill", "tasklist",
    "curl", "wget", "invoke-webrequest", "iwr", "invoke-restmethod",
    "pip", "pip3", "conda", "npm", "yarn", "pnpm", "npx",
    "powershell", "pwsh", "cmd", "bash", "sh", "zsh", "wsl", "start", "runas",
    "invoke-expression", "iex", "remove-item", "ri",
    "set-executionpolicy", "chmod", "chown", "sudo", "attrib", "icacls",
    "takeown", "cacls", "schtasks", "at", "wmic",
})

#: 解释器别名归一化：python3 / py / pythonw 都当作 python
PYTHON_ALIASES: FrozenSet[str] = frozenset({"python", "python3", "py", "pythonw"})

#: 可执行文件后缀（匹配前剥掉）
_EXECUTABLE_SUFFIXES: Tuple[str, ...] = (".exe", ".cmd", ".bat", ".com", ".ps1")


def normalize_command_token(token: Any) -> str:
    """把 argv[0] 归一化成用于白名单比对的名字。

    取 basename、剥掉可执行后缀、转小写，并把 python 的各种别名统一成 python。
    """
    text = str(token or "").strip().strip('"').strip("'")
    base = os.path.basename(text).lower()
    for suffix in _EXECUTABLE_SUFFIXES:
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    if base in PYTHON_ALIASES:
        return "python"
    return base


def _allowlist_entry_tokens(entry: Any) -> List[str]:
    parts = str(entry or "").split()
    if not parts:
        return []
    return [normalize_command_token(parts[0])] + [p.lower() for p in parts[1:]]


def match_command_allowlist(
    argv: Sequence[Any], allowlist: Iterable[str]
) -> Optional[str]:
    """命令是否命中白名单；命中返回命中的白名单条目，否则返回 None。

    匹配规则：把白名单条目按空白切成 token，要求 argv 的**前缀**与之逐一相等。
    这样 "git status" 只在 git status 开头的命令上命中，git push 不会被放行。
    """
    if not argv:
        return None
    lowered = [normalize_command_token(argv[0])] + [
        str(item).lower() for item in argv[1:]
    ]
    for entry in allowlist:
        tokens = _allowlist_entry_tokens(entry)
        if not tokens or len(tokens) > len(lowered):
            continue
        if lowered[: len(tokens)] == tokens:
            return str(entry)
    return None


def is_denied_command(argv: Sequence[Any]) -> Optional[str]:
    """命令是否落在显式拒绝表里；是则返回被拒绝的名字。"""
    if not argv:
        return None
    head = normalize_command_token(argv[0])
    if head in DENIED_COMMANDS:
        return head
    # 复合形态：python -m pip install 这类，额外扫描随后的两个 token
    for item in list(argv[:3])[1:]:
        candidate = normalize_command_token(item)
        if candidate in DENIED_COMMANDS:
            return candidate
    return None


# ----------------------------------------------------------------------------
# 异常（结构化错误，to_dict() 供 UI 直接展示）
# ----------------------------------------------------------------------------

class PermissionConfigurationError(ValueError):
    """权限配置非法（沙箱模式未知、审批策略未知等）——启动期即报错。"""


class SandboxDenied(RuntimeError):
    """路径越界（对齐 dsh-fs-sandbox 的 FS_SANDBOX_DENIED）。"""

    code = CODE_SANDBOX_DENIED

    def __init__(self, message: str, *, path: Any = None, root: Any = None,
                 code: Optional[str] = None) -> None:
        super().__init__(message)
        self.path = "" if path is None else str(path)
        self.root = "" if root is None else str(root)
        if code:
            self.code = code

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "message": str(self),
            "path": self.path,
            "root": self.root,
        }


class CommandDenied(RuntimeError):
    """命令被白名单拒绝（对齐 dsh-tool-bash 的策略级拒绝）。"""

    code = CODE_COMMAND_DENIED

    def __init__(self, message: str, *, command: Any = None, allowed: Any = None,
                 code: Optional[str] = None) -> None:
        super().__init__(message)
        self.command = "" if command is None else str(command)
        self.allowed = tuple(allowed or ())
        if code:
            self.code = code

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "message": str(self),
            "command": self.command,
            "allowed": list(self.allowed),
        }


# ----------------------------------------------------------------------------
# 路径策略（对齐 dsh-sandbox-policy / dsh-fs-sandbox）
# ----------------------------------------------------------------------------

def normalize_path(path: Any, cwd: Any = None) -> str:
    """把路径归一化成绝对路径。

    顺序（01-gap-matrix.md 第 3 节明确要求按此顺序）：
    expanduser -> 相对路径拼 cwd -> resolve(strict=False)（消 . / .. 与符号链接）
    -> normpath 兜底。
    """
    text = str(path or "").strip().strip('"').strip("'")
    if not text:
        raise SandboxDenied("路径为空，按拒绝关闭", code=CODE_SANDBOX_DENIED)
    candidate = Path(os.path.expanduser(text))
    if not candidate.is_absolute():
        candidate = Path(cwd or os.getcwd()) / candidate
    try:
        resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError):
        resolved = candidate
    return os.path.normpath(str(resolved))


def path_within(root: Any, target: Any) -> bool:
    """target 是否位于目录 root 之内（含 root 自身）。

    Windows 上先 normcase（大小写不敏感），再按路径分隔符边界比较，
    避免 C:/work2 被误判为在 C:/work 之内（前缀比较的经典坑）。
    """
    if root in (None, "") or target in (None, ""):
        return False
    try:
        resolved_root = normalize_path(root)
        resolved_target = normalize_path(target)
    except SandboxDenied:
        return False
    root_c = os.path.normcase(resolved_root)
    target_c = os.path.normcase(resolved_target)
    if target_c == root_c:
        return True
    return target_c.startswith(root_c.rstrip("\\/") + os.sep)


@dataclass(frozen=True)
class PathPolicy:
    """逐调用的沙箱路径策略。

    * roots：可信根目录（读的边界 = 任一根之内）；
    * workspace：写边界（写必须在工作区内，默认取 roots 的第一项）；
    * sandbox：沙箱模式词汇（故障安全默认 read-only）。

    resolve() 的优先级对齐 DSH 的 explicit / fold(events) / default：
    显式指定 -> 会话（或模式）绑定 -> 部署默认。
    """

    roots: Tuple[str, ...] = ()
    workspace: Optional[str] = None
    sandbox: str = SANDBOX_READ_ONLY

    def __post_init__(self) -> None:
        if self.sandbox not in SANDBOX_MODES:
            raise PermissionConfigurationError(
                "未知沙箱模式 " + repr(self.sandbox)
                + "，可选: " + repr(sorted(SANDBOX_MODES))
            )

    # ---- 构造 ----

    def with_workdir(self, workdir: Any = None, *, roots: Any = None,
                     sandbox: Optional[str] = None) -> "PathPolicy":
        """派生出「某个模式/会话」的策略（不改原对象）。"""
        resolved: List[str] = []
        for item in (roots if roots is not None else self.roots):
            try:
                normalized = normalize_path(item)
            except SandboxDenied:
                continue
            if normalized not in resolved:
                resolved.append(normalized)
        workspace: Optional[str] = None
        if workdir not in (None, ""):
            try:
                workspace = normalize_path(workdir)
            except SandboxDenied:
                workspace = None
            if workspace and workspace not in resolved:
                resolved.insert(0, workspace)
        return PathPolicy(
            roots=tuple(resolved),
            workspace=workspace or self.workspace,
            sandbox=sandbox or self.sandbox,
        )

    def resolve(self, session: Any = None, explicit: Any = None) -> str:
        """解析本次调用实际生效的沙箱模式。

        session 是鸭子类型对象（SessionStore / SessionHeader），只读取
        sandbox_mode；缺失即回落到部署默认（read-only，故障安全）。
        """
        if explicit:
            candidate = str(explicit)
            if candidate not in SANDBOX_MODES:
                raise PermissionConfigurationError(
                    "未知沙箱模式 " + repr(candidate)
                    + "，可选: " + repr(sorted(SANDBOX_MODES))
                )
            return candidate
        folded = None
        if session is not None:
            folded = getattr(session, "sandbox_mode", None)
            if not folded:
                header = getattr(session, "header", None)
                folded = getattr(header, "sandbox_mode", None) if header else None
        if folded in SANDBOX_MODES:
            return str(folded)
        return self.sandbox

    # ---- 判定 ----

    @property
    def effective_workspace(self) -> str:
        if self.workspace:
            return self.workspace
        if self.roots:
            return self.roots[0]
        return os.path.normpath(os.getcwd())

    @property
    def effective_roots(self) -> Tuple[str, ...]:
        if self.roots:
            return self.roots
        return (self.effective_workspace,)

    def is_readable(self, path: Any, *, cwd: Any = None,
                    sandbox: Optional[str] = None) -> bool:
        mode = sandbox or self.sandbox
        if mode == SANDBOX_DANGER_FULL_ACCESS:
            return True
        try:
            target = normalize_path(path, cwd)
        except SandboxDenied:
            return False
        return any(path_within(root, target) for root in self.effective_roots)

    def is_writable(self, path: Any, *, cwd: Any = None,
                    sandbox: Optional[str] = None) -> bool:
        mode = sandbox or self.sandbox
        if mode == SANDBOX_READ_ONLY:
            return False
        if mode == SANDBOX_DANGER_FULL_ACCESS:
            return True
        try:
            target = normalize_path(path, cwd)
        except SandboxDenied:
            return False
        return path_within(self.effective_workspace, target)

    def check_read(self, path: Any, *, cwd: Any = None,
                   sandbox: Optional[str] = None) -> str:
        """读取判定：越界抛 SandboxDenied，否则返回归一化路径。"""
        target = normalize_path(path, cwd)
        mode = sandbox or self.sandbox
        if mode == SANDBOX_DANGER_FULL_ACCESS:
            return target
        if any(path_within(root, target) for root in self.effective_roots):
            return target
        raise SandboxDenied(
            "拒绝读取 " + repr(target) + "：不在可信根内 "
            + repr(list(self.effective_roots))
            + "（策略级隔离，不是内核级沙箱）",
            path=target,
        )

    def check_write(self, path: Any, *, cwd: Any = None,
                    sandbox: Optional[str] = None) -> str:
        """写入判定：越界抛 SandboxDenied，否则返回归一化路径。"""
        target = normalize_path(path, cwd)
        mode = sandbox or self.sandbox
        if mode == SANDBOX_READ_ONLY:
            raise SandboxDenied(
                "拒绝写入 " + repr(target) + "：当前沙箱为 read-only"
                "（故障安全默认；请在已授权模式下显式绑定工作目录）",
                path=target,
                root=self.effective_workspace,
                code=CODE_SANDBOX_READ_ONLY,
            )
        if mode == SANDBOX_DANGER_FULL_ACCESS:
            return target
        workspace = self.effective_workspace
        if path_within(workspace, target):
            return target
        raise SandboxDenied(
            "拒绝写入 " + repr(target) + "：不在工作区 "
            + repr(workspace) + " 之内（策略级隔离，不是内核级沙箱）",
            path=target,
            root=workspace,
        )

    def check(self, path: Any, *, write: bool = False, cwd: Any = None,
              sandbox: Optional[str] = None) -> str:
        if write:
            return self.check_write(path, cwd=cwd, sandbox=sandbox)
        return self.check_read(path, cwd=cwd, sandbox=sandbox)


@dataclass(frozen=True)
class CommandPolicy:
    """命令白名单策略。

    allowlist 为空表示**该模式下不允许任何命令**（Learn 的
    command_allowlist 就是空元组）；否则按「模式白名单 + 内置白名单 +
    配置追加」求并集，但显式拒绝表 DENIED_COMMANDS 始终优先。
    """

    allowlist: Tuple[str, ...] = ()
    denied: FrozenSet[str] = DENIED_COMMANDS

    @property
    def enabled(self) -> bool:
        return bool(self.allowlist)

    def effective_allowlist(self, extra: Iterable[str] = ()) -> Tuple[str, ...]:
        merged: List[str] = []
        for item in list(self.allowlist) + list(DEFAULT_COMMAND_ALLOWLIST) + list(extra):
            text = str(item or "").strip()
            if text and text not in merged:
                merged.append(text)
        return tuple(merged)

    def check(self, argv: Sequence[Any], extra: Iterable[str] = ()) -> str:
        """判定命令；命中返回命中的白名单条目，否则抛 CommandDenied。"""
        if not argv:
            raise CommandDenied("命令为空，按拒绝关闭", code=CODE_COMMAND_DENIED)
        if not self.enabled:
            raise CommandDenied(
                "当前模式未授权执行任何命令（command_allowlist 为空）",
                command=" ".join(str(item) for item in argv),
                allowed=self.effective_allowlist(extra),
                code=CODE_COMMAND_MODE_DENIED,
            )
        denied = is_denied_command(argv)
        if denied:
            raise CommandDenied(
                "命令 " + repr(denied) + " 在显式拒绝表内，不予执行"
                "（rm/del/Remove-Item/pip install/curl/powershell 等一律拒绝）",
                command=" ".join(str(item) for item in argv),
                allowed=self.effective_allowlist(extra),
            )
        matched = match_command_allowlist(argv, self.effective_allowlist(extra))
        if matched:
            return matched
        raise CommandDenied(
            "命令不在白名单内: " + repr(" ".join(str(item) for item in argv)),
            command=" ".join(str(item) for item in argv),
            allowed=self.effective_allowlist(extra),
        )


# ----------------------------------------------------------------------------
# 审批（对齐 dsh-user-approval 的应答者 waterfall）
# ----------------------------------------------------------------------------

def coerce_decision(value: Any) -> PermissionDecision:
    """把应答者的返回值规范化；**无法识别的一律按拒绝关闭**（fail-closed）。"""
    if isinstance(value, PermissionDecision):
        if value.decision in DECISIONS:
            return value
        return PermissionDecision(
            DECISION_UNAVAILABLE,
            "审批应答者返回了词汇外的判定，按拒绝关闭: " + repr(value.decision),
            {"code": CODE_APPROVAL_UNKNOWN_ANSWER},
        )
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in DECISIONS:
            return PermissionDecision(lowered)
    if isinstance(value, Mapping):
        decision = str(value.get("decision", "") or "").strip().lower()
        if decision in DECISIONS:
            info = value.get("info")
            return PermissionDecision(
                decision,
                str(value.get("reason", "") or ""),
                dict(info) if isinstance(info, Mapping) else None,
            )
    return PermissionDecision(
        DECISION_UNAVAILABLE,
        "审批应答者返回了无法识别的结果，按拒绝关闭: " + repr(value),
        {"code": CODE_APPROVAL_UNKNOWN_ANSWER},
    )


class AutoResponder:
    """离线应答者：同步返回固定判定（不依赖 Qt，供测试与自动化使用）。"""

    def __init__(self, decision: str = DECISION_ALLOW, reason: str = "") -> None:
        if str(decision).lower() not in DECISIONS:
            raise PermissionConfigurationError("未知判定 " + repr(decision))
        self.decision = str(decision).lower()
        self.reason = reason
        self.requests: List[PermissionRequest] = []

    def __call__(self, request: PermissionRequest, request_id: str = ""):
        self.requests.append(request)
        if self.reason:
            return {"decision": self.decision, "reason": self.reason}
        return self.decision

    @property
    def call_count(self) -> int:
        return len(self.requests)


@dataclass(frozen=True)
class AuditEntry:
    """审计事件（approval/asked / approval/decided）。

    **只写日志**：不进模型可见文本，模型只看到最终工具结果与当前策略。
    """

    event: str
    tool: str
    mode: str
    request_id: str
    decision: str = ""
    reason: str = ""
    code: str = ""

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "event": self.event,
            "tool": self.tool,
            "mode": self.mode,
            "request_id": self.request_id,
        }
        if self.decision:
            payload["decision"] = self.decision
        if self.reason:
            payload["reason"] = self.reason
        if self.code:
            payload["code"] = self.code
        return payload


class ApprovalBroker:
    """一次性审批 seam。

    * policy=never：**在分发之前**确定性拒绝，应答者根本不会被调用（P6）；
    * 应答者缺失 / 抛异常 / 超时 -> unavailable（P7，铁定为不放行）；
    * 批准只对对应那一次请求生效（allowed-once），不缓存、无 allow-always；
    * 同一次审批只允许一个在跑：同一 key 的第二次请求返回 ALREADY_IN_FLIGHT（P8）。

    应答者有两种接入方式：

    1. 同步回调：ask_callback(request, request_id) 直接返回判定；
    2. 异步：回调先调用 defer(request_id) 表示「已把请求投递给主线程」，
       再返回 None；worker 侧阻塞等待主线程调用 resolve(request_id, decision)，
       超时即 unavailable（Qt 侧的写法见 DSH 台账 1.3 节）。
    """

    def __init__(
        self,
        policy: str = DEFAULT_APPROVAL_POLICY,
        ask_callback: Optional[Callable[..., Any]] = None,
        *,
        timeout: float = DEFAULT_APPROVAL_TIMEOUT,
        logger: Any = None,
    ) -> None:
        self.policy = self._validate_policy(policy)
        self.timeout = max(0.0, float(timeout))
        self._ask_callback = ask_callback
        self._logger = logger
        self._lock = threading.RLock()
        self._in_flight: set = set()
        self._pending: Dict[str, threading.Event] = {}
        self._results: Dict[str, Any] = {}
        self._audit: List[AuditEntry] = []
        self._counter = 0

    @staticmethod
    def _validate_policy(policy: Any) -> str:
        candidate = str(policy or DEFAULT_APPROVAL_POLICY).strip().lower()
        if candidate not in APPROVAL_POLICIES:
            raise PermissionConfigurationError(
                "未知审批策略 " + repr(policy)
                + "，可选: " + repr(sorted(APPROVAL_POLICIES))
            )
        return candidate

    # ---- 配置 ----

    def set_policy(self, policy: str) -> None:
        self.policy = self._validate_policy(policy)

    def set_ask_callback(self, callback: Optional[Callable[..., Any]]) -> None:
        self._ask_callback = callback

    @property
    def ask_callback(self) -> Optional[Callable[..., Any]]:
        return self._ask_callback

    # ---- 异步应答 ----

    def defer(self, request_id: str) -> threading.Event:
        """登记一个待决审批，返回 worker 侧等待的事件对象。"""
        with self._lock:
            event = self._pending.get(request_id)
            if event is None:
                event = threading.Event()
                self._pending[request_id] = event
            return event

    def resolve(self, request_id: str, decision: Any) -> bool:
        """主线程收到用户选择后回调这里；返回是否成功投递。"""
        with self._lock:
            event = self._pending.get(request_id)
            if event is None:
                return False
            self._results[request_id] = decision
        event.set()
        return True

    def pending_ids(self) -> Tuple[str, ...]:
        with self._lock:
            return tuple(self._pending)

    def in_flight(self) -> Tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._in_flight))

    # ---- 审计 ----

    def audit_log(self) -> Tuple[AuditEntry, ...]:
        with self._lock:
            return tuple(self._audit)

    def _record(self, entry: AuditEntry) -> None:
        with self._lock:
            self._audit.append(entry)
        if self._logger is not None:
            write = getattr(self._logger, "info", None)
            if callable(write):
                write("permission " + entry.event + " " + repr(entry.to_dict()))

    # ---- 请求 ----

    def request(
        self,
        request: PermissionRequest,
        key: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> PermissionDecision:
        request_id = str(key or request.call_id or request.tool or "")
        if not request_id:
            with self._lock:
                self._counter += 1
                request_id = "req-" + str(self._counter)

        # 1) never：分发前确定性拒绝，且不调用应答者（P6）
        if self.policy == APPROVAL_NEVER:
            self._record(AuditEntry(
                "approval/asked", request.tool, request.mode, request_id))
            self._record(AuditEntry(
                "approval/decided", request.tool, request.mode, request_id,
                decision=DECISION_DENY,
                reason="审批策略为 never",
                code=CODE_APPROVAL_POLICY_NEVER,
            ))
            return PermissionDecision(
                DECISION_DENY,
                "审批策略为 never：该操作在分发之前被确定性拒绝",
                {"code": CODE_APPROVAL_POLICY_NEVER, "request_id": request_id},
            )

        # 2) 同一次审批只允许一个在跑（P8）
        with self._lock:
            if request_id in self._in_flight:
                return PermissionDecision(
                    DECISION_DENY,
                    "同一次审批已在处理中（ALREADY_IN_FLIGHT），不重复分发",
                    {"code": CODE_ALREADY_IN_FLIGHT, "request_id": request_id},
                )
            self._in_flight.add(request_id)

        try:
            self._record(AuditEntry(
                "approval/asked", request.tool, request.mode, request_id))

            callback = self._ask_callback
            if callback is None:
                # 3) 无应答者 -> unavailable（P7）
                return self._finish_unavailable(
                    request, request_id,
                    "没有可用的审批应答者，按拒绝关闭（fail-closed）",
                    CODE_APPROVAL_NO_RESPONDER,
                )

            wait_for = self.timeout if timeout is None else max(0.0, float(timeout))
            try:
                answer = self._invoke(callback, request, request_id, wait_for)
            except Exception as exc:
                return self._finish_unavailable(
                    request, request_id,
                    "审批应答者异常，按拒绝关闭: " + str(exc),
                    CODE_APPROVAL_RESPONDER_FAILED,
                )

            if answer is None:
                return self._finish_unavailable(
                    request, request_id,
                    "审批无应答或超时（" + str(wait_for)
                    + "s）或窗口已关闭，按拒绝关闭",
                    CODE_APPROVAL_TIMEOUT,
                )

            decision = coerce_decision(answer)
            info = decision.info or {}
            code = ""
            if isinstance(info, Mapping):
                code = str(info.get("code", "") or "")
            self._record(AuditEntry(
                "approval/decided", request.tool, request.mode, request_id,
                decision=decision.decision, reason=decision.reason, code=code,
            ))
            return decision
        finally:
            with self._lock:
                self._in_flight.discard(request_id)
                self._pending.pop(request_id, None)
                self._results.pop(request_id, None)

    def _finish_unavailable(self, request: PermissionRequest, request_id: str,
                            reason: str, code: str) -> PermissionDecision:
        decision = PermissionDecision(
            DECISION_UNAVAILABLE, reason,
            {"code": code, "request_id": request_id},
        )
        self._record(AuditEntry(
            "approval/decided", request.tool, request.mode, request_id,
            decision=decision.decision, reason=reason, code=code,
        ))
        return decision

    def _invoke(self, callback, request: PermissionRequest,
                request_id: str, wait_for: float) -> Any:
        """调用应答者；返回 None 表示需要等待主线程 resolve()。

        注意顺序：先调用应答者、再查待决事件——应答者通常会在回调里
        调用 defer(request_id) 把请求投递给主线程，事件正是在那时登记的。
        """
        result = callback(request, request_id)
        if result is not None:
            return result
        with self._lock:
            event = self._pending.get(request_id)
        if event is None:
            # 应答者既没给判定也没登记待决请求：无法等待，按超时关闭
            return None
        if not event.wait(wait_for):
            return None
        with self._lock:
            return self._results.pop(request_id, None)


# ----------------------------------------------------------------------------
# 权限网关
# ----------------------------------------------------------------------------

@dataclass(frozen=True)
class ToolRequirement:
    """网关从工具描述符里读出的判定所需信息（鸭子类型，不 import tools/）。

    kind 取值：generic（只读）| write（写文件）| command（受限命令）| network。
    未显式声明 kind 时按工具名与 read_only 推断，保证「新工具默认按有副作用处理」。
    """

    name: str
    read_only: bool = False
    requires_approval: bool = True
    kind: str = "generic"
    parallel_safe: bool = True

    @classmethod
    def from_spec(cls, spec: Any, fallback_name: str = "") -> "ToolRequirement":
        name = str(getattr(spec, "name", None) or fallback_name or "")
        read_only = bool(getattr(spec, "read_only", False))
        requires = getattr(spec, "requires_approval", None)
        if requires is None:
            requires = not read_only
        kind = str(getattr(spec, "kind", "") or "")
        if not kind:
            if name in COMMAND_TOOL_NAMES:
                kind = "command"
            elif read_only:
                kind = "generic"
            else:
                kind = "write"
        return cls(
            name=name,
            read_only=read_only,
            requires_approval=bool(requires),
            kind=kind,
            parallel_safe=bool(getattr(spec, "parallel_safe", True)),
        )


class PermissionGateway:
    """统一权限审批网关。

    判定入口是 evaluate(request)；harness 在每个工具调用前调用它，返回
    PermissionDecision 后由 harness 把拒绝包装成 Error: 理由 的普通工具结果
    （P10，轮次继续而不中断）。

    判定顺序（对齐 dsh-tools 的流水线）：

    1. 已注册的**单调 guard**：顺序执行，首次返回理由即拒绝；
    2. 未知工具 -> UNKNOWN_TOOL；
    3. 模式可见性（复用 t3 的 ModeRegistry 裁决）-> TOOL_NOT_VISIBLE；
    4. 路径边界（只读工具走读判定，有副作用工具走写判定）-> FS_SANDBOX_DENIED；
    5. 命令白名单 -> COMMAND_DENIED / COMMAND_MODE_DENIED；
    6. 只读工具 -> allow；
    7. read-only 沙箱下的有副作用操作 -> SANDBOX_READ_ONLY；
    8. 需要审批 -> 走 ApprovalBroker（ask / never / unavailable）。
    """

    def __init__(
        self,
        config: Optional[Mapping[str, Any]] = None,
        *,
        tool_specs: Optional[Sequence[Any]] = None,
        mode: str = "chat",
        workdir: Any = None,
        roots: Optional[Iterable[str]] = None,
        sandbox: Optional[str] = None,
        mode_command_allowlist: Optional[Mapping[str, Sequence[str]]] = None,
        visibility: Optional[Callable[[str, str], bool]] = None,
        broker: Optional[ApprovalBroker] = None,
        path_policy: Optional[PathPolicy] = None,
        logger: Any = None,
    ) -> None:
        settings = dict(config or {})
        self._logger = logger
        self._mode = str(mode or "chat")

        configured_sandbox = sandbox or settings.get("sandbox_default") \
            or settings.get("sandbox") or SANDBOX_READ_ONLY
        if str(configured_sandbox) not in SANDBOX_MODES:
            raise PermissionConfigurationError(
                "配置 permissions.sandbox_default 为未知沙箱模式 "
                + repr(configured_sandbox)
                + "，可选: " + repr(sorted(SANDBOX_MODES))
            )

        self._roots: Tuple[str, ...] = self._normalized_roots(roots or ())
        self._workspace: Optional[str] = None
        self._path_policy = path_policy or PathPolicy(
            roots=self._roots, workspace=None, sandbox=str(configured_sandbox),
        )

        self._extra_commands: Tuple[str, ...] = tuple(
            str(item) for item in (settings.get("command_allowlist") or ())
            if str(item).strip()
        )
        self._mode_command_allowlist: Dict[str, Tuple[str, ...]] = {
            str(name): tuple(str(c) for c in (entries or ()))
            for name, entries in (mode_command_allowlist or {}).items()
        }

        self._requirements: Dict[str, ToolRequirement] = {}
        for spec in (tool_specs or ()):
            requirement = ToolRequirement.from_spec(spec)
            if requirement.name:
                self._requirements[requirement.name] = requirement

        self._visibility = visibility
        self._guards: List[Callable[[PermissionRequest], Optional[str]]] = []
        self._broker = broker if broker is not None else ApprovalBroker(
            settings.get("approval_policy", DEFAULT_APPROVAL_POLICY),
            logger=logger,
        )

        self.configure(workdir=workdir, mode=self._mode)

    # ---- 构造辅助 ----

    @staticmethod
    def _normalized_roots(roots: Iterable[Any]) -> Tuple[str, ...]:
        result: List[str] = []
        for item in roots or ():
            try:
                normalized = normalize_path(item)
            except SandboxDenied:
                continue
            if normalized not in result:
                result.append(normalized)
        return tuple(result)

    # ---- 配置（模式 / 工作目录 / 沙箱切换） ----

    def configure(
        self,
        *,
        mode: Optional[str] = None,
        workdir: Any = None,
        roots: Optional[Iterable[Any]] = None,
        sandbox: Optional[str] = None,
        mode_command_allowlist: Optional[Mapping[str, Sequence[str]]] = None,
    ) -> None:
        """把网关切到某个模式的工作区（会话 / 模式切换时由 ai.py 调用）。"""
        if mode is not None:
            self._mode = str(mode or "chat")
        if roots is not None:
            self._roots = self._normalized_roots(roots)
        if mode_command_allowlist is not None:
            self._mode_command_allowlist = {
                str(name): tuple(str(c) for c in (entries or ()))
                for name, entries in mode_command_allowlist.items()
            }
        if workdir is not None:
            try:
                text = str(workdir).strip()
                self._workspace = normalize_path(text) if text else None
            except SandboxDenied:
                self._workspace = None
        effective_roots: List[str] = list(self._roots)
        if self._workspace and self._workspace not in effective_roots:
            effective_roots.insert(0, self._workspace)
        if not effective_roots:
            effective_roots.append(os.path.normpath(os.getcwd()))
        self._path_policy = PathPolicy(
            roots=tuple(effective_roots),
            workspace=self._workspace,
            sandbox=sandbox or self._path_policy.sandbox,
        )

    def set_sandbox(self, sandbox: str) -> None:
        if str(sandbox) not in SANDBOX_MODES:
            raise PermissionConfigurationError("未知沙箱模式 " + repr(sandbox))
        self._path_policy = PathPolicy(
            roots=self._path_policy.roots,
            workspace=self._path_policy.workspace,
            sandbox=str(sandbox),
        )

    def register_tool(self, spec: Any) -> None:
        requirement = ToolRequirement.from_spec(spec)
        if requirement.name:
            self._requirements[requirement.name] = requirement

    # ---- 属性 ----

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def path_policy(self) -> PathPolicy:
        return self._path_policy

    @property
    def sandbox(self) -> str:
        return self._path_policy.sandbox

    @property
    def workspace(self) -> str:
        return self._path_policy.effective_workspace

    @property
    def broker(self) -> ApprovalBroker:
        return self._broker

    @property
    def approval_policy(self) -> str:
        return self._broker.policy

    def approval_available(self) -> bool:
        """当前是否**真的**具备审批能力（能产生 allow，而不是必然拒绝）。

        用于执行出口的 fail-closed 判定：

        * policy=never —— 完整判定必然 deny，因此不具备能力；
        * 没有应答者 —— 完整判定必然 unavailable（按拒绝关闭），同样不具备；
        * 其余情况（ask + 有应答者）—— 具备。

        没有这个查询时，执行出口只能看到 pre_execute 的 ask，无法区分
        「待批准」与「根本没人能批准」，于是会在无应答者时静默放行有副作用
        的工具——那正是「所有有副作用的调用必须先过网关」的反例。
        """
        if self._broker.policy == APPROVAL_NEVER:
            return False
        return self._broker.ask_callback is not None

    def set_approval_policy(self, policy: str) -> None:
        self._broker.set_policy(policy)

    def set_ask_callback(self, callback: Optional[Callable[..., Any]]) -> None:
        """注入审批应答者（UI 侧用 Signal 投递到主线程）。"""
        self._broker.set_ask_callback(callback)

    def audit_log(self) -> Tuple[AuditEntry, ...]:
        return self._broker.audit_log()

    def known_tools(self) -> Tuple[str, ...]:
        return tuple(self._requirements)

    def require(self, name: str) -> Optional[ToolRequirement]:
        return self._requirements.get(name)

    # ---- 单调 guard（P9） ----

    def guard(self, fn: Callable[[PermissionRequest], Optional[str]]) -> None:
        """注册单调守卫：返回非空理由即拒绝，且不可被后续守卫翻回允许。"""
        self._guards.append(fn)

    def guards(self) -> Tuple[Callable[[PermissionRequest], Optional[str]], ...]:
        return tuple(self._guards)

    # ---- 判定 ----

    @staticmethod
    def _deny(reason: str, code: str, **info: Any) -> PermissionDecision:
        payload: Dict[str, Any] = {"code": code}
        payload.update({k: v for k, v in info.items() if v not in (None, "")})
        return PermissionDecision(DECISION_DENY, reason, payload)

    @staticmethod
    def _unavailable(reason: str, code: str, **info: Any) -> PermissionDecision:
        payload: Dict[str, Any] = {"code": code}
        payload.update({k: v for k, v in info.items() if v not in (None, "")})
        return PermissionDecision(DECISION_UNAVAILABLE, reason, payload)

    def _mode_command_policy(self, mode: str) -> CommandPolicy:
        return CommandPolicy(allowlist=self._mode_command_allowlist.get(mode, ()))

    def command_allowlist(self, mode: Optional[str] = None) -> Tuple[str, ...]:
        """某个模式实际生效的命令白名单（供 UI 与测试读取）。"""
        return self._mode_command_policy(mode or self._mode).effective_allowlist(
            self._extra_commands
        )

    def pre_execute(self, request: PermissionRequest) -> PermissionDecision:
        """策略维度判定（不含 guard，不含审批分发）。"""
        requirement = self._requirements.get(request.tool)

        # 1) 未知工具
        if self._requirements and requirement is None:
            known = ", ".join(sorted(self._requirements)) or "（当前没有任何已注册工具）"
            return self._deny(
                "未知工具 " + repr(request.tool) + "，可用工具: " + known,
                CODE_UNKNOWN_TOOL,
            )

        # 2) 模式可见性（复用 t3 的裁决，异常路径不得绕过）
        if self._visibility is not None:
            try:
                visible = bool(self._visibility(request.mode, request.tool))
            except Exception as exc:
                return self._deny(
                    "工具可见性判定异常，按拒绝关闭: " + str(exc),
                    CODE_TOOL_NOT_VISIBLE,
                )
            if not visible:
                return self._deny(
                    "工具 " + repr(request.tool) + " 在模式 "
                    + repr(request.mode) + " 下不可见，拒绝执行",
                    CODE_TOOL_NOT_VISIBLE,
                )

        read_only = bool(requirement.read_only) if requirement else False
        sandbox = self._path_policy.sandbox
        cwd = request.cwd or self._path_policy.workspace

        # 3) 路径边界：只读工具走读判定，有副作用工具走写判定
        for raw in request.target_paths:
            try:
                self._path_policy.check(
                    raw, write=not read_only, cwd=cwd, sandbox=sandbox)
            except SandboxDenied as exc:
                return self._deny(
                    str(exc), exc.code, path=exc.path, root=exc.root)

        # 4) 命令白名单
        if request.tool in COMMAND_TOOL_NAMES:
            argv = command_argv(request.arguments)
            try:
                self._mode_command_policy(request.mode).check(
                    argv, self._extra_commands)
            except CommandDenied as exc:
                return self._deny(str(exc), exc.code, command=exc.command)

        # 5) 只读工具直接放行
        if read_only:
            return PermissionDecision(
                DECISION_ALLOW, "", {"code": "READ_ONLY_TOOL"})

        # 6) read-only 沙箱下拒绝有副作用操作
        if sandbox == SANDBOX_READ_ONLY:
            return self._deny(
                "当前沙箱为 read-only（故障安全默认），拒绝有副作用的工具 "
                + repr(request.tool) + "；请在已授权模式下绑定工作目录",
                CODE_SANDBOX_READ_ONLY,
            )

        # 7) 无需审批（但已过沙箱与路径判定）
        if requirement is not None and not requirement.requires_approval:
            return PermissionDecision(
                DECISION_ALLOW, "", {"code": "NO_APPROVAL_REQUIRED"})

        return PermissionDecision(
            DECISION_ASK, "需要用户审批", {"code": "APPROVAL_REQUIRED"})

    def evaluate(self, request: PermissionRequest) -> PermissionDecision:
        """完整判定：单调 guard -> pre_execute -> 审批分发。

        返回 PermissionDecision，decision 属于 allow | ask | deny | unavailable。
        ask 只会作为中间态出现：本方法把它交给 ApprovalBroker 解析成
        allow / deny / unavailable，因此调用方拿到的一定是可执行判定。
        """
        if not isinstance(request, PermissionRequest):
            return self._unavailable(
                "权限网关收到非法请求，按拒绝关闭: " + repr(type(request).__name__),
                CODE_BAD_ARGUMENTS,
            )

        # 1) 单调 guard：顺序执行，首次返回理由即拒绝（P9）
        for guard in self.guards():
            try:
                reason = guard(request)
            except Exception as exc:
                return self._deny(
                    "权限守卫异常，按拒绝关闭: " + str(exc), CODE_GUARD_DENIED)
            if reason:
                return self._deny(str(reason), CODE_GUARD_DENIED)

        decision = self.pre_execute(request)

        # 2) 只有 ask 才分发审批；never / 无应答者 / 超时一律按拒绝关闭
        if decision.decision == DECISION_ASK:
            return self._broker.request(
                request, key=request.call_id or request.tool)
        return decision

    # ---- 便捷入口（UI / 测试用） ----

    def decide(
        self,
        tool: str,
        arguments: Optional[Mapping[str, Any]] = None,
        *,
        mode: Optional[str] = None,
        target_paths: Optional[Iterable[str]] = None,
        call_id: str = "",
        cwd: Any = None,
    ) -> PermissionDecision:
        """用最少参数做一次判定（返回 allow/ask/deny/unavailable 与理由）。"""
        from .harness import _target_paths

        args = dict(arguments or {})
        paths = tuple(str(item) for item in target_paths) \
            if target_paths is not None else _target_paths(args)
        return self.evaluate(PermissionRequest(
            tool=str(tool),
            arguments=args,
            mode=str(mode or self._mode),
            call_id=str(call_id or ""),
            reason="decide",
            target_paths=paths,
            cwd=cwd,
        ))

    def check(
        self,
        tool: str,
        arguments: Optional[Mapping[str, Any]] = None,
        *,
        mode: Optional[str] = None,
        target_paths: Optional[Iterable[str]] = None,
        call_id: str = "",
    ) -> str:
        """只返回判定词（allow | ask | deny | unavailable），便于断言。"""
        return self.decide(
            tool, arguments, mode=mode, target_paths=target_paths, call_id=call_id,
        ).decision


def command_argv(arguments: Mapping[str, Any]) -> List[str]:
    """从 run_command 的参数里取出 argv。

    只接受显式列表形式 argv / args / command（列表），以及字符串形式
    command / cmd（按空白切分，**绝不用 shell 解析**，也绝不接受 shell=True）。
    """
    for key in ("argv", "args"):
        value = (arguments or {}).get(key)
        if isinstance(value, (list, tuple)) and value:
            return [str(item) for item in value]
        if isinstance(value, str) and value.strip():
            return value.strip().split()
    for key in ("command", "cmd"):
        value = (arguments or {}).get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().split()
        if isinstance(value, (list, tuple)) and value:
            return [str(item) for item in value]
    return []


def build_gateway(
    config: Optional[Mapping[str, Any]] = None, **kwargs: Any
) -> PermissionGateway:
    """工厂入口（ai.py 的接线点）。"""
    return PermissionGateway(config, **kwargs)
