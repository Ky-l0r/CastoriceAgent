"""文件系统工具：读、写、改、列目录（对齐 dsh-tool-fs 与 fs-observation-policy）。

三条关键语义（R5 / R6）：

1. **编辑前必须读取**（FS_NOT_OBSERVED）：edit_file 只允许改「本次会话读过
   且之后没被外部改动过」的文件；否则拒绝并要求重新读取。这避免覆盖用户
   手工维护的笔记或代码。
2. **版本守卫**（CAS）：读后被外部改动（mtime/size/内容摘要任一变化）-> 拒绝。
3. **原子写**：tempfile 同目录 + os.replace，避免写一半崩溃留下半个文件。

所有路径都过 ToolContext.check_read / check_write，越界一律返回可读错误
文本（不抛异常，轮次继续）。
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .base import (
    DEFAULT_RESULT_LIMIT,
    TOOL_KIND_GENERIC,
    TOOL_KIND_WRITE,
    ToolContext,
    ToolSpec,
    error_text,
    truncate_text,
)
from ..permissions import SandboxDenied

#: 单次读取的最大行数（防止一个巨大文件把上下文吃干）
DEFAULT_READ_LIMIT = 400
#: glob/grep 的结果上限
DEFAULT_SEARCH_LIMIT = 200
#: 跳过的大目录 / 二进制后缀
SKIP_DIRS = frozenset({".git", "__pycache__", "node_modules", ".venv", "venv",
                       "dist", "build", ".pytest_cache", ".mypy_cache"})
SKIP_SUFFIXES = frozenset({
    ".pyc", ".pyo", ".pyd", ".so", ".dll", ".exe", ".bin", ".zip", ".7z", ".rar",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".pdf",
    ".mp3", ".mp4", ".wav", ".onnx", ".dat", ".db", ".sqlite",
})


def _sha1_file(path: str, *, chunk: int = 65536) -> str:
    digest = hashlib.sha1()
    with open(path, "rb") as handle:
        while True:
            data = handle.read(chunk)
            if not data:
                break
            digest.update(data)
    return digest.hexdigest()


def _sha1_text(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest()


def _read_text(path: str) -> Optional[str]:
    try:
        return Path(path).read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            return Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
    except OSError:
        return None


def _atomic_write(path: str, content: str) -> None:
    """原子写：同目录临时文件 + os.replace（对齐 R15 的持久性写法）。"""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", newline="",
        dir=directory, prefix=".castorice-", suffix=".tmp", delete=False,
    )
    try:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    finally:
        handle.close()
    os.replace(handle.name, path)


# ----------------------------------------------------------------------------
# read_file
# ----------------------------------------------------------------------------

def make_read_file(context: ToolContext):
    def read_file(path: str, offset: int = 1, limit: int = DEFAULT_READ_LIMIT) -> str:
        try:
            target = context.check_read(path)
        except SandboxDenied as exc:
            return error_text(str(exc), exc.code)
        if not os.path.exists(target):
            return error_text("文件不存在: " + target, "FS_NOT_FOUND")
        if os.path.isdir(target):
            return error_text("这是一个目录而不是文件: " + target, "FS_IS_DIR")
        try:
            stat = os.stat(target)
            text = _read_text(target)
        except OSError as exc:
            return error_text("读取失败: " + str(exc), "FS_READ_FAILED")
        if text is None:
            return error_text("无法以文本方式解码: " + target, "FS_DECODE_FAILED")

        context.remember(target, stat, _sha1_text(text))
        lines = text.splitlines()
        total = len(lines)
        start = max(1, int(offset or 1))
        count = max(1, int(limit or DEFAULT_READ_LIMIT))
        window = lines[start - 1: start - 1 + count]
        numbered = "\n".join(
            str(start + index).rjust(6) + "\t" + line
            for index, line in enumerate(window)
        )
        header = ("文件: " + target + "（共 " + str(total) + " 行，"
                  "显示第 " + str(start) + "-" + str(start + len(window) - 1) + " 行）")
        return truncate_text(
            header + "\n" + numbered, DEFAULT_RESULT_LIMIT,
            note="…（内容过长，已省略中间部分；可用 offset/limit 分段读取）…",
        )

    return read_file


# ----------------------------------------------------------------------------
# write_file
# ----------------------------------------------------------------------------

def make_write_file(context: ToolContext):
    def write_file(path: str, content: str, create_dirs: bool = True) -> str:
        try:
            target = context.check_write(path)
        except SandboxDenied as exc:
            return error_text(str(exc), exc.code)
        if os.path.isdir(target):
            return error_text("目标是一个目录: " + target, "FS_IS_DIR")
        directory = os.path.dirname(target)
        if directory and not os.path.isdir(directory):
            if not create_dirs:
                return error_text("上级目录不存在: " + directory, "FS_NOT_FOUND")
            try:
                os.makedirs(directory, exist_ok=True)
            except OSError as exc:
                return error_text("创建目录失败: " + str(exc), "FS_WRITE_FAILED")
        existed = os.path.exists(target)
        try:
            _atomic_write(target, str(content))
            stat = os.stat(target)
        except OSError as exc:
            return error_text("写入失败: " + str(exc), "FS_WRITE_FAILED")
        context.remember(target, stat, _sha1_text(str(content)))
        return ("已写入 " + target + "（" + str(len(str(content))) + " 字符，"
                + ("覆盖已有文件" if existed else "新建文件") + "）")

    return write_file


# ----------------------------------------------------------------------------
# edit_file
# ----------------------------------------------------------------------------

def make_edit_file(context: ToolContext):
    def edit_file(path: str, old_text: str, new_text: str,
                  replace_all: bool = False) -> str:
        try:
            target = context.check_write(path)
        except SandboxDenied as exc:
            return error_text(str(exc), exc.code)
        if not os.path.exists(target):
            return error_text(
                "文件不存在: " + target
                + "（edit_file 只改已有文件；新建请用 write_file）", "FS_NOT_FOUND")

        observation = context.observation(target)
        if observation is None:
            return error_text(
                "编辑前必须先读取（FS_NOT_OBSERVED）：请先调用 read_file 读取 "
                + target + " 再修改", "FS_NOT_OBSERVED")
        try:
            stat = os.stat(target)
            current = _read_text(target)
        except OSError as exc:
            return error_text("读取失败: " + str(exc), "FS_READ_FAILED")
        if current is None:
            return error_text("无法以文本方式解码: " + target, "FS_DECODE_FAILED")

        mtime, size, digest = observation
        if (abs(float(stat.st_mtime) - mtime) > 1e-6 or int(stat.st_size) != size
                or _sha1_text(current) != digest):
            context.forget(target)
            return error_text(
                "文件在读取后被外部修改（版本守卫拒绝）：请重新 read_file "
                + target + " 后再编辑，以免覆盖他处改动", "FS_STALE_OBSERVATION")

        needle = str(old_text)
        if not needle:
            return error_text("old_text 不能为空", "BAD_ARGUMENTS")
        occurrences = current.count(needle)
        if occurrences == 0:
            return error_text(
                "未在文件中找到 old_text（edit_file 只做字面量替换，"
                "请连空白与缩进一起给出准确片段）", "FS_NO_MATCH")
        if occurrences > 1 and not replace_all:
            return error_text(
                "old_text 在文件中出现 " + str(occurrences)
                + " 次，请给出更长的唯一片段，或传 replace_all=True",
                "FS_AMBIGUOUS_MATCH")

        updated = current.replace(needle, str(new_text), -1 if replace_all else 1)
        try:
            _atomic_write(target, updated)
            stat = os.stat(target)
        except OSError as exc:
            return error_text("写入失败: " + str(exc), "FS_WRITE_FAILED")
        context.remember(target, stat, _sha1_text(updated))
        return ("已替换 " + target + " 中的 " + str(1 if not replace_all else occurrences)
                + " 处匹配")

    return edit_file


# ----------------------------------------------------------------------------
# list_dir
# ----------------------------------------------------------------------------

def make_list_dir(context: ToolContext):
    def list_dir(path: str = ".", recursive: bool = False,
                 max_entries: int = DEFAULT_SEARCH_LIMIT) -> str:
        try:
            target = context.check_read(path or context.cwd_or_workspace())
        except SandboxDenied as exc:
            return error_text(str(exc), exc.code)
        if not os.path.isdir(target):
            return error_text("不是目录: " + target, "FS_NOT_DIR")
        limit = max(1, int(max_entries or DEFAULT_SEARCH_LIMIT))
        entries: List[str] = []
        if recursive:
            for root, dirs, files in os.walk(target):
                dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
                for name in sorted(files):
                    if Path(name).suffix.lower() in SKIP_SUFFIXES:
                        continue
                    entries.append(os.path.join(root, name))
                    if len(entries) >= limit:
                        break
                if len(entries) >= limit:
                    break
        else:
            for name in sorted(os.listdir(target)):
                entries.append(os.path.join(target, name))
                if len(entries) >= limit:
                    break
        if not entries:
            return "目录为空: " + target
        rendered = []
        for item in entries:
            try:
                is_dir = os.path.isdir(item)
                size = "" if is_dir else str(os.path.getsize(item))
            except OSError:
                is_dir, size = False, ""
            rendered.append(("[目录] " if is_dir else "[文件] ") + item
                            + (("  (" + size + " 字节)") if size else ""))
        return truncate_text("目录: " + target + "\n" + "\n".join(rendered),
                             DEFAULT_RESULT_LIMIT)

    return list_dir


# ----------------------------------------------------------------------------
# glob_files / grep_files（纯 Python，不依赖 rg）
# ----------------------------------------------------------------------------

def _walk_files(root: str, limit: int) -> List[str]:
    found: List[str] = []
    for current, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(files):
            if Path(name).suffix.lower() in SKIP_SUFFIXES:
                continue
            found.append(os.path.join(current, name))
            if len(found) >= limit:
                return found
    return found


def make_glob_files(context: ToolContext):
    from fnmatch import fnmatch

    def glob_files(pattern: str, path: str = ".", max_results: int = DEFAULT_SEARCH_LIMIT) -> str:
        try:
            root = context.check_read(path or context.cwd_or_workspace())
        except SandboxDenied as exc:
            return error_text(str(exc), exc.code)
        if not os.path.isdir(root):
            return error_text("不是目录: " + root, "FS_NOT_DIR")
        limit = max(1, int(max_results or DEFAULT_SEARCH_LIMIT))
        needle = str(pattern or "*").strip() or "*"
        matches = []
        for candidate in _walk_files(root, limit * 20):
            relative = os.path.relpath(candidate, root).replace(os.sep, "/")
            if fnmatch(relative, needle) or fnmatch(os.path.basename(candidate), needle):
                matches.append(candidate)
                if len(matches) >= limit:
                    break
        if not matches:
            return "没有匹配 " + repr(needle) + " 的文件（搜索根: " + root + "）"
        return truncate_text(
            "匹配 " + repr(needle) + " 的文件（" + str(len(matches)) + " 个）：\n"
            + "\n".join(matches), DEFAULT_RESULT_LIMIT)

    return glob_files


def make_grep_files(context: ToolContext):
    def grep_files(pattern: str, path: str = ".", max_results: int = DEFAULT_SEARCH_LIMIT,
                   case_sensitive: bool = True) -> str:
        try:
            root = context.check_read(path or context.cwd_or_workspace())
        except SandboxDenied as exc:
            return error_text(str(exc), exc.code)
        import re as _re

        flags = 0 if case_sensitive else _re.IGNORECASE
        try:
            matcher = _re.compile(str(pattern or ""), flags)
        except _re.error as exc:
            return error_text("正则非法: " + str(exc), "BAD_ARGUMENTS")
        if not str(pattern or ""):
            return error_text("pattern 不能为空", "BAD_ARGUMENTS")
        if os.path.isfile(root):
            candidates = [root]
        else:
            candidates = _walk_files(root, max(1, int(max_results or DEFAULT_SEARCH_LIMIT)) * 20)

        hits: List[str] = []
        limit = max(1, int(max_results or DEFAULT_SEARCH_LIMIT))
        for candidate in candidates:
            text = _read_text(candidate)
            if text is None:
                continue
            for number, line in enumerate(text.splitlines(), 1):
                if matcher.search(line):
                    hits.append(candidate + ":" + str(number) + ": " + line.strip()[:200])
                    if len(hits) >= limit:
                        break
            if len(hits) >= limit:
                break
        if not hits:
            return "没有匹配 " + repr(pattern) + " 的内容（搜索根: " + root + "）"
        return truncate_text(
            "匹配 " + repr(pattern) + " 的行（" + str(len(hits)) + " 条）：\n"
            + "\n".join(hits), DEFAULT_RESULT_LIMIT)

    return grep_files


# ----------------------------------------------------------------------------
# 注册
# ----------------------------------------------------------------------------

def _schema(properties: Mapping[str, Any], required: Sequence[str] = ()) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "type": "object",
        "properties": dict(properties),
        "additionalProperties": False,
    }
    if required:
        payload["required"] = list(required)
    return payload


def build_fs_tools(context: ToolContext) -> List[ToolSpec]:
    """构造文件系统工具集（绑定到给定的上下文）。"""
    return [
        ToolSpec(
            name="read_file",
            description="读取文本文件内容（带行号，可用 offset/limit 分段读取）。",
            parameters=_schema({
                "path": {"type": "string", "description": "文件路径（相对工作区或绝对路径）"},
                "offset": {"type": "integer", "description": "起始行号，默认 1"},
                "limit": {"type": "integer", "description": "最多读取多少行"},
            }, ["path"]),
            handler=make_read_file(context),
            read_only=True,
            parallel_safe=True,
            kind=TOOL_KIND_GENERIC,
        ),
        ToolSpec(
            name="write_file",
            description=(
                "写入文本文件（原子写；目录不存在时自动创建）。"
                "受工作区边界约束，越界写入会被拒绝。"
            ),
            parameters=_schema({
                "path": {"type": "string", "description": "文件路径"},
                "content": {"type": "string", "description": "完整的文件内容"},
                "create_dirs": {"type": "boolean", "description": "是否自动创建上级目录，默认 true"},
            }, ["path", "content"]),
            handler=make_write_file(context),
            read_only=False,
            parallel_safe=False,
            kind=TOOL_KIND_WRITE,
        ),
        ToolSpec(
            name="edit_file",
            description=(
                "在已读取过的文件里做字面量替换（必须先 read_file；"
                "读取后被外部修改会拒绝，要求重读）。"
            ),
            parameters=_schema({
                "path": {"type": "string", "description": "文件路径"},
                "old_text": {"type": "string", "description": "要被替换的原文（含空白与缩进）"},
                "new_text": {"type": "string", "description": "替换后的文本"},
                "replace_all": {"type": "boolean", "description": "是否替换全部匹配，默认 false"},
            }, ["path", "old_text", "new_text"]),
            handler=make_edit_file(context),
            read_only=False,
            parallel_safe=False,
            kind=TOOL_KIND_WRITE,
        ),
        ToolSpec(
            name="list_dir",
            description="列出目录内容（可递归）。",
            parameters=_schema({
                "path": {"type": "string", "description": "目录路径，默认当前工作区"},
                "recursive": {"type": "boolean", "description": "是否递归列出，默认 false"},
                "max_entries": {"type": "integer", "description": "最多列出多少项"},
            }),
            handler=make_list_dir(context),
            read_only=True,
            kind=TOOL_KIND_GENERIC,
        ),
        ToolSpec(
            name="glob_files",
            description="按通配符查找文件（如 **/*.py），不依赖外部 rg。",
            parameters=_schema({
                "pattern": {"type": "string", "description": "通配符，如 *.md 或 src/**/*.py"},
                "path": {"type": "string", "description": "搜索根目录，默认当前工作区"},
                "max_results": {"type": "integer", "description": "最多返回多少条"},
            }, ["pattern"]),
            handler=make_glob_files(context),
            read_only=True,
            kind=TOOL_KIND_GENERIC,
        ),
        ToolSpec(
            name="grep_files",
            description="按正则搜索文件内容，返回文件名、行号与整行。",
            parameters=_schema({
                "pattern": {"type": "string", "description": "正则表达式"},
                "path": {"type": "string", "description": "搜索根目录或单个文件"},
                "max_results": {"type": "integer", "description": "最多返回多少条"},
                "case_sensitive": {"type": "boolean", "description": "是否区分大小写，默认 true"},
            }, ["pattern"]),
            handler=make_grep_files(context),
            read_only=True,
            kind=TOOL_KIND_GENERIC,
        ),
    ]
