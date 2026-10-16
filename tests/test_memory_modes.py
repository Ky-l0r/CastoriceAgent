"""wiki / summary / embedding 三种记忆模式与 DMAE Worldbook 生命周期。

覆盖范围（对应 t6 的验收条款）：

1. 三种模式各有独立的存储与读写接口，可经构造参数切换，默认 embedding；
2. Worldbook 条目带触发词 / 优先级 / 生命周期状态 / 访问计数，支持归档与淘汰；
3. 旧 MemoryManager 语义（add_memory / search_memories / clear / collection /
   count）保留，既有 database/ 数据仍可读出并带 [时间] 前缀；
4. summary 在超长历史下有确定性压缩策略，压缩不丢失未完成事项标记；
5. 全部用例用 tmp_path，不写仓库的 database/ / memory/。

约定（继承 tests/conftest.py 与 test_offline_guarantees.py 的护栏）：
- 不联网；不 monkeypatch 任何业务实现（一律用参数注入固定时间/替换后端）。
"""

import shutil
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import ai
from castorice.memory import (
    DEFAULT_ENGINE,
    ENGINE_NAMES,
    MODE_EMBEDDING,
    MODE_SUMMARY,
    MODE_WIKI,
    STATE_ACTIVE,
    STATE_ARCHIVED,
    STATE_EVICTED,
    EmbeddingMemory,
    InMemoryIndex,
    JsonlStore,
    MemoryEngineAdapter,
    MemoryEntry,
    MemoryStoreError,
    SummaryMemory,
    WikiMemory,
    Worldbook,
    WorldbookEntry,
    atomic_write_text,
    build_index,
    build_memory_engine,
    format_worldbook,
    has_unfinished_marker,
    new_memory_id,
    normalize_engine_name,
    read_jsonl,
    unfinished_markers,
    write_jsonl,
)
from castorice.memory.entries import _UNFINISHED_RE

PROJECT_ROOT = Path(ai.__file__).resolve().parent
REPOSITORY_DATABASE = PROJECT_ROOT / "database"


# ============================================================================
# 夹具
# ============================================================================

@pytest.fixture
def memory_root(tmp_path):
    """记忆落盘根目录（永远在临时目录里）"""
    root = tmp_path / "memory"
    root.mkdir()
    return root


@pytest.fixture(autouse=True)
def repository_database_is_untouched():
    """整份用例集都不许碰仓库的 database/（比对文件指纹）"""
    before = _fingerprint(REPOSITORY_DATABASE)
    yield
    assert _fingerprint(REPOSITORY_DATABASE) == before, (
        "测试改动了仓库的 database/，必须只用 tmp_path"
    )


def _fingerprint(directory: Path):
    """目录指纹：相对路径 -> (大小, mtime_ns)"""
    if not directory.exists():
        return None
    records = []
    for path in sorted(directory.rglob("*")):
        if path.is_file():
            stat = path.stat()
            records.append((path.relative_to(directory).as_posix(),
                            stat.st_size, stat.st_mtime_ns))
    return tuple(records)


def _engine(name, root, **kwargs):
    """在临时目录里造一个指定模式的引擎（不碰真实向量库）"""
    return build_memory_engine({
        "engine": name, "root": root, "index": InMemoryIndex(), **kwargs,
    })


# ============================================================================
# 1. 模式切换与独立存储
# ============================================================================

def test_default_engine_is_embedding(tmp_path):
    assert DEFAULT_ENGINE == MODE_EMBEDDING
    assert ENGINE_NAMES == (MODE_EMBEDDING, MODE_WIKI, MODE_SUMMARY)
    # 只给一个临时根目录，其余全部走代码内默认值
    engine = build_memory_engine({"root": tmp_path}, index=InMemoryIndex())
    assert engine.name == MODE_EMBEDDING
    assert engine.search_results == 5
    assert engine.path == tmp_path / "memories_text"


def test_build_engine_switches_by_constructor_argument(memory_root):
    wiki = _engine("wiki", memory_root)
    summary = _engine("summary", memory_root)
    embedding = _engine("embedding", memory_root)
    assert (wiki.name, summary.name, embedding.name) == ("wiki", "summary", "embedding")
    assert isinstance(wiki, WikiMemory)
    assert isinstance(summary, SummaryMemory)
    assert isinstance(embedding, EmbeddingMemory)


def test_engine_name_is_normalized(memory_root):
    assert normalize_engine_name(None) == MODE_EMBEDDING
    assert normalize_engine_name("") == MODE_EMBEDDING
    assert normalize_engine_name("WIKI") == MODE_WIKI
    assert normalize_engine_name(" Summary ") == MODE_SUMMARY
    assert _engine("WIKI", memory_root).name == MODE_WIKI


def test_unknown_engine_name_raises():
    with pytest.raises(ValueError) as error:
        normalize_engine_name("vector")
    assert "未知的记忆模式" in str(error.value)
    with pytest.raises(ValueError):
        build_memory_engine({"engine": "vector", "index": InMemoryIndex()})


@pytest.mark.parametrize("name,folder", [
    (MODE_WIKI, "wiki"),
    (MODE_SUMMARY, "summary"),
])
def test_file_modes_write_to_their_own_paths(memory_root, name, folder):
    engine = _engine(name, memory_root)
    engine.add_memory("写入一条内容")
    assert engine.path == memory_root / folder / (
        "records.jsonl" if name == MODE_SUMMARY else "entries.jsonl")
    assert engine.path.exists(), "各模式的存储文件应当落在自己的目录下"


def test_embedding_mode_writes_vectors_and_text_layer(memory_root):
    engine = _engine("embedding", memory_root)
    engine.add_memory("向量模式的记忆")
    # 文本层与向量层的分工：向量库保存正文与 metadata，文本层保存结构化字段
    assert engine.path == memory_root / "memories_text"
    assert (engine.path / "entries.jsonl").exists()
    assert engine.count() == 1
    assert engine.collection is None  # 注入的是内存索引，没有 Chroma 集合


def test_modes_do_not_see_each_others_memories(memory_root):
    wiki = _engine("wiki", memory_root)
    summary = _engine("summary", memory_root)
    embedding = _engine("embedding", memory_root)

    wiki.add_memory("wiki 里的内容")
    summary.add_memory("summary 里的内容")
    embedding.add_memory("embedding 里的内容")

    assert (wiki.count(), summary.count(), embedding.count()) == (1, 1, 1)
    assert "wiki" in wiki.search_memories("wiki")[0]
    assert "summary" in summary.search_memories("summary")[0]
    assert "embedding" in embedding.search_memories("embedding")[0]
    assert wiki.search_memories("summary") == []
    assert summary.search_memories("wiki") == []


def test_engine_options_from_other_modes_are_ignored(memory_root):
    """配置里多写了别的模式的键，不应让引擎构造失败"""
    engine = build_memory_engine({
        "engine": "wiki", "root": memory_root, "index": InMemoryIndex(),
        "collection_name": "memories", "summary_threshold": 7,
    })
    assert engine.name == MODE_WIKI
    # `index` 也是 embedding 专属参数，对 wiki 同样应当被忽略而不是报错
    assert set(engine.ignored_options) == {
        "collection_name", "index", "summary_threshold"}


