"""工具生态：注册表、构造期校验、按模式可见、权限判定与执行链路。

模块划分（对齐 01-gap-matrix.md 第 6 节与 02-architecture.md 第 2.5 节）：

* base.py     ToolSpec（defineTool 的 Python 版元数据）、ToolRegistry、
              ToolRuntime（带网关复查的执行入口）、ToolContext、
              schema 构造期校验与结果有界化；
* fs.py       read_file / write_file / edit_file / list_dir / glob_files /
              grep_files（观察记录 R5 与「编辑前必须读取」R6）；
* command.py  run_command（白名单 + 超时 + 输出上限 + ASTER_ 托管变量）；
* web.py      web_search（六后端 + 自动降级，从 ai.py 迁移）/ web_fetch；
* document.py make_document（第一期写 .md / .txt）；
* notes.py    search_notes / read_note / write_note（Learn 专属，保留 frontmatter）；
* mcp.py      register_mcp_server 接入点 + mcp__<server>__<tool> 命名；
* life.py     生活服务（提醒 / 系统信息）与音乐播放队列。

设计约束（红线 D）：本包只依赖标准库与同仓库的 castorice.permissions / modes，
不得 import ui / PySide6 / ai。
"""

from __future__ import annotations

from typing import Any, List, Mapping, Optional

from .base import (
    ALLOWED_SCHEMA_KEYS,
    ALLOWED_SCHEMA_TYPES,
    DEFAULT_RESULT_LIMIT,
    DEFAULT_TOOL_MODES,
    ERROR_PREFIX,
    KNOWN_MODES,
    KNOWN_SOURCES,
    SOURCE_BUILTIN,
    SOURCE_MCP,
    SOURCE_USER,
    TOOL_KINDS,
    TOOL_KIND_ACTION,
    TOOL_KIND_COMMAND,
    TOOL_KIND_GENERIC,
    TOOL_KIND_NETWORK,
    TOOL_KIND_WRITE,
    ToolContext,
    ToolDefinitionError,
    ToolModeError,
    ToolRegistry,
    ToolRuntime,
    ToolSchemaError,
    ToolSpec,
    error_text,
    truncate_text,
    validate_modes,
    validate_parameters,
)
from .command import (
    DEFAULT_OUTPUT_LIMIT,
    DEFAULT_TIMEOUT_S,
    ENV_KEYS,
    ENV_PREFIX,
    build_command_tools,
    build_env,
    render_result,
    run_argv,
)
from .document import build_document_tools, document_header
from .fs import (
    DEFAULT_READ_LIMIT,
    DEFAULT_SEARCH_LIMIT,
    build_fs_tools,
    make_edit_file,
    make_glob_files,
    make_grep_files,
    make_list_dir,
    make_read_file,
    make_write_file,
)
from .life import build_life_tools
from .mcp import (
    MCP_PREFIX,
    MCP_SEPARATOR,
    McpRegistry,
    McpServer,
    McpToolDescriptor,
    mcp_prompt_text,
    mcp_tool_name,
    parse_mcp_tool_name,
    register_mcp_server,
)
from .notes import build_note_tools, extract_tags, extract_title, split_frontmatter
from .web import (
    UNTRUSTED_NOTICE,
    WebFetchTool,
    build_web_tools,
)
from .web import (
    BingAPISearch,
    BingHTMLSearch,
    BochaSearch,
    DDGHTMLSearch,
    SearchBackend,
    SearchManager,
    SerperSearch,
    TavilySearch,
    WebSearchTool,
    format_search_results,
)


# ----------------------------------------------------------------------------
# 当前时间（既有内置工具，保留原名与输出格式）
# ----------------------------------------------------------------------------

def tool_get_current_time() -> str:
    """查询当前日期和时间（输出格式与 ai.tool_get_current_time 完全一致）。"""
    from datetime import datetime as _datetime

    now = _datetime.now()
    return now.strftime("当前本地时间：%Y-%m-%d %H:%M:%S（%A）")


def build_time_tools(context: Optional[ToolContext] = None) -> List[ToolSpec]:
    return [
        ToolSpec(
            name="get_current_time",
            description="查询当前的日期和时间（含星期几）。当用户问现在几点、今天几号等时使用。",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            handler=tool_get_current_time,
            read_only=True,
            parallel_safe=True,
            kind=TOOL_KIND_GENERIC,
        ),
    ]


