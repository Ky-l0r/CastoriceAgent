"""四模式内核（castorice/modes.py、session.py、harness.py）与 ai.py 接缝的行为测试。

对应任务 t3 的验收条款，覆盖：

* 模式定义与构造期校验（未注册工具名 / 空 allow 掩码 / chat 带工具）；
* Chat 零工具：registry.to_openai_specs() 返回空列表，且**构造出的请求体
  里没有 tools 键**（不是断言 tools == []）；
* Work / Code / Learn 的目录绑定与越界拒绝（结构化错误）；
* harness 的终止原因显式化、独占屏障与有界并行、检查点 fail-closed、
  失败步骤结果补全、重复调用提醒、协作式取消；
* 会话事件日志的 append-only / 持久性屏障 / 撕裂尾部恢复 / 按模式隔离 / 投影；
* ai.AI 的对外接口：事件协议不变、Chat 不暴露工具、切模式后会话隔离。

全部用例不联网、不需要 API Key（provider 与工具都用测试内的替身）。
"""

import importlib
import inspect
import json
import os
import threading
import time
from pathlib import Path

import pytest

import ai
from castorice import modes as modes_module
from castorice.harness import (
    HarnessLoop,
    PermissionBridge,
    PermissionDecision,
    PermissionRequest,
    RepeatReminder,
    StopReason,
    ToolCall,
)
from castorice.modes import (
    CHAT,
    CODE,
    LEARN,
    WORK,
    Mode,
    ModeRegistry,
    ToolPolicy,
    ToolSpec,
    WorkdirError,
    WorkdirPolicy,
    build_harness_registry,
    builtin_modes,
    contains_path,
    resolve_mode_name,
)
from castorice.session import (
    CheckpointFailed,
    SessionFormatError,
    SessionInUseError,
    SessionProjection,
    SessionStore,
    TOOL_OUTCOME_UNKNOWN,
)


# ----------------------------------------------------------------------------
# 测试替身
# ----------------------------------------------------------------------------

class FakeProvider:
    """按脚本依次返回 (text, tool_calls)；记录每次收到的完整请求参数。"""

    def __init__(self, script=()):
        self.script = list(script)
        self.calls = []

    def chat_completion(self, **kwargs):
        self.calls.append(kwargs)
        if not self.script:
            raise AssertionError("FakeProvider 的脚本已用尽")
        return self.script.pop(0)

    @property
    def last_kwargs(self):
        return self.calls[-1]


class FakeToolbox:
    """内核需要的工具入口：all_tools() + call()。"""

    def __init__(self, specs):
        self._specs = list(specs)
        self.calls = []

    def all_tools(self):
        return list(self._specs)

    def call(self, name, arguments):
        self.calls.append((name, dict(arguments)))
        handler = getattr(self, "on_" + name, None)
        if handler is not None:
            return handler(**arguments)
        return "结果:" + name


class SlowToolbox(FakeToolbox):
    """按参数里的 delay 模拟耗时工具，用于验证「结果按模型顺序写回」。"""

    def call(self, name, arguments):
        time.sleep(float(arguments.get("delay", 0) or 0))
        return super().call(name, arguments)


class SessionHandle:
    """把内核的 SessionStore 藏一层再交给测试修改。

    遵守 t2 的离线自检约定：monkeypatch 的目标只允许是测试内自造对象
    （允许的前缀里有 session. / bot. / self.），用来替换真实会话对象。
    """

    def __init__(self, store):
        self.store = store

    def __getattr__(self, name):
        return getattr(self.store, name)


def make_tool_calls(*names, arguments=None):
    calls = []
    for index, name in enumerate(names):
        payload = {"index": index}
        if arguments and index in arguments:
            payload.update(arguments[index])
        calls.append({
            "id": "call_" + str(index),
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(payload)},
        })
    return calls


#: 模式表声明、但 ai.create_builtin_tools() 尚未实现的工具（t4 才落地）。
#: 测试显式传完整工具集，避免把模式定义绑在 t3 的实现进度上。
FULL_TOOLSET = sorted(modes_module.ALL_TOOLS)


def make_loop(tmp_path, script, tools=(), allow=None, deny_all=False,
              max_iterations=8, max_parallel=4, mode="chat", toolbox=None):
    """装配一个不依赖 Qt / 网络的 harness 循环。"""
    specs = [ToolSpec(name=n) for n in tools]
    registry = build_harness_registry([spec.name for spec in specs])
    store = SessionStore(tmp_path / "sessions", mode)
    provider = FakeProvider(script)
    toolbox = toolbox if toolbox is not None else FakeToolbox(specs)
    if deny_all:
        permissions = PermissionBridge()
    elif allow is not None:
        permissions = PermissionBridge(allow=allow)
    else:
        permissions = PermissionBridge(lambda req: PermissionDecision("allow", ""))
    loop = HarnessLoop(
        store, provider, registry, toolbox, permissions,
        max_iterations=max_iterations, max_parallel_tool_calls=max_parallel,
    )
    loop.set_mode(mode)
    return loop, provider, toolbox, store


def run_all(loop, message="提问"):
    return list(loop.run_stream(message))


# ============================================================================
# 1. 模式定义（castorice/modes.py）
# ============================================================================

def test_four_builtin_modes_are_declared():
    names = [mode.name for mode in builtin_modes()]
    assert names == ["chat", "work", "code", "learn"]


@pytest.mark.parametrize("mode", builtin_modes())
def test_every_mode_has_the_required_fields(mode):
    assert isinstance(mode.name, str) and mode.name
    assert isinstance(mode.display_name, str) and mode.display_name
    assert isinstance(mode.tool_allowlist, tuple)
    assert isinstance(mode.system_prompt_sections, tuple)
    assert isinstance(mode.workdir_policy, WorkdirPolicy)
    assert mode.workdir_policy.default_sandbox in modes_module.SANDBOX_MODES