def test_every_engine_and_adapter_carry_the_legacy_manager_surface(memory_root):
    """旧 MemoryManager 的五个成员在每种模式与门面上都必须存在。

    ai.py 只用了这五件事：add_memory（写入）、search_memories（检索）、
    clear（清空）、collection（拿集合）、count（计数）。任何一个引擎缺一件，
    替换 MemoryManager 时就会在运行期炸掉。
    """
    for name in ENGINE_NAMES:
        engine = _engine(name, memory_root / name)
        for attribute in ("add_memory", "search_memories", "clear", "collection"):
            assert hasattr(engine, attribute), f"{name} 缺少 {attribute}"
        assert isinstance(engine.count(), int)
        # collection 只有 embedding 可能拿到真实集合，其余模式为 None
        assert engine.collection is None or name == MODE_EMBEDDING

    adapter = MemoryEngineAdapter.from_config(
        {"engine": "wiki", "root": memory_root / "adapter"})
    for attribute in ("add_memory", "search_memories", "clear", "collection"):
        assert hasattr(adapter, attribute), f"门面缺少 {attribute}"
    assert isinstance(adapter.count, int)
    assert adapter.collection is None


def test_embedding_exposes_a_real_collection_like_old_manager(tmp_path):
    """`collection` 属性必须给出真实 Chroma 集合（对齐旧 MemoryManager）"""
    engine = EmbeddingMemory(tmp_path / "database", "memories",
                             path=tmp_path / "text")
    collection = engine.collection
    assert collection is not None
    assert collection.count() == 0
    engine.add_memory("一条记忆")
    assert collection.count() == 1
    # 旧代码常见的用法：直接查集合
    assert collection.get(include=["metadatas"])["metadatas"][0]["timestamp"]


def test_each_mode_exposes_the_same_read_write_interface(memory_root):
    for name in ENGINE_NAMES:
        engine = _engine(name, memory_root / name)
        memory_id = engine.add_memory("统一的读写接口")
        assert isinstance(memory_id, str) and memory_id
        texts = engine.search_memories("统一")
        assert texts and texts[0].endswith("统一的读写接口")
        assert engine.count() == 1
        engine.clear()
        assert engine.count() == 0
        assert engine.search_memories("统一") == []


# ============================================================================
# 2. 旧 MemoryManager 语义保留 + 旧数据可读
# ============================================================================

def test_embedding_keeps_legacy_manager_semantics(memory_root):
    engine = _engine("embedding", memory_root)
    memory_id = engine.add_memory("用户喜欢喝美式咖啡",
                                  datetime(2024, 5, 1, 9, 30, 0))
    assert isinstance(memory_id, str) and memory_id
    assert engine.count() == 1
    texts = engine.search_memories("咖啡")
    assert texts == ["[2024-05-01 09:30:00] 用户喜欢喝美式咖啡"]
    engine.clear()
    assert engine.count() == 0
    assert engine.search_memories("咖啡") == []


def test_search_memories_default_returns_at_most_five(memory_root):
    engine = _engine("embedding", memory_root)
    for index in range(8):
        engine.add_memory(f"第 {index} 条记忆", datetime(2024, 1, 1, 0, index, 0))
    assert len(engine.search_memories("记忆")) == 5
    assert len(engine.search_memories("记忆", 2)) == 2


def test_new_ids_do_not_collide(memory_root):
    """旧实现用 count()+1 当 ID，删除或跳过写入会重号；这里必须不会"""
    assert len({new_memory_id() for _ in range(2000)}) == 2000
    engine = _engine("embedding", memory_root)
    first = engine.add_memory("第一条")
    engine.add_memory("第二条")
    assert engine.get(first).content == "第一条"
    engine.forget(first)
    third = engine.add_memory("第三条")
    assert third != first
    assert engine.get(first).content == "第一条", "旧记忆不得被覆盖"


def test_repository_database_has_legacy_entries_without_metadata(tmp_path):
    """仓库既有 database/ 的实际构成：58 条里多数是早期无 metadata 的记忆

    这条用例把「旧数据必须仍可读出」钉在真实数据上：无 metadata 的条目
    既不能丢，也不能被凭空补一个时间戳。
    """
    copied = tmp_path / "database"
    shutil.copytree(REPOSITORY_DATABASE, copied)
    engine = EmbeddingMemory(copied, "memories", path=tmp_path / "text")
    _ids, _documents, metadatas = engine.index.all_records()
    without = [item for item in metadatas if not item.get("timestamp")]
    assert without, "既有数据里应当有早期没有 metadata 的记忆"
    assert all(entry.content for entry in engine.entries()), "无 metadata 也要能读出正文"


def test_legacy_database_written_by_old_manager_is_readable(tmp_path):
    """既有 database/ 的形状：用旧 MemoryManager 写入，新引擎原样读出"""
    legacy_dir = tmp_path / "database"
    legacy = ai.MemoryManager(str(legacy_dir), "memories")
    legacy.add_memory("用户是程序员，主要写 Python",
                      datetime(2024, 3, 1, 9, 30, 0))
    legacy.add_memory("用户养了一只叫团子的猫",
                      datetime(2024, 3, 2, 10, 0, 0))
    assert legacy.count == 2

    engine = EmbeddingMemory(legacy_dir, "memories",
                             path=tmp_path / "text")
    assert engine.count() == 2
    texts = engine.search_memories("用户")
    assert texts, "旧库里的数据必须能被新引擎读出"
    assert all(text.startswith("[2024-03-0") for text in texts), texts
    assert any("程序员" in text for text in texts)
    assert engine.collection is not None
    assert engine.collection.count() == 2


def test_writes_from_new_engine_are_readable_by_old_manager(tmp_path):
    """反向兼容：新引擎写入的数据，旧 MemoryManager 必须照读（同一个集合）"""
    engine = EmbeddingMemory(tmp_path / "database", "memories",
                             path=tmp_path / "text")
    engine.add_memory("新引擎写入的记忆", datetime(2024, 4, 4, 4, 4, 4))

    legacy = ai.MemoryManager(str(tmp_path / "database"), "memories")
    assert legacy.count == 1
    assert legacy.search_memories("记忆") == [
        "[2024-04-04 04:04:04] 新引擎写入的记忆"]


def test_clear_empties_the_real_chroma_collection(tmp_path):
    """`clear()` 必须真的清空 Chroma 集合（旧 MemoryManager.clear 的语义）"""
    engine = EmbeddingMemory(tmp_path / "database", "memories",
                             path=tmp_path / "text")
    engine.add_memory("第一条")
    engine.add_memory("第二条")
    assert engine.collection.count() == 2

    engine.clear()
    assert engine.count() == 0
    assert engine.collection.count() == 0
    assert engine.search_memories("第一条") == []
    # 清空之后仍可继续写入（集合保留，不是删表重建）
    engine.add_memory("清空后的新记忆")
    assert engine.collection.count() == 1


