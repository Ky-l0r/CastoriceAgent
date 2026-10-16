"""QQ 渠道适配器（NapCat / OneBot 11）测试。

全部用本地模拟的 OneBot 11 WS 端点（绑定 127.0.0.1 回环地址；离线夹具
只拦截非回环的 socket.connect，本文件不需要真实 NapCat 实例，也不联网）。
"""

from __future__ import annotations

import asyncio
import socket
import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from castorice.channels.base import (
    ChannelCapabilities,
    ChannelContext,
    ChannelError,
    ChannelMessage,
    OutboundMessage,
    normalize_target,
)
from castorice.channels.napcat import (
    NAPCAT_CAPABILITIES,
    NapCatChannel,
    napcat_options_from_config,
)

LOOPBACK = "127.0.0.1"

# 模块导入期保存真实 connect（此时 conftest 的 block_network 尚未打补丁）。
_REAL_CONNECT = socket.socket.connect


@pytest.fixture(autouse=True)
def _allow_loopback_connect(monkeypatch):
    """放行回环地址的 socket.connect（本地模拟 OneBot 11 WS 端点）。

    conftest 的 block_network 会拦截所有 connect；但 Windows 上
    asyncio 事件循环的 self-pipe（socketpair 回退实现）本身就要对
    127.0.0.1 发起 connect，事件循环根本建不起来。本夹具在禁网前提
    下放行回环地址——真实 NapCat 也部署在回环上，模拟端点与其一致；
    非回环地址依旧拦截，禁网保证不破。
    """
    original = _REAL_CONNECT

    def _guarded(self, address):
        host = address[0] if isinstance(address, tuple) else ""
        if isinstance(host, str) and host in ("127.0.0.1", "::1", "localhost"):
            return original(self, address)
        raise RuntimeError(
            "测试期间禁止非回环连接；本地模拟端点请使用 127.0.0.1"
        )

    monkeypatch.setattr(socket.socket, "connect", _guarded)


class FakeBot:
    """鸭子类型的 ai.AI 替身：记录输入并产出固定事件流。"""

    def __init__(
        self,
        events: Optional[List[tuple]] = None,
        error: Optional[Exception] = None,
    ) -> None:
        self.calls: List[str] = []
        self.events = events if events is not None else [
            ("text", "你好"),
            ("text", "，"),
            ("text", "世界"),
            ("done", "你好，世界"),
        ]
        self.error = error

    def get_response_stream(self, user_input: str, images=None):
        self.calls.append(user_input)
        if self.error is not None:
            raise self.error
        yield from self.events


def make_ctx(bot: FakeBot, **options: Any) -> ChannelContext:
    return ChannelContext(
        bot=bot,
        sessions_root="sessions",
        data_dir="channels/napcat",
        options=options,
    )


