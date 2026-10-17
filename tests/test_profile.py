"""用户档案（castorice.profile）与外观设置 RPC 测试。

profile.json 走 ENV 覆盖指到临时目录（绝不写仓库根目录）；bridge 侧
用真实回环 WS 客户端走完整 RPC 路径（夹具同 tests/test_bridge.py）。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from castorice.profile import (
    ENV_PATH,
    FIELD_LABELS,
    PROFILE_FIELDS,
    UserProfile,
    memory_query_suffix,
    system_prompt_section,
)

# 模块导入期保存真实 connect（conftest 的 block_network 尚未打补丁）。
_REAL_CONNECT = __import__("socket").socket.connect


@pytest.fixture(autouse=True)
def _profile_env(tmp_path, monkeypatch):
    """profile.json / ui_settings.json 都指到临时目录。"""
    monkeypatch.setenv(ENV_PATH, str(tmp_path / "profile.json"))
    monkeypatch.setenv("CASTORICE_UI_SETTINGS", str(tmp_path / "ui_settings.json"))
    yield tmp_path


@pytest.fixture(autouse=True)
def _allow_loopback_connect(monkeypatch):
    """放行回环 connect（本文件的本地 WS 端点）。"""
    import socket

    original = _REAL_CONNECT

    def _guarded(self, address):
        host = address[0] if isinstance(address, tuple) else ""
        if isinstance(host, str) and host in ("127.0.0.1", "::1", "localhost"):
            return original(self, address)
        raise RuntimeError("测试期间禁止非回环连接")

    monkeypatch.setattr(socket.socket, "connect", _guarded)


# ----------------------------------------------------------------------------
# UserProfile 基础读写
# ----------------------------------------------------------------------------


def test_load_returns_empty_dict_when_file_missing():
    assert UserProfile.load() == {}


def test_save_values_writes_and_merges():
    UserProfile.save_values({"nickname": "开拓者", "city": "上海"})
    UserProfile.save_values({"appellation": "阁下"})
    data = UserProfile.load()
    assert data["nickname"] == "开拓者"
    assert data["city"] == "上海"
    assert data["appellation"] == "阁下"


def test_save_values_rejects_fields_outside_whitelist():
    UserProfile.save_values({"nickname": "A", "evil_key": "x"})
    data = UserProfile.load()
    assert data["nickname"] == "A"
    assert "evil_key" not in data


def test_save_values_none_clears_field():
    UserProfile.save_values({"nickname": "A"})
    UserProfile.save_values({"nickname": None})
    assert UserProfile.load()["nickname"] is None


def test_get_returns_str_with_default():
    assert UserProfile.get("missing", "默认") == "默认"
    UserProfile.save_values({"birthday": "2000-01-01"})
    assert UserProfile.get("birthday") == "2000-01-01"


def test_display_name_prefers_appellation():
    assert UserProfile.display_name() == ""
    UserProfile.save_values({"nickname": "开拓者", "appellation": "阁下"})
    assert UserProfile.display_name() == "阁下"
    UserProfile.save_values({"appellation": None})
    assert UserProfile.display_name() == "开拓者"


def test_corrupt_file_degrades_to_empty(tmp_path):
    path = Path(os.environ[ENV_PATH])
    path.write_text("{not json", encoding="utf-8")
    assert UserProfile.load() == {}
    # 且能照常覆盖写入（读-改-写不炸）
    UserProfile.save_values({"nickname": "B"})
    assert UserProfile.load()["nickname"] == "B"


def test_profile_fields_and_labels_align():
    assert set(FIELD_LABELS) == set(PROFILE_FIELDS)


# ----------------------------------------------------------------------------
# 提示词小节 / 记忆检索后缀
# ----------------------------------------------------------------------------


def test_system_prompt_section_empty_without_profile():
    assert system_prompt_section() == ""


def test_system_prompt_section_injects_all_fields():
    UserProfile.save_values({
        "appellation": "阁下",
        "nickname": "开拓者",
        "gender": "女",
        "birthday": "01-01",
        "city": "上海",
        "timezone": "Asia/Shanghai",
    })
    section = system_prompt_section()
    assert section.startswith("\n\n【用户档案】")
    assert "「阁下」" in section
    assert "「开拓者」" in section
    assert "女" in section
    assert "01-01" in section
    assert "上海" in section
    assert "Asia/Shanghai" in section


def test_system_prompt_section_only_nickname():
    UserProfile.save_values({"nickname": "阿星"})
    section = system_prompt_section()
    assert "「阿星」" in section
    assert "性别" not in section


def test_memory_query_suffix_appends_city():
    assert memory_query_suffix() == ""
    UserProfile.save_values({"city": "上海"})
    assert memory_query_suffix() == " 上海"


# ----------------------------------------------------------------------------
# ai.py 注入链路（_format_now 时区 / system prompt 拼接 / query 拼接）
# ----------------------------------------------------------------------------


def test_format_now_without_timezone_is_legacy_format():
    import ai

    text = ai.AI._format_now()
    assert "时区" not in text
    assert "星期" in text


def test_format_now_with_timezone_renders_that_zone():
    import ai
    from datetime import datetime

    UserProfile.save_values({"timezone": "Asia/Shanghai"})
    text = ai.AI._format_now(datetime(2026, 10, 3, 16, 20, 15))
    assert "2026-10-03 16:20:15" in text
    assert "星期六" in text
    assert "Asia/Shanghai" in text


def test_format_now_with_bad_timezone_falls_back_to_local():
    import ai
    from datetime import datetime

    UserProfile.save_values({"timezone": "Mars/Olympus"})
    text = ai.AI._format_now(datetime(2026, 10, 3, 16, 20, 15))
    # 时刻回退机器本地渲染（时区不可识别），但注明了这一点
    assert "2026-10-03" in text
    assert "Mars/Olympus" in text
    assert "未能识别" in text


def test_build_system_prompt_includes_profile_section():
    """_build_system_prompt 拼接【用户档案】小节（最小 AI 替身）。"""
    import ai

    UserProfile.save_values({"appellation": "阁下", "city": "上海"})

    class PromptStub:
        def get_prompt(self, memory_context=None, mode=None, user_message=None):
            return "BASE"

    bot = ai.AI.__new__(ai.AI)
    bot.prompt_manager = PromptStub()
    bot.emoji_catalog = None
    bot._mode_registry = type("R", (), {
        "visible_tool_names": staticmethod(lambda mode: []),
        "names": staticmethod(lambda: ["chat"]),
        "get": staticmethod(lambda name: type("M", (), {
            "system_prompt_sections": (),
        })()),
    })()

    prompt = bot._build_system_prompt(mode="chat", user_message="hi")
    assert prompt.startswith("BASE")
    assert "【用户档案】" in prompt
    assert "「阁下」" in prompt
    # 无可见工具时不含工具指引（R19-1 既有语义不回退）
    assert "get_current_time" not in prompt


def test_get_response_stream_query_carries_city():
    """get_response_stream 的记忆检索 query 附带城市后缀（源码级断言）。"""
    import ai
    import inspect

    src = inspect.getsource(ai.AI.get_response_stream)
    assert "memory_query_suffix" in src



# ----------------------------------------------------------------------------
# bridge RPC：profile.get / profile.set / ui_settings.get / ui_settings.set
# ----------------------------------------------------------------------------

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_bridge import BridgeClient, make_bot  # noqa: E402

from castorice.bridge import BridgeServer  # noqa: E402


@pytest.fixture()
def server_and_client():
    """起一个真实桥接服务器并完成握手（同 test_bridge.py 的夹具）。"""
    bot = make_bot()
    server = BridgeServer(bot)
    port = server.start()
    client = BridgeClient(port, server.token)
    yield client
    client.close()
    server.close()


def test_profile_get_returns_whitelist_defaults(server_and_client):
    frame = server_and_client.rpc("profile.get")
    assert frame["type"] == "rpc_result"
    assert frame["result"]["profile"] == {k: None for k in PROFILE_FIELDS}


def test_profile_set_roundtrip(server_and_client):
    frame = server_and_client.rpc(
        "profile.set", {"updates": {"nickname": "开拓者", "city": "上海"}})
    assert frame["type"] == "rpc_result"
    assert frame["result"]["profile"]["city"] == "上海"
    frame = server_and_client.rpc("profile.get")
    assert frame["result"]["profile"]["nickname"] == "开拓者"


def test_profile_set_rejects_unknown_field(server_and_client):
    frame = server_and_client.rpc(
        "profile.set", {"updates": {"hacker": "x"}})
    assert frame["type"] == "rpc_error"
    assert "未知档案字段" in frame["error"]["message"]


def test_profile_set_rejects_empty_updates(server_and_client):
    frame = server_and_client.rpc("profile.set", {"updates": {}})
    assert frame["type"] == "rpc_error"


def test_profile_set_rejects_overlong_value(server_and_client):
    frame = server_and_client.rpc(
        "profile.set", {"updates": {"nickname": "x" * 201}})
    assert frame["type"] == "rpc_error"
    assert "过长" in frame["error"]["message"]


def test_profile_set_none_clears_field(server_and_client):
    server_and_client.rpc("profile.set", {"updates": {"city": "上海"}})
    frame = server_and_client.rpc("profile.set", {"updates": {"city": None}})
    assert frame["type"] == "rpc_result"
    assert frame["result"]["profile"]["city"] is None


def test_ui_settings_set_then_get(server_and_client):
    frame = server_and_client.rpc(
        "ui_settings.set", {"updates": {"theme": "dark", "radius": 16}})
    assert frame["type"] == "rpc_result"
    assert frame["result"]["settings"]["theme"] == "dark"
    frame = server_and_client.rpc("ui_settings.get")
    assert frame["type"] == "rpc_result"
    assert frame["result"]["settings"]["radius"] == 16


def test_ui_settings_merge_preserves_other_keys(server_and_client):
    server_and_client.rpc("ui_settings.set", {"updates": {"theme": "dark"}})
    server_and_client.rpc("ui_settings.set", {"updates": {"ui_mode": "chat"}})
    frame = server_and_client.rpc("ui_settings.get")
    settings = frame["result"]["settings"]
    assert settings["theme"] == "dark"
    assert settings["ui_mode"] == "chat"


def test_ui_settings_set_rejects_empty_updates(server_and_client):
    frame = server_and_client.rpc("ui_settings.set", {"updates": {}})
    assert frame["type"] == "rpc_error"


def test_ui_settings_compat_with_ui_py_keys(server_and_client):
    """向后兼容：theme / ui_mode 键写出的文件形态与 ui.py 约定一致。"""
    server_and_client.rpc(
        "ui_settings.set", {"updates": {"theme": "light", "ui_mode": "work"}})
    path = Path(os.environ["CASTORICE_UI_SETTINGS"])
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == {"theme": "light", "ui_mode": "work"}