# ----------------------------------------------------------------------------
# 默认注册表
# ----------------------------------------------------------------------------

def build_default_registry(
    config: Optional[Mapping[str, Any]] = None,
    *,
    context: Optional[ToolContext] = None,
    mcp: Optional[McpRegistry] = None,
) -> ToolRegistry:
    """构造第一期的默认工具注册表。

    参数：
        config：完整配置（读 search / tools 两节）；
        context：工具执行上下文（工作区、沙箱、网关）；不传则用一个只读的
                 空上下文（此时文件工具会拒绝所有越界访问）；
        mcp：MCP 登记表；为空时不注册任何 MCP 工具、也不产生提示词文本。
    """
    settings = dict(config or {})
    tool_settings = settings.get("tools") if isinstance(settings.get("tools"), Mapping) else {}
    enabled = set(str(item) for item in (tool_settings.get("enabled") or ()))
    disabled = set(str(item) for item in (tool_settings.get("disabled") or ()))

    ctx = context if context is not None else ToolContext()
    registry = ToolRegistry()

    groups = [
        ("time", build_time_tools(ctx)),
        ("web", build_web_tools(settings.get("search") or {}, ctx)),
        ("fs", build_fs_tools(ctx)),
        ("command", build_command_tools(ctx)),
        ("document", build_document_tools(ctx)),
        ("notes", build_note_tools(ctx)),
        ("life", build_life_tools(ctx)),
    ]
    for group_name, specs in groups:
        if enabled and group_name not in enabled:
            # 显式声明了 enabled 清单时，未列出的组不注册（opt-in）
            continue
        if group_name in disabled:
            continue
        for spec in specs:
            if spec.name in disabled:
                continue
            registry.register(spec)

    if mcp is not None:
        mcp.register_into(registry)
    return registry


__all__ = [
    # base
    "ALLOWED_SCHEMA_KEYS", "ALLOWED_SCHEMA_TYPES", "DEFAULT_RESULT_LIMIT",
    "DEFAULT_TOOL_MODES", "ERROR_PREFIX", "KNOWN_MODES", "KNOWN_SOURCES",
    "SOURCE_BUILTIN", "SOURCE_MCP", "SOURCE_USER",
    "TOOL_KINDS", "TOOL_KIND_ACTION", "TOOL_KIND_COMMAND", "TOOL_KIND_GENERIC",
    "TOOL_KIND_NETWORK", "TOOL_KIND_WRITE",
    "ToolContext", "ToolDefinitionError", "ToolModeError", "ToolRegistry",
    "ToolRuntime", "ToolSchemaError", "ToolSpec",
    "error_text", "truncate_text", "validate_modes", "validate_parameters",
    # command
    "DEFAULT_OUTPUT_LIMIT", "DEFAULT_TIMEOUT_S", "ENV_KEYS", "ENV_PREFIX",
    "build_command_tools", "build_env", "render_result", "run_argv",
    # document
    "build_document_tools", "document_header",
    # fs
    "DEFAULT_READ_LIMIT", "DEFAULT_SEARCH_LIMIT", "build_fs_tools",
    "make_edit_file", "make_glob_files", "make_grep_files", "make_list_dir",
    "make_read_file", "make_write_file",
    # life
    "build_life_tools",
    # mcp
    "MCP_PREFIX", "MCP_SEPARATOR", "McpRegistry", "McpServer",
    "McpToolDescriptor", "mcp_prompt_text", "mcp_tool_name",
    "parse_mcp_tool_name", "register_mcp_server",
    # notes
    "build_note_tools", "extract_tags", "extract_title", "split_frontmatter",
    # web
    "UNTRUSTED_NOTICE", "WebFetchTool", "build_web_tools",
    "BingAPISearch", "BingHTMLSearch", "BochaSearch", "DDGHTMLSearch",
    "SearchBackend", "SearchManager", "SerperSearch", "TavilySearch",
    "WebSearchTool", "format_search_results",
    # time + 工厂
    "tool_get_current_time", "build_time_tools", "build_default_registry",
]