class FakeOneBotServer:
    """本地模拟的 OneBot 11 反向 WS 端点。

    * 校验 access token（Bearer 头 / access_token query）；
    * 记录收到的 action 帧并回执（带 message_id）；
    * 推送可编程的下行事件（message / meta_event）；
    * 可控制断开连接（供重连测试）。
    """

    def __init__(
        self,
        token: str = "",
        host: str = LOOPBACK,
        port: int = 0,
    ) -> None:
        import websockets

        self._websockets = websockets
        self.token = token
        self.host = host
        self.actions: List[Dict[str, Any]] = []
        self.connections = 0
        self.next_message_id = 1000
        self._server = None
        self._loop = None
        self._thread = None
        self._clients: set = set()
        self._drop_after: Dict[str, int] = {}
        self._started = threading.Event()
        self.port = port
        self.force_token_mismatch = False

    # ---- 生命周期 ----------------------------------------------------

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        assert self._started.wait(timeout=5.0)

    def stop(self) -> None:
        if self._loop is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._shutdown)
        if self._thread is not None:
            self._thread.join(timeout=5.0)

    def _shutdown(self) -> None:
        if self._server is not None:
            self._server.close()
        for ws in list(self._clients):
            asyncio.ensure_future(ws.close())
        self._clients.clear()

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop

        async def handler(ws):
            self.connections += 1
            self._clients.add(ws)
            try:
                await self._serve(ws)
            finally:
                self._clients.discard(ws)

        async def main():
            self._server = await self._websockets.serve(
                handler, self.host, self.port
            )
            self.port = self._server.sockets[0].getsockname()[1]
            self._started.set()
            await self._server.wait_closed()

        try:
            loop.run_until_complete(main())
        except Exception:
            pass
        finally:
            loop.close()

    async def _serve(self, ws) -> None:
        if self.force_token_mismatch or (self.token and not self._authorized(ws)):
            await ws.close(code=4001, reason="invalid access token")
            return
        async for raw in ws:
            frame = json.loads(raw)
            if isinstance(frame, dict) and "action" in frame:
                self.actions.append(frame)
                if frame.get("action") == "get_login_info":
                    await ws.send(json.dumps({
                        "status": "ok",
                        "retcode": 0,
                        "data": {"user_id": 10000, "nickname": "castorice"},
                        "echo": frame.get("echo"),
                    }))
                    continue
                reply = {
                    "status": "async" if frame.get("action") == "send_group_msg" else "ok",
                    "retcode": 0,
                    "data": {"message_id": self.next_message_id},
                    "echo": frame.get("echo"),
                }
                self.next_message_id += 1
                await ws.send(json.dumps(reply))

    def _authorized(self, ws) -> bool:
        # websockets >= 12 的 request.headers；兼容旧版 request_headers。
        request = getattr(ws, "request", None) or ws
        headers = getattr(request, "headers", {}) or {}
        auth = headers.get("authorization", "")
        if auth == f"Bearer {self.token}":
            return True
        query = getattr(request, "path", "") or ""
        return f"access_token={self.token}" in query

    # ---- 下行事件 ----------------------------------------------------

    def push(self, event: Dict[str, Any]) -> None:
        """向当前所有连接广播一条下行事件。"""
        loop = self._loop
        if loop is None or not loop.is_running():
            return

        async def _push() -> None:
            dead = []
            for ws in list(self._clients):
                try:
                    await ws.send(json.dumps(event))
                except Exception:
                    dead.append(ws)
            for ws in dead:
                self._clients.discard(ws)

        asyncio.run_coroutine_threadsafe(_push(), loop).result(timeout=5.0)

    def drop_connections(self) -> None:
        """主动断开所有客户端连接（触发渠道重连）。"""
        loop = self._loop
        if loop is None or not loop.is_running():
            return

        async def _drop() -> None:
            for ws in list(self._clients):
                try:
                    await ws.close()
                except Exception:
                    pass

        asyncio.run_coroutine_threadsafe(_drop(), loop).result(timeout=5.0)


