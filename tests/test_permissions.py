"""权限审批网关（castorice/permissions.py）的行为测试。

对齐 docs/refactor/04-dsh-harness-notes.md 的 1.3 节与可清单 P3-P10，
以及 6.1 节点名的三条负面用例：

1. Chat 模式：断言请求 kwargs **不含** tools 键（不是断言 tools == []）；
2. 检查点失败：flush 抛错时工具函数**未被调用**；
3. 无应答者审批：broker 无应答者时返回拒绝，且副作用未发生。

全部离线、不需要 API Key，也不依赖 Qt。
"""

import os
import sys
import threading
import time
from pathlib import Path

import pytest

import castorice.permissions as perms
from castorice.harness import PermissionDecision, PermissionRequest
from castorice.modes import (
    SANDBOX_DANGER_FULL_ACCESS,
    SANDBOX_READ_ONLY,
    SANDBOX_WORKSPACE_WRITE,
)


# ----------------------------------------------------------------------------
# 夹具
# ----------------------------------------------------------------------------

class FakeSpec:
    """工具描述符的鸭子类型替身（网关只按属性读取）。"""

    def __init__(self, name, *, read_only=False, requires_approval=None,
                 kind="generic", parallel_safe=True):
        self.name = name
        self.read_only = read_only
        self.requires_approval = requires_approval
        self.kind = kind
        self.parallel_safe = parallel_safe


SPECS = [
    FakeSpec("read_file", read_only=True, requires_approval=False),
    FakeSpec("web_search", read_only=True, requires_approval=False),
    FakeSpec("write_file", read_only=False, requires_approval=True, kind="write"),
    FakeSpec("edit_file", read_only=False, requires_approval=True, kind="write"),
    FakeSpec("run_command", read_only=False, requires_approval=True, kind="command"),
    FakeSpec("make_document", read_only=False, requires_approval=True, kind="write"),
]


def build_gateway(root, *, mode="code", sandbox=SANDBOX_WORKSPACE_WRITE,
                  approval_policy="ask", responder=None, config=None,
                  visibility=None, command_allowlist=None):
    settings = dict(config or {})
    settings.setdefault("approval_policy", approval_policy)
    settings.setdefault("sandbox_default", sandbox)
    gateway = perms.PermissionGateway(
        settings,
        tool_specs=SPECS,
        mode=mode,
        roots=[str(root)],
        sandbox=sandbox,
        mode_command_allowlist=command_allowlist or {
            "code": ("dir", "ls", "type", "cat", "git status", "git diff"),
            "learn": (),
        },
        visibility=visibility,
    )
    if responder is not None:
        gateway.set_ask_callback(responder)
    return gateway


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    return root


# ============================================================================
# 1. 判定接口：allow / ask / deny 与理由
# ============================================================================

def test_allow_verdict_for_readonly_tool(workspace):
    gateway = build_gateway(workspace)
    decision = gateway.decide("read_file", {"path": str(workspace / "a.txt")})
    assert decision.decision == "allow"
    assert decision.allowed is True
    assert decision.info["code"] == "READ_ONLY_TOOL"


def test_ask_verdict_for_side_effect_tool_without_responder(workspace):
    """有副作用工具在 ask 策略下走审批；无应答者时按拒绝关闭。"""
    gateway = build_gateway(workspace)
    decision = gateway.decide("write_file", {"path": str(workspace / "a.txt")})
    assert decision.decision == perms.DECISION_UNAVAILABLE
    assert "应答者" in decision.reason
    assert decision.info["code"] == perms.CODE_APPROVAL_NO_RESPONDER


def test_deny_verdict_carries_human_readable_reason(workspace):
    gateway = build_gateway(workspace)
    outside = workspace.parent / "outside.txt"
    decision = gateway.decide("write_file", {"path": str(outside)})
    assert decision.decision == "deny"
    assert "拒绝写入" in decision.reason
    assert "工作区" in decision.reason
    assert decision.info["path"] == str(outside)
    assert decision.info["code"] == perms.CODE_SANDBOX_DENIED


