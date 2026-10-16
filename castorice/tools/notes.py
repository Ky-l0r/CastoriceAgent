"""Learn 模式的笔记工具：search_notes / read_note / write_note。

对齐 01-gap-matrix.md 第 4 节：

* 目标目录是用户绑定的 Obsidian Vault（modes.learn.vault）；未绑定时给出
  **可读提示**而不是抛错；
* search_notes 先按文件名 / 标题 / 正文做字面匹配（纯 Python，不依赖 rg），
  需要语义检索时再由调用方落到记忆引擎（t6）；
* write_note 写入时**保留既有 frontmatter**，只替换正文；新建时自动补一份
  最小 frontmatter（title / created / tags）；
* Markdown 规范化只发生在 write_note 里，不污染通用 write_file。

设计约束：只依赖标准库与同包 base / fs。
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .base import (
    TOOL_KIND_GENERIC,
    TOOL_KIND_WRITE,
    ToolContext,
    ToolSpec,
    error_text,
    truncate_text,
)
from .fs import (
    DEFAULT_SEARCH_LIMIT,
    SKIP_DIRS,
    SKIP_SUFFIXES,
    _atomic_write,
    _read_text,
    _sha1_text,
)
from ..permissions import SandboxDenied

#: 识别的笔记扩展名
NOTE_SUFFIXES = frozenset({".md", ".markdown", ".txt"})

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.S)
TAG_RE = re.compile(r"(?:^|\s)#([^\s#\[\]]{1,60})")


class NotesNotBound(RuntimeError):
    """Learn 模式尚未绑定 Vault。"""

    code = "NOTES_NOT_BOUND"

    def to_dict(self) -> Dict[str, Any]:
        return {"code": self.code, "message": str(self)}


def vault_root(context: ToolContext) -> str:
    """当前绑定目录（工作区即 Learn 的 Vault）。"""
    return str(context.effective_workspace or "")


def not_bound_message() -> str:
    return error_text(
        "Learn 模式尚未绑定笔记库（Obsidian Vault）：请在设置里指定 "
        "modes.learn.vault，或在界面里绑定目录后再试",
        NotesNotBound.code,
    )


def split_frontmatter(text: str) -> Tuple[str, str]:
    """把笔记拆成 (frontmatter 原文, 正文)；无 frontmatter 时前者为空串。"""
    match = FRONTMATTER_RE.match(text or "")
    if not match:
        return "", text or ""
    return match.group(0), (text or "")[match.end():]


def extract_title(text: str) -> str:
    """取标题：优先 YAML frontmatter 的 title，其次首个 # 标题。"""
    frontmatter, body = split_frontmatter(text)
    for line in frontmatter.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("title:"):
            return stripped.split(":", 1)[1].strip().strip('"').strip("'")
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return ""


def extract_tags(text: str) -> List[str]:
    """收集 #tag 与 frontmatter 的 tags 列表。"""
    frontmatter, body = split_frontmatter(text)
    tags: List[str] = []
    for line in frontmatter.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("tags:"):
            raw = stripped.split(":", 1)[1].strip()
            raw = raw.strip("[]")
            for item in raw.split(","):
                name = item.strip().strip('"').strip("'")
                if name and name not in tags:
                    tags.append(name)
    for match in TAG_RE.finditer(body):
        name = match.group(1).strip()
        if name and name not in tags:
            tags.append(name)
    return tags


def default_frontmatter(title: str, tags: Optional[Sequence[str]] = None) -> str:
    moment = datetime.now().strftime("%Y-%m-%d")
    lines = ["---", "title: " + (title or "未命名笔记"), "created: " + moment]
    if tags:
        lines.append("tags: [" + ", ".join(str(tag) for tag in tags) + "]")
    lines.append("---")
    return "\n".join(lines) + "\n"


