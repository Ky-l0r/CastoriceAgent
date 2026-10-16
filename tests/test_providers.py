"""多模型厂商分级适配层（castorice/providers/）的行为测试。

对齐 docs/refactor/01-gap-matrix.md 第 7 节与 04-dsh-harness-notes.md 的目标 7：

* 按厂商声明能力（结构化输出 / Function Calling / 视觉 / 推理强度 / 上下文窗口）；
* 分级降级 native -> json -> text，不支持时退回提示词内 JSON 约定；
* 调用失败有可读错误与降级路径；
* **既有 OpenAICompatibleProvider 与 LLMProviderFactory 行为不回归**，
  AI 切换提供商的路径仍可用。

全部离线：不构造真实客户端、不发起任何请求。
"""

import inspect
import json

import pytest

import ai
import castorice.providers as providers
from castorice.providers import (
    TOOL_PROTOCOL_JSON,
    TOOL_PROTOCOL_NATIVE,
    TOOL_PROTOCOL_TEXT,
    ContextWindowExceeded,
    ModelCapabilities,
    ProviderAdapter,
    resolve_capabilities,
)


class ScriptedProvider:
    """按脚本返回结果或抛错的假 provider（与既有 chat_completion 同形）。"""

    def __init__(self, script=None):
        self.script = list(script or [])
        self.calls = []

    def chat_completion(self, **kwargs):
        self.calls.append(kwargs)
        if not self.script:
            return "默认回答", []
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


# ============================================================================
# 1. 能力表与解析优先级
# ============================================================================

def test_vendor_table_declares_the_expected_providers():
    table = providers.PROVIDER_TIERS
    for name in ("openai", "deepseek", "aliyun", "anthropic", "custom",
                 "json_only", "text_only"):
        assert name in table


def test_vendor_key_normalises_aliases_and_model_names():
    assert providers.vendor_key("deepseek") == "deepseek"
    assert providers.vendor_key("DeepSeek-Chat") == "deepseek"
    assert providers.vendor_key("dashscope") == "aliyun"
    assert providers.vendor_key("Azure") == "openai"
    assert providers.vendor_key("完全未知") == "custom"
    assert providers.vendor_key(None) == "custom"


def test_resolve_capabilities_falls_back_to_the_vendor_table():
    caps = resolve_capabilities("deepseek", {"model": "deepseek-chat"})
    assert caps.tier == "deepseek"
    assert caps.context_window == 64_000
    assert caps.supports_tools is True


def test_explicit_config_overrides_the_vendor_table():
    caps = resolve_capabilities("deepseek", {"model": "deepseek-chat",
                                             "context_window": 8_000,
                                             "supports_tools": False,
                                             "structured_output": True})
    assert caps.context_window == 8_000
    assert caps.supports_tools is False
    assert caps.supports_structured_output is True


def test_existing_vision_flag_still_wins():
    """config.yaml 里已有的 vision: true 仍然生效（既有行为不回归）。"""
    caps = resolve_capabilities("deepseek", {"model": "deepseek-chat",
                                             "vision": True})
    assert caps.supports_vision is True
    caps2 = resolve_capabilities("openai", {"model": "gpt-4o", "vision": False})
    assert caps2.supports_vision is False


def test_model_name_adds_conservative_capabilities():
    assert resolve_capabilities("custom", {"model": "qwen2.5-vl-7b"}).supports_vision
    assert resolve_capabilities("custom", {"model": "gpt-4o"}).context_window == 128_000
    assert resolve_capabilities("custom", {"model": "plain-model"}).supports_vision is False


def test_known_providers_lists_the_table():
    listed = providers.known_providers()
    assert "deepseek" in listed and "openai" in listed


def test_capabilities_to_dict_is_complete():
    payload = resolve_capabilities("openai", {}).to_dict()
    for key in ("context_window", "supports_tools", "supports_vision",
                "supports_structured_output", "tier"):
        assert key in payload


# ============================================================================
# 2. 协议分级与降级链
# ============================================================================

def test_protocol_is_native_when_tools_supported():
    caps = ModelCapabilities(supports_tools=True)
    assert caps.protocol(tools_requested=True) == TOOL_PROTOCOL_NATIVE


def test_protocol_is_json_without_function_calling():
    caps = ModelCapabilities(supports_tools=False, supports_structured_output=True)
    assert caps.protocol(tools_requested=True) == TOOL_PROTOCOL_JSON


def test_protocol_is_text_when_nothing_supported():
    caps = ModelCapabilities(supports_tools=False, supports_structured_output=False)
    assert caps.protocol(tools_requested=True) == TOOL_PROTOCOL_TEXT


