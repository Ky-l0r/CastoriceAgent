"""记忆条目的统一形状与时间戳工具。

三种记忆模式（wiki / summary / embedding）与 DMAE Worldbook 都围绕
`MemoryEntry` 交换数据：引擎只负责「存哪儿、怎么召回」，条目的字段语义
由本模块统一规定。这样 ai.py 侧无论接哪种引擎，`with_prefix()` 出来的
文本形状都一致（`[YYYY-MM-DD HH:MM:SS] 正文`），旧 UI 的显示习惯不变。

时间戳写在 metadata / 字段里而不是正文里：正文保持纯净，向量与关键词
检索质量都不受日期数字干扰；召回时再把时间拼回每条记忆前面，
模型既知道「这件事是什么时候说的」，又不影响召回效果
（沿用 ai.py:454-529 的既有设计）。
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

#: 时间戳格式，与 ai.py 旧实现一致（`%Y-%m-%d %H:%M:%S`）
STAMP_FORMAT = "%Y-%m-%d %H:%M:%S"

#: 自动生成的记忆 ID 里随机部分的长度（12 位十六进制 ≈ 48 bit 熵）
ID_RANDOM_LENGTH = 12

#: 源标记：会话问答、手动写入、摘要归档
SOURCE_CONVERSATION = "conversation"
SOURCE_MANUAL = "manual"
SOURCE_SUMMARY = "summary"

#: 未完成标记词（中文词；命中即视为「还没做完」）
UNFINISHED_CN_WORDS = (
    "未完成",
    "未办",
    "待办",
    "待处理",
    "待完成",
    "尚未完成",
    "还没做",
)

#: 未完成标记词（英文词；必须整词命中，避免 `xxx` 命中 `xxxl` 这类误判）
UNFINISHED_EN_WORDS = ("todo", "fixme", "xxx")

#: 「事项」类名词：前后什么都不用跟，出现即算未完成
UNFINISHED_NOUNS = ("未完成事项", "待办事项", "待处理事项", "未办事项")

#: 英文/中文标记词后面允许出现多少字符再遇到冒号（限制长度，避免把
#: 「TODO 后面跟一整段话」误判成标记）
_MARKER_GAP = 10

#: 裸写标记右侧的合法边界：空白/行尾（\s 含换行）、中英文标点
_CN_BOUNDARY = "\\s，。；：:！!？?、,.;\\[\\]【】（）()「」“”‘’'\""

#: 包裹形式使用的括号对
_WRAPPERS = (("[", "]"), ("【", "】"))

#: 裸写标记允许的行首前导：缩进与项目符号（如 `- 待办 …`）
_LEADING_ALLOWED = r"[ \t\u3000]*(?:[-*•·][ \t\u3000]*)?"

#: 裸写标记允许的行首短前缀（如 `【运营部】待办 …` 的来源标注）
_LEADING_PREFIX = r"(?:[\[【(][^\]】)\n]{0,20}[\]】)][ \t\u3000]*)?"


def _unfinished_patterns() -> List[str]:
    """拼出未完成标记的全部正则分支。

    必须同时认三种写法（t14 修：此前只认带方括号的，裸写会被当普通内容截断）：

    1. **包裹形式**：`[未完成]` / `[TODO]` / `【待办】`——任意位置出现即算；
    2. **冒号形式**：`TODO：…` / `未完成：…` / `待办:`——词后（允许隔着
       少量修饰词，如 `TODO 未完成：`）跟中英文冒号，任意位置出现即算；
    3. **裸写形式**：标记词位于**行首**（允许缩进、项目符号 `-`/`*`/`•`，
       以及一个 `【…】`/`[…] ` 短前缀，如 `【运营部】待办 明天交稿`），
       词后再跟空白/标点/行尾。

    **为什么裸写必须限定行首**（t17 修，t12 复验发现的 F4b 误报）：
    此前裸写只做了「左右非字母数字」的整词断言，于是**句子中间**提到的裸词
    同样命中——`这是我的 todo list 说明` 被当成未完成事项。后果是把一句
    普通叙述升级为待办、逐字保留且不受摘要上限约束，污染【未完成事项】段。

    真标记与「叙述里提到标记词」在**位置上**是可分的：

    * 真标记是**清单项**——`待办 明天交稿`、`TODO 收拾行李`、`- [TODO] x`；
    * 叙述里提到的词处在句子中间——`这是我的 todo list 说明`、`列个 todo 吧`。

    因此裸写分支要求词在行首（只放行缩进/项目符号/来源标注这类非叙述前缀）。
    包裹与冒号形式**刻意不要求行首**——它们自带结构标记，即便出现在句子中间
    （`用户说：未完成事项还有三个`）也是真信号；这两类形式覆盖了句中出现的情况。

    假阴性（真待办在句中却漏判）在本规则下只可能发生在「句中的裸词」上，
    而那类句子本就无法与叙述区分；宁可放过，也不要把叙述误报成待办。
    """
    patterns: List[str] = []

    for open_bracket, close_bracket in _WRAPPERS:
        word = "|".join(re.escape(item) for item in UNFINISHED_CN_WORDS)
        english = "|".join(UNFINISHED_EN_WORDS)
        patterns.append(
            re.escape(open_bracket)
            + r"(?:"
            + word
            + r"|(?:"
            + english
            + r"))"
            + re.escape(close_bracket)
        )

    cn_word = "|".join(re.escape(item) for item in UNFINISHED_CN_WORDS)
    english_word = "|".join(UNFINISHED_EN_WORDS)
    # 冒号形式：标记词（允许夹带少量修饰词）之后跟中英文冒号
    patterns.append(
        r"(?:"
        + cn_word
        + r"|(?<![A-Za-z])(?:"
        + english_word
        + r"))"
        + r"[\s]{0,2}(?:[^\s：:]{1,"
        + str(_MARKER_GAP)
        + r"})?[：:]"
    )
    # 裸写形式：标记词位于行首（允许缩进/项目符号/【…】短前缀），
    # 右侧是空白、标点或行尾。行首约束是 t17 的关键——见函数 docstring。
    patterns.append(
        r"^"
        + _LEADING_ALLOWED
        + _LEADING_PREFIX
        + r"(?:"
        + cn_word
        + r"|(?<![A-Za-z])(?:"
        + english_word
        + r")(?![A-Za-z]))"
        + r"(?![^"
        + _CN_BOUNDARY
        + r"])"
    )
    patterns.extend(re.escape(item) for item in UNFINISHED_NOUNS)
    return patterns


#: 裸写分支带 `^` 锚点，必须用 MULTILINE 才能匹配「非首行」的行首；
#: IGNORECASE 让英文标记（todo/fixme/xxx）不区分大小写。中文不受影响。
_UNFINISHED_RE = re.compile(
    "|".join("(?:" + item + ")" for item in _unfinished_patterns()),
    re.IGNORECASE | re.MULTILINE,
)


def stamp_of(moment: Optional[datetime] = None) -> str:
    """把时刻格式化成旧实现的时间戳串（默认取当前本地时间）。"""
    return (moment or datetime.now()).strftime(STAMP_FORMAT)


def parse_stamp(text: str) -> Optional[datetime]:
    """解析时间戳串；格式不认识（旧数据没记时间）时返回 `None`。"""
    if not text:
        return None
    try:
        return datetime.strptime(str(text), STAMP_FORMAT)
    except (TypeError, ValueError):
        return None


def new_memory_id(prefix: str = "mem") -> str:
    """生成一个不会重号的记忆 ID。

    旧实现用 `str(collection.count() + 1)` 当 ID（ai.py:473）：删除或跳过
    写入会造成重号，进而覆盖既有记忆。这里改成随机 ID，格式保持
    `<prefix>_<12 位十六进制>`，可读性不降。
    """
    return f"{prefix}_{uuid.uuid4().hex[:ID_RANDOM_LENGTH]}"


def normalize_tags(tags: Any) -> Tuple[str, ...]:
    """把外部传入的标签归一化成去重、去空白、保序的字符串元组。"""
    if tags is None:
        return ()
    if isinstance(tags, str):
        candidates: Iterable[Any] = tags.replace("，", ",").split(",")
    else:
        candidates = tags
    seen: Dict[str, None] = {}
    for raw in candidates:
        text = str(raw).strip()
        if text:
            seen.setdefault(text, None)
    return tuple(seen)


def has_unfinished_marker(text: str) -> bool:
    """正文里是否带「未完成事项」标记。

    summary 压缩必须原样保留这些标记（契约要求），因此判定逻辑集中在这里，
    压缩、召回、上下文注入三处共用同一套规则，不会出现「这处认为未完成、
    那处认为已完成」的分歧。
    """
    return bool(_UNFINISHED_RE.search(text or ""))


def unfinished_markers(text: str) -> Tuple[str, ...]:
    """取出正文里出现的未完成标记词（保序去重），用于统计与断言。"""
    found: Dict[str, None] = {}
    for match in _UNFINISHED_RE.finditer(text or ""):
        found.setdefault(match.group(0), None)
    return tuple(found)


@dataclass
class MemoryEntry:
    """一条记忆。

    字段语义（三种模式共用）：

    * `content`    正文（写入向量库的就是它，不含时间戳前缀）；
    * `created_at` 首次写入时刻；`updated_at` 最后一次改动时刻；
    * `timestamp`  召回时拼前缀用的时刻，默认等于 `created_at`；
    * `revision`   乐观并发版本号，每次 `patched()` 自增；
    * `forgotten`  遮蔽标记——隐藏但**不物理删除**（对齐「压缩只遮蔽不删除」）；
    * `metadata`   引擎私有附加信息（必须 JSON 可序列化的标量）。
    """

    content: str = ""
    id: str = ""
    title: str = ""
    tags: Tuple[str, ...] = ()
    source: str = SOURCE_CONVERSATION
    #: None 表示「未提供，自动取当前时间」；"" 表示「显式没有时间」
    #: （旧数据没记时间时必须保持无前缀，不能凭空补一个时间戳）
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    timestamp: Optional[str] = None
    revision: int = 1
    forgotten: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id:
            self.id = new_memory_id()
        self.content = "" if self.content is None else str(self.content)
        self.title = "" if self.title is None else str(self.title)
        self.source = str(self.source or SOURCE_CONVERSATION)
        self.tags = normalize_tags(self.tags)
        # 三段时间的自动补全规则：timestamp 是展示用的时刻，
        # created_at 是身份，updated_at 是最后改动时刻。
        if self.created_at is None:
            self.created_at = self.timestamp or stamp_of()
        if self.timestamp is None:
            self.timestamp = self.created_at or stamp_of()
        if self.updated_at is None:
            self.updated_at = self.created_at
        self.revision = int(self.revision or 1)
        self.forgotten = bool(self.forgotten)
        if not isinstance(self.metadata, dict):
            self.metadata = dict(self.metadata or {})

    # -- 展示 -----------------------------------------------------------------

    def with_prefix(self) -> str:
        """`[时间] 正文`；没有时间戳的旧数据保持原样（与既有行为一致）。"""
        return f"[{self.timestamp}] {self.content}" if self.timestamp else self.content

    @property
    def display(self) -> str:
        """标题 + 时间戳 + 正文的一行式展示（上下文注入用）。"""
        parts: List[str] = []
        if self.title:
            parts.append(self.title)
        body = self.content
        if self.timestamp:
            parts.append(f"[{self.timestamp}]")
        parts.append(body)
        return " ".join(part for part in parts if part).strip()

    # -- 序列化 ---------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "content": self.content,
            "title": self.title,
            "tags": list(self.tags),
            "source": self.source,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "timestamp": self.timestamp,
            "revision": self.revision,
            "forgotten": self.forgotten,
            "metadata": dict(self.metadata),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> MemoryEntry:
        """从落盘记录还原；未知字段一律忽略（向前兼容）。"""
        data = dict(data or {})
        metadata = data.get("metadata")
        return cls(
            content=data.get("content", ""),
            id=str(data.get("id") or ""),
            title=data.get("title", "") or "",
            tags=normalize_tags(data.get("tags")),
            source=data.get("source") or SOURCE_CONVERSATION,
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
            timestamp=data.get("timestamp"),
            revision=int(data.get("revision") or 1),
            forgotten=bool(data.get("forgotten")),
            metadata=dict(metadata) if isinstance(metadata, Mapping) else {},
        )

    # -- 改写 -----------------------------------------------------------------

    PATCHABLE_FIELDS = (
        "content",
        "title",
        "tags",
        "source",
        "timestamp",
        "metadata",
        "forgotten",
    )

    def patched(self, *, stamp: Optional[str] = None, **changes: Any) -> MemoryEntry:
        """返回应用改动后的副本：`revision` 自增、`updated_at` 刷新。

        不原地改动（条目在引擎里可能被多处持有），也不允许改 `id` /
        `created_at`——它们标识同一条记忆的历史。
        """
        unknown = set(changes) - set(self.PATCHABLE_FIELDS)
        if unknown:
            raise ValueError(
                "不可修改的记忆字段: "
                + ", ".join(sorted(unknown))
                + "（可改："
                + ", ".join(self.PATCHABLE_FIELDS)
                + "）"
            )
        payload = self.to_dict()
        payload.update(changes)
        payload["revision"] = self.revision + 1
        payload["updated_at"] = stamp or stamp_of()
        return MemoryEntry.from_dict(payload)


def sort_by_time(
    entries: Sequence[MemoryEntry], *, newest_first: bool = False
) -> List[MemoryEntry]:
    """按 `created_at` **稳定**排序（时间串可直接字典序比较）。

    刻意不用 id 打破平局：同一秒写入的多条记录，其先后只能由「写入顺序」
    决定（Python 的稳定排序会保留输入顺序）。用随机 id 当平局键会让
    「压缩后摘要内容」随 id 变化，破坏确定性。
    """
    ordered = sorted(entries, key=lambda entry: entry.created_at or "")
    if newest_first:
        ordered.reverse()
    return ordered


def recency_weight(
    entry: MemoryEntry, *, now: Optional[datetime] = None, half_life_days: float = 30.0
) -> float:
    """时间新鲜度得分，落在 (0, 1]：越新越接近 1。

    用 `updated_at` 解析；解析失败（旧数据没记时间）时给中性值 0.5，
    保证「没有时间的旧记忆仍可被召回」，只是不加权。
    """
    raw = entry.updated_at or entry.created_at or entry.timestamp
    if not raw:
        return 0.5
    try:
        moment = datetime.strptime(raw, STAMP_FORMAT)
    except (TypeError, ValueError):
        return 0.5
    reference = now or datetime.now()
    age_days = max((reference - moment).total_seconds() / 86400.0, 0.0)
    return 0.5 ** (age_days / max(half_life_days, 0.001))


__all__ = [
    "ID_RANDOM_LENGTH",
    "SOURCE_CONVERSATION",
    "SOURCE_MANUAL",
    "SOURCE_SUMMARY",
    "STAMP_FORMAT",
    "MemoryEntry",
    "has_unfinished_marker",
    "new_memory_id",
    "normalize_tags",
    "parse_stamp",
    "recency_weight",
    "sort_by_time",
    "stamp_of",
    "unfinished_markers",
]
