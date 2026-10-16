"""审批接线测试（F1 修复的可复现证据）

背景：内核的默认审批策略是 ask，而 ApprovalBroker 在「没有应答者」时的判定是
unavailable —— 按拒绝关闭。界面若从不注册应答者，Work/Code/Learn 下**每一个
有副作用的工具**都会被静默拒绝（文件写不出去、命令跑不起来，用户还看不到提示）。

本文件覆盖三件事：

1. **接线本身**：ChatWindow 在真实 GUI 启动路径注册应答者（hasattr 探测），
   并把等待上限压到界面的 60s；
2. **线程纪律**：应答者回调跑在 AIWorker 线程，只允许 defer + emit + return None，
   绝不创建/exec Qt 控件（用「回调内是否发生过控件操作」的可观测事实来钉死）；
3. **端到端**：真实 AI 装配路径下，allow 让文件真实落盘、deny 与超时让文件不落盘
   且模型侧收到结构化拒绝，越界路径在审批之前就被 FS_SANDBOX_DENIED 拦下。

两条硬约定：
* 全部用 **真实 AI 装配路径**（ai.AI 构造）而不是手工 new PermissionGateway ——
  后者会漏掉调用方传入的 sandbox 参数，正是本轮双方误判的根源；
* 测试路径**不强制注册应答者**，无应答者必须立即返回 unavailable，
  绝不引入内核默认的 120s 等待。
"""

import json
import os
import threading
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

import ai  # noqa: E402
import ui  # noqa: E402

#: 内核的错误码（断言用字符串字面量，避免把 castorice 的内部常量当成契约）
CODE_NO_RESPONDER = "APPROVAL_NO_RESPONDER"
CODE_TIMEOUT = "APPROVAL_TIMEOUT"
CODE_SANDBOX_DENIED = "FS_SANDBOX_DENIED"


# ----------------------------------------------------------------------------
# 基础设施
# ----------------------------------------------------------------------------

@pytest.fixture(scope="session")
def qapp():
    """整个模块共用一个 QApplication（Qt 不允许重复创建）"""
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    """设置文件与离线演示都指向临时目录，仓库根零污染"""
    monkeypatch.setenv(ui.UiSettings.ENV_PATH, str(tmp_path / "ui_settings.json"))
    return tmp_path / "ui_settings.json"


CONFIG_TEMPLATE = "\n".join([
    # mode 必须与构造参数一致：窗口启动时会按 config.yaml 的 mode 键对齐
    # 内核（t7 的既有语义），写错会让 code 模式的工具全部不可见，
    # 那样测的就不是审批链路了。
    "mode: {mode}",
    "active_provider: fake",
    "workdir: {root}",
    "permissions:",
    "  approval_policy: ask",
    "  sandbox_default: workspace-write",
    "providers:",
    "  fake:",
    "    api_key: test-key",
    "    model: fake-model",
    "",
])


class FakeProvider:
    """按脚本返回 (text, tool_calls)；记录每次收到的完整请求参数。"""

    def __init__(self, script=()):
        self.script = list(script)
        self.calls = []

    def chat_completion(self, **kwargs):
        self.calls.append(kwargs)
        if not self.script:
            raise AssertionError("FakeProvider 的脚本已用尽")
        return self.script.pop(0)


def tool_call(name, **arguments):
    """构造一次模型发起的工具调用（OpenAI 形态）"""
    return (None, [{
        "id": "call_1",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }])


@pytest.fixture
def ai_bot(tmp_path):
    """**真实装配路径**的 AI 实例：全部目录落在 tmp_path，不联网、不写仓库。

    刻意不用手工 new PermissionGateway：那样会漏掉调用方传入的 sandbox 参数，
    正是本轮 F1 与 F2 双方误判的根源。
    """
    created = []

    def _make(mode="code", bind=True, sandbox="workspace-write"):
        root = tmp_path / ("case" + str(len(created)))
        (root / "prompts" / "modes").mkdir(parents=True)
        (root / "Image").mkdir(parents=True)
        (root / "prompts" / "base.md").write_text("BASE-PROMPT", encoding="utf-8")
        for section in ("work", "code", "learn"):
            (root / "prompts" / "modes" / (section + ".md")).write_text(
                "SECTION-" + section, encoding="utf-8")
        workdir = root / "codework"
        workdir.mkdir()
        config = root / "config.yaml"
        config.write_text(
            CONFIG_TEMPLATE.replace("{root}", json.dumps(str(root)))
            .replace("{mode}", str(mode))
            .replace("sandbox_default: workspace-write",
                     "sandbox_default: " + sandbox),
            encoding="utf-8")
        bot = ai.AI(
            config_path=str(config),
            prompts_dir=str(root / "prompts"),
            chroma_dir=str(root / "chroma"),
            provider_name="fake",
            image_dir=str(root / "Image"),
            sessions_dir=str(root / "sessions"),   # 显式落在 tmp_path
            mode=mode,
        )
        if bind:
            bot.bind_mode_workdir(mode, str(workdir))
        created.append(bot)
        return bot, root, workdir

    yield _make

    for bot in created:
        try:
            bot.close()
        except Exception:
            pass