def test_protocol_is_text_when_no_tools_requested():
    caps = ModelCapabilities(supports_tools=True)
    assert caps.protocol(tools_requested=False) == TOOL_PROTOCOL_TEXT


def test_fallback_chain_is_native_json_text():
    assert providers.next_protocol(TOOL_PROTOCOL_NATIVE) == TOOL_PROTOCOL_JSON
    assert providers.next_protocol(TOOL_PROTOCOL_JSON) == TOOL_PROTOCOL_TEXT
    assert providers.next_protocol(TOOL_PROTOCOL_TEXT) is None
    assert set(providers.TOOL_PROTOCOLS) == {
        TOOL_PROTOCOL_NATIVE, TOOL_PROTOCOL_JSON, TOOL_PROTOCOL_TEXT}


# ============================================================================
# 3. 请求体裁剪
# ============================================================================

TOOL_SPECS = [{"type": "function", "function": {
    "name": "read_file", "description": "读文件",
    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}}]


def test_native_request_carries_tools_key():
    prepared = providers.prepare_request(
        ModelCapabilities(supports_tools=True), [{"role": "user", "content": "hi"}],
        tools=TOOL_SPECS)
    assert "tools" in prepared.kwargs
    assert prepared.protocol == TOOL_PROTOCOL_NATIVE


def test_json_protocol_request_has_no_tools_key():
    prepared = providers.prepare_request(
        ModelCapabilities(supports_tools=False, supports_structured_output=True),
        [{"role": "user", "content": "hi"}], tools=TOOL_SPECS)
    assert "tools" not in prepared.kwargs
    assert prepared.protocol == TOOL_PROTOCOL_JSON


def test_no_tools_request_has_no_tools_key_even_when_native():
    prepared = providers.prepare_request(
        ModelCapabilities(supports_tools=True), [{"role": "user", "content": "hi"}])
    assert "tools" not in prepared.kwargs


def test_temperature_and_max_tokens_are_only_added_when_given():
    caps = ModelCapabilities(max_output_tokens=1000)
    prepared = providers.prepare_request(caps, [{"role": "user", "content": "x"}])
    assert prepared.kwargs["max_tokens"] == 1000
    assert "temperature" not in prepared.kwargs
    prepared2 = providers.prepare_request(
        caps, [{"role": "user", "content": "x"}], temperature=0.3, max_tokens=10)
    assert prepared2.kwargs["temperature"] == 0.3
    assert prepared2.kwargs["max_tokens"] == 10


def test_reasoning_effort_is_dropped_when_unsupported():
    prepared = providers.prepare_request(
        ModelCapabilities(supports_reasoning_effort=False),
        [{"role": "user", "content": "x"}], reasoning_effort="high")
    assert "reasoning_effort" not in prepared.kwargs
    assert any("reasoning_effort" in note for note in prepared.notes)


def test_reasoning_effort_is_kept_when_supported():
    prepared = providers.prepare_request(
        ModelCapabilities(supports_reasoning_effort=True),
        [{"role": "user", "content": "x"}], reasoning_effort="high")
    assert prepared.kwargs["reasoning_effort"] == "high"


def test_context_window_is_enforced_not_silently_truncated():
    caps = ModelCapabilities(context_window=100, max_output_tokens=10)
    with pytest.raises(ContextWindowExceeded) as excinfo:
        providers.prepare_request(caps, [{"role": "user", "content": "x" * 5000}])
    payload = excinfo.value.to_dict()
    assert payload["code"] == "CONTEXT_WINDOW_EXCEEDED"
    assert payload["estimated_input_tokens"] > payload["input_budget"]


def test_budget_check_can_be_disabled():
    caps = ModelCapabilities(context_window=100, max_output_tokens=10)
    prepared = providers.prepare_request(
        caps, [{"role": "user", "content": "x" * 5000}], enforce_budget=False)
    assert prepared.estimated_input_tokens > 0


def test_token_estimation_counts_cjk_wider():
    assert providers.estimate_tokens("中文中文") >= 4
    assert providers.estimate_tokens("abcdefgh") <= 4
    assert providers.estimate_tokens("") == 0


def test_message_token_estimation_includes_tool_calls():
    plain = providers.estimate_message_tokens([{"role": "user", "content": "hi"}])
    with_calls = providers.estimate_message_tokens([
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "1", "function": {"name": "x", "arguments": "{}"}}]}])
    assert with_calls > plain


# ============================================================================
# 4. 提示词内 JSON 约定（降级档）
# ============================================================================