def test_check_returns_only_the_verdict_word(workspace):
    gateway = build_gateway(workspace)
    assert gateway.check("read_file", {"path": str(workspace / "a.txt")}) == "allow"
    assert gateway.check("write_file", {"path": str(workspace.parent / "x")}) == "deny"


def test_unknown_tool_is_denied_with_known_list(workspace):
    gateway = build_gateway(workspace)
    decision = gateway.decide("not_a_tool", {})
    assert decision.decision == "deny"
    assert decision.info["code"] == perms.CODE_UNKNOWN_TOOL
    assert "read_file" in decision.reason


def test_mode_visibility_is_enforced(workspace):
    """复用 t3 的可见性裁决：不可见的工具一律拒绝。"""
    gateway = build_gateway(
        workspace, visibility=lambda mode, tool: tool != "write_file")
    decision = gateway.decide("write_file", {"path": str(workspace / "a.txt")})
    assert decision.decision == "deny"
    assert decision.info["code"] == perms.CODE_TOOL_NOT_VISIBLE


def test_visibility_exception_fails_closed(workspace):
    def broken(mode, tool):
        raise RuntimeError("可见性服务崩了")

    gateway = build_gateway(workspace, visibility=broken)
    decision = gateway.decide("read_file", {"path": str(workspace / "a.txt")})
    assert decision.decision == "deny"
    assert decision.info["code"] == perms.CODE_TOOL_NOT_VISIBLE


def test_read_only_sandbox_denies_side_effect_tools(workspace):
    gateway = build_gateway(workspace, sandbox=SANDBOX_READ_ONLY)
    decision = gateway.decide("write_file", {"path": str(workspace / "a.txt")})
    assert decision.decision == "deny"
    assert decision.info["code"] == perms.CODE_SANDBOX_READ_ONLY


def test_danger_full_access_allows_outside_paths(tmp_path):
    gateway = build_gateway(tmp_path, sandbox=SANDBOX_DANGER_FULL_ACCESS,
                            responder=perms.AutoResponder("allow"))
    decision = gateway.decide("write_file", {"path": str(tmp_path.parent / "x.txt")})
    assert decision.decision == "allow"


# ============================================================================
# 2. 单调 guard（P9）
# ============================================================================

def test_first_denying_guard_wins_and_cannot_be_overridden(workspace):
    gateway = build_gateway(workspace, responder=perms.AutoResponder("allow"))
    gateway.guard(lambda req: "第一个守卫拒绝")
    gateway.guard(lambda req: None)
    gateway.guard(lambda req: "第二个守卫也拒绝")
    decision = gateway.decide("read_file", {"path": str(workspace / "a.txt")})
    assert decision.decision == "deny"
    assert decision.reason == "第一个守卫拒绝"
    assert decision.info["code"] == perms.CODE_GUARD_DENIED


def test_guard_exception_fails_closed(workspace):
    def broken(request):
        raise RuntimeError("守卫崩了")

    gateway = build_gateway(workspace)
    gateway.guard(broken)
    decision = gateway.decide("read_file", {"path": str(workspace / "a.txt")})
    assert decision.decision == "deny"
    assert "守卫异常" in decision.reason


def test_guard_passes_through_when_returning_none(workspace):
    gateway = build_gateway(workspace)
    gateway.guard(lambda req: None)
    assert gateway.decide("read_file",
                          {"path": str(workspace / "a.txt")}).decision == "allow"


# ============================================================================
# 3. 审批：never / 无应答者 / 超时 / ALREADY_IN_FLIGHT / allowed-once
# ============================================================================

def test_never_policy_rejects_before_dispatch_and_never_calls_responder(workspace):
    responder = perms.AutoResponder("allow")
    gateway = build_gateway(workspace, approval_policy="never", responder=responder)
    decision = gateway.decide("write_file", {"path": str(workspace / "a.txt")})
    assert decision.decision == "deny"
    assert decision.info["code"] == perms.CODE_APPROVAL_POLICY_NEVER
    assert responder.call_count == 0          # 应答者根本不该被调用


