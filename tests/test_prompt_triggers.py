# -*- coding: utf-8 -*-
"""触发词注入（第二期 t2）：按用户消息动态筛选 prompts 模块。

覆盖面：
- PromptManager 对外接口向后兼容（get_prompt 旧签名逐字节旧行为）；
- 命中行为确定性可测（同消息同结果、排序规则固定）；
- 未命中时给出最小核心集（常驻模块），绝不出现空提示词；
- token 节省可量化（selection_report）；
- config.yaml prompts 节：缺省关闭 = 全量拼接，旧配置零改动可跑。
"""

import json

import pytest

import ai
from ai import PromptManager


@pytest.fixture
def prompt_dir(tmp_path):
    """写一个最小但结构完整的 prompts 目录（带触发词标注）。"""
    root = tmp_path / "prompts"
    root.mkdir()
    (root / "identity.md").write_text(
        "# 身份\n你是遐蝶。", encoding="utf-8")
    (root / "soul.md").write_text(
        "# 灵魂\n你是谁、你如何说话。", encoding="utf-8")
    (root / "world.md").write_text("\n".join([
        "# 世界观",
        "",
        "## 翁法罗斯",
        "- 触发词: 翁法罗斯, 永恒之地",
        "- 常驻: 否",
        "- 优先级: 150",
        "",
        "翁法罗斯是永恒之地。",
        "",
        "## 帝皇权杖",
        "- 触发词: 帝皇权杖, 权杖",
        "- 常驻: 否",
        "- 优先级: 120",
        "",
        "帝皇权杖是星体计算机。",
    ]) + "\n", encoding="utf-8")
    (root / "characters.md").write_text("\n".join([
        "# 人物",
        "",
        "## 白厄 / Phainon",
        "- 触发词: 白厄, Phainon, 烈阳哥",
        "- 常驻: 否",
        "- 优先级: 100",
        "",
        "白厄是纷争的太阳。",
        "",
        "## 风堇 / Hyacine",
        "- 触发词: 风堇, Hyacine, 泰坦",
        "- 常驻: 否",
        "- 优先级: 100",
        "",
        "风堇是天空泰坦的医师。",
    ]) + "\n", encoding="utf-8")
    return root

# --------------------------------------------------------------------------
# a) 对外接口向后兼容
# --------------------------------------------------------------------------

def test_default_get_prompt_is_full_concat(prompt_dir):
    """缺省（无 user_message / 未开配置）= 现全量拼接，逐字节一致。"""
    pm = PromptManager(str(prompt_dir))
    assert pm.get_prompt() == pm.prompt
    assert "白厄是纷争的太阳。" in pm.get_prompt()
    assert pm.get_prompt(memory_context="记忆") == \
        pm.prompt + "\n\n【联想到的相关记忆】：\n记忆"


def test_constructor_keeps_positional_signature(prompt_dir):
    """PromptManager(prompts_dir) 位置参数形态不变。"""
    pm = PromptManager(str(prompt_dir))
    assert pm.prompts_dir == prompt_dir
    assert pm.dynamic_enabled is False


def test_prompt_property_and_sections_unchanged(prompt_dir):
    """prompt / sections / get_section / compose_sections 行为不变。"""
    pm = PromptManager(str(prompt_dir))
    assert "你是遐蝶" in pm.prompt
    assert pm.sections == {}
    assert pm.get_section("不存在") == ""
    assert pm.compose_sections(("不存在",)) == ""


# --------------------------------------------------------------------------
# b) 命中行为确定性可测
# --------------------------------------------------------------------------

def test_entry_parsing(prompt_dir):
    pm = PromptManager(str(prompt_dir))
    world = next(m for m in pm._modules if m.name == "world.md")
    assert world.is_entry_based
    assert [e["title"] for e in world.entries] == ["翁法罗斯", "帝皇权杖"]
    assert world.entries[0]["triggers"] == ("翁法罗斯", "永恒之地")
    assert world.entries[0]["priority"] == 150
    assert not world.entries[0]["resident"]
    identity = next(m for m in pm._modules if m.name == "identity.md")
    assert not identity.is_entry_based


