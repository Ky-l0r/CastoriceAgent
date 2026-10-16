"""QQ 渠道适配器：NapCat / OneBot 11 反向 WebSocket（第二期）。

对接方式（NapCat 的「反向 WebSocket」部署）：

    NapCat（WS 服务端）
        ↑ NapCat 把 QQ 消息事件推给本适配器
    NapCatChannel（本文件，作为 WS 客户端连上去）
        ↑ 用 action 请求（send_private_msg / send_group_msg）回消息
    同一条连接上双向收发，鉴权用 access token（Bearer 头）。

本模块实现 docs/refactor/05-channel-contract.md 的 Channel 协议：

* start() / close() 幂等；start 连不上即抛 ChannelError（fail-fast，
  不静默降级）；连接建立后的断线按指数退避自动重连。
* on_message 产出与 ai.AI.get_response_stream 同构的恒 2 元组事件流
  （('text', str) / ('tool', dict) / ('emoji', payload) / ('done', str)，
  done 恰好一次且最后），同时在消费过程中按平台节奏把 text 分段发送到
  QQ——分段、限速是渠道职责（契约 §3）。
* send(OutboundMessage) 发送出站消息并返回平台消息 id。
* message_id 幂等去重：同 id 重复投递被渠道丢弃（契约 §2）。
* 目标 id（chat_id / allowed_senders）统一过 normalize_target。

第三期衔接注记：渠道层与 DesktopNotifier 抽象是 Electron 壳 + Python 后端
架构下 Web 前端也要消费的两个接口——本模块公共接口只用纯 Python 类型
（dataclass / str / dict / Iterator），不引入任何 Qt / PySide6 类型。

线程模型：start() 起一个后台线程跑 asyncio 事件循环；协议方法
（start/close/on_message/send）全部是同步调用，内部用
run_coroutine_threadsafe 桥接。on_message 的消费（调用 agent 并分段
发送）放进 executor，保证事件循环在 agent 生成器阻塞期间仍能收心跳与
回执。红线 D：本模块不 import ui / PySide6 / ai；对 AI 的引用通过
ChannelContext.bot 鸭子类型传入。
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections import deque
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from .base import (
    ChannelCapabilities,
    ChannelContext,
    ChannelError,
    ChannelMessage,
    OutboundMessage,
    normalize_target,
)

__all__ = [
    "NapCatChannel",
    "NAPCAT_CAPABILITIES",
    "napcat_options_from_config",
]

logger = logging.getLogger(__name__)

#: 渠道能力声明：QQ 文本双向 + 表情包贴纸 + 主动聊天（机器人可先手）。
NAPCAT_CAPABILITIES = ChannelCapabilities(
    name="qq",
    display_name="QQ (NapCat)",
    supports_markdown=False,
    supports_image_input=False,   # 图片接收协议位后置（预留，见 attachments）
    supports_emoji=True,
    supports_proactive=True,      # NapCat 可先手发私聊/群聊消息
)

_DEFAULT_MAX_CHUNK_LEN = 4500
_SEEN_MAX = 4096


def napcat_options_from_config(section: Any) -> Dict[str, Any]:
    """把 config.yaml 的 channels.napcat 节整理成合法 options。

    校验失败抛 ChannelError（fail-fast）；目标 id 一律过 normalize_target，
    杜绝「空串匹配一切」的静默放行。

    必填：url（NapCat 反向 WS 地址，形如 ws://127.0.0.1:3001）。
    可选：access_token（空串 = 不鉴权）、allowed_senders / allowed_groups
    （列表；缺省 = 不限制，但列表里的每一项都必须是非空 id）、
    default_chat_type（private | group，裸数字 chat_id 的归属）、
    max_chunk_len（长文本分段长度）、reconnect_initial / reconnect_max
    （重连退避秒数）、report_tools（是否把 tool 事件折叠成提示）、
    connect_timeout（start 的首次连接超时秒数）、emoji_dir（表情包目录，
    预留协议位）。
    """
    if section is None:
        raise ChannelError("config.yaml 缺少 channels.napcat 节")
    if not isinstance(section, dict):
        raise ChannelError("channels.napcat 节必须是映射")
    options: Dict[str, Any] = dict(section)

    url = str(options.get("url", "") or "").strip()
    if not url:
        raise ChannelError("channels.napcat.url 不能为空（NapCat 反向 WS 地址）")
    options["url"] = url

    # access_token 允许空串（= 不鉴权），统一转 str。
    options["access_token"] = str(options.get("access_token") or "")

    for list_key in ("allowed_senders", "allowed_groups"):
        raw = options.get(list_key) or []
        if not isinstance(raw, (list, tuple)):
            raise ChannelError(f"channels.napcat.{list_key} 必须是列表")
        # 逐项过 normalize_target：空串项在这里直接炸，而不是运行期静默放行。
        options[list_key] = [normalize_target(item) for item in raw]

    chat_type = str(options.get("default_chat_type") or "private").strip()
    if chat_type not in ("private", "group"):
        raise ChannelError(
            f"channels.napcat.default_chat_type 必须是 private|group，得到 {chat_type!r}"
        )
    options["default_chat_type"] = chat_type

    for num_key, default, minimum in (
        ("max_chunk_len", _DEFAULT_MAX_CHUNK_LEN, 1),
        ("reconnect_initial", 1.0, 0.0),
        ("reconnect_max", 30.0, 0.0),
        ("connect_timeout", 10.0, 0.0),
    ):
        value = options.get(num_key)
        try:
            value = default if value is None else float(value)
        except (TypeError, ValueError):
            raise ChannelError(f"channels.napcat.{num_key} 必须是数字，得到 {value!r}")
        if value < minimum:
            raise ChannelError(f"channels.napcat.{num_key} 不能小于 {minimum}")
        options[num_key] = value

    options["report_tools"] = bool(options.get("report_tools", False))
    return options


def _require_websockets() -> Any:
    try:
        import websockets  # noqa: PLC0415 - 延迟导入，基础协议不硬依赖
    except ImportError as exc:  # pragma: no cover - 环境缺依赖
        raise ChannelError(
            "QQ 渠道需要 websockets 库（pip install websockets）"
        ) from exc
    return websockets


class NapCatChannel:
    """Channel 协议的 QQ 实现（NapCat / OneBot 11 反向 WS）。

    生命周期::

        ctx = ChannelContext(bot=ai, sessions_root=..., data_dir=...,
                             options=napcat_options_from_config(cfg))
        ch = NapCatChannel(ctx)
        ch.start()        # 连接失败抛 ChannelError；重复 start 幂等
        ...
        ch.close()        # 幂等；close 后允许重新 start（复用同一实例）
    """

    capabilities: ChannelCapabilities = NAPCAT_CAPABILITIES

    def __init__(self, ctx: ChannelContext) -> None:
        self._ctx = ctx
        self._options = napcat_options_from_config(ctx.options)
        self._url: str = self._options["url"]
        self._token: str = self._options["access_token"]
        self._allowed_senders: List[str] = self._options["allowed_senders"]
        self._allowed_groups: List[str] = self._options["allowed_groups"]
        self._default_chat_type: str = self._options["default_chat_type"]
        self._max_chunk_len: int = int(self._options["max_chunk_len"])
        self._reconnect_initial: float = self._options["reconnect_initial"]
        self._reconnect_max: float = self._options["reconnect_max"]
        self._connect_timeout: float = self._options["connect_timeout"]
        self._report_tools: bool = self._options["report_tools"]

        # 幂等去重：同 message_id 重复投递必须被渠道丢弃（契约 §2）。
        self._seen_ids: set = set()
        self._seen_order: deque = deque(maxlen=_SEEN_MAX)

        # 运行期状态（均由 _lock 保护）。
        self._lock = threading.RLock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._thread_id: Optional[int] = None
        self._closed = False
        self._running = False
        self._ready = threading.Event()
        self._start_error: str = ""

        # WS 收发状态（只在事件循环线程里动）。
        self._ws: Any = None
        self._echo_seq = 0
        self._echo_futures: Dict[str, asyncio.Future] = {}
        self._last_sent_message_id: str = ""

    # ------------------------------------------------------------------
    # Channel 协议：生命周期
    # ------------------------------------------------------------------

    def start(self) -> None:
        """连接 NapCat 并开始接收。重复 start 幂等；失败抛 ChannelError。

        首次连接同步等待结果（fail-fast）：连不上 / 鉴权被拒时抛
        ChannelError，不出现「看起来在线但其实收不到」的假在线。
        连接建立之后的断线由后台循环按指数退避自动重连。
        """
        with self._lock:
            if self._running and not self._closed:
                return  # 重复 start 幂等
            if self._thread is not None and self._thread.is_alive():
                # 防御：线程活着但 _running 还没置位（极少见的竞态），
                # 让首次连接的同步等待继续进行，而不是叠开第二个线程。
                pass
            else:
                self._closed = False
                self._running = False
                self._ready.clear()
                self._start_error = ""
                self._thread = threading.Thread(
                    target=self._thread_main, name="napcat-channel", daemon=True
                )
                self._thread.start()

        if not self._ready.wait(timeout=self._connect_timeout + 5.0):
            self.close()
            raise ChannelError(
                f"NapCat 连接超时（{self._connect_timeout}s）：{self._url}"
            )
        if self._start_error:
            raise ChannelError(self._start_error)

    def close(self) -> None:
        """断开连接并释放资源。幂等：close 后再 close 不抛。"""
        with self._lock:
            thread = self._thread
            loop = self._loop
            already_closed = self._closed
            self._closed = True
            self._running = False
        if already_closed:
            return
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(self._shutdown_loop)
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=5.0)

    def _shutdown_loop(self) -> None:
        # 事件循环线程内执行：先同步关 WS，再停循环（不留 pending task）。
        ws = self._ws
        self._ws = None
        if ws is not None:
            try:
                # fail_connection 是同步入口：发出 close 帧并标记关闭，
                # 不需要 await（此时循环马上要 stop，不能留 pending task）。
                ws.fail_connection(code=1000)
            except Exception:  # pragma: no cover - 关闭竞态
                pass
        asyncio.get_running_loop().stop()

    # ------------------------------------------------------------------
    # 后台线程 / 事件循环
    # ------------------------------------------------------------------

    def _thread_main(self) -> None:
        websockets = _require_websockets()
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._thread_id = threading.get_ident()
        try:
            loop.run_until_complete(self._run_session(websockets))
        except Exception as exc:  # pragma: no cover - 兜底，不让线程裸崩
            logger.error("NapCat 渠道线程异常退出: %s", exc)
            if not self._ready.is_set():
                self._start_error = f"NapCat 渠道线程异常: {exc}"
                self._ready.set()
        finally:
            loop.close()
            with self._lock:
                self._loop = None
                self._thread_id = None
                self._running = False

    async def _run_session(self, websockets: Any) -> None:
        """连接 + 收发主循环；断线后按指数退避重连，直到 close。"""
        backoff = self._reconnect_initial
        first_attempt = True
        while not self._closed:
            try:
                headers = {}
                if self._token:
                    headers["Authorization"] = "Bearer " + self._token
                async with websockets.connect(
                    self._url,
                    additional_headers=headers or None,
                    open_timeout=self._connect_timeout,
                ) as ws:
                    self._ws = ws
                    self._echo_futures = {}
                    # 接收循环独立成 task：action 回执（含首连验证）需要
                    # 有人持续读 socket——websockets 的接收是惰性的，没有
                    # 活跃的 recv 时回执只会躺在缓冲里。
                    receiver = asyncio.ensure_future(self._recv_loop(ws))
                    try:
                        if first_attempt:
                            first_attempt = False
                            # 首连 fail-fast 验证（契约 §2）：token 错误时
                            # NapCat 会在握手后立即关闭连接，TCP 层「连接
                            # 成功」是假象。发一次标准 OneBot action
                            # （get_login_info），对端限时回执才认在线——
                            # 不出现「看起来在线但其实收不到」的静默降级。
                            # 重连路径不做这一步，避免对端繁忙时反复打扰。
                            await self._send_action(
                                "get_login_info", {}, timeout=5.0
                            )
                            with self._lock:
                                self._running = True
                            self._ready.set()
                            logger.info("NapCat 渠道已连接: %s", self._url)
                        backoff = self._reconnect_initial
                        await receiver
                    finally:
                        if not receiver.done():
                            receiver.cancel()
                            try:
                                await receiver
                            except (asyncio.CancelledError, Exception):
                                pass
            except Exception as exc:
                if first_attempt:
                    # 首连失败：fail-fast，不静默降级。
                    first_attempt = False
                    self._start_error = "NapCat 连接失败（" + self._url + "）: " + str(exc)
                    self._ready.set()
                    return
                if self._closed:
                    return
                logger.warning(
                    "NapCat 连接断开（%.1fs 后重连）: %s", backoff, exc
                )
            self._ws = None
            if self._closed:
                return
            await asyncio.sleep(backoff)
            backoff = min(
                backoff * 2 if backoff else self._reconnect_initial,
                self._reconnect_max,
            )

    async def _recv_loop(self, ws: Any) -> None:
        """持续读取下行帧（含 action 回执），直到连接关闭。"""
        async for raw in ws:
            self._handle_raw(raw)

    def _handle_raw(self, raw: Any) -> None:
        """处理一条 WS 下行帧（事件循环线程内）。"""
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            logger.warning("NapCat 下行非 JSON 帧，忽略")
            return
        if not isinstance(payload, dict):
            return

        echo = payload.get("echo")
        if echo is not None:
            future = self._echo_futures.pop(str(echo), None)
            if future is not None and not future.done():
                future.set_result(payload)
            if payload.get("post_type") is None:
                return  # 纯 action 回执，没有事件体

        post_type = payload.get("post_type")
        if post_type == "meta_event":
            return  # 心跳 / 生命周期事件，无需处理
        if post_type == "message":
            self._dispatch_platform_message(payload)
        # 其余 post_type（notice / request / …）按需扩展。

    def _dispatch_platform_message(self, event: Dict[str, Any]) -> None:
        """把 OneBot message 事件转成 ChannelMessage 交给 agent。

        放进 executor 跑：事件循环在 agent 生成器阻塞期间仍能收心跳与
        action 回执（契约 §4 的线程纪律：不在事件循环里做重活）。
        """
        message = self._build_channel_message(event)
        if message is None:
            return
        loop = self._loop
        if loop is None:  # pragma: no cover - 关闭竞态
            return
        loop.run_in_executor(None, self._consume, message)

    def _build_channel_message(self, event: Dict[str, Any]) -> Optional[ChannelMessage]:
        message_type = event.get("message_type") or (
            "group" if event.get("group_id") else "private"
        )
        user_id = str(event.get("user_id", ""))
        chat_num = str(event.get("group_id") if message_type == "group" else user_id)
        if not chat_num:
            logger.warning("NapCat message 事件缺 user_id/group_id，忽略")
            return None

        sender_id = user_id
        if self._allowed_senders and sender_id not in self._allowed_senders:
            logger.info("NapCat 拒绝非白名单发送者 %s", sender_id)
            return None
        if message_type == "group" and self._allowed_groups and chat_num not in self._allowed_groups:
            logger.info("NapCat 拒绝非白名单群 %s", chat_num)
            return None

        message_id = str(event.get("message_id", ""))
        text = str(event.get("raw_message") or "") or _segments_to_text(event.get("message"))
        return ChannelMessage(
            channel="qq",
            sender_id=sender_id,
            chat_id=message_type + ":" + chat_num,
            text=text,
            message_id=message_id,
            # 图片等附件协议位预留：视觉输入启用时在此填充平台临时引用。
            attachments=(),
        )

    def _consume(self, message: ChannelMessage) -> None:
        """消费 on_message 的事件流（executor 线程）：迭代即完成发送。"""
        try:
            for kind, _payload in self.on_message(message):
                if kind == "done":
                    break
        except Exception as exc:  # pragma: no cover - on_message 已兜异常
            logger.error("NapCat 消息消费异常: %s", exc)

    # ------------------------------------------------------------------
    # Channel 协议：on_message（事件流，契约 §3）
    # ------------------------------------------------------------------

    def on_message(self, message: ChannelMessage) -> Iterator[tuple]:
        """入站消息 -> agent 事件流（与 get_response_stream 同构）。

        返回的迭代器：恒 2 元组，('text', str) / ('tool', dict) /
        ('emoji', payload) / ('done', str)，done 恰好一次且最后。迭代
        过程中渠道同时完成平台侧发送：

        * text 按 max_chunk_len 分段（长文本分页是渠道职责）；
        * tool 事件折叠成「正在执行 xx…」提示或吞掉（report_tools）；
        * emoji 按平台图片消息发送（表情包协议位）；
        * 迭代器内部异常翻译成 ('done', 错误说明文本)，不让平台侧看到
          裸异常栈（契约 §3）。

        幂等去重在进入迭代器之前完成：同 message_id 重复投递直接返回
        空迭代器，不再触发 agent（契约 §2）。
        """
        message_id = message.message_id
        if message_id:
            with self._lock:
                if message_id in self._seen_ids:
                    logger.info("NapCat 丢弃重复消息 id=%s", message_id)
                    return iter(())
                self._seen_ids.add(message_id)
                self._seen_order.append(message_id)
                while len(self._seen_ids) > _SEEN_MAX:
                    stale = self._seen_order.popleft()
                    self._seen_ids.discard(stale)
        return self._generate_response(message)

    def _generate_response(self, message: ChannelMessage) -> Iterator[tuple]:
        chat_id = normalize_target(message.chat_id)
        full_parts: List[str] = []
        buffer = ""
        done_text = ""

        def _try_push(push) -> None:
            # 平台侧发送失败不污染事件流（发送是渠道职责的尽力而为；
            # 事件流的 done 语义仍由 bot 的流决定）。send() 的显式调用
            # 路径不经这里，失败照常抛 ChannelError。
            try:
                push()
            except Exception as push_exc:
                logger.warning("NapCat 发送失败（不阻断事件流）: %s", push_exc)

        try:
            stream = self._ctx.bot.get_response_stream(message.text)
            for kind, payload in stream:
                if kind == "text":
                    full_parts.append(str(payload))
                    buffer += str(payload)
                    while len(buffer) >= self._max_chunk_len:
                        chunk = buffer[:self._max_chunk_len]
                        buffer = buffer[self._max_chunk_len:]
                        _try_push(lambda c=chunk: self._push_text(chat_id, c))
                    yield ("text", payload)
                elif kind == "tool":
                    if self._report_tools and isinstance(payload, dict):
                        name = str(payload.get("name") or "工具")
                        _try_push(lambda n=name: self._push_text(chat_id, f"⚙ 正在执行 {n} …"))
                    yield ("tool", payload)
                elif kind == "emoji":
                    _try_push(lambda pl=payload: self._push_emoji(chat_id, pl))
                    yield ("emoji", payload)
                elif kind == "done":
                    # bot 的 done 恰好一次且最后；本层在其后只补自己的收尾。
                    done_text = str(payload)
                    break
                else:
                    # 未知 kind 原样透传，保持与上游流同构。
                    yield (kind, payload)
            if buffer:
                _try_push(lambda b=buffer: self._push_text(chat_id, b))
            if not done_text:
                done_text = "".join(full_parts)
            yield ("done", done_text)
        except Exception as exc:
            logger.exception("NapCat 事件流异常，翻译为 done")
            error_text = f"处理请求时出错: {exc}"
            _try_push(lambda e=error_text: self._push_text(chat_id, e))
            yield ("done", error_text)

    # ------------------------------------------------------------------
    # Channel 协议：send / 出站
    # ------------------------------------------------------------------

    def send(self, message: OutboundMessage) -> str:
        """发送出站消息，返回平台消息 id；失败抛 ChannelError。

        可从任意线程同步调用（主动聊天 / 外部装配方）；渠道内部消费
        事件流时的分段发送走 _push_text，不阻塞事件流。
        """
        return self._send_blocking(message)

    def _push_text(self, chat_id: str, text: str) -> None:
        """事件流消费路径的发送：事件循环线程内为异步派发，否则同步。"""
        message = OutboundMessage(chat_id=chat_id, text=text)
        if threading.get_ident() == self._thread_id:
            loop = self._loop
            if loop is None:  # pragma: no cover - 关闭竞态
                return
            loop.call_soon_threadsafe(
                lambda: loop.create_task(self._send_message(message))
            )
        else:
            self._send_blocking(message)

    def _push_emoji(self, chat_id: str, payload: Any) -> None:
        """表情包协议位：QQ 侧按图片消息发送。

        payload 兼容两种形态——契约 §3 的 (tag, path) 元组与 ai.AI 实际
        产出的 {'name': ..., 'path': ...} dict。解析不到本地路径则跳过
        （表情包是锦上添花，不因它阻断回复）。
        """
        path = ""
        if isinstance(payload, dict):
            path = str(payload.get("path") or "")
        elif isinstance(payload, (tuple, list)) and len(payload) >= 2:
            path = str(payload[1] or "")
        if not path:
            return
        message = OutboundMessage(chat_id=chat_id, text="", emoji_tag=path)
        if threading.get_ident() == self._thread_id:
            loop = self._loop
            if loop is None:  # pragma: no cover
                return
            loop.call_soon_threadsafe(
                lambda: loop.create_task(self._send_message(message))
            )
        else:
            self._send_blocking(message)

    def _send_blocking(self, message: OutboundMessage) -> str:
        loop = self._loop
        if loop is None or not loop.is_running() or self._closed:
            raise ChannelError("NapCat 渠道未连接（send 失败）")
        future = asyncio.run_coroutine_threadsafe(self._send_message(message), loop)
        try:
            return future.result(timeout=self._connect_timeout + 30.0)
        except ChannelError:
            raise
        except Exception as exc:
            raise ChannelError(f"NapCat 发送失败: {exc}") from exc

    async def _send_message(self, message: OutboundMessage) -> str:
        chat_type, target_id = self._split_chat_id(message.chat_id)
        if not message.text and not message.emoji_tag:
            raise ChannelError("OutboundMessage 无内容（text 与 emoji_tag 均为空）")

        if message.text:
            segments: List[Dict[str, Any]] = []
            if message.reply_to:
                segments.append({"type": "reply", "data": {"id": str(message.reply_to)}})
            segments.append({"type": "text", "data": {"text": message.text}})
            action = "send_group_msg" if chat_type == "group" else "send_private_msg"
            params: Dict[str, Any] = {"message": segments}
            if chat_type == "group":
                params["group_id"] = target_id
            else:
                params["user_id"] = target_id
            await self._send_action(action, params)

        # 表情包（预留协议位）：tag 为本地路径时按图片段补发一条。
        emoji_path = self._resolve_emoji(message.emoji_tag)
        if emoji_path:
            action = "send_group_msg" if chat_type == "group" else "send_private_msg"
            params = {"message": [{"type": "image", "data": {"file": emoji_path}}]}
            if chat_type == "group":
                params["group_id"] = target_id
            else:
                params["user_id"] = target_id
            return await self._send_action(action, params)
        return self._last_sent_message_id

    async def _send_action(
        self, action: str, params: Dict[str, Any], timeout: float = 30.0
    ) -> Dict[str, Any]:
        ws = self._ws
        if ws is None:
            raise ChannelError("NapCat 连接不可用")
        self._echo_seq += 1
        echo = f"castorice-{self._echo_seq}"
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._echo_futures[echo] = future
        frame = json.dumps(
            {"action": action, "params": params, "echo": echo},
            ensure_ascii=False,
        )
        await ws.send(frame)
        try:
            response = await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self._echo_futures.pop(echo, None)
            raise ChannelError(f"NapCat action 超时: {action}")
        if not isinstance(response, dict):
            raise ChannelError(f"NapCat action 回执格式异常: {action}")
        retcode = response.get("retcode")
        if retcode != 0:
            raise ChannelError(
                f"NapCat action 失败: {action} retcode={retcode} "
                f"wording={response.get('wording')!r} status={response.get('status')!r}"
            )
        data = response.get("data") or {}
        message_id = data.get("message_id")
        if message_id is not None:
            self._last_sent_message_id = str(message_id)
        return data

    def _resolve_emoji(self, emoji_tag: str) -> str:
        """表情包 tag -> 本地路径（预留协议位）。

        tag 本身是存在的本地文件路径时转 file URI；纯 tag 名解析依赖
        options.emoji_dir（表情包目录，未配置则返回空串跳过——表情包
        不阻断回复）。第三期视觉输入启用图片接收后可扩展远端 URL 直传。
        """
        tag = (emoji_tag or "").strip()
        if not tag:
            return ""
        candidate = Path(tag)
        if candidate.is_file():
            return candidate.resolve().as_uri()
        emoji_dir = str(self._options.get("emoji_dir") or "").strip()
        if emoji_dir:
            for suffix in ("", ".png", ".jpg", ".jpeg", ".gif"):
                path = Path(emoji_dir) / (tag + suffix)
                if path.is_file():
                    return path.resolve().as_uri()
        logger.debug("表情包 %r 无本地资源，跳过", tag)
        return ""

    def _split_chat_id(self, chat_id: str) -> Tuple[str, int]:
        """'private:12345' / 'group:678' / 裸数字 -> (类型, 数字 id)。"""
        text = normalize_target(chat_id)
        if ":" in text:
            chat_type, _, raw_id = text.partition(":")
        else:
            chat_type, raw_id = self._default_chat_type, text
        chat_type = chat_type.strip().lower()
        if chat_type not in ("private", "group"):
            raise ChannelError(
                f"chat_id 必须是 private:<id> / group:<id>，得到 {chat_id!r}"
            )
        raw_id = raw_id.strip()
        if not raw_id.isdigit():
            raise ChannelError(f"chat_id 的目标号必须是纯数字，得到 {chat_id!r}")
        return chat_type, int(raw_id)


def _segments_to_text(message: Any) -> str:
    """OneBot message 数组 / 字符串 -> 纯文本（尽力）。"""
    if message is None:
        return ""
    if isinstance(message, str):
        return message
    if isinstance(message, list):
        parts: List[str] = []
        for seg in message:
            if isinstance(seg, dict) and seg.get("type") == "text":
                parts.append(str((seg.get("data") or {}).get("text") or ""))
        return "".join(parts)
    return str(message)
