"""ui.py 四模式界面（模式切换 / 会话隔离 / Code-Learn 面板）测试

设计原则：

1. **本文件不 import ai**：界面只从 bot 上读取少量接口元数据
   （available_modes / set_mode / bind_mode_workdir / get_history / sessions），
   注入一个 FakeBot 就能把界面逻辑完整跑通。这样既快，又与 ai.py 的改造解耦
   （那些接口的契约由 tests/test_modes.py 与 tests/test_backward_compat.py 守着）。
2. 断言「界面可观察状态」：模式条选中项、消息区行数、面板文案、写回的配置，
   而不是内部实现细节。
3. 全部用例跑在 Qt 的 offscreen 平台上：无需显示器、不联网、不写仓库文件
   （设置文件由环境变量 CASTORICE_UI_SETTINGS 指向 tmp_path）。
"""

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import ui  # noqa: E402

MODES = ("chat", "work", "code", "learn")


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
    """把 ui_settings.json 指到临时目录，绝不写进仓库根目录"""
    path = tmp_path / "ui_settings.json"
    monkeypatch.setenv(ui.UiSettings.ENV_PATH, str(path))
    return path


class FakeConfigManager:
    """最小配置管理器：只实现界面真正用到的那几个读法。

    set_values 会把改动写回文件，因此「选择写入配置 → 重启后恢复」
    这条链路真的跨了进程边界（新窗口重新从文件读）。
    """

    provider_names = ["fake"]

    def __init__(self, path: Path, mode: str = "chat") -> None:
        self.path = Path(path)
        self._values = {"mode": mode, "providers": {"fake": {"model": "fake"}}}
        self.writes = []
        self._write(mode)

    def _write(self, mode: str) -> None:
        self.path.write_text('mode: "' + mode + '"\n', encoding="utf-8")

    def get(self, key, default=None):
        node = self._values
        for part in str(key).split("."):
            if not isinstance(node, dict):
                return default
            node = node.get(part)
            if node is None:
                return default
        return node

    def get_provider_config(self, name: str):
        return {"api_key": "fake", "base_url": "http://localhost", "model": "fake"}

    def set_values(self, updates) -> None:
        self.writes.append(dict(updates))
        for path, value in updates.items():
            node = self._values
            for part in path[:-1]:
                node = node.setdefault(part, {})
            node[path[-1]] = value
            if path == ("mode",):
                self._write(str(value))


class FakeSession:
    """对齐 castorice.SessionStore 的最小写入方（只看关闭语义）"""

    def __init__(self, mode: str, path: Path) -> None:
        self.mode = mode
        self.path = Path(path)
        self.events = []
        self.dirty = False
        self.closed = False
        self.flushes = 0

    @property
    def session_id(self) -> str:
        return self.path.stem

    def append(self, kind: str, payload=None, **fields) -> None:
        self.events.append((kind, dict(payload or {})))
        self.dirty = True

    def flush(self, reason: str = "") -> None:
        self.flushes += 1
        lines = [str(kind) + ":" + str(payload) for kind, payload in self.events]
        self.path.write_text("\n".join(lines), encoding="utf-8")
        self.dirty = False

    def close(self) -> None:
        self.closed = True


