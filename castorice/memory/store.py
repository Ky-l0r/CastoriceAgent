"""JSONL / JSON 落盘后端：原子替换 + 撕裂尾部容忍。

三种记忆模式里 wiki 与 summary 走文件落盘，Worldbook 走单个 JSON 文件。
两者共用本模块，目的是把「写坏一半」这件事收在一处解决：

* 写入：先写同目录临时文件，`flush` + `fsync` 后再 `os.replace` 覆盖，
  任一步失败都不会留下半截的目标文件（对齐 castorice/session.py 的
  持久性屏障做法）；
* 读取：逐行解析，遇到解析失败的行时，**只容忍最后一行**（进程被杀留下的
  撕裂尾部），中间出现坏行说明文件真被破坏，抛错而不是静默丢数据。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

#: 记录格式版本；未知版本的记录在加载时被跳过并计数
FORMAT_VERSION = 1
VERSION_KEY = "v"


class MemoryStoreError(RuntimeError):
    """记忆落盘层的通用错误（IO 失败、格式损坏）。"""


def ensure_directory(path: Path) -> Path:
    """创建目录（含父级），返回同一个 Path，便于链式调用。"""
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _fsync_file(handle: Any) -> None:
    try:
        handle.flush()
        os.fsync(handle.fileno())
    except (OSError, ValueError):  # pragma: no cover - 平台差异上的兜底
        pass


def atomic_write_text(path: Path, text: str) -> None:
    """原子写文本：临时文件 → fsync → os.replace。"""
    path = Path(path)
    ensure_directory(path.parent)
    tmp_fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            _fsync_file(handle)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    """读取 JSONL；容忍撕裂尾部，拒绝版本不认识或中间损坏的文件。"""
    path = Path(path)
    if not path.exists():
        return []
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:  # pragma: no cover - 权限等环境问题
        raise MemoryStoreError(f"读取记忆文件失败: {path} ({exc})") from exc

    lines = list(raw.split("\n"))
    # 末尾换行会产生一个空串，丢掉它（不是数据）
    while lines and not lines[-1].strip():
        lines.pop()

    records: List[Dict[str, Any]] = []
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            if index == len(lines) - 1:
                # 撕裂尾部：最后一行写了一半，丢掉即可（前面的记录完整）
                break
            raise MemoryStoreError(f"记忆文件第 {index + 1} 行损坏: {path} ({exc})") from exc
        if not isinstance(record, dict):
            raise MemoryStoreError(f"记忆文件第 {index + 1} 行不是对象: {path}")
        version = record.get(VERSION_KEY, FORMAT_VERSION)
        if version != FORMAT_VERSION:
            # 不认识的版本：跳过而不是按当前语义误读
            continue
        records.append(record)
    return records


def write_jsonl(path: Path, records: Sequence[Mapping[str, Any]]) -> None:
    """整文件原子替换写入（每条记录自动补齐版本号）。"""
    lines: List[str] = []
    for record in records:
        payload = dict(record)
        payload.setdefault(VERSION_KEY, FORMAT_VERSION)
        lines.append(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    text = "\n".join(lines)
    if text:
        text += "\n"
    try:
        atomic_write_text(Path(path), text)
    except OSError as exc:
        raise MemoryStoreError(f"写入记忆文件失败: {path} ({exc})") from exc


def append_jsonl(path: Path, record: Mapping[str, Any]) -> None:
    """追加一条记录（`O_APPEND` + fsync），崩溃最多留下撕裂尾部。"""
    path = Path(path)
    ensure_directory(path.parent)
    payload = dict(record)
    payload.setdefault(VERSION_KEY, FORMAT_VERSION)
    line = json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
    try:
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line)
            _fsync_file(handle)
    except OSError as exc:
        raise MemoryStoreError(f"追加记忆失败: {path} ({exc})") from exc


def read_json(path: Path, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """读取单个 JSON 对象；文件不存在或为空时返回 `default`。"""
    path = Path(path)
    if not path.exists():
        return dict(default or {})
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:  # pragma: no cover
        raise MemoryStoreError(f"读取 JSON 失败: {path} ({exc})") from exc
    if not raw.strip():
        return dict(default or {})
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MemoryStoreError(f"JSON 损坏: {path} ({exc})") from exc
    if not isinstance(data, dict):
        raise MemoryStoreError(f"JSON 顶层必须是对象: {path}")
    return data


def write_json(path: Path, data: Mapping[str, Any]) -> None:
    """原子写单个 JSON 对象（`ensure_ascii=False`，便于人工查看）。"""
    payload = dict(data)
    payload.setdefault(VERSION_KEY, FORMAT_VERSION)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    try:
        atomic_write_text(Path(path), text)
    except OSError as exc:
        raise MemoryStoreError(f"写入 JSON 失败: {path} ({exc})") from exc


class JsonlStore:
    """带缓存的 JSONL 文件门面（wiki / summary 共用一个实现）。"""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.skipped_versions = 0

    def exists(self) -> bool:
        return self.path.exists()

    def load(self) -> List[Dict[str, Any]]:
        return read_jsonl(self.path)

    def replace(self, records: Sequence[Mapping[str, Any]]) -> None:
        write_jsonl(self.path, records)

    def append(self, record: Mapping[str, Any]) -> None:
        append_jsonl(self.path, record)

    def truncate(self) -> None:
        self.replace([])

    def size_bytes(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0

    def __repr__(self) -> str:  # pragma: no cover - 调试便利
        return f"JsonlStore({str(self.path)!r})"


__all__ = [
    "FORMAT_VERSION",
    "VERSION_KEY",
    "JsonlStore",
    "MemoryStoreError",
    "append_jsonl",
    "atomic_write_text",
    "ensure_directory",
    "read_json",
    "read_jsonl",
    "write_json",
    "write_jsonl",
]
