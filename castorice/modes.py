"""四模式（Chat / Work / Code / Learn）与工具可见性策略。

本模块是 DeepSeek Harness 的 dsh-tools 工具可见性机制在 Python 侧的落点
（见 docs/refactor/04-dsh-harness-notes.md 第 1.2 节与可移植清单 P1 / P2）：

* ToolPolicy    —— 对齐 ctx.tools.restrict({allow, deny}) 的掩码语义；
* Mode          —— 模式决定「系统提示词分层 + 工具可见性 + 工作目录绑定」；
* ModeRegistry  —— 在**构造期**对模式声明的工具名做校验（复刻
  dsh-tools/lib/index.js:2893-2909 的三条校验：未注册名报错、空 allow
  过滤器报错、未知模式名报错），并在**投影之前**求值可见性。

设计约束（docs/refactor/02-architecture.md 红线 D）：本模块只依赖标准库，
不得 import ui / PySide6 / ai。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import (
    Any,
    Dict,
    FrozenSet,
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)


# ----------------------------------------------------------------------------
# 沙箱模式词汇（封闭集合，对齐 dsh-sandbox-policy；故障安全默认 read-only）
# ----------------------------------------------------------------------------

SANDBOX_READ_ONLY = "read-only"
SANDBOX_WORKSPACE_WRITE = "workspace-write"
SANDBOX_DANGER_FULL_ACCESS = "danger-full-access"
SANDBOX_MODES: FrozenSet[str] = frozenset({
    SANDBOX_READ_ONLY, SANDBOX_WORKSPACE_WRITE, SANDBOX_DANGER_FULL_ACCESS,
})


# ----------------------------------------------------------------------------
# 工具描述符
# ----------------------------------------------------------------------------

@dataclass(frozen=True)
class ToolSpec:
    """工具的内核侧描述符。

    与 ai.Tool 的区别：内核只关心「名字 / 描述 / JSON Schema / 是否可并行 /
    结果长度上限」，**不持有可执行函数**——真正执行由调用方注入的 ToolRunner
    完成（对齐 DSH 的 schemas(scope) 只返回 schema、不返回 execute）。
    """

    name: str
    description: str = ""
    parameters: Mapping[str, Any] = field(
        default_factory=lambda: {"type": "object", "properties": {}})
    parallel_safe: bool = True
    result_limit: int = 8000

    def to_openai_spec(self) -> Dict[str, Any]:
        """单个 OpenAI function-calling schema。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": dict(self.parameters),
            },
        }


def coerce_tool(obj: Any) -> ToolSpec:
    """把外部工具对象（如 ai.Tool）适配成 ToolSpec。

    刻意不 import ai：只按鸭子类型读取属性，避免 castorice → ai 的反向依赖
    （红线 D）。
    """
    if isinstance(obj, ToolSpec):
        return obj
    if isinstance(obj, str):
        return ToolSpec(name=obj)
    name = getattr(obj, "name", None)
    if not name:
        raise TypeError("无法把 " + repr(obj) + " 适配成 ToolSpec：缺少 name")
    return ToolSpec(
        name=str(name),
        description=str(getattr(obj, "description", "") or ""),
        parameters=dict(
            getattr(obj, "parameters", None) or {"type": "object", "properties": {}}),
        parallel_safe=bool(getattr(obj, "parallel_safe", True)),
        result_limit=int(getattr(obj, "result_limit", 8000)),
    )


# ----------------------------------------------------------------------------
# 工具可见性策略（对齐 ctx.tools.restrict）
# ----------------------------------------------------------------------------

