"""update_yaml_scalars 回归测试

关注点：写回 config.yaml 时不得破坏用户原有的注释与排版。
「设置」窗口保存后配置文件仍需保持人类可读，这是本期改造的前置基线。
"""

import pytest
import yaml

import ai

BASE = """# config.yaml - 支持多种LLM提供商

# 当前激活的提供商
active_provider: "deepseek"  # 可选: openai, deepseek, aliyun, etc.

providers:
  # OpenAI
  openai:
    api_key: "your-openai-api-key"
    base_url: "https://api.openai.com/v1"
    model: "gpt-4"
    temperature: 0.7

  # DeepSeek
  deepseek:
    api_key: "sk-old"        # 别提交
    base_url: "https://api.deepseek.com"
    model: "deepseek-chat"
    temperature: 0.7
"""


# ----------------------------------------------------------------------------
# 基本写回
# ----------------------------------------------------------------------------


def test_updates_scalar_value():
    result = ai.update_yaml_scalars(BASE, {("active_provider",): "aliyun"})
    assert 'active_provider: "aliyun"' in result
    assert 'active_provider: "deepseek"' not in result


def test_keeps_trailing_comment_on_updated_line():
    result = ai.update_yaml_scalars(BASE, {("active_provider",): "aliyun"})
    line = _line_with(result, "active_provider:")
    assert line.endswith("# 可选: openai, deepseek, aliyun, etc.")
    # 注释前的空白也要原样保留
    assert line == 'active_provider: "aliyun"  # 可选: openai, deepseek, aliyun, etc.'


def test_keeps_all_other_lines_byte_identical():
    result = ai.update_yaml_scalars(BASE, {("providers", "deepseek", "api_key"): "sk-new"})
    before = BASE.splitlines()
    after = result.splitlines()
    assert len(before) == len(after)
    for old, new in zip(before, after):
        if old.startswith('    api_key: "sk-old"'):
            assert new == '    api_key: "sk-new"        # 别提交'
        else:
            assert old == new


def test_keeps_indent_and_section_comments():
    result = ai.update_yaml_scalars(BASE, {("providers", "deepseek", "temperature"): 0.3})
    assert "  # OpenAI" in result
    assert "  # DeepSeek" in result
    assert "    temperature: 0.3" in result
    # openai 段的值不受影响
    assert "    temperature: 0.7" in result


def test_multiple_updates_apply_in_one_pass():
    result = ai.update_yaml_scalars(
        BASE,
        {
            ("active_provider",): "aliyun",
            ("providers", "openai", "model"): "gpt-4o",
            ("providers", "deepseek", "model"): "deepseek-reasoner",
        },
    )
    assert 'active_provider: "aliyun"' in result
    assert 'model: "gpt-4o"' in result
    assert 'model: "deepseek-reasoner"' in result


# ----------------------------------------------------------------------------
# 键不存在时的插入规则
# ----------------------------------------------------------------------------


def test_inserts_missing_key_at_end_of_parent_block():
    result = ai.update_yaml_scalars(BASE, {("providers", "deepseek", "vision"): True})
    lines = result.splitlines()
    # openai 段里也有同名行，这里要定位 deepseek 段内的那一处
    deepseek_index = lines.index("  deepseek:")
    tail = lines[deepseek_index:]
    temp_index = deepseek_index + tail.index("    temperature: 0.7")
    assert lines[temp_index + 1] == "    vision: true"


def test_inserted_key_uses_parent_indent_plus_two():
    text = "a:\n  b:\n    c: 1\nd: 2\n"
    result = ai.update_yaml_scalars(text, {("a", "b", "e"): "x"})
    assert result == 'a:\n  b:\n    c: 1\n    e: "x"\nd: 2\n'


def test_skips_key_when_parent_block_missing():
    result = ai.update_yaml_scalars(BASE, {("nonexistent", "api_key"): "x"})
    assert result == BASE


def test_skips_missing_top_level_key():
    # 顶层键不存在且没有父块可插入：不擅自创造结构
    result = ai.update_yaml_scalars(BASE, {("brand_new",): "x"})
    assert result == BASE
    assert "brand_new" not in result


def test_existing_key_update_never_inserts_new_line():
    result = ai.update_yaml_scalars(BASE, {("providers", "openai", "api_key"): "k"})
    assert len(result.splitlines()) == len(BASE.splitlines())


# ----------------------------------------------------------------------------
# 换行与标量渲染
# ----------------------------------------------------------------------------


def test_preserves_trailing_newline():
    assert ai.update_yaml_scalars(BASE, {("active_provider",): "x"}).endswith("\n")


def test_does_not_add_trailing_newline_when_absent():
    assert ai.update_yaml_scalars("a: 1", {("a",): 2}) == "a: 2"


def test_scalar_rendering_by_type():
    result = ai.update_yaml_scalars(
        "a: 1\nb: 1\nc: 1\nd: 1\n",
        {("a",): True, ("b",): False, ("c",): 3, ("d",): 0.5},
    )
    assert result.splitlines() == ["a: true", "b: false", "c: 3", "d: 0.5"]