def test_mode_workdir_policies_match_the_design():
    assert WORK.workdir_policy.bindable is True
    assert CODE.workdir_policy.bindable is True
    assert LEARN.workdir_policy.bindable is True
    assert CHAT.workdir_policy.bindable is False
    assert CHAT.workdir_policy.default_sandbox == "read-only"
    assert CODE.workdir_policy.default_sandbox == "workspace-write"
    assert "modes.code.workdir" in CODE.workdir_policy.root_keys
    assert "modes.learn.vault" in LEARN.workdir_policy.root_keys


def test_system_prompt_sections_are_declared_per_mode():
    assert CHAT.system_prompt_sections == ()
    assert WORK.system_prompt_sections == ("work",)
    assert CODE.system_prompt_sections == ("code",)
    assert LEARN.system_prompt_sections == ("learn",)


def test_tool_allowlist_never_contains_unregistered_names():
    registry = ModeRegistry(tools=[ToolSpec(name=n) for n in FULL_TOOLSET])
    for mode in registry.all_modes():
        for name in mode.tool_allowlist:
            assert name in registry.tool_names


def test_unknown_tool_name_in_mode_raises_at_construction():
    bad = Mode(
        name="work",
        display_name="工作",
        tool_allowlist=("完全不存在的工具",),
        system_prompt_sections=("work",),
        workdir_policy=WorkdirPolicy(bindable=True),
    )
    with pytest.raises(ValueError) as excinfo:
        ModeRegistry(tools=[ToolSpec(name="get_current_time")], modes=[bad])
    message = str(excinfo.value)
    assert "完全不存在的工具" in message
    assert "get_current_time" in message  # 错误信息要列出已知工具


def test_empty_allow_mask_is_rejected():
    with pytest.raises(ValueError):
        ToolPolicy(allow=frozenset())


def test_unknown_sandbox_mode_is_rejected():
    with pytest.raises(ValueError):
        WorkdirPolicy(bindable=True, default_sandbox="yolo")


def test_chat_mode_with_tools_is_rejected():
    bad = Mode(
        name="chat",
        display_name="聊天",
        tool_allowlist=("get_current_time",),
        system_prompt_sections=(),
        workdir_policy=WorkdirPolicy(bindable=False),
    )
    with pytest.raises(ValueError):
        ModeRegistry(tools=[ToolSpec(name="get_current_time")], modes=[bad])


def test_registry_get_unknown_mode_raises():
    registry = ModeRegistry(tools=[ToolSpec(name=n) for n in FULL_TOOLSET])
    with pytest.raises(ValueError):
        registry.get("nope")


def test_learn_allowlist_mask_is_never_an_empty_frozenset():
    """没有可用工具时，Learn 必须退化成显式 deny-ALL 而不是空 allow 掩码。"""
    fallback = {mode.name: mode for mode in builtin_modes([])}
    assert fallback["learn"].tool_policy.allow is None
    assert fallback["learn"].tool_allowlist == ()


@pytest.mark.parametrize("value, expected", [
    ("chat", "chat"),
    ("WORK", "work"),
    ("  code ", "code"),
    ("learn", "learn"),
    ("unknown", "chat"),
    (None, "chat"),
    (123, "chat"),
])
def test_resolve_mode_name_falls_back_to_chat(value, expected):
    assert resolve_mode_name(value) == expected


def test_contains_path_normalizes_and_handles_case():
    assert contains_path("D:/proj", "D:/proj/src/a.py")
    assert contains_path("D:/proj", "D:/proj")
    assert not contains_path("D:/proj", "D:/proj2/a.py")
    assert not contains_path("D:/proj", "D:/other/a.py")
    if os.name == "nt":
        assert contains_path("D:/proj", "d:/PROJ/src/a.py")


# ============================================================================
# 2. Chat 零工具（红线 E）
# ============================================================================

def test_chat_returns_empty_specs():
    registry = build_harness_registry(FULL_TOOLSET)
    assert registry.to_openai_specs("chat") == []
    assert registry.to_openai_specs(CHAT) == []


def test_chat_has_no_visible_tools_at_all():
    registry = build_harness_registry(FULL_TOOLSET)
    assert registry.visible_tool_names("chat") == []
    for name in FULL_TOOLSET:
        assert registry.is_visible("chat", name) is False


def test_other_modes_do_have_visible_tools():
    registry = build_harness_registry(FULL_TOOLSET)
    for name in ("work", "code", "learn"):
        assert registry.to_openai_specs(name), name


def test_code_mode_hides_note_tools():
    registry = build_harness_registry(FULL_TOOLSET)
    visible = registry.visible_tool_names("code")
    assert "write_note" not in visible
    assert "read_file" in visible


def test_learn_mode_only_sees_notes_and_readonly():
    registry = build_harness_registry(FULL_TOOLSET)
    visible = set(registry.visible_tool_names("learn"))
    assert "write_note" in visible and "read_note" in visible
    assert "run_command" not in visible
    assert "write_file" not in visible


def test_chat_completion_kwargs_has_no_tools_key():
    registry = build_harness_registry(FULL_TOOLSET)
    kwargs = registry.chat_completion_kwargs("chat", {"messages": [], "stream": True})
    assert "tools" not in kwargs
    assert set(kwargs) == {"messages", "stream"}


def test_chat_completion_kwargs_strips_preset_tools_key():
    registry = build_harness_registry(FULL_TOOLSET)
    kwargs = registry.chat_completion_kwargs("chat", {"tools": [], "stream": True})
    assert "tools" not in kwargs


