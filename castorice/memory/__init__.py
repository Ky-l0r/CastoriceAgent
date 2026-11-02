"""记忆引擎：三种可切换的记忆模式 + DMAE Worldbook。

```
   build_memory_engine({"engine": "wiki", "root": "memory"})
        │
        ├─ embedding（默认）  EmbeddingMemory  → ChromaDB 向量检索
        ├─ wiki               WikiMemory       → 结构化条目 JSONL + 关键词
        └─ summary            SummaryMemory    → 滚动摘要 + 确定性压缩
                               Worldbook       → 触发词 + 优先级 + 生命周期
```

三条设计约束：

1. **旧接口不变**：三种引擎都提供 `add_memory` / `search_memories` / `clear`
   / `count()`，embedding 还提供 `collection`，因此把 `ai.MemoryManager`
   换成任何一个引擎都不会破坏既有调用方（ai.py:2038 检索、ai.py:2105 写入、
   ai.py:2192 清空、ai.py:2218 计数）。
2. **旧数据照读**：embedding 用同一个 `chroma_dir` + `collection_name`，
   既有 `database/` 里的向量与 metadata 原样可读出，并带
   `[YYYY-MM-DD HH:MM:SS]` 前缀（旧数据没记时间则保持原样）。
3. **可注入**：`index` 参数允许把向量后端换成 `InMemoryIndex`，
   于是没有 chromadb 的环境也能验证三种模式的读写与生命周期。

上下文注入顺序（`MemoryEngineAdapter.context`）：

```
  Worldbook 优先（确定性设定：命中就必须想起）
      ↓
  当前模式的召回结果（相关性降序）
```

Worldbook 命中在前，是因为它是「不容置疑的设定」；召回结果在后，
是因为它们只是「可能相关的往事」。两段之间用空行分隔，便于模型区分。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .embedding import DEFAULT_SEARCH_RESULTS, EmbeddingMemory
from .entries import (
    UNFINISHED_CN_WORDS,
    UNFINISHED_EN_WORDS,
    UNFINISHED_NOUNS,
    MemoryEntry,
    has_unfinished_marker,
    new_memory_id,
    normalize_tags,
    recency_weight,
    sort_by_time,
    stamp_of,
    unfinished_markers,
)
from .index import (
    DEFAULT_CHROMA_DIR,
    DEFAULT_COLLECTION_NAME,
    ChromaIndex,
    IndexBackend,
    IndexUnavailable,
    InMemoryIndex,
    build_index,
)
from .store import (
    JsonlStore,
    MemoryStoreError,
    atomic_write_text,
    read_json,
    read_jsonl,
    write_json,
    write_jsonl,
)
from .summary import DEFAULT_THRESHOLD as SUMMARY_DEFAULT_THRESHOLD
from .summary import SummaryMemory
from .wiki import WikiMemory
from .worldbook import (
    STATE_ACTIVE,
    STATE_ARCHIVED,
    STATE_EVICTED,
    STATES,
    Worldbook,
    WorldbookEntry,
    format_worldbook,
)

#: 三种模式名（封闭集合）
MODE_EMBEDDING = "embedding"
MODE_WIKI = "wiki"
MODE_SUMMARY = "summary"
ENGINE_NAMES: Tuple[str, ...] = (MODE_EMBEDDING, MODE_WIKI, MODE_SUMMARY)

#: 默认模式：embedding（行为与现状一致）
DEFAULT_ENGINE = MODE_EMBEDDING

#: 默认落盘根目录
DEFAULT_ROOT = "memory"

#: 注入上下文的默认条数上限
DEFAULT_CONTEXT_LIMIT = 5

#: 各模式接受的构造参数名（配置里多余的键被安全忽略并记录到 `ignored_options`）
_ENGINE_OPTIONS: Dict[str, frozenset] = {
    MODE_EMBEDDING: frozenset(
        {
            "chroma_dir",
            "collection_name",
            "path",
            "index",
            "search_results",
            "recency_weight",
            "recency_half_life_days",
        }
    ),
    MODE_WIKI: frozenset({"path", "max_entries", "recency_weight"}),
    MODE_SUMMARY: frozenset(
        {
            "path",
            "summaries_path",
            "threshold",
            "char_threshold",
            "keep_recent",
            "keep_chars",
            "summary_char_limit",
        }
    ),
}


def normalize_engine_name(value: Any) -> str:
    """把配置里的引擎名归一化；未知名字抛 `ValueError`（不静默退回默认）。"""
    name = str(value if value is not None else DEFAULT_ENGINE).strip().lower()
    if not name:
        name = DEFAULT_ENGINE
    if name not in ENGINE_NAMES:
        raise ValueError(f"未知的记忆模式: {name!r}（可选：{', '.join(ENGINE_NAMES)}）")
    return name


def build_memory_engine(config: Optional[Mapping[str, Any]] = None, **overrides: Any):
    """按配置构造记忆引擎（`memory.engine` 决定模式）。

    配置项（全部可选，缺失时走代码内默认值）：

    ```yaml
    memory:
      engine: "embedding"        # embedding | wiki | summary
      root: "memory"             # wiki/summary/worldbook 的落盘根目录
      chroma_dir: "database"     # 仅 embedding 用
      collection_name: "memories"
      summary_threshold: 40
    ```

    额外关键字参数作为构造参数透传（如 `index=InMemoryIndex()`），
    便于测试注入替身。
    """
    options: Dict[str, Any] = {k: v for k, v in dict(config or {}).items() if v is not None}
    options.update(overrides)
    name = normalize_engine_name(options.pop("engine", DEFAULT_ENGINE))
    root = Path(str(options.pop("root", DEFAULT_ROOT) or DEFAULT_ROOT))
    root.mkdir(parents=True, exist_ok=True)

    #: 各模式接受的构造参数；配置里属于「别的模式」的键在这里被安全丢弃，
    #: 不会因为多写了一个 key 就让引擎构造失败。
    if name == MODE_EMBEDDING:
        text_dir = options.get("path")
        if text_dir is None:
            collection_name = options.get("collection_name", DEFAULT_COLLECTION_NAME)
            options["path"] = root / f"{collection_name}_text"
        options.setdefault("chroma_dir", DEFAULT_CHROMA_DIR)
    elif name == MODE_SUMMARY:
        options.setdefault("threshold", options.pop("summary_threshold", SUMMARY_DEFAULT_THRESHOLD))

    accepted = _ENGINE_OPTIONS[name]
    kept = {key: value for key, value in options.items() if key in accepted}
    ignored = sorted(set(options) - set(kept))
    if name == MODE_EMBEDDING:
        engine = EmbeddingMemory(**kept)
    elif name == MODE_WIKI:
        engine = WikiMemory(root=root, **kept)
    else:
        engine = SummaryMemory(root=root, **kept)
    engine.ignored_options = ignored
    return engine


@dataclass
class MemoryContext:
    """一次上下文注入的完整材料（文本 + 结构化来源）。"""

    text: str = ""
    worldbook: Tuple[WorldbookEntry, ...] = ()
    memories: Tuple[MemoryEntry, ...] = ()
    engine: str = DEFAULT_ENGINE

    def __bool__(self) -> bool:
        return bool(self.text)

    def __str__(self) -> str:  # pragma: no cover - 调试便利
        return self.text


class MemoryEngineAdapter:
    """把「某个记忆模式 + Worldbook」合成对对话主流程友好的一个门面。

    职责只有两件事：

    * `remember_conversation(user, reply)`：把一轮问答写进记忆；
    * `context(query)`：为下一轮请求准备「Worldbook + 召回」文本。

    `ai.AI` 只需要这两件事，模式细节（写 JSONL 还是写向量库、
    要不要先压缩）全部收在引擎里，符合「模式可切换、调用方无感」的要求。
    """

    def __init__(
        self,
        engine: Any,
        *,
        worldbook: Optional[Worldbook] = None,
        engine_name: Optional[str] = None,
        context_limit: int = DEFAULT_CONTEXT_LIMIT,
    ) -> None:
        self.engine = engine
        self.worldbook = (
            worldbook
            if worldbook is not None
            else Worldbook(getattr(engine, "root", DEFAULT_ROOT), engine=self)
        )
        self.engine_name = engine_name or normalize_engine_name(
            getattr(engine, "name", DEFAULT_ENGINE)
        )
        self.context_limit = max(int(context_limit), 1)

    # -- 便捷构造 -------------------------------------------------------------

    @classmethod
    def from_config(
        cls, config: Optional[Mapping[str, Any]] = None, **overrides: Any
    ) -> MemoryEngineAdapter:
        """按配置一次装配「引擎 + Worldbook」。"""
        options = {k: v for k, v in dict(config or {}).items() if v is not None}
        options.update(overrides)
        enabled = bool(options.pop("worldbook_enabled", True))
        engine = build_memory_engine(options)
        worldbook = Worldbook(
            getattr(engine, "root", options.get("root", DEFAULT_ROOT)),
            engine=engine,
            enabled=enabled,
        )
        return cls(engine, worldbook=worldbook, engine_name=engine.name)

    # -- 写 -------------------------------------------------------------------

    def remember(self, content: str, **fields: Any) -> str:
        """写入一条记忆（按当前模式选择落点）。"""
        if self.engine_name == MODE_WIKI:
            entry = self.engine.add(content, **fields)
            return entry.id
        if self.engine_name == MODE_SUMMARY:
            return self.engine.add_memory(content, **fields)
        return self.engine.add_memory(content, **fields)

    def remember_conversation(self, user_text: str, reply_text: str) -> str:
        """把一轮问答写进记忆（与 ai.py:2104 的既有格式逐字一致）。"""
        content = f"用户: {user_text}\n助理: {reply_text}"
        return self.remember(content)

    # -- 读 -------------------------------------------------------------------

    def recall(self, query: str, k: Optional[int] = None) -> List[MemoryEntry]:
        """只召回当前模式的条目（不含 Worldbook）。"""
        limit = self.context_limit if k is None else int(k)
        return list(self.engine.recall(query, limit))

    def recall_texts(self, query: str, k: Optional[int] = None) -> List[str]:
        """召回的文本列表（带 `[时间]` 前缀，形状与旧实现一致）。"""
        return [entry.with_prefix() for entry in self.recall(query, k)]

    def worldbook_entries(self, query: str, limit: Optional[int] = None) -> List[WorldbookEntry]:
        """命中且**已记录访问**的 Worldbook 条目。"""
        return self.worldbook.relevant(query, limit=limit)

    def context(self, query: str, k: Optional[int] = None) -> MemoryContext:
        """组装一次上下文注入：Worldbook 在前，召回结果在后。"""
        limit = self.context_limit if k is None else int(k)
        text = (query or "").strip()
        entries: Tuple[WorldbookEntry, ...] = ()
        memories: Tuple[MemoryEntry, ...] = ()
        blocks: List[str] = []
        if text:
            entries = tuple(self.worldbook.relevant(text, limit=limit))
            if entries:
                blocks.append("【Worldbook】\n" + format_worldbook(list(entries)))
            remembered = self.recall(text, limit)
            memories = tuple(remembered)
            if remembered:
                blocks.append(
                    "【相关记忆】\n" + "\n".join(entry.with_prefix() for entry in remembered)
                )
        return MemoryContext(
            text="\n\n".join(blocks),
            worldbook=entries,
            memories=memories,
            engine=self.engine_name,
        )

    # -- 维护 -----------------------------------------------------------------

    def compress(self, *, force: bool = False):
        """summary 模式专用：触发一次滚动压缩（其它模式返回 `None`）。"""
        compress = getattr(self.engine, "compress", None)
        if compress is None:
            return None
        return compress(force=force)

    def should_compress(self) -> bool:
        checker = getattr(self.engine, "should_compress", None)
        return bool(checker()) if checker is not None else False

    def clear(self) -> None:
        """清空当前模式的记忆（不动 Worldbook）。"""
        self.engine.clear()

    def clear_all(self) -> None:
        """清空当前模式的记忆与 Worldbook（「清空记忆」按钮的语义）。"""
        self.engine.clear()
        self.worldbook.clear()

    @property
    def count(self) -> int:
        """记忆条数（与旧 `MemoryManager.count` 一样是属性，不是方法）。"""
        counter = self.engine.count
        return int(counter() if callable(counter) else counter)

    # -- 透传：保证引擎的旧接口对调用方依然可见 -------------------------------

    def __getattr__(self, item: str) -> Any:
        # 只在下述「明确已知」的名字上透传，避免掩盖拼写错误
        if item in {
            "add_memory",
            "search_memories",
            "collection",
            "entries",
            "recall",
            "recall_texts",
            "get",
            "update",
            "forget",
            "recent",
            "summaries",
            "pending",
            "should_compress",
            "compress",
        }:
            return getattr(self.engine, item)
        raise AttributeError(item)


__all__ = [
    "DEFAULT_CHROMA_DIR",
    "DEFAULT_COLLECTION_NAME",
    "DEFAULT_CONTEXT_LIMIT",
    "DEFAULT_ENGINE",
    "DEFAULT_ROOT",
    "DEFAULT_SEARCH_RESULTS",
    "ENGINE_NAMES",
    "MODE_EMBEDDING",
    "MODE_SUMMARY",
    "MODE_WIKI",
    "STATES",
    "STATE_ACTIVE",
    "STATE_ARCHIVED",
    "STATE_EVICTED",
    "UNFINISHED_CN_WORDS",
    "UNFINISHED_EN_WORDS",
    "UNFINISHED_NOUNS",
    "ChromaIndex",
    "EmbeddingMemory",
    "InMemoryIndex",
    "IndexBackend",
    "IndexUnavailable",
    "JsonlStore",
    "MemoryContext",
    "MemoryEngineAdapter",
    "MemoryEntry",
    "MemoryStoreError",
    "SummaryMemory",
    "WikiMemory",
    "Worldbook",
    "WorldbookEntry",
    "atomic_write_text",
    "build_index",
    "build_memory_engine",
    "format_worldbook",
    "has_unfinished_marker",
    "new_memory_id",
    "normalize_engine_name",
    "normalize_tags",
    "read_json",
    "read_jsonl",
    "recency_weight",
    "sort_by_time",
    "stamp_of",
    "unfinished_markers",
    "write_json",
    "write_jsonl",
]