def test_string_values_are_quoted_and_escaped():
    result = ai.update_yaml_scalars('k: "old"\n', {("k",): 'he said "hi" \\ back'})
    assert result == 'k: "he said \\"hi\\" \\\\ back"\n'
    # 转义后的文本必须能被 YAML 正确读回
    assert yaml.safe_load(result)["k"] == 'he said "hi" \\ back'


def test_quoted_key_is_matched():
    result = ai.update_yaml_scalars('"provider": "a"\n', {("provider",): "b"})
    assert result == '"provider": "b"\n'


# ----------------------------------------------------------------------------
# 与 YAML 解析器的一致性
# ----------------------------------------------------------------------------


def test_result_is_valid_yaml_with_expected_values():
    result = ai.update_yaml_scalars(
        BASE,
        {
            ("active_provider",): "aliyun",
            ("providers", "deepseek", "temperature"): 0.3,
            ("providers", "deepseek", "vision"): True,
        },
    )
    loaded = yaml.safe_load(result)
    assert loaded["active_provider"] == "aliyun"
    assert loaded["providers"]["deepseek"]["temperature"] == 0.3
    assert loaded["providers"]["deepseek"]["vision"] is True
    # 未更新的内容完全保留
    assert loaded["providers"]["openai"]["model"] == "gpt-4"
    assert loaded["providers"]["deepseek"]["api_key"] == "sk-old"


def test_repeated_update_is_stable():
    once = ai.update_yaml_scalars(BASE, {("providers", "deepseek", "api_key"): "sk-new"})
    twice = ai.update_yaml_scalars(once, {("providers", "deepseek", "api_key"): "sk-new"})
    assert once == twice


def test_roundtrip_through_yaml_load_and_dump_is_not_required():
    """回归基线：写回只做文本级替换，键顺序与块结构保持不变"""
    result = ai.update_yaml_scalars(BASE, {("providers", "deepseek", "api_key"): "sk-new"})
    keys = [line for line in result.splitlines() if line and not line.lstrip().startswith("#")]
    assert keys[0].strip().startswith("active_provider:")
    assert keys[1].strip() == "providers:"
    assert keys[2].strip() == "openai:"


# ----------------------------------------------------------------------------
# 底层定位辅助函数
# ----------------------------------------------------------------------------


def test_yaml_key_of_variants():
    assert ai._yaml_key_of("  a: 1") == (2, "a")
    assert ai._yaml_key_of('  "q": 2') == (2, "q")
    assert ai._yaml_key_of("# comment") == (-1, None)
    assert ai._yaml_key_of("") == (-1, None)
    assert ai._yaml_key_of("  - item") == (-1, None)
    assert ai._yaml_key_of("a: 1") == (0, "a")


def test_locate_yaml_key_respects_nesting():
    lines = BASE.splitlines()
    assert ai._locate_yaml_key(lines, ("providers", "deepseek", "model")) is not None
    assert ai._locate_yaml_key(lines, ("providers", "openai", "model")) is not None
    # 同名键在不同父块下不会串位
    assert ai._locate_yaml_key(lines, ("deepseek", "model")) is None
    assert ai._locate_yaml_key(lines, ("providers", "deepseek", "nope")) is None


def _line_with(text, needle):
    for line in text.splitlines():
        if needle in line:
            return line
    raise AssertionError(f"未找到包含 {needle!r} 的行")


# ----------------------------------------------------------------------------
# 整节写入 / 原子提交（渠道配置保存的真实回归）
# ----------------------------------------------------------------------------


def test_section_write_creates_missing_nested_section(tmp_path):
    """回归：渠道配置整节不存在时也要能写入，且不破坏后续内容。

    旧实现把父块后面的注释当成「子级」，把新键插到了下一个顶层键
    之后——整份 config.yaml 变成非法 YAML，下次启动直接起不来。
    """
    path = tmp_path / "config.yaml"
    path.write_text(
        "# 头注释\n"
        'active_provider: "deepseek"\n'
        "\n"
        "# ---- 渠道 ----\n"
        "# 整节注释掉 = 不启用\n"
        "# channels:\n"
        "#   napcat:\n"
        '#     url: ""\n'
        "\n"
        "memory:\n"
        '  engine: "embedding"\n',
        encoding="utf-8",
    )
    manager = ai.ConfigManager(str(path))
    manager.set_section_values(
        "channels.napcat",
        {
            "url": "ws://127.0.0.1:3001",
            "allowed_senders": [123456, 234567],
            "report_tools": False,
        },
    )

    text = path.read_text(encoding="utf-8")
    loaded = yaml.safe_load(text)
    assert loaded["channels"]["napcat"]["url"] == "ws://127.0.0.1:3001"
    assert loaded["channels"]["napcat"]["allowed_senders"] == [123456, 234567]
    assert loaded["memory"]["engine"] == "embedding"
    # 头注释与说明注释都必须原地保留
    assert "# 头注释" in text
    assert "# 整节注释掉 = 不启用" in text