class FakeBot:
    """实现 ai.AI 上面向界面的那一小撮接口（字段名与真实实现一致）"""

    def __init__(self, mode: str = "chat", root: Path = Path(".")) -> None:
        self.root = Path(root)
        self._mode = ui.normalize_mode_name(mode)
        self.config_manager = FakeConfigManager(self.root / "config.yaml", self._mode)
        # 对齐 ai.AI 的语义：_sessions 是「模式 -> 会话」的缓存，
        # session 是当前模式的会话；缓存里被摘掉的会话会在下次激活时重新打开。
        self._sessions = {}
        self.created_sessions = []
        self._current_session = None
        for name in MODES:
            self._sessions[name] = self._open_session(name)
        self._current_session = self._sessions[self._mode]
        self._history = {
            "chat": [{"role": "user", "content": "聊天模式的旧消息"}],
            "work": [{"role": "user", "content": "工作模式的旧消息"}],
            "code": [{"role": "user", "content": "编程模式的旧消息"}],
            "learn": [{"role": "user", "content": "学习模式的旧消息"}],
        }
        self._workdirs = {}
        self._bind_error = None
        self.set_mode_calls = []
        self.bind_calls = []

    # ---- 模式 ----

    @property
    def mode(self) -> str:
        return self._mode

    def available_modes(self):
        visible = {
            "chat": [],
            "work": ["get_current_time", "web_search"],
            "code": ["read_file", "write_file", "run_command"],
            "learn": ["search_notes", "read_note"],
        }
        return [
            {
                "name": item["name"],
                "display_name": item["display_name"],
                "tool_allowlist": visible[item["name"]],
                "visible_tools": visible[item["name"]],
                "system_prompt_sections": [] if item["name"] == "chat" else [item["name"]],
                "workdir_policy": {
                    "bindable": item["name"] != "chat",
                    "default_sandbox": (
                        "read-only" if item["name"] == "chat" else "workspace-write"
                    ),
                    "command_allowlist": [],
                },
                "workdir": self._workdirs.get(item["name"]) or None,
                "sandbox": "read-only" if item["name"] == "chat" else "workspace-write",
                "bindable": item["name"] != "chat",
                "supports_vision_input": True,
                "active": item["name"] == self._mode,
            }
            for item in ui.FALLBACK_MODES
        ]

    def _open_session(self, name: str) -> FakeSession:
        """打开（必要时新建）某模式的会话——对应内核的 _build_session"""
        store = self._sessions.get(name)
        if store is None or store.closed:
            store = FakeSession(name, self.root / "sessions" / (name + ".jsonl"))
            self._sessions[name] = store
            self.created_sessions.append(store)
        return store

    def set_mode(self, mode: str, *, persist: bool = True) -> None:
        name = ui.normalize_mode_name(mode)
        self.set_mode_calls.append((name, persist))
        self._mode = name
        self._current_session = self._open_session(name)
        if persist:
            self.config_manager.set_values({("mode",): name})

    def bind_mode_workdir(self, mode: str, path=None) -> str:
        name = ui.normalize_mode_name(mode)
        self.bind_calls.append((name, path))
        if self._bind_error is not None:
            raise self._bind_error
        if name == "chat":
            raise ValueError("模式 chat 不支持绑定工作目录")
        if path in (None, ""):
            self._workdirs.pop(name, None)
            return ""
        normalized = os.path.normpath(str(Path(str(path)).expanduser()))
        self._workdirs[name] = normalized
        return normalized

    def workdir(self, mode=None):
        return self._workdirs.get(ui.normalize_mode_name(mode or self._mode))

    # ---- 会话 ----

    @property
    def session(self):
        return self._current_session

    @property
    def sessions(self):
        return dict(self._sessions)

    def get_history(self):
        return list(self._history.get(self._mode, []))

    # ---- 其它（设置窗口与图片按钮要用） ----

    @property
    def provider_name(self) -> str:
        return "fake"

    def supports_vision(self, provider_name=None) -> bool:
        return True

    def change_provider(self, *args, **kwargs) -> None:
        return None

    def get_response_stream(self, text: str, images=None):
        yield ('text', "echo:" + text)
        yield ('done', "echo:" + text)


@pytest.fixture
def window(qapp, tmp_path):
    """建一个挂上 FakeBot 的 ChatWindow，用完自动关闭（释放写入方）"""
    created = []

    def factory(mode: str = "chat", **kwargs):
        bot = FakeBot(mode=mode, root=tmp_path)
        for key, value in kwargs.items():
            setattr(bot, key, value)
        win = ui.ChatWindow(bot=bot, ai_avatar_path=None, emoji_dir=tmp_path / "emoji")
        win.show()
        qapp.processEvents()
        win._test_bot = bot
        created.append(win)
        return win

    yield factory

    for win in created:
        win.close()
        win.deleteLater()
    qapp.processEvents()


