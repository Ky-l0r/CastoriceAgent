"""生活服务与音乐播放工具。

第一期刻意**不启动任何外部程序**（对齐 04-dsh-harness-notes.md 不适用项 N5
「便携桌面工具不做常驻终端」与 N11「重型系统级依赖与发布包预算冲突」）：

* play_music 只做两件事——校验音频文件确实在可信目录内，然后把它写进
  播放队列（JSONL）并返回一条结构化播放指令；真正播放由界面层消费该队列
  决定（例如调用系统默认播放器），因此工具本身无副作用、可离线测试；
* set_reminder / list_reminders / clear_reminders 把提醒写成 JSONL 落盘；
* system_info 是只读的系统信息查询。

设计约束：只依赖标准库与同包 base / fs。
"""

from __future__ import annotations

import json
import os
import platform
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .base import (
    TOOL_KIND_ACTION,
    TOOL_KIND_GENERIC,
    TOOL_KIND_WRITE,
    ToolContext,
    ToolSpec,
    error_text,
    truncate_text,
)
from .fs import _atomic_write, _read_text, _sha1_text
from ..permissions import SandboxDenied

#: 支持的音频后缀
AUDIO_SUFFIXES = frozenset({
    ".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma", ".ape",
})

#: 队列与提醒的落盘文件名（放在工作区根目录下）
QUEUE_FILE = "music_queue.jsonl"
REMINDER_FILE = "reminders.jsonl"

MAX_REMINDER_CHARS = 500


def _append_jsonl(context: ToolContext, name: str, payload: Mapping[str, Any]) -> str:
    """向工作区里的 JSONL 文件追加一条记录（受写边界约束，原子替换）。"""
    target = context.check_write(os.path.join(context.effective_workspace, name))
    existing = _read_text(target) or ""
    line = json.dumps(dict(payload), ensure_ascii=False)
    updated = existing + line + "\n"
    _atomic_write(target, updated)
    stat = os.stat(target)
    context.remember(target, stat, _sha1_text(updated))
    return target


def make_play_music(context: ToolContext):
    def play_music(path: str = "", query: str = "") -> str:
        if not str(path or "").strip() and not str(query or "").strip():
            return error_text("必须提供 path（本地音频文件）或 query（曲目名）",
                              "BAD_ARGUMENTS")
        entry: Dict[str, Any] = {
            "requested_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "query": str(query or ""),
        }
        if str(path or "").strip():
            try:
                target = context.check_read(path)
            except SandboxDenied as exc:
                return error_text(str(exc), exc.code)
            if not os.path.exists(target):
                return error_text("音频文件不存在: " + target, "FS_NOT_FOUND")
            if os.path.splitext(target)[1].lower() not in AUDIO_SUFFIXES:
                return error_text(
                    "不是受支持的音频格式: " + repr(os.path.splitext(target)[1])
                    + "，可选 " + repr(sorted(AUDIO_SUFFIXES)),
                    "UNSUPPORTED_FORMAT",
                )
            entry["path"] = target
        try:
            queue_path = _append_jsonl(context, QUEUE_FILE, entry)
        except SandboxDenied as exc:
            return error_text(str(exc), exc.code)
        except OSError as exc:
            return error_text("写入播放队列失败: " + str(exc), "FS_WRITE_FAILED")

        target_desc = entry.get("path") or ("曲目 " + repr(entry.get("query")))
        return ("已把播放请求加入队列：" + target_desc
                + "\n队列文件: " + queue_path
                + "\n说明：第一期不自动启动外部播放器，界面层会消费该队列"
                "（这是刻意的安全设计——工具本身不产生副作用）。")

    return play_music


def make_set_reminder(context: ToolContext):
    def set_reminder(text: str, when: str = "") -> str:
        content = str(text or "").strip()
        if not content:
            return error_text("必须提供 text（提醒内容）", "BAD_ARGUMENTS")
        if len(content) > MAX_REMINDER_CHARS:
            return error_text(
                "提醒内容过长（上限 " + str(MAX_REMINDER_CHARS) + " 字符）",
                "BAD_ARGUMENTS")
        entry = {
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "when": str(when or "").strip(),
            "text": content,
            "done": False,
        }
        try:
            target = _append_jsonl(context, REMINDER_FILE, entry)
        except SandboxDenied as exc:
            return error_text(str(exc), exc.code)
        except OSError as exc:
            return error_text("写入提醒失败: " + str(exc), "FS_WRITE_FAILED")
        when_text = ("（" + entry["when"] + "）") if entry["when"] else ""
        return "已记下提醒" + when_text + "：" + content + "\n提醒文件: " + target

    return set_reminder