def test_legacy_metadata_shape_is_preserved(tmp_path):
    """旧库 metadata 的四个键（timestamp/date/time/weekday）必须仍能读"""
    legacy_dir = tmp_path / "database"
    legacy = ai.MemoryManager(str(legacy_dir), "memories")
    legacy.add_memory("带完整时间信息的记忆", datetime(2024, 3, 1, 9, 30, 0))

    engine = EmbeddingMemory(legacy_dir, "memories", path=tmp_path / "text")
    entries = engine.recall("记忆")
    assert len(entries) == 1
    assert entries[0].timestamp == "2024-03-01 09:30:00"
    assert entries[0].with_prefix().startswith("[2024-03-01 09:30:00] ")
    raw = legacy.collection.get(include=["metadatas"])
    metadata = raw["metadatas"][0]
    assert metadata["timestamp"] == "2024-03-01 09:30:00"
    assert metadata["date"] == "2024-03-01"
    assert metadata["time"] == "09:30:00"
    assert metadata["weekday"] == "Friday"


@pytest.mark.skipif(not (REPOSITORY_DATABASE / "chroma.sqlite3").exists(),
                    reason="仓库里没有既有 database/")
def test_existing_repository_database_is_readable(tmp_path):
    """仓库既有 database/ 的真实数据：复制到临时目录后读出（仓库零写入）"""
    copied = tmp_path / "database"
    shutil.copytree(REPOSITORY_DATABASE, copied)
    engine = EmbeddingMemory(copied, "memories", path=tmp_path / "text")
    assert engine.count() >= 1, "既有 database/ 里应当能读到记忆"
    entries = engine.recall("用户", 5)
    assert entries, "既有数据必须能被召回"
    for entry in entries:
        assert entry.content, "旧数据的正文必须原样读出"
        assert entry.with_prefix().endswith(entry.content)

    # 旧库是历史累积的：早期的记忆没有 metadata（没有时间），
    # 后期写入的带 `timestamp`。两种都必须能读出来，带时间的要带上前缀。
    _ids, _documents, metadatas = engine.index.all_records()
    stamped = [item for item in metadatas if item.get("timestamp")]
    legacy = [item for item in metadatas if not item.get("timestamp")]
    assert stamped, "既有 database/ 里应当存在带时间戳的记忆"
    assert legacy, "既有 database/ 里应当存在没有时间戳的早期记忆"
    for meta in stamped:
        assert meta["timestamp"], meta
        assert meta["date"] and meta["time"], meta
    assert engine.search_memories("用户"), "旧数据必须能被检索到"


def test_repository_database_stays_readable_after_copy(tmp_path):
    """同一份既有数据用只读 sqlite 直查，交叉验证 count 的可信度"""
    database = tmp_path / "database"
    shutil.copytree(REPOSITORY_DATABASE, database)
    engine = EmbeddingMemory(database, "memories", path=tmp_path / "text")
    connection = sqlite3.connect(f"file:{database / 'chroma.sqlite3'}?mode=ro", uri=True)
    try:
        rows = connection.execute("select count(*) from embeddings").fetchone()
    finally:
        connection.close()
    assert engine.count() == rows[0]


def test_entries_without_timestamp_keep_plain_text():
    """旧数据没有记录时间时不加前缀（与既有实现一致）"""
    entry = MemoryEntry(content="没有时间的记忆", timestamp="", created_at="",
                        updated_at="")
    assert entry.with_prefix() == "没有时间的记忆"
    stamped = MemoryEntry(content="有时间的记忆", timestamp="2024-01-02 03:04:05")
    assert stamped.with_prefix() == "[2024-01-02 03:04:05] 有时间的记忆"


# ============================================================================
# 3. Worldbook：条目字段、匹配与上下文注入
# ============================================================================

@pytest.fixture
def worldbook(tmp_path):
    return Worldbook(tmp_path / "memory")


def test_worldbook_entry_carries_required_fields(worldbook):
    entry = worldbook.add(
        "称呼偏好", "用户希望被叫「小雨」",
        trigger_words=["叫我", "称呼"], priority=7,
    )
    assert entry.trigger_words == ("叫我", "称呼")
    assert entry.priority == 7
    assert entry.state == STATE_ACTIVE
    assert entry.access_count == 0
    assert entry.last_accessed == ""
    assert entry.created_at and entry.updated_at


def test_worldbook_rejects_unknown_state(tmp_path):
    with pytest.raises(ValueError) as error:
        WorldbookEntry(title="x", content="y", state="deleted")
    assert "未知的生命周期状态" in str(error.value)
    assert Worldbook(tmp_path / "memory").count() == 0


def test_worldbook_match_is_case_insensitive(worldbook):
    worldbook.add("偏好", "用户喜欢猫", trigger_words=["Cat"], priority=1)
    assert worldbook.match("我喜欢 cat 这种动物")
    assert worldbook.match("CAT 也可以")
    assert worldbook.match("和动物无关的话") == []


def test_worldbook_match_returns_nothing_for_empty_query(worldbook):
    worldbook.add("偏好", "用户喜欢猫", trigger_words=["猫"])
    assert worldbook.match("") == []
    assert worldbook.match("   ") == []


def test_worldbook_entry_without_triggers_never_matches(worldbook):
    worldbook.add("无触发词", "永远不该被注入")
    assert worldbook.match("任何文本都不会命中") == []
    assert worldbook.count() == 1


def test_worldbook_orders_by_hits_then_priority(worldbook):
    low = worldbook.add("低优先", "低", trigger_words=["猫"], priority=1)
    high = worldbook.add("高优先", "高", trigger_words=["猫"], priority=9)
    both = worldbook.add("双命中", "双", trigger_words=["猫", "咖啡"], priority=0)
    matched = worldbook.match("我有一只猫，也爱喝咖啡")
    assert [entry.id for entry in matched] == [both.id, high.id, low.id]


def test_worldbook_match_respects_limit(worldbook):
    for index in range(6):
        worldbook.add(f"条目{index}", f"内容{index}", trigger_words=["猫"],
                      priority=index)
    assert len(worldbook.match("猫")) == 5
    assert len(worldbook.match("猫", limit=2)) == 2
    assert worldbook.match("猫", limit=0) == []


def test_worldbook_match_is_side_effect_free(worldbook):
    entry = worldbook.add("偏好", "用户喜欢猫", trigger_words=["猫"])
    worldbook.match("猫")
    worldbook.match("猫")
    assert worldbook.get(entry.id).access_count == 0
    assert worldbook.get(entry.id).last_accessed == ""


# --- t14 F5：match() 必须是纯查询 ---

def test_worldbook_match_is_idempotent(worldbook):
    """反复调用结果完全一致，且不推进 revision、不改状态"""
    entry = worldbook.add("偏好", "用户喜欢猫", trigger_words=["猫"], priority=3)
    before = worldbook.get(entry.id).to_dict()

    first = worldbook.match("猫")
    second = worldbook.match("猫")
    third = worldbook.match("猫", limit=10)

    assert [item.id for item in first] == [item.id for item in second]
    assert [item.id for item in first] == [item.id for item in third]
    assert worldbook.get(entry.id).to_dict() == before, (
        "match() 不得改变条目任何字段（含 revision / access_count / state）"
    )