def test_matched_entries_sorted_by_priority_then_hits(prompt_dir):
    pm = PromptManager(str(prompt_dir))
    world = next(m for m in pm._modules if m.name == "world.md")
    hits = world.matched_entries("翁法罗斯和帝皇权杖")
    assert [e["title"] for e in hits] == ["翁法罗斯", "帝皇权杖"]
    chars = next(m for m in pm._modules if m.name == "characters.md")
    hits = chars.matched_entries("Phainon 白厄 烈阳哥")
    assert [e["title"] for e in hits] == ["白厄 / Phainon"]
    assert hits[0]["_hits"] == 3


def test_selection_is_deterministic(prompt_dir):
    pm = PromptManager(str(prompt_dir), config={"enabled": True})
    message = "白厄和帝皇权杖"
    assert pm.get_prompt(user_message=message) == \
        pm.get_prompt(user_message=message)


def test_match_is_case_insensitive_for_latin_triggers(prompt_dir):
    pm = PromptManager(str(prompt_dir), config={"enabled": True})
    prompt = pm.get_prompt(user_message="phainon 是谁")
    assert "白厄是纷争的太阳。" in prompt


# --------------------------------------------------------------------------
# c) 未命中时给最小核心集，绝不空提示词
# --------------------------------------------------------------------------

def test_no_hit_returns_resident_core(prompt_dir):
    pm = PromptManager(str(prompt_dir), config={"enabled": True})
    prompt = pm.get_prompt(user_message="今天天气不错")
    assert prompt.strip(), "未命中也不允许空提示词"
    assert "你是遐蝶" in prompt      # identity 常驻
    assert "你是谁、你如何说话" in prompt  # soul 常驻
    assert "白厄是纷争的太阳。" not in prompt
    assert "翁法罗斯是永恒之地。" not in prompt


def test_resident_flag_entry_is_always_injected(tmp_path):
    """「常驻: 是」的条目随常驻部分注入，不参与筛选。"""
    root = tmp_path / "prompts"
    root.mkdir()
    (root / "facts.md").write_text("\n".join([
        "# 事实",
        "",
        "## 服务条款",
        "- 触发词: 条款",
        "- 常驻: 是",
        "",
        "常驻条目内容。",
        "",
        "## 临时条目",
        "- 触发词: 临时",
        "- 常驻: 否",
        "",
        "临时内容。",
    ]) + "\n", encoding="utf-8")
    pm = PromptManager(str(root), config={"enabled": True})
    prompt = pm.get_prompt(user_message="无关消息")
    assert "常驻条目内容。" in prompt
    assert "临时内容。" not in prompt


def test_file_without_any_trigger_annotation_is_resident(prompt_dir):
    pm = PromptManager(str(prompt_dir), config={"enabled": True})
    assert pm._resident_names() == ["identity.md", "soul.md"]


def test_empty_message_falls_back_to_resident_core(prompt_dir):
    pm = PromptManager(str(prompt_dir), config={"enabled": True})
    prompt = pm.get_prompt(user_message="   ")
    assert prompt.strip()
    assert "你是遐蝶" in prompt


def test_never_returns_empty_prompt(tmp_path):
    """极端配置（常驻名单写空）也必须兜底非空。"""
    root = tmp_path / "prompts"
    root.mkdir()
    (root / "only.md").write_text("# 唯一模块\n内容。", encoding="utf-8")
    pm = PromptManager(
        str(root),
        config={"enabled": True, "resident_modules": ["不存在.md"]})
    assert pm.get_prompt(user_message="随便说什么").strip()


# --------------------------------------------------------------------------
# d) token 节省可量化
# --------------------------------------------------------------------------