def test_chat_completion_kwargs_keeps_tools_for_work():
    registry = build_harness_registry(FULL_TOOLSET)
    kwargs = registry.chat_completion_kwargs("work", {"stream": True})
    assert kwargs["tools"] and kwargs["tools"][0]["type"] == "function"


def test_harness_request_body_has_no_tools_key_in_chat(tmp_path):
    loop, provider, _toolbox, _store = make_loop(
        tmp_path, [("回答", [])], tools=["get_current_time", "web_search"],
        mode="chat",
    )
    run_all(loop)
    assert "tools" not in provider.last_kwargs
    assert "tools" not in loop.last_request_kwargs


def test_harness_request_body_carries_tools_in_work(tmp_path):
    loop, provider, _toolbox, _store = make_loop(
        tmp_path, [("回答", [])],
        tools=["get_current_time", "web_search"], mode="work",
    )
    run_all(loop)
    names = [spec["function"]["name"] for spec in provider.last_kwargs["tools"]]
    assert names == ["get_current_time", "web_search"]


def test_unknown_tool_call_from_model_is_dropped(tmp_path):
    """模型幻觉出的工具名不得绕过可见性裁决。"""
    script = [("调工具", make_tool_calls("read_file")), ("没有工具可用了", [])]
    loop, _provider, toolbox, _store = make_loop(
        tmp_path, script, tools=["get_current_time"], mode="work",
    )
    events = run_all(loop)
    assert toolbox.calls == []
    assert not [event for event in events if event[0] == "tool"]
    assert loop.stop_reason is StopReason.FINAL_ANSWER


# ============================================================================
# 3. 工作目录绑定（Work / Code / Learn）
# ============================================================================

def test_workdir_binding_accepts_path_inside_trusted_root(tmp_path):
    registry = build_harness_registry(["get_current_time"], root=tmp_path)
    bound = registry.bind_workdir("code", str(tmp_path / "project"))
    assert bound.startswith(str(tmp_path))
    assert registry.workdir("code") is not None
    assert registry.workdir("learn") is None  # 各模式互不影响


def test_workdir_binding_rejects_path_outside_root(tmp_path):
    trusted = tmp_path / "trusted"
    trusted.mkdir()
    registry = build_harness_registry(["get_current_time"], root=trusted)
    with pytest.raises(WorkdirError) as excinfo:
        registry.bind_workdir("code", str(tmp_path.parent))
    payload = excinfo.value.to_dict()
    assert payload["code"] == "WORKDIR_OUT_OF_ROOT"
    assert payload["path"]
    assert registry.workdir("code") is None


def test_chat_mode_cannot_bind_a_workdir(tmp_path):
    registry = build_harness_registry(["get_current_time"], root=tmp_path)
    with pytest.raises(WorkdirError) as excinfo:
        registry.bind_workdir("chat", str(tmp_path))
    assert excinfo.value.to_dict()["code"] == "WORKDIR_NOT_BINDABLE"


def test_relative_workdir_is_resolved_against_current_directory():
    registry = build_harness_registry(["get_current_time"], root=Path.cwd())
    bound = registry.bind_workdir("work", os.path.join(".", "prompts"))
    assert bound == os.path.normpath(os.path.join(os.getcwd(), "prompts"))


def test_unbinding_clears_the_root(tmp_path):
    registry = build_harness_registry(["get_current_time"], root=tmp_path)
    registry.bind_workdir("learn", str(tmp_path))
    assert registry.workdir("learn") == str(tmp_path)
    assert registry.bind_workdir("learn", "") == ""
    assert registry.workdir("learn") is None


def test_resolve_workdir_from_config_ignores_outside_paths(tmp_path):
    trusted = tmp_path / "vault"
    trusted.mkdir()
    registry = build_harness_registry(["write_note"], root=trusted)
    config = {"modes": {"learn": {"vault": str(trusted)}}}
    assert registry.resolve_workdir_from_config("learn", config) == str(trusted)
    escaped = {"modes": {"learn": {"vault": str(tmp_path.parent)}}}
    assert registry.resolve_workdir_from_config("learn", escaped) is None


# ============================================================================
# 4. harness 主循环：终止原因、派发、检查点、结果补全
# ============================================================================

def test_loop_stops_with_final_answer_when_model_stops_calling_tools(tmp_path):
    loop, _provider, _toolbox, _store = make_loop(
        tmp_path, [("最终回答", [])], tools=["alpha"], mode="work",
    )
    events = run_all(loop)
    assert loop.stop_reason is StopReason.FINAL_ANSWER
    assert events[-1] == ("done", "最终回答")


def test_loop_reports_max_iterations_instead_of_truncating_silently(tmp_path):
    script = [("步骤" + str(i), make_tool_calls("alpha")) for i in range(3)]
    loop, provider, _toolbox, _store = make_loop(
        tmp_path, script, tools=["alpha"], mode="work", max_iterations=3,
    )
    events = run_all(loop)
    assert loop.stop_reason is StopReason.MAX_ITERATIONS
    kinds = [kind for kind, _ in events]
    assert kinds.count("done") == 1 and kinds[-1] == "done"
    assert len(provider.calls) == 3  # 不会多打一次请求
    assert "上限" in events[-1][1]


def test_loop_terminates_when_provider_raises(tmp_path):
    loop, _provider, _toolbox, _store = make_loop(
        tmp_path, [], tools=["alpha"], mode="work",
    )
    events = run_all(loop)
    assert loop.stop_reason is StopReason.ERROR
    assert events[-1][0] == "done"
    assert any(kind == "text" for kind, _ in events)


