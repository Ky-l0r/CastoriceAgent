"""多模型厂商分级适配层（目标 7）。

按厂商声明能力（结构化输出 / Function Calling / 视觉 / 推理强度 /
上下文窗口），并据此决定功能可用性与参数裁剪；不支持 function calling 时
退回提示词内的 JSON 约定（json_protocol），调用失败有可读错误与降级路径。

模块划分：

* capabilities.py  —— 厂商能力表、ModelCapabilities、上下文预算与显式拒绝；
* json_protocol.py —— 提示词内 JSON 约定的声明与解析（native 不可用时的降级档）；
* adapter.py       —— ProviderAdapter：能力 -> 请求体裁剪，统一的完成接口，
                      以及可读错误与降级链。

设计约束（红线 D）：本包只依赖标准库与 castorice 的 modes/harness/permissions，
不得 import ui / PySide6 / ai。
"""

from .adapter import (
    CallOutcome,
    PreparedRequest,
    ProviderAdapter,
    ProviderCallError,
    ProviderError,
    install_json_protocol,
    prepare_request,
    resolve_adapter,
)
from .capabilities import (
    TOOL_PROTOCOL_FALLBACK,
    TOOL_PROTOCOL_JSON,
    TOOL_PROTOCOL_NATIVE,
    TOOL_PROTOCOL_TEXT,
    TOOL_PROTOCOLS,
    ContextWindowExceeded,
    ModelCapabilities,
    PROVIDER_ALIASES,
    PROVIDER_TIERS,
    capabilities_from_config,
    estimate_message_tokens,
    estimate_tokens,
    known_providers,
    model_overrides,
    next_protocol,
    resolve_capabilities,
    vendor_key,
)
from .json_protocol import (
    JSON_PROTOCOL_TEMPLATE,
    JSON_PROTOCOL_VERSION,
    JsonToolCall,
    build_json_protocol_instructions,
    looks_like_json_protocol,
    parse_json_tool_calls,
    render_tool_catalog,
)

__all__ = [
    # capabilities
    "TOOL_PROTOCOL_FALLBACK", "TOOL_PROTOCOL_JSON", "TOOL_PROTOCOL_NATIVE",
    "TOOL_PROTOCOL_TEXT", "TOOL_PROTOCOLS",
    "ContextWindowExceeded", "ModelCapabilities",
    "PROVIDER_ALIASES", "PROVIDER_TIERS",
    "capabilities_from_config", "estimate_message_tokens", "estimate_tokens",
    "known_providers", "model_overrides", "next_protocol",
    "resolve_capabilities", "vendor_key",
    # json_protocol
    "JSON_PROTOCOL_TEMPLATE", "JSON_PROTOCOL_VERSION", "JsonToolCall",
    "build_json_protocol_instructions", "looks_like_json_protocol",
    "parse_json_tool_calls", "render_tool_catalog",
    # adapter
    "CallOutcome", "PreparedRequest", "ProviderAdapter", "ProviderCallError",
    "ProviderError", "install_json_protocol", "prepare_request", "resolve_adapter",
]