def test_selection_report_quantifies_savings(prompt_dir):
    pm = PromptManager(str(prompt_dir), config={"enabled": True})
    full = len(pm.prompt.encode("utf-8"))
    report = pm.selection_report("白厄和帝皇权杖")
    assert report["full_bytes"] == full
    assert report["selected_bytes"] < full
    assert report["saved_bytes"] == full - report["selected_bytes"]
    assert 0.0 < report["saved_ratio"] < 1.0
    assert report["matched"], "命中条目应被列出"


def test_selection_report_is_pure_query(prompt_dir):
    pm = PromptManager(str(prompt_dir), config={"enabled": True})
    assert pm.selection_report("白厄") == pm.selection_report("白厄")


def test_real_world_prompts_save_tokens():
    """仓库自带 11 模块：命中与未命中都必须显著小于 76KB 全量。"""
    pm = PromptManager("prompts", config={"enabled": True})
    full = len(pm.prompt.encode("utf-8"))
    assert full > 70 * 1024
    hit = pm.selection_report("白厄和万敌在翁法罗斯的往事")
    miss = pm.selection_report("帮我看看这段代码怎么写")
    assert hit["selected_bytes"] < full * 0.7
    assert miss["selected_bytes"] < full * 0.5
    assert miss["saved_ratio"] > 0.5

# --------------------------------------------------------------------------
# e) config.yaml prompts 节；缺省行为 = 全量拼接
# --------------------------------------------------------------------------

def test_disabled_by_default_matches_full(prompt_dir):
    """显式 enabled: False（含 config 完全没有 prompts 节）= 全量拼接。"""
    pm_off = PromptManager(str(prompt_dir), config={"enabled": False})
    assert pm_off.get_prompt(user_message="白厄") == pm_off.prompt


def test_enabled_config_switches_behavior(prompt_dir):
    pm_on = PromptManager(str(prompt_dir), config={"enabled": True})
    assert pm_on.get_prompt(user_message="白厄") != pm_on.prompt
    assert "白厄是纷争的太阳。" in pm_on.get_prompt(user_message="白厄")


def test_unknown_config_keys_are_ignored(prompt_dir):
    pm = PromptManager(str(prompt_dir), config={"enabled": True, "乱写的": 1})
    assert pm.dynamic_enabled is True


def test_max_entries_caps_matched_entries(prompt_dir):
    pm = PromptManager(
        str(prompt_dir), config={"enabled": True, "max_entries": 1})
    prompt = pm.get_prompt(user_message="白厄和帝皇权杖")
    hits = [
        text for text in ("翁法罗斯是永恒之地。", "白厄是纷争的太阳。")
        if text in prompt
    ]
    assert len(hits) == 1


def test_resident_modules_override(prompt_dir):
    """resident_modules 显式点名谁常驻（覆盖自动判定）。"""
    pm = PromptManager(
        str(prompt_dir),
        config={"enabled": True, "resident_modules": ["soul.md"]})
    prompt = pm.get_prompt(user_message="无关")
    assert "你是谁、你如何说话" in prompt
    assert "你是遐蝶" not in prompt  # identity 被排除出常驻名单


# --------------------------------------------------------------------------
# AI 层集成：config prompts 节 + 每轮 system 头刷新
# --------------------------------------------------------------------------

CONFIG_TEXT = "\n".join([
    "mode: chat",
    "active_provider: fake",
    "workdir: {root}",
    "providers:",
    "  fake:",
    "    api_key: test-key",
    "    model: fake-model",
    "{prompts_section}",
    "",
])


class ScriptedProvider:
    """返回固定回复的 provider 替身（记录收到的 messages）。"""

    def __init__(self, bot):
        self.bot = bot
        self.calls = []

    def chat_completion(self, messages, stream=True, **kwargs):
        self.calls.append([dict(m) for m in messages])
        return "好的。", []