def wait_until(predicate, timeout: float = 5.0, interval: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


@pytest.fixture
def server_factory():
    servers = []

    def _make(**kwargs) -> FakeOneBotServer:
        server = FakeOneBotServer(**kwargs)
        server.start()
        servers.append(server)
        return server

    yield _make
    for server in servers:
        server.stop()


# ---------------------------------------------------------------------------
# 配置与 normalize_target
# ---------------------------------------------------------------------------


def test_options_requires_url():
    with pytest.raises(ChannelError):
        napcat_options_from_config(None)
    with pytest.raises(ChannelError):
        napcat_options_from_config({"access_token": "x"})


def test_options_defaults_and_normalization():
    options = napcat_options_from_config({"url": "ws://127.0.0.1:3001"})
    assert options["access_token"] == ""
    assert options["allowed_senders"] == []
    assert options["default_chat_type"] == "private"
    assert options["max_chunk_len"] == 4500
    assert options["report_tools"] is False


def test_options_empty_sender_rejected():
    # 「空串匹配一切」的静默放行必须被 normalize_target 拦下。
    with pytest.raises(ChannelError):
        napcat_options_from_config(
            {"url": "ws://127.0.0.1:3001", "allowed_senders": ["", "123"]}
        )


def test_options_bad_chat_type_rejected():
    with pytest.raises(ChannelError):
        napcat_options_from_config(
            {"url": "ws://127.0.0.1:3001", "default_chat_type": "channel"}
        )


def test_normalize_target_empty_raises():
    with pytest.raises(ChannelError):
        normalize_target("")


# ---------------------------------------------------------------------------
# 协议与能力
# ---------------------------------------------------------------------------


def test_capabilities_pure_python_types():
    # 第三期 Electron 壳注记：渠道公共接口不得引入 Qt 类型。
    assert isinstance(NAPCAT_CAPABILITIES, ChannelCapabilities)
    assert NAPCAT_CAPABILITIES.name == "qq"
    assert NAPCAT_CAPABILITIES.supports_proactive is True


def test_channel_satisfies_protocol():
    from castorice.channels.base import Channel

    bot = FakeBot()
    channel = NapCatChannel(make_ctx(bot, url="ws://127.0.0.1:1"))
    assert isinstance(channel, Channel)  # runtime_checkable 协议
    assert channel.capabilities.name == "qq"


def test_close_is_idempotent_without_start():
    channel = NapCatChannel(make_ctx(FakeBot(), url="ws://127.0.0.1:1"))
    channel.close()
    channel.close()  # 不抛


# ---------------------------------------------------------------------------
# 连接 / 鉴权 / 事件分发
# ---------------------------------------------------------------------------


def test_start_fail_fast_on_refused_connection():
    bot = FakeBot()
    channel = NapCatChannel(make_ctx(bot, url="ws://127.0.0.1:9", connect_timeout=1))
    with pytest.raises(ChannelError):
        channel.start()
    channel.close()


def test_start_rejects_bad_token(server_factory):
    server = server_factory(token="secret")
    url = f"ws://{LOOPBACK}:{server.port}"
    channel = NapCatChannel(make_ctx(FakeBot(), url=url, access_token="wrong"))
    with pytest.raises(ChannelError):
        channel.start()
    channel.close()


def test_message_dispatch_end_to_end(server_factory):
    server = server_factory()
    bot = FakeBot()
    channel = NapCatChannel(
        make_ctx(bot, url=f"ws://{LOOPBACK}:{server.port}", max_chunk_len=1000)
    )
    channel.start()
    try:
        server.push({
            "post_type": "message",
            "message_type": "private",
            "user_id": 12345,
            "message_id": 9001,
            "raw_message": "ping",
        })
        assert wait_until(lambda: bot.calls == ["ping"])
        assert wait_until(
            lambda: any(
                a.get("action") == "send_private_msg"
                and a.get("params", {}).get("user_id") == 12345
                for a in server.actions
            )
        )
        sent = [a for a in server.actions if a["action"] == "send_private_msg"]
        text = sent[-1]["params"]["message"][0]["data"]["text"]
        assert text == "你好，世界"
    finally:
        channel.close()


def test_meta_event_ignored(server_factory):
    server = server_factory()
    bot = FakeBot()
    channel = NapCatChannel(make_ctx(bot, url=f"ws://{LOOPBACK}:{server.port}"))
    channel.start()
    try:
        server.push({
            "post_type": "meta_event",
            "meta_event_type": "heartbeat",
            "status": {},
            "interval": 5000,
        })
        server.push({
            "post_type": "message",
            "message_type": "private",
            "user_id": 1,
            "message_id": 2,
            "raw_message": "hi",
        })
        assert wait_until(lambda: bot.calls == ["hi"])
        # 心跳没有触发任何发送：唯一动作是对真实消息的回复。
        send_actions = [a for a in server.actions if a["action"] == "send_private_msg"]
        assert len(send_actions) == 1
        assert send_actions[0]["params"]["user_id"] == 1
    finally:
        channel.close()


def test_sender_allowlist_blocks(server_factory):
    server = server_factory()
    bot = FakeBot()
    channel = NapCatChannel(
        make_ctx(bot, url=f"ws://{LOOPBACK}:{server.port}", allowed_senders=["42"])
    )
    channel.start()
    try:
        server.push({
            "post_type": "message",
            "message_type": "private",
            "user_id": 99999,
            "message_id": 5,
            "raw_message": "hello",
        })
        time.sleep(0.3)
        assert bot.calls == []
        # 唯一动作是首连验证的 get_login_info，被拒消息不触发任何发送。
        assert server.actions == [
            a for a in server.actions if a["action"] == "get_login_info"
        ]
    finally:
        channel.close()


# ---------------------------------------------------------------------------
# 消息幂等去重（契约 §2：同 id 重复投递必须被渠道丢弃）
# ---------------------------------------------------------------------------


def test_duplicate_message_id_dropped(server_factory):
    server = server_factory()
    bot = FakeBot()
    channel = NapCatChannel(make_ctx(bot, url=f"ws://{LOOPBACK}:{server.port}"))
    channel.start()
    try:
        event = {
            "post_type": "message",
            "message_type": "private",
            "user_id": 7,
            "message_id": 555,
            "raw_message": "once",
        }
        server.push(event)
        assert wait_until(lambda: bot.calls == ["once"])

        server.push(dict(event))  # 同 id 重放（NapCat 重发场景）
        server.push(dict(event))
        time.sleep(0.4)
        assert bot.calls == ["once"], "重复 message_id 必须被丢弃"

        # 不同 id 仍正常投递。
        server.push({**event, "message_id": 556, "raw_message": "twice"})
        assert wait_until(lambda: bot.calls == ["once", "twice"])
    finally:
        channel.close()


def test_dedup_on_on_message_directly():
    # 不经 WS 直接测渠道对象本身：重复投递返回空迭代器。
    bot = FakeBot()
    channel = NapCatChannel(make_ctx(bot, url="ws://127.0.0.1:1"))
    message = ChannelMessage(
        channel="qq", sender_id="1", chat_id="private:1",
        text="hi", message_id="abc",
    )
    first = list(channel.on_message(message))
    assert [k for k, _ in first][-1] == "done"
    second = list(channel.on_message(message))
    assert second == []


# ---------------------------------------------------------------------------
# 事件流同构（契约 §3）
# ---------------------------------------------------------------------------


def test_on_message_event_stream_shape():
    bot = FakeBot(events=[
        ("text", "a"),
        ("tool", {"name": "web_search", "status": "start"}),
        ("tool", {"name": "web_search", "status": "done"}),
        ("text", "b"),
        ("done", "ab"),
    ])
    channel = NapCatChannel(make_ctx(bot, url="ws://127.0.0.1:1"))
    events = list(channel.on_message(ChannelMessage(
        channel="qq", sender_id="1", chat_id="private:1", text="q", message_id="m1",
    )))
    kinds = [k for k, _ in events]
    assert all(isinstance(e, tuple) and len(e) == 2 for e in events)
    assert kinds == ["text", "tool", "tool", "text", "done"]
    assert events[-1] == ("done", "ab")


def test_on_message_translates_exception_to_done():
    bot = FakeBot(error=RuntimeError("模型爆炸了"))
    channel = NapCatChannel(make_ctx(bot, url="ws://127.0.0.1:1"))
    events = list(channel.on_message(ChannelMessage(
        channel="qq", sender_id="1", chat_id="private:1", text="q", message_id="m2",
    )))
    assert len(events) == 1
    kind, payload = events[0]
    assert kind == "done"
    assert "模型爆炸了" in payload


def test_long_text_chunked(server_factory):
    server = server_factory()
    bot = FakeBot(events=[
        ("text", "x" * 120),
        ("text", "y" * 120),
        ("done", "x" * 120 + "y" * 120),
    ])
    channel = NapCatChannel(
        make_ctx(bot, url=f"ws://{LOOPBACK}:{server.port}", max_chunk_len=100)
    )
    channel.start()
    try:
        server.push({
            "post_type": "message",
            "message_type": "private",
            "user_id": 8,
            "message_id": 77,
            "raw_message": "long",
        })
        assert wait_until(lambda: bot.calls == ["long"])
        assert wait_until(lambda: len(server.actions) >= 3)
        sent = [a for a in server.actions if a["action"] == "send_private_msg"]
        texts = [a["params"]["message"][0]["data"]["text"] for a in sent]
        assert len(texts) == 3  # 120/100 + 120/100 -> 2 + 2 段中的收尾合并
        assert "".join(texts) == "x" * 120 + "y" * 120
        assert all(len(t) <= 100 for t in texts)
    finally:
        channel.close()


# ---------------------------------------------------------------------------
# 断线重连
# ---------------------------------------------------------------------------


def test_reconnect_after_server_drop(server_factory):
    server = server_factory()
    bot = FakeBot()
    channel = NapCatChannel(
        make_ctx(
            bot,
            url=f"ws://{LOOPBACK}:{server.port}",
            reconnect_initial=0.1,
            reconnect_max=0.5,
        )
    )
    channel.start()
    try:
        connections_before = server.connections
        server.drop_connections()
        # 断线后渠道应自动重连上同一个端点。
        assert wait_until(lambda: server.connections > connections_before, timeout=8.0)
        # 重连后新消息照常处理。
        server.push({
            "post_type": "message",
            "message_type": "private",
            "user_id": 11,
            "message_id": 88,
            "raw_message": "after-drop",
        })
        assert wait_until(lambda: bot.calls == ["after-drop"], timeout=8.0)
    finally:
        channel.close()


# ---------------------------------------------------------------------------
# send / 出站
# ---------------------------------------------------------------------------


def test_send_returns_platform_message_id(server_factory):
    server = server_factory()
    channel = NapCatChannel(make_ctx(FakeBot(), url=f"ws://{LOOPBACK}:{server.port}"))
    channel.start()
    try:
        message_id = channel.send(OutboundMessage(chat_id="private:123", text="主动消息"))
        assert message_id == str(1000)  # FakeOneBotServer 从 1000 起发 id
        assert wait_until(
            lambda: any(a["action"] == "send_private_msg" for a in server.actions)
        )
    finally:
        channel.close()


def test_send_group_message(server_factory):
    server = server_factory()
    channel = NapCatChannel(make_ctx(FakeBot(), url=f"ws://{LOOPBACK}:{server.port}"))
    channel.start()
    try:
        channel.send(OutboundMessage(chat_id="group:678", text="群消息"))
        assert wait_until(
            lambda: any(
                a["action"] == "send_group_msg"
                and a["params"]["group_id"] == 678
                for a in server.actions
            )
        )
    finally:
        channel.close()


def test_send_before_start_raises():
    channel = NapCatChannel(make_ctx(FakeBot(), url="ws://127.0.0.1:1"))
    with pytest.raises(ChannelError):
        channel.send(OutboundMessage(chat_id="private:1", text="x"))


def test_send_invalid_chat_id_raises(server_factory):
    server = server_factory()
    channel = NapCatChannel(make_ctx(FakeBot(), url=f"ws://{LOOPBACK}:{server.port}"))
    channel.start()
    try:
        with pytest.raises(ChannelError):
            channel.send(OutboundMessage(chat_id="irc:abc", text="x"))
        with pytest.raises(ChannelError):
            channel.send(OutboundMessage(chat_id="", text="x"))
    finally:
        channel.close()


def test_send_empty_message_raises(server_factory):
    server = server_factory()
    channel = NapCatChannel(make_ctx(FakeBot(), url=f"ws://{LOOPBACK}:{server.port}"))
    channel.start()
    try:
        with pytest.raises(ChannelError):
            channel.send(OutboundMessage(chat_id="private:1", text="", emoji_tag=""))
    finally:
        channel.close()


# ---------------------------------------------------------------------------
# 生命周期幂等
# ---------------------------------------------------------------------------


def test_start_idempotent_and_restart_after_close(server_factory):
    server = server_factory()
    bot = FakeBot()
    channel = NapCatChannel(make_ctx(bot, url=f"ws://{LOOPBACK}:{server.port}"))
    channel.start()
    connections = server.connections
    channel.start()  # 幂等：不再建新连接
    time.sleep(0.3)
    assert server.connections == connections

    channel.close()
    channel.close()  # 幂等

    # close 后复用同一实例重启是允许的（契约：close 后 start 复用同一实例）。
    channel.start()
    try:
        assert wait_until(lambda: server.connections > connections)
        server.push({
            "post_type": "message",
            "message_type": "private",
            "user_id": 3,
            "message_id": 9,
            "raw_message": "again",
        })
        assert wait_until(lambda: bot.calls == ["again"])
    finally:
        channel.close()