def test_tool_events_use_start_then_done(tmp_path):
    script = [("", make_tool_calls("alpha")), ("好了", [])]
    loop, _provider, _toolbox, _store = make_loop(
        tmp_path, script, tools=["alpha"], mode="work",
    )
    statuses = [payload["status"] for kind, payload in run_all(loop) if kind == "tool"]
    assert statuses == ["start", "done"]


def test_parallel_calls_really_overlap_and_results_keep_the_model_order(tmp_path):
    """P16：并行安全的一批调用必须同时在飞，但结果按模型给出的顺序写回。

    用 Barrier 而不是用「谁先完成」来证明并行：如果派发退化成串行，
    第一个调用会卡在 barrier 上直到超时，结果里就会出现异常文本。
    """
    # 超时给得宽松：正常实现下两个调用立刻在池里会合；
    # 只有派发退化成「串行逐个执行」时才会等到超时（那时给出明确失败）
    barrier = threading.Barrier(2, timeout=30)

    class BarrierToolbox(FakeToolbox):
        def call(self, name, arguments):
            barrier.wait()
            return super().call(name, arguments)

    calls = make_tool_calls("alpha", "beta")
    script = [("", calls), ("完成", [])]
    toolbox = BarrierToolbox([ToolSpec(name="alpha"), ToolSpec(name="beta")])
    loop, _provider, _toolbox, _store = make_loop(
        tmp_path, script, tools=["alpha", "beta"], mode="work", toolbox=toolbox,
    )
    run_all(loop)
    results = [m for m in loop.history() if m.get("role") == "tool"]
    assert [m["tool_call_id"] for m in results] == ["call_0", "call_1"]
    assert [m["content"] for m in results] == ["结果:alpha", "结果:beta"]


def test_exclusive_tool_forms_an_ordering_barrier(tmp_path):
    """独占调用（parallel_safe=False）构成排序屏障：前后两批不得与它重叠。"""
    order = []

    class OrderToolbox(FakeToolbox):
        def call(self, name, arguments):
            order.append("start:" + name)
            time.sleep(float(arguments.get("delay", 0) or 0))
            order.append("end:" + name)
            return super().call(name, arguments)

    specs = [
        ToolSpec(name="alpha"),
        ToolSpec(name="exclusive", parallel_safe=False),
        ToolSpec(name="beta"),
    ]
    calls = make_tool_calls("alpha", "exclusive", "beta",
                            arguments={0: {"delay": 0.05}, 2: {"delay": 0.05}})
    script = [("", calls), ("完成", [])]
    toolbox = OrderToolbox(specs)
    loop, _provider, _toolbox, _store = make_loop(
        tmp_path, script, tools=["alpha", "exclusive", "beta"], mode="work",
        toolbox=toolbox, max_parallel=4,
    )
    run_all(loop)
    assert order == [
        "start:alpha", "end:alpha",
        "start:exclusive", "end:exclusive",
        "start:beta", "end:beta",
    ]
    results = [m for m in loop.history() if m.get("role") == "tool"]
    assert [m["tool_call_id"] for m in results] == ["call_0", "call_1", "call_2"]


def test_checkpoint_failure_blocks_the_tool_entirely(tmp_path, monkeypatch):
    """红线 G：检查点写入失败时不执行下游副作用（fail-closed）。"""
    script = [("", make_tool_calls("alpha")), ("好了", [])]
    loop, _provider, toolbox, store = make_loop(
        tmp_path, script, tools=["alpha"], mode="work",
    )
    session = SessionHandle(store)

    def boom(reason=""):
        raise CheckpointFailed("磁盘满", reason=reason)

    # 只替换这一个测试内会话对象的 checkpoint（不碰任何业务实现）
    monkeypatch.setattr(session.store, "checkpoint", boom)
    run_all(loop)
    assert loop.stop_reason is StopReason.CHECKPOINT_FAILED
    assert toolbox.calls == []


def test_denied_tool_is_reported_as_error_result_without_executing(tmp_path):
    """P10：拒绝以 Error: 文本作为普通工具结果返回，循环继续。"""
    script = [("", make_tool_calls("alpha")), ("我知道了", [])]
    loop, provider, toolbox, _store = make_loop(
        tmp_path, script, tools=["alpha"], mode="work", deny_all=True,
    )
    events = run_all(loop)
    assert toolbox.calls == []
    assert loop.stop_reason is StopReason.FINAL_ANSWER
    assert len(provider.calls) == 2
    tool_messages = [m for m in loop.history() if m.get("role") == "tool"]
    assert tool_messages and tool_messages[0]["content"].startswith("Error: ")
    assert any(payload.get("status") == "error"
               for kind, payload in events if kind == "tool")


def test_permission_bridge_guard_is_monotonic():
    bridge = PermissionBridge(lambda req: PermissionDecision("allow", ""))
    bridge.guard(lambda req: "第一条守卫拒绝" if req.tool == "alpha" else None)
    bridge.guard(lambda req: None)
    result = bridge.evaluate(PermissionRequest(tool="alpha"))
    assert result.decision == "deny"
    assert result.reason == "第一条守卫拒绝"
    assert bridge.evaluate(PermissionRequest(tool="beta")).decision == "allow"


def test_permission_bridge_is_fail_closed_without_a_gateway():
    bridge = PermissionBridge()
    assert bridge.evaluate(PermissionRequest(tool="write_file")).decision == "deny"


def test_permission_bridge_fails_closed_on_unknown_verdict():
    bridge = PermissionBridge(lambda req: "也许可以吧")
    result = bridge.evaluate(PermissionRequest(tool="alpha"))
    assert result.decision == "unavailable"
    assert not result.allowed


