"""ConfigManager 回归测试

覆盖 get_provider_config / set_active_provider（以及它们依赖的读写链路）。
所有配置都写在 pytest 临时目录，不触碰仓库里的 config.yaml，也不需要 API Key。
"""

import pytest
import yaml

import ai

CONFIG = '''# config.yaml - 支持多种LLM提供商

# 当前激活的提供商
active_provider: "deepseek"  # 可选: openai, deepseek, aliyun

providers:
  # DeepSeek
  deepseek:
    api_key: ""              # 留空也算已配置
    base_url: "https://api.deepseek.com"
    model: "deepseek-chat"
    temperature: 0.7

  # 阿里云
  aliyun:
    api_key: "sk-aliyun"
    base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"
    model: "qwen-plus"
    temperature: 0.5
'''


@pytest.fixture
def cm(write_config):
    return ai.ConfigManager(str(write_config(CONFIG)))


# ----------------------------------------------------------------------------
# get_provider_config
# ----------------------------------------------------------------------------

def test_get_provider_config_returns_provider_block(cm):
    config = cm.get_provider_config("deepseek")
    assert config["model"] == "deepseek-chat"
    assert config["temperature"] == 0.7
    assert config["base_url"] == "https://api.deepseek.com"


def test_get_provider_config_second_provider(cm):
    assert cm.get_provider_config("aliyun")["model"] == "qwen-plus"


def test_get_provider_config_empty_api_key_is_still_a_configured_provider(cm):
    # api_key 为空字符串时不能当成「未配置」而被误判
    assert cm.get_provider_config("deepseek")["api_key"] == ""


def test_get_provider_config_unknown_provider_raises(cm):
    with pytest.raises(ValueError, match="未找到提供商配置: nope"):
        cm.get_provider_config("nope")


def test_get_provider_config_does_not_require_api_key(cm):
    """没有可用的 API Key 也必须能读出配置（离线可用）"""
    for name in cm.provider_names:
        assert isinstance(cm.get_provider_config(name), dict)


def test_provider_names(cm):
    assert cm.provider_names == ["deepseek", "aliyun"]


# ----------------------------------------------------------------------------
# get_active_provider
# ----------------------------------------------------------------------------

def test_get_active_provider(cm):
    assert cm.get_active_provider() == "deepseek"


def test_get_active_provider_missing_raises(write_config):
    manager = ai.ConfigManager(str(write_config("providers:\n  x:\n    api_key: ''\n")))
    with pytest.raises(ValueError, match="未配置 active_provider"):
        manager.get_active_provider()


# ----------------------------------------------------------------------------
# set_active_provider
# ----------------------------------------------------------------------------

def test_set_active_provider_updates_memory(cm):
    cm.set_active_provider("aliyun")
    assert cm.get_active_provider() == "aliyun"


def test_set_active_provider_persists_to_disk(cm):
    cm.set_active_provider("aliyun")
    on_disk = yaml.safe_load(cm.config_path.read_text(encoding="utf-8"))
    assert on_disk["active_provider"] == "aliyun"


def test_set_active_provider_keeps_comments_and_layout(cm):
    cm.set_active_provider("aliyun")
    text = cm.config_path.read_text(encoding="utf-8")
    assert "# config.yaml - 支持多种LLM提供商" in text
    assert "# 当前激活的提供商" in text
    assert "# DeepSeek" in text
    assert "# 阿里云" in text
    assert 'active_provider: "aliyun"  # 可选: openai, deepseek, aliyun' in text


def test_set_active_provider_only_touches_target_line(cm):
    before = cm.config_path.read_text(encoding="utf-8").splitlines()
    cm.set_active_provider("aliyun")
    after = cm.config_path.read_text(encoding="utf-8").splitlines()
    assert len(before) == len(after)
    diffs = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
    assert diffs == [3]


def test_set_active_provider_reloads_into_memory(cm):
    cm.set_active_provider("aliyun")
    assert cm.config["active_provider"] == "aliyun"
    # 其它提供商的配置没有被连带改动
    assert cm.get_provider_config("deepseek")["model"] == "deepseek-chat"


def test_set_active_provider_is_idempotent(cm):
    cm.set_active_provider("aliyun")
    first = cm.config_path.read_text(encoding="utf-8")
    cm.set_active_provider("aliyun")
    assert cm.config_path.read_text(encoding="utf-8") == first


def test_switching_back_and_forth(cm):
    cm.set_active_provider("aliyun")
    cm.set_active_provider("deepseek")
    assert cm.get_active_provider() == "deepseek"


# ----------------------------------------------------------------------------
# set_provider_values / set_values
# ----------------------------------------------------------------------------

def test_set_provider_values_updates_and_persists(cm):
    cm.set_provider_values("deepseek", {"api_key": "sk-new", "temperature": 0.2})
    config = cm.get_provider_config("deepseek")
    assert config["api_key"] == "sk-new"
    assert config["temperature"] == 0.2
    on_disk = yaml.safe_load(cm.config_path.read_text(encoding="utf-8"))
    assert on_disk["providers"]["deepseek"]["api_key"] == "sk-new"


def test_set_provider_values_inserts_new_field(cm):
    cm.set_provider_values("deepseek", {"vision": True})
    assert cm.get_provider_config("deepseek")["vision"] is True
    text = cm.config_path.read_text(encoding="utf-8")
    assert "    vision: true\n" in text


def test_reload_picks_up_external_change(cm):
    cm.set_active_provider("aliyun")
    cm.config_path.write_text(
        cm.config_path.read_text(encoding="utf-8").replace('"aliyun"', '"deepseek"'),
        encoding="utf-8",
    )
    # 内存里仍是旧值，reload 后才同步
    assert cm.get_active_provider() == "aliyun"
    cm.reload()
    assert cm.get_active_provider() == "deepseek"


# ----------------------------------------------------------------------------
# get / 错误路径
# ----------------------------------------------------------------------------

def test_get_dotted_path_and_default(cm):
    assert cm.get("providers.deepseek.model") == "deepseek-chat"
    assert cm.get("providers.deepseek.nope", "fallback") == "fallback"
    assert cm.get("not.there", 42) == 42


def test_missing_config_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        ai.ConfigManager(str(tmp_path / "no-such-config.yaml"))


def test_invalid_yaml_raises_value_error(write_config):
    with pytest.raises(ValueError, match="配置文件格式错误"):
        ai.ConfigManager(str(write_config("a: [1,\n")))


def test_real_config_yaml_is_loadable():
    """仓库自带的 config.yaml 必须能被 ConfigManager 正常解析"""
    from pathlib import Path

    root_config = Path(ai.__file__).resolve().parent / "config.yaml"
    if not root_config.exists():
        pytest.skip("仓库未提供 config.yaml")
    manager = ai.ConfigManager(str(root_config))
    assert manager.get_active_provider()
    assert manager.get_provider_config(manager.get_active_provider())