def normalize_note(body: str) -> str:
    """Markdown 规范化：统一换行、合并三个以上连续空行、结尾补一个换行。"""
    text = str(body or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.rstrip() + "\n"


def iter_notes(root: str, limit: int = DEFAULT_SEARCH_LIMIT * 5) -> List[str]:
    found: List[str] = []
    for current, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs
                         if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(files):
            if os.path.splitext(name)[1].lower() not in NOTE_SUFFIXES:
                continue
            if os.path.splitext(name)[1].lower() in SKIP_SUFFIXES:
                continue
            found.append(os.path.join(current, name))
            if len(found) >= limit:
                return found
    return found


def make_search_notes(context: ToolContext):
    def search_notes(query: str = "", max_results: int = 20,
                     tags: Optional[Sequence[str]] = None) -> str:
        root = vault_root(context)
        if not root or not os.path.isdir(root):
            return not_bound_message()
        try:
            root = context.check_read(root)
        except SandboxDenied as exc:
            return error_text(str(exc), exc.code)

        needle = str(query or "").strip().lower()
        wanted = [str(tag).strip().lower() for tag in (tags or []) if str(tag).strip()]
        limit = max(1, int(max_results or 20))
        if not needle and not wanted:
            return error_text("至少要给出 query 或 tags 之一", "BAD_ARGUMENTS")

        scored: List[Tuple[int, str, str, List[str]]] = []
        for path in iter_notes(root):
            text = _read_text(path)
            if text is None:
                continue
            title = extract_title(text)
            note_tags = extract_tags(text)
            if wanted and not set(wanted) <= {tag.lower() for tag in note_tags}:
                continue
            haystack = (os.path.basename(path) + "\n" + title + "\n" + text).lower()
            if needle and needle not in haystack:
                continue
            score = 0
            if needle and needle in os.path.basename(path).lower():
                score += 3
            if needle and needle in title.lower():
                score += 2
            if needle and needle in text.lower():
                score += 1
            score += len(wanted)
            scored.append((score, path, title, note_tags))

        if not scored:
            return ("没有找到匹配的笔记（" + ("关键词 " + repr(query) if needle else "")
                    + (" 标签 " + repr(wanted) if wanted else "")
                    + "，笔记库: " + root + "）")
        scored.sort(key=lambda item: (-item[0], item[1]))
        lines = []
        for _score, path, title, note_tags in scored[:limit]:
            relative = os.path.relpath(path, root).replace(os.sep, "/")
            suffix = ("  #" + " #".join(note_tags)) if note_tags else ""
            lines.append("- " + relative + ("（" + title + "）" if title else "") + suffix)
        return truncate_text(
            "找到 " + str(len(scored)) + " 篇笔记（笔记库: " + root + "）：\n"
            + "\n".join(lines), 8000)

    return search_notes


def make_read_note(context: ToolContext):
    def read_note(path: str, max_chars: int = 12000) -> str:
        root = vault_root(context)
        if not root or not os.path.isdir(root):
            return not_bound_message()
        target = str(path or "").strip()
        if not target:
            return error_text("必须提供 path", "BAD_ARGUMENTS")
        if not os.path.splitext(target)[1]:
            target += ".md"
        try:
            resolved = context.check_read(os.path.join(root, target)
                                          if not os.path.isabs(target) else target)
        except SandboxDenied as exc:
            return error_text(str(exc), exc.code)
        if not os.path.exists(resolved):
            return error_text("笔记不存在: " + resolved, "FS_NOT_FOUND")
        text = _read_text(resolved)
        if text is None:
            return error_text("无法解码笔记: " + resolved, "FS_DECODE_FAILED")
        try:
            stat = os.stat(resolved)
            context.remember(resolved, stat, _sha1_text(text))
        except OSError:
            pass
        title = extract_title(text)
        tags = extract_tags(text)
        header = ("笔记: " + os.path.relpath(resolved, root).replace(os.sep, "/")
                  + ("（" + title + "）" if title else "")
                  + ("  标签: " + ", ".join(tags) if tags else ""))
        return truncate_text(header + "\n\n" + text, max(1, int(max_chars)),
                             note="…（笔记过长，已截断；可用 max_chars 调整）…")

    return read_note


def make_write_note(context: ToolContext):
    def write_note(path: str, content: str, title: str = "",
                   tags: Optional[Sequence[str]] = None,
                   update_frontmatter: bool = False) -> str:
        root = vault_root(context)
        if not root or not os.path.isdir(root):
            return not_bound_message()
        target = str(path or "").strip()
        if not target:
            return error_text("必须提供 path", "BAD_ARGUMENTS")
        if not os.path.splitext(target)[1]:
            target += ".md"
        try:
            resolved = context.check_write(os.path.join(root, target)
                                           if not os.path.isabs(target) else target)
        except SandboxDenied as exc:
            return error_text(str(exc), exc.code)

        body = normalize_note(content)
        existing = _read_text(resolved) if os.path.exists(resolved) else None
        if existing is None:
            frontmatter = default_frontmatter(title or os.path.splitext(
                os.path.basename(resolved))[0], tags)
            rendered = frontmatter + "\n" + body
            action = "新建笔记"
        else:
            current_frontmatter, _current_body = split_frontmatter(existing)
            if update_frontmatter or not current_frontmatter:
                frontmatter = default_frontmatter(
                    title or extract_title(existing)
                    or os.path.splitext(os.path.basename(resolved))[0],
                    tags or extract_tags(existing))
                rendered = frontmatter + "\n" + body
                action = "更新笔记（frontmatter 已更新）"
            else:
                # 保留既有 frontmatter，只替换正文（第 4 节明确要求）
                rendered = current_frontmatter.rstrip("\n") + "\n\n" + body
                action = "更新笔记（保留原 frontmatter）"

        try:
            _atomic_write(resolved, rendered)
            stat = os.stat(resolved)
        except OSError as exc:
            return error_text("写入笔记失败: " + str(exc), "FS_WRITE_FAILED")
        context.remember(resolved, stat, _sha1_text(rendered))
        return ("已" + action + " " + resolved + "（" + str(len(rendered)) + " 字符）")

    return write_note


def _schema(properties: Mapping[str, Any], required: Sequence[str] = ()) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "type": "object",
        "properties": dict(properties),
        "additionalProperties": False,
    }
    if required:
        payload["required"] = list(required)
    return payload