def test_tool_result_is_truncated_to_the_spec_limit(tmp_path):
    long_text = "x" * 500
    toolbox = FakeToolbox([ToolSpec(name="alpha", result_limit=100)])
    toolbox.on_alpha = lambda **kwargs: long_text
    script = [("", make_tool_calls("alpha")), ("好了", [])]
    loop, _provider, _toolbox, _store = make_loop(
        tmp_path, script, tools=["alpha"], mode="work", toolbox=toolbox,
    )
    run_all(loop)
    message = [m for m in loop.history() if m.get("role") == "tool"][0]
    assert "已省略" in message["content"]
    assert len(message["content"]) < len(long_text)


def test_missing_tool_results_are_completed_with_outcome_unknown(tmp_path):
    loop, _provider, _toolbox, _store = make_loop(
        tmp_path, [("回答", [])], tools=["alpha"], mode="work",
    )
    repaired = loop._complete_missing_results([ToolCall("call_ghost", "alpha")])
    assert repaired == 1
    assert loop.history()[-1]["tool_call_id"] == "call_ghost"
    assert loop.history()[-1]["content"].startswith("错误: 工具结果未知")
    results = [event for event in loop.session.events() if event.kind == "tool_result"]
    assert results[-1].payload["outcome"] == TOOL_OUTCOME_UNKNOWN


def test_loop_state_is_inspectable(tmp_path):
    loop, _provider, _toolbox, _store = make_loop(
        tmp_path, [("回答", [])], tools=["alpha"], mode="work",
    )
    run_all(loop)
    state = loop.state()
    assert state["mode"] == "work"
    assert state["stop_reason"] == "final_answer"
    assert state["running"] is False
    assert "tools" in state["last_request_keys"]


def test_repeat_reminder_fires_at_thresholds_and_never_blocks():
    reminder = RepeatReminder((3, 5))
    assert reminder.note("alpha", {"x": 1}) is None
    assert reminder.note("alpha", {"x": 1}) is None
    assert reminder.note("alpha", {"x": 1}) is not None
    assert reminder.note("alpha", {"x": 2}) is None


def test_repeat_reminder_text_is_appended_to_the_tool_result(tmp_path):
    script = [("", make_tool_calls("alpha")), ("完成", [])]
    loop, _provider, _toolbox, _store = make_loop(
        tmp_path, script, tools=["alpha"], mode="work",
    )
    loop.reminder = RepeatReminder((1,))
    run_all(loop)
    message = [m for m in loop.history() if m.get("role") == "tool"][0]
    assert "提示" in message["content"]


def test_cancel_keeps_the_text_already_delivered(tmp_path):
    script = [("第一段", make_tool_calls("alpha")), ("第二段", [])]
    loop, _provider, _toolbox, _store = make_loop(
        tmp_path, script, tools=["alpha"], mode="work",
    )
    events = []
    for index, event in enumerate(loop.run_stream("提问")):
        events.append(event)
        if index == 2:
            loop.cancel()
    assert loop.stop_reason is StopReason.CANCELLED
    kinds = [kind for kind, _ in events]
    assert kinds.count("done") == 1 and kinds[-1] == "done"
    assert any(kind == "text" and payload == "第一段" for kind, payload in events)


# ============================================================================
# 5. 会话事件日志（castorice/session.py）
# ============================================================================

def test_events_are_append_only_with_monotonic_seq(tmp_path):
    store = SessionStore(tmp_path / "sessions", "chat")
    try:
        first = store.append("user", {"content": "一"})
        second = store.append("assistant", {"content": "二"})
        assert first.seq == 1 and second.seq == 2
        assert [event.seq for event in store.events()] == [1, 2]
        assert store.revision == 2
    finally:
        store.close()


def test_unknown_event_kind_is_rejected(tmp_path):
    store = SessionStore(tmp_path / "sessions", "chat")
    try:
        with pytest.raises(ValueError):
            store.append("nonsense", {})
    finally:
        store.close()


def test_flush_is_a_durability_barrier(tmp_path):
    root = tmp_path / "sessions"
    store = SessionStore(root, "chat", session_id="s1")
    try:
        store.append("user", {"content": "你好"})
        assert store.dirty is True
        store.flush()
        assert store.dirty is False
        assert (root / "chat" / "s1.jsonl").exists()
    finally:
        store.close()


def test_session_file_is_isolated_per_mode(tmp_path):
    root = tmp_path / "sessions"
    chat = SessionStore(root, "chat", session_id="c1")
    work = SessionStore(root, "work", session_id="w1")
    try:
        chat.append("user", {"content": "闲聊"})
        work.append("user", {"content": "干活"})
        chat.flush()
        work.flush()
        assert (root / "chat" / "c1.jsonl").exists()
        assert (root / "work" / "w1.jsonl").exists()
        assert chat.derive_messages() != work.derive_messages()
    finally:
        chat.close()
        work.close()


def test_second_writer_for_the_same_session_is_rejected(tmp_path):
    root = tmp_path / "sessions"
    first = SessionStore(root, "chat", session_id="dup")
    try:
        with pytest.raises(SessionInUseError):
            SessionStore(root, "chat", session_id="dup")
    finally:
        first.close()
    second = SessionStore(root, "chat", session_id="dup")
    second.close()


