"""多模型厂商能力表与分级降级判定（目标 7）。

本模块对应 docs/refactor/01-gap-matrix.md 第 7 节：不止「都走 OpenAI 兼容」，
而是按厂商声明能力，并据此决定功能可用性与参数裁剪。

对齐的 DSH 事实（docs/refactor/04-dsh-harness-notes.md 0.1 节）：
* dsh-llm 通过适配器流式调用、发现模型、解析**模型能力与调用默认值**；
* dsh-agent-loop 的 prepareCall() 在活跃轮次下校验适配器持有的字段、
  解析推理强度与输出 token 默认值。

分级（tier）由「结构化输出」与「Function Calling」两个维度决定，
降级链固定为 native -> json -> text（见 ToolProtocol）：

* native：厂商原生 function calling（请求体带 tools）；
* json：不支持 function calling，**退回提示词内的 JSON 约定**
  （见 json_protocol.py）；
* text：连结构化输出都不可用，只做纯文本回复（工具不可见）。

解析优先级（与既有 AI.supports_vision 的顺序保持一致，保证不回归）：
显式配置 > 厂商表 > 默认值。config.yaml 里已有的 vision: true 仍然生效。

设计约束（红线 D）：本模块只依赖标准库，不得 import ui / PySide6 / ai。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, fields, replace
from typing import Any, Dict, FrozenSet, Mapping, Optional, Tuple


# ----------------------------------------------------------------------------
# 工具协议级别（分级降级的三档）
# ----------------------------------------------------------------------------

TOOL_PROTOCOL_NATIVE = "native"
TOOL_PROTOCOL_JSON = "json"
TOOL_PROTOCOL_TEXT = "text"

TOOL_PROTOCOLS: Tuple[str, ...] = (
    TOOL_PROTOCOL_NATIVE, TOOL_PROTOCOL_JSON, TOOL_PROTOCOL_TEXT,
)

#: 降级链：从最理想的一档开始，逐级回退
TOOL_PROTOCOL_FALLBACK: Dict[str, Optional[str]] = {
    TOOL_PROTOCOL_NATIVE: TOOL_PROTOCOL_JSON,
    TOOL_PROTOCOL_JSON: TOOL_PROTOCOL_TEXT,
    TOOL_PROTOCOL_TEXT: None,
}


def next_protocol(protocol: str) -> Optional[str]:
    """降级链的下一档；已是最后一档返回 None。"""
    return TOOL_PROTOCOL_FALLBACK.get(str(protocol))


# ----------------------------------------------------------------------------
# 模型能力
# ----------------------------------------------------------------------------

@dataclass(frozen=True)
class ModelCapabilities:
    """一个 provider/model 的能力声明。

    所有字段都有故障安全默认值：**默认不假设任何高级能力**——
    supports_tools 为 True 是因为这是当前所有已接入厂商的现状
    （既有实现无条件传 tools），其余高级能力一律默认关闭。
    """

    context_window: int = 32_000
    max_output_tokens: int = 4_096
    supports_tools: bool = True
    supports_structured_output: bool = False
    supports_stream_tool_calls: bool = True
    supports_vision: bool = False
    supports_reasoning_effort: bool = False
    supports_parallel_tool_calls: bool = False
    supports_system_prompt: bool = True
    tier: str = "openai-compatible"

    # ---- 分级 ----

    def protocol(self, *, tools_requested: bool = True) -> str:
        """本能力下实际使用的工具协议级别。

        * 没有工具需求 -> text（不带 tools 键，也不注入 JSON 约定）；
        * 原生 function calling 可用 -> native；
        * 支持结构化输出但无 function calling -> json（提示词内 JSON 约定）；
        * 两者都不支持 -> text。
        """
        if not tools_requested:
            return TOOL_PROTOCOL_TEXT
        if self.supports_tools:
            return TOOL_PROTOCOL_NATIVE
        if self.supports_structured_output:
            return TOOL_PROTOCOL_JSON
        return TOOL_PROTOCOL_TEXT

    def degraded(self, level: str) -> str:
        """强行降到某一档（调用失败后的降级路径使用）。"""
        if level not in TOOL_PROTOCOLS:
            raise ValueError("未知工具协议级别 " + repr(level))
        return level

    # ---- 预算 ----

    @property
    def input_budget(self) -> int:
        """留给输入消息的 token 预算。"""
        return max(0, int(self.context_window) - int(self.max_output_tokens))

    def fits(self, estimated_input_tokens: int) -> bool:
        return int(estimated_input_tokens) <= self.input_budget

    def to_dict(self) -> Dict[str, Any]:
        return {field.name: getattr(self, field.name) for field in fields(self)}


#: 常见的「模型名 -> 能力」修正规则（在厂商表之上叠加）。
#: 只按模型名做**保守**修正：能加能力的地方加，不能确定的一律不加。
_MODEL_OVERRIDES: Tuple[Tuple[str, Dict[str, Any]], ...] = (
    (r"(^|[-_/])(vl|vision|multimodal)([-_/]|$)", {"supports_vision": True}),
    (r"gpt-4o|gpt-4-turbo|gpt-4\.1|o[134](-|$)", {
        "supports_vision": True, "supports_structured_output": True,
        "supports_parallel_tool_calls": True, "context_window": 128_000,
    }),
    (r"claude-3|claude-4|claude-sonnet|claude-opus|claude-haiku", {
        "supports_vision": True, "context_window": 200_000,
    }),
    (r"gemini", {"supports_vision": True, "context_window": 1_000_000}),
    (r"deepseek-reasoner|deepseek-r1|\br1\b", {
        "supports_reasoning_effort": True, "supports_tools": True,
    }),
    (r"qwen(3)?(-|/)?(max|plus|turbo|vl)", {"supports_vision": True}),
    (r"llama|mistral|mixtral|qwen", {"supports_tools": True}),
)


# ----------------------------------------------------------------------------
# 厂商能力表
# ----------------------------------------------------------------------------

#: 内置厂商表（键一律小写；别名见 PROVIDER_ALIASES）
PROVIDER_TIERS: Dict[str, ModelCapabilities] = {
    "openai": ModelCapabilities(
        context_window=128_000,
        max_output_tokens=16_384,
        supports_tools=True,
        supports_structured_output=True,
        supports_stream_tool_calls=True,
        supports_vision=True,
        supports_reasoning_effort=True,
        supports_parallel_tool_calls=True,
        tier="openai",
    ),
    "deepseek": ModelCapabilities(
        context_window=64_000,
        max_output_tokens=8_192,
        supports_tools=True,
        supports_structured_output=False,
        supports_stream_tool_calls=True,
        supports_vision=False,
        supports_reasoning_effort=True,
        supports_parallel_tool_calls=False,
        tier="deepseek",
    ),
    "aliyun": ModelCapabilities(
        context_window=131_072,
        max_output_tokens=8_192,
        supports_tools=True,
        supports_structured_output=False,
        supports_stream_tool_calls=True,
        supports_vision=True,
        supports_reasoning_effort=False,
        supports_parallel_tool_calls=False,
        tier="aliyun",
    ),
    "anthropic": ModelCapabilities(
        context_window=200_000,
        max_output_tokens=8_192,
        supports_tools=True,
        supports_structured_output=False,
        supports_stream_tool_calls=True,
        supports_vision=True,
        supports_reasoning_effort=False,
        supports_parallel_tool_calls=True,
        tier="anthropic",
    ),
    "custom": ModelCapabilities(tier="custom"),
    # 明确声明的「无 function calling」档：用于接入只提供纯文本接口的模型，
    # 走提示词内 JSON 约定（json 协议）而不是直接失去工具能力。
    "json_only": ModelCapabilities(
        context_window=32_000,
        supports_tools=False,
        supports_structured_output=True,
        supports_stream_tool_calls=False,
        tier="json-only",
    ),
    # 最保守的一档：无工具、无结构化输出，只做纯文本
    "text_only": ModelCapabilities(
        context_window=16_000,
        supports_tools=False,
        supports_structured_output=False,
        supports_stream_tool_calls=False,
        tier="text-only",
    ),
}

#: 厂商别名（配置里写 deepseek-chat / dashscope 之类也能解析到正确的表项）
PROVIDER_ALIASES: Dict[str, str] = {
    "openai-compatible": "custom",
    "openai_compatible": "custom",
    "azure": "openai",
    "azure-openai": "openai",
    "dashscope": "aliyun",
    "qwen": "aliyun",
    "tongyi": "aliyun",
    "moonshot": "custom",
    "kimi": "custom",
    "zhipu": "custom",
    "glm": "custom",
    "siliconflow": "custom",
    "ollama": "custom",
    "vllm": "custom",
    "oneapi": "custom",
    "newapi": "custom",
    "anthropic-claude": "anthropic",
    "claude": "anthropic",
}

#: 能力的显式配置键 -> ModelCapabilities 字段名
_CONFIG_KEYS: Dict[str, str] = {
    "context_window": "context_window",
    "contextwindow": "context_window",
    "max_output_tokens": "max_output_tokens",
    "max_output": "max_output_tokens",
    "max_tokens": "max_output_tokens",
    "supports_tools": "supports_tools",
    "tools": "supports_tools",
    "function_calling": "supports_tools",
    "supports_structured_output": "supports_structured_output",
    "structured_output": "supports_structured_output",
    "json_mode": "supports_structured_output",
    "supports_stream_tool_calls": "supports_stream_tool_calls",
    "stream_tool_calls": "supports_stream_tool_calls",
    "supports_vision": "supports_vision",
    "vision": "supports_vision",
    "supports_reasoning_effort": "supports_reasoning_effort",
    "reasoning": "supports_reasoning_effort",
    "reasoning_effort": "supports_reasoning_effort",
    "supports_parallel_tool_calls": "supports_parallel_tool_calls",
    "parallel_tool_calls": "supports_parallel_tool_calls",
    "supports_system_prompt": "supports_system_prompt",
    "system_prompt": "supports_system_prompt",
    "tier": "tier",
    "protocol": "tier",
}

_BOOL_FIELDS: FrozenSet[str] = frozenset({
    "supports_tools", "supports_structured_output", "supports_stream_tool_calls",
    "supports_vision", "supports_reasoning_effort", "supports_parallel_tool_calls",
    "supports_system_prompt",
})
_INT_FIELDS: FrozenSet[str] = frozenset({"context_window", "max_output_tokens"})


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in ("0", "false", "no", "off", "none", "")


def _to_int(value: Any, default: int) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def vendor_key(provider_name: Any) -> str:
    """把 provider 名归一化成厂商表的键（未知则回落到 custom）。"""
    text = str(provider_name or "").strip().lower()
    if text in PROVIDER_TIERS:
        return text
    if text in PROVIDER_ALIASES:
        return PROVIDER_ALIASES[text]
    # 形如 deepseek-chat 的配置名：取第一段再试一次
    head = re.split(r"[-_/]", text)[0]
    if head in PROVIDER_TIERS:
        return head
    if head in PROVIDER_ALIASES:
        return PROVIDER_ALIASES[head]
    return "custom"


def known_providers() -> Tuple[str, ...]:
    """内置厂商表里的名字（排序后，供错误信息与 UI 展示）。"""
    return tuple(sorted(PROVIDER_TIERS))


def capabilities_from_config(config: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """从 provider 配置里抽出显式能力声明。"""
    overrides: Dict[str, Any] = {}
    for key, value in dict(config or {}).items():
        field_name = _CONFIG_KEYS.get(str(key).strip().lower())
        if not field_name or value is None:
            continue
        if field_name in _BOOL_FIELDS:
            overrides[field_name] = _to_bool(value)
        elif field_name in _INT_FIELDS:
            overrides[field_name] = _to_int(value, getattr(ModelCapabilities(), field_name))
        else:
            overrides[field_name] = str(value)
    return overrides


def model_overrides(model_name: Any) -> Dict[str, Any]:
    """按模型名做的保守能力修正（厂商表之上、显式配置之下）。"""
    text = str(model_name or "").strip().lower()
    if not text:
        return {}
    result: Dict[str, Any] = {}
    for pattern, patch in _MODEL_OVERRIDES:
        if re.search(pattern, text):
            result.update(patch)
    return result


def resolve_capabilities(
    provider_name: Any = None,
    provider_config: Optional[Mapping[str, Any]] = None,
    *,
    model: Any = None,
) -> ModelCapabilities:
    """解析某个 provider 的能力：显式配置 > 厂商表 > 默认值。

    与既有 AI.supports_vision 的解析顺序保持一致（配置优先、名称/表兜底），
    因此 config.yaml 里已有的 vision: true 仍然生效，不产生回归。
    """
    config = dict(provider_config or {})
    key = vendor_key(provider_name)
    base = PROVIDER_TIERS.get(key, PROVIDER_TIERS["custom"])

    merged = base.to_dict()
    model_name = model if model is not None else config.get("model")
    merged.update(model_overrides(model_name))
    merged.update(capabilities_from_config(config))
    if not merged.get("tier") or merged.get("tier") == base.tier:
        merged["tier"] = base.tier
    return ModelCapabilities(**{
        field.name: merged[field.name] for field in fields(ModelCapabilities)
    })


# ----------------------------------------------------------------------------
# 上下文预算
# ----------------------------------------------------------------------------

class ContextWindowExceeded(ValueError):
    """上下文超限——**明确拒绝**，绝不静默截断（目标 7 的验收条款）。"""

    code = "CONTEXT_WINDOW_EXCEEDED"

    def __init__(self, message: str, *, estimated: int = 0, budget: int = 0,
                 provider: str = "", model: str = "") -> None:
        super().__init__(message)
        self.estimated = int(estimated)
        self.budget = int(budget)
        self.provider = provider
        self.model = model

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "message": str(self),
            "estimated_input_tokens": self.estimated,
            "input_budget": self.budget,
            "provider": self.provider,
            "model": self.model,
        }


def estimate_tokens(text: Any) -> int:
    """粗估 token 数。

    保守策略：中日韩字符按 1 字符 ≈ 1 token，其余按 4 字符 ≈ 1 token
    （宁可高估触发拒绝，也不低估导致真实超限）。
    """
    content = "" if text is None else str(text)
    if not content:
        return 0
    wide = sum(
        1 for char in content
        if "\u2e80" <= char <= "\u9fff" or "\uff00" <= char <= "\uffef"
    )
    narrow = len(content) - wide
    return wide + max(1, math.ceil(narrow / 4)) if narrow else wide


def estimate_message_tokens(messages: Any) -> int:
    """估算一组 OpenAI 格式消息的总 token（含每条消息的固定开销）。"""
    total = 0
    for message in messages or ():
        if not isinstance(message, Mapping):
            total += estimate_tokens(message)
            continue
        total += 4  # role / 分隔符的固定开销
        total += estimate_tokens(message.get("content"))
        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, (list, tuple)):
            total += estimate_tokens(str(tool_calls))
        if message.get("name"):
            total += estimate_tokens(message.get("name"))
    return total
