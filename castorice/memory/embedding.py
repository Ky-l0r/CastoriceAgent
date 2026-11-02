"""embedding 记忆模式：沿用 ChromaDB 的向量检索。

这是**默认模式**，行为与现状（`ai.MemoryManager`）保持一致：

* 正文不含时间戳，时间写进 metadata（`timestamp/date/time/weekday`），
  召回时再拼成 `[YYYY-MM-DD HH:MM:SS] 正文`；
* 默认检索 5 条（旧实现的 `DEFAULT_SEARCH_RESULTS`）；
* 检索失败（索引不可用、查询异常）时返回空列表并记下 `last_error`，
  绝不把异常抛给对话主流程——与 ai.py:527-529 的容错一致。

同时修掉旧实现的两个隐患：

1. **ID 不再用 `count()+1`**（删除或跳过写入会重号并覆盖既有记忆），
   改为 `mem_<随机十六进制>`；旧库里已有的纯数字 ID 照常可读；
2. **召回顺序稳定**：候选按「相似度 + 新鲜度」加权排序，同分时按 ID
   稳定打破平局，避免同一次查询两次结果不同。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .entries import (
    SOURCE_CONVERSATION,
    MemoryEntry,
    recency_weight,
    stamp_of,
)
from .index import (
    DEFAULT_CHROMA_DIR,
    DEFAULT_COLLECTION_NAME,
    IndexBackend,
    build_index,
)
from .store import JsonlStore, MemoryStoreError

#: 文本层默认目录名后缀（结构化字段、遮蔽标记的真相来源）
TEXT_DIR_SUFFIX = "_text"

#: 默认召回条数（旧实现 `DEFAULT_SEARCH_RESULTS`）
DEFAULT_SEARCH_RESULTS = 5

#: 新鲜度加权系数：得分 = 相似度 * (1 - W) + 新鲜度 * W
RECENCY_WEIGHT = 0.3

#: 新鲜度的半衰期（天）
RECENCY_HALF_LIFE_DAYS = 30.0


class EmbeddingMemory:
    """向量记忆模式。

    前两个构造参数与 `ai.MemoryManager` 对齐（`chroma_dir` /
    `collection_name`），因此 ai.py 侧接上本类只是换一行实例化，
    **同一个库、同一个集合**，旧数据无需迁移即可读出。

    额外可选参数：
    * `path`：文本层目录，默认 `<chroma_dir>/<collection_name>_text`；
    * `index`：显式注入的索引后端（测试用 `InMemoryIndex`，或自定义后端）；
    * `search_results`：默认召回条数。
    """

    #: 引擎名，供配置 `memory.engine` 使用
    name = "embedding"

    def __init__(
        self,
        chroma_dir: Any = DEFAULT_CHROMA_DIR,
        collection_name: str = DEFAULT_COLLECTION_NAME,
        *,
        path: Any = None,
        index: Optional[IndexBackend] = None,
        search_results: int = DEFAULT_SEARCH_RESULTS,
        recency_weight: float = RECENCY_WEIGHT,
        recency_half_life_days: float = RECENCY_HALF_LIFE_DAYS,
    ) -> None:
        self.chroma_dir = Path(str(chroma_dir))
        self.collection_name = str(collection_name)
        self.search_results = int(search_results)
        self.recency_weight = min(max(float(recency_weight), 0.0), 1.0)
        self.recency_half_life_days = float(recency_half_life_days)

        #: 文本层路径（结构化字段、遮蔽标记、revision 都在这里）
        self.path = (
            Path(path)
            if path is not None
            else (self.chroma_dir / f"{self.collection_name}{TEXT_DIR_SUFFIX}")
        )
        self.path.mkdir(parents=True, exist_ok=True)
        self.text_store = JsonlStore(self.path / "entries.jsonl")

        self.index: IndexBackend = build_index(self.chroma_dir, self.collection_name, backend=index)
        self.last_error: Optional[str] = None

    # -- 写 -------------------------------------------------------------------

    def add_memory(self, content: str, timestamp: Optional[datetime] = None, **fields: Any) -> str:
        """写入一条记忆并返回其 ID（旧签名兼容：前两个参数不变）。"""
        stamp = stamp_of(timestamp)
        entry = MemoryEntry(
            content=content,
            timestamp=stamp,
            created_at=stamp,
            updated_at=stamp,
            source=fields.pop("source", SOURCE_CONVERSATION),
            title=fields.pop("title", ""),
            tags=fields.pop("tags", ()),
            metadata=dict(fields.pop("metadata", {}) or {}),
        )
        if fields:
            raise TypeError("add_memory 收到未知参数: " + ", ".join(sorted(fields)))
        self.remember(entry)
        return entry.id

    def remember(self, entry: MemoryEntry) -> str:
        """写入（或覆盖）一条记忆：同一 ID 重复写入先删旧向量，保持幂等。"""
        if not isinstance(entry, MemoryEntry):
            entry = MemoryEntry(content=str(entry))
        if self._index_has(entry.id):
            self._delete_vectors([entry.id])
        self.index.add(
            ids=[entry.id],
            documents=[entry.content],
            metadatas=[self._metadata_for(entry)],
        )
        records = [item.to_dict() for item in self._load_entries() if item.id != entry.id]
        records.append(entry.to_dict())
        self.text_store.replace(records)
        return entry.id

    def update(
        self, entry_id: str, patch: Mapping[str, Any], *, expected_revision: Optional[int] = None
    ) -> MemoryEntry:
        """局部更新一条记忆；`expected_revision` 不符时抛 `ValueError`。"""
        entries = self._load_entries()
        for index, entry in enumerate(entries):
            if entry.id != entry_id:
                continue
            if expected_revision is not None and entry.revision != expected_revision:
                raise ValueError(
                    f"记忆 {entry_id} 的 revision 已变"
                    f"（期望 {expected_revision}，实际 {entry.revision}）"
                )
            updated = entry.patched(**dict(patch))
            entries[index] = updated
            self.text_store.replace([item.to_dict() for item in entries])
            self._delete_vectors([entry_id])
            self.index.add(
                ids=[entry_id],
                documents=[updated.content],
                metadatas=[self._metadata_for(updated)],
            )
            return updated
        raise KeyError(f"未找到记忆: {entry_id}")

    def forget(self, entry_id: str) -> MemoryEntry:
        """遮蔽一条记忆：从向量索引移除，文本层保留（不物理删除）。"""
        updated = self.update(entry_id, {"forgotten": True})
        self._delete_vectors([entry_id])
        return updated

    def clear(self) -> None:
        """清空所有记忆（旧签名兼容）。"""
        self.text_store.truncate()
        self.index.clear()

    # -- 读 -------------------------------------------------------------------

    def search_memories(self, query: str, n_results: Optional[int] = None) -> List[str]:
        """检索相关记忆，返回带 `[时间]` 前缀的文本（旧签名兼容）。"""
        return [entry.with_prefix() for entry in self.recall(query, n_results)]

    def recall(self, query: str, k: Optional[int] = None) -> List[MemoryEntry]:
        """检索相关记忆条目；任何异常都降级为空列表。"""
        limit = self.search_results if k is None else int(k)
        text = (query or "").strip()
        if not text or limit <= 0:
            return []
        hidden = {entry.id for entry in self._load_entries() if entry.forgotten}
        try:
            ids, documents, metadatas = self.index.query(text, max(limit * 4, limit))
        except Exception as exc:
            self.last_error = str(exc)
            return []

        entries: List[MemoryEntry] = []
        for position, memory_id in enumerate(ids):
            if memory_id in hidden:
                continue
            document = documents[position] if position < len(documents) else ""
            metadata = metadatas[position] if position < len(metadatas) else {}
            if not document:
                document = self._document_of(memory_id)
            if not document:
                continue
            entries.append(self._entry_from_index(memory_id, document, metadata or {}))
        return self._rank(entries, limit)

    def count(self) -> int:
        """索引里的记忆数量（被遮蔽的条目已移出索引，因此不计入）。"""
        try:
            return int(self.index.count())
        except Exception as exc:  # pragma: no cover - 取决于后端
            self.last_error = str(exc)
            return len(self._load_entries())

    @property
    def collection(self) -> Any:
        """底层 Chroma 集合（旧属性兼容；无 Chroma 时返回 `None`）。"""
        return self.index.as_collection()

    @property
    def root(self) -> Path:
        """本模式的落盘根目录（三种引擎统一的属性）。

        定义为「文本层目录的父目录」：`build_memory_engine` 默认把文本层放在
        `<root>/<collection>_text`，父目录即 root，与 wiki/summary 的 root
        语义一致。

        这个属性是必需的——`MemoryEngineAdapter` 靠它定位 Worldbook。
        缺了它适配器只能退回相对路径默认值，把 `memory/worldbook.json`
        写进**调用方的当前工作目录**（t14 修的真实缺陷：仓库根凭空多出
        `memory/`，且内容与任何用例都对不上）。
        """
        return self.path.parent

    def entries(self) -> List[MemoryEntry]:
        """文本层的全部条目（含被遮蔽的），按写入时间排序。"""
        return self._load_entries()

    def get(self, entry_id: str) -> MemoryEntry:
        for entry in self._load_entries():
            if entry.id == entry_id:
                return entry
        raise KeyError(f"未找到记忆: {entry_id}")

    # -- 内部 -----------------------------------------------------------------

    def _metadata_for(self, entry: MemoryEntry) -> Dict[str, Any]:
        moment = self._parse_stamp(entry.timestamp or entry.created_at)
        stamp = stamp_of(moment) if moment else (entry.timestamp or entry.created_at)
        date_part, _, time_part = stamp.partition(" ")
        return {
            "timestamp": stamp,
            "date": date_part,
            "time": time_part,
            "weekday": moment.strftime("%A") if moment else "",
            "entry_id": entry.id,
            "title": entry.title,
            "tags": ",".join(entry.tags),
            "source": entry.source,
            "forgotten": bool(entry.forgotten),
        }

    @staticmethod
    def _parse_stamp(text: str) -> Optional[datetime]:
        if not text:
            return None
        try:
            return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError):
            return None

    def _entry_from_index(
        self, memory_id: str, document: str, metadata: Mapping[str, Any]
    ) -> MemoryEntry:
        meta = dict(metadata or {})
        stamp = str(meta.get("timestamp") or "")
        return MemoryEntry(
            id=memory_id,
            content=document,
            title=str(meta.get("title") or ""),
            tags=meta.get("tags") or (),
            source=str(meta.get("source") or SOURCE_CONVERSATION),
            created_at=stamp,
            updated_at=stamp,
            timestamp=stamp,
            forgotten=bool(meta.get("forgotten")),
            metadata={"from_index": True},
        )

    def _rank(self, entries: Sequence[MemoryEntry], limit: int) -> List[MemoryEntry]:
        """按「相似度 + 新鲜度」排序后截断（相似度由索引给出的顺序隐含表示）。"""
        total = max(len(entries), 1)
        decorated: List[Tuple[float, str, MemoryEntry]] = []
        for position, entry in enumerate(entries):
            similarity = 1.0 - (position / float(total))
            freshness = recency_weight(entry, half_life_days=self.recency_half_life_days)
            score = similarity * (1.0 - self.recency_weight) + freshness * self.recency_weight
            decorated.append((score, entry.id, entry))
        decorated.sort(key=lambda item: (-item[0], item[1]))
        return [item[2] for item in decorated[:limit]]

    def _load_entries(self) -> List[MemoryEntry]:
        try:
            return [MemoryEntry.from_dict(item) for item in self.text_store.load()]
        except MemoryStoreError:
            return []

    def _document_of(self, memory_id: str) -> str:
        for entry in self._load_entries():
            if entry.id == memory_id:
                return entry.content
        return ""

    def _index_has(self, memory_id: str) -> bool:
        try:
            ids, _documents, _metadatas = self.index.all_records()
        except Exception:
            return False
        return memory_id in ids

    def _delete_vectors(self, ids: Sequence[str]) -> None:
        try:
            self.index.delete(list(ids))
        except Exception as exc:  # pragma: no cover - 取决于后端
            self.last_error = str(exc)


__all__ = [
    "DEFAULT_SEARCH_RESULTS",
    "RECENCY_HALF_LIFE_DAYS",
    "RECENCY_WEIGHT",
    "TEXT_DIR_SUFFIX",
    "EmbeddingMemory",
]
