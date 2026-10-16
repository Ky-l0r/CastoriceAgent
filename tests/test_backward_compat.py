"""ai.py 对 UI 层的向后兼容接口冒烟测试

后续改造必须保持 AI / AIFactory / get_ai / get_response_stream 的对外契约不变。
这里只做「不联网、不建库」的静态检查：
- 类与函数的签名仍在；
- get_response_stream 仍是生成器函数；
- 事件协议里出现的键名仍被引用（源码级快照，比字符串更严格）。

这些断言是给后续改造者的护栏：接口变了必须显式改测试，而不是悄悄漂移。
"""

import inspect

import pytest

import ai


# ----------------------------------------------------------------------------
# 公开符号仍在
# ----------------------------------------------------------------------------

@pytest.mark.parametrize("name", [
    "AI", "AIFactory", "get_ai",
    "ConfigManager", "PromptManager", "MemoryManager",
    "LLMProvider", "LLMProviderFactory", "OpenAICompatibleProvider",
    "Tool", "ToolRegistry", "create_builtin_tools",
    "EmojiCatalog", "EmojiSession", "EmojiSticker",
])
def test_public_names_exist(name):
    assert hasattr(ai, name), f"ai.{name} 不应被移除（UI 依赖它）"


def test_get_response_stream_is_provided_by_ai_class():
    assert hasattr(ai.AI, "get_response_stream")
    assert inspect.isgeneratorfunction(ai.AI.get_response_stream)


def test_ai_class_keeps_public_methods():
    for name in [
        "get_response", "clear_history", "get_history",
        "clear_memories", "get_memory_count", "change_provider",
    ]:
        assert callable(getattr(ai.AI, name, None)), f"AI.{name} 缺失"


def test_ai_init_signature_is_backward_compatible():
    params = inspect.signature(ai.AI.__init__).parameters
    names = list(params)
    assert names[:7] == [
        "self", "config_path", "prompts_dir", "chroma_dir",
        "collection_name", "provider_name", "image_dir",
    ]
    assert params["provider_name"].default is None


def test_get_ai_signature():
    params = inspect.signature(ai.get_ai).parameters
    assert list(params) == ["provider_name"]
    assert params["provider_name"].default is None


def test_get_response_stream_signature():
    params = inspect.signature(ai.AI.get_response_stream).parameters
    assert list(params) == ["self", "user_input", "images"]
    assert params["images"].default is None


def test_ai_factory_methods():
    assert callable(ai.AIFactory.create_default_bot)
    assert callable(ai.AIFactory.create_with_custom_config)


def test_module_import_has_no_side_effects_at_import_time():
    """导入 ai 不应创建实例、不应建库"""
    assert ai._bot_instance is None


# ----------------------------------------------------------------------------
# 事件协议键名（源码级快照）
# ----------------------------------------------------------------------------

@pytest.fixture(scope="module")
def stream_source():
    return inspect.getsource(ai.AI.get_response_stream)


def test_stream_event_kinds_are_unchanged(stream_source):
    """ui.py 只认这四种事件类型，任何改造都必须保持"""
    for kind in ["'text'", "'tool'", "'emoji'", "'done'"]:
        assert kind in stream_source, f"流式事件类型 {kind} 不应消失"


def test_stream_emits_two_element_tuples(stream_source):
    # ui.py 用 isinstance(chunk, tuple) and len(chunk) == 2 判断事件
    assert "yield ('" in stream_source


def test_stream_tool_payload_keys_are_unchanged(stream_source):
    # ui.py 读取 payload.get('name') 与 payload.get('status')
    assert "'name'" in stream_source
    assert "'status'" in stream_source
    assert "'path'" in stream_source


def test_memory_message_shape_is_unchanged(stream_source):
    assert "tool_call_id" in stream_source
    assert "content" in stream_source


def test_memory_and_provider_error_messages_unchanged():
    source = inspect.getsource(ai)
    for message in ["未找到提供商配置", "未配置 active_provider"]:
        assert message in source


# ----------------------------------------------------------------------------
# 离线可导入
# ----------------------------------------------------------------------------

def test_ai_module_has_no_import_time_network_usage():
    """ai.py 顶层不得出现网络调用（有则说明引入了导入即联网的依赖）"""
    source = inspect.getsource(ai)
    top_level = source.split("def setup_logger")[0]
    assert "requests.get(" not in top_level
    assert "urlopen(" not in top_level