@dataclass(frozen=True)
class ToolPolicy:
    """工具可见性掩码。

    allow：None 表示「不施加 allow 掩码」（不限制）；空集合是**非法配置**，
    构造期即报错（复刻 DSH 的 restrict({}) no-op 检查，因为空过滤器几乎总是
    「配置被物化成空」的 bug）。
    deny：在 allow 通过之后再排除。
    """

    allow: Optional[FrozenSet[str]] = None
    deny: FrozenSet[str] = frozenset()

    def __post_init__(self) -> None:
        if self.allow is not None and len(self.allow) == 0:
            raise ValueError(
                "ToolPolicy.allow 不能是空集合：空 allow 过滤器几乎总是"
                "「配置被物化成空」的 bug（对齐 dsh-tools 的 restrict({}) 校验）；"
                "要表达「不限制」请用 allow=None，"
                "要表达「全部不可见」请用 deny=ALL_TOOLS"
            )

    def permits(self, name: str) -> bool:
        """该工具名在此策略下是否对模型可见。"""
        if self.allow is not None and name not in self.allow:
            return False
        return name not in self.deny


# ----------------------------------------------------------------------------
# 工作目录绑定
# ----------------------------------------------------------------------------

class WorkdirError(ValueError):
    """工作目录绑定失败。to_dict() 给出结构化错误（UI 可直接展示）。"""

    code = "WORKDIR_INVALID"

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


def _strip_trailing_sep(text: str) -> str:
    """去掉尾部分隔符，但保留驱动器根（如 D: + 分隔符）。"""
    separators = {os.sep}
    if os.altsep:
        separators.add(os.altsep)
    while len(text) > 1 and text[-1] in separators:
        # 驱动器根的形态是 「X: + 分隔符」（长度为 3），不能剥掉
        if len(text) == 3 and text[1] == ":":
            break
        text = text[:-1]
    return text


def contains_path(root: Any, target: Any) -> bool:
    """target 是否位于目录 root 之内（含 root 自身）。

    只做**词法归一化**（os.path.normpath + 大小写折叠），不做 resolve()：
    符号链接属于第二期内核级沙箱的范畴（见 R13 / N12），此处不越权处理，
    也避免在目标尚不存在时抛错。
    """
    if root in (None, "") or target in (None, ""):
        return False
    r = _strip_trailing_sep(os.path.normpath(str(root)))
    t = os.path.normpath(str(target))
    rc = os.path.normcase(r)
    tc = os.path.normcase(t)
    if tc == rc:
        return True
    return tc.startswith(rc + os.sep)


@dataclass(frozen=True)
class WorkdirPolicy:
    """模式的工作目录绑定规则。

    bindable：该模式是否允许绑定根目录（Chat 为 False）；
    default_sandbox：该模式的默认沙箱模式（故障安全默认 read-only）；
    command_allowlist：预留的命令白名单（t4 的 run_command 消费）；
    root_keys：允许作为根目录的配置键路径，按顺序解析
      （Code 认 modes.code.workdir / code_root，Learn 认 modes.learn.vault）。
    """

    bindable: bool = False
    default_sandbox: str = SANDBOX_READ_ONLY
    command_allowlist: Tuple[str, ...] = ()
    root_keys: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.default_sandbox not in SANDBOX_MODES:
            raise ValueError(
                "未知沙箱模式 " + repr(self.default_sandbox)
                + "，可选: " + repr(sorted(SANDBOX_MODES))
            )


# ----------------------------------------------------------------------------
# 模式
# ----------------------------------------------------------------------------

@dataclass(frozen=True)
class Mode:
    """一个会话模式。

    对应 DSH 里「模式即用户可见的权限预设」（见不适用项 N3）：一个 Mode 同时
    决定系统提示词分层（system_prompt_sections）、工具可见性（tool_policy）
    与工作目录绑定（workdir_policy）。
    """

    name: str
    display_name: str
    tool_allowlist: Tuple[str, ...]
    system_prompt_sections: Tuple[str, ...]
    workdir_policy: WorkdirPolicy
    tool_policy: ToolPolicy = field(default_factory=ToolPolicy)
    supports_vision_input: bool = True

    # ---- 兼容别名（02-architecture.md 第 2.1 节的命名） ----
    @property
    def prompt_sections(self) -> Tuple[str, ...]:
        return self.system_prompt_sections

    @property
    def sandbox(self) -> str:
        return self.workdir_policy.default_sandbox

    @property
    def bindable(self) -> bool:
        return self.workdir_policy.bindable

    def __str__(self) -> str:
        return "Mode(" + self.name + ")"