def make_list_reminders(context: ToolContext):
    def list_reminders(include_done: bool = True) -> str:
        try:
            target = context.check_read(
                os.path.join(context.effective_workspace, REMINDER_FILE))
        except SandboxDenied as exc:
            return error_text(str(exc), exc.code)
        if not os.path.exists(target):
            return "还没有任何提醒。"
        text = _read_text(target) or ""
        reminders: List[Dict[str, Any]] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if isinstance(item, Mapping) and (include_done or not item.get("done")):
                reminders.append(dict(item))
        if not reminders:
            return "还没有任何提醒。"
        rendered = []
        for index, item in enumerate(reminders, 1):
            when = item.get("when") or "（未指定时间）"
            mark = "已完成 " if item.get("done") else ""
            rendered.append(str(index) + ". [" + str(when) + "] " + mark
                            + str(item.get("text", "")))
        return truncate_text("提醒（" + str(len(rendered)) + " 条）：\n"
                             + "\n".join(rendered), 8000)

    return list_reminders


def make_clear_reminders(context: ToolContext):
    def clear_reminders() -> str:
        try:
            target = context.check_write(
                os.path.join(context.effective_workspace, REMINDER_FILE))
        except SandboxDenied as exc:
            return error_text(str(exc), exc.code)
        if not os.path.exists(target):
            return "还没有任何提醒，无需清空。"
        try:
            _atomic_write(target, "")
        except OSError as exc:
            return error_text("清空提醒失败: " + str(exc), "FS_WRITE_FAILED")
        context.forget(target)
        return "已清空提醒：" + target

    return clear_reminders


def make_system_info(context: ToolContext):
    def system_info() -> str:
        lines = [
            "系统: " + platform.system() + " " + platform.release(),
            "版本: " + platform.version(),
            "机器: " + platform.machine(),
            "Python: " + sys.version.split()[0] + " (" + sys.executable + ")",
            "工作区: " + str(context.effective_workspace),
            "沙箱: " + str(context.sandbox),
            "模式: " + str(context.mode),
        ]
        try:
            load = os.getloadavg()
            lines.append("平均负载: " + ", ".join(f"{value:.2f}" for value in load))
        except (OSError, AttributeError):
            pass
        if hasattr(os, "cpu_count"):
            lines.append("CPU 核数: " + str(os.cpu_count()))
        return "\n".join(lines)

    return system_info


def _schema(properties: Mapping[str, Any], required: Sequence[str] = ()) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "type": "object",
        "properties": dict(properties),
        "additionalProperties": False,
    }
    if required:
        payload["required"] = list(required)
    return payload


def build_life_tools(context: ToolContext) -> List[ToolSpec]:
    """生活服务 + 音乐播放工具集。"""
    return [
        ToolSpec(
            name="play_music",
            description=(
                "把一首本地音频加入播放队列并返回播放指令（不会自动启动外部播放器）。"
                "可以给 path（本地文件）或 query（曲目名）。"
            ),
            parameters=_schema({
                "path": {"type": "string", "description": "本地音频文件路径"},
                "query": {"type": "string", "description": "曲目名（交给界面层匹配）"},
            }),
            handler=make_play_music(context),
            read_only=False,
            parallel_safe=False,
            kind=TOOL_KIND_ACTION,
        ),
        ToolSpec(
            name="set_reminder",
            description="记下一条提醒（写入工作区的 reminders.jsonl）。",
            parameters=_schema({
                "text": {"type": "string", "description": "提醒内容"},
                "when": {"type": "string", "description": "时间描述，如「明天上午」"},
            }, ["text"]),
            handler=make_set_reminder(context),
            read_only=False,
            parallel_safe=False,
            kind=TOOL_KIND_WRITE,
        ),
        ToolSpec(
            name="list_reminders",
            description="列出已经记下的提醒。",
            parameters=_schema({
                "include_done": {"type": "boolean", "description": "是否包含已完成，默认 true"},
            }),
            handler=make_list_reminders(context),
            read_only=True,
            kind=TOOL_KIND_GENERIC,
        ),
        ToolSpec(
            name="clear_reminders",
            description="清空所有提醒。",
            parameters=_schema({}),
            handler=make_clear_reminders(context),
            read_only=False,
            requires_approval=True,
            parallel_safe=False,
            kind=TOOL_KIND_WRITE,
        ),
        ToolSpec(
            name="system_info",
            description="查询当前系统与运行环境信息（平台、Python 版本、工作区、沙箱）。",
            parameters=_schema({}),
            handler=make_system_info(context),
            read_only=True,
            kind=TOOL_KIND_GENERIC,
        ),
    ]