def build_note_tools(context: ToolContext) -> List[ToolSpec]:
    """Learn 专属工具；modes 只声明 learn（Chat 永不出现）。"""
    learn_only = frozenset({"learn"})
    return [
        ToolSpec(
            name="search_notes",
            description="在笔记库（Obsidian Vault）里按关键词或标签查找笔记。",
            parameters=_schema({
                "query": {"type": "string", "description": "关键词（文件名 / 标题 / 正文）"},
                "tags": {"type": "array", "description": "标签过滤",
                         "items": {"type": "string"}},
                "max_results": {"type": "integer", "description": "最多返回多少条"},
            }),
            handler=make_search_notes(context),
            read_only=True,
            modes=learn_only,
            kind=TOOL_KIND_GENERIC,
        ),
        ToolSpec(
            name="read_note",
            description="读取笔记库里的某篇笔记（含 frontmatter 与标签）。",
            parameters=_schema({
                "path": {"type": "string", "description": "相对笔记库的路径，如 学习/算法.md"},
                "max_chars": {"type": "integer", "description": "读取字符上限"},
            }, ["path"]),
            handler=make_read_note(context),
            read_only=True,
            modes=learn_only,
            kind=TOOL_KIND_GENERIC,
        ),
        ToolSpec(
            name="write_note",
            description=(
                "写入笔记库（新建或更新）；更新时**保留既有 frontmatter**，"
                "只替换正文，避免覆盖用户手工维护的元数据。"
            ),
            parameters=_schema({
                "path": {"type": "string", "description": "相对笔记库的路径"},
                "content": {"type": "string", "description": "笔记正文（Markdown）"},
                "title": {"type": "string", "description": "标题（新建时写入 frontmatter）"},
                "tags": {"type": "array", "description": "标签", "items": {"type": "string"}},
                "update_frontmatter": {
                    "type": "boolean",
                    "description": "是否连 frontmatter 一起更新，默认 false",
                },
            }, ["path", "content"]),
            handler=make_write_note(context),
            read_only=False,
            requires_approval=True,
            parallel_safe=False,
            modes=learn_only,
            kind=TOOL_KIND_WRITE,
        ),
    ]
