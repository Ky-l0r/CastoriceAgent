"""WebSocket 桥接层（第三期 t1）：Electron 壳 + Python 后端的事件通道。

定位（castorice/channels/base.py 的渠道思路在桌面侧的对应物）：
桥接层是**平级于 NapCat 渠道**的一个渠道实现——消费 ai.AI 冻结接口
（get_response_stream / set_mode / set_approval_responder），把恒 2 元组
事件流 (('text', str) / ('tool', dict) / ('emoji', dict) / ('done', str)，
done 恰好一次且最后) 按 JSON 帧转发给 Web 前端，同时把前端的输入与
控制操作翻译回 bot 调用。harness.py 签名零改动。

为什么自己起 WS 服务而不是挂进渠道组合根：桌面桥接没有「平台事件推送」
语义——前端是唯一的客户端，服务生命周期与 Python 后端进程绑定（Electron
主进程拉起后端并把端口 / token 写进环境变量）。协议形态（连接生命周期、
鉴权、事件流纪律）仍对齐渠道契约（docs/refactor/05-channel-contract.md）。

协议（JSON 文本帧，双向）：

    客户端 -> 服务端（每帧一个 JSON 对象）::

        {"type": "hello", "token": "..."}          # 首帧握手，超时 5s
        {"type": "chat", "text": "...",
         "images": ["C:/...png", ...]}              # 触发一轮对话
        {"type": "interrupt"}                       # 停止当前轮次
        {"type": "rpc", "id": "...", "method": "...",
         "params": {...}}                           # JSON-RPC 风格方法面

    服务端 -> 客户端::

        {"type": "hello_ok"}                        # 握手通过
        {"type": "event", "event": {"kind": "text", "payload": "..."}}
        {"type": "event", "event": {"kind": "done", "payload": "...",
                                    "ok": true}}
        {"type": "rpc_result", "id": "...", "result": {...}}
        {"type": "rpc_error", "id": "...",
         "error": {"code": ..., "message": ...}}

事件流纪律（红线 5.1 铁律 2）：done 恰好一次且在最后；一轮进行中拒绝新
的 chat（busy），interrupt 尽力打断（生成器迭代中断，done 以 ok=False
补发，流纪律不破）。事件只发给已握手连接；多个客户端并存时互不隔离
（桌面单用户模型，Electron 壳只拉一个渲染进程）。

RPC 方法面（fail-closed；未知方法 / 参数错误返回 rpc_error）：

* config.get    {key?}                  -> {"value": ...}
* config.set    {updates: {dotted: v}}  -> {"ok": true}（注释保持写回）
* memory.search {query, n_results?}     -> {"memories": [...]}
* memory.clear  {}                      -> {"ok": true}
* mode.list     {}                      -> {"modes": [...], "active": name}
* mode.switch   {mode}                  -> {"ok": true, "active": name}
* approval.pending {}                   -> {"pending": [...]}
* approval.resolve {request_id, decision} -> {"delivered": bool}
* notify.register {}                    -> {"ok": true}
* profile.get   {}                      -> {"profile": {...}}（用户档案白名单字段）
* profile.set   {updates: {field: v}}   -> {"ok": true, "profile": {...}}（未知字段拒绝）
* ui_settings.get {}                    -> {"settings": {...}}（ui_settings.json 内容）
* ui_settings.set {updates: {k: v}}     -> {"ok": true, "settings": {...}}（合并写回）

红线 D：本模块不 import ui / PySide6 / ai；对 bot 的引用全部鸭子类型，
引用的 castorice 子模块本身都是纯 Python。websockets 延迟导入（与
channels.napcat 同标准），import castorice.bridge 不需要任何第三方库。
鉴权：启动期 secrets.token_urlsafe 生成 token，hello 首帧校验（时间恒定
比较），失败即断开——不出现「先连上再慢慢授权」的窗口。

线程模型：start() 起一个后台线程跑 asyncio 事件循环；协议方法
（start / close / send_event）是同步调用；RPC 与 chat 处理走线程池，
保证事件循环在 bot 生成器阻塞期间仍能收心跳与新帧。
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import secrets
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from .channels.base import ChannelError
from .session import SessionStore

__all__ = [
    "BridgeServer",
    "WEB_BRIDGE_CAPABILITIES",
]

logger = logging.getLogger(__name__)

#: 渠道能力声明：桌面 Web 前端，Markdown / 图片输入 / 表情包全支持，
#: 主动聊天取决于调度器（Web 通知通道注册后即可先手）。
WEB_BRIDGE_CAPABILITIES = {
    "name": "web",
    "display_name": "Web (Electron)",
    "supports_markdown": True,
    "supports_image_input": True,
    "supports_emoji": True,
    "supports_proactive": True,
}

_LOOPBACK = "127.0.0.1"
_HANDSHAKE_TIMEOUT = 5.0
_MAX_FRAME_BYTES = 8 * 1024 * 1024   # 图片走 base64 内联（视觉输入协议位）
_SEEN_EVENT_MAX = 512

# JSON-RPC 错误码（沿 http 语义，-32700 段留给解析错误）
_RPC_PARSE_ERROR = -32700
_RPC_METHOD_NOT_FOUND = -32601
_RPC_INVALID_PARAMS = -32602
_RPC_INTERNAL_ERROR = -32603
_RPC_BUSY = -32001
_RPC_FORBIDDEN = -32002


def _require_websockets() -> Any:
    try:
        import websockets  # noqa: PLC0415 - 延迟导入，基础协议不硬依赖
    except ImportError as exc:  # pragma: no cover - 环境缺依赖
        raise ChannelError(
            "桥接层需要 websockets 库（pip install websockets）"
        ) from exc
    return websockets


def _jsonable(value: Any) -> Any:
    """把任意 Python 值压成 JSON 安全形态（不可序列化的降级为 str）。"""
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except (TypeError, ValueError):
        return str(value)


def request_payload_dict(request: Any) -> Dict[str, Any]:
    """把 PermissionRequest（或鸭子类型对象）压成 JSON dict。

    前端要展示工具名、参数、来源模式；dataclass 字段用
    dataclasses.asdict 拿（PermissionRequest 是 frozen dataclass），
    非字段属性跳过，字段值不可序列化时降级为 str——它跑在展示路径上，
    出问题只需要显示得含糊一点，而不是让审批流程崩掉。
    """
    payload: Dict[str, Any] = {}
    dataclass = getattr(type(request), "__dataclass_fields__", None)
    if dataclass:
        for name in dataclass:
            value = getattr(request, name, None)
            if isinstance(value, tuple):
                value = list(value)
            payload[name] = _jsonable(value)
        return payload
    # 非 dataclass 的鸭子类型对象：读已知字段名
    for name in ("tool", "mode", "call_id", "reason", "cwd"):
        value = getattr(request, name, None)
        if value is not None:
            payload[name] = _jsonable(value)
    args = getattr(request, "arguments", None)
    if args is not None:
        payload["arguments"] = _jsonable(dict(args))
    return payload


def _dotted_config_path(key: str) -> Tuple[str, ...]:
    """把 "a.b.c" 拆成 ('a', 'b', 'c')（空段拒绝）。"""
    parts = tuple(
        segment.strip() for segment in str(key or "").split(".") if segment.strip()
    )
    if not parts:
        raise ChannelError("配置键不能为空")
    return parts


# ----------------------------------------------------------------------------
# RPC 方法面（与 bot 的接缝全部鸭子类型；缺接口时按 fail-closed 报错）
# ----------------------------------------------------------------------------

class _RpcHandlers:
    """bridge RPC 方法面。每个方法接 bot 的鸭子类型接口。

    约定：handler 返回 JSON dict（result）；参数错误抛 ChannelError
    （映射 -32602）；bot 侧缺接口 / 接口异常抛 ChannelError（映射
    -32000 语义失败，消息可读）——绝不让一个 RPC 把连接打崩。
    """

    def __init__(self, bot: Any, owner: Any = None) -> None:
        self._bot = bot
        #: 桥接服务器自身（notify.register 时把 WebNotifier 绑到它）。
        self._owner = owner

    # ---- config（复用 ConfigManager：get 读内存快照，set 注释保持写回）----

    def config_get(self, params: Dict[str, Any]) -> Dict[str, Any]:
        manager = getattr(self._bot, "config_manager", None)
        if manager is None:
            raise ChannelError("bot 没有 config_manager（无法读取配置）")
        key = str(params.get("key") or "").strip()
        if key:
            missing = object()
            value = manager.get(key, missing)
            if value is missing:
                raise ChannelError("配置键不存在: " + repr(key))
            return {"value": _jsonable(value)}
        return {"value": _jsonable(manager.config)}

    def config_set(self, params: Dict[str, Any]) -> Dict[str, Any]:
        manager = getattr(self._bot, "config_manager", None)
        if manager is None:
            raise ChannelError("bot 没有 config_manager（无法写回配置）")
        updates = params.get("updates")
        if not isinstance(updates, dict) or not updates:
            raise ChannelError("config.set 需要 updates 映射（dotted 路径 -> 值）")
        prepared: Dict[Tuple[str, ...], Any] = {}
        for key, value in updates.items():
            prepared[_dotted_config_path(key)] = value
        try:
            manager.set_values(prepared)
        except FileNotFoundError as exc:
            raise ChannelError("配置文件不存在: " + str(exc)) from exc
        except Exception as exc:
            raise ChannelError("配置写回失败: " + str(exc)) from exc
        return {"ok": True}

    # ---- memory（ai.MemoryManager 同步接口）----

    def memory_search(self, params: Dict[str, Any]) -> Dict[str, Any]:
        query = str(params.get("query") or "").strip()
        if not query:
            raise ChannelError("memory.search 需要 query（检索关键词）")
        try:
            n_results = int(params.get("n_results") or 5)
        except (TypeError, ValueError):
            raise ChannelError("n_results 必须是整数")
        manager = getattr(self._bot, "memory_manager", None)
        search = getattr(manager, "search_memories", None) if manager else None
        if not callable(search):
            raise ChannelError("bot 没有 memory_manager.search_memories（无法检索记忆）")
        if n_results < 1:
            raise ChannelError("n_results 不能小于 1")
        try:
            memories = list(search(query, n_results=n_results))
        except Exception as exc:
            raise ChannelError("记忆检索失败: " + str(exc)) from exc
        return {"memories": [_jsonable(m) for m in memories]}

    def memory_clear(self, params: Dict[str, Any]) -> Dict[str, Any]:
        clear = getattr(self._bot, "clear_memories", None)
        if not callable(clear):
            raise ChannelError("bot 没有 clear_memories（无法清空记忆）")
        try:
            clear()
        except Exception as exc:
            raise ChannelError("记忆清空失败: " + str(exc)) from exc
        return {"ok": True}

    # ---- mode（ai.AI.available_modes / set_mode）----

    def _require_modes(self) -> Tuple[List[Dict[str, Any]], str]:
        names: List[str] = []
        try:
            resolve = getattr(self._bot, "_mode_registry", None)
            resolve = getattr(resolve, "names", None)
            if callable(resolve):
                names = [str(n) for n in resolve()]
        except Exception:
            names = []
        if not names:
            raise ChannelError("bot 没有可用的模式注册表（无法枚举模式）")
        return names

    def mode_list(self, params: Dict[str, Any]) -> Dict[str, Any]:
        available = getattr(self._bot, "available_modes", None)
        if callable(available):
            try:
                modes = [_jsonable(dict(m)) for m in available()]
            except Exception as exc:
                raise ChannelError("模式枚举失败: " + str(exc)) from exc
        else:
            # 演示 bot / 旧实现没有 available_modes：退化成裸名单
            names = self._known_mode_names()
            if names is None:
                raise ChannelError("bot 没有可用的模式注册表（无法枚举模式）")
            modes = [{"name": name} for name in names]
        active = ""
        try:
            active = str(getattr(self._bot, "mode", "") or "")
        except Exception:
            active = ""
        return {"modes": modes, "active": active}

    def _known_mode_names(self) -> Optional[List[str]]:
        """合法模式名单：available_modes 优先，退回 _mode_registry.names()。

        两处都没有（演示 bot）时返回 None——mode.switch 跳过预校验，
        交给 set_mode 自己的回退语义处理。
        """
        available = getattr(self._bot, "available_modes", None)
        if callable(available):
            try:
                names = [str(m.get("name") or "") for m in available()
                         if isinstance(m, dict) and m.get("name")]
                if names:
                    return names
            except Exception:
                pass
        try:
            registry_names = getattr(self._bot, "_mode_registry", None)
            names_fn = getattr(registry_names, "names", None)
            if callable(names_fn):
                got = [str(n) for n in names_fn()]
                if got:
                    return got
        except Exception:
            pass
        return None

    def mode_switch(self, params: Dict[str, Any]) -> Dict[str, Any]:
        mode = str(params.get("mode") or "").strip()
        if not mode:
            raise ChannelError("mode.switch 需要 mode（目标模式名）")
        # 先校验目标合法（未知名字直接拒绝，而不是依赖 set_mode 静默回退
        # 到 chat——回退语义对界面是误导，界面上应该显式报错）。
        names = self._known_mode_names()
        if names is not None and mode not in names:
            raise ChannelError(
                "未知模式 " + repr(mode) + "，可用: " + repr(sorted(names)))
        setter = getattr(self._bot, "set_mode", None)
        if not callable(setter):
            raise ChannelError("bot 没有 set_mode（无法切换模式）")
        try:
            setter(mode)
        except Exception as exc:
            raise ChannelError("模式切换失败: " + str(exc)) from exc
        return {"ok": True, "active": mode}

    # ---- 会话列表（第四期：侧栏「最近对话」的数据源）----

    def sessions_list(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """列出当前模式的历史会话（按修改时间倒序）。

        只读会话文件，不打开 SessionStore（打开会触发单写者锁，
        把正在使用的会话锁死）。标题取会话第一条 user 事件的文本前缀，
        读不到就退化为「新对话」——列表永远可显示，不因脏文件报错。
        """
        modes_root = self._sessions_root()
        if modes_root is None:
            return {"sessions": [], "active": ""}

        mode = str(params.get("mode") or getattr(self._bot, "mode", "") or "chat")
        try:
            paths = SessionStore.list_sessions(modes_root, mode)
        except Exception as exc:
            raise ChannelError("会话列表读取失败: " + str(exc)) from exc

        sessions: List[Dict[str, Any]] = []
        for path in paths[:50]:
            try:
                stat = path.stat()
            except OSError:
                continue
            sessions.append({
                "id": path.stem,
                "mode": mode,
                "title": _session_title(path),
                "updatedAt": int(stat.st_mtime * 1000),
                "messageCount": _session_event_count(path),
            })
        active = ""
        try:
            active = str(getattr(getattr(self._bot, "_session", None), "session_id", "") or "")
        except Exception:
            active = ""
        return {"sessions": sessions, "active": active}

    def _sessions_root(self) -> Optional[Path]:
        """会话根目录：优先取 bot 已装配的会话根的父目录。"""
        store = getattr(self._bot, "_session", None)
        root = getattr(store, "root", None)
        if root:
            return Path(root)
        # bot 有 _sessions 映射（模式 -> SessionStore）时同样可用
        mapping = getattr(self._bot, "_sessions", None)
        if isinstance(mapping, dict):
            for candidate in mapping.values():
                root = getattr(candidate, "root", None)
                if root:
                    return Path(root)
        return None

    # ---- approval（ApprovalBroker defer/resolve 的远端入口）----

    def _require_broker(self) -> Any:
        broker = getattr(self._bot, "approval_broker", None)
        if broker is None or not callable(getattr(broker, "resolve", None)):
            raise ChannelError("bot 没有 approval_broker（审批通道未接线）")
        return broker

    def approval_pending(self, params: Dict[str, Any]) -> Dict[str, Any]:
        broker = self._require_broker()
        ids = list(broker.pending_ids())
        return {"pending": ids}

    def approval_resolve(self, params: Dict[str, Any]) -> Dict[str, Any]:
        broker = self._require_broker()
        request_id = str(params.get("request_id") or "").strip()
        if not request_id:
            raise ChannelError("approval.resolve 需要 request_id")
        decision = str(params.get("decision") or "").strip().lower()
        if decision not in ("allow", "deny"):
            raise ChannelError(
                "decision 必须是 allow | deny（一次性授权，无 allow-always）")
        delivered = bool(broker.resolve(request_id, decision))
        if not delivered:
            raise ChannelError("审批请求不存在或已结束: " + repr(request_id))
        return {"delivered": True}

    # ---- notify（DesktopNotifier 的 Web 实现，注册进进程级注册表）----

    def notify_register(self, params: Dict[str, Any]) -> Dict[str, Any]:
        from .notify import (  # noqa: PLC0415 - 延迟导入，保持 import 轻量
            WebNotifier,
            get_notifier_registry,
        )

        notifier = WebNotifier()
        notifier.bind(self._owner)
        get_notifier_registry().register("web", notifier, default=True)
        return {"ok": True}

    # ---- profile（用户档案：castorice.profile 的 JSON 落盘读写） ----

    def profile_get(self, params: Dict[str, Any]) -> Dict[str, Any]:
        from .profile import PROFILE_FIELDS, UserProfile  # noqa: PLC0415

        profile = UserProfile.load()
        # 只吐白名单字段：文件里可能有历史遗留键，不向前端扩散
        return {"profile": {k: profile.get(k) for k in PROFILE_FIELDS}}

    def profile_set(self, params: Dict[str, Any]) -> Dict[str, Any]:
        from .profile import PROFILE_FIELDS, UserProfile  # noqa: PLC0415

        updates = params.get("updates")
        if not isinstance(updates, dict) or not updates:
            raise ChannelError("profile.set 需要 updates 映射（字段 -> 值）")
        unknown = [k for k in updates if k not in PROFILE_FIELDS]
        if unknown:
            raise ChannelError(
                "未知档案字段: " + repr(sorted(unknown))
                + "，可用: " + repr(list(PROFILE_FIELDS)))
        # 值统一压成 str / None（None = 清空该字段；档案语义是文本事实）
        clean: Dict[str, Any] = {}
        for key, value in updates.items():
            if value is None:
                clean[key] = None
            else:
                text = str(value)
                if len(text) > 200:
                    raise ChannelError(
                        "档案字段 " + repr(key) + " 过长（上限 200 字符）")
                clean[key] = text
        UserProfile.save_values(clean)
        return {"ok": True, "profile": {
            k: v for k, v in UserProfile.load().items()
            if k in PROFILE_FIELDS}}

    # ---- ui_settings（外观偏好：ui_settings.json 的 Web 侧读写） ----

    def _ui_settings_loader(self):
        """UiSettings.load 的解析：优先复用 ui.py，Qt 不可用时走本地实现。

        第三期 Qt 退役后 ui.py 会消失，这里提前把读写做成「有 ui 用 ui、
        没 ui 用本地等价实现」——键名 / 容错语义与 ui.py:UiSettings 一致
        （读-改-写合并、坏文件返回空 dict），向后兼容老用户的
        ui_settings.json（theme / ui_mode 键继续生效）。
        """
        loader = getattr(self._bot, "_ui_settings_load", None)
        if callable(loader):
            return loader
        try:
            import ui  # noqa: PLC0415 - 仅在 Qt 环境可用时复用
            return ui.UiSettings.load
        except Exception:
            return None

    def ui_settings_get(self, params: Dict[str, Any]) -> Dict[str, Any]:
        loader = self._ui_settings_loader()
        data = loader() if loader else _load_ui_settings_file()
        return {"settings": data if isinstance(data, dict) else {}}

    def ui_settings_set(self, params: Dict[str, Any]) -> Dict[str, Any]:
        updates = params.get("updates")
        if not isinstance(updates, dict) or not updates:
            raise ChannelError("ui_settings.set 需要 updates 映射（键 -> 值）")
        clean = {str(k): v for k, v in updates.items()}
        saver = getattr(self._bot, "_ui_settings_save", None)
        if not callable(saver):
            try:
                import ui  # noqa: PLC0415
                saver = ui.UiSettings.save_values
            except Exception:
                saver = _save_ui_settings_file
        saver(clean)
        return {"ok": True, "settings": (
            self._ui_settings_loader()() if self._ui_settings_loader()
            else _load_ui_settings_file())}


def _session_title(path: Path, limit: int = 32) -> str:
    """会话标题：第一条 user 事件的正文前缀（读不到就返回「新对话」）。

    JSONL 是 append-only，顺序读第一行即可，不需要完整解析文件。
    """
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except Exception:
                    continue
                if not isinstance(event, dict) or event.get("kind") != "user":
                    continue
                payload = event.get("payload") or {}
                text = ""
                if isinstance(payload, dict):
                    text = str(payload.get("text") or payload.get("content") or "")
                text = " ".join(text.split())
                if not text:
                    continue
                return text[:limit] + ("…" if len(text) > limit else "")
    except Exception:
        pass
    return "新对话"


def _session_event_count(path: Path) -> int:
    """会话事件条数（脏行跳过，读失败按 0 计）。"""
    count = 0
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    count += 1
    except Exception:
        return 0
    return count


def _load_ui_settings_file() -> Dict[str, Any]:
    """ui_settings.json 的兜底读取（ui.py 不可用时；语义对齐 UiSettings.load）。"""
    import json  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    override = os.environ.get("CASTORICE_UI_SETTINGS")
    path = (
        Path(override).expanduser() if override
        else Path(__file__).resolve().parent.parent / "ui_settings.json"
    )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _save_ui_settings_file(values: Dict[str, Any]) -> None:
    """ui_settings.json 的兜底写入（读-改-写合并，坏文件静默降级）。"""
    data = _load_ui_settings_file()
    data.update(values)
    override = os.environ.get("CASTORICE_UI_SETTINGS")
    path = (
        Path(override).expanduser() if override
        else Path(__file__).resolve().parent.parent / "ui_settings.json"
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


# ----------------------------------------------------------------------------
# DesktopNotifier 的 Web 实现（注册进 NotifierRegistry 供调度器使用）
# ----------------------------------------------------------------------------

# ----------------------------------------------------------------------------
# 进程级桥接服务器登记（WebNotifier 的默认绑定目标）
# ----------------------------------------------------------------------------

_ACTIVE_SERVER: Optional["BridgeServer"] = None
_ACTIVE_SERVER_LOCK = threading.Lock()


def _set_active_server(server: Optional["BridgeServer"]) -> None:
    global _ACTIVE_SERVER
    with _ACTIVE_SERVER_LOCK:
        _ACTIVE_SERVER = server


def active_server() -> Optional["BridgeServer"]:
    """进程内最近启动的桥接服务器（未启动返回 None）。

    castorice.notify.WebNotifier 的默认绑定目标：notify.py 不 import
    bridge（无环依赖），运行期通过本访问器解析。
    """
    with _ACTIVE_SERVER_LOCK:
        return _ACTIVE_SERVER


# ----------------------------------------------------------------------------
# 桥接服务器
# ----------------------------------------------------------------------------

@dataclass
class _BridgeState:
    """服务器共享可变状态（_lock 保护）。"""
    closed: bool = False
    busy: bool = False            # 当前是否有一轮对话在跑
    generation: int = 0           # 轮次代号（interrupt 递增）
    loop: Optional[asyncio.AbstractEventLoop] = None
    clients: Dict[Any, Dict[str, Any]] = field(default_factory=dict)
    #: 广播发送任务（引用住避免被 GC；done 回调自动移除）。
    sending: set = field(default_factory=set)


class BridgeServer:
    """本地 WebSocket 桥接服务（Electron 壳 <-> Python 后端）。

    生命周期::

        server = BridgeServer(bot)          # bot: ai.AI（鸭子类型）
        token = server.token
        port = server.start()               # 阻塞到监听建立，失败抛 ChannelError
        ...
        server.close()                      # 幂等；close 后允许重新 start

    事件转发：ai.AI.get_response_stream 的恒 2 元组事件流原样翻译成
    {"type": "event", "event": {"kind": ..., "payload": ...}} 帧；
    done 恰好一次且在最后（流纪律由 bot 保证，桥不做改写——
    与 NapCatChannel 对事件流的处理同构）。
    """

    def __init__(
        self,
        bot: Any,
        *,
        host: str = _LOOPBACK,
        port: int = 0,
        token: Optional[str] = None,
    ) -> None:
        self._bot = bot
        self._host = str(host or _LOOPBACK)
        self._port = int(port or 0)
        #: 启动期生成的鉴权 token（hello 首帧校验）。
        self._token = str(token or secrets.token_urlsafe(24))
        self._state = _BridgeState()
        self._lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._start_error: str = ""
        self._actual_port: int = 0
        self._rpc = _RpcHandlers(bot, owner=self)
        self._rpc_methods: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {
            "config.get": self._rpc.config_get,
            "config.set": self._rpc.config_set,
            "memory.search": self._rpc.memory_search,
            "memory.clear": self._rpc.memory_clear,
            "mode.list": self._rpc.mode_list,
            "mode.switch": self._rpc.mode_switch,
            "sessions.list": self._rpc.sessions_list,
            "approval.pending": self._rpc.approval_pending,
            "approval.resolve": self._rpc.approval_resolve,
            "notify.register": self._rpc.notify_register,
            "profile.get": self._rpc.profile_get,
            "profile.set": self._rpc.profile_set,
            "ui_settings.get": self._rpc.ui_settings_get,
            "ui_settings.set": self._rpc.ui_settings_set,
        }

    # ---- 属性 ----

    @property
    def token(self) -> str:
        """握手鉴权 token（组合根负责交给 Electron 主进程）。"""
        return self._token

    @property
    def port(self) -> int:
        """实际监听端口（start 之前为 0）。"""
        return self._actual_port

    @property
    def host(self) -> str:
        return self._host

    @property
    def capabilities(self) -> Dict[str, Any]:
        return dict(WEB_BRIDGE_CAPABILITIES)

    # ---- 生命周期 ----

    def start(self) -> int:
        """启动 WS 监听（阻塞到监听建立），返回实际端口。

        重复 start 幂等（已运行时直接返回当前端口）；绑定失败抛
        ChannelError（fail-fast，不静默降级成「看起来在线但连不上」）。
        """
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                if not self._ready.wait(timeout=10.0):
                    raise ChannelError("桥接服务器启动超时")
                if self._start_error:
                    raise ChannelError(self._start_error)
                return self._actual_port
            self._closed_reset()
            self._ready.clear()
            self._start_error = ""
            self._thread = threading.Thread(
                target=self._thread_main, name="web-bridge", daemon=True
            )
            self._thread.start()
        if not self._ready.wait(timeout=10.0):
            self.close()
            raise ChannelError("桥接服务器启动超时")
        if self._start_error:
            raise ChannelError(self._start_error)
        return self._actual_port

    def _closed_reset(self) -> None:
        self._state.closed = False
        self._state.busy = False
        self._state.generation = 0
        self._state.clients.clear()

    def close(self) -> None:
        """停止服务并释放资源。幂等：close 后再 close 不抛。"""
        with self._lock:
            state = self._state
            if state.closed:
                return
            state.closed = True
            state.generation += 1        # 打断在跑的轮次
            loop = state.loop
            thread = self._thread
        _set_active_server(None)
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(self._shutdown_loop)
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=5.0)

    def _shutdown_loop(self) -> None:
        # 事件循环线程内执行：不留 pending task 地停循环。
        for task in asyncio.all_tasks():
            task.cancel()
        asyncio.get_running_loop().stop()

    # ---- 事件转发（同步入口，供通知 / 外部模块调用）----

    def send_event(self, kind: str, payload: Any) -> bool:
        """向所有已握手客户端广播一条 event 帧；返回是否至少送达一个。

        恒 2 元组事件（chat 流）走 _send_tuple_event；通知等附加事件
        直接用本方法。没有客户端时返回 False（软失败，不抛）。
        """
        frame = {
            "type": "event",
            "event": {"kind": str(kind), "payload": _jsonable(payload)},
        }
        return self._broadcast(frame)

    def _send_tuple_event(self, kind: str, payload: Any) -> None:
        """把一个 (kind, payload) 元组事件翻译成 event 帧（线程池内）。"""
        self.send_event(kind, payload)

    def _broadcast(self, frame: Dict[str, Any]) -> bool:
        """把一帧 JSON 广播给全部已握手客户端（尽力而为，不等待发送）。"""
        loop = self._state.loop
        if loop is None or not loop.is_running():
            return False
        text = json.dumps(frame, ensure_ascii=False, default=str)
        loop.call_soon_threadsafe(self._do_broadcast, text)
        # 是否真的有接收者：看握手客户端计数（发送本身是异步尽力而为）。
        return any(
            meta.get("handshaked") for meta in list(self._state.clients.values())
        )

    def _do_broadcast(self, text: str) -> None:
        """事件循环线程内执行的实际发送（每个客户端一个受管任务）。"""
        for ws, meta in list(self._state.clients.items()):
            if not meta.get("handshaked"):
                continue
            task = asyncio.ensure_future(self._safe_send(ws, text))
            # 引用住避免任务被 GC；结束后自动清出集合。
            self._state.sending.add(task)
            task.add_done_callback(self._state.sending.discard)

    # ---- 后台线程 / 事件循环 ----

    def _thread_main(self) -> None:
        websockets = _require_websockets()
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._state.loop = loop
        try:
            loop.run_until_complete(self._serve(websockets))
        except Exception as exc:  # pragma: no cover - 兜底，不让线程裸崩
            logger.error("桥接服务器线程异常退出: %s", exc)
            if not self._ready.is_set():
                self._start_error = "桥接服务器启动失败: " + str(exc)
                self._ready.set()
        finally:
            with self._lock:
                self._state.loop = None
            loop.close()

    async def _serve(self, websockets: Any) -> None:
        handler = self._handle_connection
        server = await websockets.serve(
            handler,
            self._host,
            self._port,
            max_size=_MAX_FRAME_BYTES,
        )
        self._actual_port = server.sockets[0].getsockname()[1] if server.sockets else self._port
        _set_active_server(self)
        self._ready.set()
        logger.info("Web 桥接已监听: ws://%s:%s", self._host, self._actual_port)
        await server.wait_closed()

    # ---- 连接处理 ----

    async def _handle_connection(self, ws: Any) -> None:
        """单个客户端连接：握手 -> 收发循环。"""
        meta: Dict[str, Any] = {"handshaked": False}
        self._state.clients[ws] = meta
        try:
            # 1) 握手：首帧必须是带 token 的 hello
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=_HANDSHAKE_TIMEOUT)
            except asyncio.TimeoutError:
                await self._close_with(ws, "握手超时")
                return
            if not self._check_hello(raw):
                await self._close_with(ws, "鉴权失败")
                return
            meta["handshaked"] = True
            await ws.send(json.dumps({"type": "hello_ok"}, ensure_ascii=False))

            # 2) 收发循环
            async for raw in ws:
                await self._handle_frame(ws, raw)
        except Exception:
            pass  # 连接断开 / 客户端关闭：清理即可
        finally:
            self._state.clients.pop(ws, None)

    async def _close_with(self, ws: Any, reason: str) -> None:
        try:
            await ws.close(code=4001, reason=reason)
        except Exception:  # pragma: no cover
            pass

    def _check_hello(self, raw: Any) -> bool:
        """校验 hello 帧；时间恒定比较 token，不因长度差异提前返回。"""
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            return False
        if not isinstance(payload, dict) or payload.get("type") != "hello":
            return False
        supplied = str(payload.get("token") or "")
        expected = self._token
        return secrets.compare_digest(
            supplied.encode("utf-8"), expected.encode("utf-8"))

    # ---- 帧分发 ----

    async def _handle_frame(self, ws: Any, raw: Any) -> None:
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            await self._send_rpc_error(ws, "", _RPC_PARSE_ERROR, "帧不是合法 JSON")
            return
        if not isinstance(payload, dict):
            await self._send_rpc_error(ws, "", _RPC_PARSE_ERROR, "帧必须是 JSON 对象")
            return

        frame_type = payload.get("type")
        if frame_type == "chat":
            # chat 是长耗时操作（LLM 流式回复）：spawn 独立 task 处理，
            # 不阻塞本连接的收发循环——否则 interrupt / RPC 帧会排在
            # 整轮对话之后才被处理，打断永远迟到。
            task = asyncio.ensure_future(self._handle_chat(ws, payload))
            self._state.sending.add(task)
            task.add_done_callback(self._state.sending.discard)
        elif frame_type == "interrupt":
            self._handle_interrupt()
        elif frame_type == "rpc":
            await self._handle_rpc(ws, payload)
        elif frame_type == "ping":
            await self._safe_send(ws, json.dumps(
                {"type": "pong", "ts": payload.get("ts", "")},
                ensure_ascii=False))
        else:
            await self._send_rpc_error(
                ws, str(payload.get("id") or ""),
                _RPC_METHOD_NOT_FOUND, "未知帧类型: " + repr(frame_type))

    # ---- chat（事件流） ----

    async def _handle_chat(self, ws: Any, payload: Dict[str, Any]) -> None:
        text = str(payload.get("text") or "")
        images = payload.get("images") or []
        if not isinstance(images, list):
            images = []
        stream_factory = getattr(self._bot, "get_response_stream", None)
        if not callable(stream_factory):
            await self._send_rpc_error(ws, "", _RPC_METHOD_NOT_FOUND,
                                       "bot 没有 get_response_stream（无法对话）")
            return

        with self._lock:
            if self._state.busy:
                await self._send_rpc_error(
                    ws, "", _RPC_BUSY, "已有对话在进行（先 interrupt 或等待 done）")
                return
            self._state.busy = True
            self._state.generation += 1
            generation = self._state.generation

        loop = asyncio.get_running_loop()
        try:
            # bot 生成器在线程池里消费（可能长时间阻塞 LLM 请求），
            # 事件经广播帧发给前端；done 语义由 _consume_stream 的出口保证。
            done_sent = await loop.run_in_executor(
                None, self._consume_stream,
                stream_factory, text, list(images), generation)
        finally:
            with self._lock:
                still_mine = self._state.generation == generation
                self._state.busy = False
            # 兜底：轮次仍是本回合（未被打断 / 未因异常封口）且流没产 done
            # 时，补一条 ok=True 的 done——done 恰好一次且最后（红线 5.1）。
            # interrupt 与 _stream_failed 路径已各自封口（generation+1），
            # 走不到这里，不会出现第二条 done。
            if still_mine and not done_sent:
                self.send_event("done", {"ok": True, "payload": ""})

    def _consume_stream(self, stream_factory: Callable, text: str,
                        images: List[str], generation: int) -> bool:
        """线程池内消费 bot 事件流并逐事件广播；返回流是否产出了 done。

        bot 契约保证 done 恰好一次且最后（t19 实测冻结）；本方法同时
        兼容「迭代自然耗尽却没产 done」的越约 bot——返回 False 让
        _handle_chat 补发兜底 done，前端永远恰好收到一条收尾帧。
        """
        try:
            stream = stream_factory(text, images)
        except Exception as exc:
            self._stream_failed(generation, exc)
            return True   # _stream_failed 已补发 ok=False 的 done
        try:
            for kind, event_payload in stream:
                if self._state.closed or self._state.generation != generation:
                    # close / interrupt：退出迭代即打断生成器（生成器内的
                    # finally 完成收尾），后续事件一律不再发。
                    stream.close()
                    return True   # 打断路径已补发 done(ok=False)
                if not isinstance(kind, str):
                    continue
                self.send_event(kind, event_payload)
                if kind == "done":
                    with self._lock:
                        self._state.busy = False
                        self._state.generation += 1   # 正常结束，轮次封口
                    return True
            return False   # 流耗尽但没产 done：由 _handle_chat 补发兜底
        except Exception as exc:
            self._stream_failed(generation, exc)
            return True
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:  # pragma: no cover - 生成器收尾容错
                    pass

    def _stream_failed(self, generation: int, exc: Exception) -> None:
        """流中途异常：补一条 ok=False 的 done 帧（done 恰好一次且最后）。"""
        with self._lock:
            if self._state.generation != generation:
                return
            self._state.busy = False
            self._state.generation += 1
        message = str(exc) or type(exc).__name__
        self.send_event("done", {"ok": False, "error": message, "payload": ""})

    def _handle_interrupt(self) -> None:
        """打断当前轮次：generation 递增使流消费退出，busy 同步复位。

        busy 在这里（而不是等 _consume_stream 退出）复位：流消费线程
        可能还阻塞在 bot 生成器内部（慢 LLM），前端收到 done(ok=False)
        后必须能立即发起下一轮，不能等旧生成器自然睡醒。
        """
        with self._lock:
            if not self._state.busy:
                return
            self._state.generation += 1
            self._state.busy = False
        self.send_event("done", {"ok": False, "error": "已打断", "payload": ""})

    # ---- RPC ----

    async def _handle_rpc(self, ws: Any, payload: Dict[str, Any]) -> None:
        rpc_id = payload.get("id")
        method = str(payload.get("method") or "")
        params = payload.get("params")
        if not isinstance(params, dict):
            params = {}
        if not method:
            await self._send_rpc_error(ws, rpc_id, _RPC_INVALID_PARAMS,
                                       "缺少 method")
            return
        handler = self._rpc_methods.get(method)
        if handler is None:
            await self._send_rpc_error(ws, rpc_id, _RPC_METHOD_NOT_FOUND,
                                       "未知方法: " + repr(method))
            return
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(None, handler, dict(params))
        except ChannelError as exc:
            await self._send_rpc_error(ws, rpc_id, _RPC_INVALID_PARAMS, str(exc))
            return
        except Exception as exc:
            logger.warning("RPC %s 执行失败: %s", method, exc)
            await self._send_rpc_error(ws, rpc_id, _RPC_INTERNAL_ERROR,
                                       "RPC 执行失败: " + str(exc))
            return
        await self._safe_send(ws, json.dumps(
            {"type": "rpc_result", "id": rpc_id, "result": result},
            ensure_ascii=False, default=str))

    async def _send_rpc_error(self, ws: Any, rpc_id: Any,
                              code: int, message: str) -> None:
        await self._safe_send(ws, json.dumps(
            {"type": "rpc_error", "id": rpc_id,
             "error": {"code": code, "message": message}},
            ensure_ascii=False))

    async def _safe_send(self, ws: Any, text: str) -> None:
        try:
            await ws.send(text)
        except Exception:  # pragma: no cover - 连接已断
            pass