def test_worldbook_match_result_is_an_isolated_snapshot(worldbook):
    """返回值与内部状态隔离：改返回值不得影响引擎"""
    entry = worldbook.add("偏好", "用户喜欢猫", trigger_words=["猫"])
    snapshot = worldbook.match("猫")[0]

    snapshot.access_count = 99
    snapshot.state = STATE_EVICTED
    snapshot.trigger_words = ()

    stored = worldbook.get(entry.id)
    assert stored.access_count == 0, "内部计数不得被返回值改写"
    assert stored.state == STATE_ACTIVE
    assert stored.trigger_words == ("猫",)
    assert worldbook.match("猫"), "改返回值后匹配能力不受影响"


def test_worldbook_match_does_not_affect_evict_stale(worldbook):
    """纯查询语义落到实处：看一眼不能改变淘汰判定"""
    now = datetime(2024, 6, 1, 0, 0, 0)
    quiet = worldbook.add("老旧设定", "很久没人用的设定", trigger_words=["老旧"])
    worldbook.archive(quiet.id, stamp="2024-01-01 00:00:00")

    # 反复「看一眼」：若 match() 记账，这条就不会被淘汰
    for _ in range(5):
        assert worldbook.match("老旧", include_archived=True)

    victims = worldbook.evict_stale(now=now, idle_days=30, max_access=1)
    assert quiet.id in victims, "match() 不得让闲置条目变得「活跃」"


def test_worldbook_relevant_is_what_keeps_entry_alive(worldbook):
    """对照组：真正用上了（relevant）才算访问，从而免于淘汰"""
    now = datetime(2024, 6, 1, 0, 0, 0)
    used = worldbook.add("常用设定", "常被用到的设定", trigger_words=["常用"])
    worldbook.archive(used.id, stamp="2024-01-01 00:00:00")
    # 归档条目默认不参与检索，显式带上 include_archived 才算「真的取用了」
    worldbook.relevant("常用", include_archived=True,
                       moment=datetime(2024, 5, 31, 0, 0, 0))

    assert worldbook.get(used.id).access_count == 1
    assert worldbook.evict_stale(now=now, idle_days=30, max_access=1) == []
    assert worldbook.get(used.id).state == STATE_ARCHIVED


def test_adapter_context_alone_does_not_keep_matching_idempotent(memory_root):
    """门面层同样成立：worldbook_entries 计访问，裸 match 不计"""
    adapter = MemoryEngineAdapter.from_config({"engine": "wiki", "root": memory_root})
    entry = adapter.worldbook.add("偏好", "用户喜欢猫", trigger_words=["猫"])

    adapter.worldbook.match("猫")
    adapter.worldbook.match("猫")
    assert adapter.worldbook.get(entry.id).access_count == 0

    adapter.worldbook_entries("猫")
    assert adapter.worldbook.get(entry.id).access_count == 1, (
        "worldbook_entries 走 relevant，应当计一次访问"
    )


def test_worldbook_relevant_records_access(worldbook):
    entry = worldbook.add("偏好", "用户喜欢猫", trigger_words=["猫"])
    moment = datetime(2024, 6, 1, 12, 0, 0)
    matched = worldbook.relevant("猫", moment=moment)
    assert [item.id for item in matched] == [entry.id]
    assert matched[0].access_count == 1
    assert matched[0].last_accessed == "2024-06-01 12:00:00"
    worldbook.relevant("猫", moment=moment)
    assert worldbook.get(entry.id).access_count == 2


def test_worldbook_disabled_returns_nothing(tmp_path):
    book = Worldbook(tmp_path / "memory", enabled=False)
    book.add("偏好", "用户喜欢猫", trigger_words=["猫"])
    assert book.match("猫") == []
    assert book.relevant("猫") == []
    assert book.context("猫") == ""
    assert book.count() == 1, "关闭检索不影响读写接口"


def test_worldbook_context_formats_entries(worldbook):
    worldbook.add("称呼偏好", "用户希望被叫「小雨」", trigger_words=["称呼"])
    text = worldbook.context("可以换个称呼吗")
    assert "称呼偏好：" in text
    assert "用户希望被叫「小雨」" in text
    assert worldbook.context("与触发词无关") == ""
    assert format_worldbook(worldbook.all()).startswith("- 称呼偏好：")


def test_worldbook_persists_between_instances(tmp_path):
    path = tmp_path / "memory"
    first = Worldbook(path)
    entry = first.add("偏好", "用户喜欢猫", trigger_words=["猫"], priority=3)
    reloaded = Worldbook(path)
    assert reloaded.count() == 1
    assert reloaded.get(entry.id).priority == 3
    assert reloaded.get(entry.id).trigger_words == ("猫",)


def test_worldbook_clear_removes_all(tmp_path):
    book = Worldbook(tmp_path / "memory")
    book.add("a", "b", trigger_words=["x"])
    book.clear()
    assert book.count() == 0
    assert Worldbook(tmp_path / "memory").count() == 0


# ============================================================================
# 4. Worldbook 生命周期：归档 / 恢复 / 淘汰
# ============================================================================

def test_archive_hides_entry_but_keeps_data(worldbook):
    entry = worldbook.add("旧设定", "已经过时的设定", trigger_words=["旧"])
    archived = worldbook.archive(entry.id, reason="用户改口了")
    assert archived.state == STATE_ARCHIVED
    assert archived.metadata["archive_reason"] == "用户改口了"
    assert archived.metadata["archived_at"]
    assert worldbook.match("旧") == [], "归档后不再参与检索"
    assert worldbook.match("旧", include_archived=True), "归档条目仍可显式查询"
    assert worldbook.count() == 1, "归档不是删除"
    assert worldbook.count(STATE_ARCHIVED) == 1


def test_restore_brings_entry_back(worldbook):
    entry = worldbook.add("设定", "内容", trigger_words=["设定"])
    worldbook.archive(entry.id)
    worldbook.evict(entry.id, reason="太久了")
    assert worldbook.get(entry.id).state == STATE_EVICTED
    restored = worldbook.restore(entry.id)
    assert restored.state == STATE_ACTIVE
    assert worldbook.match("设定"), "恢复后重新参与检索"


def test_evict_marks_instead_of_deleting(worldbook):
    entry = worldbook.add("设定", "内容", trigger_words=["设定"])
    evicted = worldbook.evict(entry.id, reason="闲置太久")
    assert evicted.state == STATE_EVICTED
    assert evicted.metadata["evict_reason"] == "闲置太久"
    assert evicted.metadata["evicted_at"]
    assert worldbook.count() == 1
    assert worldbook.all(STATE_EVICTED)[0].id == entry.id
    assert worldbook.path.exists(), "淘汰只是标记，文件里必须还在"


def test_evict_stale_only_targets_archived_and_untouched(worldbook):
    now = datetime(2024, 6, 1, 0, 0, 0)
    stale = worldbook.add("老旧", "闲置的设定", trigger_words=["老"])
    used = worldbook.add("常用", "常被想起的设定", trigger_words=["常用"])
    active = worldbook.add("在役", "还在用的设定", trigger_words=["在役"])

    for entry in (stale, used):
        worldbook.archive(entry.id, stamp="2024-01-01 00:00:00")
    worldbook.update(used.id, {"access_count": 5,
                               "last_accessed": "2024-01-01 00:00:00"})
    worldbook.update(active.id, {"last_accessed": "2020-01-01 00:00:00"})

    victims = worldbook.evict_stale(now=now, idle_days=30, max_access=1)
    assert victims == [stale.id]
    assert worldbook.get(stale.id).state == STATE_EVICTED
    assert worldbook.get(used.id).state == STATE_ARCHIVED, "访问过的不淘汰"
    assert worldbook.get(active.id).state == STATE_ACTIVE, "active 不淘汰"