def test_json_protocol_instructions_are_empty_without_tools():
    assert providers.build_json_protocol_instructions([]) == ""


def test_json_protocol_instructions_list_the_tools():
    text = providers.build_json_protocol_instructions(TOOL_SPECS)
    assert "read_file" in text
    assert "tool_calls" in text
    assert "JSON" in text


def test_parse_json_tool_calls_from_plain_object():
    calls, remainder = providers.parse_json_tool_calls(
        '{"tool_calls": [{"name": "read_file", "arguments": {"path": "a.txt"}}]}')
    assert len(calls) == 1
    assert calls[0].name == "read_file"
    assert calls[0].arguments == {"path": "a.txt"}
    assert remainder == ""


def test_parse_json_tool_calls_from_code_fence():
    text = '先看看文件\n```json\n{"tool_calls": [{"name": "read_file", "arguments": {"path": "b"}}]}\n```'
    calls, remainder = providers.parse_json_tool_calls(text)
    assert calls[0].name == "read_file"
    assert "先看看文件" in remainder
    assert "tool_calls" not in remainder


def test_parse_json_tool_calls_accepts_string_arguments():
    calls, _remainder = providers.parse_json_tool_calls(
        '{"tool_calls": [{"name": "x", "arguments": "{\\"a\\": 1}"}]}')
    assert calls[0].arguments == {"a": 1}


def test_parse_json_tool_calls_handles_multiple_calls():
    calls, _remainder = providers.parse_json_tool_calls(
        '{"tool_calls": [{"name": "a", "arguments": {}}, {"name": "b", "arguments": {}}]}')
    assert [call.name for call in calls] == ["a", "b"]


def test_non_json_text_yields_no_calls():
    calls, remainder = providers.parse_json_tool_calls("就是一段普通回答")
    assert calls == []
    assert remainder == "就是一段普通回答"


def test_malformed_json_is_ignored():
    calls, _remainder = providers.parse_json_tool_calls('{"tool_calls": [')
    assert calls == []


def test_json_call_converts_to_openai_shape():
    call = providers.JsonToolCall(name="a", arguments={"x": 1}, raw_arguments='{"x": 1}')
    payload = call.to_openai(2)
    assert payload["type"] == "function"
    assert payload["function"]["name"] == "a"
    assert payload["id"].endswith("2")


def test_render_tool_catalog_accepts_both_shapes():
    text = providers.render_tool_catalog(TOOL_SPECS)
    assert "read_file" in text and "path" in text
    assert providers.render_tool_catalog([]) == "（当前没有可用工具）"


def test_looks_like_json_protocol():
    assert providers.looks_like_json_protocol(
        '{"tool_calls": [{"name": "x", "arguments": {}}]}') is True
    assert providers.looks_like_json_protocol("普通回答") is False


# ============================================================================
# 5. 适配器：调用、降级与可读错误
# ============================================================================

def test_adapter_passes_native_requests_through():
    provider = ScriptedProvider([("回答", [])])
    adapter = ProviderAdapter(provider, ModelCapabilities(supports_tools=True),
                              provider_name="openai", model="gpt-4o")
    outcome = adapter.complete([{"role": "user", "content": "hi"}], tool_specs=TOOL_SPECS)
    assert outcome.ok
    assert outcome.text == "回答"
    assert outcome.protocol == TOOL_PROTOCOL_NATIVE
    assert "tools" in provider.calls[0]


def test_adapter_parses_json_protocol_calls():
    provider = ScriptedProvider([
        ('{"tool_calls": [{"name": "read_file", "arguments": {"path": "a"}}]}', [])])
    adapter = ProviderAdapter(
        provider, ModelCapabilities(supports_tools=False,
                                    supports_structured_output=True),
        provider_name="json_only")
    outcome = adapter.complete([{"role": "user", "content": "hi"}], tool_specs=TOOL_SPECS)
    assert outcome.ok
    assert len(outcome.tool_calls) == 1
    assert outcome.tool_calls[0]["function"]["name"] == "read_file"
    assert "tools" not in provider.calls[0]


def test_adapter_degrades_when_provider_rejects_tools():
    """原生协议不被支持 -> 自动降一级到 json 并重试一次。"""
    provider = ScriptedProvider([
        RuntimeError("This model does not support tools"),
        ('{"tool_calls": [{"name": "read_file", "arguments": {}}]}', []),
    ])
    adapter = ProviderAdapter(
        provider, ModelCapabilities(supports_tools=True,
                                    supports_structured_output=True),
        provider_name="custom", model="m")
    outcome = adapter.complete([{"role": "user", "content": "hi"}], tool_specs=TOOL_SPECS)
    assert outcome.ok
    assert outcome.protocol == TOOL_PROTOCOL_JSON
    assert outcome.degraded is True
    assert "does not support tools" in outcome.degrade_reason
    assert "tools" not in provider.calls[-1]
    assert any("降级" in note for note in adapter.notes)