def bubbles_in(win):
    """消息区里所有气泡的纯文本（按显示顺序）"""
    texts = []
    layout = win._message_layout
    for index in range(layout.count()):
        item = layout.itemAt(index)
        container = item.widget() if item is not None else None
        if container is None:
            continue
        for child in container.children():
            if isinstance(child, ui.ChatBubble) and child._label is not None:
                texts.append(child._label.text())
    return texts


def label_texts(win):
    """消息区里所有 QLabel 的文字（含工具提示行与提示条）"""
    texts = []
    layout = win._message_layout
    for index in range(layout.count()):
        item = layout.itemAt(index)
        widget = item.widget() if item is not None else None
        if isinstance(widget, ui.QLabel):
            texts.append(widget.text())
            continue
        if widget is None:
            continue
        for child in widget.findChildren(ui.QLabel):
            texts.append(child.text())
    return texts
# ----------------------------------------------------------------------------
# 1. 模式切换条与持久化
# ----------------------------------------------------------------------------

def test_mode_bar_shows_four_modes_in_order(window):
    win = window()
    assert win._mode_bar is not None
    assert win._mode_bar.mode_names() == list(MODES)
    labels = [win._mode_bar._buttons[name].text() for name in MODES]
    assert labels == ["聊天", "工作", "编程", "学习"]


def test_mode_bar_marks_active_mode(window):
    win = window()
    assert win._mode_bar.active_mode() == "chat"
    assert win._mode_bar._buttons["chat"].is_active()
    assert not win._mode_bar._buttons["code"].is_active()
    win.switch_mode("code")
    assert win._mode_bar.active_mode() == "code"
    assert win._mode_bar._buttons["code"].is_active()
    assert not win._mode_bar._buttons["chat"].is_active()


def test_clicking_mode_button_switches_mode(window):
    win = window()
    win._mode_bar._buttons["learn"].click()
    assert win.current_mode() == "learn"
    assert win._test_bot.mode == "learn"


def test_switch_mode_persists_into_config(window):
    win = window()
    win.switch_mode("code")
    # 内核收到了切换，并且要求落盘
    assert ("code", True) in win._test_bot.set_mode_calls
    # 配置管理器写的是 mode 键；文件里也是 code
    writes = win._test_bot.config_manager.writes
    assert {("mode",): "code"} in writes
    content = (win._test_bot.config_manager.path).read_text(encoding="utf-8")
    assert 'mode: "code"' in content


def test_switch_mode_remembers_locally_even_without_kernel(window):
    win = window()
    win.switch_mode("learn")
    # ui_settings.json 是内核不可用时的兜底记忆
    assert ui.ModeSettings.load() == "learn"


def test_mode_restored_after_restart(window, tmp_path):
    win = window()
    win.switch_mode("work")
    win.close()
    # 「重启」：换一个读同一份配置的 bot 重新建窗口
    bot = FakeBot(mode="work", root=tmp_path)
    again = ui.ChatWindow(bot=bot, ai_avatar_path=None, emoji_dir=tmp_path / "emoji")
    try:
        assert again.current_mode() == "work"
        assert again._mode_bar.active_mode() == "work"
    finally:
        again.close()


def test_startup_aligns_kernel_with_saved_ui_mode(qapp, tmp_path):
    """
    config.yaml 里可能没有 mode 键（update_yaml_scalars 不创建顶层新键），
    此时内核停在 chat，而用户上次的选择只存在于 ui_settings.json。
    启动必须把内核拉齐，否则界面显示「学习」而内核还在 chat。
    """
    ui.ModeSettings.save("learn")
    bot = FakeBot(mode="chat", root=tmp_path)
    # 模拟「旧 config.yaml 还没有 mode 键」：只留界面里的记忆
    bot.config_manager._values.pop("mode", None)
    win = ui.ChatWindow(bot=bot, ai_avatar_path=None, emoji_dir=tmp_path / "emoji")
    try:
        assert win.current_mode() == "learn"
        assert bot.mode == "learn", "内核必须被拉到界面记录的模式"
        assert ("learn", True) in bot.set_mode_calls
    finally:
        win.close()