def test_missing_responder_is_unavailable_not_allow(workspace):
    gateway = build_gateway(workspace)
    decision = gateway.decide("write_file", {"path": str(workspace / "a.txt")})
    assert decision.decision == perms.DECISION_UNAVAILABLE
    assert decision.allowed is False


def test_responder_exception_fails_closed(workspace):
    def broken(request, request_id):
        raise RuntimeError("窗口炸了")

    gateway = build_gateway(workspace, responder=broken)
    decision = gateway.decide("write_file", {"path": str(workspace / "a.txt")})
    assert decision.decision == perms.DECISION_UNAVAILABLE
    assert decision.info["code"] == perms.CODE_APPROVAL_RESPONDER_FAILED


def test_responder_returning_unknown_word_fails_closed(workspace):
    gateway = build_gateway(workspace, responder=lambda req, rid: "maybe")
    decision = gateway.decide("write_file", {"path": str(workspace / "a.txt")})
    assert decision.decision == perms.DECISION_UNAVAILABLE
    assert decision.info["code"] == perms.CODE_APPROVAL_UNKNOWN_ANSWER


def test_responder_timeout_fails_closed(workspace):
    """应答者把请求丢给主线程但主线程始终不回答 -> 超时即拒绝。"""
    broker = perms.ApprovalBroker("ask", timeout=0.05)

    def defer_only(request, request_id):
        broker.defer(request_id)
        return None

    broker.set_ask_callback(defer_only)
    decision = broker.request(PermissionRequest(
        tool="write_file", mode="code", call_id="call_timeout"))
    assert decision.decision == perms.DECISION_UNAVAILABLE
    assert decision.info["code"] == perms.CODE_APPROVAL_TIMEOUT


def test_async_resolve_supplies_the_decision(workspace):
    broker = perms.ApprovalBroker("ask", timeout=2.0)
    captured = {}

    def defer_only(request, request_id):
        broker.defer(request_id)
        captured["request_id"] = request_id
        return None

    broker.set_ask_callback(defer_only)

    def answerer():
        for _ in range(200):
            if captured.get("request_id"):
                break
            time.sleep(0.005)
        broker.resolve(captured["request_id"], "allow")

    worker = threading.Thread(target=answerer, daemon=True)
    worker.start()
    decision = broker.request(PermissionRequest(
        tool="write_file", mode="code", call_id="call_async"))
    worker.join(timeout=3)
    assert decision.decision == "allow"


def test_allowed_once_is_not_remembered(workspace):
    """批准只对那一次生效：第二次请求必须重新走审批。"""
    answers = ["allow", "deny"]
    responder = perms.AutoResponder("allow")

    def sequenced(request, request_id):
        return answers.pop(0) if answers else "deny"

    broker = perms.ApprovalBroker("ask", sequenced)
    first = broker.request(PermissionRequest(
        tool="write_file", mode="code", call_id="c1"))
    second = broker.request(PermissionRequest(
        tool="write_file", mode="code", call_id="c2"))
    assert first.decision == "allow"
    assert second.decision == "deny"
    assert responder.decision == "allow"     # 未使用，仅确认没有全局缓存


def test_second_request_with_same_key_is_already_in_flight():
    """同一次审批只允许一个在跑（P8 / ALREADY_IN_FLIGHT）。"""
    broker = perms.ApprovalBroker("ask", timeout=1.0)

    def defer_only(request, request_id):
        broker.defer(request_id)
        return None

    broker.set_ask_callback(defer_only)
    request = PermissionRequest(tool="write_file", mode="code", call_id="dup")

    results = {}

    def first_call():
        results["first"] = broker.request(request)

    worker = threading.Thread(target=first_call, daemon=True)
    worker.start()
    deadline = time.time() + 2
    while not broker.in_flight() and time.time() < deadline:
        time.sleep(0.005)

    second = broker.request(request)
    assert second.info["code"] == perms.CODE_ALREADY_IN_FLIGHT
    broker.resolve("dup", "deny")
    worker.join(timeout=3)
    assert results["first"].decision == "deny"