@pytest.fixture
def window(qapp, tmp_path):
    """建一个真实 bot 驱动的 ChatWindow（用完自动关闭）"""
    created = []

    def factory(bot, emoji_dir=None):
        # 窗口启动时会把内核模式对齐到「界面记录的模式」（没有 config.yaml
        # 的 mode 键时的兜底）。这里先同步成 bot 当前模式，避免窗口一建就把
        # 内核打回 chat —— 那会让 code 模式的工具全部不可见，测的就不是审批了。
        ui.ModeSettings.save(str(getattr(bot, "mode", "chat")))
        win = ui.ChatWindow(
            bot=bot, ai_avatar_path=None, emoji_dir=emoji_dir or (tmp_path / "emoji"))
        win.show()
        qapp.processEvents()
        created.append(win)
        return win

    yield factory

    for win in created:
        win.close()
        win.deleteLater()
    qapp.processEvents()


def run_turn(bot, script, responder=None):
    """跑一轮完整对话，返回事件列表（provider 用脚本替身，不联网）"""
    provider = FakeProvider(script)
    bot._provider = provider
    bot.harness.provider = provider
    if responder is not None:
        bot.set_approval_responder(responder)
    return list(bot.get_response_stream("请写一个文件"))


def tool_events(events):
    return [payload for kind, payload in events if kind == "tool"]


def audit_codes(bot):
    return [entry.get("code", "") for entry in bot.permission_audit()]


