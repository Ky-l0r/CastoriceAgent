"""summary 记忆模式：滚动摘要 + **确定性**压缩。

长对话不能整段塞进提示词，但也不能随手丢掉——本模式把历史当作一串待摘要
的记录，累积到阈值后把**最旧的若干条**压成一条摘要：

* 压缩**不调用模型**（第一期），只用确定性规则：按时间拼接、逐条截断、
  去重、保留头尾；同样的输入永远得到同样的输出，用例可以逐字断言；
* 压缩**不物理删除**原文：被压缩的记录打上 `forgotten` 遮蔽标记并记下
  `compressed_into`，仍在磁盘上，随时可回查（对齐「压缩只遮蔽不删除」）；
* 压缩**不丢失未完成事项**：带 `[未完成]` / `[TODO]` / `[待办]`
  等标记的整行原样保留（不截断、不被总量上限砍掉），并在摘要里单独成段。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .entries import (
    SOURCE_SUMMARY,
    MemoryEntry,
    has_unfinished_marker,
    recency_weight,
    sort_by_time,
    stamp_of,
)
from .store import JsonlStore, MemoryStoreError, ensure_directory
from .wiki import relevance

#: 默认召回条数（与旧 `MemoryManager.DEFAULT_SEARCH_RESULTS` 一致）
DEFAULT_SEARCH_RESULTS = 5

#: 累积多少条待摘要记录后触发压缩
DEFAULT_THRESHOLD = 40

#: 待摘要记录的总字符数达到多少后触发压缩
DEFAULT_CHAR_THRESHOLD = 4000

#: 压缩时至少保留最近多少条不摘要（保证上下文连续）
DEFAULT_KEEP_RECENT = 3

#: 压缩后每条原文在摘要里保留多少字符（首部 + 尾部）
DEFAULT_KEEP_CHARS = 400

#: 单条摘要正文的总长度上限（未完成事项不计入此上限）
DEFAULT_SUMMARY_CHAR_LIMIT = 2000

#: 去重用的最短正文长度（太短的内容容易误判为重复）
MIN_DEDUPE_LENGTH = 8

#: 未完成事项在摘要里的分段标题
UNFINISHED_HEADING = "【未完成事项】"

#: 召回时历史记录的相关性权重（摘要条目更权威，权重更高）
RECORD_WEIGHT = 0.6
SUMMARY_WEIGHT = 1.0


def truncate_middle(text: str, limit: int, *, marker: str = "…（已截断）…") -> str:
    """保留首尾、中间省略的确定性截断。

    首尾都要留：结尾常带结论或「未完成」信号，只留开头会丢关键信息。
    含未完成标记的文本不做截断（调用方负责判断）。
    """
    if limit <= 0 or len(text) <= limit:
        return text
    keep = max(limit - len(marker), 0)
    head = keep // 2 + keep % 2
    tail = keep - head
    if tail <= 0:
        return text[:limit]
    return text[:head] + marker + text[-tail:]


class SummaryMemory:
    """滚动摘要记忆。

    参数：
    * `root`：落盘根目录，默认 `memory/`；
    * `path`：记录文件路径，默认 `<root>/summary/records.jsonl`；
    * `summaries_path`：摘要文件路径，默认 `<root>/summary/summaries.jsonl`；
    * `threshold` / `char_threshold`：触发压缩的阈值；
    * `keep_recent`：压缩时保留最近多少条不摘要。
    """

    name = "summary"

    def __init__(
        self,
        root: Any = "memory",
        *,
        path: Any = None,
        summaries_path: Any = None,
        threshold: int = DEFAULT_THRESHOLD,
        char_threshold: int = DEFAULT_CHAR_THRESHOLD,
        keep_recent: int = DEFAULT_KEEP_RECENT,
        keep_chars: int = DEFAULT_KEEP_CHARS,
        summary_char_limit: int = DEFAULT_SUMMARY_CHAR_LIMIT,
    ) -> None:
        self.root = Path(str(root))
        self.path = Path(path) if path is not None else self.root / "summary" / "records.jsonl"
        self.summaries_path = (
            Path(summaries_path)
            if summaries_path is not None
            else self.path.parent / "summaries.jsonl"
        )
        ensure_directory(self.path.parent)
        self.store = JsonlStore(self.path)
        self.summaries_store = JsonlStore(self.summaries_path)
        self.threshold = max(int(threshold), 1)
        self.char_threshold = max(int(char_threshold), 1)
        self.keep_recent = max(int(keep_recent), 0)
        self.keep_chars = max(int(keep_chars), 1)
        self.summary_char_limit = max(int(summary_char_limit), 1)
        self.last_error: Optional[str] = None

    # -- 写 -------------------------------------------------------------------

    def add_memory(self, content: str, timestamp: Any = None, **fields: Any) -> str:
        """记一条待摘要的原文（旧签名兼容：前两个参数不变）。

        追加即返回，**不做压缩**：压缩只由 `compress()` 触发，
        调用方可以决定何时（或在 `context()` 里）执行。
        """
        entry = self.add(
            content, timestamp=timestamp, metadata=dict(fields.pop("metadata", {}) or {})
        )
        if fields:
            raise TypeError("add_memory 收到未知参数: " + ", ".join(sorted(fields)))
        return entry.id

    def add(
        self, content: str, *, timestamp: Any = None, metadata: Optional[Mapping[str, Any]] = None
    ) -> MemoryEntry:
        """追加一条待摘要记录。"""
        moment = timestamp if isinstance(timestamp, str) else stamp_of(timestamp)
        entry = MemoryEntry(
            content=content,
            timestamp=moment,
            created_at=moment,
            updated_at=moment,
            metadata=dict(metadata or {}),
        )
        self.remember(entry)
        return entry

    def remember(self, entry: MemoryEntry) -> str:
        """写入一条记录。

        刻意**不**在这里触发压缩：写入路径保持廉价且无副作用，
        压缩由 `compress()` 显式驱动（或在 `should_compress()` 为真时由
        调用方决定时机），这样「什么时候改写历史」是可观测的。
        """
        if not isinstance(entry, MemoryEntry):
            entry = MemoryEntry(content=str(entry))
        self.store.append(entry.to_dict())
        return entry.id

    def clear(self) -> None:
        """清空记录与摘要。"""
        self.store.truncate()
        self.summaries_store.truncate()

    # -- 压缩 -----------------------------------------------------------------

    def pending(self) -> List[MemoryEntry]:
        """尚未被压缩的记录（按时间升序）。"""
        return sort_by_time([item for item in self._load_records() if not item.forgotten])

    def archived(self) -> List[MemoryEntry]:
        """已被压进摘要的记录（仍保留在磁盘上）。"""
        return sort_by_time([item for item in self._load_records() if item.forgotten])

    def pending_chars(self) -> int:
        return sum(len(item.content) for item in self.pending())

    def should_compress(self) -> bool:
        """是否达到压缩阈值（条数或字符数任一满足）。"""
        pending = self.pending()
        if len(pending) >= self.threshold:
            return True
        return sum(len(item.content) for item in pending) >= self.char_threshold

    def compress(self, *, force: bool = False) -> Optional[MemoryEntry]:
        """把最旧的若干条记录压成一条摘要。

        * 未达阈值且未 `force` 时返回 `None`（不改动任何数据）；
        * 可压缩条数不足以构成一次压缩（`<= keep_recent`）时返回 `None`；
        * 原文保留并遮蔽，摘要追加到摘要文件；
        * 返回值即新建的摘要条目。
        """
        pending = self.pending()
        if not force and not self.should_compress():
            return None
        if len(pending) <= self.keep_recent:
            return None

        batch = pending[: len(pending) - self.keep_recent] if self.keep_recent else list(pending)
        if not batch:
            return None

        summary_entry = self._build_summary(batch, index=len(self.summaries()) + 1)
        self.summaries_store.append(summary_entry.to_dict())

        batch_ids = {item.id for item in batch}
        records: List[MemoryEntry] = []
        for record in self._load_records():
            current = record
            if record.id in batch_ids:
                current = record.patched(
                    forgotten=True,
                    metadata=dict(record.metadata or {}, compressed_into=summary_entry.id),
                )
            records.append(current)
        self.store.replace([item.to_dict() for item in records])
        return summary_entry

    def _build_summary(self, batch: Sequence[MemoryEntry], index: int) -> MemoryEntry:
        """确定性摘要构造（纯函数，便于用例直接断言文本形状）。

        普通内容与未完成事项走**两条不同的处理路径**：

        * 普通内容：折叠空白 → 去重 → 首尾截断 → 受总长度上限约束；
        * 未完成事项：**原样保留**（不折叠、不去重、不截断、不受上限约束）。

        第二条路径必须在空白折叠**之前**分流（t14 修）：早期实现先折叠再判定，
        于是 `TODO 未完成：给用户准备\n生日惊喜` 里的换行被换成空格，
        摘要里出现的已经不是原文，逐字核对与「原文字符数不变」都无从谈起。
        """
        normal_lines: List[str] = []
        unfinished_lines: List[str] = []
        seen: Dict[str, None] = {}
        for record in batch:
            raw = record.content or ""
            if not raw.strip():
                continue
            if has_unfinished_marker(raw):
                # 原文一字不动地保留（含内部换行与连续空白）
                unfinished_lines.append(raw)
                continue
            text = " ".join(raw.split())
            key = text.lower()
            if len(text) >= MIN_DEDUPE_LENGTH and key in seen:
                continue
            seen.setdefault(key, None)
            normal_lines.append(text)

        head = (
            f"【压缩摘要 #{index}】"
            f"{batch[0].created_at} → {batch[-1].created_at}"
            f"（{len(batch)} 条）"
        )

        body_parts: List[str] = []
        used = 0
        for text in normal_lines:
            rendered = truncate_middle(text, self.keep_chars)
            line = f"- {rendered}"
            if used and used + len(line) + 1 > self.summary_char_limit:
                break
            body_parts.append(line)
            used += len(line) + 1

        parts = [head]
        if body_parts:
            parts.append("\n".join(body_parts))
        if unfinished_lines:
            # 未完成事项整段保留、不截断，也不受总量上限影响
            parts.append(UNFINISHED_HEADING)
            parts.extend(f"- {text}" for text in unfinished_lines)

        stamp = batch[-1].created_at or stamp_of()
        return MemoryEntry(
            content="\n".join(parts),
            source=SOURCE_SUMMARY,
            timestamp=stamp,
            created_at=stamp,
            updated_at=stamp,
            metadata={
                "compressed_ids": [item.id for item in batch],
                "compressed_count": len(batch),
                "unfinished_count": len(unfinished_lines),
                "summary_index": index,
            },
        )

    # -- 读 -------------------------------------------------------------------

    def summaries(self) -> List[MemoryEntry]:
        """全部摘要（按时间升序）。"""
        return sort_by_time(self._load_summaries())

    def recall(self, query: str, k: int = 5) -> List[MemoryEntry]:
        """召回摘要与尚未压缩的记录（摘要权重更高，优先返回）。

        含未完成标记的条目按原文返回，不做任何截断。
        """
        text = (query or "").strip()
        if k <= 0:
            return []
        candidates: List[Tuple[float, str, MemoryEntry]] = []
        for entry, weight in [(item, SUMMARY_WEIGHT) for item in self.summaries()] + [
            (item, RECORD_WEIGHT) for item in self.pending()
        ]:
            base = relevance(text, entry) if text else 0.0
            if text and base <= 0.0:
                continue
            freshness = recency_weight(entry)
            score = (base * weight * 0.7) + freshness * 0.3
            candidates.append((score, entry.id, entry))
        candidates.sort(key=lambda item: (-item[0], item[1]))
        return [item[2] for item in candidates[:k]]

    def search_memories(self, query: str, n_results: Optional[int] = None) -> List[str]:
        """`[时间] 正文` 形式的召回结果（三种模式共用的读接口）。"""
        limit = DEFAULT_SEARCH_RESULTS if n_results is None else int(n_results)
        return [entry.with_prefix() for entry in self.recall(query, limit)]

    @property
    def collection(self) -> Any:
        """旧 `MemoryManager.collection` 的占位：本模式没有向量集合。"""
        return None

    def recent_records(self, k: int = 5) -> List[MemoryEntry]:
        """最近 k 条记录（不区分是否已压缩），最新的在前。"""
        records = sort_by_time(self._load_records())
        records.reverse()
        return records[: max(k, 0)]

    def count(self) -> int:
        """记忆条数 = 摘要数 + 未压缩记录数。"""
        return len(self.summaries()) + len(self.pending())

    def entries(self) -> List[MemoryEntry]:
        return sort_by_time(self._load_records() + self._load_summaries())

    # -- 内部 -----------------------------------------------------------------

    def _load_records(self) -> List[MemoryEntry]:
        return self._load(self.store)

    def _load_summaries(self) -> List[MemoryEntry]:
        return self._load(self.summaries_store)

    def _load(self, store: JsonlStore) -> List[MemoryEntry]:
        try:
            return [MemoryEntry.from_dict(item) for item in store.load()]
        except MemoryStoreError as exc:
            self.last_error = str(exc)
            return []


__all__ = [
    "DEFAULT_CHAR_THRESHOLD",
    "DEFAULT_KEEP_CHARS",
    "DEFAULT_KEEP_RECENT",
    "DEFAULT_SEARCH_RESULTS",
    "DEFAULT_SUMMARY_CHAR_LIMIT",
    "DEFAULT_THRESHOLD",
    "MIN_DEDUPE_LENGTH",
    "RECORD_WEIGHT",
    "SUMMARY_WEIGHT",
    "UNFINISHED_HEADING",
    "SummaryMemory",
    "truncate_middle",
]