# ----------------------------------------------------------------------------
# 注册表
# ----------------------------------------------------------------------------

DEFAULT_MODE_ORDER: Tuple[str, ...] = ("chat", "work", "code", "learn")
FALLBACK_MODE = "chat"


def resolve_mode_name(name: Any, known: Iterable[str] = DEFAULT_MODE_ORDER,
                      *, default: str = FALLBACK_MODE) -> str:
    """把 config.yaml 里的 mode 字段解析成合法模式名。

    未知值**回退到 chat**（失败安全：宁可少给工具，不可多给工具），
    与 DSH 的故障安全默认一致。
    """
    known_set = set(known)
    if isinstance(name, str):
        candidate = name.strip().lower()
        if candidate in known_set:
            return candidate
    if default not in known_set:
        raise ValueError("默认模式 " + repr(default) + " 不在已知模式中: "
                         + repr(sorted(known_set)))
    return default


class ModeRegistry:
    """模式注册表 + 构造期校验 + 工具可见性投影。

    构造期校验（复刻 dsh-tools 的 restrict 校验）：
    1. 模式声明的工具名不在已知工具集里 → ValueError，错误信息列出已知工具；
    2. ToolPolicy.allow 是空集合 → ValueError（在 ToolPolicy 内部抛出）；
    3. 未知模式名 / 重复模式名 / chat 带工具 → ValueError。
    """

    def __init__(
        self,
        tools: Optional[Sequence[Any]] = None,
        modes: Optional[Sequence[Mode]] = None,
        *,
        sources: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self._tools: Dict[str, ToolSpec] = {}
        for raw in (tools or ()):
            spec = coerce_tool(raw)
            self._tools[spec.name] = spec

        if modes is None:
            resolved = builtin_modes(self._tools.keys(), tools=self._tools.keys())
        else:
            resolved = [self._validate_mode(m) for m in modes]

        if not resolved:
            raise ValueError("模式列表不能为空")
        registry: Dict[str, Mode] = {}
        for mode in resolved:
            if mode.name in registry:
                raise ValueError("重复的模式名: " + repr(mode.name))
            registry[mode.name] = mode
        self._modes = registry
        self._sources: Dict[str, Any] = dict(sources or {})
        self._roots: Dict[str, str] = {}

    # ---- 构造期校验 ----

    def _validate_mode(self, mode: Mode) -> Mode:
        unknown = sorted(set(mode.tool_allowlist) - set(self._tools))
        if unknown:
            known = ", ".join(sorted(self._tools)) or "（当前没有任何已注册工具）"
            raise ValueError(
                "模式 " + repr(mode.name) + " 声明了未注册的工具 "
                + repr(unknown) + "；已知工具: " + known
            )
        if not isinstance(mode.tool_allowlist, tuple):
            raise ValueError("模式 " + repr(mode.name) + " 的 tool_allowlist 必须是 tuple")
        if mode.name == "chat" and mode.tool_allowlist:
            raise ValueError(
                "chat 模式的 tool_allowlist 必须为空元组："
                "Chat 绝不向模型暴露任何工具（红线 E）"
            )
        return mode

    # ---- 查询 ----

    @property
    def tool_names(self) -> Tuple[str, ...]:
        return tuple(self._tools)

    def all_tools(self) -> List[ToolSpec]:
        return list(self._tools.values())

    def get(self, name: str) -> Mode:
        try:
            return self._modes[name]
        except KeyError:
            raise ValueError(
                "未知模式 " + repr(name) + "，已知模式: " + repr(sorted(self._modes))
            ) from None

    def has(self, name: str) -> bool:
        return name in self._modes

    def all_modes(self) -> List[Mode]:
        ordered = [self._modes[n] for n in DEFAULT_MODE_ORDER if n in self._modes]
        extra = [m for n, m in self._modes.items() if n not in DEFAULT_MODE_ORDER]
        return ordered + extra

    def names(self) -> List[str]:
        return [m.name for m in self.all_modes()]

    # ---- 工具可见性投影（对齐 dsh-tools 的 schemas(scope)） ----

    def is_visible(self, mode: Any, name: str) -> bool:
        """逐工具裁决：先按模式 allow/deny 过滤，再要求工具已注册。

        这是唯一的裁决入口，任何构造请求的路径都必须经过它（含重试路径与
        工具失败后的续轮路径）。
        """
        resolved = self._coerce_mode(mode)
        if resolved.name == "chat":
            return False
        if name not in self._tools:
            return False
        return resolved.tool_policy.permits(name)

    def visible_tools(self, mode: Any) -> List[ToolSpec]:
        resolved = self._coerce_mode(mode)
        return [t for t in self._tools.values() if self.is_visible(resolved, t.name)]

    def visible_tool_names(self, mode: Any) -> List[str]:
        return [t.name for t in self.visible_tools(mode)]

    def to_openai_specs(self, mode: Any) -> List[Dict[str, Any]]:
        """该模式下对模型可见的工具 schema 列表。

        **Chat 模式必须返回严格空列表**（红线 E）。可以传 Mode 或模式名字符串。
        """
        resolved = self._coerce_mode(mode)
        if resolved.name == "chat":
            return []
        return [t.to_openai_spec() for t in self.visible_tools(resolved)]

    def chat_completion_kwargs(
        self, mode: Any, base: Optional[Mapping[str, Any]] = None
    ) -> Dict[str, Any]:
        """构造请求体：**没有可见工具时不出现 tools 键**。

        这是 DSH「可见性判定必须发生在构造请求之前，且异常路径不得绕过」在
        Python 侧的收口点：ai.py 与 harness.py 都通过它构造 kwargs，不允许
        任何地方直接写 tools=[] 或 tools=None。
        """
        kwargs: Dict[str, Any] = dict(base or {})
        specs = self.to_openai_specs(mode)
        if specs:
            kwargs["tools"] = specs
        else:
            kwargs.pop("tools", None)
        return kwargs

    def _coerce_mode(self, mode: Any) -> Mode:
        if isinstance(mode, Mode):
            if mode.name in self._modes:
                return self._modes[mode.name]
            return self._validate_mode(mode)
        if isinstance(mode, str):
            return self.get(mode)
        raise TypeError("需要 Mode 或模式名字符串，得到 " + type(mode).__name__)

    # ---- 工作目录绑定（对齐 dsh-sandbox-policy 的「会话不可变 cwd」） ----

    def bind_workdir(self, mode: Any, path: Any, *, source: str = "explicit") -> str:
        """为模式绑定根目录，返回规范化后的路径。

        越界路径（不在任何可信根之内）**被拒绝**并抛出带 to_dict() 的
        WorkdirError（结构化错误，UI 可直接展示）。
        """
        resolved = self._coerce_mode(mode)
        policy = resolved.workdir_policy
        if not policy.bindable:
            raise WorkdirError(
                "模式 " + repr(resolved.name) + " 不支持绑定工作目录",
                path=path, code="WORKDIR_NOT_BINDABLE",
            )
        if path in (None, ""):
            self._roots.pop(resolved.name, None)
            return ""
        text = str(path).strip()
        if not text:
            self._roots.pop(resolved.name, None)
            return ""

        candidate = Path(text).expanduser()
        if not candidate.is_absolute():
            # 相对路径相对当前进程工作目录解析，避免「以为是某个根目录」的误绑定
            candidate = Path.cwd() / candidate
        normalized = os.path.normpath(str(candidate))

        roots = self.trusted_roots()
        if not any(contains_path(r, normalized) for r in roots):
            raise WorkdirError(
                "拒绝绑定越界路径 " + repr(normalized) + "：可信根为 "
                + repr(list(roots) or "（未配置可信根）"),
                path=normalized, code="WORKDIR_OUT_OF_ROOT",
            )
        self._roots[resolved.name] = normalized
        return normalized

    def workdir(self, mode: Any) -> Optional[str]:
        """模式当前绑定的工作目录（未绑定返回 None）。"""
        return self._roots.get(self._coerce_mode(mode).name)

    def trusted_roots(self) -> Tuple[str, ...]:
        """可信根目录列表（来自构造参数 sources）。

        支持 roots / trusted_roots / code_root / vault 四个键；未配置任何可信
        根时**回落到进程当前目录**（等价于「只信任项目目录」）。
        """
        raw: List[Any] = []
        for key in ("roots", "trusted_roots", "code_root", "vault"):
            value = self._sources.get(key)
            if value in (None, "", (), []):
                continue
            if isinstance(value, (list, tuple, set)):
                raw.extend(value)
            else:
                raw.append(value)
        roots: List[str] = []
        for item in raw:
            normalized = os.path.normpath(str(Path(str(item)).expanduser()))
            if normalized not in roots:
                roots.append(normalized)
        if not roots:
            roots.append(os.path.normpath(os.getcwd()))
        return tuple(roots)

    def resolve_workdir_from_config(
        self, mode: Any, config: Mapping[str, Any]
    ) -> Optional[str]:
        """按 WorkdirPolicy.root_keys 从配置里取根目录（第一个非空值）。

        不抛异常：配置缺项或越界时返回 None，由调用方决定是否提示用户
        （对齐 P3 的 explicit ?? fold(events) ?? default，这里的 explicit 一级
        就是 config）。
        """
        resolved = self._coerce_mode(mode)
        for key in resolved.workdir_policy.root_keys:
            node: Any = config
            for part in key.split("."):
                if isinstance(node, Mapping):
                    node = node.get(part)
                else:
                    node = None
                    break
            if node:
                try:
                    return self.bind_workdir(resolved, node, source="config")
                except WorkdirError:
                    return None
        return None


# ----------------------------------------------------------------------------
# 内置模式定义
# ----------------------------------------------------------------------------

#: 通用工具（Work / Code 的基础集合）。t4 才会真正注册这些工具，这里先把它们
#: 声明为「计划中的工具名」，使模式表在 t3 即可被静态验证，并在 t4 注册表就绪
#: 时自动生效（注册表里的工具名是这里的子集）。
PLANNED_TOOL_NAMES: FrozenSet[str] = frozenset({
    "get_current_time",
    "web_search",
    "web_fetch",
    "read_file",
    "write_file",
    "edit_file",
    "list_dir",
    "glob_files",
    "grep_files",
    "run_command",
    "make_document",
})

#: Learn 专属的笔记工具
NOTE_TOOL_NAMES: FrozenSet[str] = frozenset({
    "search_notes", "read_note", "write_note",
})

#: 全部已知工具名（第一期）
ALL_TOOLS: FrozenSet[str] = PLANNED_TOOL_NAMES | NOTE_TOOL_NAMES

#: Chat 一律拒绝的工具集（配合 allow=None 表达「一个都不给」）
ALL_TOOLS_DENIED: FrozenSet[str] = ALL_TOOLS

#: Code 模式拒绝的笔记工具（对齐 02-architecture.md 的「通用工具 − 笔记工具」）
CODE_DENIED: FrozenSet[str] = NOTE_TOOL_NAMES

#: Learn 允许的工具（对齐 02-architecture.md 的 Learn 行）
LEARN_ALLOWED: FrozenSet[str] = frozenset({
    "get_current_time", "read_file", "list_dir", "glob_files", "grep_files",
}) | NOTE_TOOL_NAMES

#: 只读命令白名单（t4 的 run_command 消费）
READONLY_COMMANDS: Tuple[str, ...] = (
    "dir", "ls", "type", "cat", "head", "tail", "findstr", "grep",
)


def builtin_modes(known_tools: Optional[Iterable[str]] = None,
                  *, tools: Optional[Iterable[str]] = None) -> List[Mode]:
    """构造第一期四个内置模式。

    known_tools（或别名 tools）给出**当前已注册**的工具名；这里按它裁剪模式
    声明的工具名，保证 ModeRegistry 的构造期校验不会因为「工具尚未实现」而
    误报。传入 None 时使用 ALL_TOOLS（完整的第一期工具表），用于对模式表本身
    做静态校验。
    """
    if known_tools is None:
        known_tools = tools
    available = set(ALL_TOOLS if known_tools is None else known_tools)

    def keep(names: Iterable[str]) -> Tuple[str, ...]:
        return tuple(sorted(set(names) & available))

    def allow_mask(names: Iterable[str]) -> ToolPolicy:
        """把「允许集合」物化成掩码。

        allow 是空集合在 ToolPolicy 里是非法的（那是「配置被物化成空」的
        bug），所以交集为空时改成显式 deny-ALL，语义等价且不会误报。
        """
        kept = set(names) & available
        if not kept:
            return ToolPolicy(allow=None, deny=ALL_TOOLS_DENIED)
        return ToolPolicy(allow=frozenset(kept), deny=frozenset())

    chat = Mode(
        name="chat",
        display_name="聊天",
        tool_allowlist=(),
        system_prompt_sections=(),
        workdir_policy=WorkdirPolicy(
            bindable=False,
            default_sandbox=SANDBOX_READ_ONLY,
            root_keys=(),
        ),
        tool_policy=ToolPolicy(allow=None, deny=ALL_TOOLS_DENIED),
    )

    work = Mode(
        name="work",
        display_name="工作",
        tool_allowlist=keep(PLANNED_TOOL_NAMES),
        system_prompt_sections=("work",),
        workdir_policy=WorkdirPolicy(
            bindable=True,
            default_sandbox=SANDBOX_WORKSPACE_WRITE,
            command_allowlist=READONLY_COMMANDS,
            root_keys=("modes.work.workdir", "workdir"),
        ),
        tool_policy=ToolPolicy(allow=None, deny=frozenset()),
    )

    code = Mode(
        name="code",
        display_name="编程",
        tool_allowlist=keep(PLANNED_TOOL_NAMES - CODE_DENIED),
        system_prompt_sections=("code",),
        workdir_policy=WorkdirPolicy(
            bindable=True,
            default_sandbox=SANDBOX_WORKSPACE_WRITE,
            command_allowlist=READONLY_COMMANDS,
            root_keys=("modes.code.workdir", "code_root", "workdir"),
        ),
        tool_policy=ToolPolicy(allow=None, deny=CODE_DENIED),
    )

    learn = Mode(
        name="learn",
        display_name="学习",
        tool_allowlist=keep(LEARN_ALLOWED),
        system_prompt_sections=("learn",),
        workdir_policy=WorkdirPolicy(
            bindable=True,
            default_sandbox=SANDBOX_WORKSPACE_WRITE,
            command_allowlist=(),
            root_keys=("modes.learn.vault", "vault", "modes.learn.workdir"),
        ),
        tool_policy=allow_mask(LEARN_ALLOWED),
    )

    return [chat, work, code, learn]


BUILTIN_MODES: Tuple[Mode, ...] = tuple(builtin_modes())
CHAT, WORK, CODE, LEARN = BUILTIN_MODES


def build_harness_registry(
    known_tools: Iterable[str],
    root: Any = None,
    *,
    sources: Optional[Mapping[str, Any]] = None,
) -> ModeRegistry:
    """按「当前已注册工具」构造注册表（ai.py 的接入点）。"""
    names = list(known_tools)
    mode_list = builtin_modes(names)
    merged_sources: Dict[str, Any] = dict(sources or {})
    if root not in (None, ""):
        merged_sources.setdefault("roots", [str(root)])
    return ModeRegistry(
        tools=[ToolSpec(name=n) for n in names],
        modes=mode_list,
        sources=merged_sources,
    )
