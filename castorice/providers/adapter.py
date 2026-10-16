"""厂商适配器：能力 -> 请求体裁剪 + 统一完成接口 + 分级降级。

本模块把 ModelCapabilities 落到真实调用上（对齐 dsh-agent-loop 的
prepareCall()：在活跃轮次下校验适配器持有的字段、解析推理强度与输出 token
默认值），并实现「调用失败有可读错误与降级路径」这条验收条款。

零依赖接入：适配器只要求被包装的 provider 暴露

    chat_completion(messages, stream=..., **kwargs) -> (text, tool_calls)

这正是既有 ai.OpenAICompatibleProvider 的形态，因此
OpenAICompatibleProvider 与 LLMProviderFactory 的行为完全不变，
只是在外面多了一层能力感知的体裁剪与降级（不回归）。

设计约束（红线 D）：本模块只依赖标准库，不得 import ui / PySide6 / ai。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from .capabilities import (
    TOOL_PROTOCOL_JSON,
    TOOL_PROTOCOL_NATIVE,
    TOOL_PROTOCOL_TEXT,
    ContextWindowExceeded,
    ModelCapabilities,
    estimate_message_tokens,
    next_protocol,
    resolve_capabilities,
)
from .json_protocol import (
    JsonToolCall,
    build_json_protocol_instructions,
    parse_json_tool_calls,
)


# ----------------------------------------------------------------------------
# 错误
# ----------------------------------------------------------------------------

class ProviderError(RuntimeError):
    """厂商层错误基类。to_dict() 给出结构化错误（UI 可直接展示）。"""

    code = "PROVIDER_ERROR"

    def __init__(self, message: str, *, provider: str = "", model: str = "",
                 code: Optional[str] = None, cause: Any = None) -> None:
        super().__init__(message)
        self.provider = provider
        self.model = model
        self.cause = cause
        if code:
            self.code = code

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "message": str(self),
            "provider": self.provider,
            "model": self.model,
        }


class ProviderCallError(ProviderError):
    """一次模型调用失败（可读错误 + 可降级标记）。"""

    code = "PROVIDER_CALL_FAILED"

    def __init__(self, message: str, *, provider: str = "", model: str = "",
                 protocol: str = "", retryable: bool = False,
                 degrade_to: Optional[str] = None, cause: Any = None) -> None:
        super().__init__(message, provider=provider, model=model, cause=cause)
        self.protocol = protocol
        self.retryable = retryable
        self.degrade_to = degrade_to

    def to_dict(self) -> Dict[str, Any]:
        payload = super().to_dict()
        payload.update({
            "protocol": self.protocol,
            "retryable": self.retryable,
            "degrade_to": self.degrade_to,
        })
        return payload


class ProviderConfigurationError(ProviderError):
    """厂商配置非法（未知 provider / 缺 api_key）——启动期即报错。"""

    code = "PROVIDER_CONFIG_INVALID"


# ----------------------------------------------------------------------------
# 请求体裁剪
# ----------------------------------------------------------------------------

@dataclass(frozen=True)
class PreparedRequest:
    """裁剪后的请求体与本次调用的元信息。"""

    kwargs: Dict[str, Any]
    protocol: str
    capabilities: ModelCapabilities
    estimated_input_tokens: int = 0
    notes: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kwargs_keys": sorted(self.kwargs),
            "protocol": self.protocol,
            "estimated_input_tokens": self.estimated_input_tokens,
            "notes": list(self.notes),
        }


def prepare_request(
    capabilities: ModelCapabilities,
    messages: Sequence[Mapping[str, Any]],
    *,
    tools: Optional[Sequence[Any]] = None,
    stream: bool = True,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    reasoning_effort: Optional[str] = None,
    provider_name: str = "",
    extra: Optional[Mapping[str, Any]] = None,
    protocol: Optional[str] = None,
    enforce_budget: bool = True,
) -> PreparedRequest:
    """按能力裁剪请求体。

    * 工具：native -> 带 tools 键；json / text -> **不带 tools 键**
      （json 档改走提示词约定，text 档直接没有工具能力）；
    * 工具为空的 native 场景同样**不带 tools 键**（不是 tools=[]）；
    * temperature / max_tokens 只在显式给出时带上，否则沿用 provider 配置；
    * reasoning_effort 只在模型声明支持时带上；
    * 上下文超限时抛 ContextWindowExceeded（**明确拒绝**，不静默截断）。
    """
    notes: List[str] = []
    effective = protocol or capabilities.protocol(tools_requested=bool(tools))
    if effective == TOOL_PROTOCOL_NATIVE and not capabilities.supports_tools:
        notes.append("模型声明不支持 function calling，native 协议不可用")
        effective = capabilities.protocol(tools_requested=True)

    kwargs: Dict[str, Any] = {
        "messages": list(messages),
        "stream": bool(stream),
    }
    if model:
        kwargs["model"] = model
    if temperature is not None:
        kwargs["temperature"] = temperature
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    elif capabilities.max_output_tokens:
        kwargs["max_tokens"] = capabilities.max_output_tokens

    if reasoning_effort is not None:
        if capabilities.supports_reasoning_effort:
            kwargs["reasoning_effort"] = reasoning_effort
        else:
            notes.append("模型不支持 reasoning_effort，已省略该字段")

    specs = list(tools or ())
    if effective == TOOL_PROTOCOL_NATIVE and specs:
        kwargs["tools"] = specs
    else:
        kwargs.pop("tools", None)
        if specs and effective == TOOL_PROTOCOL_JSON:
            notes.append("已改用提示词内 JSON 约定（不带 tools 键）")
        elif specs and effective == TOOL_PROTOCOL_TEXT:
            notes.append("模型无工具能力，工具不可用")
    if not specs:
        kwargs.pop("tools", None)

    for key, value in dict(extra or {}).items():
        if value is not None:
            kwargs[key] = value

    estimated = estimate_message_tokens(messages)
    if enforce_budget and not capabilities.fits(estimated):
        raise ContextWindowExceeded(
            "上下文超出模型可用窗口：估算输入 " + str(estimated)
            + " tokens，可用预算 " + str(capabilities.input_budget)
            + " tokens（context_window=" + str(capabilities.context_window)
            + ", max_output_tokens=" + str(capabilities.max_output_tokens)
            + "）；请缩减历史或改用更大窗口的模型（不会静默截断）",
            estimated=estimated,
            budget=capabilities.input_budget,
            provider=provider_name,
            model=str(kwargs.get("model", "") or ""),
        )

    return PreparedRequest(
        kwargs=kwargs,
        protocol=effective,
        capabilities=capabilities,
        estimated_input_tokens=estimated,
        notes=tuple(notes),
    )


# ----------------------------------------------------------------------------
# 调用结果
# ----------------------------------------------------------------------------

@dataclass(frozen=True)
class CallOutcome:
    """一次完成调用的结果（文本 + 工具调用 + 降级痕迹）。"""

    text: str = ""
    tool_calls: Tuple[Dict[str, Any], ...] = ()
    protocol: str = TOOL_PROTOCOL_NATIVE
    degraded: bool = False
    degrade_reason: str = ""
    error: str = ""
    attempts: int = 1

    @property
    def ok(self) -> bool:
        return not self.error

    @property
    def used_tools(self) -> bool:
        return bool(self.tool_calls)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "protocol": self.protocol,
            "degraded": self.degraded,
            "degrade_reason": self.degrade_reason,
            "error": self.error,
            "attempts": self.attempts,
            "tool_calls": len(self.tool_calls),
        }


# ----------------------------------------------------------------------------
# 适配器
# ----------------------------------------------------------------------------

class ProviderAdapter:
    """把「能力感知 + 分级降级」包在任意 chat_completion 实现外面。

    构造参数 provider 需要提供 chat_completion(messages, stream=..., **kwargs)，
    这正是既有 ai.OpenAICompatibleProvider 的形态（不回归）。
    """

    def __init__(
        self,
        provider: Any,
        capabilities: Optional[ModelCapabilities] = None,
        *,
        provider_name: str = "",
        model: Optional[str] = None,
        config: Optional[Mapping[str, Any]] = None,
        logger: Any = None,
    ) -> None:
        self.provider = provider
        self.provider_name = str(provider_name or "")
        self.config = dict(config or {})
        self.logger = logger
        self.capabilities = capabilities if capabilities is not None else \
            resolve_capabilities(
                self.provider_name, self.config,
                model=model if model is not None else self.config.get("model"),
            )
        self.model = str(
            model if model is not None else self.config.get("model", "")
        )
        #: 当前生效的协议（调用失败降级后会下调）
        self.protocol = self.capabilities.protocol(tools_requested=True)
        self.notes: List[str] = []
        self.last_prepared: Optional[PreparedRequest] = None

    # ---- 能力查询 ----

    @property
    def supports_tools(self) -> bool:
        return bool(self.capabilities.supports_tools)

    @property
    def supports_vision(self) -> bool:
        return bool(self.capabilities.supports_vision)

    @property
    def context_window(self) -> int:
        return int(self.capabilities.context_window)

    def capability_summary(self) -> Dict[str, Any]:
        """供 UI / 诊断展示的能力摘要（tier 与协议级别都给出）。"""
        payload = self.capabilities.to_dict()
        payload.update({
            "provider": self.provider_name,
            "model": self.model,
            "protocol": self.protocol,
            "degraded": self.protocol != TOOL_PROTOCOL_NATIVE,
            "notes": list(self.notes),
        })
        return payload

    # ---- 提示词注入 ----

    def protocol_instructions(self, tool_specs: Sequence[Any]) -> str:
        """按当前协议生成需要注入系统提示词的工具说明。

        native 协议不需要额外文本（schema 由 tools 参数承载）；
        json 协议注入 JSON 约定；text 协议不注入任何文本。
        """
        if self.protocol == TOOL_PROTOCOL_JSON:
            return build_json_protocol_instructions(tool_specs)
        return ""

    def system_prompt_with_protocol(
        self, system_prompt: str, tool_specs: Sequence[Any]
    ) -> str:
        extra = self.protocol_instructions(tool_specs)
        if not extra:
            return system_prompt
        return (system_prompt or "") + extra

    # ---- 降级 ----

    def degrade(self, reason: str = "", *, target: Optional[str] = None) -> str:
        """下调一级协议（native -> json -> text），返回新协议。"""
        target = target or next_protocol(self.protocol) or self.protocol
        if target == self.protocol:
            return self.protocol
        self.protocol = target
        if reason:
            self.notes.append("降级到 " + target + "：" + reason)
        if self.logger is not None and callable(getattr(self.logger, "warning", None)):
            self.logger.warning(
                "provider " + (self.provider_name or "?") + " 降级到 "
                + target + "（" + (reason or "未说明原因") + "）"
            )
        return self.protocol

    def reset(self) -> None:
        """恢复到能力表决定的初始协议（用户主动重试时用）。"""
        self.protocol = self.capabilities.protocol(tools_requested=True)
        self.notes.clear()

    def _should_degrade(self, error: Exception) -> bool:
        """判断错误是否属于「原生工具协议不兼容」这一类，可降级重试。

        只对明确的能力类错误降级（unsupported / not support / tools 400），
        网络与鉴权错误不降级——那不是能力问题，重试也没意义。
        """
        if self.protocol != TOOL_PROTOCOL_NATIVE:
            return False
        text = str(error).lower()
        markers = (
            "does not support tool", "not support tool", "tool_calls is not",
            "unsupported parameter", "unknown parameter", "tools is not",
            "function calling", "function_call", "unexpected keyword argument 'tools'",
            "invalid_request_error",
        )
        return any(marker in text for marker in markers)

    # ---- 调用 ----

    def complete(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        tool_specs: Optional[Sequence[Any]] = None,
        stream: bool = True,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        reasoning_effort: Optional[str] = None,
        extra: Optional[Mapping[str, Any]] = None,
        allow_degrade: bool = True,
        enforce_budget: bool = True,
    ) -> CallOutcome:
        """执行一次完成调用；失败给出可读错误，必要时自动降一级再试一次。

        Returns:
            CallOutcome。error 非空表示最终失败（**不抛异常**），
            degrade_reason 说明是否发生过降级。
        """
        specs = list(tool_specs or ())
        degrade_reason = ""
        attempts = 0

        while True:
            attempts += 1
            try:
                prepared = prepare_request(
                    self.capabilities,
                    messages,
                    tools=specs,
                    stream=stream,
                    model=self.model or None,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    reasoning_effort=reasoning_effort,
                    provider_name=self.provider_name,
                    extra=extra,
                    protocol=self.protocol,
                    enforce_budget=enforce_budget,
                )
            except ContextWindowExceeded as exc:
                return CallOutcome(
                    text="", tool_calls=(), protocol=self.protocol,
                    degraded=bool(degrade_reason), degrade_reason=degrade_reason,
                    error=str(exc), attempts=attempts,
                )

            self.last_prepared = prepared
            runner = getattr(self.provider, "chat_completion", None)
            if not callable(runner):
                return CallOutcome(
                    text="", tool_calls=(), protocol=prepared.protocol,
                    degraded=bool(degrade_reason), degrade_reason=degrade_reason,
                    error="provider 未实现 chat_completion，无法调用（"
                          + type(self.provider).__name__ + "）",
                    attempts=attempts,
                )

            try:
                kwargs = dict(prepared.kwargs)
                text, tool_calls = runner(**kwargs)
            except Exception as exc:
                error = ProviderCallError(
                    "模型调用失败（provider=" + (self.provider_name or "?")
                    + ", protocol=" + prepared.protocol + "）：" + str(exc),
                    provider=self.provider_name,
                    model=self.model,
                    protocol=prepared.protocol,
                    retryable=self._should_degrade(exc),
                    degrade_to=next_protocol(prepared.protocol),
                    cause=exc,
                )
                if allow_degrade and self._should_degrade(exc):
                    new_protocol = self.degrade(str(exc))
                    degrade_reason = str(exc)
                    if new_protocol != prepared.protocol:
                        continue
                return CallOutcome(
                    text="", tool_calls=(), protocol=prepared.protocol,
                    degraded=bool(degrade_reason), degrade_reason=degrade_reason,
                    error=str(error), attempts=attempts,
                )

            normalised = tuple(dict(call) for call in (tool_calls or ()))
            if prepared.protocol == TOOL_PROTOCOL_JSON and not normalised:
                parsed, remainder = parse_json_tool_calls(text)
                if parsed:
                    normalised = tuple(
                        call.to_openai(index) for index, call in enumerate(parsed)
                    )
                    text = remainder
            return CallOutcome(
                text=str(text or ""),
                tool_calls=normalised,
                protocol=prepared.protocol,
                degraded=bool(degrade_reason) or prepared.protocol != TOOL_PROTOCOL_NATIVE,
                degrade_reason=degrade_reason,
                attempts=attempts,
            )

    # ---- 诊断 ----

    def call(self, messages: Sequence[Mapping[str, Any]], **kwargs: Any) -> Tuple[str, List[Dict[str, Any]]]:
        """与既有 provider.chat_completion 同形的便捷入口（失败返回空结果）。"""
        outcome = self.complete(messages, **kwargs)
        if not outcome.ok:
            return "", []
        return outcome.text, [dict(call) for call in outcome.tool_calls]


def resolve_adapter(
    provider: Any,
    provider_name: str = "",
    provider_config: Optional[Mapping[str, Any]] = None,
    *,
    logger: Any = None,
) -> ProviderAdapter:
    """为任意 provider 实例建一个能力感知的适配器（ai.py 的接线点）。"""
    config = dict(provider_config or {})
    return ProviderAdapter(
        provider,
        resolve_capabilities(provider_name, config),
        provider_name=provider_name,
        config=config,
        logger=logger,
    )


def install_json_protocol(
    adapter: ProviderAdapter,
    system_prompt: str,
    tool_specs: Sequence[Any],
) -> str:
    """把当前协议需要的提示词说明拼进系统提示词（无说明时原样返回）。"""
    return adapter.system_prompt_with_protocol(system_prompt, tool_specs)
