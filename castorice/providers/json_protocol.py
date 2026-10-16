"""提示词内 JSON 约定（不支持 function calling 时的降级协议）。

当厂商能力表判定 supports_tools=False 时，原生 tools 参数不可用，
但模型仍可以按**纯文本约定**输出一次工具调用。本模块提供这套约定的
「声明」与「解析」两端，是 capabilities.ModelCapabilities.protocol() 返回
json 时的实际载体（对齐 docs/refactor/01-gap-matrix.md 第 7 节
「不支持时退回提示词内 JSON 约定」）。

约定格式（协议版本 v1，见 JSON_PROTOCOL_VERSION）：

    {"tool_calls": [{"name": "read_file", "arguments": {"path": "a.txt"}}]}

模型可以直接回一个 JSON 对象，也可以把它放在 ```json 代码块里，
或与解释性文字混排——解析器会优先取**最后一个**合法 JSON 对象，
因为约定要求「需要调用工具时先输出 JSON，再等待结果」。

设计约束（红线 D）：本模块只依赖标准库，不得 import ui / PySide6 / ai。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


JSON_PROTOCOL_VERSION = 1

#: 提示词里的约定说明（{tools} 处填入可见工具的 schema 摘要）
JSON_PROTOCOL_TEMPLATE = (
    "\n\n【工具调用约定（JSON 协议 v{v}）】\n"
    "当前模型不支持原生 function calling，请改用下面的 JSON 约定来调用工具。\n"
    "可用工具：\n{tools}\n"
    "需要调用工具时，**只输出一个 JSON 对象**，不要输出其它内容：\n"
    '{{"tool_calls": [{{"name": "工具名", "arguments": {{...}}}}]}}\n'
    "一次可以并行调用多个工具，把它们放进同一个 tool_calls 数组。\n"
    "不需要调用工具时，直接正常回答即可。\n"
    "收到工具结果后，再基于结果继续作答。"
)

_CODE_FENCE_RE = re.compile(r"\x60\x60\x60(?:json)?\s*(.*?)\x60\x60\x60", re.S | re.I)


@dataclass(frozen=True)
class JsonToolCall:
    """从文本里解析出来的工具调用。"""

    name: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    raw_arguments: str = "{}"

    def to_openai(self, index: int = 0) -> Dict[str, Any]:
        return {
            "id": "json_call_" + str(index),
            "type": "function",
            "function": {"name": self.name, "arguments": self.raw_arguments},
        }


def render_tool_catalog(tool_specs: Sequence[Any]) -> str:
    """把工具 schema 渲染成模型可读的清单（只取名字 / 描述 / 参数）。"""
    lines: List[str] = []
    for spec in tool_specs or ():
        if isinstance(spec, Mapping):
            function = spec.get("function") if spec.get("type") == "function" else spec
            function = function if isinstance(function, Mapping) else {}
            name = str(function.get("name", "") or "")
            description = str(function.get("description", "") or "")
            parameters = function.get("parameters") or {}
        else:
            name = str(getattr(spec, "name", "") or "")
            description = str(getattr(spec, "description", "") or "")
            parameters = getattr(spec, "parameters", None) or {}
        if not name:
            continue
        lines.append("- " + name + "：" + description)
        properties = (parameters or {}).get("properties") if isinstance(parameters, Mapping) else None
        if isinstance(properties, Mapping) and properties:
            detail = ", ".join(
                str(key) + "(" + str((value or {}).get("type", "any")) + ")"
                for key, value in properties.items()
            )
            lines.append("    参数: " + detail)
    return "\n".join(lines) if lines else "（当前没有可用工具）"


def build_json_protocol_instructions(
    tool_specs: Sequence[Any], *, version: int = JSON_PROTOCOL_VERSION
) -> str:
    """生成注入系统提示词的 JSON 协议说明。

    tool_specs 为空时返回空串——**没有可见工具时不产生任何提示词文本**
    （对齐 dsh-mcp-client 的「调用方作用域为空时不添加工具或提示词文本」）。
    """
    if not list(tool_specs or ()):
        return ""
    return JSON_PROTOCOL_TEMPLATE.format(v=version, tools=render_tool_catalog(tool_specs))


def _iter_json_candidates(text: str) -> List[str]:
    """从文本里扫出所有顶层 JSON 对象候选（含代码块内的）。"""
    candidates: List[str] = []
    for match in _CODE_FENCE_RE.finditer(text or ""):
        candidates.append(match.group(1).strip())
    for chunk in _scan_balanced_objects(text or ""):
        candidates.append(chunk)
    return candidates


def _scan_balanced_objects(text: str) -> List[str]:
    """按花括号配平扫出候选 JSON 对象（忽略字符串内的花括号）。"""
    results: List[str] = []
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    results.append(text[start: index + 1])
                    start = -1
    return results


def _coerce_calls(payload: Any) -> Optional[List[JsonToolCall]]:
    """把解析出的 JSON 负载规范成工具调用列表；不是约定结构则返回 None。"""
    if not isinstance(payload, Mapping):
        return None
    raw_calls = payload.get("tool_calls")
    if raw_calls is None:
        # 宽容写法：直接给一个 {"name": ..., "arguments": ...}
        if payload.get("name"):
            raw_calls = [payload]
        else:
            return None
    if isinstance(raw_calls, Mapping):
        raw_calls = [raw_calls]
    if not isinstance(raw_calls, (list, tuple)):
        return None

    calls: List[JsonToolCall] = []
    for item in raw_calls:
        if not isinstance(item, Mapping):
            return None
        function = item.get("function") if isinstance(item.get("function"), Mapping) else None
        name = str(
            (function or {}).get("name") if function else item.get("name") or ""
        ).strip()
        if not name:
            return None
        raw_arguments = (function or {}).get("arguments") if function \
            else item.get("arguments", item.get("parameters"))
        if raw_arguments is None:
            arguments: Dict[str, Any] = {}
            raw_text = "{}"
        elif isinstance(raw_arguments, Mapping):
            arguments = dict(raw_arguments)
            raw_text = json.dumps(arguments, ensure_ascii=False)
        else:
            raw_text = str(raw_arguments)
            try:
                loaded = json.loads(raw_text)
            except (ValueError, TypeError):
                return None
            if isinstance(loaded, Mapping):
                arguments = dict(loaded)
            else:
                arguments = {"value": loaded}
        calls.append(JsonToolCall(name=name, arguments=arguments, raw_arguments=raw_text))
    return calls or None


def parse_json_tool_calls(text: Any) -> Tuple[List[JsonToolCall], str]:
    """从模型回复里解析 JSON 协议的工具调用。

    Returns:
        (工具调用列表, 去掉调用负载后的残留文本)

    取**最后一个**合法候选：约定要求模型先输出调用 JSON，被解析的这段
    不应留在给用户看的文本里；解析失败时工具列表为空、残留文本原样返回。
    """
    content = "" if text is None else str(text)
    if not content.strip():
        return [], content

    offset_ranges: List[Tuple[int, int]] = []
    for match in _CODE_FENCE_RE.finditer(content):
        offset_ranges.append((match.start(), match.end()))
    consumed = len(offset_ranges)

    for index, candidate in enumerate(reversed(_iter_json_candidates(content))):
        try:
            payload = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        calls = _coerce_calls(payload)
        if not calls:
            continue
        remainder = content
        if index < consumed:
            start, end = offset_ranges[len(offset_ranges) - 1 - index]
            remainder = content[:start] + content[end:]
        else:
            remainder = _remove_once(content, candidate)
        return calls, remainder.strip()

    return [], content


def _remove_once(text: str, chunk: str) -> str:
    position = text.find(chunk)
    if position < 0:
        return text
    return text[:position] + text[position + len(chunk):]


def looks_like_json_protocol(text: Any) -> bool:
    """快速判断回复是否「看起来像」JSON 协议调用（供降级诊断用）。"""
    content = "" if text is None else str(text)
    if "tool_calls" not in content:
        return False
    return bool(_iter_json_candidates(content))