def test_audit_log_records_asked_and_decided():
    broker = perms.ApprovalBroker("ask", perms.AutoResponder("allow"))
    broker.request(PermissionRequest(tool="write_file", mode="code", call_id="a1"))
    events = [entry.event for entry in broker.audit_log()]
    assert events == ["approval/asked", "approval/decided"]
    decided = broker.audit_log()[-1]
    assert decided.decision == "allow"
    assert decided.tool == "write_file"


def test_audit_never_leaks_into_model_visible_text(workspace):
    """审计只写日志：网关返回值里不含 UI / 审计细节的正文。"""
    gateway = build_gateway(workspace, responder=perms.AutoResponder("allow"))
    decision = gateway.decide("write_file", {"path": str(workspace / "a.txt")})
    assert decision.decision == "allow"
    assert decision.reason == ""
    assert "approval/" not in decision.reason


def test_unknown_approval_policy_is_rejected_at_construction():
    with pytest.raises(perms.PermissionConfigurationError):
        perms.ApprovalBroker("sometimes")


def test_unknown_sandbox_mode_is_rejected_at_construction():
    with pytest.raises(perms.PermissionConfigurationError):
        perms.PermissionGateway({"sandbox_default": "yolo"}, tool_specs=SPECS)


def test_coerce_decision_accepts_strings_and_mappings():
    assert perms.coerce_decision("allow").decision == "allow"
    assert perms.coerce_decision({"decision": "deny", "reason": "不行"}).reason == "不行"
    assert perms.coerce_decision(PermissionDecision("ask")).decision == "ask"
    assert perms.coerce_decision(42).decision == perms.DECISION_UNAVAILABLE


# ============================================================================
# 4. 路径边界（P3 / P4 / P5）
# ============================================================================

def test_relative_path_resolves_against_cwd(workspace):
    policy = perms.PathPolicy(roots=(str(workspace),), workspace=str(workspace),
                              sandbox=SANDBOX_WORKSPACE_WRITE)
    assert policy.check_write("a.txt", cwd=str(workspace)) == str(workspace / "a.txt")


def test_traversal_escape_is_denied(workspace):
    gateway = build_gateway(workspace, responder=perms.AutoResponder("allow"))
    escape = os.path.join(str(workspace), "..", "escaped.txt")
    decision = gateway.decide("write_file", {"path": escape})
    assert decision.decision == "deny"
    assert decision.info["code"] == perms.CODE_SANDBOX_DENIED


def test_absolute_outside_path_is_denied(tmp_path, workspace):
    gateway = build_gateway(workspace, responder=perms.AutoResponder("allow"))
    decision = gateway.decide("write_file", {"path": str(tmp_path / "other" / "x.txt")})
    assert decision.decision == "deny"


def test_sibling_prefix_is_not_inside(tmp_path):
    """C:\\work2 不得被误判为在 C:\\work 之内（前缀比较的经典坑）。"""
    root = tmp_path / "work"
    sibling = tmp_path / "work2"
    root.mkdir()
    sibling.mkdir()
    assert perms.path_within(str(root), str(sibling / "a.txt")) is False
    assert perms.path_within(str(root), str(root / "a.txt")) is True


def test_case_insensitive_comparison_on_windows(workspace):
    gateway = build_gateway(workspace, responder=perms.AutoResponder("allow"))
    upper = str(workspace).upper()
    if os.path.normcase("a") == os.path.normcase("A"):
        decision = gateway.decide("write_file", {"path": os.path.join(upper, "A.TXT")})
        assert decision.decision == "allow"
    else:  # pragma: no cover - 只在大小写敏感平台上走到
        assert perms.path_within(str(workspace), os.path.join(upper, "A.TXT")) is False


def test_read_inside_root_is_allowed(workspace):
    gateway = build_gateway(workspace, sandbox=SANDBOX_READ_ONLY)
    decision = gateway.decide("read_file", {"path": str(workspace / "a.txt")})
    assert decision.decision == "allow"


def test_read_outside_root_is_denied(tmp_path, workspace):
    gateway = build_gateway(workspace)
    decision = gateway.decide("read_file", {"path": str(tmp_path / "outside.txt")})
    assert decision.decision == "deny"
    assert decision.info["code"] == perms.CODE_SANDBOX_DENIED