def test_evict_stale_respects_idle_window(worldbook):
    now = datetime(2024, 6, 1, 0, 0, 0)
    entry = worldbook.add("设定", "内容", trigger_words=["设定"])
    worldbook.archive(entry.id, stamp=now.strftime("%Y-%m-%d %H:%M:%S"))
    assert worldbook.evict_stale(now=now, idle_days=30) == [], "刚归档的不算闲置"
    assert worldbook.evict_stale(now=now + timedelta(days=31), idle_days=30) == [entry.id]


def test_worldbook_capacity_archives_instead_of_deleting(tmp_path):
    book = Worldbook(tmp_path / "memory", max_entries=2)
    first = book.add("一", "内容一", trigger_words=["一"])
    second = book.add("二", "内容二", trigger_words=["二"])
    third = book.add("三", "内容三", trigger_words=["三"])
    assert book.count() == 3, "超出容量只归档，不删除"
    assert book.count(STATE_ARCHIVED) == 1
    assert book.count(STATE_ACTIVE) == 2
    assert book.get(first.id).state == STATE_ARCHIVED, "最久未访问的先归档"
    assert {second.id, third.id} == {e.id for e in book.all(STATE_ACTIVE)}


def test_worldbook_record_access_updates_counters(worldbook):
    entry = worldbook.add("设定", "内容", trigger_words=["设定"])
    book_entry = worldbook.record_access(entry.id, moment=datetime(2024, 2, 2, 3, 4, 5))
    assert book_entry.access_count == 1
    assert book_entry.last_accessed == "2024-02-02 03:04:05"
    assert book_entry.revision == 2, "每次改动都推进 revision"


def test_worldbook_update_rejects_stale_revision(worldbook):
    entry = worldbook.add("设定", "内容", trigger_words=["设定"])
    worldbook.update(entry.id, {"priority": 1}, expected_revision=entry.revision)
    with pytest.raises(ValueError) as error:
        worldbook.update(entry.id, {"priority": 2}, expected_revision=entry.revision)
    assert "revision 已变" in str(error.value)


def test_worldbook_entry_rejects_unknown_patch_field(worldbook):
    entry = worldbook.add("设定", "内容", trigger_words=["设定"])
    with pytest.raises(ValueError):
        worldbook.update(entry.id, {"created_at": "2020-01-01 00:00:00"})


def test_worldbook_missing_entry_raises(worldbook):
    with pytest.raises(KeyError):
        worldbook.get("wb_not_exists")
    assert worldbook.find("wb_not_exists") is None
    assert worldbook.remove("wb_not_exists") is False


def test_worldbook_corrupt_file_falls_back_to_empty(tmp_path):
    path = tmp_path / "memory"
    path.mkdir()
    (path / "worldbook.json").write_text("{ 这不是 JSON", encoding="utf-8")
    book = Worldbook(path)
    assert book.count() == 0
    assert book.last_error, "坏文件必须被记录而不是静默忽略"


# ============================================================================
# 5. summary 滚动摘要与确定性压缩
# ============================================================================

def test_summary_appends_records_without_compressing(memory_root):
    memory = SummaryMemory(memory_root, threshold=3)
    memory.add_memory("第一条")
    memory.add_memory("第二条")
    assert memory.count() == 2
    assert memory.summaries() == [], "写入本身不触发压缩"
    assert memory.should_compress() is False


def test_summary_triggers_on_count_threshold(memory_root):
    memory = SummaryMemory(memory_root, threshold=3, keep_recent=1)
    for index in range(4):
        memory.add_memory(f"第 {index} 条对话")
    assert memory.should_compress() is True
    summary = memory.compress()
    assert summary is not None
    assert memory.summaries() == [summary]
    assert memory.should_compress() is False, "压缩后待摘要量下降"


def test_summary_triggers_on_char_threshold(memory_root):
    memory = SummaryMemory(memory_root, threshold=1000, char_threshold=5,
                           keep_recent=0)
    memory.add_memory("这是一段比较长的内容")
    assert memory.pending_chars() >= 5
    assert memory.should_compress() is True


def test_summary_compress_is_noop_below_threshold(memory_root):
    memory = SummaryMemory(memory_root, threshold=10)
    memory.add_memory("只有一条")
    assert memory.compress() is None
    assert memory.summaries() == []
    assert memory.count() == 1


def test_summary_compress_needs_more_than_keep_recent(memory_root):
    memory = SummaryMemory(memory_root, threshold=10, keep_recent=3)
    for index in range(3):
        memory.add_memory(f"第 {index} 条")
    assert memory.compress(force=True) is None


def test_summary_compression_keeps_originals_as_shadowed(memory_root):
    memory = SummaryMemory(memory_root, threshold=2, keep_recent=0)
    memory.add_memory("被压缩的原文", timestamp="2024-01-01 10:00:00")
    memory.add_memory("第二条原文", timestamp="2024-01-01 10:01:00")
    summary = memory.compress(force=True)
    assert summary is not None
    archived = memory.archived()
    assert len(archived) == 2, "原文不能物理删除"
    assert all(entry.forgotten for entry in archived)
    assert all(entry.metadata.get("compressed_into") == summary.id
               for entry in archived)
    assert memory.count() == 1, "压缩后只算摘要一条"


def test_summary_compression_is_deterministic(memory_root):
    def build(root):
        memory = SummaryMemory(root, threshold=2, keep_recent=0)
        for text in ["第一件事", "第二件事", "第三件事"]:
            memory.add_memory(text, timestamp="2024-01-01 10:00:00")
        return memory.compress(force=True).content

    first = build(memory_root / "a")
    second = build(memory_root / "b")
    assert first == second, "同样的输入必须得到同样的摘要文本"
    assert first.splitlines()[0].startswith("【压缩摘要 #1】")


def test_summary_compression_keeps_unfinished_markers(memory_root):
    memory = SummaryMemory(memory_root, threshold=2, keep_recent=0)
    memory.add_memory("已经聊过的话题")
    memory.add_memory("[未完成] 给用户准备生日惊喜")
    memory.add_memory("[TODO] 帮用户查一下显示器型号")
    summary = memory.compress(force=True)
    assert "[未完成] 给用户准备生日惊喜" in summary.content
    assert "[TODO] 帮用户查一下显示器型号" in summary.content
    assert "【未完成事项】" in summary.content
    assert summary.metadata["unfinished_count"] == 2


def test_summary_never_truncates_unfinished_items(memory_root):
    memory = SummaryMemory(memory_root, threshold=1, keep_recent=0,
                           keep_chars=10, summary_char_limit=10)
    unfinished = "[TODO] " + "很重要的待办事项" * 30
    memory.add_memory(unfinished)
    memory.add_memory("普通的一条很长的对话内容" * 30)
    summary = memory.compress(force=True)
    assert unfinished in summary.content, "未完成事项不能被截断"
    assert "…（已截断）…" in summary.content, "普通内容仍按长度截断"


