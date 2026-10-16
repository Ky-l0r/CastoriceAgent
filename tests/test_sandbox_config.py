"""t11 修复轮轮回归测试：modes.<name>.sandbox 死配置与沙箱解析单点权威。

契约要求：config 的 modes.<name>.sandbox 覆盖该模式内置 default_sandbox；
default_sandbox 的全部读取点（网关装配 / available_modes 的 sandbox 与
default_sandbox / 会话投影 / _sync_permission_context）统一走同一个解析函数；
非法值记 warning 并回落内置默认，绝不把未校验值塞进 Mode
（WorkdirPolicy.__post_init__ 对未知沙箱模式抛 ValueError）。

回归断言的核心是**一致性**：path_policy.sandbox、available_modes 输出的
sandbox 与 default_sandbox、projection.snapshot().sandbox_mode 四者必须相等
且随配置变化——只改其中一处就会在这里失败。
"""

import json
import os
from pathlib import Path

import pytest

import ai
import castorice.session as session_module
from castorice.modes import SANDBOX_DANGER_FULL_ACCESS, SANDBOX_WORKSPACE_WRITE
from castorice.permissions import AutoResponder


# ----------------------------------------------------------------------------

# ----------------------------------------------------------------------------
# 夹具：造一个完全离线、目录全在 tmp_path 的 AI 实例
# ----------------------------------------------------------------------------

def write_config(root: Path, *, mode: str = "chat", sandbox: str = "",
                 mode_sandbox: str = "", extra: str = "") -> Path:
    lines = [
        "mode: " + mode,
        "active_provider: fake",
        "workdir: " + json.dumps(str(root)),
    ]
    if sandbox:
        lines += ["permissions:", "  sandbox_default: " + json.dumps(sandbox)]
    else:
        lines += ["permissions:", '  sandbox_default: ""']
    lines += [
        "modes:",
        "  code:",
        "    workdir: " + json.dumps(str(root)),
        "    sandbox: " + json.dumps(mode_sandbox),
        "  learn:",
        "    vault: " + json.dumps(str(root)),
        "    sandbox: " + json.dumps(mode_sandbox),
        "providers:",
        "  fake:",
        "    api_key: test-key",
        "    model: fake-model",
    ]
    if extra:
        lines.append(extra)
    config = root / "config.yaml"
    config.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return config


def scaffold(root: Path) -> None:
    (root / "prompts" / "modes").mkdir(parents=True, exist_ok=True)
    (root / "Image").mkdir(parents=True, exist_ok=True)
    (root / "prompts" / "base.md").write_text("BASE-PROMPT", encoding="utf-8")
    for section in ("work", "code", "learn"):
        (root / "prompts" / "modes" / (section + ".md")).write_text(
            "SECTION-" + section, encoding="utf-8")


@pytest.fixture
def make_bot(tmp_path):
    """返回 (构造器, 已释放实例列表)。用例结束自动 close，避免写者登记外泄。"""
    created = []

    def _make(case: str = "case", *, mode: str = "chat", sandbox: str = "",
              mode_sandbox: str = "", sessions_dir=None) -> ai.AI:
        root = tmp_path / case
        root.mkdir(parents=True, exist_ok=True)
        scaffold(root)
        config = write_config(root, mode=mode, sandbox=sandbox,
                              mode_sandbox=mode_sandbox)
        bot = ai.AI(
            config_path=str(config),
            prompts_dir=str(root / "prompts"),
            chroma_dir=str(root / "chroma"),
            provider_name="fake",
            image_dir=str(root / "Image"),
            sessions_dir=str(sessions_dir or (root / "sessions")),
        )
        created.append(bot)
        return bot

    yield _make
    for bot in created:
        try:
            bot.close()
        except Exception:
            pass
# ============================================================================
# D. 沙箱单点权威：modes.<name>.sandbox 必须真实生效且三处一致
# ============================================================================

@pytest.mark.parametrize("global_sb, mode_sb, expected", [
    # 全局缺省 + 模式级留空 -> 内置默认
    ("", "", SANDBOX_WORKSPACE_WRITE),
    # 只有全局 -> 全局
    (SANDBOX_DANGER_FULL_ACCESS, "", SANDBOX_DANGER_FULL_ACCESS),
    # 只有模式级 -> 模式级
    ("", SANDBOX_DANGER_FULL_ACCESS, SANDBOX_DANGER_FULL_ACCESS),
    # 两者都有 -> 更具体的模式级优先
    (SANDBOX_WORKSPACE_WRITE, SANDBOX_DANGER_FULL_ACCESS,
     SANDBOX_DANGER_FULL_ACCESS),
    # 非法值一律回落到内置默认，不抛异常
    ("yolo", "", SANDBOX_WORKSPACE_WRITE),
    ("", "bogus", SANDBOX_WORKSPACE_WRITE),
    ("yolo", "bogus", SANDBOX_WORKSPACE_WRITE),
])
def test_sandbox_resolution_priority(make_bot, global_sb, mode_sb, expected):
    bot = make_bot(f"sb-{global_sb or 'empty'}-{mode_sb or 'empty'}",
                   mode="code", sandbox=global_sb, mode_sandbox=mode_sb)
    assert bot.resolve_sandbox("code") == expected