def test_section_write_updates_existing_section(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "channels:\n"
        "  napcat:\n"
        '    url: "ws://127.0.0.1:3001"   # 保留这行注释\n'
        "    report_tools: false\n",
        encoding="utf-8",
    )
    manager = ai.ConfigManager(str(path))
    manager.set_section_values(
        "channels.napcat",
        {
            "url": "ws://127.0.0.1:5000",
            "report_tools": True,
        },
    )
    text = path.read_text(encoding="utf-8")
    assert "# 保留这行注释" in text
    loaded = yaml.safe_load(text)
    assert loaded["channels"]["napcat"] == {"url": "ws://127.0.0.1:5000", "report_tools": True}


def test_bad_write_is_rejected_without_touching_file(tmp_path):
    """写坏的文件绝不能落盘：宁可报错，也不能留下无法启动的配置。"""
    path = tmp_path / "config.yaml"
    original = "a: 1\nb:\n  c: 2\n"
    path.write_text(original, encoding="utf-8")
    manager = ai.ConfigManager(str(path))
    with pytest.raises(ValueError):
        manager._commit("a: 1\n b: 2\n")  # 缩进非法的文本
    assert path.read_text(encoding="utf-8") == original
    # 正常写入仍然成功
    manager.set_values({("a",): 2})
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["a"] == 2


# ----------------------------------------------------------------------------
# 损坏配置的自动修复（历史版本写坏的文件不能把程序永久卡死）
# ----------------------------------------------------------------------------


def test_repair_dedents_odd_block_and_keeps_channel_config():
    """旧版本的畸形产物：整个渠道节多缩进一格。修复后内容一个不丢。"""
    broken = (
        'active_provider: "deepseek"\n'
        "providers:\n"
        "  deepseek:\n"
        '    api_key: "sk-test"\n'
        "\n"
        "# 渠道说明\n"
        " channels:\n"
        "   napcat:\n"
        '     url: "ws://127.0.0.1:3001"\n'
        "     allowed_senders: [123]\n"
    )
    repaired = ai.repair_yaml_prefix(broken)
    assert repaired is not None
    fixed, dropped = repaired
    assert dropped == 0
    loaded = yaml.safe_load(fixed)
    assert loaded["channels"]["napcat"]["url"] == "ws://127.0.0.1:3001"
    assert loaded["channels"]["napcat"]["allowed_senders"] == [123]
    assert loaded["providers"]["deepseek"]["api_key"] == "sk-test"


def test_repair_truncates_unparsable_tail():
    """修不了的尾部（真写坏了）截断掉，但前面的配置必须保住。"""
    broken = (
        'active_provider: "deepseek"\n'
        "memory:\n"
        '  engine: "embedding"\n'
        "channels:\n"
        "  napcat:\n"
        '    url: "a"\n'
        "   report_tools: false\n"
    )
    repaired = ai.repair_yaml_prefix(broken)
    assert repaired is not None
    fixed, _dropped = repaired
    loaded = yaml.safe_load(fixed)
    assert loaded["active_provider"] == "deepseek"
    assert loaded["memory"]["engine"] == "embedding"


def test_repair_is_noop_for_valid_config():
    assert ai.repair_yaml_prefix(BASE) is None


def test_config_manager_heals_broken_file_and_keeps_backup(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        'active_provider: "deepseek"\n'
        "providers:\n"
        "  deepseek:\n"
        '    api_key: "sk-test"\n'
        " channels:\n"
        "   napcat:\n"
        '     url: "ws://127.0.0.1:3001"\n',
        encoding="utf-8",
    )
    manager = ai.ConfigManager(str(path))
    assert "自动修复" in manager.self_healed
    assert manager.get("channels.napcat.url") == "ws://127.0.0.1:3001"
    # 坏文件必须留下备份，用户可以自己核对被改了什么
    backups = list(tmp_path.glob("config.yaml.broken-*"))
    assert len(backups) == 1
    assert " channels:" in backups[0].read_text(encoding="utf-8")
    # 修好之后还能继续正常写入
    manager.set_section_values("channels.napcat", {"report_tools": True})
    assert ai.ConfigManager(str(path)).get("channels.napcat.report_tools") is True


def test_add_provider_block_keeps_section_comments(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "providers:\n"
        "  # 说明注释保持在最前面\n"
        "  deepseek:\n"
        '    api_key: "sk-a"\n'
        "\n"
        "channels:\n"
        "  napcat:\n"
        '    url: ""\n',
        encoding="utf-8",
    )
    manager = ai.ConfigManager(str(path))
    manager.set_provider_values("kimi", {"api_key": "sk-b", "model": "m"})
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert loaded["providers"]["kimi"] == {"api_key": "sk-b", "model": "m"}
    assert loaded["channels"]["napcat"]["url"] == ""