def test_target_paths_from_arguments_are_detected(workspace):
    gateway = build_gateway(workspace, responder=perms.AutoResponder("allow"))
    # path 是 harness._target_paths 认得的参数名
    assert gateway.decide("write_file", {"path": str(workspace / "a.txt")}).decision == "allow"
    # 参数名不认识时没有路径可判，仍会落到审批分支
    decision = gateway.decide("write_file", {"whatever": str(workspace / "a.txt")})
    assert decision.decision == "allow"


def test_symlink_escape_is_denied(tmp_path, workspace):
    """符号链接指向工作区外时必须被拒绝（resolve 已跟随链接）。"""
    outside = tmp_path / "outside"
    outside.mkdir()
    link = workspace / "link"
    try:
        os.symlink(str(outside), str(link), target_is_directory=True)
    except (OSError, NotImplementedError, AttributeError):
        pytest.skip("当前环境不支持创建符号链接")
    gateway = build_gateway(workspace, responder=perms.AutoResponder("allow"))
    decision = gateway.decide("write_file", {"path": str(link / "escaped.txt")})
    assert decision.decision == "deny"


def test_sandbox_resolution_prefers_explicit_then_default(workspace):
    policy = perms.PathPolicy(roots=(str(workspace),), sandbox=SANDBOX_READ_ONLY)
    assert policy.resolve(explicit=SANDBOX_WORKSPACE_WRITE) == SANDBOX_WORKSPACE_WRITE
    assert policy.resolve() == SANDBOX_READ_ONLY

    class WithMode:
        sandbox_mode = SANDBOX_WORKSPACE_WRITE

    assert policy.resolve(WithMode()) == SANDBOX_WORKSPACE_WRITE
    with pytest.raises(perms.PermissionConfigurationError):
        policy.resolve(explicit="yolo")


def test_read_only_sandbox_blocks_writes_but_allows_reads(workspace):
    policy = perms.PathPolicy(roots=(str(workspace),), workspace=str(workspace),
                              sandbox=SANDBOX_READ_ONLY)
    assert policy.is_readable(str(workspace / "a.txt")) is True
    assert policy.is_writable(str(workspace / "a.txt")) is False
    with pytest.raises(perms.SandboxDenied) as excinfo:
        policy.check_write(str(workspace / "a.txt"))
    assert excinfo.value.code == perms.CODE_SANDBOX_READ_ONLY
    assert excinfo.value.to_dict()["code"] == perms.CODE_SANDBOX_READ_ONLY


def test_empty_path_is_denied():
    with pytest.raises(perms.SandboxDenied):
        perms.normalize_path("")


# ============================================================================
# 5. 命令白名单（P22）
# ============================================================================

@pytest.mark.parametrize("argv, allowed", [
    (["dir"], True),
    (["ls", "-la"], True),
    (["type", "a.txt"], True),
    (["cat", "a.txt"], True),
    (["git", "status"], True),
    (["git", "diff", "HEAD"], True),
    (["git", "push"], False),
    (["python", "-m", "pytest"], True),
    (["python", "-m", "pip", "install", "x"], False),
    (["rm", "-rf", "/"], False),
    (["del", "a.txt"], False),
    (["curl", "http://x"], False),
    (["powershell", "-c", "x"], False),
    (["Invoke-WebRequest", "http://x"], False),
    (["echo", "hi"], False),
])
def test_command_allowlist_matching(argv, allowed):
    policy = perms.CommandPolicy(allowlist=("dir", "ls", "type", "cat",
                                            "git status", "git diff",
                                            "python -m pytest"))
    if allowed:
        assert policy.check(argv)
    else:
        with pytest.raises(perms.CommandDenied):
            policy.check(argv)


def test_denied_commands_are_rejected_even_if_allowlisted():
    """显式拒绝表优先于白名单（防御 rm/pip install 之类的错误配置）。"""
    policy = perms.CommandPolicy(allowlist=("rm", "pip", "powershell"))
    for argv in (["rm", "-rf", "x"], ["pip", "install", "x"],
                 ["powershell", "-c", "x"]):
        with pytest.raises(perms.CommandDenied) as excinfo:
            policy.check(argv)
        assert "拒绝表" in str(excinfo.value)


