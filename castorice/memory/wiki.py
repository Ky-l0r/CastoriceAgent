"""wiki 记忆模式：结构化条目的本地落盘 + 关键词检索。

与 embedding 模式的差别不是「存哪儿」，而是**条目形状**：wiki 的记忆是
可读、可编辑、可归档的结构化条目（标题 / 标签 / 正文 / 来源 / 版本），
适合存「关于这个人、这件事的稳定事实」。检索走关键词打分，不依赖向量库，
因此没有 chromadb 也能用。

排序规则（确定性，无随机）：

1. 关键词相关性（标题 3 分、标签 2 分、正文 1 分，按查询侧归一化到 0..1）；
2. 再叠加时间新鲜度（半衰期 30 天）；
3. 完全同分时按 ID 字典序打破平局。

超过容量上限（`max_entries`）时不物理删除最旧的条目，而是打上
`forgotten` 遮蔽标记（对齐「压缩只遮蔽不删除」）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, List, Mapping, Optional, Sequence, Tuple

from .entries import (
    SOURCE_MANUAL,
    MemoryEntry,
    normalize_tags,
    recency_weight,
    sort_by_time,
    stamp_of,
)
from .index import tokenize
from .store import JsonlStore, MemoryStoreError, ensure_directory

#: 默认条目容量（超过后遮蔽最旧的条目）
DEFAULT_MAX_ENTRIES = 500

#: 默认召回条数（与旧 `MemoryManager.DEFAULT_SEARCH_RESULTS` 一致）
DEFAULT_SEARCH_RESULTS = 5

#: 相关性与新鲜度的权重（相加为 1）
RELEVANCE_WEIGHT = 0.7
RECENCY_WEIGHT = 0.3

#: 新鲜度半衰期（天）
RECENCY_HALF_LIFE_DAYS = 30.0

# 字段权重：标题最重要，其次是标签，最后是正文
_TITLE_WEIGHT = 3.0
_TAG_WEIGHT = 2.0
_BODY_WEIGHT = 1.0
_WEIGHT_TOTAL = _TITLE_WEIGHT + _TAG_WEIGHT + _BODY_WEIGHT


def relevance(query: str, entry: MemoryEntry) -> float:
    """0..1 的关键词相关性。

    同时接受两种命中方式：整串子串命中（中文短查询更可靠）与分词重合
    （英文/长句更可靠），取两者的较大值，避免任一种分词策略失灵时召回全空。
    """
    text = (query or "").strip().lower()
    if not text:
        return 0.0
    query_tokens = set(tokenize(text))
    score = 0.0
    for haystack, weight in (
        (entry.title.lower(), _TITLE_WEIGHT),
        (" ".join(entry.tags).lower(), _TAG_WEIGHT),
        (entry.content.lower(), _BODY_WEIGHT),
    ):
        if not haystack:
            continue
        direct = 1.0 if text in haystack else 0.0
        if query_tokens:
            overlap = len(query_tokens & set(tokenize(haystack))) / float(len(query_tokens))
        else:
            overlap = 0.0
        score += weight * max(direct, overlap)
    return min(score / _WEIGHT_TOTAL, 1.0)


class WikiMemory:
    """结构化条目记忆。

    参数：
    * `root`：落盘根目录（一般是仓库的 `memory/`）；
    * `path`：显式指定 JSONL 文件路径，默认 `<root>/wiki/entries.jsonl`；
    * `max_entries`：容量上限，超出后遮蔽最旧的条目。
    """

    name = "wiki"

    def __init__(
        self,
        root: Any = "memory",
        *,
        path: Any = None,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        recency_weight: float = RECENCY_WEIGHT,
    ) -> None:
        self.root = Path(str(root))
        self.path = Path(path) if path is not None else self.root / "wiki" / "entries.jsonl"
        ensure_directory(self.path.parent)
        self.store = JsonlStore(self.path)
        self.max_entries = max(int(max_entries), 1)
        self.recency_weight = min(max(float(recency_weight), 0.0), 1.0)
        self.last_error: Optional[str] = None

    # -- 写 -------------------------------------------------------------------

    def add(
        self,
        content: str,
        *,
        title: str = "",
        tags: Any = (),
        source: str = SOURCE_MANUAL,
        timestamp: Any = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> MemoryEntry:
        """新增一条结构化条目并返回它。"""
        moment = stamp_of(timestamp) if not isinstance(timestamp, str) else timestamp
        entry = MemoryEntry(
            content=content,
            title=title or "",
            tags=normalize_tags(tags),
            source=source or SOURCE_MANUAL,
            timestamp=moment,
            created_at=moment,
            updated_at=moment,
            metadata=dict(metadata or {}),
        )
        self.remember(entry)
        return entry

    def remember(self, entry: MemoryEntry) -> str:
        """写入（存在同 ID 时按覆盖语义替换）并返回条目 ID。"""
        if not isinstance(entry, MemoryEntry):
            entry = MemoryEntry(content=str(entry))
        others = [item for item in self._load() if item.id != entry.id]
        others.append(entry)
        self._persist(self._enforce_capacity(others))
        return entry.id

    def update(
        self, entry_id: str, patch: Mapping[str, Any], *, expected_revision: Optional[int] = None
    ) -> MemoryEntry:
        """局部更新；`expected_revision` 不符时抛 `ValueError`。"""
        entries = self._load()
        for index, entry in enumerate(entries):
            if entry.id != entry_id:
                continue
            if expected_revision is not None and entry.revision != expected_revision:
                raise ValueError(
                    f"条目 {entry_id} 的 revision 已变"
                    f"（期望 {expected_revision}，实际 {entry.revision}）"
                )
            updated = entry.patched(**dict(patch))
            entries[index] = updated
            self._persist(entries)
            return updated
        raise KeyError(f"未找到条目: {entry_id}")

    def forget(self, entry_id: str) -> MemoryEntry:
        """遮蔽一条条目（保留数据，只是不再召回）。"""
        return self.update(entry_id, {"forgotten": True})

    def delete(self, entry_id: str) -> bool:
        """物理删除一条条目（Worldbook 归档等场景不需要它）。"""
        entries = self._load()
        keep = [item for item in entries if item.id != entry_id]
        if len(keep) == len(entries):
            return False
        self._persist(keep)
        return True

    def clear(self) -> None:
        """清空全部条目。"""
        self.store.truncate()

    def _enforce_capacity(self, entries: Sequence[MemoryEntry]) -> List[MemoryEntry]:
        if len(entries) <= self.max_entries:
            return list(entries)
        ordered = sort_by_time(entries)
        overflow = len(ordered) - self.max_entries
        result: List[MemoryEntry] = []
        for index, entry in enumerate(ordered):
            current = entry
            if index < overflow and not entry.forgotten:
                current = entry.patched(forgotten=True)
            result.append(current)
        return result

    def _persist(self, entries: Sequence[MemoryEntry]) -> None:
        self.store.replace([entry.to_dict() for entry in entries])

    # -- 读 -------------------------------------------------------------------

    def recall(self, query: str, k: int = 5) -> List[MemoryEntry]:
        """按相关性召回（已遮蔽的条目永不返回）。"""
        if k <= 0:
            return []
        return [entry for entry, _score in self.recall_with_scores(query, k)]

    def recall_with_scores(self, query: str, k: int = 5) -> List[Tuple[MemoryEntry, float]]:
        """按相关性召回并给出得分（上下文注入需要排序依据）。"""
        text = (query or "").strip()
        if not text or k <= 0:
            return []
        decorated: List[Tuple[float, str, MemoryEntry]] = []
        for entry in self._load():
            if entry.forgotten:
                continue
            base = relevance(text, entry)
            if base <= 0.0:
                continue
            freshness = recency_weight(entry, half_life_days=RECENCY_HALF_LIFE_DAYS)
            score = base * (1.0 - self.recency_weight) + freshness * self.recency_weight
            decorated.append((score, entry.id, entry))
        decorated.sort(key=lambda item: (-item[0], item[1]))
        return [(item[2], item[0]) for item in decorated[:k]]

    def search_memories(self, query: str, n_results: Optional[int] = None) -> List[str]:
        """`[时间] 正文` 形式的召回结果（三种模式共用的读接口）。"""
        limit = DEFAULT_SEARCH_RESULTS if n_results is None else int(n_results)
        return [entry.with_prefix() for entry in self.recall(query, limit)]

    def add_memory(self, content: str, timestamp: Any = None, **fields: Any) -> str:
        """写入一条条目并返回 ID（三种模式共用的写接口）。"""
        moment = timestamp if isinstance(timestamp, str) else stamp_of(timestamp)
        entry = self.add(content, timestamp=moment, **fields)
        return entry.id

    def recent(self, k: int = 5) -> List[MemoryEntry]:
        """最近更新的条目（空查询时的兜底召回）。"""
        entries = [item for item in self._load() if not item.forgotten]
        entries.sort(key=lambda item: (item.updated_at, item.id), reverse=True)
        return entries[: max(k, 0)]

    def count(self) -> int:
        return len([item for item in self._load() if not item.forgotten])

    def total(self) -> int:
        """含遮蔽条目的总数（迁移与自检用）。"""
        return len(self._load())

    def entries(self) -> List[MemoryEntry]:
        return self._load()

    def get(self, entry_id: str) -> MemoryEntry:
        for entry in self._load():
            if entry.id == entry_id:
                return entry
        raise KeyError(f"未找到条目: {entry_id}")

    @property
    def collection(self) -> Any:
        """旧 `MemoryManager.collection` 的占位：本模式没有向量集合。

        显式返回 `None` 而不是让属性缺失——旧代码会写
        `engine.collection.get(...)`，缺失属性会抛 `AttributeError`，
        而 `None` 至少让「这条路径不适用于本模式」这件事可判定。
        """
        return None

    def as_texts(self, query: str, k: int = 5) -> List[str]:
        """`[时间] 标题 正文` 形式的召回结果（喂给提示词用）。"""
        return [entry.with_prefix() for entry in self.recall(query, k)]

    def _load(self) -> List[MemoryEntry]:
        try:
            return [MemoryEntry.from_dict(item) for item in self.store.load()]
        except MemoryStoreError as exc:
            self.last_error = str(exc)
            return []


__all__ = [
    "DEFAULT_MAX_ENTRIES",
    "DEFAULT_SEARCH_RESULTS",
    "RECENCY_HALF_LIFE_DAYS",
    "RECENCY_WEIGHT",
    "RELEVANCE_WEIGHT",
    "WikiMemory",
    "relevance",
]
