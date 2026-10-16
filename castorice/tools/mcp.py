"""MCP 扩展接入点（第一期只留接口，不实现传输）。

对齐 01-gap-matrix.md 第 6 节与 04-dsh-harness-notes.md 的 R17：

* 命名约定：外部工具统一叫 mcp__<server>__<tool>；
* **无 server 时不产生任何工具，也不产生任何提示词文本**
  （对齐 dsh-mcp-client 的「调用方作用域为空时不添加 MCP 工具或提示词文本」）；
* 接入是纯注册动作：MCP 工具与内置工具走同一张注册表、同一套构造期校验、
  同一套权限判定与结果有界化，不引入第二套执行路径。

第二期接上真实传输时，只需把 server 的 call_tool 结果回填到 handler，
其余链路无需改动。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)

from .base import (
    SOURCE_MCP,
    TOOL_KIND_GENERIC,
    ToolDefinitionError,
    ToolRegistry,
    ToolSpec,
    error_text,
    validate_parameters,
)

#: 命名前缀与分隔符（与 dsh-mcp-client 一致）
MCP_PREFIX = "mcp__"
MCP_SEPARATOR = "__"

#: server / tool 名允许的字符（避免与分隔符冲突）
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def mcp_tool_name(server: str, tool: str) -> str:
    """构造 MCP 工具的规范名字 mcp__<server>__<tool>。"""
    server_name = str(server or "").strip()
    tool_name = str(tool or "").strip()
    if not server_name or not tool_name:
        raise ToolDefinitionError("MCP 的 server 与 tool 名都不能为空")
    for label, value in (("server", server_name), ("tool", tool_name)):
        if not _NAME_RE.match(value):
            raise ToolDefinitionError(
                "MCP 的 " + label + " 名非法: " + repr(value)
                + "（只允许字母数字与 _ . -，且不能以符号开头）"
            )
    return MCP_PREFIX + server_name + MCP_SEPARATOR + tool_name


def parse_mcp_tool_name(name: str) -> Optional[Tuple[str, str]]:
    """把 mcp__<server>__<tool> 解析回 (server, tool)；不匹配返回 None。"""
    text = str(name or "")
    if not text.startswith(MCP_PREFIX):
        return None
    rest = text[len(MCP_PREFIX):]
    if MCP_SEPARATOR not in rest:
        return None
    server, tool = rest.split(MCP_SEPARATOR, 1)
    if not server or not tool:
        return None
    return server, tool


@dataclass(frozen=True)
class McpToolDescriptor:
    """一个外部 MCP 工具的声明（传输层无关）。"""

    name: str
    description: str = ""
    parameters: Mapping[str, Any] = field(
        default_factory=lambda: {"type": "object", "properties": {}})
    read_only: bool = False
    parallel_safe: bool = False
    modes: Iterable[str] = ("work", "code", "learn")


@dataclass
class McpServer:
    """一个 MCP server 的接入登记。

    call_tool 是「第一期留出的接入点」：默认实现返回可读的未接入提示，
    第二期接上真实传输时替换它即可，工具链路其余部分不用改。
    """

    name: str
    tools: Tuple[McpToolDescriptor, ...] = ()
    call_tool: Optional[Callable[[str, Mapping[str, Any]], Any]] = None
    connected: bool = False

    def invoke(self, tool: str, arguments: Mapping[str, Any]) -> str:
        if self.call_tool is None:
            return error_text(
                "MCP server " + repr(self.name) + " 尚未接入传输层"
                "（第一期只提供注册入口）：工具 " + repr(tool) + " 未真正执行",
                "MCP_NOT_CONNECTED",
            )
        try:
            return str(self.call_tool(tool, dict(arguments or {})))
        except Exception as exc:
            return error_text(
                "MCP 工具 " + repr(tool) + " 调用失败: " + str(exc), "MCP_CALL_FAILED")


class McpRegistry:
    """MCP server 集合。

    关键短路：**没有任何 server（或 server 没有任何工具）时，tools() 返回空
    列表**，因此调用方不会注册任何工具，也就不会产生任何提示词文本。
    """

    def __init__(self) -> None:
        self._servers: Dict[str, McpServer] = {}

    def __len__(self) -> int:
        return len(self._servers)

    def __contains__(self, name: Any) -> bool:
        return str(name) in self._servers

    @property
    def empty(self) -> bool:
        return not self._servers or not any(s.tools for s in self._servers.values())

    def register_server(
        self,
        name: str,
        tools: Sequence[McpToolDescriptor] = (),
        *,
        call_tool: Optional[Callable[[str, Mapping[str, Any]], Any]] = None,
        connected: bool = False,
        replace_existing: bool = False,
    ) -> McpServer:
        server_name = str(name or "").strip()
        if not _NAME_RE.match(server_name):
            raise ToolDefinitionError("MCP server 名非法: " + repr(name))
        if server_name in self._servers and not replace_existing:
            raise ToolDefinitionError("MCP server 重复注册: " + repr(server_name))
        server = McpServer(
            name=server_name,
            tools=tuple(tools),
            call_tool=call_tool,
            connected=bool(connected),
        )
        # 构造期校验：server 内的工具名不能重复（与内置工具同样的严格度）
        seen = set()
        for descriptor in server.tools:
            key = str(descriptor.name)
            if key in seen:
                raise ToolDefinitionError(
                    "MCP server " + repr(server_name) + " 内的工具名重复: " + repr(key))
            seen.add(key)
        self._servers[server_name] = server
        return server

    def unregister_server(self, name: str) -> bool:
        return self._servers.pop(str(name), None) is not None

    def servers(self) -> List[McpServer]:
        return list(self._servers.values())

    def server_names(self) -> List[str]:
        return list(self._servers)

    def tools(self) -> List[ToolSpec]:
        """所有已登记 MCP 工具的 ToolSpec；没有 server 时返回空列表。"""
        if self.empty:
            return []
        result: List[ToolSpec] = []
        for server in self._servers.values():
            for descriptor in server.tools:
                parameters = dict(descriptor.parameters or {})
                validate_parameters(
                    parameters,
                    where="MCP 工具 " + repr(descriptor.name) + " 的 parameters",
                )
                result.append(self.build_spec(server, descriptor, parameters))
        return result

    def build_spec(self, server: McpServer, descriptor: McpToolDescriptor,
                   parameters: Mapping[str, Any]) -> ToolSpec:
        tool_name = str(descriptor.name)

        def handler(**arguments: Any) -> str:
            return server.invoke(tool_name, arguments)

        return ToolSpec(
            name=mcp_tool_name(server.name, tool_name),
            description=str(descriptor.description or ""),
            parameters=parameters,
            handler=handler,
            read_only=bool(descriptor.read_only),
            parallel_safe=bool(descriptor.parallel_safe),
            modes=tuple(descriptor.modes),
            source=SOURCE_MCP,
            kind=TOOL_KIND_GENERIC,
        )

    def register_into(self, registry: ToolRegistry) -> List[ToolSpec]:
        """把 MCP 工具注册进主注册表（重名照常抛错，不静默覆盖）。"""
        specs = self.tools()
        for spec in specs:
            registry.register(spec)
        return specs


def register_mcp_server(
    mcp_registry: McpRegistry,
    name: str,
    tools: Sequence[McpToolDescriptor] = (),
    *,
    call_tool: Optional[Callable[[str, Mapping[str, Any]], Any]] = None,
    connected: bool = False,
    replace_existing: bool = False,
) -> McpServer:
    """第一期唯一的外部扩展登记入口（对齐 R17 的接入点约定）。"""
    return mcp_registry.register_server(
        name, tools, call_tool=call_tool, connected=connected,
        replace_existing=replace_existing,
    )


def mcp_prompt_text(mcp_registry: McpRegistry) -> str:
    """MCP 相关的提示词文本。

    **无 server 时返回空串**：调用方据此决定不追加任何提示词，
    保证「没有 MCP 时工具列表与提示词与现状一致」。
    """
    if mcp_registry.empty:
        return ""
    lines = ["\n\n【外部工具（MCP）】"]
    for server in mcp_registry.servers():
        for descriptor in server.tools:
            lines.append("- " + mcp_tool_name(server.name, str(descriptor.name))
                         + "：" + str(descriptor.description or ""))
    if len(lines) == 1:
        return ""
    lines.append("这些工具来自外部 MCP 服务，调用前同样需要经过权限审批。")
    return "\n".join(lines)