def test_summary_compression_collapses_duplicates(memory_root):
    memory = SummaryMemory(memory_root, threshold=1, keep_recent=0)
    for _ in range(3):
        memory.add_memory("重复出现的一句话")
    summary = memory.compress(force=True)
    assert summary.content.count("重复出现的一句话") == 1


def test_summary_bounds_long_history(memory_root):
    memory = SummaryMemory(memory_root, threshold=10, keep_recent=2,
                           summary_char_limit=300)
    for index in range(200):
        memory.add_memory(f"第 {index} 条：这是一段比较长的对话内容，用来撑大历史。",
                          timestamp="2024-01-01 10:00:00")
    rounds = 0
    while memory.should_compress() and rounds < 100:
        assert memory.compress() is not None
        rounds += 1
    assert rounds > 0
    assert len(memory.summaries()) < 40, "压缩必须真的减少条数，而不是越压越多"
    for summary in memory.summaries():
        body = [line for line in summary.content.splitlines()
                if line and not line.startswith("【未完成事项】")]
        assert len("\n".join(body)) < 1200, "单条摘要必须有界"
    assert memory.count() < 60, "压缩后总条数必须远小于原始 200 条"


def test_summary_recall_prefers_summaries(memory_root):
    memory = SummaryMemory(memory_root, threshold=2, keep_recent=0)
    memory.add_memory("关于显示器的话题", timestamp="2024-01-01 10:00:00")
    memory.add_memory("关于显示器的另一段", timestamp="2024-01-01 10:01:00")
    summary = memory.compress(force=True)
    recalled = memory.recall("显示器")
    assert summary.id in [entry.id for entry in recalled]
    assert recalled[0].source == "summary"


def test_summary_clear_removes_everything(memory_root):
    memory = SummaryMemory(memory_root, threshold=2, keep_recent=0)
    memory.add_memory("内容一")
    memory.add_memory("内容二")
    memory.compress(force=True)
    memory.clear()
    assert memory.count() == 0
    assert memory.summaries() == []
    assert memory.pending() == []


# ============================================================================
# 6. 条目、存储层与工具函数
# ============================================================================

def test_memory_entry_round_trip():
    entry = MemoryEntry(content="正文", title="标题", tags=["a", "b"],
                        timestamp="2024-01-01 00:00:00")
    restored = MemoryEntry.from_dict(entry.to_dict())
    assert restored.to_dict() == entry.to_dict()


def test_memory_entry_patch_advances_revision():
    entry = MemoryEntry(content="原文", timestamp="2024-01-01 00:00:00")
    patched = entry.patched(content="新文", stamp="2024-02-02 00:00:00")
    assert patched.content == "新文"
    assert patched.revision == entry.revision + 1
    assert patched.updated_at == "2024-02-02 00:00:00"
    assert entry.content == "原文", "改动不得影响原对象"
    with pytest.raises(ValueError):
        entry.patched(id="another")


def test_unfinished_marker_detection():
    assert has_unfinished_marker("[未完成] 还有事")
    assert has_unfinished_marker("[TODO] 待办")
    assert has_unfinished_marker("用户说：未完成事项还有三个")
    assert not has_unfinished_marker("全部做完了")


# --- t14 F4：裸写（无方括号）的未完成标记 ---

#: 必须被识别的未完成标记写法（方括号 / 中文括号 / 冒号 / 裸写）
UNFINISHED_MARKED_TEXTS = [
    "[未完成] 给用户准备生日惊喜",
    "[TODO] 帮用户查一下显示器型号",
    "[待办] 订机票",
    "【待办】明天要买票",
    "TODO：给用户准备生日惊喜",
    "TODO 未完成：给用户准备生日惊喜",
    "TODO 未完成：",
    "未完成：写周报",
    "待办：订机票",
    "未完成 明天交稿",
    "待办 明天交稿",
    "todo: buy milk",
    "Todo 待办: 交周报",
    "用户说：未完成事项还有三个",
]

#: 不该被误判成未完成的普通叙述
#: t17 F4b：补上「句中提到裸写标记词」的叙述形态——t14 的反例集合缺
#: 『todo list 说明』这一具体形态，正是本次漏检的原因。
UNMARKED_TEXTS = [
    "全部做完了",
    "今天聊了聊天气",
    "我未完成的部分不多，但整体还算顺利",
    "xxxl 是尺码不是标记",
    "example 这个词里没有标记词",
    "这是我的 todo list 说明",
    "今天天气不错",
    "已完成：交稿",
    "我的 fixme 记录",
    "列个 todo 吧",
    "关于 fixme 的说明放在了附录",
    "他把 xxx 记在了草稿里",
]


@pytest.mark.parametrize("text", UNFINISHED_MARKED_TEXTS)
def test_unfinished_marker_detection_covers_bare_forms(text):
    """裸写与方括号两种形式都必须被识别（t14 F4）"""
    assert has_unfinished_marker(text), f"未被识别为未完成标记: {text!r}"
    assert unfinished_markers(text), f"取不到标记词: {text!r}"


@pytest.mark.parametrize("text", UNMARKED_TEXTS)
def test_unfinished_marker_detection_rejects_ordinary_text(text):
    """普通叙述不得被误判（误判会让整段内容免于压缩，失去压缩意义）

    t17 F4b：`_UNFINISHED_RE.search` 的命中位置也要落在**行首**——
    裸写分支收紧后，句中出现的裸词必须完全不参与匹配。
    """
    assert not has_unfinished_marker(text), f"被误判为未完成标记: {text!r}"
    match = _UNFINISHED_RE.search(text)
    if match is not None:
        # 仅当文本里存在包裹/冒号等合法结构时才允许非裸写命中；
        # 这批反例全是普通叙述，命中必须发生在行首（裸写分支本身）。
        assert match.start() == 0 or text[:match.start()].splitlines()[-1].strip() == "", (
            f"句中命中裸写标记: {text!r} -> {match.group(0)!r}"
        )


def test_todo_list_sentence_is_not_unfinished():
    """t17 F4b 契约点名反例：句中提到的裸词不得升级为待办。

    t14 的反例集合缺 `todo list 说明` 这一形态，是本次漏检的直接原因；
    这里单独成测试并直接断言裸写分支的匹配行为，防止再次回归。
    """
    text = "这是我的 todo list 说明"
    assert not has_unfinished_marker(text), "句中的裸 todo 不得判为未完成事项"
    match = _UNFINISHED_RE.search(text)
    assert match is None, f"裸写分支不应命中叙述中的 todo: {match and match.group(0)!r}"



@pytest.mark.parametrize("marker", ["TODO 未完成：", "未完成：", "待办：", "[TODO] ",
                                    "[未完成] ", "【待办】", "待办 ", "- TODO "])
