"""工具定义、注册表与执行运行时。

本模块是 docs/refactor/01-gap-matrix.md 第 6 节「工具生态」的落点：
在既有 ai.Tool（只有 name/description/parameters/function 四个字段）之上补齐
DSH defineTool 的元数据与**构造期校验**（对齐 dsh-tools 的 restrict 校验思路，
见 04-dsh-harness-notes.md 1.2 节与 R11）：

* 重名注册 -> 抛错（而不是静默覆盖）；
* parameters 不是「JSON Schema 的受支持子集」-> 抛错；
* modes 含未知模式 -> 抛错。

执行路径的唯一入口是 ToolRuntime.call：它在调用 handler 之前**再走一次
权限策略判定**（gateway.pre_execute，纯策略、不触发审批），从而保证
「所有有副作用的调用必须先过网关」在绕过 harness 的路径上同样成立。

设计约束（红线 D）：本包只依赖标准库与同仓库的 castorice.permissions / modes，
不得 import ui / PySide6 / ai。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
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

from ..permissions import PermissionGateway
from ..modes import ToolSpec as KernelToolSpec


# ----------------------------------------------------------------------------
# 词汇
# ----------------------------------------------------------------------------

KNOWN_MODES: FrozenSet[str] = frozenset({"chat", "work", "code", "learn"})

#: 新工具默认只对 Work / Code / Learn 可见（Chat 一律不可见，红线 E）
DEFAULT_TOOL_MODES: FrozenSet[str] = frozenset({"work", "code", "learn"})

SOURCE_BUILTIN = "builtin"
SOURCE_MCP = "mcp"
SOURCE_USER = "user"
KNOWN_SOURCES: FrozenSet[str] = frozenset({SOURCE_BUILTIN, SOURCE_MCP, SOURCE_USER})

TOOL_KIND_GENERIC = "generic"
TOOL_KIND_NETWORK = "network"
TOOL_KIND_WRITE = "write"
TOOL_KIND_COMMAND = "command"
TOOL_KIND_ACTION = "action"
TOOL_KINDS: FrozenSet[str] = frozenset({
    TOOL_KIND_GENERIC, TOOL_KIND_NETWORK, TOOL_KIND_WRITE,
    TOOL_KIND_COMMAND, TOOL_KIND_ACTION,
})

#: 工具结果默认上限（字符）；超过走 head/tail 截断 + 省略说明
DEFAULT_RESULT_LIMIT = 20_000

#: parameters 只允许的 JSON Schema 关键字（R11：受支持子集）
ALLOWED_SCHEMA_KEYS: FrozenSet[str] = frozenset({
    "type", "properties", "required", "additionalProperties",
    "description", "enum", "items", "default", "title",
})

#: 允许的 JSON 类型
ALLOWED_SCHEMA_TYPES: FrozenSet[str] = frozenset({
    "object", "array", "string", "number", "integer", "boolean", "null",
})

#: 统一的可读错误前缀（harness 据此把结果标记为失败，但**不抛异常**）
ERROR_PREFIX = "错误: "


class ToolDefinitionError(ValueError):
    """工具定义非法（重名 / 坏 schema / 未知模式）——构造期即抛错。"""


class ToolModeError(ToolDefinitionError):
    """工具的 modes 声明非法。"""


class ToolSchemaError(ToolDefinitionError):
    """工具的 parameters 不是受支持的 JSON Schema 子集。"""


# ----------------------------------------------------------------------------
# 文本工具
# ----------------------------------------------------------------------------

def error_text(message: Any, code: str = "") -> str:
    """构造统一的错误结果文本（不抛异常，轮次继续）。

    格式固定为「错误: <message>（<CODE>）」：**前缀与措辞保持与旧实现一致**
    （既有消费方按「错误: 未知工具 X」「错误: 调用工具 X 失败」做前缀判断），
    结构化的错误码追加在末尾，便于新代码分支而不破坏旧断言。
    """
    text = ERROR_PREFIX + str(message)
    if code:
        text += "（" + str(code) + "）"
    return text


def truncate_text(text: Any, limit: int = DEFAULT_RESULT_LIMIT,
                  *, note: str = "") -> str:
    """head/tail 截断 + 明确的省略说明（对齐 P20 的有界结果）。"""
    content = "" if text is None else str(text)
    if limit <= 0 or len(content) <= limit:
        return content
    head = max(1, limit // 2)
    tail = max(1, limit - head)
    omitted = len(content) - head - tail
    marker = note or "…（内容过长，已省略 " + str(omitted) + " 字符）…"
    return content[:head] + "\n" + marker + "\n" + content[-tail:]


# ----------------------------------------------------------------------------
# schema 校验（R11）
# ----------------------------------------------------------------------------

def validate_parameters(schema: Any, *, where: str = "parameters") -> None:
    """校验 parameters 是受支持的 JSON Schema 子集，否则抛 ToolSchemaError。"""
    if not isinstance(schema, Mapping):
        raise ToolSchemaError(where + " 必须是 dict（JSON Schema 对象）")
    unknown = sorted(set(str(key) for key in schema) - ALLOWED_SCHEMA_KEYS)
    if unknown:
        raise ToolSchemaError(
            where + " 含有不受支持的 JSON Schema 关键字 " + repr(unknown)
            + "；只允许 " + repr(sorted(ALLOWED_SCHEMA_KEYS))
        )
    schema_type = schema.get("type")
    if schema_type is not None and str(schema_type) not in ALLOWED_SCHEMA_TYPES:
        raise ToolSchemaError(
            where + ".type 非法: " + repr(schema_type)
            + "，可选 " + repr(sorted(ALLOWED_SCHEMA_TYPES))
        )
    if schema_type == "object" or "properties" in schema:
        properties = schema.get("properties", {})
        if not isinstance(properties, Mapping):
            raise ToolSchemaError(where + ".properties 必须是 dict")
        for name, child in properties.items():
            if not str(name).strip():
                raise ToolSchemaError(where + ".properties 里有空参数名")
            validate_parameters(child, where=where + ".properties." + str(name))
        required = schema.get("required", [])
        if not isinstance(required, (list, tuple)):
            raise ToolSchemaError(where + ".required 必须是数组")
        missing = [str(item) for item in required if str(item) not in properties]
        if missing:
            raise ToolSchemaError(
                where + ".required 里有未在 properties 中声明的参数 " + repr(missing)
            )
    additional = schema.get("additionalProperties")
    if additional is not None and not isinstance(additional, bool):
        raise ToolSchemaError(where + ".additionalProperties 只能是 bool")
    items = schema.get("items")
    if items is not None:
        if schema_type != "array":
            raise ToolSchemaError(where + ".items 只允许出现在 type=array 里")
        validate_parameters(items, where=where + ".items")
    enum = schema.get("enum")
    if enum is not None and not isinstance(enum, (list, tuple)):
        raise ToolSchemaError(where + ".enum 必须是数组")


def validate_modes(modes: Any, *, name: str = "") -> FrozenSet[str]:
    if isinstance(modes, str):
        modes = (modes,)
    try:
        resolved = frozenset(str(item) for item in modes)
    except TypeError:
        raise ToolModeError(
            "工具 " + repr(name) + " 的 modes 必须是可迭代的模式名集合"
        ) from None
    if not resolved:
        raise ToolModeError("工具 " + repr(name) + " 的 modes 不能为空集合")
    unknown = sorted(resolved - KNOWN_MODES)
    if unknown:
        raise ToolModeError(
            "工具 " + repr(name) + " 声明了未知模式 " + repr(unknown)
            + "；已知模式: " + repr(sorted(KNOWN_MODES))
        )
    if "chat" in resolved:
        raise ToolModeError(
            "工具 " + repr(name) + " 不能声明 chat 模式："
            "Chat 绝不向模型暴露任何工具（红线 E）"
        )
    return resolved


# ----------------------------------------------------------------------------
# 工具描述符
# ----------------------------------------------------------------------------

def _empty_schema() -> Dict[str, Any]:
    return {"type": "object", "properties": {}, "additionalProperties": False}


@dataclass(frozen=True)
class ToolSpec:
    """一个工具的内核侧完整描述（对齐 DSH 的 defineTool 声明）。

    handler 的调用约定与 ai.Tool.function 一致：handler(**arguments) -> 任意
    可转字符串的结果；handler 内部**不抛异常**，失败返回 error_text(...)。
    """

    name: str
    description: str = ""
    parameters: Mapping[str, Any] = field(default_factory=_empty_schema)
    handler: Optional[Callable[..., Any]] = None
    read_only: bool = False
    requires_approval: Optional[bool] = None
    parallel_safe: bool = True
    timeout_s: Optional[float] = None
    result_limit: int = DEFAULT_RESULT_LIMIT
    modes: FrozenSet[str] = DEFAULT_TOOL_MODES
    source: str = SOURCE_BUILTIN
    kind: str = TOOL_KIND_GENERIC

    def __post_init__(self) -> None:
        name = str(self.name or "").strip()
        if not name:
            raise ToolDefinitionError("工具名不能为空")
        if re.search(r"\s", name):
            raise ToolDefinitionError("工具名不能含空白: " + repr(self.name))
        object.__setattr__(self, "name", name)
        if self.name == "chat":
            raise ToolDefinitionError("工具名不能是保留字 chat")
        validate_parameters(self.parameters, where="工具 " + repr(name) + " 的 parameters")
        object.__setattr__(self, "modes", validate_modes(self.modes, name=name))
        source = str(self.source or SOURCE_BUILTIN)
        if source not in KNOWN_SOURCES:
            raise ToolDefinitionError(
                "工具 " + repr(name) + " 的 source 非法: " + repr(source)
                + "，可选 " + repr(sorted(KNOWN_SOURCES))
            )
        object.__setattr__(self, "source", source)
        kind = str(self.kind or TOOL_KIND_GENERIC)
        if kind not in TOOL_KINDS:
            raise ToolDefinitionError(
                "工具 " + repr(name) + " 的 kind 非法: " + repr(kind)
                + "，可选 " + repr(sorted(TOOL_KINDS))
            )
        object.__setattr__(self, "kind", kind)
        if self.requires_approval is None:
            object.__setattr__(self, "requires_approval", not self.read_only)
        if self.timeout_s is not None:
            object.__setattr__(self, "timeout_s", max(0.0, float(self.timeout_s)))
        object.__setattr__(self, "result_limit", max(0, int(self.result_limit)))

    # ---- 视图 ----

    def to_openai_spec(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": dict(self.parameters),
            },
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "read_only": self.read_only,
            "requires_approval": bool(self.requires_approval),
            "parallel_safe": self.parallel_safe,
            "timeout_s": self.timeout_s,
            "result_limit": self.result_limit,
            "modes": sorted(self.modes),
            "source": self.source,
            "kind": self.kind,
        }

    def with_handler(self, handler: Callable[..., Any]) -> "ToolSpec":
        return replace(self, handler=handler)

    def kernel_spec(self) -> KernelToolSpec:
        """转成 castorice.modes.ToolSpec（ModeRegistry 只认内核描述符）。"""
        return KernelToolSpec(
            name=self.name,
            description=self.description,
            parameters=dict(self.parameters),
            parallel_safe=self.parallel_safe,
            result_limit=self.result_limit,
        )


# ----------------------------------------------------------------------------
# 注册表
# ----------------------------------------------------------------------------

class ToolRegistry:
    """工具注册表：构造期校验 + 按名字取用 + OpenAI schema 导出。

    注意：本类**不做权限判定**，它是纯注册表。带网关的执行入口是
    ToolRuntime.call（见下），或 harness 的调用链。
    """

    def __init__(self, tools: Optional[Iterable[ToolSpec]] = None) -> None:
        self._tools: Dict[str, ToolSpec] = {}
        for spec in tools or ():
            self.register(spec)

    # ---- 注册 ----

    def register(self, spec: ToolSpec, *, replace_existing: bool = False) -> ToolSpec:
        """注册一个工具。重名默认抛错（构造期校验，不静默覆盖）。"""
        if not isinstance(spec, ToolSpec):
            raise ToolDefinitionError(
                "只能注册 castorice.tools.ToolSpec，得到 "
                + type(spec).__name__
            )
        if spec.name in self._tools and not replace_existing:
            raise ToolDefinitionError(
                "工具名重复: " + repr(spec.name)
                + "（已注册 " + repr(self._tools[spec.name].source)
                + " 来源的同名工具；如确实要覆盖请显式传 replace_existing=True）"
            )
        self._tools[spec.name] = spec
        return spec

    def extend(self, specs: Iterable[ToolSpec]) -> None:
        for spec in specs:
            self.register(spec)

    def unregister(self, name: str) -> bool:
        return self._tools.pop(name, None) is not None

    # ---- 查询 ----

    def __contains__(self, name: Any) -> bool:
        return str(name) in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    def __iter__(self):
        return iter(self._tools.values())

    def get(self, name: str) -> Optional[ToolSpec]:
        return self._tools.get(str(name))

    def names(self) -> List[str]:
        return list(self._tools)

    def all(self) -> List[ToolSpec]:
        return list(self._tools.values())

    def specs(self) -> List[ToolSpec]:
        return list(self._tools.values())

    def kernel_specs(self) -> List[KernelToolSpec]:
        return [spec.kernel_spec() for spec in self._tools.values()]

    def to_openai_specs(self) -> List[Dict[str, Any]]:
        return [spec.to_openai_spec() for spec in self._tools.values()]

    def by_source(self, source: str) -> List[ToolSpec]:
        return [spec for spec in self._tools.values() if spec.source == source]

    # ---- 执行（无权限判定） ----

    def call(self, name: str, arguments: Optional[Mapping[str, Any]] = None) -> str:
        """执行工具并返回文本结果；失败返回可读错误文本，**不抛异常**。"""
        spec = self._tools.get(str(name))
        if spec is None:
            known = ", ".join(self._tools) or "（当前没有任何已注册工具）"
            return error_text("未知工具 " + str(name) + "，可用工具: " + known,
                              "UNKNOWN_TOOL")
        if spec.handler is None:
            return error_text(
                "工具 " + str(name) + " 只有 schema 没有执行实现", "NO_HANDLER")
        try:
            result = spec.handler(**dict(arguments or {}))
        except TypeError as exc:
            # 参数不匹配也沿用「调用工具 X 失败」的既有措辞，只附加错误码
            return error_text(
                "调用工具 " + str(name) + " 失败: " + str(exc), "BAD_ARGUMENTS")
        except Exception as exc:
            return error_text(
                "调用工具 " + str(name) + " 失败: " + str(exc), "TOOL_FAILED")
        return truncate_text(result, spec.result_limit)


# ----------------------------------------------------------------------------
# 执行运行时（带网关复查）
# ----------------------------------------------------------------------------

def _denial_code(decision: Any) -> str:
    """取拒绝结果里最有信息量的错误码。

    网关的 info.code 比通用的 PERMISSION_DENIED 更具体（COMMAND_DENIED、
    FS_SANDBOX_DENIED、SANDBOX_READ_ONLY、TOOL_NOT_VISIBLE 等），测试与界面
    都按它分支；只有网关没给出结构化原因时才回落到通用码。
    """
    info = getattr(decision, "info", None)
    if isinstance(info, Mapping):
        code = str(info.get("code", "") or "").strip()
        if code:
            return code
    return "PERMISSION_DENIED"

class ToolRuntime:
    """内核使用的工具入口：all_tools() + call()，并在执行前复查权限策略。

    harness 已经在派发前做完整判定（含审批）；这里再走一次
    gateway.pre_execute（**纯策略、不触发审批**），保证任何绕过 harness
    的执行路径也无法跳过路径边界与命令白名单。
    """

    def __init__(self, registry: ToolRegistry,
                 gateway: Optional[PermissionGateway] = None, *,
                 mode: str = "chat", enforce: bool = True,
                 already_authorized: Optional[Callable[[], bool]] = None) -> None:
        self.registry = registry
        self.gateway = gateway
        self.mode = str(mode or "chat")
        self.enforce = bool(enforce)
        #: 判定「本次调用是否已经由上层（harness）做过完整判定（含审批）」。
        #: 为 None 时一律视为「没有」——即每个调用都要自己走完整判定，
        #: 这是直接调用（绕过 harness）时的正确且安全的默认值。
        self._already_authorized = already_authorized

    def all_tools(self) -> List[KernelToolSpec]:
        return self.registry.kernel_specs()

    def set_mode(self, mode: str) -> None:
        self.mode = str(mode or "chat")

    def names(self) -> List[str]:
        return self.registry.names()

    def _authorized(self) -> bool:
        """上层是否已为本次调用做过完整判定（含审批）。"""
        callback = self._already_authorized
        if callback is None:
            return False
        try:
            return bool(callback())
        except Exception:
            return False        # 判定不了就当没有：宁可多判一次，不可漏判

    def call(self, name: str, arguments: Optional[Mapping[str, Any]] = None) -> str:
        args = dict(arguments or {})
        if self.enforce and self.gateway is not None:
            from ..harness import PermissionRequest, _target_paths

            request = PermissionRequest(
                tool=str(name),
                arguments=args,
                mode=self.mode,
                reason="工具运行时复查",
                target_paths=_target_paths(args),
                cwd=self.gateway.workspace,
            )
            try:
                decision = self.gateway.pre_execute(request)
            except Exception as exc:
                return error_text("权限复查异常，按拒绝关闭: " + str(exc),
                                  "PERMISSION_ERROR")
            # 这里只做**策略维度**的复查（不触发审批，避免 harness 路径下的
            # 同一次调用被审批两次）：deny / unavailable 一律拦下。
            if decision.decision in ("deny", "unavailable"):
                return error_text(
                    decision.reason or decision.decision, "PERMISSION_DENIED")
            # ask 必须分清两种情况：
            #   * 上层已经做过完整判定（harness 派发路径）—— 直接放行，
            #     否则同一次调用会弹两次审批（allowed-once 被破坏）；
            #   * 没有上层判定（直接调用，例如测试或任何绕过 harness 的路径）
            #     —— 必须自己走完整判定。只看到 pre_execute 的 ask 就放行，
            #     等于「没有审批能力却静默执行了有副作用的工具」。
            if decision.decision == "ask" and not self._authorized():
                full = getattr(self.gateway, "decide", None)
                if not callable(full):
                    return error_text(
                        "权限网关不支持完整判定，按拒绝关闭", "PERMISSION_DENIED")
                try:
                    # 必须把工作区作为 cwd 传下去：否则相对路径会按**进程的**
                    # 当前目录解析，而不是工具上下文的工作区，越界判定就错了。
                    resolved = full(
                        name, args, mode=self.mode,
                        target_paths=_target_paths(args),
                        cwd=self.gateway.workspace)
                except Exception as exc:
                    return error_text(
                        "权限判定异常，按拒绝关闭: " + str(exc), "PERMISSION_ERROR")
                if not getattr(resolved, "allowed", False):
                    return error_text(
                        resolved.reason or "未获批准", "PERMISSION_DENIED")
        return self.registry.call(name, args)


# ----------------------------------------------------------------------------
# 工具上下文（工作区绑定与观察记录）
# ----------------------------------------------------------------------------

@dataclass
class ToolContext:
    """工具执行上下文：工作区、沙箱、权限策略与文件观察记录。

    由 ai.py 持有并在模式/会话切换时更新；工具闭包通过它读到**当前**边界，
    因此绑定的工作目录变化后无需重建工具。
    """

    workspace: str = ""
    cwd: str = ""
    sandbox: str = "read-only"
    mode: str = "chat"
    session_id: str = ""
    roots: Tuple[str, ...] = ()
    gateway: Optional[PermissionGateway] = None
    policy: Any = None                     # castorice.permissions.PathPolicy
    #: 绝对路径 -> (mtime, size, sha1)；供 edit_file 的「编辑前必须读取」（R6）
    observed: Dict[str, Tuple[float, int, str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.policy is None:
            from ..permissions import PathPolicy

            self.policy = PathPolicy(
                roots=tuple(self.roots) or ((self.workspace,) if self.workspace else ()),
                workspace=self.workspace or None,
                sandbox=self.sandbox,
            )

    # ---- 边界 ----

    @property
    def effective_workspace(self) -> str:
        return self.policy.effective_workspace if self.policy else self.workspace

    def cwd_or_workspace(self) -> str:
        return self.cwd or self.effective_workspace

    def check_read(self, path: Any) -> str:
        return self.policy.check(path, write=False, cwd=self.cwd_or_workspace(),
                                 sandbox=self.sandbox)

    def check_write(self, path: Any) -> str:
        return self.policy.check(path, write=True, cwd=self.cwd_or_workspace(),
                                 sandbox=self.sandbox)

    # ---- 观察记录（R5 / R6） ----

    def remember(self, path: str, stat: Any, digest: str) -> None:
        self.observed[path] = (float(stat.st_mtime), int(stat.st_size), digest)

    def forget(self, path: str) -> None:
        self.observed.pop(path, None)

    def observation(self, path: str) -> Optional[Tuple[float, int, str]]:
        return self.observed.get(path)
