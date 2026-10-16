"""会话事件日志、检查点与前端投影。

本模块是 DeepSeek Harness 三块机制在 Python 侧的落点：

* dsh-session / dsh-session-persistence（append-only 日志 + 从日志派生模型历史）
  → SessionStore.events() / derive_messages()（可移植清单 P13）；
* dsh-session-checkpoint-policy（三处语义检查点 + fail-closed）
  → SessionStore.checkpoint() / flush()（P11 / P12）；flush 失败抛
  CheckpointFailed，调用方必须阻止下游副作用；
* dsh-session-projection（客户端只读快照 + 变更通知，不重放日志）
  → SessionProjection（P18）；
* dsh-agent-loop 的失败步骤结果补全（TOOL_OUTCOME_UNKNOWN，不自动重试）
  → SessionStore.load() 的收尾（P14）。

落盘布局：sessions/<mode>/<session_id>.jsonl，**按模式隔离**（t7 的会话隔离
直接依赖它）。每行一个 JSON 对象并带 "v": 1；读到未知版本**拒绝加载并给出可读
错误**，不做静默降级（R16）。

设计约束（红线 D）：本模块只依赖标准库，不得 import ui / PySide6 / ai。
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional, Tuple

FORMAT_VERSION = 1

#: 事件 kind 词汇（对齐 02-architecture.md 第 2.2 节，另加 stop 以便持久化终止原因）
KIND_USER = "user"
KIND_ASSISTANT = "assistant"
KIND_TOOL_CALL = "tool_call"
KIND_TOOL_RESULT = "tool_result"
KIND_CONTEXT = "context"
KIND_SANDBOX = "sandbox"
KIND_MODE = "mode"
KIND_PROACTIVE = "proactive"
KIND_CHECKPOINT = "checkpoint"
KIND_STOP = "stop"

ALL_KINDS = frozenset({
    KIND_USER, KIND_ASSISTANT, KIND_TOOL_CALL, KIND_TOOL_RESULT, KIND_CONTEXT,
    KIND_SANDBOX, KIND_MODE, KIND_PROACTIVE, KIND_CHECKPOINT, KIND_STOP,
})

#: 崩溃/取消后补全的占位结果（对齐 DSH 的 TOOL_OUTCOME_UNKNOWN）
TOOL_OUTCOME_UNKNOWN = "TOOL_OUTCOME_UNKNOWN"
TOOL_NOT_STARTED = "TOOL_NOT_STARTED"
ABORTED_BEFORE_DISPATCH = "ABORTED_BEFORE_DISPATCH"

TOOL_OUTCOME_UNKNOWN_TEXT = "错误: 工具结果未知（TOOL_OUTCOME_UNKNOWN），未自动重试"


# ----------------------------------------------------------------------------
# 异常
# ----------------------------------------------------------------------------

class SessionError(RuntimeError):
    """会话层错误基类。"""


class SessionInUseError(SessionError):
    """同一 session_id 在本进程内已有写入方（对齐 dsh-session-persistence）。"""


class SessionFormatError(SessionError):
    """会话文件格式不受支持（未知版本 / 非对象行 / kind 非法）。"""


class CheckpointFailed(SessionError):
    """检查点（flush）失败。

    这是 fail-closed 的信号：捕获方**必须阻止下游副作用**（P12）。
    """

    def __init__(self, message: str, *, reason: str = "") -> None:
        super().__init__(message)
        self.reason = reason


# ----------------------------------------------------------------------------
# 头与事件
# ----------------------------------------------------------------------------

def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def new_session_id() -> str:
    """形如 20261016-153012-ab12cd34 的会话 id（时间前缀便于人读与排序）。"""
    return datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]


@dataclass(frozen=True)
class SessionHeader:
    """会话头：模式与**不可变**的工作目录（对齐 DSH 的 SessionHeader.cwd）。"""

    session_id: str
    mode: str
    cwd: Optional[str] = None
    created_at: str = field(default_factory=_now)
    format_version: int = FORMAT_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "mode": self.mode,
            "cwd": self.cwd,
            "created_at": self.created_at,
            "format_version": self.format_version,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SessionHeader":
        return cls(
            session_id=str(data.get("session_id") or new_session_id()),
            mode=str(data.get("mode") or "chat"),
            cwd=data.get("cwd") or None,
            created_at=str(data.get("created_at") or _now()),
            format_version=int(data.get("format_version") or FORMAT_VERSION),
        )


@dataclass(frozen=True)
class SessionEvent:
    """append-only 事件。seq 从 1 开始单调递增。"""

    seq: int
    kind: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    at: str = field(default_factory=_now)
    format_version: int = FORMAT_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {
            "v": self.format_version,
            "seq": self.seq,
            "kind": self.kind,
            "payload": dict(self.payload),
            "at": self.at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SessionEvent":
        version = data.get("v", 1)
        if not isinstance(version, int):
            raise SessionFormatError("会话事件版本号非法: " + repr(version))
        if version > FORMAT_VERSION:
            raise SessionFormatError(
                "不支持的会话格式版本 v=" + str(version)
                + "（本程序只支持 v<=" + str(FORMAT_VERSION) + "）；"
                "为避免静默降级，拒绝加载该会话"
            )
        kind = data.get("kind")
        if not isinstance(kind, str) or not kind:
            raise SessionFormatError("会话事件缺少 kind")
        return cls(
            seq=int(data.get("seq") or 0),
            kind=kind,
            payload=dict(data.get("payload") or {}),
            at=str(data.get("at") or ""),
            format_version=version,
        )


# ----------------------------------------------------------------------------
# 单写者登记
# ----------------------------------------------------------------------------

_WRITER_LOCK = threading.Lock()
_ACTIVE_WRITERS: set = set()


def _writer_key(path: Path) -> str:
    return os.path.normcase(str(path.resolve() if path.exists() else path))


# ----------------------------------------------------------------------------
# 事件日志
# ----------------------------------------------------------------------------

class SessionStore:
    """append-only 会话事件日志 + 持久性屏障 + 冷读恢复。"""

    def __init__(
        self,
        root: Any,
        mode: str = "chat",
        session_id: Optional[str] = None,
        *,
        cwd: Optional[str] = None,
        header: Optional[SessionHeader] = None,
        auto_load: bool = True,
    ) -> None:
        self.root = Path(root)
        self.mode = str(mode)
        if header is not None:
            self.header = header
            self.session_id = header.session_id
            self.mode = header.mode
        else:
            self.session_id = session_id or new_session_id()
            self.header = SessionHeader(session_id=self.session_id, mode=self.mode, cwd=cwd)

        self.mode_dir = self.root / self.mode
        self.path = self.mode_dir / (self.session_id + ".jsonl")

        self._lock = threading.RLock()
        self._events: List[SessionEvent] = []
        self._seq = 0
        self._revision = 0
        self._last_saved_revision = 0
        self._closed = False
        self._writer_key = _writer_key(self.path)

        with _WRITER_LOCK:
            if self._writer_key in _ACTIVE_WRITERS:
                raise SessionInUseError(
                    "同一会话已有写入方: " + str(self.path)
                    + "（对齐 dsh-session-persistence 的单写者约定）"
                )
            _ACTIVE_WRITERS.add(self._writer_key)

        self.mode_dir.mkdir(parents=True, exist_ok=True)
        if auto_load:
            self.load()

    # ---- 基本属性 ----

    @property
    def revision(self) -> int:
        """事件版本号；UI 用旧 revision 提交更新时必须被拒绝（P17 语义）。"""
        return self._revision

    @property
    def last_seq(self) -> int:
        return self._seq

    @property
    def dirty(self) -> bool:
        return self._revision != self._last_saved_revision

    # ---- 写入 ----

    def append(self, kind: str, payload: Optional[Mapping[str, Any]] = None,
               **fields: Any) -> SessionEvent:
        """追加一条事件并返回它；此时**尚未落盘**，落盘由 flush 保证。"""
        if self._closed:
            raise SessionError("会话已关闭: " + self.session_id)
        if kind not in ALL_KINDS:
            raise ValueError(
                "未知事件 kind " + repr(kind) + "，可选: " + repr(sorted(ALL_KINDS))
            )
        merged: Dict[str, Any] = dict(payload or {})
        merged.update(fields)
        with self._lock:
            self._seq += 1
            self._revision += 1
            event = SessionEvent(seq=self._seq, kind=kind, payload=merged)
            self._events.append(event)
            return event

    def checkpoint(self, reason: str = "") -> None:
        """语义检查点：写入一条 checkpoint 审计事件并 flush（fail-closed）。"""
        self.append(KIND_CHECKPOINT, {"reason": reason, "revision": self._revision + 1})
        self.flush(reason=reason)

    def flush(self, reason: str = "") -> None:
        """持久性屏障：原子替换写盘 + fsync。失败抛 CheckpointFailed。

        **返回即表示已落盘**；抛异常表示落盘失败，调用方必须阻止下游副作用。
        """
        with self._lock:
            snapshot = list(self._events)
            revision = self._revision
        try:
            self.mode_dir.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(
                prefix="." + self.session_id + ".", suffix=".tmp", dir=str(self.mode_dir))
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                    handle.write(json.dumps(self.header.to_dict(), ensure_ascii=False) + "\n")
                    for event in snapshot:
                        handle.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp_name, self.path)
                tmp_name = ""
            finally:
                if tmp_name and os.path.exists(tmp_name):
                    try:
                        os.remove(tmp_name)
                    except OSError:  # pragma: no cover - 清理失败不影响语义
                        pass
        except Exception as exc:
            raise CheckpointFailed(
                "检查点写入失败（" + (reason or "unspecified") + "）：" + str(exc),
                reason=reason or "unspecified",
            ) from exc
        self._last_saved_revision = revision

    def close(self) -> None:
        """释放单写者登记（不自动 flush，语义检查点由调用方负责）。"""
        if self._closed:
            return
        self._closed = True
        with _WRITER_LOCK:
            _ACTIVE_WRITERS.discard(self._writer_key)

    def __enter__(self) -> "SessionStore":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    # ---- 读取 ----

    def events(self) -> Iterator[SessionEvent]:
        """冷读内存中的事件序列（不触碰磁盘）。"""
        with self._lock:
            return iter(list(self._events))

    def load(self) -> List[SessionEvent]:
        """从磁盘恢复：容忍撕裂尾部，并补全未完成的工具调用结果。

        * 丢头一行是会话头；
        * 最后一行 JSON 不完整（进程被杀）时**丢弃该行并截断文件到上一条完整
          记录**，不抛异常、不丢已完整记录；
        * 存在「有 tool_call 无 tool_result」的调用时，在内存里补
          TOOL_OUTCOME_UNKNOWN 结果（**不写回文件**，避免二次污染），
          且**不自动重试**结果不明的操作（P14）。
        """
        if not self.path.exists():
            with self._lock:
                self._events = []
                self._seq = 0
                self._revision = 0
                self._last_saved_revision = 0
            return []

        raw = self.path.read_bytes()
        lines = raw.split(b"\n")
        trailing_newline = raw.endswith(b"\n")
        if trailing_newline and lines and lines[-1] == b"":
            lines.pop()

        parsed: List[SessionEvent] = []
        header_loaded = False
        truncate_to = 0
        consumed_lines = 0
        for index, line in enumerate(lines):
            if not line.strip():
                consumed_lines = index + 1
                truncate_to += len(line) + 1
                continue
            try:
                data = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                break  # 撕裂尾部：停止解析并截断
            if index == 0 and isinstance(data, dict) and "session_id" in data:
                header_loaded = True
                self.header = SessionHeader.from_dict(data)
                self.mode = self.header.mode
                consumed_lines = 1
                truncate_to += len(line) + 1
                continue
            if not isinstance(data, dict):
                break
            try:
                parsed.append(SessionEvent.from_dict(data))
            except SessionFormatError:
                raise
            consumed_lines = index + 1
            truncate_to += len(line) + 1

        if consumed_lines < len(lines):
            # 截断到最后一条完整记录，避免撕裂尾部继续污染后续追加
            with open(self.path, "wb") as handle:
                handle.write(raw[:truncate_to])
                handle.flush()
                os.fsync(handle.fileno())

        events = self._repair_dangling_tool_calls(parsed)
        with self._lock:
            self._events = events
            self._seq = max((e.seq for e in events), default=0)
            self._revision = len(events)
            self._last_saved_revision = len(parsed)
        return list(events)

    @staticmethod
    def _repair_dangling_tool_calls(events: List[SessionEvent]) -> List[SessionEvent]:
        """为「有 tool_call 无 tool_result」的调用补 TOOL_OUTCOME_UNKNOWN 结果。"""
        results = {
            str(e.payload.get("call_id") or e.payload.get("id"))
            for e in events if e.kind == KIND_TOOL_RESULT
        }
        repaired = list(events)
        seq = max((e.seq for e in events), default=0)
        for event in events:
            if event.kind != KIND_TOOL_CALL:
                continue
            call_id = str(event.payload.get("call_id") or event.payload.get("id"))
            if call_id in results:
                continue
            seq += 1
            repaired.append(SessionEvent(
                seq=seq,
                kind=KIND_TOOL_RESULT,
                payload={
                    "call_id": call_id,
                    "name": event.payload.get("name", ""),
                    "outcome": TOOL_OUTCOME_UNKNOWN,
                    "content": TOOL_OUTCOME_UNKNOWN_TEXT,
                    "repaired": True,
                },
                at=_now(),
            ))
            results.add(call_id)
        return repaired

    # ---- 派生模型历史（对齐 dsh-session 的「从日志派生」） ----

    def derive_messages(self, system_prompt: Optional[str] = None,
                        mode: Any = None) -> List[Dict[str, Any]]:
        """把事件日志投影成 OpenAI messages。

        带来源的系统消息（context / mode / sandbox）以 user 消息追加的形式
        **原地**落在它们发生的位置，因此「策略变化」不改写请求头（P19）。
        """
        messages: List[Dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        for event in self.events():
            payload = dict(event.payload)
            if event.kind == KIND_USER:
                messages.append({"role": "user", "content": payload.get("content", "")})
            elif event.kind == KIND_ASSISTANT:
                calls = payload.get("tool_calls")
                # 有工具调用时 content 允许为 None（OpenAI 的助手消息约定）；
                # 没有工具调用时必须给字符串，否则下游接口会直接报错。
                content = payload.get("content")
                if calls:
                    content = content or None
                else:
                    content = content or ""
                message: Dict[str, Any] = {"role": "assistant", "content": content}
                if calls:
                    message["tool_calls"] = [dict(c) for c in calls]
                messages.append(message)
            elif event.kind == KIND_TOOL_CALL:
                # 由紧随其后的 assistant 消息承载，这里只做防护性跳过
                continue
            elif event.kind == KIND_TOOL_RESULT:
                call_id = payload.get("call_id") or payload.get("id")
                if not call_id:
                    continue
                messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": str(payload.get("content", "")),
                })
            elif event.kind in (KIND_CONTEXT, KIND_MODE, KIND_SANDBOX, KIND_PROACTIVE):
                text = payload.get("text") or payload.get("content") or ""
                if text:
                    messages.append({"role": "user", "content": str(text)})
        return messages

    # ---- 便捷方法 ----

    def used_tool_names(self) -> List[str]:
        names: List[str] = []
        for event in self.events():
            if event.kind == KIND_TOOL_CALL:
                name = str(event.payload.get("name") or "")
                if name and name not in names:
                    names.append(name)
        return names

    # ---- 会话库（按模式隔离） ----

    @classmethod
    def latest_session_id(cls, root: Any, mode: str) -> Optional[str]:
        directory = Path(root) / str(mode)
        if not directory.is_dir():
            return None
        candidates = [p for p in directory.glob("*.jsonl") if p.is_file()]
        if not candidates:
            return None
        newest = max(candidates, key=lambda p: p.stat().st_mtime)
        return newest.stem

    @classmethod
    def list_sessions(cls, root: Any, mode: str) -> List[Path]:
        directory = Path(root) / str(mode)
        if not directory.is_dir():
            return []
        return sorted(directory.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)


# ----------------------------------------------------------------------------
# 前端投影（UI 只读快照，不重放事件日志）
# ----------------------------------------------------------------------------

@dataclass(frozen=True)
class ProjectionSnapshot:
    """投影快照。载体会把「状态」与「它反映到的最后一个事件」配对（P18）。"""

    revision: int
    mode: str
    session_id: str
    cwd: Optional[str]
    sandbox_mode: str
    tools_in_flight: Tuple[str, ...]
    message_count: int
    stop_reason: Optional[str]
    last_event_seq: int
    changed_at: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "revision": self.revision,
            "mode": self.mode,
            "session_id": self.session_id,
            "cwd": self.cwd,
            "sandbox_mode": self.sandbox_mode,
            "tools_in_flight": list(self.tools_in_flight),
            "message_count": self.message_count,
            "stop_reason": self.stop_reason,
            "last_event_seq": self.last_event_seq,
            "changed_at": self.changed_at,
        }


class SessionProjection:
    """只读投影 + 变更通知。

    对齐 dsh-session-projection：客户端拿「快照 + 变更通知」，**不自行重放
    原始事件日志**（P18）。没有 Qt 依赖——订阅者就是一个回调列表，
    PySide6 侧只需要在回调里 emit 一个 Signal。
    """

    def __init__(self, session: SessionStore) -> None:
        self._session = session
        self._revision = 0
        self._stop_reason: Optional[str] = None
        self._in_flight: List[str] = []
        self._sandbox_mode: str = "read-only"
        self._callbacks: List[Callable[[ProjectionSnapshot], None]] = []
        self._changed_at = _now()

    # ---- 订阅 ----

    def subscribe(self, callback: Callable[[ProjectionSnapshot], None]) -> Callable[[], None]:
        if callback not in self._callbacks:
            self._callbacks.append(callback)

        def _unsubscribe() -> None:
            if callback in self._callbacks:
                self._callbacks.remove(callback)

        return _unsubscribe

    def unsubscribe(self, callback: Callable[[ProjectionSnapshot], None]) -> None:
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    # ---- 变更 ----

    def update(self, *, sandbox_mode: Optional[str] = None,
               stop_reason: Optional[str] = None,
               tool_started: Optional[str] = None,
               tool_finished: Optional[str] = None,
               notify: bool = True) -> ProjectionSnapshot:
        if sandbox_mode is not None:
            self._sandbox_mode = sandbox_mode
        if stop_reason is not None:
            self._stop_reason = stop_reason
        if tool_started:
            self._in_flight.append(tool_started)
        if tool_finished:
            while tool_finished in self._in_flight:
                self._in_flight.remove(tool_finished)
        self._revision += 1
        self._changed_at = _now()
        snapshot = self.snapshot()
        if notify:
            self._notify(snapshot)
        return snapshot

    def reset_turn(self) -> None:
        """一轮开始时的复位（保留 sandbox_mode）。"""
        self._in_flight = []
        self._stop_reason = None
        self._revision += 1
        self._changed_at = _now()

    def _notify(self, snapshot: ProjectionSnapshot) -> None:
        for callback in list(self._callbacks):
            try:
                callback(snapshot)
            except Exception:  # pragma: no cover - 订阅者异常不得影响内核
                continue

    # ---- 快照 ----

    def snapshot(self) -> ProjectionSnapshot:
        session = self._session
        messages = session.derive_messages()
        return ProjectionSnapshot(
            revision=session.revision,
            mode=session.mode,
            session_id=session.session_id,
            cwd=session.header.cwd,
            sandbox_mode=self._sandbox_mode,
            tools_in_flight=tuple(self._in_flight),
            message_count=len(messages),
            stop_reason=self._stop_reason,
            last_event_seq=session.last_seq,
            changed_at=self._changed_at,
        )