def test_summary_keeps_bare_unfinished_text_byte_for_byte(memory_root, marker):
    """t14 F4 核心断言：800+ 字符的未完成事项压缩后**字符数不变**

    t17 F4b 补：裸写形态（行首裸词 `待办 `、项目符号 `- TODO `）同样要
    在超长压缩后逐字保留并进入【未完成事项】段，收紧行首约束不得回归。
    """
    """t14 F4 核心断言：800+ 字符的未完成事项压缩后**字符数不变**"""
    body = "这是一段很长的普通对话内容，用来制造超长历史。" * 40
    unfinished = marker + body
    assert len(unfinished) > 800, "用例本身要制造超长输入"

    memory = SummaryMemory(memory_root, threshold=1, keep_recent=0,
                           keep_chars=100, summary_char_limit=200)
    memory.add_memory(unfinished)
    memory.add_memory("另一条普通内容" * 200)
    summary = memory.compress(force=True)

    assert unfinished in summary.content, "未完成事项必须逐字出现在摘要里"
    kept = [line for line in summary.content.splitlines() if line.startswith("- ")]
    matched = [line[2:] for line in kept if line[2:] == unfinished]
    assert matched, "摘要里应当有一行与原文完全相等"
    assert len(matched[0]) == len(unfinished), "字符数必须不变（不得出现 809→402）"
    assert "【未完成事项】" in summary.content
    assert summary.metadata["unfinished_count"] == 1


def test_summary_keeps_internal_newlines_of_unfinished_items(memory_root):
    """未完成事项内部换行/缩进必须原样保留（t14 F4 残留的空白折叠问题）"""
    original = "TODO 未完成：给用户准备生日惊喜\n  第二步：订蛋糕\n（明天之前）"
    memory = SummaryMemory(memory_root, threshold=1, keep_recent=0)
    memory.add_memory(original)
    memory.add_memory("普通对话内容")
    summary = memory.compress(force=True)

    assert original in summary.content, "折叠空白等于改写原文，必须逐字保留"
    assert "\n  第二步：订蛋糕" in summary.content, "缩进也要保留"
    # 普通内容仍然折叠空白（两条路径必须真的分开）
    memory.add_memory("普通   对话\n内容")
    summary = memory.compress(force=True)
    assert "- 普通 对话 内容" in summary.content


def test_summary_unfinished_survives_repeated_compression(memory_root):
    """多轮压缩后未完成事项仍在（它不该被后续摘要吃掉）"""
    unfinished = "TODO：把显示器型号告诉用户"
    memory = SummaryMemory(memory_root, threshold=2, keep_recent=1)
    for index in range(30):
        memory.add_memory(f"第 {index} 条普通对话内容")
    memory.add_memory(unfinished)
    for _ in range(20):
        if not memory.compress():
            break
    everywhere = "\n".join(item.content for item in memory.entries())
    assert unfinished in everywhere, "未完成事项在压缩历史里必须始终可见"


def test_jsonl_store_round_trip(tmp_path):
    path = tmp_path / "data" / "records.jsonl"
    write_jsonl(path, [{"id": "1", "content": "一"}, {"id": "2", "content": "二"}])
    records = read_jsonl(path)
    assert [item["id"] for item in records] == ["1", "2"]
    assert all(item["v"] == 1 for item in records)


def test_jsonl_store_tolerates_torn_tail(tmp_path):
    path = tmp_path / "records.jsonl"
    write_jsonl(path, [{"id": "1", "content": "完整"}])
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"id": "2", "content": "被截')
    records = read_jsonl(path)
    assert [item["id"] for item in records] == ["1"], "撕裂尾部应被丢弃"


def test_jsonl_store_rejects_broken_middle_line(tmp_path):
    path = tmp_path / "records.jsonl"
    path.write_text('{"id": "1", "v": 1}\n坏了\n{"id": "3", "v": 1}\n',
                    encoding="utf-8")
    with pytest.raises(MemoryStoreError):
        read_jsonl(path)


def test_jsonl_store_skips_unknown_version(tmp_path):
    path = tmp_path / "records.jsonl"
    write_jsonl(path, [{"id": "1", "content": "当前版本"}])
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"id": "2", "content": "未来版本", "v": 99}\n')
    assert [item["id"] for item in read_jsonl(path)] == ["1"]


def test_atomic_write_leaves_no_temp_files(tmp_path):
    path = tmp_path / "memory" / "file.txt"
    atomic_write_text(path, "内容")
    assert path.read_text(encoding="utf-8") == "内容"
    assert [item.name for item in path.parent.iterdir()] == ["file.txt"]


def test_jsonl_store_facade(tmp_path):
    store = JsonlStore(tmp_path / "a.jsonl")
    assert store.exists() is False
    store.append({"id": "1"})
    store.append({"id": "2"})
    assert len(store.load()) == 2
    store.truncate()
    assert store.load() == []
    assert store.size_bytes() == 0


def test_build_index_uses_explicit_backend(tmp_path):
    injected = InMemoryIndex()
    assert build_index(tmp_path, "memories", backend=injected) is injected


# ============================================================================
# 7. 门面：MemoryEngineAdapter（写问答 / 组装上下文）
# ============================================================================

def test_adapter_builds_engine_from_config(memory_root):
    adapter = MemoryEngineAdapter.from_config(
        {"engine": "wiki", "root": memory_root, "worldbook_enabled": True})
    assert adapter.engine_name == MODE_WIKI
    adapter.remember_conversation("你好", "你好呀")
    assert adapter.engine.count() == 1
    assert adapter.engine.get(adapter.engine.entries()[0].id).content == (
        "用户: 你好\n助理: 你好呀")


def test_adapter_worldbook_flag_follows_config(memory_root):
    adapter = MemoryEngineAdapter.from_config(
        {"engine": "wiki", "root": memory_root, "worldbook_enabled": False})
    adapter.worldbook.add("偏好", "用户喜欢猫", trigger_words=["猫"])
    assert adapter.context("猫").worldbook == ()


def test_adapter_context_puts_worldbook_before_recall(memory_root):
    adapter = MemoryEngineAdapter.from_config({"engine": "wiki", "root": memory_root})
    adapter.worldbook.add("称呼偏好", "用户希望被叫「小雨」", trigger_words=["小雨"])
    adapter.remember("用户喜欢喝美式咖啡")

    context = adapter.context("叫我小雨，顺便聊聊咖啡")
    assert context.text
    assert context.text.index("【Worldbook】") < context.text.index("【相关记忆】")
    assert "用户希望被叫「小雨」" in context.text
    assert "用户喜欢喝美式咖啡" in context.text
    assert context.engine == MODE_WIKI
    assert context.worldbook and context.memories
    assert bool(context) is True


def test_adapter_context_skips_archived_and_evicted_entries(memory_root):
    """生命周期必须真的影响注入：归档/淘汰后不再进入上下文"""
    adapter = MemoryEngineAdapter.from_config({"engine": "wiki", "root": memory_root})
    entry = adapter.worldbook.add("旧称呼", "用户以前叫「小喵」", trigger_words=["小喵"])
    assert "用户以前叫「小喵」" in adapter.context("叫我小喵").text

    adapter.worldbook.archive(entry.id)
    assert adapter.context("叫我小喵").text == "", "归档条目不得再注入"

    adapter.worldbook.restore(entry.id)
    assert "用户以前叫「小喵」" in adapter.context("叫我小喵").text

    adapter.worldbook.evict(entry.id)
    assert adapter.context("叫我小喵").text == "", "淘汰条目不得再注入"