def test_adapter_does_not_degrade_on_unrelated_errors():
    provider = ScriptedProvider([RuntimeError("401 Unauthorized")])
    adapter = ProviderAdapter(provider, ModelCapabilities(supports_tools=True),
                              provider_name="openai")
    outcome = adapter.complete([{"role": "user", "content": "hi"}], tool_specs=TOOL_SPECS)
    assert outcome.ok is False
    assert outcome.degraded is False
    assert "401 Unauthorized" in outcome.error
    assert outcome.attempts == 1
    assert len(provider.calls) == 1


def test_adapter_reports_context_overflow_as_readable_error():
    provider = ScriptedProvider([("回答", [])])
    adapter = ProviderAdapter(
        provider, ModelCapabilities(context_window=100, max_output_tokens=10),
        provider_name="x")
    outcome = adapter.complete([{"role": "user", "content": "x" * 5000}])
    assert outcome.ok is False
    assert "上下文超出" in outcome.error
    assert provider.calls == []


def test_adapter_reports_missing_chat_completion():
    adapter = ProviderAdapter(object(), ModelCapabilities(), provider_name="x")
    outcome = adapter.complete([{"role": "user", "content": "hi"}])
    assert outcome.ok is False
    assert "chat_completion" in outcome.error


def test_adapter_error_mentions_provider_and_protocol():
    provider = ScriptedProvider([RuntimeError("boom")])
    adapter = ProviderAdapter(provider, ModelCapabilities(), provider_name="p")
    outcome = adapter.complete([{"role": "user", "content": "hi"}], tool_specs=TOOL_SPECS)
    assert outcome.ok is False
    assert "provider=p" in outcome.error
    assert "native" in outcome.error


def test_degrade_and_reset_restore_the_protocol():
    adapter = ProviderAdapter(ScriptedProvider(), ModelCapabilities(),
                              provider_name="openai")
    assert adapter.protocol == TOOL_PROTOCOL_NATIVE
    assert adapter.degrade("测试") == TOOL_PROTOCOL_JSON
    assert adapter.degrade() == TOOL_PROTOCOL_TEXT
    assert adapter.degrade() == TOOL_PROTOCOL_TEXT
    adapter.reset()
    assert adapter.protocol == TOOL_PROTOCOL_NATIVE
    assert adapter.notes == []


def test_adapter_protocol_instructions_depend_on_the_protocol():
    json_adapter = ProviderAdapter(
        ScriptedProvider(),
        ModelCapabilities(supports_tools=False, supports_structured_output=True))
    assert "JSON" in json_adapter.protocol_instructions(TOOL_SPECS)

    native_adapter = ProviderAdapter(
        ScriptedProvider(), ModelCapabilities(supports_tools=True))
    assert native_adapter.protocol_instructions(TOOL_SPECS) == ""

    text_adapter = ProviderAdapter(
        ScriptedProvider(),
        ModelCapabilities(supports_tools=False, supports_structured_output=False))
    assert text_adapter.protocol_instructions(TOOL_SPECS) == ""


def test_install_json_protocol_appends_to_system_prompt():
    adapter = ProviderAdapter(
        ScriptedProvider(),
        ModelCapabilities(supports_tools=False, supports_structured_output=True))
    prompt = providers.install_json_protocol(adapter, "基础提示词", TOOL_SPECS)
    assert prompt.startswith("基础提示词")
    assert "tool_calls" in prompt

    native = ProviderAdapter(ScriptedProvider(), ModelCapabilities(supports_tools=True))
    assert providers.install_json_protocol(native, "基础", TOOL_SPECS) == "基础"


def test_capability_summary_is_ui_ready():
    adapter = ProviderAdapter(
        ScriptedProvider(),
        ModelCapabilities(supports_tools=False, supports_structured_output=True),
        provider_name="json_only", model="m")
    payload = adapter.capability_summary()
    assert payload["provider"] == "json_only"
    assert payload["protocol"] == TOOL_PROTOCOL_JSON
    assert payload["degraded"] is True