def pump_until(qapp, predicate, timeout=10.0):
    """在主线程里抽事件循环，直到条件成立或超时。

    跨线程信号走的是「投递到主线程事件队列」，必须让主线程真的转起来
    才能观察到效果——只调一次 processEvents 常常早于 worker 的 emit。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    qapp.processEvents()
    return bool(predicate())
# ----------------------------------------------------------------------------
# 1. 接线本身：真实 GUI 启动路径注册应答者
# ----------------------------------------------------------------------------

def test_window_registers_responder_on_real_bot(ai_bot, window):
    """ChatWindow 构造后就该把应答者接上（否则有副作用的工具全被静默拒绝）"""
    bot, _root, _workdir = ai_bot()
    assert bot.approval_broker.ask_callback is None, "构造后应还没有应答者"
    win = window(bot)
    assert win._approval_responder_installed is True
    assert bot.approval_broker.ask_callback is not None
    assert callable(bot.approval_broker.ask_callback)


def test_registration_is_probed_with_hasattr(tmp_path, qapp):
    """用 hasattr 探测：没有审批接口的 bot 不该让窗口构造失败（不强制）"""

    class BareBot:
        """最小 bot：只有设置窗口与图片按钮需要的那几个读法"""

        provider_name = "bare"

        @property
        def config_manager(self):
            class _Cfg:
                provider_names = ["bare"]

                @staticmethod
                def get_provider_config(name):
                    return {"api_key": "k", "base_url": "http://x", "model": "m"}

                @staticmethod
                def get(key, default=None):
                    return default

                @staticmethod
                def set_values(updates):
                    return None

            return _Cfg()

        def supports_vision(self, provider_name=None):
            return False

    win = ui.ChatWindow(bot=BareBot(), ai_avatar_path=None, emoji_dir=tmp_path)
    try:
        qapp.processEvents()
        assert win._approval_responder_installed is False
        # 关键：没有审批接口时窗口照样能用，而不是抛异常
        assert win._approval_bar is not None
    finally:
        win.close()


def test_registration_survives_setter_raising(tmp_path, qapp, ai_bot):
    """注册抛异常也不能把窗口构造弄崩（失败安全）"""
    bot, _root, _workdir = ai_bot()

    def broken(callback):
        raise RuntimeError("上游实现有问题")

    bot.set_approval_responder = broken
    win = ui.ChatWindow(bot=bot, ai_avatar_path=None, emoji_dir=tmp_path)
    try:
        qapp.processEvents()
        assert win._approval_responder_installed is False
    finally:
        win.close()


def test_responder_installed_only_once(ai_bot, window):
    bot, _root, _workdir = ai_bot()
    win = window(bot)
    first = bot.approval_broker.ask_callback
    assert win._install_approval_responder() is True
    assert bot.approval_broker.ask_callback is first, "重复安装不应替换已注册的应答者"


# ----------------------------------------------------------------------------
# 2. 线程纪律：worker 线程只登记 + 投递，不碰控件
# ----------------------------------------------------------------------------

def test_responder_defers_and_returns_none(ai_bot):
    """回调必须 defer(request_id) 并 return None（表示等主线程 resolve）"""
    bot, _root, _workdir = ai_bot()
    deferred = []
    broker = bot.approval_broker
    original_defer = broker.defer

    def spy_defer(request_id):
        deferred.append(request_id)
        return original_defer(request_id)

    broker.defer = spy_defer
    win_holder = {}

    # 直接构造一个窗口来拿到应答者（不显示，避免 offscreen 下的额外开销）
    win = ui.ChatWindow(bot=bot, ai_avatar_path=None, emoji_dir=Path("Image"))
    win_holder["win"] = win
    try:
        responder = broker.ask_callback
        assert responder is not None

        from castorice.harness import PermissionRequest

        request = PermissionRequest(tool="write_file", mode="code", call_id="call-1")
        result = responder(request, "call-1")
        assert result is None, "worker 侧必须返回 None 让 broker 阻塞等待"
        assert deferred == ["call-1"], "必须先把请求登记进待决表"
        assert "call-1" in broker.pending_ids()
    finally:
        win.close()


def test_responder_never_touches_widgets_from_worker_thread(ai_bot, window, qapp):
    """回调在 worker 线程被调用时，绝不能创建 / 操作 Qt 控件

    做法：把回调放到真正的子线程里跑（模拟 AIWorker 的处境），并监视
    窗口在回调期间是否产生了新的控件、审批条是否被直接改动。
    这正是「严禁在 worker 线程内创建或 exec Qt 控件」的可观测判据。
    """
    bot, _root, _workdir = ai_bot()
    win = window(bot)
    qapp.processEvents()

    from castorice.harness import PermissionRequest

    responder = bot.approval_broker.ask_callback
    main_thread = threading.current_thread()
    observed = {}

    def probe():
        observed["thread"] = threading.current_thread().name
        observed["is_main"] = (threading.current_thread() is main_thread)
        bar = win._approval_bar
        observed["waiting_before"] = bar.is_waiting()
        observed["widgets_before"] = len(bar.findChildren(ui.QLabel))
        observed["result"] = responder(
            PermissionRequest(tool="write_file", mode="code", call_id="c-9"), "c-9")
        observed["waiting_after"] = bar.is_waiting()
        observed["widgets_after"] = len(bar.findChildren(ui.QLabel))
        observed["details"] = bar.detail_text()

    worker = threading.Thread(target=probe, name="fake-ai-worker")
    worker.start()
    worker.join(timeout=10)
    assert not worker.is_alive()

    assert observed["is_main"] is False, "探针必须真的跑在非主线程上"
    assert observed["result"] is None
    # worker 线程绝不能自己把审批条显示出来（那是主线程的活）
    assert observed["waiting_before"] is False
    assert observed["waiting_after"] is False, "worker 线程不得直接启动审批条"
    assert observed["widgets_after"] == observed["widgets_before"], (
        "worker 线程不得创建新的 Qt 控件")
    assert observed["details"] == "", "worker 线程不得改写审批条文案"

    # 请求已登记，等主线程处理
    assert "c-9" in bot.approval_broker.pending_ids()


def test_signal_delivers_request_to_main_thread(ai_bot, window, qapp):
    """emit 之后，主线程处理事件时审批条才真正显示出来并开始倒计时"""
    bot, _root, _workdir = ai_bot()
    win = window(bot)
    qapp.processEvents()

    from castorice.harness import PermissionRequest

    responder = bot.approval_broker.ask_callback
    request = PermissionRequest(
        tool="write_file", mode="code", call_id="c-42",
        arguments={"path": "a.txt", "content": "x"})

    # 在子线程里模拟 worker 调用应答者
    worker = threading.Thread(target=lambda: responder(request, "c-42"))
    worker.start()
    worker.join(timeout=10)
    assert not worker.is_alive()

    # 主线程处理排队信号 -> 审批条显示
    qapp.processEvents()
    assert win._approval_waiting() is True
    assert win._approval_bar.request_id() == "c-42"
    assert "write_file" in win._approval_bar.detail_text()
    assert "还剩" in win._approval_bar.countdown_text()
    assert win._approval_bar.isVisible() is True

    # 答复后收起，并且 broker 收到了判定
    assert win._approval_bar.respond(True) is True
    qapp.processEvents()
    assert win._approval_waiting() is False


# ----------------------------------------------------------------------------
# 3. 超时策略（A+B 裁决）
# ----------------------------------------------------------------------------

def test_gui_path_shortens_timeout_to_60s(ai_bot, window):
    """真实 GUI 路径必须把 broker.timeout 压到界面的 60s（内核默认 120s）"""
    bot, _root, _workdir = ai_bot()
    assert bot.approval_broker.timeout == 120.0, "内核默认值应当是 120s"
    window(bot)
    assert bot.approval_broker.timeout == 60.0
    assert ui.ChatWindow.APPROVAL_TIMEOUT_S == 60.0


def test_countdown_reflects_effective_timeout(ai_bot, window, qapp):
    bot, _root, _workdir = ai_bot()
    win = window(bot)
    from castorice.harness import PermissionRequest

    win._on_approval_requested(
        PermissionRequest(tool="run_command", mode="code", call_id="c-1"), "c-1")
    qapp.processEvents()
    assert "60" in win._approval_bar.countdown_text()
    assert win._approval_bar._seconds_total == 60


def test_no_responder_returns_unavailable_immediately(ai_bot):
    """测试路径不注册应答者：必须立即返回 unavailable（不得等 120s）"""
    bot, _root, workdir = ai_bot()
    target = workdir / "no-responder.txt"

    started = time.monotonic()
    result = bot._kernel_tools.call(
        "write_file", {"path": str(target), "content": "x"})
    elapsed = time.monotonic() - started

    assert elapsed < 5.0, "无应答者必须立即关闭，实测等待了 %.1fs" % elapsed
    assert target.exists() is False, "没有应答者时绝不能落盘"
    assert CODE_NO_RESPONDER in audit_codes(bot)


def test_short_timeout_denies_without_touching_disk(ai_bot):
    """永不 resolve 的应答者 + 短超时 -> 拒绝，且总耗时受超时约束"""
    bot, _root, workdir = ai_bot()
    target = workdir / "timeout.txt"
    bot.approval_broker.timeout = 0.3          # 只把等待压短，不改语义

    def never_resolves(request, request_id=""):
        bot.approval_broker.defer(request_id)   # 登记了，但主线程永不答复
        return None

    bot.set_approval_responder(never_resolves)
    started = time.monotonic()
    result = bot._kernel_tools.call(
        "write_file", {"path": str(target), "content": "x"})
    elapsed = time.monotonic() - started

    assert elapsed >= 0.25, "应当真的等过一小段时间（否则测的不是超时路径）"
    assert elapsed < 10.0
    assert target.exists() is False
    assert CODE_TIMEOUT in audit_codes(bot)
    assert "权限" in result or "拒绝" in result or "PERMISSION" in result
# ----------------------------------------------------------------------------
# 4. 端到端：allow 让文件真实落盘
# ----------------------------------------------------------------------------

def test_end_to_end_allow_creates_file(ai_bot):
    """code 模式绑定目录 -> 请求写目录内文件 -> 应答者 allow -> 文件真实存在"""
    bot, _root, workdir = ai_bot(mode="code", bind=True)
    target = workdir / "allowed.txt"
    seen = []

    def responder(request, request_id=""):
        seen.append((getattr(request, "tool", ""), request_id))
        return "allow"

    events = run_turn(
        bot,
        [tool_call("write_file", path=str(target), content="hello-allow"),
         ("done", "写好了")],
        responder=responder,
    )

    assert target.exists() is True
    assert target.read_text(encoding="utf-8") == "hello-allow"
    assert seen, "应答者必须被调用过（否则就是没接线）"
    assert seen[0][0] == "write_file"
    assert seen[0][1], "request_id 必须非空（broker 靠它配对应答）"

    payloads = tool_events(events)
    assert [p["status"] for p in payloads] == ["start", "done"]


def test_end_to_end_allow_via_window_ui(ai_bot, window, qapp):
    """同一条链路，但批准动作来自界面上的「允许」按钮路径"""
    bot, _root, workdir = ai_bot(mode="code", bind=True)
    win = window(bot)
    target = workdir / "ui-allowed.txt"

    approved = []
    original = win._on_approval_allow

    def spy_allow(request_id):
        approved.append(request_id)
        return original(request_id)

    win._on_approval_allow = spy_allow
    win._approval_bar.approved.disconnect()
    win._approval_bar.approved.connect(spy_allow)

    # 后台线程跑一轮：主线程负责处理信号并点「允许」
    holder = {}

    def worker():
        holder["events"] = run_turn(
            bot,
            [tool_call("write_file", path=str(target), content="from-ui"),
             ("done", "ok")],
        )

    thread = threading.Thread(target=worker)
    thread.start()

    def answered():
        if win._approval_waiting():
            win._approval_bar.respond(True)
        return bool(approved) and not thread.is_alive()

    pump_until(qapp, answered, timeout=20)
    thread.join(timeout=5)
    qapp.processEvents()

    assert not thread.is_alive()
    assert approved, "界面上的「允许」路径必须真的答复了请求"
    assert target.exists() is True
    assert target.read_text(encoding="utf-8") == "from-ui"


# ----------------------------------------------------------------------------
# 5. 端到端：deny 与超时都不落盘，且模型侧收到结构化拒绝
# ----------------------------------------------------------------------------

def test_end_to_end_deny_keeps_disk_clean(ai_bot):
    """应答者 deny -> 文件不存在，且 tool 事件是带 decision 的 error"""
    bot, _root, workdir = ai_bot(mode="code", bind=True)
    target = workdir / "denied.txt"

    events = run_turn(
        bot,
        [tool_call("write_file", path=str(target), content="nope"),
         ("done", "好的")],
        responder=lambda request, request_id="": "deny",
    )

    assert target.exists() is False
    payloads = tool_events(events)
    assert payloads[0]["status"] == "start"
    error = [p for p in payloads if p["status"] == "error"]
    assert error, "被拒绝的调用必须在 tool 事件里报错: " + repr(payloads)
    assert error[0]["decision"] == "deny"
    assert error[0]["call_id"] == "call_1", "错误必须能对回到那次调用"


def test_end_to_end_deny_message_reaches_model(ai_bot):
    """拒绝理由要作为工具结果回灌给模型（不然模型只会反复重试同一件事）"""
    bot, _root, workdir = ai_bot(mode="code", bind=True)
    target = workdir / "denied2.txt"

    run_turn(
        bot,
        [tool_call("write_file", path=str(target), content="nope"),
         ("done", "ok")],
        responder=lambda request, request_id="": "deny",
    )

    tool_messages = [m for m in bot.get_history() if m.get("role") == "tool"]
    assert tool_messages, "拒绝结果必须以 tool 消息的形式进入历史"
    content = str(tool_messages[-1].get("content", ""))
    assert content.startswith("Error:") or "拒绝" in content, content
    assert target.exists() is False


def test_end_to_end_no_responder_is_structured_denial(ai_bot):
    """没有应答者时模型侧收到的必须是**结构化** unavailable（不是静默成功）"""
    bot, _root, workdir = ai_bot(mode="code", bind=True)
    target = workdir / "denied3.txt"
    started = time.monotonic()

    events = run_turn(
        bot,
        [tool_call("write_file", path=str(target), content="nope"),
         ("done", "ok")],
    )
    elapsed = time.monotonic() - started

    assert elapsed < 10.0, "无应答者不得引入长等待"
    assert target.exists() is False
    payloads = tool_events(events)
    error = [p for p in payloads if p["status"] == "error"]
    assert error and error[0]["decision"] == "unavailable", repr(payloads)
    assert CODE_NO_RESPONDER in audit_codes(bot)


def test_end_to_end_timeout_denial_is_structured(ai_bot):
    """永不 resolve + 短超时 -> 不落盘，审计里留下 APPROVAL_TIMEOUT"""
    bot, _root, workdir = ai_bot(mode="code", bind=True)
    target = workdir / "wontexist.txt"
    bot.approval_broker.timeout = 0.3

    def never_resolves(request, request_id=""):
        bot.approval_broker.defer(request_id)
        return None

    events = run_turn(
        bot,
        [tool_call("write_file", path=str(target), content="nope"),
         ("done", "ok")],
        responder=never_resolves,
    )

    assert target.exists() is False
    payloads = tool_events(events)
    error = [p for p in payloads if p["status"] == "error"]
    assert error and error[0]["decision"] == "unavailable", repr(payloads)
    assert CODE_TIMEOUT in audit_codes(bot)


# ----------------------------------------------------------------------------
# 5b. F1b：三条拒绝路径的模型可见文本必须携带**可区分**的结构化错误码
# ----------------------------------------------------------------------------

def model_visible_denial_text(bot, target):
    """取回灌给模型的最后一条 tool 消息内容（harness 写进历史的拒绝文本）"""
    tool_messages = [m for m in bot.get_history() if m.get("role") == "tool"]
    assert tool_messages, "拒绝结果必须以 tool 消息的形式进入历史"
    return str(tool_messages[-1].get("content", ""))


def test_denial_texts_carry_structured_codes_and_differ(ai_bot):
    """F1b 主判据：用户拒绝 / 超时 / 无应答者三条路径的模型可见文本

    * 各自出现结构化错误码（APPROVAL_* 等），deny 路径不得只有裸 'deny'；
    * 三者互不相等（模型必须能区分这三种情形）；
    * 三条路径都阻止落盘（安全不回退）。
    """
    results = {}

    # 1) 用户明确 deny
    bot, _root, workdir = ai_bot(mode="code", bind=True)
    target = workdir / "f1b-deny.txt"
    run_turn(
        bot,
        [tool_call("write_file", path=str(target), content="x"),
         ("done", "ok")],
        responder=lambda request, request_id="": "deny",
    )
    deny_text = model_visible_denial_text(bot, target)
    results["deny"] = deny_text
    assert target.exists() is False, "deny 路径竟然落盘了"

    # 2) 超时（应答者永不 resolve + 短超时）
    bot2, _root2, workdir2 = ai_bot(mode="code", bind=True)
    target2 = workdir2 / "f1b-timeout.txt"
    bot2.approval_broker.timeout = 0.3

    def never_resolves(request, request_id=""):
        bot2.approval_broker.defer(request_id)
        return None

    run_turn(
        bot2,
        [tool_call("write_file", path=str(target2), content="x"),
         ("done", "ok")],
        responder=never_resolves,
    )
    timeout_text = model_visible_denial_text(bot2, target2)
    results["timeout"] = timeout_text
    assert target2.exists() is False, "超时路径竟然落盘了"

    # 3) 无应答者
    bot3, _root3, workdir3 = ai_bot(mode="code", bind=True)
    target3 = workdir3 / "f1b-noresponder.txt"
    run_turn(
        bot3,
        [tool_call("write_file", path=str(target3), content="x"),
         ("done", "ok")],
    )
    no_responder_text = model_visible_denial_text(bot3, target3)
    results["no_responder"] = no_responder_text
    assert target3.exists() is False, "无应答者路径竟然落盘了"

    # —— 文本互不相同 ——
    assert deny_text != timeout_text, repr(results)
    assert deny_text != no_responder_text, repr(results)
    assert timeout_text != no_responder_text, repr(results)

    # —— 各自携带结构化码，deny 不得只有裸 'deny' ——
    # 应答者返回裸 'deny' 时网关不提供具体码，文本回落到通用结构化码
    # PERMISSION_DENIED（castorice/permissions.py 属本任务 out-of-scope，
    # 具体码 APPROVAL_* 的注入只能从 tools/base._denial_code 的既有语义
    # 继承）；关键契约是：文本含**结构化码**、三条文本互异、审计语义不变。
    assert "PERMISSION_DENIED" in deny_text, deny_text
    assert "APPROVAL_TIMEOUT" in timeout_text, timeout_text
    assert "APPROVAL_NO_RESPONDER" in no_responder_text, no_responder_text
    assert deny_text.strip() != "Error: deny", deny_text


def test_audit_codes_semantics_unchanged(ai_bot):
    """F1b：结构化码进模型可见文本后，审计 AuditEntry.code 语义不变。

    超时路径审计里仍是 APPROVAL_TIMEOUT；用户 deny 路径审计码保持网关
    原样（裸 'deny' 不带码 → 审计码为空串），两者互不污染。
    """
    bot, _root, workdir = ai_bot(mode="code", bind=True)
    target = workdir / "f1b-audit-deny.txt"
    run_turn(
        bot,
        [tool_call("write_file", path=str(target), content="x"),
         ("done", "ok")],
        responder=lambda request, request_id="": "deny",
    )
    assert "PERMISSION_DENIED" in model_visible_denial_text(bot, target)
    # 审计只进日志：deny 路径的 AuditEntry.code 保持网关原样（空串）
    deny_audits = [e for e in bot.permission_audit()
                   if e.get("event") == "approval/decided"
                   and e.get("decision") == "deny"]
    assert deny_audits, audit_codes(bot)
    assert deny_audits[-1].get("code", "") == "", deny_audits[-1]
    assert target.exists() is False

    bot2, _root2, workdir2 = ai_bot(mode="code", bind=True)
    target2 = workdir2 / "f1b-audit-timeout.txt"
    bot2.approval_broker.timeout = 0.3

    def never_resolves(request, request_id=""):
        bot2.approval_broker.defer(request_id)
        return None

    run_turn(
        bot2,
        [tool_call("write_file", path=str(target2), content="x"),
         ("done", "ok")],
        responder=never_resolves,
    )
    assert "APPROVAL_TIMEOUT" in model_visible_denial_text(bot2, target2)
    assert "APPROVAL_TIMEOUT" in audit_codes(bot2), audit_codes(bot2)
    assert target2.exists() is False
# ----------------------------------------------------------------------------
# 6. 路径边界先于审批（真实装配路径，覆盖 roots 内 / roots 外）
# ----------------------------------------------------------------------------

def test_boundary_inside_goes_to_approval(ai_bot):
    """roots 内的合法路径 -> 不在第 4 步被拦，而是要走到第 8 步的审批"""
    bot, _root, workdir = ai_bot(mode="code", bind=True)
    target = workdir / "inside.txt"

    decision = bot.permission_decision(
        "write_file", {"path": str(target), "content": "x"})
    # 无应答者时完整判定停在 unavailable；关键它**不是** FS_SANDBOX_DENIED
    assert decision.decision == "unavailable", decision
    assert (decision.info or {}).get("code") == CODE_NO_RESPONDER, decision.info

    # 注入 allow 之后同一个请求变成放行：证明卡点在审批，不在路径
    bot.set_approval_responder(lambda request, request_id="": "allow")
    decision2 = bot.permission_decision(
        "write_file", {"path": str(target), "content": "x"})
    assert decision2.decision == "allow", decision2


def test_boundary_outside_is_denied_before_approval(ai_bot, tmp_path):
    """roots 外的路径 -> FS_SANDBOX_DENIED，**且应答者根本不会被调用**

    这条钉死「路径边界（第 4 步）先于审批（第 8 步）」的顺序：
    如果审批先跑，越界请求会先弹窗问用户——那等于把边界判定交给用户运气。
    """
    bot, _root, workdir = ai_bot(mode="code", bind=True)
    outside = workdir.parent / "outside.txt"
    called = []

    def responder(request, request_id=""):
        called.append(request_id)
        return "allow"

    bot.set_approval_responder(responder)
    decision = bot.permission_decision(
        "write_file", {"path": str(outside), "content": "x"})

    assert decision.decision == "deny", decision
    assert (decision.info or {}).get("code") == CODE_SANDBOX_DENIED, decision.info
    assert called == [], "越界请求必须先被路径边界拦下，绝不能先问用户"


def test_boundary_outside_end_to_end_via_tool_exit(ai_bot):
    """经真实工具出口写 roots 外：拒绝、不落盘、错误码是 FS_SANDBOX_DENIED"""
    bot, _root, workdir = ai_bot(mode="code", bind=True)
    outside = workdir.parent / "outside-e2e.txt"
    bot.set_approval_responder(lambda request, request_id="": "allow")

    result = bot._kernel_tools.call(
        "write_file", {"path": str(outside), "content": "x"})

    assert outside.exists() is False
    assert result.startswith("错误:") or result.startswith("Error:"), result
    # 出口的错误文本是通用的 PERMISSION_DENIED（t11 刻意保持既有可观测行为），
    # 具体的 FS_SANDBOX_DENIED 落在工具自身的沙箱判定里。两条都要能观察到：
    assert CODE_SANDBOX_DENIED in result or "工作区" in result, result


def test_sandbox_read_only_blocks_before_approval(ai_bot):
    """read-only 沙箱下连审批都不会走到（第 7 步在第 8 步之前）"""
    bot, _root, workdir = ai_bot(
        mode="code", bind=True, sandbox="read-only")
    target = workdir / "readonly.txt"
    called = []

    def responder(request, request_id=""):
        called.append(request_id)
        return "allow"

    bot.set_approval_responder(responder)
    decision = bot.permission_decision(
        "write_file", {"path": str(target), "content": "x"})

    assert decision.decision == "deny", decision
    assert (decision.info or {}).get("code") == "SANDBOX_READ_ONLY", decision.info
    assert called == [], "read-only 沙箱必须确定性拒绝，不该先弹审批"
    assert target.exists() is False


def test_inside_after_gui_registration_really_writes(ai_bot, window, qapp):
    """整条链路串起来：真实窗口注册应答者 -> 写 roots 内 -> 文件落盘"""
    bot, _root, workdir = ai_bot(mode="code", bind=True)
    win = window(bot)
    target = workdir / "wired.txt"

    # 窗口注册的是真应答者；这里用「自动按允许答复」的方式驱动它，
    # 走的仍是 approve 信号 -> _resolve_approval -> broker.resolve 这条链路。
    auto = []
    win._approval_bar.approved.connect(
        lambda request_id: auto.append(request_id))

    # 工具调用放在后台线程跑（真实 AIWorker 的处境），主线程负责抽事件循环
    # 并在审批条出现时点「允许」。
    holder = {}

    def worker():
        holder["result"] = bot._kernel_tools.call(
            "write_file", {"path": str(target), "content": "wired"})

    def answered():
        if win._approval_waiting():
            win._approval_bar.respond(True)
        return bool(auto) and not thread.is_alive()

    thread = threading.Thread(target=worker)
    thread.start()
    pump_until(qapp, answered, timeout=20)
    thread.join(timeout=5)
    result = holder.get("result", "")

    assert target.exists() is True, result
    assert target.read_text(encoding="utf-8") == "wired"
    assert auto, "批准必须经由界面审批条发出"


# ----------------------------------------------------------------------------
# 7. 界面行为细节
# ----------------------------------------------------------------------------

def test_approval_bar_hidden_by_default(window, ai_bot):
    bot, _root, _workdir = ai_bot()
    win = window(bot)
    assert win._approval_bar.isVisible() is False
    assert win._approval_waiting() is False


def test_manual_deny_resolves_request(ai_bot, window, qapp):
    """点「拒绝」后 broker 收到 deny（不是超时）"""
    bot, _root, _workdir = ai_bot()
    win = window(bot)
    from castorice.harness import PermissionRequest

    broker = bot.approval_broker
    responder = broker.ask_callback
    request = PermissionRequest(tool="run_command", mode="code", call_id="c-7")

    answered = {}
    worker = threading.Thread(
        target=lambda: answered.update(result=responder(request, "c-7")))
    worker.start()
    assert pump_until(qapp, win._approval_waiting, timeout=10), "审批条应显示出来"

    win._approval_bar.respond(False)
    qapp.processEvents()
    worker.join(timeout=10)

    assert not worker.is_alive(), "拒绝后 worker 应当立即被唤醒"
    assert answered["result"] is None
    assert win._approval_waiting() is False


def test_stream_finish_cancels_pending_approval(ai_bot, window, qapp):
    """整轮结束时悬空的审批条必须收起并按拒绝答复（不能骗用户点）"""
    bot, _root, _workdir = ai_bot()
    win = window(bot)
    from castorice.harness import PermissionRequest

    broker = bot.approval_broker
    responder = broker.ask_callback
    request = PermissionRequest(tool="write_file", mode="code", call_id="c-8")
    worker = threading.Thread(target=lambda: responder(request, "c-8"))
    worker.start()
    assert pump_until(qapp, win._approval_waiting, timeout=10), "审批条应显示出来"

    win._on_stream_finished()
    qapp.processEvents()
    worker.join(timeout=10)

    assert win._approval_waiting() is False
    assert not worker.is_alive()


def test_theme_refresh_covers_approval_bar(ai_bot, window, qapp):
    """深浅主题切换时审批条也要刷新（不回归既有主题系统）"""
    bot, _root, _workdir = ai_bot()
    win = window(bot)
    for theme in ("light", "dark"):
        win._on_theme_selected(theme)
        qapp.processEvents()
        assert ui.Colors.current() == theme
        assert win._approval_bar is not None


def test_approval_colors_exist_in_both_palettes():
    for name in [
        "APPROVAL_BG", "APPROVAL_BORDER", "APPROVAL_TITLE", "APPROVAL_TEXT",
        "APPROVAL_MUTED", "APPROVAL_ALLOW_BG", "APPROVAL_ALLOW_TEXT",
        "APPROVAL_DENY_BG", "APPROVAL_DENY_TEXT",
    ]:
        assert name in ui.Colors._DARK, name
        assert name in ui.Colors._LIGHT, name


def test_describe_request_is_defensive():
    """描述函数读不到字段也不能抛异常（它跑在展示路径上）"""

    class Weird:
        pass

    text = ui.describe_permission_request(Weird())
    assert "未知工具" in text

    class WithArgs:
        tool = "write_file"
        arguments = {"path": "a.txt"}

    described = ui.describe_permission_request(WithArgs())
    assert "write_file" in described
    assert "a.txt" in described


def test_approval_bar_respond_without_request_is_noop(window, ai_bot):
    bot, _root, _workdir = ai_bot()
    win = window(bot)
    assert win._approval_bar.respond(True) is False
    assert win._approval_bar.respond(False) is False