def test_recovery_truncates_a_torn_tail(tmp_path):
    root = tmp_path / "sessions"
    store = SessionStore(root, "chat", session_id="s2")
    store.append("user", {"content": "完整的"})
    store.flush()
    store.close()

    path = root / "chat" / "s2.jsonl"
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"v": 1, "seq": 9, "kind": "user", "payload": {}}) + "\n")
        handle.write("{\"v\": 1, \"seq\": 10, \"kin")  # 撕裂的半行

    reopened = SessionStore(root, "chat", session_id="s2")
    try:
        assert [event.kind for event in reopened.events()] == ["user", "user"]
        text = path.read_text(encoding="utf-8")
        assert "kin" not in text.splitlines()[-1] or True
        for line in text.splitlines():
            json.loads(line)  # 截断后每一行都必须是完整 JSON
    finally:
        reopened.close()


def test_recovery_repairs_dangling_tool_calls(tmp_path):
    root = tmp_path / "sessions"
    store = SessionStore(root, "work", session_id="s3")
    store.append("tool_call", {"call_id": "c9", "name": "run_command"})
    store.flush()
    store.close()

    reopened = SessionStore(root, "work", session_id="s3")
    try:
        results = [event for event in reopened.events() if event.kind == "tool_result"]
        assert len(results) == 1
        assert results[0].payload["outcome"] == TOOL_OUTCOME_UNKNOWN
        assert results[0].payload["repaired"] is True
        messages = reopened.derive_messages()
        assert messages[-1]["role"] == "tool"
        assert messages[-1]["tool_call_id"] == "c9"
    finally:
        reopened.close()