def test_startup_does_not_switch_when_already_aligned(qapp, tmp_path):
    ui.ModeSettings.save("code")
    bot = FakeBot(mode="code", root=tmp_path)
    win = ui.ChatWindow(bot=bot, ai_avatar_path=None, emoji_dir=tmp_path / "emoji")
    try:
        assert win.current_mode() == "code"
        assert bot.set_mode_calls == [], "已经一致就不该再切一次"
    finally:
        win.close()


def test_startup_without_kernel_mode_property(qapp, tmp_path):
    """演示用 bot 没有 mode 属性时，界面照常工作"""
    bot = ui.MockBot()
    win = ui.ChatWindow(bot=bot, ai_avatar_path=None, emoji_dir=tmp_path / "emoji")
    try:
        assert win.current_mode() == ui.ModeSettings.DEFAULT
        assert win._mode_bar.mode_names() == list(MODES)
    finally:
        win.close()


def test_unknown_mode_falls_back_to_chat(window):
    win = window(mode="code")
    assert win.switch_mode("不存在的模式") is True
    assert win.current_mode() == "chat"
    assert win._test_bot.mode == "chat"
    assert ui.normalize_mode_name("CHAT") == "chat"
    assert ui.normalize_mode_name("") == "chat"
    assert ui.normalize_mode_name(None) == "chat"


def test_kernel_metadata_drives_mode_bar_and_panel(window):
    win = window()
    # 模式条与面板都读内核给的元数据，而不是硬编码的四项
    infos = win._test_bot.available_modes()
    assert [item["name"] for item in infos] == list(MODES)
    win.switch_mode("learn")
    assert win.mode_display_name() == "学习"
    assert win.visible_tools() == ["search_notes", "read_note"]


def test_mode_bar_survives_without_kernel_metadata(window):
    """内核接口抛异常时界面仍可切换（失败安全，不炸界面）"""
    win = window()
    bot = win._test_bot
    original = bot.available_modes

    def broken():
        raise RuntimeError("内核不可用")

    bot.available_modes = broken
    try:
        win.refresh_modes()
        assert win._mode_bar.mode_names() == list(MODES)
        assert win.switch_mode("code") is True
        assert win.current_mode() == "code"
    finally:
        bot.available_modes = original


# ----------------------------------------------------------------------------
# 2. 会话隔离：清空消息区 + 加载该模式自己的历史
# ----------------------------------------------------------------------------

def test_switch_clears_message_area(window):
    win = window()
    win.add_message("第一句", is_user=True)
    win.add_message("第二句", is_user=False)
    assert win._message_layout.count() > 0
    win.switch_mode("code")
    # 切换后先清空，再按新模式的（空）历史重建，因此没有残留
    assert "第一句" not in bubbles_in(win)
    assert "第二句" not in bubbles_in(win)


def test_switch_loads_that_mode_history(window):
    win = window()
    win.switch_mode("code")
    assert bubbles_in(win) == ["编程模式的旧消息"]
    win.switch_mode("learn")
    assert bubbles_in(win) == ["学习模式的旧消息"]
    win.switch_mode("chat")
    assert bubbles_in(win) == ["聊天模式的旧消息"]


def test_histories_do_not_leak_between_modes(window):
    win = window()
    win.switch_mode("work")
    win.add_message("只在工作模式里说的话", is_user=True)
    win._test_bot._history["work"].append(
        {"role": "user", "content": "只在工作模式里说的话"}
    )
    win.switch_mode("chat")
    assert "只在工作模式里说的话" not in bubbles_in(win)
    win.switch_mode("work")
    assert "只在工作模式里说的话" in bubbles_in(win)
    win.switch_mode("learn")
    assert "只在工作模式里说的话" not in bubbles_in(win)


