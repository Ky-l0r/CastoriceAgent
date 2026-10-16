"""工具生态回归测试（t2 基线 + t4 新增）。

两部分：

* **ai.py 兼容面**（t2 基线）：ToolRegistry 的注册 / 导出 / 调用错误路径、
  format_search_results、宽松类型转换——这些既有行为不得回归；
* **castorice/tools/ 内核工具生态**（t4）：构造期校验、结果有界化、
  文件系统边界与观察语义（R5/R6）、命令白名单与超时（P22/P23）、
  web 迁移不回归、MCP 接入点短路。

全部离线：默认夹具会拦截 socket，因此不会有任何真实网络请求。
"""

import json
import re
import sys
from pathlib import Path

import pytest

import ai
import castorice.tools as ctools
from castorice.modes import SANDBOX_READ_ONLY, SANDBOX_WORKSPACE_WRITE
from castorice.permissions import AutoResponder, PermissionGateway


# ============================================================================
# A. ai.py 兼容面（t2 基线，不得回归）
# ============================================================================

def make_registry():
    registry = ai.ToolRegistry()
    registry.register(ai.Tool(
        name="echo",
        description="回声",
        parameters={"type": "object", "properties": {"text": {"type": "string"}}},
        function=lambda text: f"echo:{text}",
    ))
    return registry


def test_register_and_call():
    registry = make_registry()
    assert registry.call("echo", {"text": "hi"}) == "echo:hi"


def test_call_unknown_tool_returns_error_text():
    registry = make_registry()
    result = registry.call("nope", {})
    assert result.startswith("错误: 未知工具 nope")
    assert "echo" in result


def test_call_swallows_function_exception():
    registry = ai.ToolRegistry()
    registry.register(ai.Tool(
        name="boom",
        description="总是失败",
        parameters={"type": "object", "properties": {}},
        function=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("炸了")),
    ))
    result = registry.call("boom", {})
    assert result.startswith("错误: 调用工具 boom 失败")
    assert "炸了" in result


def test_call_with_wrong_arguments_returns_error_text():
    registry = make_registry()
    result = registry.call("echo", {"unexpected": 1})
    assert result.startswith("错误: 调用工具 echo 失败")


def test_call_coerces_result_to_str():
    registry = ai.ToolRegistry()
    registry.register(ai.Tool(
        name="num",
        description="返回数字",
        parameters={"type": "object", "properties": {}},
        function=lambda: 42,
    ))
    assert registry.call("num", {}) == "42"


def test_to_openai_specs_shape():
    registry = make_registry()
    specs = registry.to_openai_specs()
    assert len(specs) == 1
    spec = specs[0]
    assert spec["type"] == "function"
    assert spec["function"]["name"] == "echo"
    assert spec["function"]["description"] == "回声"
    assert spec["function"]["parameters"]["properties"]["text"]["type"] == "string"


def test_registering_same_name_overwrites():
    registry = make_registry()
    registry.register(ai.Tool(
        name="echo", description="新版", parameters={"type": "object", "properties": {}},
        function=lambda **kwargs: "v2",
    ))
    assert registry.call("echo", {}) == "v2"
    assert len(registry.to_openai_specs()) == 1


def test_empty_registry_specs():
    assert ai.ToolRegistry().to_openai_specs() == []


def test_tool_registry_exposes_kernel_metadata():
    """t4 新增：ai.ToolRegistry 内部是带元数据的 ctools.ToolSpec。"""
    registry = make_registry()
    specs = registry.specs()
    assert len(specs) == 1
    assert isinstance(specs[0], ctools.ToolSpec)
    assert registry.names() == ["echo"]
    assert "echo" in registry


# ----------------------------------------------------------------------------
# 内置工具
# ----------------------------------------------------------------------------

#: t4 落地后的完整内置工具集（顺序即注册顺序）
BUILTIN_TOOL_NAMES = [
    "get_current_time", "web_search", "web_fetch",
    "read_file", "write_file", "edit_file", "list_dir",
    "glob_files", "grep_files", "run_command", "make_document",
    "search_notes", "read_note", "write_note",
    "play_music", "set_reminder", "list_reminders", "clear_reminders",
    "system_info",
]


def test_create_builtin_tools_exposes_expected_tools():
    registry = ai.create_builtin_tools({})
    names = [spec["function"]["name"] for spec in registry.to_openai_specs()]
    assert names == BUILTIN_TOOL_NAMES


def test_create_builtin_tools_keeps_the_two_original_tools():
    """迁移 web_search 不得丢掉原有工具（既有行为不回归）。"""
    registry = ai.create_builtin_tools({})
    assert "get_current_time" in registry
    assert "web_search" in registry


def test_create_builtin_tools_accepts_search_config_without_network():
    # 只构造注册表，不发起任何请求
    registry = ai.create_builtin_tools({"provider": "bing_html", "max_results": 3})
    assert len(registry.to_openai_specs()) == len(BUILTIN_TOOL_NAMES)


def test_web_search_spec_requires_query():
    registry = ai.create_builtin_tools({})
    spec = [s for s in registry.to_openai_specs()
            if s["function"]["name"] == "web_search"][0]
    assert spec["function"]["parameters"]["required"] == ["query"]


def test_get_current_time_output_format():
    text = ai.tool_get_current_time()
    assert re.match(r"^当前本地时间：\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}（\w+）$", text)


