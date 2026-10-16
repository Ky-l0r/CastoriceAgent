"""t11 修复轮回归测试：内核工具出口模式、会话写者登记。

本文件对应 t8 验证提出的 F2（内核工具出口 mode 恒为 chat）/ F3（写者登记泄漏）/ F6（缺少公开 mode 属性）。
沙箱配置那一组在 tests/test_sandbox_config.py。

设计要点（这是 t8 指出的漏检原因，必须严格遵守）：
**所有用例都经由 ai.AI 的真实路径**，而不是单独构造 ToolRuntime / SessionStore。
现有 tests/test_tools.py 只单独构造 ToolRuntime(..., mode='code')，
从未经过 ai.AI._kernel_tools，因此 F2 这类「组装层漏接线」的缺陷完全测不出来。
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
# A. F2：内核工具出口的模式必须跟随当前模式
# ============================================================================

@pytest.mark.parametrize("mode", ["chat", "work", "code", "learn"])
def test_tools_mode_follows_the_active_mode(make_bot, mode):
    """F2 回归：四者的模式必须一致，否则工具会在出口被按 chat 拒掉。"""
    bot = make_bot("case-" + mode, mode=mode)
    assert bot.mode == mode
    assert bot.harness.mode_name == mode
    assert bot.permissions.mode == mode
    assert bot.tools_mode == mode, (
        "ai.AI._kernel_tools 的出口模式没有跟随模式切换（F2 回归）")


def test_tools_mode_follows_switching(make_bot):
    """切换模式后出口模式必须一起变（_sync_permission_context 的职责）。"""
    bot = make_bot(mode="chat")
    for name in ["code", "learn", "work", "chat"]:
        bot.set_mode(name, persist=False)
        assert bot.tools_mode == name, f"切到 {name} 后出口模式没跟上"
        assert bot.harness.mode_name == name
        assert bot.permissions.mode == name


def test_bind_workdir_keeps_tools_mode_in_sync(make_bot, tmp_path):
    """绑定目录会刷新权限上下文，出口模式不能被顺手改回 chat。"""
    bot = make_bot("case-bind", mode="code")
    bot.bind_mode_workdir("code", str(tmp_path / "case-bind"))
    assert bot.tools_mode == "code"
    assert bot.permissions.mode == "code"


def test_read_file_through_the_real_kernel_exit(make_bot, tmp_path):
    """端到端：code 模式下经 ai.AI._kernel_tools 真实读出文件内容。

    这正是 t8 指出的漏检点——单独构造 ToolRuntime(mode='code') 测不出
    「组装层把 mode 漏成 chat」，必须经由 ai.AI 的真实对象。
    """
    bot = make_bot("case-read", mode="code")
    root = tmp_path / "case-read"
    target = root / "note.md"
    target.write_text("真实文件内容\n第二行\n", encoding="utf-8")
    bot.bind_mode_workdir("code", str(root))

    result = bot._kernel_tools.call("read_file", {"path": str(target)})

    assert "真实文件内容" in result, f"code 模式下 read_file 未返回内容: {result[:200]}"
    assert "第二行" in result
    assert "PERMISSION_DENIED" not in result
    assert not result.startswith("错误:")


def test_write_file_through_the_real_kernel_exit(make_bot, tmp_path):
    """端到端：注入应答者后，work 模式的 write_file 经审批真实落盘。"""
    bot = make_bot("case-write", mode="work")
    root = tmp_path / "case-write"
    bot.bind_mode_workdir("work", str(root))
    bot.set_approval_responder(AutoResponder("allow"))

    target = root / "out.txt"
    result = bot._kernel_tools.call(
        "write_file", {"path": str(target), "content": "hello"})

    assert "已写入" in result, f"写入未成功: {result[:200]}"
    assert target.exists(), "工具报告成功但文件没有真实落盘"
    assert target.read_text(encoding="utf-8") == "hello"


def test_write_file_is_denied_without_a_responder(make_bot, tmp_path):
    """反面：没有应答者时必须按拒绝关闭，且文件确实没被创建。"""
    bot = make_bot("case-deny", mode="work")
    root = tmp_path / "case-deny"
    bot.bind_mode_workdir("work", str(root))

    target = root / "never.txt"
    result = bot._kernel_tools.call(
        "write_file", {"path": str(target), "content": "x"})

    # 断言安全属性本身（拒绝且未落盘），而不是某一个具体错误码字符串：
    # 网关可能给出更具体的码（APPROVAL_NO_RESPONDER / APPROVAL_POLICY_NEVER）
    assert result.startswith("错误:")
    assert not target.exists(), "没有应答者时写入竟然静默落盘了"


def test_deny_responder_blocks_the_write(make_bot, tmp_path):
    """反面：应答者明确拒绝时同样不落盘。"""
    bot = make_bot("case-deny2", mode="work")
    root = tmp_path / "case-deny2"
    bot.bind_mode_workdir("work", str(root))
    bot.set_approval_responder(AutoResponder("deny"))

    target = root / "denied.txt"
    result = bot._kernel_tools.call(
        "write_file", {"path": str(target), "content": "x"})

    assert result.startswith("错误:")
    assert not target.exists(), "应答者明确拒绝后写入竟然落盘了"


def test_chat_mode_still_cannot_use_tools(make_bot, tmp_path):
    """红线 E 不因本次修复而松动：Chat 下工具仍然不可见。"""
    bot = make_bot("case-chat", mode="chat")
    root = tmp_path / "case-chat"
    target = root / "note.md"
    target.write_text("x", encoding="utf-8")

    result = bot._kernel_tools.call("read_file", {"path": str(target)})
    assert result.startswith("错误:")
    assert not bot.tool_visible("read_file")


def test_run_command_through_the_real_kernel_exit(make_bot, tmp_path):
    """端到端：Code 模式可执行白名单内的命令（走 ai.AI 的真实出口）。"""
    import sys as _sys

    bot = make_bot("case-cmd", mode="code")
    root = tmp_path / "case-cmd"
    bot.bind_mode_workdir("code", str(root))
    bot.set_approval_responder(AutoResponder("allow"))

    # python 不在内置白名单里，先确认被拒
    denied = bot._kernel_tools.call(
        "run_command", {"argv": [_sys.executable, "--version"]})
    assert denied.startswith("错误:")
    assert "白名单" in denied

    # 把 python 追加进白名单后应真实执行并返回退出码
    bot.permissions.configure(mode_command_allowlist={
        "code": ("dir", "ls", "python"), "learn": ()})
    allowed = bot._kernel_tools.call(
        "run_command", {"argv": [_sys.executable, "--version"]})
    assert "退出码" in allowed, f"白名单命令未执行: {allowed[:200]}"


# ============================================================================
# B. F6：AI.tools_mode 只读属性
# ============================================================================

def test_tools_mode_is_read_only(make_bot):
    bot = make_bot(mode="code")
    with pytest.raises(AttributeError):
        bot.tools_mode = "chat"


def test_tools_mode_matches_kernel_object(make_bot):
    bot = make_bot(mode="learn")
    assert bot.tools_mode == bot._kernel_tools.mode


def test_kernel_tools_exposes_mode_property():
    """F6：_KernelTools 必须有公开的 mode 属性（转发 ToolRuntime.mode）。"""
    assert isinstance(ai._KernelTools.mode, property)


# ============================================================================
# C. F3：会话写者登记不随实例数增长
# ============================================================================

def test_repeated_construction_does_not_leak_writer_registrations(make_bot, tmp_path):
    """F3 回归：连续构造 N 个 AI 实例后 _ACTIVE_WRITERS 不增长。

    旧实现里 _placeholder_session 用一个独立 session_id 建会话再丢弃，
    每构造一个实例就永久占用一条写者登记。
    """
    baseline = len(session_module._ACTIVE_WRITERS)
    bots = [make_bot(f"leak-{i}", mode="chat") for i in range(5)]

    after_construct = len(session_module._ACTIVE_WRITERS)
    assert after_construct - baseline <= 5, (
        f"构造 5 个实例登记了 {after_construct - baseline} 条写者")

    # 每个实例只应持有它自己的那份会话
    for index, bot in enumerate(bots):
        assert len(bot._sessions) <= 1, (
            f"实例 {index} 持有 {list(bot._sessions)}，占用了多余登记")

    for bot in bots:
        bot.close()
    assert len(session_module._ACTIVE_WRITERS) == baseline, (
        "close() 之后写者登记没有回到基线")


# ============================================================================
# D. N1：learn 模式不得整表禁用（t12 发现 / t15 修复轮）
# ============================================================================

#: modes.py 里 LEARN_ALLOWED 声明的 8 个工具（这里用字面量钉住契约）
LEARN_DECLARED = {
    "get_current_time", "read_file", "list_dir", "glob_files", "grep_files",
    "search_notes", "read_note", "write_note",
}

#: deny-ALL 失效路径曾放行、修复后必须收回的工具
LEARN_FORBIDDEN = {
    "play_music", "set_reminder", "clear_reminders", "list_reminders",
    "system_info",
}


def test_learn_visible_tools_exactly_match_the_declaration(make_bot):
    """N1 主判据：learn 的可见集合必须与 LEARN_ALLOWED 声明**集合相等**。

    修复前：declared 传的是模式名集合，LEARN_ALLOWED ∩ available 恒空，
    learn 退化成 deny-ALL，可见的只剩 5 个拒绝表之外的工具。
    """
    bot = make_bot("n1-learn", mode="learn")
    visible = set(bot.visible_tools())
    assert visible == LEARN_DECLARED, (
        "learn 可见集合与声明不符: 缺 " + repr(sorted(LEARN_DECLARED - visible))
        + "，多 " + repr(sorted(visible - LEARN_DECLARED)))


def test_learn_note_tools_run_through_the_real_kernel_exit(make_bot, tmp_path):
    """N1 端到端：learn 模式下笔记工具必须**真实执行**，
    而不是返回「工具 X 在模式 learn 下不可见」。全部走 ai.AI._kernel_tools。"""
    bot = make_bot("n1-e2e", mode="learn")
    vault = tmp_path / "n1-e2e" / "vault"
    vault.mkdir()
    bot.bind_mode_workdir("learn", str(vault))
    bot.set_approval_responder(AutoResponder("allow"))

    (vault / "算法.md").write_text(
        "# 算法\n\n动态规划入门\n", encoding="utf-8")

    read_result = bot._kernel_tools.call("read_note", {"path": "算法.md"})
    assert "动态规划入门" in read_result, (
        "read_note 未真实执行: " + read_result[:200])
    assert "在模式" not in read_result

    search_result = bot._kernel_tools.call(
        "search_notes", {"query": "动态规划"})
    assert "算法.md" in search_result, (
        "search_notes 未真实执行: " + search_result[:200])
    assert "在模式" not in search_result

    write_result = bot._kernel_tools.call(
        "write_note", {"path": "新笔记.md", "content": "hello note"})
    assert write_result.startswith("已"), (
        "write_note 未真实执行: " + write_result[:200])
    assert "在模式" not in write_result
    assert (vault / "新笔记.md").exists()
    assert "hello note" in (vault / "新笔记.md").read_text(encoding="utf-8")


def test_learn_reclaims_tools_that_deny_all_let_through(make_bot):
    """N1 反向：deny-ALL 失效路径放行的工具必须被收回。"""
    bot = make_bot("n1-reverse", mode="learn")
    for name in sorted(LEARN_FORBIDDEN):
        assert not bot.tool_visible(name), (
            "learn 不应可见 " + name + "（deny-ALL 失效路径回归）")


def test_work_code_chat_visible_sets_unchanged(make_bot):
    """N1 防回归·关键：work 仍 19 / code 仍 16 / chat 仍 0，
    且 code 与 work 仍不可见笔记工具（原设计意图）。"""
    bot = make_bot("n1-counts", mode="work")
    assert len(bot.visible_tools()) == 19, sorted(bot.visible_tools())
    bot.set_mode("code", persist=False)
    code_visible = set(bot.visible_tools())
    assert len(code_visible) == 16, sorted(code_visible)
    assert not (code_visible & {"read_note", "search_notes", "write_note"}), (
        "code 不得可见笔记工具")
    bot.set_mode("chat", persist=False)
    assert bot.visible_tools() == [], "chat 可见集合恒为空"


def test_builtin_modes_keeps_mode_names_out_of_known_tools(make_bot):
    """N1 根因：ai.py 传给 builtin_modes 的 known_tools 必须是**已注册工具
    名集合**，而不是模式名集合。

    不用 monkeypatch 替换 castorice.builtin_modes（t2 护栏只放行
    ai.random / socket.socket / time.time 这几个 patch 目标），改用两条
    只读证据：
    * 静态：_build_mode_registry 里 declared 的赋值行不再引用
      KNOWN_MODES / set(allowed)（旧根因行是
      ``declared = set(ctools.KNOWN_MODES) | set(allowed)``）；
    * 动态：真实构造出的 learn 模式 tool_policy.allow 恰为 8 个声明工具
      （若入参仍是模式名，交集恒空、allow 退化成 deny-ALL）。
    """
    import inspect

    import castorice

    source = inspect.getsource(ai.AI._build_mode_registry)
    declared_lines = [line.strip() for line in source.splitlines()
                      if line.strip().startswith("declared =")]
    assert declared_lines, "_build_mode_registry 必须先物化 declared 再传给 builtin_modes"
    for line in declared_lines:
        assert "KNOWN_MODES" not in line, (
            "declared 不得由模式名集合（KNOWN_MODES）拼出（N1 根因回归）: " + line)
        assert "set(allowed)" not in line, (
            "declared 不得混入按模式分桶的 allowed（其键是模式名）: " + line)
    assert "builtin_modes(" in source, (
        "模式物化必须经过 castorice.builtin_modes")

    # 动态证据：真实装配路径下 learn 的 allow 掩码就是 8 个声明工具
    bot = make_bot("n1-root", mode="learn")
    learn = bot._mode_registry.get("learn")
    assert learn.tool_policy.allow == frozenset(LEARN_DECLARED), (
        "learn 的 allow 掩码不是声明集合（入参语义回归到模式名了）: "
        + repr(learn.tool_policy))
    assert not learn.tool_policy.deny, (
        "learn 不得退化为 deny-ALL: " + repr(learn.tool_policy))

    # 语义方向对照：把**工具名**传给 builtin_modes 时，learn 的 allowlist
    # 恰为 8 个正确工具、deny 为空（与上面的真实对象一致）
    learn_static = [m for m in castorice.builtin_modes(
        frozenset(LEARN_DECLARED)) if m.name == "learn"][0]
    assert set(learn_static.tool_allowlist) == LEARN_DECLARED
    bot.close()


def test_placeholder_session_is_reused_not_orphaned(make_bot):
    """初始会话必须被正式会话复用，而不是变成一个孤儿登记。"""
    bot = make_bot("reuse", mode="chat")
    assert "_pending_session" in vars(bot) or hasattr(bot, "_pending_session")
    # 首个正式会话就是那里复用来的对象
    assert bot.session.session_id
    assert bot.session.mode == "chat"
    assert bot._pending_session is None, "复用后不应再留下初始会话引用"
    assert bot.session is bot._sessions["chat"]


def test_close_is_idempotent(make_bot):
    bot = make_bot("idem", mode="code")
    bot.close()
    bot.close()          # 第二次不应抛异常
    # SessionStore 的关闭状态在私有字段上（没有公开 closed 属性）
    assert bot.session._closed is True


def test_context_manager_releases_writers(tmp_path):
    """with 写法同样能释放登记。"""
    root = tmp_path / "ctx"
    root.mkdir()
    scaffold(root)
    config = write_config(root, mode="chat")
    baseline = len(session_module._ACTIVE_WRITERS)
    with ai.AI(config_path=str(config), prompts_dir=str(root / "prompts"),
               chroma_dir=str(root / "chroma"), provider_name="fake",
               image_dir=str(root / "Image"),
               sessions_dir=str(root / "sessions")) as bot:
        assert bot.tools_mode == "chat"
        assert len(session_module._ACTIVE_WRITERS) == baseline + 1
    assert len(session_module._ACTIVE_WRITERS) == baseline


def test_rebuild_after_close_succeeds(tmp_path):
    """释放后在同一 sessions_dir 上重建实例必须成功。

    旧实现在这里直接抛 SessionInUseError——因为前一个实例的占位会话
    从未释放，且新实例又会去打开同一份最近日志。
    """
    root = tmp_path / "rebuild"
    root.mkdir()
    scaffold(root)
    config = write_config(root, mode="chat")
    sessions = root / "sessions"

    first = ai.AI(config_path=str(config), prompts_dir=str(root / "prompts"),
                  chroma_dir=str(root / "chroma"), provider_name="fake",
                  image_dir=str(root / "Image"), sessions_dir=str(sessions))
    first.session.append("user", {"content": "第一轮"})
    first.session.checkpoint("probe")
    first.close()

    second = ai.AI(config_path=str(config), prompts_dir=str(root / "prompts"),
                   chroma_dir=str(root / "chroma"), provider_name="fake",
                   image_dir=str(root / "Image"), sessions_dir=str(sessions))
    try:
        # 历史应当从磁盘恢复
        contents = [m.get("content") for m in second.get_history()
                    if m.get("role") == "user"]
        assert "第一轮" in contents
    finally:
        second.close()
