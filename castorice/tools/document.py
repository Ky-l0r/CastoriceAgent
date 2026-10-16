"""文档生成工具：make_document（第一期只写 .md / .txt）。

.docx 留到第二期（浏览器/办公套件依赖过重，与发布包体积预算冲突，
见 04-dsh-harness-notes.md 不适用项 N11）。本模块的做法是：

* 先在内存里渲染好整篇文本，再走**原子写**落盘（同 fs.write_file 的写法）；
* 落盘路径受工作区边界约束（越界写入被拒并给出可读错误）；
* 文档本身可以带 Markdown 结构（标题、表格、列表），由调用方给出。

设计约束：只依赖标准库与同包 base / fs。
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .base import (
    TOOL_KIND_WRITE,
    ToolContext,
    ToolSpec,
    error_text,
)
from .fs import _atomic_write, _sha1_text
from ..permissions import SandboxDenied

#: 第一期支持的扩展名
SUPPORTED_SUFFIXES = (".md", ".txt", ".markdown")
#: 扩展名 -> 默认文件类型
DEFAULT_SUFFIX = ".md"

#: 单篇文档的最大字符数（防止一次写出一个巨型文件）
MAX_DOCUMENT_CHARS = 200_000


def document_header(title: str, *, generated_at: Optional[datetime] = None) -> str:
    """Markdown 文档的统一抬头（便于用户事后辨认来源）。"""
    moment = generated_at or datetime.now()
    return ("# " + str(title) + "\n\n"
            + "> 由 CastoriceAgent 生成于 "
            + moment.strftime("%Y-%m-%d %H:%M:%S") + "\n")


def make_document(context: ToolContext):
    def make_document(
        path: str,
        content: str = "",
        title: str = "",
        sections: Optional[Sequence[Mapping[str, Any]]] = None,
        append_header: bool = True,
    ) -> str:
        target_name = str(path or "").strip()
        if not target_name:
            return error_text("必须提供 path（含文件名的落盘路径）", "BAD_ARGUMENTS")

        suffix = os.path.splitext(target_name)[1].lower()
        if not suffix:
            target_name += DEFAULT_SUFFIX
            suffix = DEFAULT_SUFFIX
        if suffix not in SUPPORTED_SUFFIXES:
            return error_text(
                "不支持的文档格式 " + repr(suffix) + "：第一期只支持 "
                + repr(list(SUPPORTED_SUFFIXES)) + "（.docx 留到第二期）",
                "UNSUPPORTED_FORMAT",
            )

        try:
            target = context.check_write(target_name)
        except SandboxDenied as exc:
            return error_text(str(exc), exc.code)

        pieces: List[str] = []
        if append_header:
            pieces.append(document_header(title or os.path.splitext(
                os.path.basename(target))[0]))
        body = str(content or "")
        if body:
            pieces.append(body.rstrip() + "\n")
        for section in sections or ():
            if not isinstance(section, Mapping):
                return error_text("sections 的每一项都必须是对象", "BAD_ARGUMENTS")
            heading = str(section.get("heading") or section.get("title") or "").strip()
            text = str(section.get("content") or section.get("body") or "").strip()
            if heading:
                pieces.append("## " + heading + "\n")
            if text:
                pieces.append(text + "\n")

        rendered = "\n".join(pieces)
        if not rendered.strip():
            return error_text("文档内容为空", "BAD_ARGUMENTS")
        if len(rendered) > MAX_DOCUMENT_CHARS:
            return error_text(
                "文档过长（" + str(len(rendered)) + " 字符，上限 "
                + str(MAX_DOCUMENT_CHARS) + "）：请拆分后再写",
                "DOCUMENT_TOO_LARGE",
            )

        try:
            _atomic_write(target, rendered)
            stat = os.stat(target)
        except OSError as exc:
            return error_text("写入文档失败: " + str(exc), "FS_WRITE_FAILED")
        context.remember(target, stat, _sha1_text(rendered))
        return ("已生成文档 " + target + "（" + str(len(rendered)) + " 字符）")

    return make_document


def _schema(properties: Mapping[str, Any], required: Sequence[str] = ()) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "type": "object",
        "properties": dict(properties),
        "additionalProperties": False,
    }
    if required:
        payload["required"] = list(required)
    return payload


def build_document_tools(context: ToolContext) -> List[ToolSpec]:
    return [
        ToolSpec(
            name="make_document",
            description=(
                "生成一篇 Markdown 或纯文本文档并落盘（.md / .txt）。"
                "适合把整理好的内容写成用户可以直接打开的交付物。"
            ),
            parameters=_schema({
                "path": {"type": "string", "description": "落盘路径，如 报告.md"},
                "title": {"type": "string", "description": "文档标题（写入抬头）"},
                "content": {"type": "string", "description": "正文（Markdown 文本）"},
                "sections": {
                    "type": "array",
                    "description": "分节内容：[{heading, content}]",
                    "items": {
                        "type": "object",
                        "properties": {
                            "heading": {"type": "string"},
                            "content": {"type": "string"},
                        },
                        "additionalProperties": False,
                    },
                },
                "append_header": {
                    "type": "boolean",
                    "description": "是否写入生成时间抬头，默认 true",
                },
            }, ["path"]),
            handler=make_document(context),
            read_only=False,
            requires_approval=True,
            parallel_safe=False,
            kind=TOOL_KIND_WRITE,
        ),
    ]