def test_get_current_time_is_callable_through_registry():
    registry = ai.create_builtin_tools({})
    result = registry.call("get_current_time", {})
    assert result.startswith("当前本地时间：")


# ----------------------------------------------------------------------------
# 搜索结果格式化
# ----------------------------------------------------------------------------

def test_format_search_results_basic():
    text = ai.format_search_results([
        {"title": "标题", "url": "https://example.com", "snippet": "简介"},
    ])
    assert "1. 标题" in text
    assert "https://example.com" in text
    assert "简介: 简介" in text


def test_format_search_results_includes_content_summary():
    text = ai.format_search_results([
        {"title": "t", "url": "u", "snippet": "", "content": "正文"},
    ])
    assert "正文摘要: 正文" in text
    assert "简介" not in text


def test_format_search_results_truncates_long_fields():
    text = ai.format_search_results([
        {"title": "t", "url": "u", "snippet": "s" * 400, "content": "c" * 700},
    ])
    assert "s" * 300 in text and "s" * 301 not in text
    assert "c" * 600 in text and "c" * 601 not in text


def test_format_search_results_empty():
    assert ai.format_search_results([]) == ""


def test_format_search_results_tolerates_missing_fields():
    assert ai.format_search_results([{}]) == "1. \n   "


# ----------------------------------------------------------------------------
# 宽松类型转换辅助函数
# ----------------------------------------------------------------------------

@pytest.mark.parametrize("value, expected", [
    (None, True),
    (True, True),
    (False, False),
    ("true", True),
    ("False", False),
    ("off", False),
    ("no", False),
    ("0", False),
    ("", False),
    (1, True),
])
def test_to_bool(value, expected):
    assert ai._to_bool(value) is expected


def test_to_bool_custom_default():
    assert ai._to_bool(None, default=False) is False


@pytest.mark.parametrize("value", ["abc", None, [], {}])
def test_to_float_falls_back(value):
    assert ai._to_float(value, 0.35) == 0.35


def test_to_float_parses():
    assert ai._to_float("0.5", 0.0) == 0.5
    assert ai._to_float(2, 0.0) == 2.0


@pytest.mark.parametrize("value", ["abc", None, []])
def test_to_int_falls_back(value):
    assert ai._to_int(value, 3) == 3


def test_to_int_parses_and_truncates():
    assert ai._to_int("5", 0) == 5
    assert ai._to_int(2.9, 0) == 2


# ============================================================================
# B. castorice/tools/ 内核工具生态（t4）
# ============================================================================

def build_context(root, *, sandbox=SANDBOX_WORKSPACE_WRITE, mode="code",
                  gateway=None, cwd=None):
    context = ctools.ToolContext(
        workspace=str(root),
        cwd=str(cwd or root),
        sandbox=sandbox,
        mode=mode,
        session_id="sess-test",
        gateway=gateway,
    )
    if gateway is not None:
        context.policy = gateway.path_policy
    return context


def build_gateway(root, *, mode="code", sandbox=SANDBOX_WORKSPACE_WRITE,
                  responder=None, command_allowlist=None):
    context = build_context(root, sandbox=sandbox, mode=mode)
    specs = ctools.build_fs_tools(context) + ctools.build_command_tools(context)
    gateway = PermissionGateway(
        {"approval_policy": "ask", "sandbox_default": sandbox},
        tool_specs=specs,
        mode=mode,
        roots=[str(root)],
        sandbox=sandbox,
        mode_command_allowlist=command_allowlist or {"code": ("dir", "ls"), "learn": ()},
    )
    if responder is not None:
        gateway.set_ask_callback(responder)
    return gateway


@pytest.fixture
def root(tmp_path):
    directory = tmp_path / "ws"
    directory.mkdir()
    (directory / "note.md").write_text("# 标题\n\n正文内容\n", encoding="utf-8")
    (directory / "sub").mkdir()
    (directory / "sub" / "data.txt").write_text("alpha\nbeta\nGAMMA\n", encoding="utf-8")
    return directory


def tools_by_name(root, *, sandbox=SANDBOX_WORKSPACE_WRITE, mode="code",
                  gateway=None):
    context = build_context(root, sandbox=sandbox, mode=mode, gateway=gateway)
    specs = list(ctools.build_fs_tools(context))
    return {spec.name: spec for spec in specs}, context


# ----------------------------------------------------------------------------
# B1. ToolSpec 与注册表的构造期校验
# ----------------------------------------------------------------------------

def test_minimal_spec_defaults_are_conservative():
    spec = ctools.ToolSpec(name="x")
    assert spec.read_only is False
    assert spec.requires_approval is True      # 未声明只读 -> 一律需要审批
    assert spec.source == ctools.SOURCE_BUILTIN
    assert spec.modes == ctools.DEFAULT_TOOL_MODES
    assert spec.kind == ctools.TOOL_KIND_GENERIC


def test_readonly_spec_does_not_require_approval():
    assert ctools.ToolSpec(name="x", read_only=True).requires_approval is False


def test_empty_name_is_rejected():
    with pytest.raises(ctools.ToolDefinitionError):
        ctools.ToolSpec(name="")
    with pytest.raises(ctools.ToolDefinitionError):
        ctools.ToolSpec(name="有 空 格")