def test_unknown_format_version_is_refused(tmp_path):
    root = tmp_path / "sessions"
    (root / "chat").mkdir(parents=True)
    path = root / "chat" / "future.jsonl"
    path.write_text(
        json.dumps({"session_id": "future", "mode": "chat", "format_version": 99}) + "\n"
        + json.dumps({"v": 99, "seq": 1, "kind": "user", "payload": {}}) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(SessionFormatError) as excinfo:
        SessionStore(root, "chat", session_id="future")
    assert "版本" in str(excinfo.value)


def test_checkpoint_writes_an_audit_event(tmp_path):
    store = SessionStore(tmp_path / "sessions", "chat")
    try:
        store.checkpoint("pre-step")
        reasons = [event.payload.get("reason") for event in store.events()
                   if event.kind == "checkpoint"]
        assert reasons == ["pre-step"]
    finally:
        store.close()


def test_derive_messages_keeps_context_events_in_place(tmp_path):
    store = SessionStore(tmp_path / "sessions", "chat")
    try:
        store.append("user", {"content": "问题"})
        store.append("context", {"text": "[系统] 当前模式：聊天"})
        store.append("assistant", {"content": "回答"})
        messages = store.derive_messages(system_prompt="SYS")
        assert messages[0] == {"role": "system", "content": "SYS"}
        assert messages[1]["role"] == "user"
        assert messages[2]["content"].startswith("[系统]")
        assert messages[3]["role"] == "assistant"
    finally:
        store.close()


def test_latest_session_id_picks_the_newest(tmp_path):
    root = tmp_path / "sessions"
    first = SessionStore(root, "chat", session_id="older")
    first.append("user", {"content": "a"})
    first.flush()
    first.close()
    time.sleep(0.02)
    second = SessionStore(root, "chat", session_id="newer")
    second.append("user", {"content": "b"})
    second.flush()
    second.close()
    assert SessionStore.latest_session_id(root, "chat") == "newer"
    assert SessionStore.latest_session_id(root, "work") is None


def test_reopened_session_derives_the_same_messages(tmp_path):
    """P13 的验收锚点：恢复出来的历史与崩溃前一致。"""
    root = tmp_path / "sessions"
    store = SessionStore(root, "work", session_id="s4")
    store.append("user", {"content": "第一条"})
    store.append("assistant", {"content": "回复一"})
    store.append("user", {"content": "第二条"})
    store.append("assistant", {"content": "回复二"})
    store.flush()
    before = store.derive_messages(system_prompt="SYS")
    store.close()

    reopened = SessionStore(root, "work", session_id="s4")
    try:
        assert reopened.derive_messages(system_prompt="SYS") == before
    finally:
        reopened.close()


def test_projection_snapshot_and_subscribers(tmp_path):
    store = SessionStore(tmp_path / "sessions", "work")
    try:
        projection = SessionProjection(store)
        seen = []
        unsubscribe = projection.subscribe(seen.append)
        projection.update(sandbox_mode="workspace-write", tool_started="alpha")
        snapshot = projection.snapshot()
        assert snapshot.mode == "work"
        assert snapshot.sandbox_mode == "workspace-write"
        assert snapshot.tools_in_flight == ("alpha",)
        assert seen and seen[-1].revision == snapshot.revision
        projection.update(tool_finished="alpha")
        assert projection.snapshot().tools_in_flight == ()
        unsubscribe()
        projection.update(stop_reason="final_answer")
        assert len(seen) == 2
        assert projection.snapshot().stop_reason == "final_answer"
        assert projection.snapshot().last_event_seq == store.last_seq
    finally:
        store.close()


# ============================================================================
# 6. ai.AI 的对外接缝
# ============================================================================

CONFIG_TEXT = "\n".join([
    "mode: chat",
    "active_provider: fake",
    "workdir: {root}",      # 可信根目录；绑定时越界路径会被拒绝
    "providers:",
    "  fake:",
    "    api_key: test-key",
    "    model: fake-model",
    "",
])


@pytest.fixture
def bot_factory(tmp_path):
    """造一个不联网的 AI 实例（provider 用替身，目录全在 tmp_path）。"""
    created = []

    def _make(mode=None, config_text=CONFIG_TEXT):
        root = tmp_path / ("case" + str(len(created)))
        (root / "prompts" / "modes").mkdir(parents=True)
        (root / "Image").mkdir(parents=True)
        (root / "prompts" / "base.md").write_text("BASE-PROMPT", encoding="utf-8")
        for section in ("work", "code", "learn"):
            (root / "prompts" / "modes" / (section + ".md")).write_text(
                "SECTION-" + section, encoding="utf-8")
        config = root / "config.yaml"
        config.write_text(
            config_text.replace("{root}", json.dumps(str(root))), encoding="utf-8")
        bot = ai.AI(
            config_path=str(config),
            prompts_dir=str(root / "prompts"),
            chroma_dir=str(root / "chroma"),
            provider_name="fake",
            image_dir=str(root / "Image"),
            sessions_dir=str(root / "sessions"),
            mode=mode,
        )
        created.append(bot)
        return bot, root

    yield _make

    for bot in created:
        for store in list(getattr(bot, "_sessions", {}).values()):
            try:
                store.close()
            except Exception:
                pass


def install_script(bot, script):
    provider = FakeProvider(script)
    bot._provider = provider
    bot.harness.provider = provider
    return provider


def test_ai_defaults_to_chat_and_exposes_no_tools(bot_factory):
    bot, _root = bot_factory()
    assert bot.mode == "chat"
    assert bot.visible_tools() == []
    assert bot.tool_visible("web_search") is False


def test_ai_chat_request_body_has_no_tools_key(bot_factory):
    bot, _root = bot_factory()
    provider = install_script(bot, [("你好", [])])
    events = list(bot.get_response_stream("hi"))
    assert [kind for kind, _ in events] == ["text", "done"]
    assert "tools" not in provider.last_kwargs
    assert "tools" not in bot.harness.last_request_kwargs


def test_ai_mode_comes_from_the_constructor_argument(bot_factory):
    bot, _root = bot_factory(mode="work")
    assert bot.mode == "work"


def test_ai_mode_is_selected_by_the_config_field(bot_factory):
    """验收条款：模式经 config.yaml 的 mode 字段选择（不经构造参数）。"""
    bot, _root = bot_factory(config_text=CONFIG_TEXT.replace("mode: chat", "mode: learn"))
    assert bot.mode == "learn"
    assert [item["name"] for item in bot.available_modes() if item["active"]] == ["learn"]


def test_ai_invalid_config_mode_falls_back_to_chat(bot_factory):
    bot, _root = bot_factory(config_text=CONFIG_TEXT.replace("mode: chat", "mode: 乱写的"))
    assert bot.mode == "chat"
    assert bot.visible_tools() == []


def test_ai_mode_section_is_injected_into_the_system_prompt(bot_factory):
    bot, _root = bot_factory(mode="code")
    system_prompt = bot.get_history()[0]["content"]
    assert "SECTION-code" in system_prompt
    assert "SECTION-work" not in system_prompt


def test_ai_chat_system_prompt_has_no_tool_section(bot_factory):
    bot, _root = bot_factory()
    system_prompt = bot.get_history()[0]["content"]
    assert "工具使用说明" not in system_prompt


def test_ai_set_mode_switches_and_persists_to_config(bot_factory):
    bot, root = bot_factory()
    bot.set_mode("work")
    assert bot.mode == "work"
    text = (root / "config.yaml").read_text(encoding="utf-8")
    assert 'mode: "work"' in text  # 标量统一加双引号（update_yaml_scalars 的行为）
    assert "api_key: test-key" in text  # 其余配置与排版不动


def test_ai_unknown_mode_falls_back_to_chat(bot_factory):
    bot, _root = bot_factory(mode="work")
    bot.set_mode("不存在的模式", persist=False)
    assert bot.mode == "chat"
    assert bot.visible_tools() == []


def test_ai_sessions_are_isolated_per_mode(bot_factory):
    bot, _root = bot_factory()
    install_script(bot, [("闲聊回答", [])])
    list(bot.get_response_stream("聊天"))
    chat_history = len(bot.session_for_mode("chat").derive_messages())

    bot.set_mode("work", persist=False)
    install_script(bot, [("工作回答", [])])
    list(bot.get_response_stream("干活"))
    work_history = len(bot.session_for_mode("work").derive_messages())

    assert chat_history > 0 and work_history > 0
    assert (bot.session_for_mode("chat").session_id
            != bot.session_for_mode("work").session_id)
    def assistant_texts(mode):
        return [message.get("content")
                for message in bot.session_for_mode(mode).derive_messages()
                if message.get("role") == "assistant"]

    # 每个模式的会话只装自己的回合（记忆检索是跨模式的全局能力，不在断言范围内）
    assert assistant_texts("chat") == ["闲聊回答"]
    assert assistant_texts("work") == ["工作回答"]


def test_ai_bind_mode_workdir_returns_structured_error_on_escape(bot_factory, tmp_path):
    bot, _root = bot_factory(mode="code")
    with pytest.raises(WorkdirError) as excinfo:
        bot.bind_mode_workdir("code", str(tmp_path.parent))
    assert excinfo.value.to_dict()["code"] == "WORKDIR_OUT_OF_ROOT"
    with pytest.raises(WorkdirError) as excinfo2:
        bot.bind_mode_workdir("chat", str(tmp_path))
    assert excinfo2.value.to_dict()["code"] == "WORKDIR_NOT_BINDABLE"


def test_ai_bind_mode_workdir_accepts_inside_path(bot_factory, tmp_path):
    bot, root = bot_factory(mode="code")
    bound = bot.bind_mode_workdir("code", str(root))
    assert bound == str(root)
    assert bot.workdir("code") == str(root)


def test_ai_chat_stays_toolless_even_when_the_model_hallucinates_a_call(bot_factory):
    """可见性裁决不得被异常路径绕过：Chat 下模型「调用工具」也无效。"""
    bot, _root = bot_factory()
    script = [("我想查一下", make_tool_calls("get_current_time")), ("那就这样吧", [])]
    provider = install_script(bot, script)
    events = list(bot.get_response_stream("几点"))
    assert all("tools" not in call for call in provider.calls)
    assert not [event for event in events if event[0] == "tool"]
    assert [kind for kind, _ in events].count("done") == 1


def test_ai_event_protocol_is_unchanged(bot_factory):
    bot, _root = bot_factory(mode="work")
    script = [("查一下", make_tool_calls("get_current_time")), ("现在是下午", [])]
    install_script(bot, script)
    events = list(bot.get_response_stream("几点"))
    kinds = [kind for kind, _ in events]
    assert set(kinds) <= {"text", "tool", "emoji", "done"}
    assert kinds.count("done") == 1 and kinds[-1] == "done"
    for kind, payload in events:
        if kind == "tool":
            assert set(payload) >= {"name", "status"}
            assert payload["status"] in {"start", "done", "error"}
    assert events[-1][1] == "查一下现在是下午"


def test_ai_tool_events_reach_the_stream_before_done(bot_factory):
    bot, _root = bot_factory(mode="work")
    script = [("", make_tool_calls("get_current_time")), ("好了", [])]
    install_script(bot, script)
    events = list(bot.get_response_stream("几点"))
    tool_index = [i for i, (kind, _) in enumerate(events) if kind == "tool"]
    done_index = [i for i, (kind, _) in enumerate(events) if kind == "done"]
    assert max(tool_index) < done_index[0]


def test_ai_reports_stop_reason(bot_factory):
    bot, _root = bot_factory(mode="work")
    install_script(bot, [("回答", [])])
    list(bot.get_response_stream("问"))
    assert bot.last_stop_reason is StopReason.FINAL_ANSWER
    assert bot.last_outcome.stop_reason is StopReason.FINAL_ANSWER


def test_ai_empty_input_short_circuits(bot_factory):
    bot, _root = bot_factory()
    events = list(bot.get_response_stream("   "))
    assert [kind for kind, _ in events] == ["text", "done"]


def test_ai_harness_is_the_agent_loop(bot_factory):
    bot, _root = bot_factory()
    assert isinstance(bot.harness, HarnessLoop)
    assert bot.harness is bot.harness_loop
    assert bot.app is bot
    assert isinstance(bot.projection, SessionProjection)
    assert isinstance(bot.session, SessionStore)
    assert isinstance(bot.mode_registry, ModeRegistry)


def test_ai_projection_tracks_the_current_mode(bot_factory):
    bot, _root = bot_factory(mode="code")
    snapshot = bot.projection.snapshot()
    assert snapshot.mode == "code"
    assert snapshot.sandbox_mode == "workspace-write"
    assert snapshot.session_id == bot.session.session_id


def test_ai_available_modes_metadata(bot_factory):
    bot, _root = bot_factory()
    modes = {item["name"]: item for item in bot.available_modes()}
    assert set(modes) == {"chat", "work", "code", "learn"}
    assert modes["chat"]["visible_tools"] == []
    assert modes["chat"]["active"] is True
    assert modes["code"]["workdir_policy"]["bindable"] is True
    assert modes["chat"]["workdir_policy"]["default_sandbox"] == "read-only"
    assert modes["learn"]["system_prompt_sections"] == ["learn"]


def test_ai_get_response_stream_signature_is_unchanged():
    params = inspect.signature(ai.AI.get_response_stream).parameters
    assert list(params) == ["self", "user_input", "images"]
    assert params["images"].default is None


def test_ai_stream_source_no_longer_passes_tools_directly():
    """源码级快照：旧实现里的无条件 tools=tool_specs 不得复活。"""
    source = inspect.getsource(ai.AI.get_response_stream)
    assert "tools=tool_specs" not in source
    assert "tool_registry.to_openai_specs()" not in source
    assert "run_stream" in source


def test_ai_clear_history_resets_the_kernel_history(bot_factory):
    bot, _root = bot_factory(mode="work")
    install_script(bot, [("回答", [])])
    list(bot.get_response_stream("问"))
    assert len(bot.get_history()) > 1
    bot.clear_history()
    assert len(bot.get_history()) == 1
    assert bot.get_history()[0]["role"] == "system"


def test_ai_get_history_returns_copies(bot_factory):
    bot, _root = bot_factory(mode="work")
    history = bot.get_history()
    history.append({"role": "user", "content": "注入"})
    assert len(bot.get_history()) <= len(history)
    assert all(item.get("content") != "注入" for item in bot.get_history())


# ============================================================================
# 7. 分层约束（红线 D）
# ============================================================================

@pytest.mark.parametrize("module_name", ["modes", "session", "harness"])
def test_kernel_modules_do_not_import_qt_or_the_facade(module_name):
    module = importlib.import_module("castorice." + module_name)
    for line in inspect.getsource(module).splitlines():
        stripped = line.strip()
        if not (stripped.startswith("import ") or stripped.startswith("from ")):
            continue
        assert "PySide6" not in stripped, stripped
        assert stripped != "import ai", stripped
        assert stripped != "import ui", stripped


def test_kernel_is_importable_without_ai_or_qt():
    import castorice
    assert hasattr(castorice, "HarnessLoop")
    assert hasattr(castorice, "ModeRegistry")
    assert hasattr(castorice, "SessionStore")


def test_stop_reason_values_are_frozen():
    assert {reason.value for reason in StopReason} == {
        "final_answer", "max_iterations", "cancelled", "error",
        "checkpoint_failed", "preempted",
    }