def test_switch_closes_previous_session(window):
    """单写者约定：切换模式前必须 flush + close 旧会话，并摘出内核缓存"""
    win = window()
    chat_session = win._test_bot.sessions["chat"]
    chat_session.append("user", {"content": "会话里的事件"})
    win.switch_mode("code")
    assert chat_session.closed is True
    assert chat_session.flushes == 1, "关闭前必须先把未落盘的事件 flush 掉"
    assert "chat" not in win._test_bot._sessions, "已关闭的会话必须从缓存里摘除"


def test_switching_to_same_mode_is_noop(window):
    win = window()
    session = win._test_bot.sessions["chat"]
    assert win.switch_mode("chat") is False
    assert session.closed is False


def test_switch_is_blocked_while_replying(window):
    """AI 回复进行中不允许切模式（工作线程正在写旧会话）"""

    class RunningWorker:
        def isRunning(self):
            return True

    win = window()
    win._worker = RunningWorker()
    assert win.switch_mode("code") is False
    assert win.current_mode() == "chat"
    win._worker = None


def test_repeated_switching_keeps_single_writer_bookkeeping(window):
    win = window()
    for name in ["code", "learn", "work", "chat"] * 2:
        assert win.switch_mode(name) is True
        assert win.current_mode() == name
    # 每离开一个模式，它的会话都必须被释放，否则再切回来会撞上 SessionInUseError
    for name, session in win._test_bot._sessions.items():
        assert session.closed is (name != win.current_mode())


def test_history_replay_skips_system_messages(window):
    win = window()
    win._test_bot._history["code"] = [
        {"role": "system", "content": "系统提示词不该出现在气泡里"},
        {"role": "user", "content": "用户的话"},
        {"role": "assistant", "content": "AI 的话"},
    ]
    win.switch_mode("code")
    texts = bubbles_in(win)
    assert "系统提示词不该出现在气泡里" not in texts
    assert texts == ["用户的话", "AI 的话"]