def test_sandbox_is_consistent_across_all_read_points(make_bot):
    """网关 / 面板 / 投影三者必须一致，且都随配置变化。

    这正是「不得只改一处」的验收：只要有一个读取点还取内置默认值，
    就会出现「网关按配置跑、面板显示内置值」的更糟不一致。
    """
    bot = make_bot("consistent", mode="code",
                   sandbox=SANDBOX_DANGER_FULL_ACCESS, mode_sandbox="")
    meta = [m for m in bot.available_modes() if m["name"] == "code"][0]

    values = {
        "gateway.path_policy.sandbox": bot.permissions.path_policy.sandbox,
        "available_modes.sandbox": meta["sandbox"],
        "available_modes.default_sandbox": meta["workdir_policy"]["default_sandbox"],
        "projection.sandbox_mode": bot.projection.snapshot().sandbox_mode,
        "resolve_sandbox": bot.resolve_sandbox("code"),
    }
    assert set(values.values()) == {SANDBOX_DANGER_FULL_ACCESS}, values


def test_sandbox_config_change_is_visible_everywhere(make_bot):
    """改配置后三处一起变（证明不是把内置值硬编码回去）。"""
    bot = make_bot("change", mode="code", sandbox="", mode_sandbox="")
    assert bot.permissions.path_policy.sandbox == SANDBOX_WORKSPACE_WRITE

    bot.config_manager.set_values({("permissions", "sandbox_default"):
                                   SANDBOX_DANGER_FULL_ACCESS})
    bot._sync_permission_context()

    meta = [m for m in bot.available_modes() if m["name"] == "code"][0]
    assert bot.permissions.path_policy.sandbox == SANDBOX_DANGER_FULL_ACCESS
    assert meta["sandbox"] == SANDBOX_DANGER_FULL_ACCESS
    assert meta["workdir_policy"]["default_sandbox"] == SANDBOX_DANGER_FULL_ACCESS
    assert bot.projection.snapshot().sandbox_mode == SANDBOX_DANGER_FULL_ACCESS
    assert bot.resolve_sandbox("code") == SANDBOX_DANGER_FULL_ACCESS


def test_mode_level_sandbox_overrides_the_global_default(make_bot):
    """模式级配置比全局缺省更具体，必须由它胜出。"""
    bot = make_bot("override", mode="code",
                   sandbox=SANDBOX_WORKSPACE_WRITE,
                   mode_sandbox=SANDBOX_DANGER_FULL_ACCESS)
    assert bot.resolve_sandbox("code") == SANDBOX_DANGER_FULL_ACCESS
    assert bot.permissions.path_policy.sandbox == SANDBOX_DANGER_FULL_ACCESS


def test_illegal_sandbox_values_fall_back_and_are_logged(make_bot, caplog):
    """非法值必须 warning 后回落内置默认，且不得把程序带崩。

    WorkdirPolicy.__post_init__ 对未知沙箱模式抛 ValueError，
    所以未经校验的配置值绝不能直接塞进 Mode——这条用例钉死这个边界。
    """
    import logging

    with caplog.at_level(logging.WARNING):
        bot = make_bot("illegal", mode="code", sandbox="yolo", mode_sandbox="bogus")
        resolved = bot.resolve_sandbox("code")

    assert resolved == SANDBOX_WORKSPACE_WRITE
    assert bot.permissions.path_policy.sandbox == SANDBOX_WORKSPACE_WRITE
    meta = [m for m in bot.available_modes() if m["name"] == "code"][0]
    assert meta["sandbox"] == SANDBOX_WORKSPACE_WRITE
    assert bot.projection.snapshot().sandbox_mode == SANDBOX_WORKSPACE_WRITE
    assert any("不是合法沙箱模式" in record.message
               for record in caplog.records), "非法配置必须留下 warning"


def test_builtin_sandbox_is_still_reported(make_bot):
    """面板仍能读到模式的出厂默认值（用于区分「被配置覆盖」）。"""
    bot = make_bot("builtin", mode="code",
                   sandbox=SANDBOX_DANGER_FULL_ACCESS, mode_sandbox="")
    meta = [m for m in bot.available_modes() if m["name"] == "code"][0]
    assert meta["workdir_policy"]["builtin_sandbox"] == SANDBOX_WORKSPACE_WRITE
    assert meta["workdir_policy"]["default_sandbox"] == SANDBOX_DANGER_FULL_ACCESS


def test_available_modes_does_not_fall_back_to_chat(make_bot):
    """回归：available_modes 传入 Mode 对象时不得被误解析成 chat。

    早前 _resolve_mode_object 缺位时，resolve_sandbox(Mode) 会经
    resolve_mode_name 判定非法并回退 chat，导致面板显示 chat 的 read-only。
    """
    bot = make_bot("nochat", mode="code", sandbox="", mode_sandbox="")
    for meta in bot.available_modes():
        if meta["name"] == "chat":
            assert meta["sandbox"] == "read-only"
        else:
            assert meta["sandbox"] == SANDBOX_WORKSPACE_WRITE, meta["name"]


def test_configured_sandbox_actually_gates_writes(make_bot, tmp_path):
    """配置生效不能只体现在展示层：read-only 时写入必须真的被拒。"""
    bot = make_bot("gate", mode="code", sandbox="read-only", mode_sandbox="")
    root = tmp_path / "gate"
    bot.bind_mode_workdir("code", str(root))
    bot.set_approval_responder(AutoResponder("allow"))

    target = root / "x.txt"
    result = bot._kernel_tools.call(
        "write_file", {"path": str(target), "content": "x"})
    assert result.startswith("错误:")
    assert not target.exists(), "read-only 沙箱下写入竟然落盘了"