def test_adapter_context_is_empty_without_hits(memory_root):
    adapter = MemoryEngineAdapter.from_config({"engine": "wiki", "root": memory_root})
    context = adapter.context("完全无关的一句话")
    assert context.text == ""
    assert bool(context) is False
    assert str(context) == ""


def test_adapter_delegates_legacy_methods(memory_root):
    adapter = MemoryEngineAdapter.from_config(
        {"engine": "embedding", "root": memory_root, "index": InMemoryIndex()})
    adapter.engine.add_memory("用户喜欢喝美式咖啡")
    assert adapter.search_memories("咖啡")[0].endswith("用户喜欢喝美式咖啡")
    assert adapter.count == 1
    assert adapter.collection is None
    with pytest.raises(AttributeError):
        adapter.this_method_does_not_exist


def test_adapter_clear_all_wipes_memory_and_worldbook(memory_root):
    adapter = MemoryEngineAdapter.from_config({"engine": "wiki", "root": memory_root})
    adapter.worldbook.add("偏好", "用户喜欢猫", trigger_words=["猫"])
    adapter.remember("一条记忆")
    adapter.clear_all()
    assert adapter.count == 0
    assert adapter.worldbook.count() == 0


def test_adapter_compress_only_applies_to_summary(memory_root):
    summary_adapter = MemoryEngineAdapter.from_config(
        {"engine": "summary", "root": memory_root / "s",
         "summary_threshold": 2, "keep_recent": 0})
    summary_adapter.remember("内容一")
    summary_adapter.remember("内容二")
    assert summary_adapter.should_compress() is True
    assert summary_adapter.compress() is not None

    wiki_adapter = MemoryEngineAdapter.from_config(
        {"engine": "wiki", "root": memory_root / "w"})
    wiki_adapter.remember("内容")
    assert wiki_adapter.should_compress() is False
    assert wiki_adapter.compress() is None


# ============================================================================
# 8. 隔离性：测试不得写进仓库
# ============================================================================

def test_memory_writes_stay_under_explicitly_configured_roots(tmp_path, monkeypatch):
    """记忆写入必须落在显式配置的 root 下——即便从仓库根启动。

    这里刻意**不**断言「仓库根没有 memory/」（t14 改）：多人共用同一工作区时
    那是竞态断言，其他成员并发的探针脚本会在仓库根留下 `memory/`，与本模块
    代码无关。实测该目录内容（`title='t'` / `'猫 喜欢 鱼'`）不属于本文件任何
    用例，且逐个测试文件单独运行都不会创建它；本文件全部用例都显式传 root。

    改为断言**不变量**：CWD 指向仓库根时，显式 root 的写入必须全部落在 root 内。
    这直接覆盖了真实缺陷（引擎缺 `root` 属性 → 适配器静默回退相对路径
    `"memory"` → Worldbook 写进调用方 CWD），又不依赖共享文件系统的时序。
    """
    monkeypatch.chdir(PROJECT_ROOT)
    for name in ENGINE_NAMES:
        root = tmp_path / name
        adapter = MemoryEngineAdapter.from_config({
            "engine": name, "root": root, "index": InMemoryIndex(),
        })
        adapter.remember_conversation("你好", "你好呀")
        adapter.worldbook.add("偏好", "用户喜欢猫", trigger_words=["猫"])
        assert adapter.context("猫").text, "装配好的门面必须可用"

        worldbook_path = Path(adapter.worldbook.path).resolve()
        assert root.resolve() in worldbook_path.parents, (
            f"{name}: Worldbook 落在 {worldbook_path}，不在配置的 root 内"
        )
        assert worldbook_path.exists()


def test_every_engine_exposes_a_root_for_worldbook_placement(memory_root):
    """三种引擎都必须给出 root，且落在自己的落盘根下。

    t14 修的真实缺陷：`EmbeddingMemory` 早期没有 `root` 属性，于是
    `MemoryEngineAdapter` 里 `getattr(engine, "root", DEFAULT_ROOT)` 静默
    退回相对路径 `"memory"`——Worldbook 会被写进**调用方的当前工作目录**。
    表现就是仓库根凭空多出 `memory/worldbook.json`，且内容与任何用例都对不上。
    """
    for name in ENGINE_NAMES:
        engine = _engine(name, memory_root / name)
        assert hasattr(engine, "root"), f"{name} 引擎缺少 root 属性"
        root = Path(engine.root)
        assert root.is_absolute(), f"{name} 的 root 必须是绝对路径，实际 {root}"
        assert memory_root in root.resolve().parents or root.resolve() == memory_root, (
            f"{name} 的 root 落到了临时目录之外: {root}"
        )


def test_adapter_worldbook_never_lands_in_the_working_directory(tmp_path, monkeypatch):
    """门面装配的 Worldbook 必须落在引擎 root 下，绝不用 CWD 兜底"""
    working_directory = tmp_path / "pretend_cwd"
    working_directory.mkdir()
    monkeypatch.chdir(working_directory)

    for name in ENGINE_NAMES:
        adapter = MemoryEngineAdapter.from_config({
            "engine": name, "root": tmp_path / name, "index": InMemoryIndex(),
        })
        adapter.worldbook.add("偏好", "用户喜欢猫", trigger_words=["猫"])

        assert adapter.worldbook.path.exists()
        assert tmp_path in adapter.worldbook.path.resolve().parents, (
            f"{name}: Worldbook 落到了 {adapter.worldbook.path}"
        )
    assert not (working_directory / "memory").exists(), (
        "Worldbook 不得写进调用方的当前工作目录"
    )


def test_embedding_root_matches_its_text_layer_parent(tmp_path):
    """embedding 的 root 语义与 wiki/summary 一致：文本层目录的父目录"""
    engine = EmbeddingMemory(tmp_path / "database", "memories",
                             path=tmp_path / "memory" / "memories_text",
                             index=InMemoryIndex())
    assert Path(engine.root) == tmp_path / "memory"
    assert Path(engine.path) == tmp_path / "memory" / "memories_text"


def test_adapter_from_config_places_worldbook_under_configured_root(tmp_path):
    """显式配置 root 时，Worldbook 必须落在该 root 下"""
    for name in ENGINE_NAMES:
        adapter = MemoryEngineAdapter.from_config({
            "engine": name, "root": tmp_path / name, "index": InMemoryIndex(),
        })
        assert Path(adapter.worldbook.path) == tmp_path / name / "worldbook.json"


def test_worldbook_uses_engine_root_when_not_given_one(memory_root):
    """不显式传 worldbook 时，门面用引擎的 root 定位"""
    for name in ENGINE_NAMES:
        engine = _engine(name, memory_root / name)
        adapter = MemoryEngineAdapter(engine)
        assert Path(adapter.worldbook.path) == Path(engine.root) / "worldbook.json"


def test_temporary_memory_root_is_outside_repository(memory_root):
    assert PROJECT_ROOT not in memory_root.resolve().parents
    assert memory_root.exists()