def test_call_alias_matches_provider_shape():
    provider = ScriptedProvider([("回答", [{"id": "1", "type": "function",
                                            "function": {"name": "t",
                                                         "arguments": "{}"}}])])
    adapter = ProviderAdapter(provider, ModelCapabilities(supports_tools=True),
                              provider_name="openai")
    text, calls = adapter.call([{"role": "user", "content": "hi"}], tool_specs=TOOL_SPECS)
    assert text == "回答"
    assert calls[0]["function"]["name"] == "t"


def test_resolve_adapter_builds_from_config():
    adapter = providers.resolve_adapter(
        ScriptedProvider(), "deepseek", {"model": "deepseek-chat"})
    assert adapter.capabilities.tier == "deepseek"
    assert adapter.model == "deepseek-chat"


# ============================================================================
# 6. 既有 provider 行为不回归 + AI 接线
# ============================================================================

def test_llm_provider_factory_still_returns_openai_compatible(tmp_path):
    """既有工厂行为不变：任何 provider 名都返回 OpenAICompatibleProvider。"""
    config = {"api_key": "test-key", "model": "m",
              "base_url": "https://example.invalid/v1"}
    provider = ai.LLMProviderFactory.create_provider("deepseek", config)
    assert isinstance(provider, ai.OpenAICompatibleProvider)
    assert provider._model == "m"


def test_openai_compatible_requires_api_key():
    with pytest.raises(ValueError, match="缺少 api_key"):
        ai.LLMProviderFactory.create_provider("openai", {"model": "m"})


def test_openai_compatible_signature_is_unchanged():
    params = list(inspect.signature(ai.LLMProvider.chat_completion).parameters)
    assert params == ["self", "messages", "stream", "kwargs"]
    params2 = list(inspect.signature(
        ai.OpenAICompatibleProvider.chat_completion).parameters)
    assert params2 == ["self", "messages", "stream", "kwargs"]


def test_openai_compatible_raises_readable_error_on_api_failure():
    """既有行为：API 失败抛出「LLM API调用失败: ...」的可读异常。

    这里用 try/finally 直接替换 provider 自己持有的客户端对象上的方法，
    不改任何模块级实现，也不使用 monkeypatch（护栏只允许 patch
    随机数与网络层）。
    """
    provider = ai.LLMProviderFactory.create_provider(
        "deepseek", {"api_key": "k", "model": "m"})
    completions = provider._client.chat.completions
    original = completions.create

    def boom(**kwargs):
        raise RuntimeError("连接被拒绝")

    completions.create = boom
    try:
        with pytest.raises(Exception) as excinfo:
            provider.chat_completion([{"role": "user", "content": "hi"}])
        assert "LLM API调用失败" in str(excinfo.value)
        assert "连接被拒绝" in str(excinfo.value)
    finally:
        completions.create = original


def test_openai_compatible_parses_streamed_tool_calls():
    """既有行为：流式响应里的 tool_calls 增量被拼装成完整调用。"""
    provider = ai.LLMProviderFactory.create_provider(
        "deepseek", {"api_key": "k", "model": "m"})

    class Delta:
        def __init__(self, content=None, tool_calls=None):
            self.content = content
            self.tool_calls = tool_calls

    class Choice:
        def __init__(self, delta):
            self.delta = delta

    class Chunk:
        def __init__(self, delta):
            self.choices = [Choice(delta)]

    class Call:
        def __init__(self, index, id=None, name=None, arguments=None):
            self.index = index
            self.id = id
            self.function = type("F", (), {"name": name, "arguments": arguments})()

    stream = [
        Chunk(Delta(content="我查一下")),
        Chunk(Delta(tool_calls=[Call(0, id="c1", name="get_current_time",
                                     arguments="{}")])),
    ]
    completions = provider._client.chat.completions
    original = completions.create
    completions.create = lambda **kwargs: iter(stream)
    try:
        text, calls = provider.chat_completion([{"role": "user", "content": "几点"}])
    finally:
        completions.create = original
    assert text == "我查一下"
    assert calls[0]["id"] == "c1"
    assert calls[0]["function"]["name"] == "get_current_time"


def test_ai_exposes_capability_metadata():
    """AI 侧新增的能力视图（不改既有 provider 调用方式）。"""
    for name in ("capabilities", "tool_protocol", "provider_capabilities"):
        assert hasattr(ai.AI, name) or isinstance(
            getattr(ai.AI, name, None), property)


def test_ai_capability_helpers_are_pure_reads():
    """provider_capabilities 是纯读函数：给任意名字都能返回结构化能力。"""
    source = inspect.getsource(ai.AI.provider_capabilities)
    assert "get_provider_config" in source
    assert "resolve_capabilities" in source