def test_history_replay_flattens_multimodal_content(window):
    win = window()
    win._test_bot._history["code"] = [{
        "role": "user",
        "content": [
            {"type": "text", "text": "看这张图"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA"}},
        ],
    }]
    win.switch_mode("code")
    assert bubbles_in(win) == ["看这张图 [图片 x 1]"]


def test_clear_messages_removes_every_row(window):
    win = window()
    win.add_message("一", is_user=True)
    win.add_message("二", is_user=False)
    win._clear_messages()
    assert win._message_layout.count() == 0


# ----------------------------------------------------------------------------
# 3. Chat 模式不显示工具调用提示行与目录绑定入口
# ----------------------------------------------------------------------------

def test_chat_mode_hides_side_panel(window):
    win = window()
    assert win.current_mode() == "chat"
    assert win.mode_bindable() is False
    assert win._side_panel.isVisible() is False
    assert win._side_panel._bind_button.isVisible() is False


def test_chat_mode_does_not_show_tool_hint_rows(window):
    win = window()
    assert win.tools_visible() is False
    assert win.visible_tools() == []
    win._on_tool_event("web_search", "start")
    assert win._tool_labels == {}
    assert not any("正在调用" in text for text in label_texts(win))


def test_chat_mode_clears_stale_tool_labels_on_switch(window):
    win = window()
    win.switch_mode("work")
    win._on_tool_event("web_search", "start")
    assert win._tool_labels != {}
    win.switch_mode("chat")
    assert win._tool_labels == {}
    assert not any("正在调用" in text for text in label_texts(win))


def test_non_chat_modes_do_show_tool_hint_rows(window):
    win = window()
    win.switch_mode("work")
    assert win.tools_visible() is True
    win._on_tool_event("web_search", "start")
    assert "web_search" in win._tool_labels
    assert any("正在调用 web_search" in text for text in label_texts(win))
    win._on_tool_event("web_search", "done")
    assert win._tool_labels == {}


# ----------------------------------------------------------------------------
# 4. Code / Learn 的目录绑定与右侧面板
# ----------------------------------------------------------------------------

def test_panel_visible_and_bindable_in_code_mode(window):
    win = window()
    win.switch_mode("code")
    assert win._side_panel.isVisible() is True
    assert win.mode_bindable() is True
    assert win._side_panel._bind_button.isVisible() is True


def test_unbound_panel_gives_readable_hint(window):
    win = window()
    for mode, keyword in [("code", "代码目录"), ("learn", "笔记库"), ("work", "目录")]:
        win.switch_mode(mode)
        text = win._side_panel.workdir_text()
        assert keyword in text, text
        assert "未绑定" in text, text
    assert win.mode_workdir("code") is None


def test_bind_workdir_shows_path_on_panel(window, tmp_path):
    win = window()
    win.switch_mode("code")
    target = tmp_path / "code_root"
    target.mkdir()
    assert win.bind_workdir("code", str(target)) is True
    assert win.mode_workdir("code") == os.path.normpath(str(target))
    assert "已绑定" in win._side_panel.workdir_text()
    assert str(target) in win._side_panel.workdir_text()
    assert win._side_panel._unbind_button.isVisible() is True


def test_unbind_workdir_restores_hint(window, tmp_path):
    win = window()
    win.switch_mode("code")
    win.bind_workdir("code", str(tmp_path))
    assert win.bind_workdir("code", None) is True
    assert win.mode_workdir("code") is None
    assert "未绑定" in win._side_panel.workdir_text()


def test_bind_rejection_is_reported_on_panel(window, tmp_path):
    """越界目录被内核拒绝：界面不能静默，必须把原因说出来"""
    win = window()
    win.switch_mode("code")

    class WorkdirError(Exception):
        def to_dict(self):
            return {"code": "WORKDIR_OUT_OF_ROOT", "message": "拒绝绑定越界路径：/nope"}

    win._test_bot._bind_error = WorkdirError("越界")
    assert win.bind_workdir("code", str(tmp_path / "outside")) is False
    assert "拒绝绑定越界路径" in win._side_panel.hint_text()
    assert win.mode_workdir("code") is None


def test_bind_calls_kernel_with_mode_and_path(window, tmp_path):
    win = window()
    win.switch_mode("learn")
    win.bind_workdir("learn", str(tmp_path))
    assert win._test_bot.bind_calls[-1] == ("learn", str(tmp_path))


def test_panel_tracks_mode_when_switching(window, tmp_path):
    win = window(mode="chat", _workdirs={"code": str(tmp_path)})
    win.switch_mode("code")
    assert win.mode_workdir("code") == os.path.normpath(str(tmp_path))
    assert str(tmp_path) in win._side_panel.workdir_text()
    win.switch_mode("learn")
    assert "未绑定" in win._side_panel.workdir_text()
    win.switch_mode("code")
    assert str(tmp_path) in win._side_panel.workdir_text()


def test_panel_hidden_again_after_returning_to_chat(window, tmp_path):
    win = window(mode="chat", _workdirs={"code": str(tmp_path)})
    win.switch_mode("code")
    assert str(tmp_path) in win._side_panel.workdir_text()
    win.switch_mode("chat")
    assert win._side_panel.isVisible() is False


# ----------------------------------------------------------------------------
# 5. 既有功能不回归：主题、表情包、图片附件、打字机、淡入动画
# ----------------------------------------------------------------------------

def test_theme_colors_are_redefined_for_both_palettes():
    for name in [
        "MODE_BAR_BG", "MODE_TAB_BG_ACTIVE", "MODE_TAB_TEXT_ACTIVE",
        "MODE_TAB_TEXT", "MODE_TAB_TEXT_HOVER", "MODE_TAB_BG_HOVER",
        "PANEL_BG", "PANEL_BORDER", "PANEL_TITLE", "PANEL_TEXT",
        "PANEL_MUTED", "PANEL_KEY_BG",
    ]:
        assert name in ui.Colors._DARK, name
        assert name in ui.Colors._LIGHT, name


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_theme_switch_keeps_ui_working(window, theme):
    win = window()
    win.switch_mode("code")
    win._on_theme_selected(theme)
    assert ui.Colors.current() == theme
    assert win._mode_bar.mode_names() == list(MODES)
    assert win._side_panel.workdir_text() != ""
    win._on_theme_selected("system")
    assert ui.Colors.current() in ("light", "dark")


def test_emoji_dir_scan_is_untouched(tmp_path, qapp):
    image = tmp_path / "开心.png"
    QPixmap(8, 8).save(str(image))
    found = ui.scan_emoji_dir(tmp_path)
    assert [item["name"] for item in found] == ["开心"]


def test_emoji_tag_still_renders_after_mode_switch(window, tmp_path):
    win = window()
    image = tmp_path / "得意.png"
    QPixmap(8, 8).save(str(image))
    win._emoji_map["得意"] = str(image)
    win.switch_mode("code")
    win.add_message("[表情:得意] 附带表情的回复", is_user=False)
    # 文字照常显示，标记被替换成表情图（因此文字里不再出现标记）
    assert "附带表情的回复" in bubbles_in(win)
    assert not any("[表情:得意]" in text for text in bubbles_in(win))


def test_image_attachment_flow_is_untouched(window, tmp_path):
    win = window()
    image = tmp_path / "shot.png"
    QPixmap(8, 8).save(str(image))
    win._pending_images.append(str(image))
    win._refresh_attachments()
    assert len(win._attachment_items) == 1
    win._clear_attachments()
    assert win._pending_images == []
    assert win._attachment_items == []


def test_typewriter_still_queues_and_flushes(window):
    win = window()
    win._on_chunk_received("打字机")
    assert win._streaming_bubble is not None
    win._flush_typewriter()
    assert win._streaming_bubble._label is not None
    assert "打字机" in win._streaming_bubble._label.text()


def test_row_entrance_animation_still_applied(window):
    """新消息行仍要走 RowEntranceAnimator（淡入 + 由上向下展开）"""
    win = window()
    seen = []
    original = ui.RowEntranceAnimator

    class SpyAnimator(original):
        def __init__(self, container, duration=220):
            seen.append(container)
            super().__init__(container, duration)

    ui.RowEntranceAnimator = SpyAnimator
    try:
        win.add_message("新消息", is_user=False)
    finally:
        ui.RowEntranceAnimator = original
    assert seen, "新消息行仍要走淡入 + 展开动画"
    assert seen[-1].graphicsEffect() is not None, "入场动画仍给行容器挂透明度特效"


def test_mode_switch_does_not_disturb_image_button(window):
    win = window()
    assert win._image_button.isVisible() is True
    win.switch_mode("learn")
    assert win._image_button.isVisible() is True


def test_send_button_state_after_mode_switch(window):
    win = window()
    assert win._send_button.isEnabled() is False
    win._input_text.setPlainText("写点什么")
    win._on_input_changed()
    assert win._send_button.isEnabled() is True
    win.switch_mode("code")
    assert win._send_button.isEnabled() is True


# ----------------------------------------------------------------------------
# 6. 设置持久化的底层行为
# ----------------------------------------------------------------------------

def test_ui_settings_keeps_theme_and_mode_together(isolated_settings):
    ui.ThemeSettings.save("dark")
    ui.ModeSettings.save("code")
    data = json.loads(isolated_settings.read_text(encoding="utf-8"))
    assert data == {"theme": "dark", "ui_mode": "code"}
    assert ui.ThemeSettings.load() == "dark"
    assert ui.ModeSettings.load() == "code"


def test_ui_settings_writes_only_into_configured_path(isolated_settings):
    ui.ModeSettings.save("work")
    assert isolated_settings.exists()
    # 仓库根目录的 ui_settings.json 不该出现
    assert not (ui.app_root() / "ui_settings.json").exists()


def test_corrupted_settings_file_falls_back_to_defaults(isolated_settings):
    isolated_settings.write_text("{ 这不是 json", encoding="utf-8")
    assert ui.ThemeSettings.load() == ui.ThemeMode.SYSTEM
    assert ui.ModeSettings.load() == "chat"


def test_theme_settings_save_still_works_with_new_layout(isolated_settings):
    ui.ThemeSettings.save(ui.ThemeMode.SYSTEM)
    assert ui.ThemeSettings.load() == ui.ThemeMode.SYSTEM



