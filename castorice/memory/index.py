"""向量索引后端：把「真正的存储」与「embedding 引擎」解耦。

embedding 模式必须沿用 ChromaDB（契约要求），但「向量库可以用」这件事
不该成为测试与降级路径的前提：本模块把索引抽象成一个最小接口
（`add / query / delete / count / clear / all_records / as_collection`），
于是：

* 生产环境注入 ChromaDB 集合，行为与现状一致；
* 没有 chromadb 的环境（或只想验证纯逻辑的用例）注入
  `InMemoryIndex`，三种模式的读写接口依然可用。

`chromadb` 只在本模块的工厂函数里被延迟导入，`castorice` 包在无向量库的
环境下仍可被导入与测试（对齐 `castorice/__init__.py` 的分层约束）。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

#: 与 ai.py 旧实现相同的默认集合名与目录
DEFAULT_COLLECTION_NAME = "memories"
DEFAULT_CHROMA_DIR = "database"


class IndexUnavailable(RuntimeError):
    """向量索引不可用（未安装 chromadb、集合损坏等）。"""


class IndexBackend:
    """向量索引的最小接口。

    ID 一律由调用方给定（改掉旧实现用 `count()+1` 生成 ID 的重号隐患）。
    `query` 返回三元组 `(ids, documents, metadatas)`，三者等长且保持
    相似度降序，调用方无需再回查正文。
    """

    #: 是否支持文本查询（Chroma 自带本地 embedding；纯 Python 实现用关键词兜底）
    supports_text_query = False

    def add(
        self, ids: Sequence[str], documents: Sequence[str], metadatas: Sequence[Mapping[str, Any]]
    ) -> None:
        raise NotImplementedError

    def query(
        self, query: str, n_results: int
    ) -> Tuple[List[str], List[str], List[Mapping[str, Any]]]:
        raise NotImplementedError

    def delete(self, ids: Sequence[str]) -> None:
        raise NotImplementedError

    def count(self) -> int:
        raise NotImplementedError

    def clear(self) -> None:
        raise NotImplementedError

    def all_records(self) -> Tuple[List[str], List[str], List[Mapping[str, Any]]]:
        """返回全部记录 `(ids, documents, metadatas)`，用于迁移与幂等写入检查。"""
        raise NotImplementedError

    def as_collection(self) -> Any:
        """暴露底层集合对象（`MemoryManager.collection` 需要原样返回）。"""
        return getattr(self, "_collection", None)


_WORD_RE = re.compile(r"[0-9A-Za-z_]+|[\u4e00-\u9fff]")


def tokenize(text: str) -> List[str]:
    """中英混排的极简分词：英文/数字按词、中文按字。

    只服务于纯 Python 兜底检索，不引入任何外部分词依赖（打包成 exe 时
    不需要额外资源文件）。
    """
    return _WORD_RE.findall((text or "").lower())


def keyword_score(query: str, document: str) -> float:
    """0..1 的关键词重合度（按查询侧归一化的 Jaccard）。"""
    query_tokens = set(tokenize(query))
    if not query_tokens:
        return 0.0
    doc_tokens = set(tokenize(document))
    if not doc_tokens:
        return 0.0
    return len(query_tokens & doc_tokens) / float(len(query_tokens))


class InMemoryIndex(IndexBackend):
    """纯 Python 的向量索引替身。

    检索按关键词重合度打分；全部为 0 时退化为「越新越靠前」，
    保证 `n_results`（返回条数）语义与 Chroma 一致，便于用例断言。
    """

    supports_text_query = True

    def __init__(self) -> None:
        self._ids: List[str] = []
        self._documents: List[str] = []
        self._metadatas: List[Dict[str, Any]] = []

    def add(
        self, ids: Sequence[str], documents: Sequence[str], metadatas: Sequence[Mapping[str, Any]]
    ) -> None:
        for memory_id, document, metadata in zip(ids, documents, metadatas):
            key = str(memory_id)
            if key in self._ids:
                raise IndexUnavailable(f"向量索引里已存在 id: {key}")
            self._ids.append(key)
            self._documents.append(str(document))
            self._metadatas.append(dict(metadata or {}))

    def query(
        self, query: str, n_results: int
    ) -> Tuple[List[str], List[str], List[Mapping[str, Any]]]:
        if n_results <= 0 or not self._ids:
            return [], [], []
        scored: List[Tuple[float, int]] = [
            (keyword_score(query, document), position)
            for position, document in enumerate(self._documents)
        ]
        scored.sort(key=lambda item: (-item[0], -item[1]))
        picked = [position for _score, position in scored[:n_results]]
        return (
            [self._ids[position] for position in picked],
            [self._documents[position] for position in picked],
            [dict(self._metadatas[position]) for position in picked],
        )

    def delete(self, ids: Sequence[str]) -> None:
        targets = {str(item) for item in ids}
        keep = [i for i, key in enumerate(self._ids) if key not in targets]
        self._ids = [self._ids[i] for i in keep]
        self._documents = [self._documents[i] for i in keep]
        self._metadatas = [self._metadatas[i] for i in keep]

    def count(self) -> int:
        return len(self._ids)

    def clear(self) -> None:
        self._ids.clear()
        self._documents.clear()
        self._metadatas.clear()

    def all_records(self) -> Tuple[List[str], List[str], List[Mapping[str, Any]]]:
        return list(self._ids), list(self._documents), [dict(m) for m in self._metadatas]


class ChromaIndex(IndexBackend):
    """ChromaDB 集合的薄包装（生产路径，与 ai.MemoryManager 完全同构）。"""

    supports_text_query = True

    def __init__(self, collection: Any) -> None:
        self._collection = collection

    @property
    def collection(self) -> Any:
        return self._collection

    def add(
        self, ids: Sequence[str], documents: Sequence[str], metadatas: Sequence[Mapping[str, Any]]
    ) -> None:
        self._collection.add(
            ids=[str(item) for item in ids],
            documents=[str(item) for item in documents],
            metadatas=[dict(item or {}) for item in metadatas],
        )

    def query(
        self, query: str, n_results: int
    ) -> Tuple[List[str], List[str], List[Mapping[str, Any]]]:
        if n_results <= 0:
            return [], [], []
        raw = (
            self._collection.query(
                query_texts=[query],
                n_results=n_results,
                include=["documents", "metadatas"],
            )
            or {}
        )
        ids = [str(item) for item in ((raw.get("ids") or [[]])[0] or [])]
        raw_documents = (raw.get("documents") or [[]])[0] or []
        raw_metadatas = (raw.get("metadatas") or [[]])[0] or []
        documents = [
            str(raw_documents[index])
            if index < len(raw_documents) and raw_documents[index] is not None
            else ""
            for index in range(len(ids))
        ]
        metadatas: List[Mapping[str, Any]] = [
            dict(raw_metadatas[index])
            if index < len(raw_metadatas) and raw_metadatas[index]
            else {}
            for index in range(len(ids))
        ]
        return ids, documents, metadatas

    def delete(self, ids: Sequence[str]) -> None:
        if not ids:
            return
        self._collection.delete(ids=[str(item) for item in ids])

    def count(self) -> int:
        return int(self._collection.count())

    def clear(self) -> None:
        """清空集合。

        `delete(where=None)` 的语义在不同 Chroma 版本上不够明确，因此显式给
        一个恒真条件；失败则回退到「全量读取后按 id 删除」。
        """
        try:
            self._collection.delete(where={"timestamp": {"$ne": ""}})
            return
        except Exception:
            pass
        try:
            records = self._collection.get(include=[])
            ids = list(records.get("ids") or [])
            if ids:
                self._collection.delete(ids=ids)
        except Exception as exc:  # pragma: no cover - 取决于 Chroma 版本
            raise IndexUnavailable(f"清空向量集合失败: {exc}") from exc

    def all_records(self) -> Tuple[List[str], List[str], List[Mapping[str, Any]]]:
        raw = self._collection.get(include=["documents", "metadatas"]) or {}
        ids = [str(item) for item in (raw.get("ids") or [])]
        documents = [str(item or "") for item in (raw.get("documents") or [])]
        metadatas = [dict(item or {}) for item in (raw.get("metadatas") or [])]
        while len(documents) < len(ids):
            documents.append("")
        while len(metadatas) < len(ids):
            metadatas.append({})
        return ids, documents, metadatas

    def as_collection(self) -> Any:
        return self._collection


def build_index(
    chroma_dir: Any = DEFAULT_CHROMA_DIR,
    collection_name: str = DEFAULT_COLLECTION_NAME,
    *,
    backend: Optional[IndexBackend] = None,
) -> IndexBackend:
    """构造向量索引。

    `backend` 显式给定时直接使用（测试或显式降级）；否则按 `chroma_dir` /
    `collection_name` 建 ChromaDB 持久化集合，行为与 `ai.MemoryManager`
    一致（目录自动创建 + `get_or_create_collection`）。
    """
    if backend is not None:
        return backend

    try:  # 延迟导入：没有 chromadb 时本包其余部分依然可用
        import chromadb
    except ImportError as exc:  # pragma: no cover - 取决于环境
        raise IndexUnavailable(
            "未安装 chromadb，无法使用向量记忆；请先 pip install -r requirements.txt"
        ) from exc

    directory = Path(str(chroma_dir))
    directory.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(directory))
    collection = client.get_or_create_collection(name=collection_name)
    return ChromaIndex(collection)


__all__ = [
    "DEFAULT_CHROMA_DIR",
    "DEFAULT_COLLECTION_NAME",
    "ChromaIndex",
    "InMemoryIndex",
    "IndexBackend",
    "IndexUnavailable",
    "build_index",
    "keyword_score",
    "tokenize",
]
