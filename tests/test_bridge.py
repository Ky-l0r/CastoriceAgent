"""Web 桥接层（castorice.bridge）测试。

全部走本地回环（127.0.0.1）真实 WebSocket 连接——离线夹具只拦截
非回环 socket.connect（夹具同 tests/test_channels_napcat.py），不需要
真实 Electron 壳，也不联网。
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from typing import Any, Dict, List, Optional

import pytest

from castorice.bridge import (
    WEB_BRIDGE_CAPABILITIES,
    BridgeServer,
    request_payload_dict,
)
from castorice.channels.base import ChannelError
from castorice.harness import PermissionRequest

LOOPBACK = "127.0.0.1"

# 模块导入期保存真实 connect（conftest 的 block_network 尚未打补丁）。
_REAL_CONNECT = socket.socket.connect


@pytest.fixture(autouse=True)
def _allow_loopback_connect(monkeypatch):
    """放行回环 connect（本文件的本地 WS 端点 + asyncio self-pipe）。"""
    original = _REAL_CONNECT

    def _guarded(self, address):
        host = address[0] if isinstance(address, tuple) else ""
        if isinstance(host, str) and host in ("127.0.0.1", "::1", "localhost"):
            return original(self, address)
        raise RuntimeError("测试期间禁止非回环连接")

    monkeypatch.setattr(socket.socket, "connect", _guarded)


class FakeBot:
    """鸭子类型 ai.AI 替身：固定事件流 + 记录调用。"""

    def __init__(
        self,
        events: Optional[List[tuple]] = None,
        error: Optional[Exception] = None,
        slow_event: Optional[float] = None,
    ) -> None:
        self.calls: List[str] = []
        self.images_seen: List[List[str]] = []
        self.events = events if events is not None else [
            ("text", "你好"),
            ("tool", {"name": "read_file", "status": "start"}),
            ("tool", {"name": "read_file", "status": "done"}),
            ("text", "，世界"),
            ("done", "你好，世界"),
        ]
        self.error = error
        self.slow_event = slow_event
        self.mode = "chat"
        self.mode_switched: List[str] = []
        self.memories_cleared = 0

    def get_response_stream(self, user_input: str, images=None):
        self.calls.append(user_input)
        self.images_seen.append(list(images or []))
        if self.error is not None:
            raise self.error
        for kind, payload in self.events:
            if self.slow_event is not None:
                time.sleep(self.slow_event)
            yield (kind, payload)

    def available_modes(self):
        return [
            {"name": "chat", "display_name": "Chat", "active": self.mode == "chat"},
            {"name": "work", "display_name": "Work", "active": self.mode == "work"},
        ]

    def set_mode(self, mode):
        self.mode = mode
        self.mode_switched.append(mode)

    def clear_memories(self):
        self.memories_cleared += 1


class ConfigStub:
    """ConfigManager 替身：dotted get + 注释保持写回接口。"""

    def __init__(self, data: Dict[str, Any]) -> None:
        self._data = data
        self.written: List[Dict[tuple, Any]] = []

    def get(self, key: str, default: Any = None) -> Any:
        value: Any = self._data
        for part in key.split("."):
            if not isinstance(value, dict) or part not in value:
                return default
            value = value[part]
        return value

    @property
    def config(self) -> Dict[str, Any]:
        return self._data

    def set_values(self, updates: Dict[tuple, Any]) -> None:
        self.written.append(dict(updates))
        for path, value in updates.items():
            node = self._data
            for part in path[:-1]:
                node = node.setdefault(part, {})
            node[path[-1]] = value


class MemoryStub:
    def search_memories(self, query, n_results=5):
        return ["[2025-01-01 08:00:00] 记忆A"] * min(n_results, 1)


class ApprovalBrokerStub:
    """模拟真实 ApprovalBroker 的一次性语义：resolve 后请求不再 pending。"""

    def __init__(self) -> None:
        self.resolved: List[tuple] = []
        self._pending: set = {"req-1"}

    def pending_ids(self):
        return tuple(self._pending)

    def resolve(self, request_id, decision):
        self.resolved.append((request_id, decision))
        if request_id in self._pending:
            self._pending.discard(request_id)
            return True
        return False


def make_bot(**kwargs: Any) -> FakeBot:
    bot = FakeBot(**kwargs)
    bot.config_manager = ConfigStub({
        "active_provider": "deepseek",
        "providers": {"deepseek": {"api_key": "sk-test", "model": "x"}},
    })
    bot.memory_manager = MemoryStub()
    bot.approval_broker = ApprovalBrokerStub()
    return bot


# ----------------------------------------------------------------------------
# 客户端辅助
# ----------------------------------------------------------------------------

class BridgeClient:
    """同步测试客户端（独立事件循环线程）。"""

    def __init__(self, port: int, token: str, do_handshake: bool = True) -> None:
        import websockets

        self._websockets = websockets
        self.port = port
        self.token = token
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        async def _connect():
            return await websockets.connect(
                "ws://" + LOOPBACK + ":" + str(port), open_timeout=5)

        self.ws = self._call(_connect())
        if do_handshake:
            self.send({"type": "hello", "token": token})
            self.recv(timeout=5)   # hello_ok

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def _call(self, coro, timeout: float = 5.0):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def send(self, frame: Dict[str, Any]) -> None:
        self._call(self.ws.send(json.dumps(frame, ensure_ascii=False)))

    def recv(self, timeout: float = 5.0) -> Dict[str, Any]:
        try:
            raw = self._call(self.ws.recv(), timeout=timeout)
        except asyncio.TimeoutError:
            raise TimeoutError("等待服务端帧超时")
        return json.loads(raw)

    def rpc(self, method: str, params: Optional[Dict[str, Any]] = None,
            timeout: float = 5.0) -> Dict[str, Any]:
        """发一条 rpc 并等到对应 id 的回帧（跳过 event 帧）。"""
        rpc_id = "rpc-" + str(time.monotonic_ns())
        self.send({"type": "rpc", "id": rpc_id, "method": method,
                   "params": params or {}})
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("RPC " + method + " 回帧超时")
            frame = self.recv(timeout=remaining)
            if frame.get("type") in ("rpc_result", "rpc_error") and \
                    frame.get("id") == rpc_id:
                return frame

    def close(self) -> None:
        try:
            self._call(self.ws.close(), timeout=2)
        except Exception:
            pass
        self.loop.call_soon_threadsafe(self.loop.stop)


@pytest.fixture()
def server_and_client():
    """起一个真实桥接服务器并完成握手；测试结束关闭资源。"""
    servers: List[BridgeServer] = []
    clients: List[BridgeClient] = []

    def _make(bot: Any = None, **kw: Any):
        bot = bot or make_bot()
        server = BridgeServer(bot, **kw)
        port = server.start()
        servers.append(server)
        client = BridgeClient(port, server.token)
        clients.append(client)
        return server, client, bot

    yield _make
    for client in clients:
        client.close()
    for server in servers:
        server.close()


def wait_until(predicate, timeout: float = 5.0, interval: float = 0.02) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise AssertionError("条件在超时前未满足")


# ----------------------------------------------------------------------------
# 生命周期 / 握手
# ----------------------------------------------------------------------------

def test_server_starts_on_random_loopback_port_and_reports_it(server_and_client):
    server, client, bot = server_and_client()
    assert server.host == LOOPBACK
    assert server.port > 0
    assert server.capabilities == WEB_BRIDGE_CAPABILITIES
    assert server.token   # 启动期生成，非空


def test_repeated_start_is_idempotent():
    bot = make_bot()
    server = BridgeServer(bot)
    port1 = server.start()
    try:
        port2 = server.start()
        assert port1 == port2
    finally:
        server.close()


def test_close_is_idempotent_and_restart_allowed():
    bot = make_bot()
    server = BridgeServer(bot)
    server.start()
    server.close()
    server.close()   # 幂等
    port = server.start()   # 复用同一实例重启
    try:
        assert port > 0
    finally:
        server.close()


def test_wrong_token_is_rejected_at_handshake():
    bot = make_bot()
    server = BridgeServer(bot)
    port = server.start()
    try:
        client = BridgeClient(port, server.token, do_handshake=False)
        client.send({"type": "hello", "token": "wrong-token"})
        # 服务端直接关连接（close code 4001），recv 抛关闭异常
        with pytest.raises(Exception):
            client.recv(timeout=5)
        client.close()
    finally:
        server.close()


def test_non_hello_first_frame_is_rejected():
    bot = make_bot()
    server = BridgeServer(bot)
    port = server.start()
    try:
        client = BridgeClient(port, server.token, do_handshake=False)
        client.send({"type": "chat", "text": "绕过握手"})
        with pytest.raises(Exception):
            client.recv(timeout=5)
        client.close()
    finally:
        server.close()


def test_handshake_timeout_closes_idle_connection():
    bot = make_bot()
    server = BridgeServer(bot)
    port = server.start()
    try:
        # 不握手也不发任何帧：握手超时后服务端主动关连接
        client = BridgeClient(port, server.token, do_handshake=False)
        start = time.monotonic()
        with pytest.raises(Exception):
            client.recv(timeout=8)
        elapsed = time.monotonic() - start
        assert elapsed < 7.5   # 握手超时（5s）触发，而不是无限等待
        client.close()
    finally:
        server.close()


# ----------------------------------------------------------------------------
# 事件流（恒 2 元组 -> event 帧，done 恰好一次且最后）
# ----------------------------------------------------------------------------

def test_chat_forwards_events_and_done_exactly_once_at_end(server_and_client):
    server, client, bot = server_and_client()
    client.send({"type": "chat", "text": "在吗"})
    frames: List[Dict[str, Any]] = []
    while True:
        frame = client.recv(timeout=10)
        frames.append(frame)
        if frame.get("type") == "event" and \
                frame["event"]["kind"] == "done":
            break
    events = [f["event"] for f in frames if f.get("type") == "event"]
    kinds = [e["kind"] for e in events]
    assert kinds == ["text", "tool", "tool", "text", "done"]
    assert events[-1]["payload"] == "你好，世界"
    assert bot.calls == ["在吗"]


def test_chat_passes_images_through(server_and_client):
    server, client, bot = server_and_client()
    client.send({"type": "chat", "text": "", "images": ["C:/x.png"]})
    while True:
        frame = client.recv(timeout=10)
        if frame.get("type") == "event" and frame["event"]["kind"] == "done":
            break
    assert bot.images_seen == [["C:/x.png"]]


def test_stream_error_becomes_done_ok_false(server_and_client):
    server, client, _ = server_and_client(bot=make_bot(error=RuntimeError("上游炸了")))
    client.send({"type": "chat", "text": "hi"})
    frame = client.recv(timeout=10)
    while frame.get("type") != "event" or frame["event"]["kind"] != "done":
        frame = client.recv(timeout=10)
    event = frame["event"]
    assert event["payload"]["ok"] is False
    assert "上游炸了" in event["payload"]["error"]


def test_bot_without_done_gets_synthesized_done(server_and_client):
    """越约 bot（流耗尽没产 done）：桥补发恰好一条 done。"""
    bot = make_bot(events=[("text", "只有文本")])
    server, client, _ = server_and_client(bot=bot)
    client.send({"type": "chat", "text": "hi"})
    kinds: List[str] = []
    while True:
        frame = client.recv(timeout=10)
        if frame.get("type") == "event":
            kinds.append(frame["event"]["kind"])
            if frame["event"]["kind"] == "done":
                break
    assert kinds == ["text", "done"]


def test_second_chat_while_busy_is_rejected(server_and_client):
    server, client, bot = server_and_client(bot=make_bot(slow_event=0.3))
    client.send({"type": "chat", "text": "第一轮"})
    time.sleep(0.1)   # 让第一轮进入 busy
    client.send({"type": "chat", "text": "第二轮"})
    busy_rejected = False
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        frame = client.recv(timeout=max(0.1, deadline - time.monotonic()))
        if frame.get("type") == "rpc_error" and frame["error"]["code"] == -32001:
            busy_rejected = True
            break
        if frame.get("type") == "event" and frame["event"]["kind"] == "done":
            break
    assert busy_rejected
    # 第一轮不受影响，正常收尾
    while True:
        frame = client.recv(timeout=10)
        if frame.get("type") == "event" and frame["event"]["kind"] == "done":
            break
    assert bot.calls == ["第一轮"]


def test_interrupt_stops_the_stream_and_sends_done(server_and_client):
    server, client, bot = server_and_client(bot=make_bot(slow_event=0.4))
    client.send({"type": "chat", "text": "慢慢说"})
    time.sleep(0.2)   # 进入流消费
    client.send({"type": "interrupt"})
    seen_done_ok_false = False
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        frame = client.recv(timeout=max(0.1, deadline - time.monotonic()))
        if frame.get("type") == "event" and frame["event"]["kind"] == "done":
            assert frame["event"]["payload"]["ok"] is False
            seen_done_ok_false = True
            break
    assert seen_done_ok_false
    # 流被打断后桥恢复可用：新一轮完整收尾，done 后无残留事件
    client.send({"type": "chat", "text": "新的话"})
    kinds: List[str] = []
    while True:
        frame = client.recv(timeout=10)
        if frame.get("type") == "event":
            kinds.append(frame["event"]["kind"])
            if frame["event"]["kind"] == "done":
                break
    assert kinds[-1] == "done"
    assert kinds.count("done") == 1


# ----------------------------------------------------------------------------
# RPC 方法面
# ----------------------------------------------------------------------------

def test_rpc_config_get_whole_and_by_key(server_and_client):
    server, client, _ = server_and_client()
    whole = client.rpc("config.get", {})
    assert whole["type"] == "rpc_result"
    assert whole["result"]["value"]["active_provider"] == "deepseek"

    keyed = client.rpc("config.get", {"key": "providers.deepseek.api_key"})
    assert keyed["result"]["value"] == "sk-test"

    missing = client.rpc("config.get", {"key": "no.such.key"})
    assert missing["type"] == "rpc_error"


def test_rpc_config_set_writes_back_comment_preserved(server_and_client):
    server, client, bot = server_and_client()
    reply = client.rpc("config.set",
                       {"updates": {"providers.deepseek.model": "deepseek-chat"}})
    assert reply["type"] == "rpc_result"
    assert reply["result"]["ok"] is True
    assert bot.config_manager.written == [
        {("providers", "deepseek", "model"): "deepseek-chat"}]


def test_rpc_memory_search_and_clear(server_and_client):
    server, client, bot = server_and_client()
    found = client.rpc("memory.search", {"query": "昨天"})
    assert found["result"]["memories"] == ["[2025-01-01 08:00:00] 记忆A"]

    cleared = client.rpc("memory.clear", {})
    assert cleared["result"]["ok"] is True
    assert bot.memories_cleared == 1


def test_rpc_mode_list_and_switch(server_and_client):
    server, client, bot = server_and_client()
    listing = client.rpc("mode.list", {})
    assert listing["result"]["active"] == "chat"
    assert [m["name"] for m in listing["result"]["modes"]] == ["chat", "work"]

    switched = client.rpc("mode.switch", {"mode": "work"})
    assert switched["result"] == {"ok": True, "active": "work"}
    assert bot.mode_switched == ["work"]

    bad = client.rpc("mode.switch", {"mode": "hacker"})
    assert bad["type"] == "rpc_error"


def test_rpc_approval_pending_and_resolve(server_and_client):
    server, client, bot = server_and_client()
    pending = client.rpc("approval.pending", {})
    assert pending["result"]["pending"] == ["req-1"]

    resolved = client.rpc("approval.resolve",
                          {"request_id": "req-1", "decision": "allow"})
    assert resolved["result"] == {"delivered": True}
    assert bot.approval_broker.resolved == [("req-1", "allow")]

    gone = client.rpc("approval.resolve",
                      {"request_id": "req-1", "decision": "deny"})
    assert gone["type"] == "rpc_error"

    bad = client.rpc("approval.resolve",
                     {"request_id": "req-1", "decision": "always"})
    assert bad["type"] == "rpc_error"


def test_rpc_notify_register_puts_web_notifier_as_default(server_and_client):
    from castorice.notify import (
        DeliveryResult,
        WebNotifier,
        get_notifier_registry,
    )

    server, client, _ = server_and_client()
    reply = client.rpc("notify.register", {})
    assert reply["result"] == {"ok": True}
    registry = get_notifier_registry()
    notifier = registry.default
    assert isinstance(notifier, WebNotifier)
    try:
        # 服务器活着且客户端已握手：通知应送达（web 通道）
        result = notifier.notify("标题", "正文", target_id="web:main")
        assert isinstance(result, DeliveryResult) and result.ok is True
        frame = client.recv(timeout=5)
        assert frame["type"] == "event" and frame["event"]["kind"] == "notification"
        assert frame["event"]["payload"]["title"] == "标题"
    finally:
        registry.set_default(None)


def test_rpc_unknown_method_and_bad_params(server_and_client):
    server, client, _ = server_and_client()
    unknown = client.rpc("no.such.method", {})
    assert unknown["type"] == "rpc_error"
    assert unknown["error"]["code"] == -32601

    bad = client.rpc("config.set", {"updates": "not-a-dict"})
    assert bad["type"] == "rpc_error"


def test_malformed_json_frame_gets_parse_error(server_and_client):
    server, client, _ = server_and_client()
    client._call(client.ws.send("这不是JSON{{{"), timeout=5)
    frame = client.recv(timeout=5)
    assert frame["type"] == "rpc_error"
    assert frame["error"]["code"] == -32700


def test_ping_pong(server_and_client):
    server, client, _ = server_and_client()
    client.send({"type": "ping", "ts": 123})
    frame = client.recv(timeout=5)
    assert frame == {"type": "pong", "ts": 123}


# ----------------------------------------------------------------------------
# 通知投递（send_event / WebNotifier 软失败）
# ----------------------------------------------------------------------------

def test_send_event_returns_false_without_clients():
    bot = make_bot()
    server = BridgeServer(bot)
    server.start()
    try:
        assert server.send_event("notification", {"title": "x"}) is False
    finally:
        server.close()


def test_request_payload_dict_serializes_permission_request():
    request = PermissionRequest(
        tool="run_command", arguments={"command": "git status"},
        mode="work", call_id="c1", target_paths=("D:/a",))
    payload = request_payload_dict(request)
    assert payload["tool"] == "run_command"
    assert payload["arguments"] == {"command": "git status"}
    assert payload["target_paths"] == ["D:/a"]
    assert payload["mode"] == "work"