@pytest.fixture
def bot(tmp_path):
    """带触发词标注目录的离线 AI 实例（provider 替身）。"""
    root = tmp_path / "case"
    prompts = root / "prompts"
    prompts.mkdir(parents=True)
    (prompts / "identity.md").write_text("你是遐蝶。", encoding="utf-8")
    (prompts / "world.md").write_text("\n".join([
        "# 世界观",
        "",
        "## 翁法罗斯",
        "- 触发词: 翁法罗斯",
        "- 常驻: 否",
        "",
        "翁法罗斯是永恒之地。",
    ]) + "\n", encoding="utf-8")
    (prompts / "modes").mkdir()
    (root / "Image").mkdir()
    config = root / "config.yaml"
    config.write_text(
        CONFIG_TEXT.replace("{root}", json.dumps(str(root)))
                    .replace("{prompts_section}", ""),
        encoding="utf-8")
    instance = ai.AI(
        config_path=str(config),
        prompts_dir=str(prompts),
        chroma_dir=str(root / "chroma"),
        provider_name="fake",
        image_dir=str(root / "Image"),
        sessions_dir=str(root / "sessions"),
        mode="chat",
    )
    yield instance
    for store in list(instance._sessions.values()):
        store.close()


def test_ai_default_config_keeps_full_prompt(bot):
    """AI 缺省（config 无 prompts 节）：请求 system 含全量模块内容。"""
    provider = ScriptedProvider(bot)
    bot._provider = provider
    bot.harness.provider = provider
    list(bot.get_response_stream("白厄是谁"))
    system = provider.calls[0][0]["content"]
    assert bot.prompt_manager.prompt in system, "基础提示词全量在场"
    assert "翁法罗斯是永恒之地。" in system, "未筛选：条目随文件全量注入"


def test_ai_dynamic_injection_via_config(bot):
    """AI 开启 prompts.enabled 后：未命中只有常驻核心，命中才带条目。"""
    config_path = bot.config_manager.config_path
    text = config_path.read_text(encoding="utf-8")
    config_path.write_text(
        text.replace("mode: chat", "mode: chat\nprompts:\n  enabled: true"),
        encoding="utf-8")
    bot.config_manager.reload()
    bot.prompt_manager = ai.PromptManager(
        str(bot.prompt_manager.prompts_dir),
        config=bot.config_manager.get("prompts") or {})

    provider = ScriptedProvider(bot)
    bot._provider = provider
    bot.harness.provider = provider

    list(bot.get_response_stream("今天天气不错"))
    miss_system = provider.calls[0][0]["content"]
    assert "翁法罗斯是永恒之地。" not in miss_system
    assert "你是遐蝶。" in miss_system, "常驻核心仍在"

    list(bot.get_response_stream("翁法罗斯是什么地方"))
    hit_system = provider.calls[1][0]["content"]
    assert "翁法罗斯是永恒之地。" in hit_system


def test_ai_system_head_refreshes_each_turn(bot):
    """每轮请求的 system 头用本轮提示词（触发词注入的前提）。"""
    provider = ScriptedProvider(bot)
    bot._provider = provider
    bot.harness.provider = provider
    list(bot.get_response_stream("第一轮"))
    (bot.prompt_manager.prompts_dir / "identity.md").write_text(
        "你是新的设定。", encoding="utf-8")
    bot.prompt_manager._load_prompts()
    list(bot.get_response_stream("第二轮"))
    system = provider.calls[1][0]["content"]
    assert "你是新的设定。" in system


def test_backward_compat_public_names_unchanged(prompt_dir):
    """向后兼容冒烟：PromptManager 仍是 ai 的公开符号。"""
    assert hasattr(ai, "PromptManager")
    pm = PromptManager(str(prompt_dir))
    for attr in ("get_prompt", "compose_sections", "get_section", "prompt",
                 "sections", "prompts_dir"):
        assert hasattr(pm, attr), attr