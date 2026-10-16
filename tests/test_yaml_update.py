"""update_yaml_scalars 回归测试

关注点：写回 config.yaml 时不得破坏用户原有的注释与排版。
「设置」窗口保存后配置文件仍需保持人类可读，这是本期改造的前置基线。
"""

import yaml

import ai

BASE = '''# config.yaml - 支持多种LLM提供商

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
'''


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
    assert line.endswith('# 可选: openai, deepseek, aliyun, etc.')
    # 注释前的空白也要原样保留
    assert line == 'active_provider: "aliyun"  # 可选: openai, deepseek, aliyun, etc.'


def test_keeps_all_other_lines_byte_identical():
    result = ai.update_yaml_scalars(
        BASE, {("providers", "deepseek", "api_key"): "sk-new"}
    )
    before = BASE.splitlines()
    after = result.splitlines()
    assert len(before) == len(after)
    for old, new in zip(before, after):
        if old.startswith("    api_key: \"sk-old\""):
            assert new == '    api_key: "sk-new"        # 别提交'
        else:
            assert old == new


def test_keeps_indent_and_section_comments():
    result = ai.update_yaml_scalars(
        BASE, {("providers", "deepseek", "temperature"): 0.3}
    )
    assert "  # OpenAI" in result
    assert "  # DeepSeek" in result
    assert "    temperature: 0.3" in result
    # openai 段的值不受影响
    assert "    temperature: 0.7" in result


def test_multiple_updates_apply_in_one_pass():
    result = ai.update_yaml_scalars(BASE, {
        ("active_provider",): "aliyun",
        ("providers", "openai", "model"): "gpt-4o",
        ("providers", "deepseek", "model"): "deepseek-reasoner",
    })
    assert 'active_provider: "aliyun"' in result
    assert 'model: "gpt-4o"' in result
    assert 'model: "deepseek-reasoner"' in result


# ----------------------------------------------------------------------------
# 键不存在时的插入规则
# ----------------------------------------------------------------------------

def test_inserts_missing_key_at_end_of_parent_block():
    result = ai.update_yaml_scalars(
        BASE, {("providers", "deepseek", "vision"): True}
    )
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
    result = ai.update_yaml_scalars(
        "k: \"old\"\n", {("k",): 'he said "hi" \\ back'}
    )
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
    result = ai.update_yaml_scalars(BASE, {
        ("active_provider",): "aliyun",
        ("providers", "deepseek", "temperature"): 0.3,
        ("providers", "deepseek", "vision"): True,
    })
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