def test_bad_schema_keyword_is_rejected():
    with pytest.raises(ctools.ToolSchemaError) as excinfo:
        ctools.ToolSpec(name="x", parameters={"type": "object", "$ref": "y"})
    assert "$ref" in str(excinfo.value)


def test_bad_schema_type_is_rejected():
    with pytest.raises(ctools.ToolSchemaError):
        ctools.ToolSpec(name="x", parameters={"type": "tuple"})


def test_required_must_reference_declared_properties():
    with pytest.raises(ctools.ToolSchemaError) as excinfo:
        ctools.ToolSpec(name="x", parameters={
            "type": "object", "properties": {"a": {"type": "string"}},
            "required": ["b"]})
    assert "b" in str(excinfo.value)


def test_nested_schema_is_validated():
    with pytest.raises(ctools.ToolSchemaError):
        ctools.ToolSpec(name="x", parameters={
            "type": "object",
            "properties": {"a": {"type": "object",
                                 "properties": {"b": {"type": "nope"}}}}})
    ctools.ToolSpec(name="x", parameters={
        "type": "object",
        "properties": {"a": {"type": "object", "properties": {"b": {"type": "string"}}}}})


def test_items_only_allowed_for_arrays():
    ctools.ToolSpec(name="x", parameters={"type": "array", "items": {"type": "string"}})
    with pytest.raises(ctools.ToolSchemaError):
        ctools.ToolSpec(name="x", parameters={"type": "object",
                                              "items": {"type": "string"}})


def test_unknown_mode_is_rejected():
    with pytest.raises(ctools.ToolModeError) as excinfo:
        ctools.ToolSpec(name="x", modes=("work", "yolo"))
    assert "yolo" in str(excinfo.value)


def test_chat_mode_is_rejected_for_tools():
    """红线 E：Chat 绝不向模型暴露任何工具。"""
    with pytest.raises(ctools.ToolModeError):
        ctools.ToolSpec(name="x", modes=("chat",))


def test_empty_modes_is_rejected():
    with pytest.raises(ctools.ToolModeError):
        ctools.ToolSpec(name="x", modes=())


def test_unknown_source_is_rejected():
    with pytest.raises(ctools.ToolDefinitionError):
        ctools.ToolSpec(name="x", source="外星来源")


def test_duplicate_registration_raises_by_default():
    registry = ctools.ToolRegistry()
    registry.register(ctools.ToolSpec(name="dup"))
    with pytest.raises(ctools.ToolDefinitionError) as excinfo:
        registry.register(ctools.ToolSpec(name="dup"))
    assert "重复" in str(excinfo.value)
    registry.register(ctools.ToolSpec(name="dup", description="新版"),
                      replace_existing=True)
    assert registry.get("dup").description == "新版"


def test_registry_exports_openai_specs():
    registry = ctools.ToolRegistry([
        ctools.ToolSpec(name="a", description="d",
                        parameters={"type": "object", "properties": {}}),
    ])
    specs = registry.to_openai_specs()
    assert specs[0]["type"] == "function"
    assert specs[0]["function"]["name"] == "a"


def test_registry_call_returns_error_text_for_unknown_tool():
    assert ctools.ToolRegistry().call("nope", {}).startswith("错误: 未知工具")


def test_registry_call_never_raises():
    def boom(**kwargs):
        raise RuntimeError("炸了")

    registry = ctools.ToolRegistry([ctools.ToolSpec(name="boom", handler=boom)])
    result = registry.call("boom", {})
    assert result.startswith("错误: 调用工具 boom 失败")
    assert "炸了" in result


def test_spec_without_handler_reports_readably():
    registry = ctools.ToolRegistry([ctools.ToolSpec(name="only_schema")])
    assert "没有执行实现" in registry.call("only_schema", {})


# ----------------------------------------------------------------------------
# B2. 结果有界化（P20）
# ----------------------------------------------------------------------------

def test_truncate_text_keeps_head_and_tail():
    text = "H" * 100 + "M" * 500 + "T" * 100
    result = ctools.truncate_text(text, 100)
    assert result.startswith("H")
    assert result.endswith("T")
    assert "已省略" in result
    assert len(result) < len(text)


def test_truncate_text_is_noop_under_limit():
    assert ctools.truncate_text("abc", 10) == "abc"
    assert ctools.truncate_text("abc", 0) == "abc"


def test_registry_call_applies_result_limit():
    registry = ctools.ToolRegistry([
        ctools.ToolSpec(name="big", handler=lambda **kwargs: "x" * 500,
                        result_limit=100)])
    assert "已省略" in registry.call("big", {})


# ----------------------------------------------------------------------------
# B3. 文件系统工具（R5 / R6）
# ----------------------------------------------------------------------------

def test_read_file_includes_line_numbers(root):
    tools, _context = tools_by_name(root)
    result = tools["read_file"].handler(path="sub/data.txt")
    assert "1\talpha" in result
    assert "2\tbeta" in result
    assert "共 3 行" in result


def test_read_file_outside_root_is_denied(root, tmp_path):
    tools, _context = tools_by_name(root)
    result = tools["read_file"].handler(path=str(tmp_path / "outside.txt"))
    assert result.startswith("错误:")
    assert "FS_SANDBOX_DENIED" in result


def test_read_file_missing_is_readable_error(root):
    tools, _context = tools_by_name(root)
    assert "FS_NOT_FOUND" in tools["read_file"].handler(path="nope.txt")


