"""DMAE Worldbook：带触发词、优先级与生命周期的设定条目。

Worldbook 解决的是一类向量检索做不好的记忆：**只要聊到某个词，就该想起
的稳定设定**（人物关系、称呼、长期偏好、世界规则）。它不走向量，
而是「触发词命中 + 优先级排序」，因此结果是确定的、可解释的，
不会因为 embedding 模型的波动而时灵时不灵。

条目形状（契约要求）：

* `trigger_words` 触发词（命中即候选，大小写不敏感）；
* `priority`    优先级（同命中数下高者先返回）；
* `state`       生命周期状态 `active | archived | evicted`；
* `access_count / last_accessed` 访问计数与最近访问时间。

生命周期：

```
            archive()                 evict() / evict_stale()
  active ─────────────▶ archived ─────────────────────▶ evicted
     ▲                     │                                │
     └──── restore() ──────┘            restore() 也可从 evicted 回来
```

两条硬规则：

1. **淘汰是标记而不是删除**：`evict` 只改 `state` 并记录原因/时间，
   条目始终留在磁盘上（对齐「压缩只遮蔽不删除」）；
2. **只有 `relevant()` 计入访问**：`match()` 是纯查询、无副作用，
   便于在测试与 UI 预览里随便调用。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .entries import new_memory_id, normalize_tags, parse_stamp, stamp_of
from .store import MemoryStoreError, ensure_directory, read_json, write_json

#: 生命周期状态（封闭集合）
STATE_ACTIVE = "active"
STATE_ARCHIVED = "archived"
STATE_EVICTED = "evicted"
STATES = frozenset({STATE_ACTIVE, STATE_ARCHIVED, STATE_EVICTED})

#: 淘汰默认参数：`state=archived` 且闲置超过 30 天且访问次数少于 1 次
DEFAULT_IDLE_DAYS = 30.0
DEFAULT_MAX_ACCESS = 1

#: 单次检索默认返回条数
DEFAULT_MATCH_LIMIT = 5


def _time_value(text: str) -> float:
    """把时间串转成「越大越新」的数值（无时间给 0.0，排在所有真实时间之后）。

    刻意不用 `datetime.timestamp()`：它依赖本地时区，而这里只做排序，
    用序数 + 当日秒数即可得到稳定、可重现的比较值。
    """
    moment = parse_stamp(text)
    if moment is None:
        return 0.0
    return moment.toordinal() * 86400.0 + moment.hour * 3600 + moment.minute * 60 + moment.second


@dataclass
class WorldbookEntry:
    """一条 Worldbook 设定。"""

    content: str = ""
    id: str = ""
    title: str = ""
    trigger_words: Tuple[str, ...] = ()
    priority: int = 0
    state: str = STATE_ACTIVE
    access_count: int = 0
    last_accessed: str = ""
    created_at: str = ""
    updated_at: str = ""
    revision: int = 1
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id:
            self.id = new_memory_id("wb")
        self.content = "" if self.content is None else str(self.content)
        self.title = "" if self.title is None else str(self.title)
        # 触发词保留大小写（展示用），匹配时统一小写比较
        self.trigger_words = normalize_tags(self.trigger_words)
        self.state = str(self.state or STATE_ACTIVE)
        if self.state not in STATES:
            raise ValueError(
                f"未知的生命周期状态: {self.state!r}（可选：{', '.join(sorted(STATES))}）"
            )
        self.priority = int(self.priority or 0)
        self.access_count = max(int(self.access_count or 0), 0)
        self.revision = int(self.revision or 1)
        if not self.created_at:
            self.created_at = stamp_of()
        if not self.updated_at:
            self.updated_at = self.created_at
        if not isinstance(self.metadata, dict):
            self.metadata = dict(self.metadata or {})

    # -- 匹配 -----------------------------------------------------------------

    @property
    def is_active(self) -> bool:
        return self.state == STATE_ACTIVE

    def hit_count(self, text: str) -> int:
        """文本命中了几个触发词（大小写不敏感）。"""
        if not self.trigger_words:
            return 0
        haystack = (text or "").lower()
        if not haystack:
            return 0
        return sum(1 for word in self.trigger_words if word.lower() in haystack)

    def matches(self, text: str) -> bool:
        """是否命中（无触发词的条目不参与匹配，避免变成「永远注入」）。"""
        return self.hit_count(text) > 0

    def age_days(self, now: Optional[datetime] = None) -> float:
        """距最近一次访问（无访问记录则用更新时间）过去了多少天。"""
        raw = self.last_accessed or self.updated_at or self.created_at
        moment = parse_stamp(raw)
        if moment is None:
            return 0.0
        reference = now or datetime.now()
        return max((reference - moment).total_seconds() / 86400.0, 0.0)

    def is_evictable(
        self,
        *,
        now: Optional[datetime] = None,
        idle_days: float = DEFAULT_IDLE_DAYS,
        max_access: int = DEFAULT_MAX_ACCESS,
    ) -> bool:
        """是否满足淘汰条件（只对 `archived` 生效）。"""
        return (
            self.state == STATE_ARCHIVED
            and self.access_count < int(max_access)
            and self.age_days(now) > float(idle_days)
        )

    # -- 序列化 ---------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "content": self.content,
            "trigger_words": list(self.trigger_words),
            "priority": self.priority,
            "state": self.state,
            "access_count": self.access_count,
            "last_accessed": self.last_accessed,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "revision": self.revision,
            "metadata": dict(self.metadata),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> WorldbookEntry:
        data = dict(data or {})
        metadata = data.get("metadata")
        return cls(
            content=data.get("content", ""),
            id=str(data.get("id") or ""),
            title=data.get("title", "") or "",
            trigger_words=data.get("trigger_words") or (),
            priority=data.get("priority") or 0,
            state=data.get("state") or STATE_ACTIVE,
            access_count=data.get("access_count") or 0,
            last_accessed=str(data.get("last_accessed") or ""),
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
            revision=data.get("revision") or 1,
            metadata=dict(metadata) if isinstance(metadata, Mapping) else {},
        )

    PATCHABLE_FIELDS = (
        "content",
        "title",
        "trigger_words",
        "priority",
        "state",
        "access_count",
        "last_accessed",
        "metadata",
    )

    def snapshot(self) -> WorldbookEntry:
        """返回一份与内部状态完全隔离的副本（不推进 `revision`）。

        与 `patched()` 的区别：`patched` 表示「这是一次真实改动」，
        `snapshot` 表示「这只是拿出来给人看」。把副本交给调用方，
        引擎内部的状态就不可能被外部意外改写。
        """
        return WorldbookEntry.from_dict(self.to_dict())

    def patched(self, *, stamp: Optional[str] = None, **changes: Any) -> WorldbookEntry:
        """返回应用改动后的副本：`revision` 自增、`updated_at` 刷新。"""
        unknown = set(changes) - set(self.PATCHABLE_FIELDS)
        if unknown:
            raise ValueError(
                "不可修改的 Worldbook 字段: "
                + ", ".join(sorted(unknown))
                + "（可改："
                + ", ".join(self.PATCHABLE_FIELDS)
                + "）"
            )
        payload = self.to_dict()
        payload.update(changes)
        payload["revision"] = self.revision + 1
        payload["updated_at"] = stamp or stamp_of()
        return WorldbookEntry.from_dict(payload)


def format_worldbook(entries: Sequence[WorldbookEntry]) -> str:
    """把条目拼成注入提示词的文本（一行一条，标题加粗式前缀）。"""
    lines: List[str] = []
    for entry in entries:
        label = f"{entry.title}：" if entry.title else ""
        lines.append(f"- {label}{entry.content}")
    return "\n".join(lines)


class Worldbook:
    """Worldbook 条目的容器与生命周期管理器。

    参数：
    * `root`：落盘根目录，默认 `memory/`；
    * `path`：显式指定 JSON 文件（默认 `<root>/worldbook.json`）；
    * `enabled`：关闭时 `match / relevant` 恒返回空列表（配置项
      `memory.worldbook_enabled`），但读写接口照常可用；
    * `max_entries`：容量上限，超出时**归档**最久未访问的 active 条目
      （而不是删除）。
    """

    DEFAULT_MAX_ENTRIES = 1000

    def __init__(
        self,
        root: Any = "memory",
        *,
        path: Any = None,
        engine: Any = None,
        auto_load: bool = True,
        enabled: bool = True,
        max_entries: int = DEFAULT_MAX_ENTRIES,
    ) -> None:
        self.root = Path(str(root))
        self.path = Path(path) if path is not None else self.root / "worldbook.json"
        self.enabled = bool(enabled)
        self.max_entries = max(int(max_entries), 1)
        self.engine = engine
        self.loaded = False
        ensure_directory(self.path.parent)
        self._entries: List[WorldbookEntry] = []
        if auto_load:
            self.load()

    # -- 落盘 -----------------------------------------------------------------

    def load(self) -> int:
        """从磁盘载入（返回条目数）。坏文件不抛错，退回空集合并记 `last_error`。"""
        self.last_error: Optional[str] = None
        try:
            data = read_json(self.path, {})
        except MemoryStoreError as exc:
            self.last_error = str(exc)
            data = {}
        # 兼容两种版本字段：`write_json` 注入的 `v` 与本模块自写的 `version`
        version = data.get("version", data.get("v", 1))
        raw_entries = data.get("entries") or []
        entries: List[WorldbookEntry] = []
        if version == 1 and isinstance(raw_entries, list):
            for item in raw_entries:
                if not isinstance(item, Mapping):
                    continue
                try:
                    entries.append(WorldbookEntry.from_dict(item))
                except (TypeError, ValueError):
                    continue
        self._entries = entries
        self.loaded = True
        return len(entries)

    def save(self) -> None:
        write_json(
            self.path,
            {
                "version": 1,
                "saved_at": stamp_of(),
                "entries": [entry.to_dict() for entry in self._entries],
            },
        )

    # -- 写 -------------------------------------------------------------------

    def add(
        self,
        title: str = "",
        content: str = "",
        *,
        trigger_words: Any = (),
        priority: int = 0,
        state: str = STATE_ACTIVE,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> WorldbookEntry:
        """创建一个条目并落盘。"""
        entry = WorldbookEntry(
            title=title,
            content=content,
            trigger_words=trigger_words,
            priority=priority,
            state=state,
            metadata=dict(metadata or {}),
        )
        self.upsert(entry)
        return entry

    #: 与 `add` 等价的别名（术语对齐：worldbook 条目是「记下来的设定」）
    remember = add

    def upsert(self, entry: WorldbookEntry) -> WorldbookEntry:
        """插入或整条替换（按 ID）。"""
        stored = entry if isinstance(entry, WorldbookEntry) else WorldbookEntry.from_dict(entry)
        replaced = False
        for index, existing in enumerate(self._entries):
            if existing.id == stored.id:
                self._entries[index] = stored
                replaced = True
                break
        if not replaced:
            self._entries.append(stored)
            self._enforce_capacity()
        self.save()
        return stored

    def update(
        self,
        entry_id: str,
        patch: Mapping[str, Any],
        *,
        expected_revision: Optional[int] = None,
        stamp: Optional[str] = None,
    ) -> WorldbookEntry:
        """局部更新；`expected_revision` 不符时抛 `ValueError`。

        `stamp` 显式给出本次改动时刻（生命周期动作需要它能被测试固定，
        否则 `updated_at` 总是「现在」，淘汰策略就无法验证）。
        """
        for index, entry in enumerate(self._entries):
            if entry.id != entry_id:
                continue
            if expected_revision is not None and entry.revision != expected_revision:
                raise ValueError(
                    f"条目 {entry_id} 的 revision 已变"
                    f"（期望 {expected_revision}，实际 {entry.revision}）"
                )
            updated = entry.patched(stamp=stamp, **dict(patch))
            self._entries[index] = updated
            self.save()
            return updated
        raise KeyError(f"未找到 Worldbook 条目: {entry_id}")

    def get(self, entry_id: str) -> WorldbookEntry:
        for entry in self._entries:
            if entry.id == entry_id:
                return entry
        raise KeyError(f"未找到 Worldbook 条目: {entry_id}")

    def find(self, entry_id: str) -> Optional[WorldbookEntry]:
        for entry in self._entries:
            if entry.id == entry_id:
                return entry
        return None

    def remove(self, entry_id: str) -> bool:
        """物理删除（仅用于用户明确要求彻底删除；常规流程用归档/淘汰）。"""
        keep = [item for item in self._entries if item.id != entry_id]
        if len(keep) == len(self._entries):
            return False
        self._entries = keep
        self.save()
        return True

    def clear(self) -> None:
        self._entries = []
        self.save()

    # -- 生命周期 -------------------------------------------------------------

    def archive(
        self, entry_id: str, *, reason: str = "", stamp: Optional[str] = None
    ) -> WorldbookEntry:
        """归档：不再参与检索，但数据完整保留。"""
        now = stamp or stamp_of()
        return self.update(
            entry_id,
            {
                "state": STATE_ARCHIVED,
                "metadata": dict(
                    self.get(entry_id).metadata or {}, archived_at=now, archive_reason=reason or ""
                ),
            },
            stamp=now,
        )

    def restore(self, entry_id: str) -> WorldbookEntry:
        """从归档/淘汰恢复为 active。"""
        now = stamp_of()
        entry = self.get(entry_id)
        metadata = dict(entry.metadata or {})
        metadata.setdefault("restored_at", now)
        return self.update(entry_id, {"state": STATE_ACTIVE, "metadata": metadata})

    def evict(
        self, entry_id: str, *, reason: str = "", stamp: Optional[str] = None
    ) -> WorldbookEntry:
        """淘汰：**标记而非删除**——`state=evicted` 并记下原因与时间。"""
        now = stamp or stamp_of()
        entry = self.get(entry_id)
        return self.update(
            entry_id,
            {
                "state": STATE_EVICTED,
                "metadata": dict(entry.metadata or {}, evicted_at=now, evict_reason=reason or ""),
            },
            stamp=now,
        )

    def evict_stale(
        self,
        *,
        now: Optional[datetime] = None,
        idle_days: float = DEFAULT_IDLE_DAYS,
        max_access: int = DEFAULT_MAX_ACCESS,
    ) -> List[str]:
        """按策略批量淘汰，返回被淘汰的条目 ID（顺序稳定）。"""
        moment = now or datetime.now()
        stamp = stamp_of(moment)
        victims = [
            entry.id
            for entry in self._entries
            if entry.is_evictable(now=moment, idle_days=idle_days, max_access=max_access)
        ]
        for entry_id in victims:
            self.evict(
                entry_id,
                reason=(f"闲置超过 {idle_days} 天且访问次数少于 {max_access}"),
                stamp=stamp,
            )
        return victims

    def record_access(self, entry_id: str, *, moment: Optional[datetime] = None) -> WorldbookEntry:
        """记录一次访问：`access_count + 1`，刷新 `last_accessed`。"""
        entry = self.get(entry_id)
        now = stamp_of(moment)
        return self.update(
            entry_id,
            {
                "access_count": entry.access_count + 1,
                "last_accessed": now,
            },
            stamp=now,
        )

    # -- 读 -------------------------------------------------------------------

    def all(self, state: Optional[str] = None) -> List[WorldbookEntry]:
        """全部条目（可按状态过滤），按创建时间稳定排序。"""
        entries = sorted(self._entries, key=lambda item: item.created_at or "")
        if state is None:
            return entries
        return [entry for entry in entries if entry.state == state]

    def count(self, state: Optional[str] = None) -> int:
        return len(self.all(state))

    def match(
        self,
        text: str,
        *,
        limit: Optional[int] = None,
        include_archived: bool = False,
        include_evicted: bool = False,
    ) -> List[WorldbookEntry]:
        """按触发词匹配（**纯查询**：不改访问计数，也不改任何条目状态）。

        排序：命中触发词个数降序 → priority 降序 → 最近访问时间降序；
        完全同分的保持写入顺序（稳定排序）。

        返回的是**独立快照**而不是内部对象（t14 修）：此前返回内部实例，
        调用方一句 `match(...)[0].access_count = 99` 就能改坏引擎状态，
        而 `access_count` 正是 `evict_stale` 的输入——于是「看一眼」也会
        改变淘汰判定。现在快照与内部状态完全隔离，纯查询是**结构上**成立的，
        而不只是「约定上」成立。

        需要累计访问时用 `relevant()`（它才调用 `record_access`）。
        """
        return [
            entry.snapshot()
            for entry in self._match_internal(
                text,
                limit=limit,
                include_archived=include_archived,
                include_evicted=include_evicted,
            )
        ]

    def _match_internal(
        self,
        text: str,
        *,
        limit: Optional[int] = None,
        include_archived: bool = False,
        include_evicted: bool = False,
    ) -> List[WorldbookEntry]:
        """匹配的内部实现：返回**内部实例**（只读使用，绝不外泄）。

        `enabled` 开关放在这里而不是 `match()`：`match` 与 `relevant`
        都汇聚到本方法，开关只写一次就不可能被某条路径绕过。
        """
        if not self.enabled:
            return []
        haystack = (text or "").strip()
        if not haystack:
            return []
        candidates: List[WorldbookEntry] = []
        for entry in self._entries:
            # 只让活跃条目参与匹配；归档 / 淘汰条目需显式要求才纳入
            if (
                entry.state == STATE_ACTIVE
                or (entry.state == STATE_ARCHIVED and include_archived)
                or (entry.state == STATE_EVICTED and include_evicted)
            ) and entry.matches(haystack):
                candidates.append(entry)
        candidates.sort(
            key=lambda entry: (
                -entry.hit_count(haystack),
                -entry.priority,
                -_time_value(entry.last_accessed or entry.updated_at),
            )
        )
        cap = DEFAULT_MATCH_LIMIT if limit is None else int(limit)
        return candidates[:cap] if cap > 0 else []

    def relevant(
        self,
        text: str,
        *,
        limit: Optional[int] = None,
        include_archived: bool = False,
        moment: Optional[datetime] = None,
    ) -> List[WorldbookEntry]:
        """检索并**记录访问**（唯一会让 `access_count` 增长的检索入口）。

        返回的是记录访问后的最新副本。`match()` 保持纯查询，
        因此「预览命中」与「真的用上了」在计数上是可区分的。
        """
        matched = self._match_internal(text, limit=limit, include_archived=include_archived)
        updated: List[WorldbookEntry] = []
        for entry in matched:
            updated.append(self.record_access(entry.id, moment=moment))
        return updated

    def context(
        self,
        text: str,
        *,
        limit: Optional[int] = None,
        include_archived: bool = False,
        moment: Optional[datetime] = None,
    ) -> str:
        """检索并格式化成可注入提示词的文本（无命中时返回空串）。"""
        entries = self.relevant(text, limit=limit, include_archived=include_archived, moment=moment)
        return format_worldbook(entries)

    # -- 内部 -----------------------------------------------------------------

    def _enforce_capacity(self) -> None:
        """超出容量时归档最久未访问的 active 条目（不删除）。"""
        if len(self._entries) <= self.max_entries:
            return
        # 保持列表自身的顺序（= 写入顺序）而不是按随机 ID 排序：
        # 同一秒写入的条目，其「谁更久没人用」只能由写入先后决定。
        actives = [entry for entry in self._entries if entry.state == STATE_ACTIVE]
        overflow = len(self._entries) - self.max_entries
        now = stamp_of()
        for entry in actives[:overflow]:
            position = self._entries.index(entry)
            self._entries[position] = entry.patched(
                stamp=now,
                state=STATE_ARCHIVED,
                metadata=dict(entry.metadata or {}, archived_at=now, archive_reason="超出容量上限"),
            )


__all__ = [
    "DEFAULT_IDLE_DAYS",
    "DEFAULT_MATCH_LIMIT",
    "DEFAULT_MAX_ACCESS",
    "STATES",
    "STATE_ACTIVE",
    "STATE_ARCHIVED",
    "STATE_EVICTED",
    "Worldbook",
    "WorldbookEntry",
    "format_worldbook",
]