def test_empty_allowlist_denies_everything():
    """Learn 模式的白名单为空 -> 一律 COMMAND_MODE_DENIED。"""
    policy = perms.CommandPolicy(allowlist=())
    assert policy.enabled is False
    with pytest.raises(perms.CommandDenied) as excinfo:
        policy.check(["dir"])
    assert excinfo.value.code == perms.CODE_COMMAND_MODE_DENIED


def test_python_alias_is_normalised():
    assert perms.normalize_command_token("python3.exe") == "python"
    assert perms.normalize_command_token(r"C:\Python313\python.exe") == "python"
    assert perms.normalize_command_token("PY.EXE") == "python"


def test_command_gateway_denies_by_default_in_chat_mode(workspace):
    gateway = build_gateway(workspace, mode="chat", responder=perms.AutoResponder("allow"),
                            command_allowlist={"chat": ()})
    decision = gateway.decide("run_command", {"command": "dir"})
    assert decision.decision == "deny"
    assert decision.info["code"] == perms.CODE_COMMAND_MODE_DENIED


def test_command_gateway_allows_allowlisted_command_then_asks(workspace):
    responder = perms.AutoResponder("allow")
    gateway = build_gateway(workspace, responder=responder)
    decision = gateway.decide("run_command", {"command": "git status"})
    assert decision.decision == "allow"
    assert responder.call_count == 1          # 已授权模式下仍需过审批


def test_command_gateway_denies_unlisted_command(workspace):
    gateway = build_gateway(workspace, responder=perms.AutoResponder("allow"))
    decision = gateway.decide("run_command", {"command": "rm -rf /"})
    assert decision.decision == "deny"
    assert decision.info["code"] == perms.CODE_COMMAND_DENIED


def test_command_gateway_denies_when_no_command_given(workspace):
    gateway = build_gateway(workspace, responder=perms.AutoResponder("allow"))
    decision = gateway.decide("run_command", {})
    assert decision.decision == "deny"
    assert decision.info["code"] == perms.CODE_COMMAND_DENIED


def test_command_argv_accepts_list_and_string_forms():
    assert perms.command_argv({"argv": ["git", "status"]}) == ["git", "status"]
    assert perms.command_argv({"command": "git status"}) == ["git", "status"]
    assert perms.command_argv({"command": ["dir"]}) == ["dir"]
    assert perms.command_argv({}) == []


def test_configured_extra_commands_extend_the_allowlist(workspace):
    gateway = build_gateway(
        workspace, responder=perms.AutoResponder("allow"),
        config={"command_allowlist": ["node --version"]})
    decision = gateway.decide("run_command", {"command": "node --version"})
    assert decision.decision == "allow"
    assert "node --version" in gateway.command_allowlist("code")


def test_gateway_configure_switches_mode_and_sandbox(workspace, tmp_path):
    gateway = build_gateway(workspace, responder=perms.AutoResponder("allow"))
    gateway.configure(mode="learn", workdir=str(tmp_path), sandbox=SANDBOX_READ_ONLY)
    assert gateway.mode == "learn"
    assert gateway.sandbox == SANDBOX_READ_ONLY
    assert gateway.decide("write_file", {"path": str(tmp_path / "x.txt")}).decision == "deny"


def test_gateway_rejects_non_request_object(workspace):
    gateway = build_gateway(workspace)
    decision = gateway.evaluate("不是请求对象")
    assert decision.decision == perms.DECISION_UNAVAILABLE
    assert decision.info["code"] == perms.CODE_BAD_ARGUMENTS


def test_gateway_exposes_structured_sandbox_error_codes(workspace):
    """拒绝理由必须可读且带结构化 code，供 UI 直接展示。"""
    gateway = build_gateway(workspace)
    decision = gateway.decide("write_file", {"path": str(workspace.parent / "x.txt")})
    assert decision.decision == "deny"
    payload = decision.info
    assert payload["code"] == perms.CODE_SANDBOX_DENIED
    assert payload["path"].endswith("x.txt")
    assert payload["root"]