def test_write_file_creates_and_persists(root):
    tools, _context = tools_by_name(root)
    result = tools["write_file"].handler(path="out/new.txt", content="hello")
    assert "已写入" in result
    assert (root / "out" / "new.txt").read_text(encoding="utf-8") == "hello"


def test_write_file_outside_workspace_is_denied(root, tmp_path):
    tools, _context = tools_by_name(root)
    outside = tmp_path / "outside" / "x.txt"
    result = tools["write_file"].handler(path=str(outside), content="x")
    assert result.startswith("错误:")
    assert "拒绝写入" in result
    assert not outside.exists()          # 关键：越界写入真的没有发生


def test_read_only_sandbox_blocks_writes(root):
    tools, _context = tools_by_name(root, sandbox=SANDBOX_READ_ONLY)
    result = tools["write_file"].handler(path="x.txt", content="a")
    assert "SANDBOX_READ_ONLY" in result
    assert not (root / "x.txt").exists()


def test_edit_file_requires_prior_read(root):
    """FS_NOT_OBSERVED：编辑前必须读取。"""
    tools, _context = tools_by_name(root)
    result = tools["edit_file"].handler(path="note.md", old_text="正文",
                                        new_text="内容")
    assert "FS_NOT_OBSERVED" in result
    assert "正文内容" in (root / "note.md").read_text(encoding="utf-8")


def test_edit_file_succeeds_after_read(root):
    tools, _context = tools_by_name(root)
    tools["read_file"].handler(path="note.md")
    result = tools["edit_file"].handler(path="note.md", old_text="正文内容",
                                        new_text="新内容")
    assert "已替换" in result
    assert "新内容" in (root / "note.md").read_text(encoding="utf-8")


def test_edit_file_rejects_stale_observation(root):
    """读后被外部改动 -> 拒绝并要求重读（版本守卫）。"""
    tools, _context = tools_by_name(root)
    tools["read_file"].handler(path="note.md")
    (root / "note.md").write_text("# 标题\n\n被别人改了\n", encoding="utf-8")
    result = tools["edit_file"].handler(path="note.md", old_text="被别人改了",
                                        new_text="改回来")
    assert "FS_STALE_OBSERVATION" in result
    assert "被别人改了" in (root / "note.md").read_text(encoding="utf-8")


def test_edit_file_reports_ambiguous_match(root):
    tools, _context = tools_by_name(root)
    (root / "dup.txt").write_text("aaa\naaa\n", encoding="utf-8")
    tools["read_file"].handler(path="dup.txt")
    result = tools["edit_file"].handler(path="dup.txt", old_text="aaa", new_text="b")
    assert "FS_AMBIGUOUS_MATCH" in result


def test_edit_file_reports_no_match(root):
    tools, _context = tools_by_name(root)
    tools["read_file"].handler(path="note.md")
    assert "FS_NO_MATCH" in tools["edit_file"].handler(
        path="note.md", old_text="不存在", new_text="x")


def test_edit_file_replace_all(root):
    tools, _context = tools_by_name(root)
    (root / "dup.txt").write_text("aaa\naaa\n", encoding="utf-8")
    tools["read_file"].handler(path="dup.txt")
    result = tools["edit_file"].handler(path="dup.txt", old_text="aaa",
                                        new_text="b", replace_all=True)
    assert "2 处" in result
    assert (root / "dup.txt").read_text(encoding="utf-8") == "b\nb\n"


def test_list_dir_and_glob_and_grep(root):
    tools, _context = tools_by_name(root)
    listing = tools["list_dir"].handler(path=".")
    assert "[文件]" in listing and "note.md" in listing
    assert "note.md" in tools["glob_files"].handler(pattern="*.md")
    assert "data.txt:2" in tools["grep_files"].handler(pattern="beta", path=".")


def test_grep_files_reports_bad_regex(root):
    tools, _context = tools_by_name(root)
    assert "BAD_ARGUMENTS" in tools["grep_files"].handler(pattern="[unclosed")


def test_list_dir_skips_vcs_and_binary(root):
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("x", encoding="utf-8")
    (root / "img.png").write_bytes(b"\x89PNG")
    tools, _context = tools_by_name(root)
    listing = tools["list_dir"].handler(path=".", recursive=True)
    assert ".git" not in listing
    assert "img.png" not in listing


def test_fs_tools_declare_expected_metadata(root):
    tools, _context = tools_by_name(root)
    assert tools["read_file"].read_only is True
    assert tools["write_file"].read_only is False
    assert tools["write_file"].requires_approval is True
    assert tools["edit_file"].parallel_safe is False


# ----------------------------------------------------------------------------
# B4. 受限命令执行（P22 / P23）
# ----------------------------------------------------------------------------

def test_run_command_defaults_to_deny_without_gateway(root):
    """网关不可用时白名单为空 -> 一律拒绝（默认 deny）。"""
    tools, _context = tools_by_name(root)
    specs = {spec.name: spec for spec in ctools.build_command_tools(
        build_context(root, mode="code"))}
    result = specs["run_command"].handler(command="dir")
    assert result.startswith("错误:")
    assert "COMMAND_MODE_DENIED" in result


def test_run_command_denies_unlisted_command(root):
    gateway = build_gateway(root, responder=AutoResponder("allow"))
    context = build_context(root, gateway=gateway)
    specs = {spec.name: spec for spec in ctools.build_command_tools(context)}
    result = specs["run_command"].handler(command="rm -rf /")
    assert "COMMAND_DENIED" in result


def test_run_command_rejects_empty_command(root):
    gateway = build_gateway(root, responder=AutoResponder("allow"))
    context = build_context(root, gateway=gateway)
    specs = {spec.name: spec for spec in ctools.build_command_tools(context)}
    assert "BAD_ARGUMENTS" in specs["run_command"].handler()


def test_run_command_reports_nonzero_exit_as_structured_result(root):
    """非零退出码必须作为结构化结果返回，而不是抛异常。"""
    result = ctools.run_argv(
        [sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(3)"],
        cwd=str(root), timeout=30,
    )
    assert result["exit_code"] == 3
    assert result["timed_out"] is False
    assert "非零退出码 3" in result["rendered"]
    assert "boom" in result["stderr"]


def test_run_command_never_raises_on_missing_executable(root):
    result = ctools.run_argv(["definitely-not-a-real-binary-xyz"], cwd=str(root))
    assert result["exit_code"] is None
    assert "找不到可执行文件" in result["stderr"]


def test_run_command_timeout_is_structured(root):
    result = ctools.run_argv(
        [sys.executable, "-c", "import time; time.sleep(5)"],
        cwd=str(root), timeout=0.5,
    )
    assert result["timed_out"] is True
    assert result["exit_code"] is None
    assert "命令超时" in result["rendered"]


def test_run_command_output_is_bounded(root):
    result = ctools.run_argv(
        [sys.executable, "-c", "print('x' * 50000)"],
        cwd=str(root), output_limit=500,
    )
    assert result["exit_code"] == 0
    assert "已省略" in result["stdout"]


def test_run_argv_forbids_shell(root):
    with pytest.raises(ValueError):
        ctools.run_argv(["echo", "hi"], shell=True)
    source = Path(ctools.__file__).parent.joinpath("command.py").read_text(encoding="utf-8")
    assert "shell=False" in source
    # 唯一允许出现 "shell=True" 的地方是拒绝分支的报错文案
    assert source.count("shell=True") == 1
    assert "禁止 shell=True" in source


def test_env_exposes_managed_aster_variables(root):
    gateway = build_gateway(root, responder=AutoResponder("allow"))
    context = build_context(root, gateway=gateway)
    context.session_id = "sess-42"
    env = ctools.build_env(context)
    assert env["ASTER_SESSION_ID"] == "sess-42"
    assert env["ASTER_MODE"] == "code"
    assert env["ASTER_WORKSPACE"] == str(root)
    assert env["ASTER_SANDBOX"] == SANDBOX_WORKSPACE_WRITE
    assert set(ctools.ENV_KEYS) <= set(env)


def test_env_is_visible_to_the_child_process(root):
    gateway = build_gateway(root, responder=AutoResponder("allow"))
    context = build_context(root, gateway=gateway)
    context.session_id = "sess-99"
    result = ctools.run_argv(
        [sys.executable, "-c", "import os; print(os.environ['ASTER_SESSION_ID'])"],
        cwd=str(root), env=ctools.build_env(context),
    )
    assert "sess-99" in result["stdout"]


def test_run_command_allows_allowlisted_command(root):
    gateway = build_gateway(root, responder=AutoResponder("allow"))
    context = build_context(root, gateway=gateway)
    specs = {spec.name: spec for spec in ctools.build_command_tools(context)}
    result = specs["run_command"].handler(argv=[sys.executable, "--version"])
    # python --version 不在白名单里 -> 拒绝；白名单命中的路径放到下面验证
    assert "COMMAND_DENIED" in result

    # 白名单显式追加后放行
    gateway.configure(mode_command_allowlist={
        "code": ("dir", "ls", "python"), "learn": ()})
    result2 = specs["run_command"].handler(argv=[sys.executable, "--version"])
    assert "退出码" in result2


def test_run_command_respects_workspace_as_cwd(root):
    gateway = build_gateway(root, responder=AutoResponder("allow"))
    gateway.configure(mode_command_allowlist={"code": ("python",), "learn": ()})
    context = build_context(root, gateway=gateway)
    specs = {spec.name: spec for spec in ctools.build_command_tools(context)}
    result = specs["run_command"].handler(
        argv=[sys.executable, "-c", "import os; print(os.getcwd())"])
    assert str(root) in result


def test_render_result_distinguishes_timeout_and_failure():
    failed = ctools.render_result({"argv": ["x"], "exit_code": 2, "timed_out": False,
                                   "duration_s": 0.1, "stdout": "", "stderr": "e"})
    timed = ctools.render_result({"argv": ["x"], "exit_code": None, "timed_out": True,
                                  "duration_s": 0.1, "stdout": "", "stderr": ""})
    assert "非零退出码 2" in failed
    assert "命令超时" in timed


# ----------------------------------------------------------------------------
# B5. web 工具（迁移不回归 + R18）
# ----------------------------------------------------------------------------

def test_six_backends_are_registered_after_migration():
    manager = ctools.SearchManager({"provider": "auto"})
    names = [backend.name for backend in manager._backends]
    assert names == ["bing_html", "ddg_html"]


def test_key_backends_are_preferred_when_key_present():
    manager = ctools.SearchManager({"provider": "auto", "api_key": "k"})
    names = [backend.name for backend in manager._backends]
    assert names == ["tavily", "serper", "bing_api", "bocha", "bing_html", "ddg_html"]


@pytest.mark.parametrize("provider, expected", [
    ("tavily", ["tavily"]),
    ("serper", ["serper"]),
    ("bing_api", ["bing_api"]),
    ("bocha", ["bocha"]),
    ("bing_html", ["bing_html"]),
    ("ddg_html", ["ddg_html"]),
])
def test_explicit_provider_selects_single_backend(provider, expected):
    manager = ctools.SearchManager({"provider": provider, "api_key": "k"})
    assert [backend.name for backend in manager._backends] == expected


def test_ai_reexports_are_the_same_objects():
    """迁移后 ai.py 的旧引用必须仍然指向同一实现（兼容）。"""
    assert ai.SearchManager is ctools.SearchManager
    assert ai.SearchBackend is ctools.SearchBackend
    assert ai.BingHTMLSearch is ctools.BingHTMLSearch
    assert ai.DDGHTMLSearch is ctools.DDGHTMLSearch
    assert ai.TavilySearch is ctools.TavilySearch
    assert ai.SerperSearch is ctools.SerperSearch
    assert ai.BingAPISearch is ctools.BingAPISearch
    assert ai.BochaSearch is ctools.BochaSearch
    assert ai.format_search_results is ctools.format_search_results
    assert ai._html_to_text is ctools.web._html_to_text


def test_search_falls_back_to_the_next_backend():
    """第一个后端抛错时自动降级到下一个（既有行为）。"""
    class Boom(ctools.SearchBackend):
        name = "boom"

        def search(self, query, max_results):
            raise RuntimeError("后端挂了")

    class Good(ctools.SearchBackend):
        name = "good"

        def search(self, query, max_results):
            return [{"title": "t", "url": "u", "snippet": "s"}]

    manager = ctools.SearchManager({"provider": "bing_html"})
    manager._backends = [Boom(), Good()]
    manager.fetch_content = False
    results = manager.search("q")
    assert results and results[0]["title"] == "t"


def test_search_raises_readable_error_when_all_backends_fail():
    class Boom(ctools.SearchBackend):
        name = "boom"

        def search(self, query, max_results):
            raise RuntimeError("后端挂了")

    manager = ctools.SearchManager({"provider": "bing_html"})
    manager._backends = [Boom()]
    with pytest.raises(RuntimeError) as excinfo:
        manager.search("q")
    assert "boom" in str(excinfo.value)


class ScriptedSearchManager:
    """测试内自造的搜索管理器替身（不替换任何生产实现）。

    WebSearchTool 只依赖 self._manager.search(query, max_results) 这一个
    方法，因此注入替身即可覆盖「失败」与「成功」两条路径，
    而不必给 ctools.SearchManager 打补丁。
    """

    def __init__(self, results=None, error=None):
        self.results = results
        self.error = error
        self.queries = []

    def search(self, query, max_results=None):
        self.queries.append(query)
        if self.error is not None:
            raise self.error
        return list(self.results or [])


def make_web_search_tool(manager):
    """构造一个注入了替身管理器的 web_search 工具。"""
    tool = ctools.WebSearchTool({})
    tool._manager = manager
    return tool


def test_web_search_tool_reports_failure_as_readable_text():
    tool = make_web_search_tool(ScriptedSearchManager(error=RuntimeError("网络不可用")))
    result = tool("q")
    assert result.startswith("错误:")
    assert "网络不可用" in result
    assert "SEARCH_FAILED" in result


def test_web_search_tool_marks_external_content_untrusted():
    tool = make_web_search_tool(ScriptedSearchManager(results=[
        {"title": "标题", "url": "https://e.com", "snippet": "摘要"}]))
    result = tool("q")
    assert ctools.UNTRUSTED_NOTICE in result
    assert "标题" in result and "https://e.com" in result and "摘要" in result


def test_web_search_tool_reports_empty_results_readably():
    tool = make_web_search_tool(ScriptedSearchManager(results=[]))
    result = tool("关键词")
    assert "未找到" in result


def test_web_search_specs_declare_network_kind():
    specs = {spec.name: spec for spec in ctools.build_web_tools({})}
    assert specs["web_search"].read_only is True
    assert specs["web_search"].kind == ctools.TOOL_KIND_NETWORK
    assert specs["web_fetch"].requires_approval is False


def test_web_fetch_rejects_non_http_url():
    result = ctools.WebFetchTool({})("file:///etc/passwd")
    assert result.startswith("错误:")
    assert "http/https" in result


def test_web_fetch_reports_fetch_failure_readably():
    """抓取失败必须转成可读错误文本，而不是抛异常。

    这里刻意不「打桩」requests：默认夹具已经禁网，因此这次请求必然失败，
    正好覆盖真实的失败路径（也顺带证明禁网夹具对工具层生效）。
    """
    result = ctools.WebFetchTool({})("https://example.com")
    assert result.startswith("错误:")
    assert "FETCH_FAILED" in result


# ----------------------------------------------------------------------------
# B6. 文档生成
# ----------------------------------------------------------------------------

def test_make_document_writes_markdown(root):
    context = build_context(root)
    specs = {spec.name: spec for spec in ctools.build_document_tools(context)}
    result = specs["make_document"].handler(path="报告.md", title="周报",
                                            content="正文")
    assert "已生成文档" in result
    text = (root / "报告.md").read_text(encoding="utf-8")
    assert text.startswith("# 周报")
    assert "正文" in text


def test_make_document_supports_sections(root):
    context = build_context(root)
    specs = {spec.name: spec for spec in ctools.build_document_tools(context)}
    specs["make_document"].handler(
        path="s.md", title="T",
        sections=[{"heading": "一", "content": "内容1"},
                  {"heading": "二", "content": "内容2"}])
    text = (root / "s.md").read_text(encoding="utf-8")
    assert "## 一" in text and "## 二" in text


def test_make_document_rejects_unsupported_format(root):
    context = build_context(root)
    specs = {spec.name: spec for spec in ctools.build_document_tools(context)}
    result = specs["make_document"].handler(path="x.docx", content="a")
    assert "UNSUPPORTED_FORMAT" in result


def test_make_document_defaults_to_markdown(root):
    context = build_context(root)
    specs = {spec.name: spec for spec in ctools.build_document_tools(context)}
    specs["make_document"].handler(path="无扩展名", content="a")
    assert (root / "无扩展名.md").exists()


def test_make_document_respects_the_workspace_boundary(root, tmp_path):
    context = build_context(root)
    specs = {spec.name: spec for spec in ctools.build_document_tools(context)}
    outside = tmp_path / "outside.md"
    result = specs["make_document"].handler(path=str(outside), content="a")
    assert result.startswith("错误:")
    assert not outside.exists()


# ----------------------------------------------------------------------------
# B7. MCP 接入点（R17）
# ----------------------------------------------------------------------------

def test_no_mcp_server_means_no_tools_and_no_prompt_text():
    """没有 server 时工具列表与提示词文本都与现状一致（空）。"""
    registry = ctools.McpRegistry()
    assert registry.empty is True
    assert registry.tools() == []
    assert ctools.mcp_prompt_text(registry) == ""
    assert "mcp__" not in ctools.mcp_prompt_text(registry)


def test_mcp_tool_naming_round_trip():
    name = ctools.mcp_tool_name("fs", "read")
    assert name == "mcp__fs__read"
    assert ctools.parse_mcp_tool_name(name) == ("fs", "read")
    assert ctools.parse_mcp_tool_name("read_file") is None


def test_mcp_invalid_names_are_rejected():
    with pytest.raises(ctools.ToolDefinitionError):
        ctools.mcp_tool_name("", "read")
    with pytest.raises(ctools.ToolDefinitionError):
        ctools.mcp_tool_name("bad name", "read")


def test_mcp_tools_are_registered_with_the_expected_names():
    registry = ctools.McpRegistry()
    ctools.register_mcp_server(registry, "fs", [
        ctools.McpToolDescriptor(name="read", description="读文件",
                                 parameters={"type": "object", "properties": {}}),
    ])
    specs = registry.tools()
    assert [spec.name for spec in specs] == ["mcp__fs__read"]
    assert specs[0].source == ctools.SOURCE_MCP
    assert "mcp__fs__read" in ctools.mcp_prompt_text(registry)


def test_mcp_duplicate_tool_names_are_rejected():
    registry = ctools.McpRegistry()
    with pytest.raises(ctools.ToolDefinitionError):
        ctools.register_mcp_server(registry, "fs", [
            ctools.McpToolDescriptor(name="read"),
            ctools.McpToolDescriptor(name="read"),
        ])


def test_mcp_duplicate_server_is_rejected():
    registry = ctools.McpRegistry()
    ctools.register_mcp_server(registry, "fs", [])
    with pytest.raises(ctools.ToolDefinitionError):
        ctools.register_mcp_server(registry, "fs", [])


def test_unconnected_mcp_tool_returns_readable_error():
    registry = ctools.McpRegistry()
    ctools.register_mcp_server(registry, "fs", [ctools.McpToolDescriptor(name="read")])
    spec = registry.tools()[0]
    result = spec.handler()
    assert "MCP_NOT_CONNECTED" in result


def test_connected_mcp_tool_dispatches_to_call_tool():
    registry = ctools.McpRegistry()
    ctools.register_mcp_server(
        registry, "fs", [ctools.McpToolDescriptor(name="read", read_only=True)],
        call_tool=lambda tool, args: "内容:" + tool,
    )
    spec = registry.tools()[0]
    assert spec.handler() == "内容:read"


def test_mcp_registers_into_the_main_registry():
    mcp = ctools.McpRegistry()
    ctools.register_mcp_server(mcp, "fs", [ctools.McpToolDescriptor(name="read")])
    main = ctools.ToolRegistry()
    added = mcp.register_into(main)
    assert [spec.name for spec in added] == ["mcp__fs__read"]
    assert "mcp__fs__read" in main.names()


# ----------------------------------------------------------------------------
# B8. 生活服务与音乐播放
# ----------------------------------------------------------------------------

def test_play_music_queues_a_local_file(root):
    audio = root / "song.mp3"
    audio.write_bytes(b"ID3")
    context = build_context(root)
    specs = {spec.name: spec for spec in ctools.build_life_tools(context)}
    result = specs["play_music"].handler(path="song.mp3")
    assert "已把播放请求加入队列" in result
    queue = (root / "music_queue.jsonl").read_text(encoding="utf-8").strip()
    assert json.loads(queue)["path"] == str(audio)


def test_play_music_rejects_unsupported_format(root):
    (root / "note.txt").write_text("x", encoding="utf-8")
    context = build_context(root)
    specs = {spec.name: spec for spec in ctools.build_life_tools(context)}
    assert "UNSUPPORTED_FORMAT" in specs["play_music"].handler(path="note.txt")


def test_play_music_rejects_file_outside_workspace(root, tmp_path):
    outside = tmp_path / "song.mp3"
    outside.write_bytes(b"ID3")
    context = build_context(root)
    specs = {spec.name: spec for spec in ctools.build_life_tools(context)}
    result = specs["play_music"].handler(path=str(outside))
    assert result.startswith("错误:")


def test_reminder_lifecycle(root):
    context = build_context(root)
    specs = {spec.name: spec for spec in ctools.build_life_tools(context)}
    assert "还没有任何提醒" in specs["list_reminders"].handler()
    assert "已记下提醒" in specs["set_reminder"].handler(text="喝水", when="10:00")
    listed = specs["list_reminders"].handler()
    assert "喝水" in listed and "10:00" in listed
    assert "已清空提醒" in specs["clear_reminders"].handler()
    assert "还没有任何提醒" in specs["list_reminders"].handler()


def test_system_info_reports_the_context(root):
    context = build_context(root)
    specs = {spec.name: spec for spec in ctools.build_life_tools(context)}
    text = specs["system_info"].handler()
    assert "Python:" in text
    assert str(root) in text
    assert "code" in text


# ----------------------------------------------------------------------------
# B9. 默认注册表与分组开关
# ----------------------------------------------------------------------------

def test_default_registry_contains_all_groups():
    registry = ctools.build_default_registry({})
    names = registry.names()
    for expected in ("get_current_time", "web_search", "read_file",
                     "run_command", "make_document", "search_notes",
                     "play_music"):
        assert expected in names


def test_default_registry_respects_disabled_groups():
    registry = ctools.build_default_registry(
        {"tools": {"disabled": ["notes", "life"]}})
    assert "search_notes" not in registry.names()
    assert "play_music" not in registry.names()
    assert "read_file" in registry.names()


def test_default_registry_respects_enabled_opt_in():
    registry = ctools.build_default_registry({"tools": {"enabled": ["fs"]}})
    assert registry.names() == ["read_file", "write_file", "edit_file",
                                "list_dir", "glob_files", "grep_files"]


def test_default_registry_can_opt_out_a_single_tool():
    registry = ctools.build_default_registry({"tools": {"disabled": ["run_command"]}})
    assert "run_command" not in registry.names()


def test_default_registry_registers_mcp_tools_when_present():
    mcp = ctools.McpRegistry()
    ctools.register_mcp_server(mcp, "fs", [ctools.McpToolDescriptor(name="read")])
    registry = ctools.build_default_registry({"tools": {"enabled": ["time"]}}, mcp=mcp)
    assert "mcp__fs__read" in registry.names()


def test_kernel_specs_are_castorice_modes_specs(root):
    from castorice.modes import ToolSpec as KernelSpec

    registry = ctools.ToolRegistry(ctools.build_fs_tools(build_context(root)))
    for spec in registry.kernel_specs():
        assert isinstance(spec, KernelSpec)


# ----------------------------------------------------------------------------
# B10. ToolRuntime：绕过 harness 也必须过网关
# ----------------------------------------------------------------------------

def test_runtime_rechecks_permission_before_executing(root, tmp_path):
    """所有有副作用的调用都必须先过网关——即使绕开 harness 直接 call。"""
    gateway = build_gateway(root)
    context = build_context(root, gateway=gateway)
    registry = ctools.ToolRegistry(ctools.build_fs_tools(context))
    runtime = ctools.ToolRuntime(registry, gateway, mode="code")

    outside = tmp_path / "outside.txt"
    result = runtime.call("write_file", {"path": str(outside), "content": "x"})
    assert result.startswith("错误:")
    assert "PERMISSION_DENIED" in result
    assert not outside.exists()


def test_runtime_allows_paths_inside_the_workspace(root):
    gateway = build_gateway(root, responder=AutoResponder("allow"))
    context = build_context(root, gateway=gateway)
    registry = ctools.ToolRegistry(ctools.build_fs_tools(context))
    runtime = ctools.ToolRuntime(registry, gateway, mode="code")
    result = runtime.call("write_file", {"path": "ok.txt", "content": "x"})
    assert "已写入" in result
    assert (root / "ok.txt").read_text(encoding="utf-8") == "x"


def test_runtime_reports_gateway_failure_as_denial(root):
    class Broken:
        workspace = str(root)

        def pre_execute(self, request):
            raise RuntimeError("策略服务崩了")

    context = build_context(root)
    registry = ctools.ToolRegistry(ctools.build_fs_tools(context))
    runtime = ctools.ToolRuntime(registry, Broken(), mode="code")
    result = runtime.call("write_file", {"path": "x.txt", "content": "a"})
    assert "PERMISSION_ERROR" in result
    assert not (root / "x.txt").exists()


def test_runtime_exposes_kernel_tools(root):
    context = build_context(root)
    registry = ctools.ToolRegistry(ctools.build_fs_tools(context))
    runtime = ctools.ToolRuntime(registry, None, mode="code", enforce=False)
    assert "read_file" in [spec.name for spec in runtime.all_tools()]
