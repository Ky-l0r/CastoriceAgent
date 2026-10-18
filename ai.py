"""
AI核心模块
"""

import os
import re
import io
import json
import base64
import random
import html as html_module
import logging
import contextlib
import threading
from datetime import datetime
from dataclasses import dataclass
from typing import Optional, List, Dict, Any, Union, Sequence, Callable, Tuple
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

import yaml
import chromadb
from chromadb.api.models.Collection import Collection
from openai import OpenAI
from openai.types.chat import ChatCompletionMessageParam

import castorice
import castorice.profile as cprofile
import castorice.tools as ctools
import castorice.tools.web as tools_web
from castorice import (
    HarnessLoop,
    Mode,
    ModeRegistry,
    PermissionDecision,
    PermissionRequest,
    SessionProjection,
    SessionStore,
    StopReason,
    ToolSpec,
    WorkdirError,
    build_harness_registry,
    resolve_mode_name,
)
from castorice import permissions as cpermissions
from castorice import providers as cproviders


# ============================================================================
# 日志配置
# ============================================================================

def setup_logger(name: str = "AI") -> logging.Logger:

    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    
    return logger


# ============================================================================
# 配置管理
# ============================================================================

def _yaml_scalar_text(value: Any) -> str:
    """把 Python 值格式化成 YAML 标量文本（字符串统一加双引号）"""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _yaml_key_of(line: str) -> Tuple[int, Optional[str]]:
    """
    解析一行 YAML 的缩进与键名

    Returns:
        (缩进空格数, 键名)；不是「键: 值」行时键名为 None
    """
    raw = line.rstrip("\n")
    stripped = raw.strip()
    if not stripped or stripped.startswith("#") or stripped.startswith("- "):
        return (-1, None)
    indent = len(raw) - len(raw.lstrip(" "))
    match = re.match(r"^([^:#]+?)\s*:(?:\s|$)", stripped)
    if not match:
        return (indent, None)
    key = match.group(1).strip().strip('"\'')
    return (indent, key)


def _locate_yaml_key(lines: List[str], path: Tuple[str, ...]) -> Optional[int]:
    """定位指定键路径所在的行号；不存在返回 None"""
    stack: List[Tuple[int, str]] = []
    for index, line in enumerate(lines):
        indent, key = _yaml_key_of(line)
        if key is None:
            continue
        while stack and stack[-1][0] >= indent:
            stack.pop()
        stack.append((indent, key))
        if tuple(item[1] for item in stack) == path:
            return index
    return None


def update_yaml_scalars(text: str, updates: Dict[Tuple[str, ...], Any]) -> str:
    """
    更新 YAML 文本中指定路径的标量值，保留原有注释与排版

    只改动目标键所在行（行尾注释原样保留）；键不存在时在父块开头补一行。
    找不到父块则跳过该键，不擅自创造结构。

    Args:
        text: 原始 YAML 文本
        updates: {(键路径...): 新值}，如 {("providers", "deepseek", "api_key"): "sk-x"}

    Returns:
        更新后的 YAML 文本
    """
    lines = text.splitlines()

    for path, value in updates.items():
        rendered = _yaml_scalar_text(value)
        index = _locate_yaml_key(lines, path)

        if index is not None:
            raw = lines[index]
            match = re.match(r"^(\s*(?:-\s+)?[^:#]+?:\s*)(.*)$", raw)
            if match:
                rest = match.group(2)
                # 保留行尾注释（连同其前面的空白）
                comment = ""
                hash_index = rest.find("#")
                if hash_index >= 0:
                    start = hash_index
                    while start > 0 and rest[start - 1] in " \t":
                        start -= 1
                    comment = rest[start:]
                lines[index] = f"{match.group(1)}{rendered}{comment}"
            continue

        # 键不存在：追加到父块的末尾
        parent = path[:-1]
        if not parent:
            continue
        parent_index = _locate_yaml_key(lines, parent)
        if parent_index is None:
            continue
        parent_indent, _ = _yaml_key_of(lines[parent_index])
        
        insert_at = parent_index + 1
        for index in range(parent_index + 1, len(lines)):
            raw = lines[index]
            if not raw.strip():
                continue
            current_indent = len(raw) - len(raw.lstrip(" "))
            if current_indent <= parent_indent:
                break
            insert_at = index + 1
        
        lines.insert(
            insert_at,
            f"{' ' * (parent_indent + 2)}{path[-1]}: {rendered}"
        )

    result = "\n".join(lines)
    if text.endswith("\n"):
        result += "\n"
    return result


class ConfigManager:

    
    DEFAULT_CONFIG_PATH = "config.yaml"
    
    def __init__(self, config_path: str = DEFAULT_CONFIG_PATH):

        self.config_path = Path(config_path)
        self._config: Optional[Dict[str, Any]] = None
        self._load_config()
    
    def _load_config(self) -> None:
        """加载配置文件"""
        if not self.config_path.exists():
            raise FileNotFoundError(f"配置文件不存在: {self.config_path}")
        
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                self._config = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ValueError(f"配置文件格式错误: {e}")
    
    def get(self, key: str, default: Any = None) -> Any:

        if self._config is None:
            return default
        
        keys = key.split('.')
        value = self._config
        
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
                if value is None:
                    return default
            else:
                return default
        
        return value
    
    def get_provider_config(self, provider_name: str) -> Dict[str, Any]:

        provider_config = self.get(f'providers.{provider_name}')
        if not provider_config:
            raise ValueError(f"未找到提供商配置: {provider_name}")
        
        return provider_config
    
    def get_active_provider(self) -> str:
        """
        获取当前激活的提供商
        
        Returns:
            提供商名称
        """
        provider = self.get('active_provider')
        if not provider:
            raise ValueError("未配置 active_provider")
        return provider
    
    @property
    def provider_names(self) -> List[str]:
        """所有已配置的提供商名称"""
        providers = self.get('providers') or {}
        if not isinstance(providers, dict):
            return []
        return list(providers.keys())
    
    def set_values(
        self, updates: Dict[Tuple[str, ...], Any]
    ) -> None:
        """
        写回配置项（保留 config.yaml 的注释与排版），随后重新加载

        Args:
            updates: {(键路径...): 新值}
        """
        if not self.config_path.exists():
            raise FileNotFoundError(f"配置文件不存在: {self.config_path}")
        
        text = self.config_path.read_text(encoding="utf-8")
        new_text = update_yaml_scalars(text, updates)
        self.config_path.write_text(new_text, encoding="utf-8")
        self._load_config()
    
    def set_provider_values(
        self, provider_name: str, values: Dict[str, Any]
    ) -> None:
        """更新某个提供商的配置字段"""
        updates = {
            ("providers", provider_name, key): value
            for key, value in values.items()
        }
        self.set_values(updates)
    
    def set_active_provider(self, provider_name: str) -> None:
        """切换当前激活的提供商"""
        self.set_values({("active_provider",): provider_name})
    
    def reload(self) -> None:
        """重新读取配置文件（外部修改后同步内存中的配置）"""
        self._load_config()
    
    @property
    def config(self) -> Dict[str, Any]:
        """获取完整配置"""
        if self._config is None:
            raise ValueError("配置未加载")
        return self._config


# ============================================================================
# 提示词管理
# ============================================================================

class PromptModule:
    """一个基础提示词文件（prompts/*.md）解析后的形态。

    条目级触发词标注（worldbook 风格）：

        ## 条目标题
        - 触发词: 词1, 词2, 词3
        - 常驻: 否
        - 内在价值: 80
        - 优先级: 150
        - 连带触发词: 另一条目
        （正文……）

    解析规则（与 README「触发词注入」一节一致）：
    - 以 "## " 开头的行开启一个条目，直到下一个 "## " 或文件结束；
    - 条目头部各行识别 触发词 / 常驻 / 优先级 字段（内在价值与
      连带触发词仅记录，不参与注入决策）；
    - 没有任何条目级触发词标注的文件整体视为常驻模块（缺省安全：
      identity / soul / tone 这类纯常驻文件无需任何标注）；
    - 触发词大小写不敏感（英文），按子串匹配用户消息。
    """

    #: 条目头部里会被解析的字段行
    _TRIGGER_RE = re.compile(r"^\s*-\s*触发词\s*[:：]\s*(.*)$")
    _RESIDENT_RE = re.compile(r"^\s*-\s*常驻\s*[:：]\s*(.*)$")
    _PRIORITY_RE = re.compile(r"^\s*-\s*优先级\s*[:：]\s*(\d+)\s*$")
    _HEADING_RE = re.compile(r"^##\s+")

    def __init__(self, name: str, content: str):
        self.name = name
        self.content = content
        self.header = content
        self.entries: List[Dict[str, Any]] = []
        self._parse_entries()

    # -- 解析 -----------------------------------------------------------------

    def _parse_entries(self) -> None:
        """把文件切成「文件头 + 条目列表」；无条目标注时保持整段常驻。"""
        lines = self.content.split("\n")
        heading_indexes = [
            i for i, line in enumerate(lines) if self._HEADING_RE.match(line)
        ]
        if not heading_indexes:
            return  # 整个文件是常驻模块
        self.header = "\n".join(lines[:heading_indexes[0]]).strip()

        bounds = heading_indexes + [len(lines)]
        for start, stop in zip(bounds, bounds[1:]):
            block = lines[start:stop]
            title = block[0].lstrip("# ").strip()
            triggers: List[str] = []
            resident: Optional[bool] = None
            priority = 0
            body_start = 1
            for offset in range(1, len(block)):
                line = block[offset]
                hit = self._TRIGGER_RE.match(line)
                if hit:
                    triggers = [
                        word.strip()
                        for word in re.split(r"[,，、]", hit.group(1))
                        if word.strip()
                    ]
                    body_start = offset + 1
                    continue
                hit = self._RESIDENT_RE.match(line)
                if hit:
                    resident = hit.group(1).strip().lower() in (
                        "是", "true", "yes", "1")
                    body_start = offset + 1
                    continue
                hit = self._PRIORITY_RE.match(line)
                if hit:
                    priority = int(hit.group(1))
                    body_start = offset + 1
                    continue
                if line.strip() and not line.lstrip().startswith("-"):
                    break  # 头部结束，进入正文
            body = "\n".join(block[body_start:]).strip()
            # 「常驻: 是」的条目随模块常驻注入，不参与触发词筛选
            content = "## " + title
            if body:
                content = content + "\n" + body
            self.entries.append({
                "title": title,
                "triggers": tuple(triggers),
                "resident": resident is True,
                "priority": priority,
                "content": content,
            })

        # 文件里出现过「触发词」标注才算条目化模块；否则全部还原为常驻
        # （防止只写了标题结构、忘了写触发词的文件被整体漏掉）
        if self.entries and not any(e["triggers"] for e in self.entries):
            self.entries = []
            self.header = self.content

    # -- 查询 -----------------------------------------------------------------

    @property
    def is_entry_based(self) -> bool:
        """是否为条目化模块（有触发词标注、可按条目筛选）。"""
        return bool(self.entries)

    def resident_content(self) -> str:
        """常驻部分：文件头 + 标记为「常驻: 是」的条目。"""
        parts = [self.header] if self.header else []
        parts.extend(
            entry["content"] for entry in self.entries if entry["resident"]
        )
        return "\n\n".join(part for part in parts if part)

    def matched_entries(self, message: str) -> List[Dict[str, Any]]:
        """按用户消息命中条目；返回按优先级降序的条目快照（确定性）。

        排序键写进快照（priority / _hits / _order），供跨文件统一排序：
        优先级降序 -> 命中触发词数降序 -> 文件内出现顺序（稳定排序）。
        """
        haystack = (message or "").lower()
        if not haystack:
            return []
        matched: List[Dict[str, Any]] = []
        for index, entry in enumerate(self.entries):
            if entry["resident"] or not entry["triggers"]:
                continue
            count = sum(
                1 for word in entry["triggers"] if word.lower() in haystack
            )
            if count:
                snapshot = dict(entry)
                snapshot["_hits"] = count
                snapshot["_order"] = index
                matched.append(snapshot)
        matched.sort(key=lambda e: (-e["priority"], -e["_hits"], e["_order"]))
        return matched


class PromptManager:
    """
    提示词管理器
    
    负责加载和管理AI提示词模板。

    触发词注入（第二期）：基础提示词文件可按条目携带触发词标注，
    get_prompt(user_message=...) 只注入常驻部分与命中条目；
    不传 user_message（或 config 的 prompts.enabled 未开启）时保持
    全量拼接的旧行为，旧配置零改动即可运行。
    """
    
    DEFAULT_PROMPTS_DIR = "prompts"

    #: 模式化提示词分层目录（由 castorice.Mode.system_prompt_sections 选择）
    MODES_SUBDIR = "modes"

    #: 兼容别名（02-architecture.md §2.1 用的是 prompt_sections）
    MODES_ALIAS = ("talk", "chat")

    #: config.yaml prompts 节的缺省值：关闭动态筛选 = 现全量拼接
    DEFAULT_DYNAMIC_CONFIG: Dict[str, Any] = {
        "enabled": False,
        "resident_modules": [],   # 空 = 按文件自身标注自动判定
        "max_entries": 0,         # 0 = 不限制单轮注入的命中条目数
    }
    
    def __init__(
        self,
        prompts_dir: str = DEFAULT_PROMPTS_DIR,
        config: Optional[Dict[str, Any]] = None,
    ):

        self.prompts_dir = Path(prompts_dir)
        self._prompt: Optional[str] = None
        self._sections: Dict[str, str] = {}
        self._modules: List[PromptModule] = []
        self._dynamic: Dict[str, Any] = dict(self.DEFAULT_DYNAMIC_CONFIG)
        if isinstance(config, dict):
            self._dynamic.update({
                key: value for key, value in config.items()
                if key in self.DEFAULT_DYNAMIC_CONFIG
            })
        self._load_prompts()

    def _sections_dir(self) -> Path:
        return self.prompts_dir / self.MODES_SUBDIR
    
    def _load_prompts(self) -> None:
        """加载所有提示词文件"""
        if not self.prompts_dir.exists():
            self._prompt = ""
            return
        
        prompt_parts = []
        modules: List[PromptModule] = []
        
        for filepath in sorted(self.prompts_dir.iterdir()):
            if filepath.suffix.lower() in ['.md', '.txt']:
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        content = f.read().strip()
                        if content:
                            prompt_parts.append(content)
                            modules.append(
                                PromptModule(filepath.name, content))
                except Exception as e:
                    print(f"警告: 无法读取提示词文件 {filepath}: {e}")
        
        self._prompt = "\n\n".join(prompt_parts)
        self._modules = modules
        self._load_sections()

    def _load_sections(self) -> None:
        """加载 prompts/modes/ 下的模式化提示词分层。

        这一层是「提示词分层」的落点：基础提示词（即本目录下的 *.md）
        保持不变，模式专属段落按需追加。目录不存在时静默降级为「无分层」，
        旧仓库结构不需要任何改动即可运行。
        """
        self._sections = {}
        sections_dir = self._sections_dir()
        if not sections_dir.is_dir():
            return
        for filepath in sorted(sections_dir.iterdir()):
            if filepath.suffix.lower() not in ('.md', '.txt'):
                continue
            try:
                content = filepath.read_text(encoding="utf-8").strip()
            except Exception as e:
                print(f"警告: 无法读取模式提示词 {filepath}: {e}")
                continue
            if content:
                self._sections[filepath.stem.lower()] = content

    @property
    def sections(self) -> Dict[str, str]:
        """可用的模式提示词分层（名称 -> 内容）"""
        return dict(self._sections)

    def get_section(self, name: str) -> str:
        """按名取一个分层；不存在返回空串（模式名与文件名不一致时不报错）"""
        return self._sections.get(str(name).lower(), "")

    def get_prompt(
        self,
        memory_context: Optional[str] = None,
        mode: Optional["Mode"] = None,
        user_message: Optional[str] = None,
    ) -> str:
        """组装系统提示词：基础提示词 + 该模式的提示词分层 (+ 记忆上下文)

        Args:
            memory_context: 追加到末尾的记忆上下文
            mode: 模式对象；其 system_prompt_sections 决定追加哪些分层
            user_message: 本轮用户消息。为 None（或动态筛选未开启）时使用
                全量拼接的旧行为；非 None 且 prompts.enabled=True 时只注入
                常驻部分与命中条目（触发词注入，第二期 t2）。
        """
        prompt = self._select_base_prompt(user_message)

        if mode is not None:
            prompt += self.compose_sections(
                getattr(mode, 'system_prompt_sections', ()) or ()
            )
        
        if memory_context:
            prompt += f"\n\n【联想到的相关记忆】：\n{memory_context}"
        
        return prompt

    # -- 触发词注入（第二期 t2） ------------------------------------------------

    @property
    def dynamic_enabled(self) -> bool:
        """触发词筛选是否开启（config.yaml 的 prompts.enabled）。"""
        return bool(self._dynamic.get("enabled"))

    def _resident_names(self) -> List[str]:
        """常驻模块名单：配置覆盖优先，否则按文件标注自动判定。"""
        configured = self._dynamic.get("resident_modules") or []
        if configured:
            wanted = {str(name).strip().lower() for name in configured}
            return [
                module.name for module in self._modules
                if module.name.lower() in wanted
            ]
        return [module.name for module in self._modules if not module.is_entry_based]

    def _select_base_prompt(self, user_message: Optional[str]) -> str:
        """按 user_message 决定基础提示词的形态。

        - 未开启筛选或 user_message 为 None：全量拼接（旧行为，逐字节一致）；
        - 开启筛选：常驻模块 + 命中条目，按 (优先级, 命中数, 文件顺序) 排序；
          命中条目数超过 max_entries（>0 时）按该序截断。
          常驻集恒非空兜底：若配置把常驻名单写空且没有可判定模块，
          退回完整第一模块，保证不出现空提示词。
        """
        if not self.dynamic_enabled or user_message is None:
            return self._prompt or ""
        resident_names = set(self._resident_names())
        # resident_modules 显式点名时是「完全替换」：名单外的非条目化模块
        # 不再自动视为常驻（用户明确选了核心集）；条目化模块仍按条目筛选。
        explicit_resident = bool(self._dynamic.get("resident_modules"))
        parts: List[str] = []
        matched: List[Dict[str, Any]] = []
        for module in self._modules:
            if module.name in resident_names:
                content = module.content
            elif explicit_resident and not module.is_entry_based:
                content = ""  # 名单外的纯常驻文件被显式排除
            else:
                content = module.resident_content()
                matched.extend(module.matched_entries(user_message))
            if content:
                parts.append(content)
        if matched:
            # 跨文件统一排序：优先级降序 -> 命中数降序 -> (文件序, 条目序)
            # （各文件的 matched_entries 已带 priority/_hits/_order 排序键）
            limit = self._dynamic.get("max_entries") or 0
            if limit and limit > 0:
                matched = matched[:int(limit)]
            parts.extend(entry["content"] for entry in matched)
        if not parts and self._modules:
            parts.append(self._modules[0].content)  # 兜底：绝不返回空提示词
        self.last_selection = {
            "resident": sorted(resident_names),
            "matched": [entry["title"] for entry in matched],
            "selected_bytes": sum(len(part.encode("utf-8")) for part in parts),
            "full_bytes": len((self._prompt or "").encode("utf-8")),
        }
        return "\n\n".join(parts)

    def selection_report(self, message: str) -> Dict[str, Any]:
        """量化一次消息的筛选效果（不改变任何状态，纯查询）。

        返回 dict：resident / matched / selected_bytes / full_bytes / saved_bytes
        与 saved_ratio（0~1，越大说明本轮省得越多）。确定性：同消息同结果。
        """
        prompt = self._select_base_prompt(message)
        stats = dict(getattr(self, "last_selection", {}))
        full = int(stats.get("full_bytes") or 0)
        selected = int(stats.get("selected_bytes")
                       or len(prompt.encode("utf-8")))
        report = {
            "resident": stats.get("resident", []),
            "matched": stats.get("matched", []),
            "selected_bytes": selected,
            "full_bytes": full,
            "saved_bytes": max(full - selected, 0),
        }
        report["saved_ratio"] = (
            round(report["saved_bytes"] / full, 4) if full else 0.0)
        return report

    def compose_sections(self, names: Sequence[str]) -> str:
        """把若干分层名拼成一段文本；空名与缺失名一律跳过"""
        parts: List[str] = []
        seen = set()
        for name in names or ():
            key = str(name).lower()
            if key in seen:
                continue
            seen.add(key)
            content = self._sections.get(key)
            if content:
                parts.append(content)
        if not parts:
            return ""
        return "\n\n" + "\n\n".join(parts)
    
    @property
    def prompt(self) -> str:
        """获取基础提示词"""
        return self._prompt or ""


# ============================================================================
# 记忆管理
# ============================================================================

class MemoryManager:
    """
    记忆管理器
    
    负责管理向量数据库中的记忆存储和检索。
    """
    
    DEFAULT_COLLECTION_NAME = "memories"
    DEFAULT_CHROMA_DIR = "database"
    DEFAULT_SEARCH_RESULTS = 5
    
    def __init__(
        self,
        chroma_dir: str = DEFAULT_CHROMA_DIR,
        collection_name: str = DEFAULT_COLLECTION_NAME
    ):
        """
        初始化记忆管理器
        
        Args:
            chroma_dir: ChromaDB数据存储目录
            collection_name: 集合名称
        """
        self.chroma_dir = Path(chroma_dir)
        self.collection_name = collection_name
        
        # 确保数据目录存在
        self.chroma_dir.mkdir(parents=True, exist_ok=True)
        
        # 初始化ChromaDB客户端
        self._client = chromadb.PersistentClient(path=str(self.chroma_dir))
        self._collection = self._client.get_or_create_collection(
            name=collection_name
        )
    
    def add_memory(
        self, content: str, timestamp: Optional[datetime] = None
    ) -> str:
        """
        添加记忆到数据库（同时记录写入时刻的日期与时间）

        时间戳写进 metadata 而不是正文：正文保持纯净，向量检索质量不受
        日期数字干扰；检索时再把时间拼回每条记忆前面，
        这样模型既知道「这件事是什么时候说的」，又不影响召回效果。

        Args:
            content: 记忆内容
            timestamp: 记忆时刻，默认取当前本地时间

        Returns:
            记忆ID
        """
        moment = timestamp or datetime.now()
        stamp = moment.strftime("%Y-%m-%d %H:%M:%S")
        memory_id = str(self._collection.count() + 1)
        
        self._collection.add(
            documents=[content],
            metadatas=[{
                "timestamp": stamp,
                "date": moment.strftime("%Y-%m-%d"),
                "time": moment.strftime("%H:%M:%S"),
                "weekday": moment.strftime("%A"),
            }],
            ids=[memory_id]
        )
        
        return memory_id
    
    def search_memories(
        self,
        query: str,
        n_results: int = DEFAULT_SEARCH_RESULTS
    ) -> List[str]:
        """
        检索相关记忆

        返回的每条记忆都自带写入时刻前缀（[YYYY-MM-DD HH:MM:SS]），
        时间来自 metadata，因此旧数据（没有记录时间）会保持原样返回。

        Args:
            query: 检索关键词
            n_results: 返回条数

        Returns:
            记忆文本列表
        """
        try:
            results = self._collection.query(
                query_texts=[query],
                n_results=n_results,
                include=["documents", "metadatas"]
            )
            
            documents = (results or {}).get('documents') or [[]]
            metadatas = (results or {}).get('metadatas') or [[]]
            docs = documents[0] if documents else []
            metas = metadatas[0] if metadatas else []
            
            memories: List[str] = []
            for index, doc in enumerate(docs):
                if not doc:
                    continue
                meta = metas[index] if index < len(metas) else None
                stamp = (meta or {}).get('timestamp')
                memories.append(f"[{stamp}] {doc}" if stamp else doc)
            return memories
            
        except Exception as e:
            print(f"搜索记忆时出错: {e}")
            return []
    
    def clear(self) -> None:
        """清空所有记忆"""
        # 删除并重新创建集合
        self._client.delete_collection(self.collection_name)
        self._collection = self._client.create_collection(
            name=self.collection_name
        )
    
    @property
    def collection(self) -> Collection:
        """获取ChromaDB集合"""
        return self._collection
    
    @property
    def count(self) -> int:
        """获取记忆数量"""
        return self._collection.count()


# ============================================================================
# LLM提供商适配器
# ============================================================================

class LLMProvider:
    """
    LLM提供商基类
    
    定义统一的LLM接口。
    """
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self._client = None
        self._setup_client()
    
    def _setup_client(self) -> None:
        """设置客户端"""
        raise NotImplementedError("子类必须实现 _setup_client 方法")
    
    def chat_completion(
        self,
        messages: Sequence[Dict[str, Any]],
        stream: bool = True,
        **kwargs
    ) -> Tuple[str, List[Dict[str, Any]]]:
        """
        执行聊天补全

        Args:
            messages: 消息列表
            stream: 是否使用流式输出
            **kwargs: 额外参数（如 tools）

        Returns:
            (生成的文本, 工具调用列表)；无工具调用时工具列表为空
        """
        raise NotImplementedError("子类必须实现 chat_completion 方法")


class OpenAICompatibleProvider(LLMProvider):
    """
    OpenAI兼容的LLM提供商
    
    支持OpenAI、DeepSeek、阿里云等兼容OpenAI API的服务。
    """
    
    def _setup_client(self) -> None:
        """设置OpenAI客户端"""
        api_key = self.config.get('api_key')
        base_url = self.config.get('base_url')
        
        if not api_key:
            raise ValueError("缺少 api_key 配置")
        
        client_kwargs = {'api_key': api_key}
        if base_url:
            client_kwargs['base_url'] = base_url
        
        self._client = OpenAI(**client_kwargs)
        self._model = self.config.get('model', 'gpt-3.5-turbo')
    
    def chat_completion(
        self,
        messages: Sequence[Dict[str, Any]],
        stream: bool = True,
        **kwargs
    ) -> Tuple[str, List[Dict[str, Any]]]:
        """
        执行聊天补全，支持流式文本与流式工具调用（function calling）

        Returns:
            (完整文本, 工具调用列表)
            工具调用格式(与OpenAI一致):
            [{'id': str, 'type': 'function',
              'function': {'name': str, 'arguments': str}}]
        """
        try:
            # 构建请求参数
            request_params = {
                'model': self._model,
                'messages': messages,
                'stream': stream,
                **kwargs
            }
            
            # 添加可选参数
            if 'temperature' in self.config:
                request_params['temperature'] = self.config['temperature']
            
            if stream:
                # 流式输出：同时累积文本和工具调用增量
                stream_response = self._client.chat.completions.create(**request_params)
                
                full_reply = ""
                tool_slots: Dict[int, Dict[str, str]] = {}
                
                for chunk in stream_response:
                    if not chunk.choices or len(chunk.choices) == 0:
                        continue
                    delta = chunk.choices[0].delta
                    
                    if delta.content:
                        content = delta.content.replace('\n\n', '\n')
                        full_reply += content
                    
                    if delta.tool_calls:
                        for tc in delta.tool_calls:
                            slot = tool_slots.setdefault(
                                tc.index, {'id': '', 'name': '', 'args': ''}
                            )
                            if tc.id:
                                slot['id'] = tc.id
                            if tc.function:
                                if tc.function.name:
                                    slot['name'] += tc.function.name
                                if tc.function.arguments:
                                    slot['args'] += tc.function.arguments
                
                tool_calls = [
                    {
                        'id': slot['id'] or f"call_{i}",
                        'type': 'function',
                        'function': {
                            'name': slot['name'],
                            'arguments': slot['args']
                        }
                    }
                    for i, slot in sorted(tool_slots.items())
                ]
                return full_reply, tool_calls
            else:
                # 非流式输出
                response = self._client.chat.completions.create(**request_params)
                message = response.choices[0].message
                tool_calls = []
                if getattr(message, 'tool_calls', None):
                    for tc in message.tool_calls:
                        tool_calls.append({
                            'id': tc.id or f"call_{len(tool_calls)}",
                            'type': 'function',
                            'function': {
                                'name': tc.function.name,
                                'arguments': tc.function.arguments or '{}'
                            }
                        })
                return message.content or "", tool_calls
                
        except Exception as e:
            raise Exception(f"LLM API调用失败: {e}")


class LLMProviderFactory:
    """
    LLM提供商工厂
    
    根据配置创建相应的LLM提供商实例。
    """
    
    @staticmethod
    def create_provider(provider_name: str, config: Dict[str, Any]) -> LLMProvider:
        """
        创建LLM提供商实例
        
        Args:
            provider_name: 提供商名称
            config: 提供商配置
            
        Returns:
            LLM提供商实例
        """
        # 目前只实现了OpenAI兼容的提供商
        # 可以根据需要扩展其他类型的提供商
        return OpenAICompatibleProvider(config)


# ============================================================================
# 工具系统（Agent 的核心）
# ============================================================================

@dataclass
class Tool:
    """
    工具定义（向后兼容的最小形态）

    castorice.tools.ToolSpec 是它的增强版（多出 read_only / modes / kind /
    result_limit 等元数据与构造期校验）。ToolRegistry 在注册时做一次适配，
    因此老代码继续用 Tool 也不会失去新的策略能力。

    Args:
        name: 工具名称（模型调用时使用）
        description: 工具描述（帮助模型决定何时调用）
        parameters: JSON Schema 格式的参数定义
        function: 实际执行的函数，接收参数字典并返回字符串结果
    """
    name: str
    description: str
    parameters: Dict[str, Any]
    function: Callable[..., str]
    read_only: bool = False
    requires_approval: Optional[bool] = None
    parallel_safe: bool = True
    result_limit: int = ctools.DEFAULT_RESULT_LIMIT
    modes: Optional[Tuple[str, ...]] = None

    def to_spec(self) -> "ctools.ToolSpec":
        """适配成 castorice.tools.ToolSpec（注册表内部的统一形态）。"""
        modes = self.modes or tuple(sorted(ctools.DEFAULT_TOOL_MODES))
        return ctools.ToolSpec(
            name=self.name,
            description=self.description,
            parameters=dict(self.parameters or {}),
            handler=self.function,
            read_only=bool(self.read_only),
            requires_approval=self.requires_approval,
            parallel_safe=bool(self.parallel_safe),
            result_limit=int(self.result_limit),
            modes=frozenset(modes),
        )


class ToolRegistry:
    """
    工具注册表：注册、描述、调用工具

    内部持有 castorice.tools.ToolSpec（带元数据与构造期校验），对外保持
    与旧实现完全一致的 register / to_openai_specs / call 三个方法，
    并新增 specs() / kernel_specs() / by_source() 供内核与界面读取。

    构造期校验（重名 / 坏 schema / 未知模式）由 ctools.ToolSpec 与
    ctools.ToolRegistry 负责，错误在注册时立刻抛出而不是静默忽略。
    """

    def __init__(self, tools: Optional[Sequence["Tool"]] = None) -> None:
        self._registry = ctools.ToolRegistry()
        for tool in tools or ():
            self.register(tool)

    def register(self, tool: "Tool", *, replace_existing: bool = True) -> "Tool":
        """注册一个工具（同名默认覆盖，保持旧行为；传 False 则抛错）"""
        spec = tool.to_spec() if isinstance(tool, Tool) else tool
        self._registry.register(spec, replace_existing=replace_existing)
        return tool

    def register_spec(self, spec: "ctools.ToolSpec",
                      *, replace_existing: bool = True) -> "ctools.ToolSpec":
        """直接注册内核形态的工具描述（castorice.tools 用）。"""
        return self._registry.register(spec, replace_existing=replace_existing)

    def to_openai_specs(self) -> List[Dict[str, Any]]:
        """生成 OpenAI function calling 格式的工具列表"""
        return self._registry.to_openai_specs()

    def specs(self) -> List["ctools.ToolSpec"]:
        """内核形态的工具描述列表（带 read_only / modes / kind 等元数据）"""
        return self._registry.all()

    def kernel_specs(self) -> List["ToolSpec"]:
        """castorice.modes.ToolSpec 列表（ModeRegistry 直接消费）"""
        return self._registry.kernel_specs()

    def by_source(self, source: str) -> List["ctools.ToolSpec"]:
        return self._registry.by_source(source)

    def names(self) -> List[str]:
        return self._registry.names()

    def __contains__(self, name: Any) -> bool:
        return str(name) in self._registry

    def __len__(self) -> int:
        return len(self._registry)

    def call(self, name: str, arguments: Dict[str, Any]) -> str:
        """
        调用工具并返回结果文本

        Args:
            name: 工具名称
            arguments: 工具参数

        Returns:
            工具执行结果（字符串）；失败时返回错误信息
        """
        return self._registry.call(name, arguments)


# ----------------------------------------------------------------------------
# 内置工具实现
# ----------------------------------------------------------------------------

def tool_get_current_time() -> str:
    """查询当前日期和时间"""
    now = datetime.now()
    return now.strftime("当前本地时间：%Y-%m-%d %H:%M:%S（%A）")


# ----------------------------------------------------------------------------
# 联网工具（六后端 + 自动降级）——实现已迁到 castorice/tools/web.py
# ----------------------------------------------------------------------------
#
# 迁移原则（01-gap-matrix.md 第 6 节 / R18）：六个后端名与 auto 的优先级
# 一字不改，ai.py 侧保留同名再导出，保证旧引用（ai.SearchManager、
# ai._html_to_text、ai.BingHTMLSearch 等）与既有行为都不失效。
# 新增的 web_fetch 与「外部内容不可信」前缀由 castorice/tools/ 提供。

_UA = tools_web._UA
_html_to_text = tools_web._html_to_text
SearchBackend = tools_web.SearchBackend
BingHTMLSearch = tools_web.BingHTMLSearch
DDGHTMLSearch = tools_web.DDGHTMLSearch
TavilySearch = tools_web.TavilySearch
SerperSearch = tools_web.SerperSearch
BingAPISearch = tools_web.BingAPISearch
BochaSearch = tools_web.BochaSearch
SearchManager = tools_web.SearchManager
format_search_results = tools_web.format_search_results
WebSearchTool = tools_web.WebSearchTool
WebFetchTool = tools_web.WebFetchTool


# ----------------------------------------------------------------------------
# 内置工具注册表（构造期校验 + 能力元数据）
# ----------------------------------------------------------------------------

def create_builtin_tools(
    search_config: Optional[Dict[str, Any]] = None,
    *,
    tools_config: Optional[Dict[str, Any]] = None,
    context: Optional["ctools.ToolContext"] = None,
    mcp: Optional["ctools.McpRegistry"] = None,
) -> ToolRegistry:
    """按配置创建内置工具注册表。

    兼容旧的单参数调用（只传 search 配置）；额外的关键字参数让装配侧注入
    工具上下文（工作区 / 沙箱 / 权限网关）与 MCP 登记表。

    返回的 ai.ToolRegistry 内部是 ctools.ToolSpec，因此每个工具都带有
    read_only / requires_approval / parallel_safe / modes / result_limit
    元数据，权限网关与内核据此裁决。
    """
    registry = ToolRegistry()
    built = ctools.build_default_registry(
        {"search": search_config or {}, "tools": tools_config or {}},
        context=context,
        mcp=mcp,
    )
    for spec in built.all():
        registry.register_spec(spec, replace_existing=True)
    return registry


# ============================================================================
# 表情包系统（贴纸）
# ============================================================================

# 表情包标签规范：模型在回复中用 [表情:名称] 内联引用 Image/表情包 下的贴纸
EMOJI_TAG_RE = re.compile(
    r"[\[【]\s*(?:表情包?|贴纸|emoji|sticker)\s*[:：]\s*([^\]】\n]{1,24}?)\s*[\]】]",
    re.IGNORECASE,
)

# 用户明确表示不要发图片时的关键词（本回合不再自动补表情包）
_EMOJI_OPT_OUT_RE = re.compile(
    r"(不要|别|不用|无需|禁止|不许)[^。！？\n]{0,6}(表情|贴纸|图片|图|emoji)", re.I
)

# [表情:名] 的起始标记（用于流式解析时判断是否需要暂存字符）
EMOJI_TAG_PREFIXES = ("[表", "[贴", "[e", "[E", "【表", "【贴")

# 名称 -> 心情描述（内置默认；可被 config.yaml 的 emoji.moods 覆盖）
# 对应 Image/表情包 目录下的文件名主干
DEFAULT_EMOJI_MOODS: Dict[str, str] = {
    "喜欢": "喜欢、心动、被吸引、觉得对方可爱、心里甜甜的",
    "喜欢1": "悄悄喜欢、眼神藏不住、忍不住多看几眼",
    "害羞": "害羞、被夸后不好意思、脸红、紧张",
    "害羞1": "格外害羞、捂脸、不敢直视、被看穿心思",
    "惊讶": "惊讶、吃惊、没想到、意外",
    "惊讶1": "睁大眼睛的惊讶、愣住、被震到",
    "生气": "生气、不满、被惹到、故作凶巴巴",
    "疑问": "疑惑、不解、没听懂、想把事情问清楚",
    "疑问1": "歪着头想不通、有点懵、迷惑",
    "哭泣": "难过、委屈、伤心、想哭、低落",
    "困倦": "困了、疲惫、想睡觉、打哈欠、没精神",
    "欣赏": "欣赏、赞许、觉得不错、认可对方",
    "称赞": "称赞、夸奖对方、觉得厉害、真诚赞美",
    "耍帅": "耍帅、自信、有点得意、故作潇洒",
    "苦恼": "苦恼、烦恼、纠结、为难、头疼",
    "苦恼1": "更深的苦恼、无奈、叹气、想不出办法",
}

# 名称 -> 心情关键词（用于从回复文本猜心情）
# 说明：关键词长度需 >= 2，避免「想」「累」这类单字误命中
DEFAULT_EMOJI_KEYWORDS: Dict[str, List[str]] = {
    "喜欢": ["喜欢", "心动", "好可爱", "爱你", "在意你", "想你", "好喜欢",
             "舍不得", "中意", "偏爱"],
    "喜欢1": ["偷偷喜欢", "忍不住看", "多看一眼", "着迷", "移不开眼"],
    "害羞": ["害羞", "不好意思", "脸红", "羞", "别夸我", "讨厌啦", "被你发现",
             "难为情"],
    "害羞1": ["捂脸", "不敢看", "羞死", "太羞", "躲起来", "别看我"],
    "惊讶": ["惊讶", "吃惊", "没想到", "竟然", "居然", "真的吗", "不会吧"],
    "惊讶1": ["吓了一跳", "愣住", "震惊", "天啊", "怎么会", "不敢相信"],
    "生气": ["生气", "过分", "气死", "不理你", "讨厌你", "欺负", "别闹",
             "哼"],
    "疑问": ["疑问", "不解", "为什么", "什么意思", "没听懂", "不太明白",
             "确定吗", "是这样吗"],
    "疑问1": ["想不通", "有点懵", "迷惑", "搞不懂", "奇怪", "哪里不对"],
    "哭泣": ["难过", "伤心", "委屈", "想哭", "低落", "不开心", "失落",
             "难受", "撑不住", "崩溃"],
    "困倦": ["困了", "好累", "疲惫", "想睡", "睡觉", "打哈欠", "没精神",
             "熬夜", "晚安", "睁不开眼"],
    "欣赏": ["欣赏", "有品味", "认可", "佩服", "不错", "挺好的", "很赞"],
    "称赞": ["称赞", "夸", "好棒", "真棒", "厉害", "优秀", "干得好",
             "太强了", "了不起"],
    "耍帅": ["耍帅", "帅气", "得意", "自信", "很酷", "骄傲", "厉害吧"],
    "苦恼": ["苦恼", "烦恼", "纠结", "为难", "头疼", "怎么办", "麻烦了"],
    "苦恼1": ["叹气", "无奈", "想不出", "没办法", "发愁", "难办", "没辙"],
}

# 关键词都匹配不上时的兜底候选（随机取用）
# 只放情绪温和、不易出错的几张，避免在语气平和时随机到
# 哭泣 / 生气 / 苦恼 这类强烈情绪而显得违和
_FALLBACK_MOOD_ORDER = ["欣赏", "害羞", "疑问", "耍帅", "喜欢", "惊讶"]


@dataclass
class EmojiSticker:
    """一个表情包贴纸"""
    name: str                      # 标签名，也是文件名主干
    path: str                      # 图片绝对路径
    mood: str = ""                 # 心情描述（给模型看）
    keywords: Tuple[str, ...] = () # 心情关键词（无标签时兜底用）

    @property
    def file_name(self) -> str:
        return os.path.basename(self.path)


class EmojiCatalog:
    """
    表情包目录

    表情包统一放在 Image/表情包 子目录（该子目录不存在时退回 Image 本身，
    兼容旧结构），扫描其中的图片文件（跳过 _ 开头的 UI 资源），
    每个文件名主干即表情包标签名。同名文件（如 喜欢.png / 喜欢.jpg）
    会归到同名的多个贴纸，发送时随机挑一个，避免重复。
    """

    SUPPORTED_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
    SKIP_PREFIXES = ("_", ".")
    # 表情包专用子目录名
    SUBDIR_NAME = "表情包"

    def __init__(
        self,
        image_dir: Union[str, Path] = "Image",
        avatar_name: str = "CastoriceAvatar",
        moods: Optional[Dict[str, str]] = None,
        keywords: Optional[Dict[str, List[str]]] = None,
    ):
        self._root_dir = Path(image_dir)
        self.image_dir = self._resolve_dir(self._root_dir)
        self.avatar_name = avatar_name
        self._moods = dict(DEFAULT_EMOJI_MOODS)
        self._moods.update(moods or {})
        self._keywords = {k: list(v) for k, v in DEFAULT_EMOJI_KEYWORDS.items()}
        for name, words in (keywords or {}).items():
            self._keywords[name] = list(words)

        # 名称 -> 贴纸列表（同名多个文件）
        self._stickers: Dict[str, List[EmojiSticker]] = {}
        self._build()

    # ------------------------------------------------------------------
    # 构建
    # ------------------------------------------------------------------

    @classmethod
    def _resolve_dir(cls, image_dir: Path) -> Path:
        """优先使用 <image_dir>/表情包 子目录，不存在则用 <image_dir> 本身"""
        sub_directory = image_dir / cls.SUBDIR_NAME
        if sub_directory.is_dir():
            return sub_directory
        return image_dir

    def _build(self) -> None:
        # 每次重建都重新解析目录，便于用户新建/移动表情包子目录后热更新
        self.image_dir = self._resolve_dir(self._root_dir)
        if not self.image_dir.exists():
            return

        for filepath in sorted(self.image_dir.iterdir()):
            if not filepath.is_file():
                continue
            if filepath.suffix.lower() not in self.SUPPORTED_SUFFIXES:
                continue
            if filepath.name.startswith(self.SKIP_PREFIXES):
                continue

            name = filepath.stem
            if name == self.avatar_name:
                continue

            sticker = EmojiSticker(
                name=name,
                path=str(filepath),
                mood=self._moods.get(name, ""),
                keywords=tuple(self._keywords.get(name, ())),
            )
            self._stickers.setdefault(name, []).append(sticker)

    def reload(self) -> None:
        """重新扫描目录（用户新增图片后可热更新）"""
        self._stickers.clear()
        self._build()

    def set_root_dir(self, image_dir: Union[str, Path]) -> None:
        """更换图片根目录并重新扫描"""
        self._root_dir = Path(image_dir)
        self.reload()

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    @property
    def names(self) -> List[str]:
        """所有表情包标签名（按文件名排序）"""
        return list(self._stickers.keys())

    @property
    def count(self) -> int:
        return len(self._stickers)

    def __bool__(self) -> bool:
        return bool(self._stickers)

    def __contains__(self, name: str) -> bool:
        return self._normalize(name) in self._stickers

    def get(self, name: str) -> Optional[EmojiSticker]:
        """按名称取贴纸（同名随机挑一个，让同一心情有变化）"""
        candidates = self._stickers.get(self._normalize(name))
        if not candidates:
            return None
        return random.choice(candidates)

    def labels(self) -> List[str]:
        """名称+心情描述，用于注入提示词"""
        items = []
        for name, stickers in self._stickers.items():
            mood = stickers[0].mood or "（未定义心情）"
            items.append(f"{name}（{mood}）")
        return items

    @staticmethod
    def _normalize(name: str) -> str:
        """把模型可能写出的名称规整到目录里的真实名称"""
        name = (name or "").strip()
        name = name.replace("表情包", "表情").replace("贴纸", "")
        name = name.strip(" 　'\"“”‘’")
        if name.endswith("表情"):
            name = name[:-2].strip()
        return name

    # ------------------------------------------------------------------
    # 匹配心情
    # ------------------------------------------------------------------

    def match_by_mood(self, text: str) -> Optional[str]:
        """按心情关键词从回复文本里猜一个表情包名；猜不到则给一个通用表情"""
        if not text:
            return None
        scores: Dict[str, int] = {}
        for name, keywords in self._keywords.items():
            if name not in self._stickers:
                continue
            # 太短的关键词（单字）容易误命中，直接忽略
            score = sum(1 for kw in keywords if len(kw) >= 2 and kw in text)
            if score:
                scores[name] = score
        if scores:
            best = max(scores.values())
            top = [n for n, s in scores.items() if s == best]
            # 同分时随机，避免永远只发同一个
            return random.choice(top)

        # 兜底：从通用候选里随机挑一个（避免每次都是同一张）
        available = [n for n in _FALLBACK_MOOD_ORDER if n in self._stickers]
        if not available:
            available = self.names
        return random.choice(available) if available else None


class EmojiSession:
    """
    单个机器人实例的表情包发送状态

    控制：是否启用、出现概率、冷却回合、每场合计上限。
    默认 probability=0.35、cooldown_turns=2，即偶尔（约三分之一概率，
    且发过一次后至少隔两轮）才附带一个表情包，不会每条回复都发。
    """

    def __init__(
        self,
        enabled: bool = True,
        probability: float = 0.35,
        cooldown_turns: int = 2,
        max_per_session: int = 0,
    ):
        self.enabled = enabled
        self.probability = max(0.0, min(1.0, probability))
        self.cooldown_turns = max(0, cooldown_turns)
        self.max_per_session = max(0, max_per_session)
        self.reset()

    def reset(self) -> None:
        """新一轮会话：清空计数（切换提供商 / 清空历史时调用）"""
        self._turns = 0
        self._last_used_turn = -999
        self._sent = 0

    def should_append(self) -> bool:
        """本轮回复是否应该附带表情包"""
        if not self.enabled:
            return False
        if self.max_per_session and self._sent >= self.max_per_session:
            return False
        # 必发模式（probability=1）：每轮都带一个，冷却不生效
        if self.probability >= 1.0:
            return True
        # 冷却：刚发过就先停几轮
        if self._turns - self._last_used_turn <= self.cooldown_turns:
            return False
        # 第一轮必发一次，让新会话立刻有情绪；之后按概率
        if self._turns == 1:
            return True
        return random.random() < self.probability

    def mark_turn(self) -> None:
        """回合开始：轮次 +1"""
        self._turns += 1

    def mark_sent(self) -> None:
        """记录一次发送"""
        self._sent += 1
        self._last_used_turn = self._turns

    @property
    def sent_count(self) -> int:
        return self._sent

    @property
    def turn(self) -> int:
        return self._turns


def _to_bool(value: Any, default: bool = True) -> bool:
    """宽松地把配置值转成 bool"""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in ("0", "false", "no", "off", "none", "")


def _to_float(value: Any, default: float) -> float:
    """宽松地把配置值转成 float"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value: Any, default: int) -> int:
    """宽松地把配置值转成 int"""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


# ============================================================================
# AI聊天机器人
# ============================================================================

class AI:
    """
    AI聊天机器人核心类
    
    整合大模型API、记忆系统和提示词管理。
    """
    
    def __init__(
        self,
        config_path: str = "config.yaml",
        prompts_dir: str = "prompts",
        chroma_dir: str = "database",
        collection_name: str = "memories",
        provider_name: Optional[str] = None,
        image_dir: str = "Image",
        sessions_dir: str = "sessions",
        mode: Optional[str] = None
    ):  
        
        # 初始化日志
        self.logger = setup_logger("AI")
        
        # 初始化配置管理器
        self.config_manager = ConfigManager(config_path)
        
        # 初始化提示词管理器（触发词注入配置走 config.yaml 的 prompts 节，
        # 缺省关闭 = 全量拼接旧行为，旧配置零改动可跑）
        self.prompt_manager = PromptManager(
            prompts_dir, config=self.config_manager.get('prompts') or {})
        
        # 初始化记忆管理器
        self.memory_manager = MemoryManager(chroma_dir, collection_name)
        
        # 初始化LLM提供商
        self._provider_name = provider_name or self.config_manager.get_active_provider()
        self._setup_provider()
        
        # 工具执行上下文（工作区 / 沙箱 / 权限网关），先于工具注册表建立：
        # 工具闭包在调用时读它，因此绑定目录变化后无需重建工具。
        self._tool_context = self._build_tool_context()

        # MCP 扩展接入点（第一期只登记，不建立传输）
        self._mcp_registry = ctools.McpRegistry()

        # 初始化工具系统（传入搜索配置与工具上下文）
        self.tool_registry = create_builtin_tools(
            self.config_manager.get('search', {}),
            tools_config=self.config_manager.get('tools') or {},
            context=self._tool_context,
            mcp=self._mcp_registry,
        )
        
        # 初始化表情包系统（扫描 Image 目录 + 读取 emoji 配置）
        self._init_emoji(image_dir)
        
        # 初始化对话历史（消息可包含 content=None 与 tool_calls，故用 Any 值）
        self._messages: List[Dict[str, Any]] = []

        # 四模式内核（模式注册表 / 会话事件日志 / agent 主循环）
        self._init_harness(sessions_dir)

        # 按 config.yaml 的 mode 字段（可用 mode 参数覆盖）选择模式；
        # 切换即激活该模式自己的会话，因此四个模式的对话历史天然相互隔离
        self.set_mode(mode or self._config_mode(), persist=False)
        
        self.logger.info(
            f"AI聊天机器人初始化完成 (提供商: {self._provider_name}, "
            f"模式: {self._mode_name}, 内核: castorice {castorice.__version__}, "
            f"表情包: {self.emoji_catalog.count} 个 @ {self.emoji_catalog.image_dir})"
        )

    #: 只读工具（兼容保留）：真正的判定已交给 castorice/permissions.py 的
    #: PermissionGateway，它按每个 ToolSpec 的 read_only 元数据裁决；
    #: 这张表仍然放行同名工具，用于兼容旧路径与「网关不可用」时的降级。
    READONLY_TOOLS = frozenset({
        'get_current_time', 'web_search', 'web_fetch',
        'read_file', 'list_dir', 'glob_files', 'grep_files',
        'search_notes', 'read_note', 'list_reminders', 'system_info',
    })

    def _build_tool_context(self) -> "ctools.ToolContext":
        """构造工具执行上下文（工作区 / 沙箱 / 可信根）。"""
        modes_config = self.config_manager.get('modes') or {}
        workspace = ""
        if isinstance(modes_config, dict):
            for key in ('code', 'work', 'learn'):
                node = modes_config.get(key)
                if isinstance(node, dict):
                    workspace = node.get('workdir') or node.get('vault') or ""
                    if workspace:
                        break
        workspace = workspace or self.config_manager.get('workdir') or str(Path.cwd())
        # 此处 _mode_registry 还没建立（工具上下文先于 _init_harness 构造），
        # 因此只能取「与模式无关」的全局配置；随后 _sync_permission_context
        # 会用 resolve_sandbox(模式) 把它校正成该模式的实际生效值。
        # 配置非法时回落 read-only（故障安全：宁可拒绝，不可放行）。
        return ctools.ToolContext(
            workspace=str(workspace),
            cwd=os.getcwd(),
            sandbox=self._config_sandbox_default() or cpermissions.SANDBOX_READ_ONLY,
            mode='chat',
            roots=tuple(self._workdir_sources().get('roots') or ()),
        )

    def _init_harness(self, sessions_dir: str) -> None:
        """装配四模式内核：模式注册表 → 权限桥 → 会话 → agent 主循环

        依赖方向永远是 ai.py → castorice/（红线 D）；内核只拿到 provider、
        工具执行入口与权限桥，不知道 UI 的存在。
        """
        self.sessions_dir = Path(sessions_dir)
        self._sessions: Dict[str, SessionStore] = {}

        # 每个工具声明自己可见的模式（Chat 永不在内），因此 ModelRegistry 的
        # 构造期校验按「工具的 modes 声明」裁剪，而不是按全局工具表。
        self._mode_registry = self._build_mode_registry()
        self._mode_name = self._config_mode()
        self._bind_configured_workdirs()

        self._permissions = self._init_permissions()
        self._kernel_tools = _KernelTools(
            self.tool_registry, self._mode_registry.all_tools(),
            gateway=self._permissions, mode=self._mode_name)
        self._harness = HarnessLoop(
            session=self._placeholder_session(),
            provider=self._provider,
            modes=self._mode_registry,
            tools=self._kernel_tools,
            permissions=self._permissions,
            max_iterations=self.MAX_ITERATIONS,
            logger=self.logger,
        )
        self._session = self._harness.session
        self._session_projection = SessionProjection(self._session)

    def _build_mode_registry(self) -> "ModeRegistry":
        """按每个工具声明的 modes 构造模式注册表。

        ModelRegistry 的构造期校验要求「模式声明的工具名必须已注册」，
        因此这里按工具自己的 modes 声明把工具名裁进对应模式：这样 Learn 的
        笔记工具不会出现在 Code 的工具表里，而 Chat 的可见集合恒为空。
        """
        allowed: Dict[str, set] = {}
        for spec in self.tool_registry.specs():
            for mode_name in spec.modes:
                allowed.setdefault(str(mode_name), set()).add(spec.name)

        # N1（t12/t15）根因修复：builtin_modes(known_tools=...) 的入参语义
        # 是「当前已注册的工具名」（见 castorice/modes.py builtin_modes），
        # 过去这里传的是 set(KNOWN_MODES) | set(allowed)——纯模式名集合，
        # 导致 LEARN_ALLOWED 与它的交集恒空、learn 退化为 deny-ALL
        # （整表禁用），deny-ALL 又放行了拒绝表之外的提醒/音乐等工具。
        # 正确来源就是注册表里全部已注册工具名；allowed 只是每个工具声明
        # 自己可见模式的镜像，不再参与裁剪。
        declared = frozenset(
            spec.name for spec in self.tool_registry.specs())
        assert not (declared & set(ctools.KNOWN_MODES)), (
            "工具名与模式名出现重名，builtin_modes 的裁剪语义会被破坏")
        modes = []
        for mode in castorice.builtin_modes(declared):
            names = tuple(sorted(allowed.get(mode.name, set()) & set(mode.tool_allowlist)))
            if mode.name == "chat":
                modes.append(mode)
                continue
            modes.append(
                mode if names == tuple(sorted(mode.tool_allowlist))
                else type(mode)(
                    name=mode.name,
                    display_name=mode.display_name,
                    tool_allowlist=names,
                    system_prompt_sections=mode.system_prompt_sections,
                    workdir_policy=mode.workdir_policy,
                    tool_policy=(
                        mode.tool_policy if names
                        else castorice.ToolPolicy(allow=None,
                                                  deny=castorice.ALL_TOOLS_DENIED)
                    ),
                    supports_vision_input=mode.supports_vision_input,
                )
            )
        return ModeRegistry(
            tools=self.tool_registry.kernel_specs(),
            modes=modes,
            sources=self._workdir_sources(),
        )

    def _config_mode(self) -> str:
        """读取 config.yaml 的 mode 字段（缺失 / 非法一律回退到 chat）"""
        return resolve_mode_name(
            self.config_manager.get('mode'),
            self._mode_registry.names(),
        )

    def _workdir_sources(self) -> Dict[str, Any]:
        """可信根目录来源：配置里的显式根 + 程序当前目录"""
        sources: Dict[str, Any] = {}
        modes_config = self.config_manager.get('modes') or {}
        for key in ('code', 'learn', 'work'):
            node = modes_config.get(key) if isinstance(modes_config, dict) else None
            if isinstance(node, dict):
                for field in ('workdir', 'vault'):
                    if node.get(field):
                        sources.setdefault('roots', []).append(node[field])
        for extra in ('workdir', 'code_root', 'vault'):
            if self.config_manager.get(extra):
                sources.setdefault('roots', []).append(self.config_manager.get(extra))
        sources.setdefault('roots', []).append(str(Path.cwd()))
        return sources

    def _bind_configured_workdirs(self) -> None:
        """把 config.yaml 里的 modes.* 目录绑定到对应模式（越界即忽略）"""
        modes_config = self.config_manager.get('modes') or {}
        if not isinstance(modes_config, dict):
            return
        for name, node in modes_config.items():
            if not isinstance(node, dict) or not self._mode_registry.has(name):
                continue
            for field in ('workdir', 'vault'):
                if node.get(field):
                    try:
                        self._mode_registry.bind_workdir(name, node[field])
                    except WorkdirError as e:
                        self.logger.warning(f"忽略越界的工作目录配置 {name}.{field}: {e}")
                    break

    def _config_sandbox_default(self) -> Optional[str]:
        """读取 permissions.sandbox_default（未配置 / 非法时返回 None）。

        非法的配置值不能直接塞进 Mode：WorkdirPolicy.__post_init__ 会对未知
        沙箱模式抛 ValueError，那会把「配置写错」升级成「程序起不来」。
        这里只做校验并返回 None，由 resolve_sandbox 负责 warning + 回落。
        """
        raw = self.config_manager.get("permissions.sandbox_default")
        if raw in (None, ""):
            return None
        value = str(raw).strip()
        if value not in cpermissions.SANDBOX_MODES:
            self.logger.warning(
                f"permissions.sandbox_default={value!r} 不是合法沙箱模式，已忽略；"
                f"可选: {sorted(cpermissions.SANDBOX_MODES)}"
            )
            return None
        return value

    def resolve_sandbox(self, mode: Any = None) -> str:
        """某个模式**实际生效**的沙箱模式（单点权威）。

        解析优先级（越具体越优先，对齐 DSH 的
        `explicit ?? fold(events) ?? default`）：

        1. modes.<name>.sandbox —— 该模式的显式配置，最具体；
        2. permissions.sandbox_default —— 全局缺省，语义上就是「默认值」，
           因此让更具体的模式级配置赢过它；
        3. 模式内置默认（Mode.workdir_policy.default_sandbox）。

        非法配置值一律 warning 后回落到下一级，**不抛异常**：配置写错不该
        让程序起不来（WorkdirPolicy.__post_init__ 对未知模式会抛 ValueError，
        所以未经校验的值绝不能塞进 Mode）。

        返回值同时被网关、available_modes 面板、会话投影与工具上下文使用，
        任何一处单独取内置默认值都会造成「网关按配置跑、面板显示内置值」
        的不一致，因此全部读取点必须走这个函数。

        Args:
            mode: 模式名、Mode 对象，或 None（当前模式）
        """
        resolved_mode = self._resolve_mode_object(mode)
        name = resolved_mode.name
        builtin = resolved_mode.workdir_policy.default_sandbox

        # 1) 模式级显式配置（最具体）
        node = self._mode_config_node(name)
        per_mode = node.get("sandbox") if isinstance(node, dict) else None
        if per_mode not in (None, ""):
            value = str(per_mode).strip()
            if value in cpermissions.SANDBOX_MODES:
                return value
            self.logger.warning(
                f"modes.{name}.sandbox={value!r} 不是合法沙箱模式，已忽略；"
                f"可选: {sorted(cpermissions.SANDBOX_MODES)}"
            )

        # 2) 全局缺省
        configured = self._config_sandbox_default()
        if configured:
            return configured

        # 3) 模式内置默认
        return builtin

    def _resolve_mode_object(self, mode: Any = None) -> "Mode":
        """把「模式名 / Mode 对象 / None」统一解析成注册表里的 Mode。

        注意不能直接走 _mode_object：它只接受模式名，收到 Mode 对象时
        resolve_mode_name 会判定非法并回退到 chat，导致 available_modes
        这类「已经在遍历 Mode」的调用方拿到 chat 的沙箱值。
        """
        if isinstance(mode, Mode):
            return self._mode_registry.get(mode.name)
        if mode is None:
            return self._mode_registry.get(self._mode_name)
        return self._mode_object(mode)

    def _mode_config_node(self, name: str) -> Dict[str, Any]:
        """config.yaml 里 modes.<name> 这一节（缺失或类型不对时返回空 dict）。"""
        modes_config = self.config_manager.get("modes") or {}
        if not isinstance(modes_config, dict):
            return {}
        node = modes_config.get(str(name))
        return node if isinstance(node, dict) else {}

    def _init_permissions(self) -> "cpermissions.PermissionGateway":
        """装配统一权限审批网关（castorice/permissions.py）。

        判定顺序：单调 guard → 未知工具 → 模式可见性 → 路径边界 →
        命令白名单 → read-only 沙箱 → 审批（ask / never / unavailable）。

        审批应答者默认**没有注入**：ask 会解析成 unavailable 并按拒绝关闭
        （fail-closed）。界面侧接上之后才能产生真正的 allow
        （见 AI.set_approval_responder）。
        """
        config = self.config_manager.get('permissions') or {}
        if not isinstance(config, dict):
            config = {}
        mode_command_allowlist = {
            mode.name: tuple(mode.workdir_policy.command_allowlist)
            for mode in self._mode_registry.all_modes()
        }
        gateway = cpermissions.PermissionGateway(
            config,
            tool_specs=self.tool_registry.specs(),
            mode=self._mode_name,
            roots=list(self._workdir_sources().get('roots') or ()),
            sandbox=self.resolve_sandbox(self._mode_name),
            mode_command_allowlist=mode_command_allowlist,
            visibility=lambda mode, tool: self._mode_registry.is_visible(mode, tool),
            logger=self.logger,
        )
        # 旧的只读白名单作为一条单调 guard：即使网关配置被写坏，
        # 旧路径的只读工具依然放行，而有副作用的工具依然要过审批。
        gateway.guard(self._readonly_guard)
        return gateway

    @staticmethod
    def _readonly_guard(request: PermissionRequest) -> Optional[str]:
        """单调 guard：只读工具永不因 guard 被拒（返回 None 表示放行）。"""
        return None

    def _placeholder_session(self) -> SessionStore:
        """为 HarnessLoop 提供的**初始会话**，随后被 _build_session 复用。

        为什么需要它：HarnessLoop 构造时必须已有一个可用的 SessionStore
        （要读写 header / revision / 检查点），但正式会话要等 set_mode()
        经 _activate_mode_session 到 _build_session 才确定。

        为什么不直接写进 _sessions：那样会让 set_mode 里
        name-not-in-self._sessions 的判定为假，从而**跳过首次激活**
        （进而跳过 _sync_permission_context，工具出口模式会停在 chat）。

        为什么不能创建后丢弃：同一个 session_id 被登记两次写者时，第二次
        会抛 SessionInUseError。旧实现正是这样每构造一个 AI 实例就永久泄漏
        一条 _ACTIVE_WRITERS 登记，并让同一 sessions_dir 下的**重建实例
        直接构造失败**。这里改为暂存在 _pending_session 上，由 _build_session
        在模式匹配时**复用**，因此每个实例只登记一次。
        """
        session_id = None
        try:
            session_id = SessionStore.latest_session_id(self.sessions_dir, self._mode_name)
        except OSError:
            session_id = None
        store = SessionStore(
            self.sessions_dir, self._mode_name, session_id=session_id,
            cwd=self._workdir_for_session(),
        )
        self._pending_session = store
        return store
    
    # agent 循环最大迭代次数（防止无限调用工具）
    MAX_ITERATIONS = 8

    # 注入系统提示词的工具使用说明
    TOOL_INSTRUCTIONS = (
        "\n\n【工具使用说明】\n"
        "你是一个具备行动能力的智能助手，可以调用以下工具完成真实操作：\n"
        "- get_current_time：查询当前日期和时间\n"
        "- web_search：联网搜索最新信息\n"
        "使用规则：\n"
        "1. 当问题需要实时信息或计算结果时，先调用工具，再根据工具返回的结果组织回答；\n"
        "2. 一次思考中可并行调用多个不相关的工具；\n"
        "3. 不要编造工具返回的内容，如实转述结果。"
    )

    # 注入系统提示词的时间说明（基础两条，所有模式共用）
    TIME_INSTRUCTIONS = (
        "\n\n【时间信息】\n"
        "1. 每条用户消息开头的【当前时间】就是这条消息发出时的真实本地日期与时间，"
        "回答「现在几点 / 今天几号 / 今天星期几」这类问题时以它为准，不要凭空猜测或说过时的时间；"
        "标注了时区时说明用户不在机器本地时区，跨时区问候（早安 / 晚安）以标注时区为准；\n"
        "2. 【相关记忆】里每条记忆都以 [日期 时间] 开头，表示那件事发生的时刻，"
        "可以据此判断事情的先后顺序与相隔多久。"
    )

    # 时间说明的补充条目：仅当该模式可见 get_current_time 时才拼接
    # （R19-1：Chat 模式无任何可见工具，system prompt 不得出现工具名字样）
    TIME_TOOL_INSTRUCTION = (
        "\n3. 需要更精确的时间时也可以调用 get_current_time 工具。"
    )

    # 表情包说明模板（{catalog} 处填入可用贴纸列表）
    # 注意：表情包由程序在回复末尾自动附带，模型只负责产出纯文本，
    # 因此这里只声明「不要自己写标记」，避免一条回复出现多个表情包。
    EMOJI_INSTRUCTIONS = (
        "\n\n【表情包规则】\n"
        "你的部分回复在界面上会由程序自动附带一张表情包图片来表达心情"
        "（并非每条都会发），你只需要专心写好文字内容，"
        "不要输出任何表情包标记或图片链接。\n"
        "供参考的可用表情包（名称（适用心情）），"
        "程序会依据你的文字情绪从中挑选，你无需也无法手动指定：\n"
        "{catalog}\n"
        "因此请注意：\n"
        "1. 不要在回复里写「[表情:xx]」这类标记，也不要写文件名或扩展名；\n"
        "2. 不要在回复里描述、解释、点评自己发了什么表情包；\n"
        "3. 文字依然保持纯文字，情绪用语气、措辞、断句来表达。"
    )

    def _init_emoji(self, image_dir: str) -> None:
        """初始化表情包目录与会话状态"""
        emoji_config = self.config_manager.get('emoji', {}) or {}

        self.emoji_catalog = EmojiCatalog(
            image_dir=image_dir,
            moods=emoji_config.get('moods') or {},
            keywords=emoji_config.get('keywords') or {},
        )
        self.emoji_session = EmojiSession(
            enabled=_to_bool(emoji_config.get('enabled'), True),
            probability=_to_float(emoji_config.get('probability'), 0.35),
            cooldown_turns=_to_int(emoji_config.get('cooldown_turns'), 2),
            max_per_session=_to_int(emoji_config.get('max_per_session'), 0),
        )

        if not self.emoji_catalog:
            self.logger.info(
                f"未在 {self.emoji_catalog.image_dir} 找到可用表情包"
                f"（表情包放在 Image/{EmojiCatalog.SUBDIR_NAME} 下，"
                f"文件名不能以 _ 开头）"
            )

    def _setup_provider(self) -> None:
        """设置LLM提供商（并把内核循环切到新 provider）

        既有 OpenAICompatibleProvider 与 LLMProviderFactory 的行为完全不变；
        这里额外按厂商能力表解析能力，并据此决定工具协议级别
        （native / json / text），供系统提示词注入与请求体裁剪使用。
        """
        provider_config = self.config_manager.get_provider_config(self._provider_name)
        self._provider = LLMProviderFactory.create_provider(
            self._provider_name,
            provider_config
        )
        # 能力适配层（castorice/providers）：只做「能力 -> 协议」的判定，
        # 不改动 provider 本身的调用方式，因此不产生行为回归。
        try:
            self._capabilities = cproviders.resolve_capabilities(
                self._provider_name, provider_config)
            self._tool_protocol = self._capabilities.protocol(tools_requested=True)
        except Exception as e:
            self._capabilities = None
            self._tool_protocol = cproviders.TOOL_PROTOCOL_NATIVE
            self.logger.warning(f"能力表解析失败，按 native 协议继续: {e}")
        harness = getattr(self, '_harness', None)
        if harness is not None:
            # provider 自持 model（见 OpenAICompatibleProvider._setup_client），
            # 因此不把 model 塞进请求体，避免两处来源互相覆盖。
            harness.provider = self._provider
    
    def _build_system_prompt(
        self,
        memory_context: Optional[str] = None,
        mode: Optional[str] = None,
        user_message: Optional[str] = None,
    ) -> str:
        """构建系统提示词：基础提示词 + 模式分层 + 工具说明 + 表情包规则

        记忆已改放到 user 消息中（见 get_response_stream），系统提示词保持稳定
        以节省 token。模式分层由 Mode.system_prompt_sections 决定；
        Chat 模式下没有可见工具，因此**不注入工具使用说明**。
        user_message 非空且触发词筛选开启时，基础提示词只注入常驻部分
        与命中条目（prompts.enabled，第二期 t2）；缺省保持全量拼接。
        """
        resolved_mode = self._mode_object(mode)
        prompt = self.prompt_manager.get_prompt(
            mode=resolved_mode, user_message=user_message)
        if self.emoji_catalog:
            prompt += self._build_emoji_instructions()
        # 用户档案小节（第三期 t4）：称呼 / 性别 / 生日 / 城市 / 时区。
        # castorice.profile 零第三方依赖且读失败返回空，这里不 try：
        # 注入失败 = 没有小节，而不是对话失败。
        prompt += cprofile.system_prompt_section()
        # 语气风格小节（第四期）：来自界面状态条的「风格」下拉。
        # 未选择 / 选「默认」时不注入，保持既有提示词不变。
        prompt += self._style_section()
        time_instructions = self.TIME_INSTRUCTIONS
        if "get_current_time" in self._mode_registry.visible_tool_names(
                resolved_mode):
            # 只有该模式真的可见 get_current_time，才注入第 3 条工具指引
            # （R19-1：Chat 的 system prompt 不得含 get_current_time 字样）
            time_instructions += self.TIME_TOOL_INSTRUCTION
        return (
            prompt
            + self._build_tool_instructions(resolved_mode)
            + self._build_tool_protocol_instructions(resolved_mode)
            + time_instructions
        )

    def _visible_tool_specs(self, mode: Optional["Mode"] = None) -> List[Dict[str, Any]]:
        """当前模式对模型可见的工具 schema（Chat 恒为空列表）。"""
        return self._mode_registry.to_openai_specs(self._mode_object(mode))

    def _build_tool_protocol_instructions(self, mode: Optional["Mode"] = None) -> str:
        """按厂商能力决定工具协议：native 无需额外文本，json 注入 JSON 约定。

        这是目标 7「分级降级」在提示词侧的落点：不支持 function calling 的
        模型改走 castorice.providers.json_protocol 的提示词内 JSON 约定；
        没有可见工具时不注入任何文本。
        """
        protocol = getattr(self, '_tool_protocol', cproviders.TOOL_PROTOCOL_NATIVE)
        if protocol != cproviders.TOOL_PROTOCOL_JSON:
            return ""
        specs = self._visible_tool_specs(mode)
        if not specs:
            return ""
        return cproviders.build_json_protocol_instructions(specs)

    def _build_tool_instructions(self, mode: Optional["Mode"] = None) -> str:
        """按模式可见工具生成工具使用说明；无可见工具时返回空串"""
        visible = self.mode_registry.visible_tool_names(self._mode_object(mode))
        if not visible:
            return ""
        if {"get_current_time", "web_search"} & set(visible):
            return self.TOOL_INSTRUCTIONS
        listed = "\n".join(f"- {name}" for name in visible)
        return (
            "\n\n【工具使用说明】\n"
            "你是一个具备行动能力的智能助手，本轮可以调用以下工具：\n"
            f"{listed}\n"
            "使用规则：\n"
            "1. 当问题需要实时信息或本地文件内容时，先调用工具，再根据结果组织回答；\n"
            "2. 一次思考中可并行调用多个不相关的工具；\n"
            "3. 不要编造工具返回的内容，如实转述结果；\n"
            "4. 没有出现在上面的工具就是本轮不可用的，不要尝试调用。"
        )

    def _mode_object(self, mode: Optional[str] = None) -> "Mode":
        """把模式名解析成注册表里的 Mode 对象（未知值回退到 chat）"""
        name = resolve_mode_name(
            mode if mode is not None else self._mode_name,
            self.mode_registry.names(),
        )
        return self.mode_registry.get(name)

    #: 语气风格 -> 提示词片段。键与界面状态条的「风格」下拉一一对应。
    STYLE_SECTIONS = {
        "温柔": "语气温柔耐心，多用缓和的表达，避免命令式措辞；即使对方急躁也保持稳定。",
        "简洁": "表达尽量精简，直给结论，不铺垫、不重复、不写多余的客套。",
        "活泼": "语气轻快俏皮，可以适度使用口语与语气词，让对话有活力但不吵闹。",
        "学术": "表达严谨，术语准确，必要时给出前提与依据；避免模糊措辞与夸张比喻。",
    }

    def _style_section(self) -> str:
        """当前语气风格对应的提示词片段（默认风格返回空串）。

        读取失败 / 未知风格一律返回空串：风格是锦上添花，绝不能因为
        ui_settings.json 损坏就让整轮对话失败。
        """
        try:
            style = str(self._ui_style() or "").strip()
        except Exception:
            return ""
        text = self.STYLE_SECTIONS.get(style)
        if not text:
            return ""
        return "\n\n【语气风格】\n" + text

    def _ui_style(self) -> str:
        """从 ui_settings 读当前风格（无 ui 模块时读 JSON 兜底）。"""
        getter = getattr(self, "_ui_settings_get", None)
        if callable(getter):
            try:
                return str((getter() or {}).get("style") or "")
            except Exception:
                return ""
        try:
            import json  # noqa: PLC0415
            from pathlib import Path  # noqa: PLC0415
            path = Path(__file__).resolve().parent / "ui_settings.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            return str((data or {}).get("style") or "")
        except Exception:
            return ""

    def _build_emoji_instructions(self) -> str:
        """按当前 Image 目录内容生成表情包规则说明"""
        catalog = "\n".join(f"- {item}" for item in self.emoji_catalog.labels())
        return self.EMOJI_INSTRUCTIONS.format(catalog=catalog)

    # 视觉模型识别特征：模型名命中任一即认为支持图片输入
    VISION_MODEL_RE = re.compile(
        r"(vision|multimodal|omni|gemini|llava|pixtral|internvl|"
        r"minicpm-v|glm-4v|step-1v|claude-[34]|gpt-4o|gpt-4-turbo|"
        r"(?:^|[-_/.])vl(?:[-_/.\d]|$))",
        re.IGNORECASE,
    )

    # 图片编码后最长边上限（像素），控制视觉 token 消耗
    IMAGE_MAX_SIDE = 1280

    def supports_vision(self, provider_name: Optional[str] = None) -> bool:
        """
        指定提供商是否支持图片输入（视觉模型）

        判断顺序：
        1. config.yaml 里该提供商显式配置的 vision 字段；
        2. 否则按模型名特征自动识别（如 *-vl、gpt-4o、claude-3 等）。

        Args:
            provider_name: 提供商名称，默认当前提供商

        Returns:
            是否支持图片输入
        """
        name = provider_name or self._provider_name
        try:
            config = self.config_manager.get_provider_config(name)
        except ValueError:
            return False
        
        explicit = config.get('vision')
        if explicit is not None:
            return _to_bool(explicit, False)
        return bool(self.VISION_MODEL_RE.search(str(config.get('model', ''))))

    @classmethod
    def encode_image_data_url(cls, path: str) -> str:
        """
        把本地图片编码成 data URL（供视觉模型识别）

        用 Pillow 统一转成 JPEG 并限制最长边，避免超大图占用过多 token；
        Pillow 不可用时退回原始字节。动图只取第一帧。

        Args:
            path: 图片文件路径

        Returns:
            形如 data:image/jpeg;base64,... 的字符串
        """
        mime = "image/png"
        try:
            from PIL import Image
        except ImportError:
            Image = None  # type: ignore[assignment]
        
        if Image is not None:
            with Image.open(path) as image:
                image = image.convert("RGB")
                if max(image.size) > cls.IMAGE_MAX_SIDE:
                    ratio = cls.IMAGE_MAX_SIDE / float(max(image.size))
                    resampling = getattr(Image, "Resampling", Image)
                    resample = getattr(resampling, "LANCZOS", 1)
                    image = image.resize(
                        (
                            max(1, int(image.width * ratio)),
                            max(1, int(image.height * ratio)),
                        ),
                        resample,
                    )
                buffer = io.BytesIO()
                image.save(buffer, format="JPEG", quality=88)
                data = buffer.getvalue()
            mime = "image/jpeg"
        else:
            data = Path(path).read_bytes()
            suffix = Path(path).suffix.lower()
            mime = {
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".webp": "image/webp",
                ".gif": "image/gif",
            }.get(suffix, "image/png")
        
        return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"

    def _build_user_message(
        self, text: str, images: Sequence[str]
    ) -> Dict[str, Any]:
        """
        构造 user 消息

        没有图片时用最简单的字符串 content（兼容性最好）；
        有图片时用 OpenAI 视觉格式的 content 数组。
        """
        if not images:
            return {'role': 'user', 'content': text}
        
        parts: List[Dict[str, Any]] = []
        if text:
            parts.append({'type': 'text', 'text': text})
        
        for path in images:
            try:
                parts.append({
                    'type': 'image_url',
                    'image_url': {'url': self.encode_image_data_url(path)},
                })
            except Exception as e:
                self.logger.warning(f"图片读取失败 {path}: {e}")
        
        if not parts:
            parts.append({'type': 'text', 'text': text or '（图片）'})
        return {'role': 'user', 'content': parts}

    #: 用户正文里的表情包标记，如 [表情:害羞]
    USER_STICKER_RE = re.compile(
        r"[\[【]\s*表情\s*[:：]\s*([^\]】\n]{1,24}?)\s*[\]】]"
    )

    def _extract_user_stickers(self, text: str) -> Tuple[str, List[str]]:
        """把用户正文里的 [表情:xx] 标记剥离，返回 (纯文本, 贴纸图片路径列表)。

        只返回**存在且后端真的扫到过**的贴纸路径：标记名不在 catalog 里时
        不产生图片（避免拿一个不存在的路径去 encode_image_data_url 报错）。
        视觉能力判定不在本函数做——调用方统一走 supports_vision()，
        不支持的模型只是拿到「对方发了 xx 表情」的文字描述。
        """
        if not text or not self.USER_STICKER_RE.search(text):
            return text, []

        names: List[str] = []

        def _take(match: "re.Match[str]") -> str:
            name = (match.group(1) or "").strip()
            if name:
                names.append(name)
            return ""

        plain = self.USER_STICKER_RE.sub(_take, text).strip()
        if not names:
            return plain, []

        paths: List[str] = []
        described: List[str] = []
        vision = False
        try:
            vision = bool(self.supports_vision())
        except Exception:
            vision = False

        for name in names:
            sticker = self.emoji_catalog.get(name) if self.emoji_catalog else None
            if sticker is not None and vision:
                paths.append(sticker.path)
            else:
                described.append(name)

        if described:
            # 没有视觉能力：用文字告诉模型对方发了什么表情（不改变角色设定）。
            hint = "（对方发来了表情：" + "、".join(described) + "）"
            plain = (plain + "\n" + hint).strip() if plain else hint
        return plain, paths

    def _pick_emoji_for_reply(self, reply_text: str) -> Optional[EmojiSticker]:
        """
        为一条回复挑选表情包（每条回复最多一个）

        模型若自己写了标记（不推荐，规则里已禁止），则不再额外附加，
        保证一条回复只会出现一个表情包。

        Args:
            reply_text: 本轮完整回复文本

        Returns:
            选中的贴纸；不需要发时返回 None
        """
        if not self.emoji_catalog or not self.emoji_session.should_append():
            return None
        if _EMOJI_OPT_OUT_RE.search(reply_text or ""):
            return None

        # 模型自己已经发过表情包标记 -> 不再追加第二个
        for raw_name in EMOJI_TAG_RE.findall(reply_text or ""):
            existing = self.emoji_catalog.get(raw_name)
            if existing is not None:
                return None

        plain = EMOJI_TAG_RE.sub("", reply_text or "")
        name = self.emoji_catalog.match_by_mood(plain)
        if not name:
            return None
        return self.emoji_catalog.get(name)

    def _strip_emoji_tags(self, text: str) -> str:
        """把表情包标记从文本中移除（存记忆/历史时用纯文本更干净）"""
        cleaned = EMOJI_TAG_RE.sub("", text or "")
        cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
        cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
        return cleaned.strip()

    def _reset_messages(
        self,
        memory_context: Optional[str] = None,
        mode: Optional[str] = None,
    ) -> None:
        """重置对话历史（只重建 system 头，不动会话事件日志）"""
        system_prompt = self._build_system_prompt(memory_context, mode=mode)
        self._messages = [
            {'role': 'system', 'content': system_prompt}
        ]
    
    def _format_memory_context(self, memories: List[str]) -> str:
        """
        格式化记忆上下文
        
        Args:
            memories: 记忆列表（每条已自带 [日期 时间] 前缀）
            
        Returns:
            格式化后的上下文字符串
        """
        if not memories:
            return ""
        
        return "\n".join([f"- {memory}" for memory in memories])
    
    @staticmethod
    def _format_now(moment: Optional[datetime] = None) -> str:
        """
        当前本地时间的文本表示（含星期）

        用户档案配置了时区（castorice/profile.json 的 timezone，IANA 名）
        时按该时区渲染并附注时区；未配置 / 时区名非法时退回机器本地时间
        （旧行为，字节级一致）。

        Args:
            moment: 指定时刻，默认取当前时间

        Returns:
            形如「2026-10-03 16:20:15（星期六）」，配置时区时形如
            「2026-10-03 16:20:15（星期六，时区 Asia/Shanghai）」
        """
        now = moment or datetime.now()
        tz_name = ""
        try:
            tz_name = cprofile.UserProfile.get("timezone").strip()
        except Exception:
            tz_name = ""
        tz_note = ""
        if tz_name:
            try:
                from zoneinfo import ZoneInfo  # noqa: PLC0415 - 按需加载
                now = now.astimezone(ZoneInfo(tz_name))
                tz_note = f"，时区 {tz_name}"
            except Exception:
                tz_note = f"，时区 {tz_name}（未能识别，按本地时间显示）"
        weekday = "星期" + "一二三四五六日"[now.weekday()]
        return f"{now.strftime('%Y-%m-%d %H:%M:%S')}（{weekday}{tz_note}）"
    
    def get_response_stream(
        self, user_input: str, images: Optional[List[str]] = None
    ):
        """
        流式 agent 循环（ReAct：思考 → 调工具 → 观察 → 继续）

        生成事件元组：
            ('text', 文本片段)          -> 流式文本
            ('tool', {'name':..., 'status':'start'|'done'})
                                       -> 工具调用开始/结束
            ('emoji', {'name':..., 'path':...})
                                       -> 额外发送一个表情包贴纸
            ('done', 完整回复)          -> 循环结束

        Args:
            user_input: 用户输入文本（可以只发图片，此时文本可为空）
            images: 随消息一起发送的本地图片路径列表（视觉模型才能识别）

        Yields:
            Tuple[str, Any]: 事件元组
        """
        images = [p for p in (images or []) if p and os.path.exists(p)]
        has_text = bool(user_input and user_input.strip())

        # 用户发了表情包（正文里的 [表情:xx] 标记，界面贴纸面板产出）：
        # 1) 若模型支持视觉输入，把贴纸文件当图片一起发过去，模型能真正看到；
        # 2) 否则把标记翻译成自然语言描述，模型至少知道对方发了哪个表情。
        #    两种情况下标记本身都要从正文里去掉——[表情:害羞] 直接喂给模型
        #    会让它复读这个方括号字符串。
        if has_text:
            user_input, sticker_paths = self._extract_user_stickers(user_input)
            user_input = user_input or ""
            has_text = bool(user_input.strip())
            if sticker_paths:
                images = list(images) + sticker_paths

        if not has_text and not images:
            yield ('text', "请输入有效的问题。")
            yield ('done', "请输入有效的问题。")
            return
        
        try:
            # 0. 本轮表情包回合计数（用于概率/冷却判定）
            self.emoji_session.mark_turn()
            
            # 1. 搜索相关记忆（只发图片时不检索）。
            #    用户档案配了城市时（castorice.profile.memory_query_suffix），
            #    查询串附带城市做地理定位——「明天天气怎么样」这类问题的
            #    检索与后续工具入参都能落在正确城市上。
            query = (user_input or "").strip()
            if query:
                query = query + cprofile.memory_query_suffix()
            memories = self.memory_manager.search_memories(query) if query else []
            
            # 2. 当前时间 + 记忆都放到 user 消息里，而不是系统提示词（节省 token）
            #    每轮都重新取时间，保证模型知道「现在」是什么时候
            time_prefix = f"【当前时间】{self._format_now()}\n\n"
            
            memory_prefix = ""
            if memories:
                memory_context = self._format_memory_context(memories)
                memory_prefix = f"【相关记忆】\n{memory_context}\n\n"
            
            user_message = time_prefix + memory_prefix + (user_input or "")
            # user 消息由 ai.py 组装（保留【当前时间】/【相关记忆】前缀的原有位置），
            # 内核负责记录事件、检查点、工具可见性裁决与逐轮状态。
            kernel_user_message = self._build_user_message(user_message, images)
            system_prompt = self._build_system_prompt(
                mode=self._mode_name, user_message=user_input)
            self._harness.set_system_prompt(system_prompt)
            # 本轮 system 头必须真正生效：seed_history 带来的旧 system 会压住
            # 内核的 _prepare_history 插入逻辑，因此在种子前先替换/补插 system 头
            # （与 _reset_messages 的语义一致；触发词注入依赖每轮按消息刷新）。
            if self._messages and self._messages[0].get('role') == 'system':
                self._messages[0]['content'] = system_prompt
            else:
                self._messages.insert(
                    0, {'role': 'system', 'content': system_prompt})
            if self.prompt_manager.dynamic_enabled and user_input:
                stats = getattr(self.prompt_manager, "last_selection", None)
                if stats:
                    self.logger.info(
                        f"触发词注入: 常驻={len(stats['resident'])}个模块 "
                        f"命中条目={len(stats['matched'])} "
                        f"本轮提示词 {stats['selected_bytes']}B / "
                        f"全量 {stats['full_bytes']}B")
            self._harness.seed_history(self._messages)
            full_reply = ""

            # 3. agent 循环（castorice.harness.HarnessLoop）
            #    内核产出的 ('done', ...) 不直接转发：表情包事件必须落在 done
            #    之前，而 done 必须恰好一次且在最后（红线 5.1 铁律 2）。
            # turn() 标记「当前在 harness 轮次内」：期间工具调用已由内核做过
            # 完整判定（含审批），执行出口不再重复判定，避免同一次调用弹两次
            # 审批、破坏 allowed-once 语义。直接调用（无此标记）会自己走
            # 完整判定——这是「所有有副作用的调用必须先过网关」的兜底。
            with self._kernel_tools.turn():
                for kind, payload in self._harness.run_stream(kernel_user_message):
                    if kind == 'text':
                        full_reply += payload
                        yield (kind, payload)
                    elif kind == 'done':
                        full_reply = payload
                    else:
                        yield (kind, payload)

            # 3.1 把内核维护的历史同步回 AI._messages（保持旧读法的语义）。
            #     内核保证了工具调用与结果成对（结果补全见
            #     HarnessLoop._complete_missing_results），但历史可能被外部
            #     改写（如切提供商时的 keep_history），所以这里再做一次自检：
            #     OpenAI 接口在存在孤立 tool_calls 时会直接返回 400，
            #     缺 tool_call_id 的工具消息必须被记下来而不是静默带出去。
            self._messages = self._harness.history()
            orphan_tool_messages = [
                message for message in self._messages
                if message.get('role') == 'tool' and not message.get('tool_call_id')
            ]
            if orphan_tool_messages:
                self.logger.warning(
                    f"历史里有 {len(orphan_tool_messages)} 条工具消息缺少 "
                    f"tool_call_id，下一轮请求可能被模型接口拒绝"
                )
            
            # 6. 表情包：每条回复固定附带一个（模型没自己发标记时由这里补上）
            sticker = self._pick_emoji_for_reply(full_reply)
            if sticker is not None:
                self.emoji_session.mark_sent()
                self.logger.info(f"附带表情包: {sticker.file_name}")
                yield ('emoji', {'name': sticker.name, 'path': sticker.path})
            
            # 7. 历史里的助手回复保持纯文本（去掉表情包标记）。
            #    内核已经把助手发言记进会话日志，这里只做「就地把最后一条
            #    助手消息改成纯文本」，避免同一条回复在历史里出现两次。
            plain_reply = self._strip_emoji_tags(full_reply)
            if self._messages and self._messages[-1].get('role') == 'assistant':
                self._messages[-1]['content'] = plain_reply
            else:
                self._messages.append({'role': 'assistant', 'content': plain_reply})
            
            # 8. 保存对话到记忆（只发图片时用占位文本）
            user_desc = query or f"[图片×{len(images)}]"
            memory_content = f"用户: {user_desc}\n助理: {plain_reply}"
            self.memory_manager.add_memory(memory_content)
            
            self.logger.info(
                f"用户: {user_desc[:50]}... -> AI: {plain_reply[:50]}... "
                f"(历史消息数: {len(self._messages)})"
            )
            
            yield ('done', full_reply)
            
        except Exception as e:
            error_msg = f"处理请求时出错: {str(e)}"
            self.logger.error(error_msg)
            yield ('text', f"抱歉，处理您的请求时出现了错误：{str(e)}")
            yield ('done', f"抱歉，处理您的请求时出现了错误：{str(e)}")
    
    def get_response(self, user_input: str) -> str:
        """
        同步获取回复（内部走 agent 循环，收集全部文本）

        Args:
            user_input: 用户输入

        Returns:
            完整回复文本
        """
        parts = []
        for kind, payload in self.get_response_stream(user_input):
            if kind == 'text':
                parts.append(payload)
        return ''.join(parts)
    
    def change_provider(
        self,
        provider_name: str,
        reload_config: bool = False,
        keep_history: bool = False
    ) -> None:
        """
        切换LLM提供商
        
        Args:
            provider_name: 提供商名称
            reload_config: 是否先重新读取 config.yaml（设置界面改过 API 后需要）
            keep_history: 是否保留当前对话历史（默认切提供商时清空）
        """
        if reload_config:
            self.config_manager.reload()
        
        self._provider_name = provider_name
        self._setup_provider()
        if not keep_history:
            self._reset_messages()
        self.emoji_session.reset()
        self.logger.info(f"已切换到提供商: {provider_name}")
    
    def clear_history(self) -> None:
        """清空对话历史（只清当前模式；其他模式的会话保持不变）"""
        self._reset_messages()
        if getattr(self, '_harness', None) is not None:
            self._harness.reset_history()
            self._harness.seed_history(self._messages)
        self.emoji_session.reset()
        self.logger.info("对话历史已清空")
    
    def reload_emojis(self, image_dir: Optional[str] = None) -> int:
        """
        重新扫描表情包目录（用户新增图片后无需重启即可识别）

        Args:
            image_dir: 可选，新的图片根目录；不传则沿用原目录

        Returns:
            识别到的表情包数量
        """
        if image_dir:
            self.emoji_catalog.set_root_dir(image_dir)
        else:
            self.emoji_catalog.reload()
        self._reset_messages()
        self.logger.info(
            f"表情包已重新载入: {self.emoji_catalog.count} 个 "
            f"@ {self.emoji_catalog.image_dir}"
        )
        return self.emoji_catalog.count
    
    def clear_memories(self) -> None:
        """清空所有记忆"""
        self.memory_manager.clear()
        self.logger.info("所有记忆已清空")

    def close(self) -> None:
        """释放本实例持有的会话（单写者登记）。

        为什么需要它：SessionStore 在构造时按会话文件路径登记单写者，
        `close()` 才解除登记。同一个 sessions_dir 下要再建一个 AI 实例
        （重建、测试连续构造、切换配置后重载）时，前一个实例必须先释放，
        否则新实例会在打开最近日志时撞上 SessionInUseError。

        幂等：可重复调用；也支持 `with AI(...) as bot:` 的写法。
        """
        released: List[str] = []
        for name, store in list(getattr(self, "_sessions", {}).items()):
            try:
                store.close()
                released.append(str(name))
            except Exception as e:  # pragma: no cover - 释放失败不该阻断退出
                self.logger.warning(f"释放会话 {name} 失败: {e}")
        pending = getattr(self, "_pending_session", None)
        if pending is not None and not getattr(pending, "closed", False):
            try:
                pending.close()
                released.append("pending")
            except Exception as e:  # pragma: no cover
                self.logger.warning(f"释放初始会话失败: {e}")
        self._pending_session = None
        if released:
            self.logger.info("已释放会话: " + ", ".join(released))

    def __enter__(self) -> "AI":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()
    
    def get_history(self) -> List[Dict[str, Any]]:
        """
        获取对话历史

        历史由 agent 内核维护（每条消息都对应会话日志里的事件），
        这里返回同一份历史的副本。

        Returns:
            对话历史列表
        """
        if getattr(self, '_harness', None) is not None:
            history = self._harness.history()
            if history:
                return [dict(message) for message in history]
        return self._messages.copy()
    
    def get_memory_count(self) -> int:
        """
        获取记忆数量
        
        Returns:
            记忆数量
        """
        return self.memory_manager.count
    
    @property
    def messages(self) -> List[Dict[str, Any]]:
        """获取对话历史（只读）"""
        if getattr(self, '_harness', None) is not None:
            history = self._harness.history()
            if history:
                return [dict(message) for message in history]
        return self._messages.copy()
    
    @property
    def provider_name(self) -> str:
        """获取当前提供商名称"""
        return self._provider_name

    # ========================================================================
    # 四模式内核对外接口（t3 新增）
    #
    # 变更约定：t4/t6/t7 只允许**增加**实现，不得修改这里的签名与事件语义
    # （见 docs/refactor/02-architecture.md 第 5 节「ai.py 的接缝」）。
    # ========================================================================

    @property
    def harness(self) -> "HarnessLoop":
        """agent 主循环（castorice.harness.HarnessLoop）"""
        return self._harness

    @property
    def harness_loop(self) -> "HarnessLoop":
        """harness 的别名（对外文档用的是 harness / harness_loop 两个叫法）"""
        return self._harness

    @property
    def mode_registry(self) -> "ModeRegistry":
        """模式注册表（工具可见性裁决与构造期校验都在这里）"""
        return self._mode_registry

    @property
    def modes(self) -> "ModeRegistry":
        """模式注册表的别名（供 bot.app.modes 这种读法）"""
        return self._mode_registry

    @property
    def session(self) -> "SessionStore":
        """当前模式的会话事件日志（按模式隔离）"""
        return self._session

    @property
    def sessions(self) -> Dict[str, "SessionStore"]:
        """已打开的会话（模式名 -> SessionStore），可检查各模式的恢复状态"""
        return dict(self._sessions)

    def session_for_mode(self, mode: str) -> "SessionStore":
        """取得某模式的会话（不存在则为其打开一个，供 t7 的会话隔离检查）"""
        name = resolve_mode_name(mode, self._mode_registry.names())
        if name not in self._sessions:
            self._build_session(name)
        return self._sessions[name]

    @property
    def projection(self) -> "SessionProjection":
        """当前模式的前端投影（UI 只读快照，不重放事件日志）"""
        return self._session_projection

    @property
    def app(self):
        """组合根视图。

        t3 的内置组合根就是 AI 自身（一个类装齐 provider / 会话 / 模式 / 权限 /
        harness）；t10 会把装配抽到 castorice/app.py 的 build_app()，
        届时这里返回那个 App 对象，UI 侧读法（bot.app.mode / bot.app.session /
        bot.app.projection）保持不变。
        """
        return self

    @property
    def mode(self) -> str:
        """当前模式名（chat | work | code | learn）"""
        return self._mode_name

    @property
    def mode_display_name(self) -> str:
        """当前模式的中文名（界面用）"""
        return self._mode_object().display_name

    def available_modes(self) -> List[Dict[str, Any]]:
        """所有模式的可展示元数据（t7 的模式切换条直接消费）"""
        return [
            {
                'name': mode.name,
                'display_name': mode.display_name,
                'tool_allowlist': list(mode.tool_allowlist),
                'visible_tools': self._mode_registry.visible_tool_names(mode),
                'system_prompt_sections': list(mode.system_prompt_sections),
                'workdir_policy': {
                    'bindable': mode.workdir_policy.bindable,
                    # 两个字段都走单点权威解析：default_sandbox 表示「实际
                    # 生效值」（配置优先），builtin_sandbox 保留模式的出厂值，
                    # 面板需要区分「配置覆盖了」与「用的是内置默认」时读它。
                    'default_sandbox': self.resolve_sandbox(mode),
                    'builtin_sandbox': mode.workdir_policy.default_sandbox,
                    'command_allowlist': list(mode.workdir_policy.command_allowlist),
                },
                'workdir': self._mode_registry.workdir(mode),
                'sandbox': self.resolve_sandbox(mode),
                'bindable': mode.workdir_policy.bindable,
                'supports_vision_input': mode.supports_vision_input,
                'active': mode.name == self._mode_name,
            }
            for mode in self._mode_registry.all_modes()
        ]

    def set_mode(
        self,
        mode: str,
        *,
        persist: bool = True,
        activate: bool = True,
    ) -> None:
        """切换模式。

        * 未知模式名回退到 chat（失败安全：宁可少给工具，不可多给工具）；
        * persist 为真时写回 config.yaml 的 mode 键（键不存在时
          update_yaml_scalars 会静默跳过，不影响切换本身）；
        * 切换会**激活该模式自己的会话**，因此四个模式的对话历史相互隔离；
        * 模式切换以「追加一条带来源的消息」告知模型，**不改写系统提示词的
          基础部分**（对齐 P19，保持 KV 前缀稳定）。

        Args:
            mode: 目标模式名
            persist: 是否写回 config.yaml
            activate: 是否激活目标模式的会话（False 只改名字，内部用）
        """
        name = resolve_mode_name(mode, self._mode_registry.names())
        if name != mode:
            self.logger.info(f"未知模式 {mode!r}，回退到 {name}")
        changed = name != self._mode_name or name not in self._sessions
        if persist and name != self._mode_name:
            try:
                self.config_manager.set_values({('mode',): name})
            except Exception as e:
                # 键不存在 / 配置只读都不应打断切换
                self.logger.warning(f"模式未写入 config.yaml: {e}")
        self._mode_name = name
        if activate and changed:
            self._activate_mode_session(name)
            self.emoji_session.reset()
            self.logger.info(f"已切换到模式: {name}")

    def switch_session_mode(self, mode: str, *, persist: bool = True) -> None:
        """显式切换模式的别名（语义与 set_mode 相同，便于界面侧读代码）"""
        self.set_mode(mode, persist=persist, activate=True)

    def bind_mode_workdir(self, mode: str, path: Optional[str] = None) -> str:
        """为某个模式绑定根目录（Code=可信代码目录，Learn=Obsidian Vault）。

        Args:
            mode: 模式名
            path: 根目录；留空表示解绑

        Returns:
            规范化后的根目录（解绑时为空串）

        Raises:
            WorkdirError: 模式不可绑定，或路径越界。异常对象的 to_dict()
                给出结构化错误（code / message / path / root）。
        """
        name = resolve_mode_name(mode, self._mode_registry.names())
        normalized = self._mode_registry.bind_workdir(name, path)
        if name == self._mode_name:
            self._refresh_mode_session_context()
        self.logger.info(f"模式 {name} 的工作目录: {normalized or '（未绑定）'}")
        return normalized

    def workdir(self, mode: Optional[str] = None) -> Optional[str]:
        """某模式当前绑定的工作目录（默认当前模式）"""
        return self._mode_registry.workdir(self._mode_object(mode))

    def tool_visible(self, name: str, mode: Optional[str] = None) -> bool:
        """某个工具在当前（或指定）模式下是否对模型可见（t8 的检查入口）"""
        return self._mode_registry.is_visible(self._mode_object(mode), name)

    def visible_tools(self, mode: Optional[str] = None) -> List[str]:
        """当前（或指定）模式下可见的工具名列表；Chat 恒为空列表"""
        return self._mode_registry.visible_tool_names(self._mode_object(mode))

    @property
    def registered_tools(self) -> List["ToolSpec"]:
        """内核已注册的工具描述符"""
        return self._mode_registry.all_tools()

    @property
    def last_stop_reason(self) -> Optional["StopReason"]:
        """上一轮的显式终止原因（对齐 P15；未跑过任何一轮时为 None）"""
        return self._harness.stop_reason

    @property
    def last_outcome(self):
        """上一轮的结果对象（stop_reason / steps / error / used_tools）"""
        return self._harness.outcome()

    # ---- 内部：会话与模式装配 ----

    def _build_session(self, name: str) -> "SessionStore":
        """为某个模式打开会话（优先复用初始会话，其次恢复磁盘上最近的一会话）"""
        pending = getattr(self, "_pending_session", None)
        if (pending is not None
                and not getattr(pending, "closed", False)
                and getattr(pending, "mode", None) == name):
            # 复用 _init_harness 建好的初始会话：避免同一 session_id 被登记
            # 两次写者（旧实现在这里泄漏登记并让重建实例直接失败）。
            self._pending_session = None
            self._sessions[name] = pending
            return pending
        session_id = None
        try:
            session_id = SessionStore.latest_session_id(self.sessions_dir, name)
        except OSError:
            session_id = None
        store = SessionStore(
            self.sessions_dir, name, session_id=session_id, cwd=self._workdir_for_session()
        )
        self._sessions[name] = store
        return store

    # ---- 会话管理（第四期：侧栏点击切换 / 删除） ----

    def switch_to_session(self, session_id: str) -> None:
        """切到当前模式下指定的历史会话。

        语义与 set_mode 的会话激活一致：先 flush 并 close 旧会话（否则新
        会话文件会撞单写者登记），再从磁盘恢复历史进内核。
        找不到该会话时抛 ValueError——界面据此提示，而不是静默不动。
        """
        name = self._mode_name
        target = str(session_id or "").strip()
        if not target:
            raise ValueError("session_id 不能为空")

        path = self.sessions_dir / name / (target + ".jsonl")
        if not path.is_file():
            raise ValueError(
                "会话不存在: " + target + "（模式 " + name + "）")

        current = self._sessions.get(name)
        if current is not None and getattr(current, "session_id", "") == target:
            return  # 已在该会话，幂等
        if current is not None:
            if getattr(current, "dirty", False):
                current.flush("切换会话")
            current.close()
            self._sessions.pop(name, None)

        store = SessionStore(
            self.sessions_dir, name, session_id=target,
            cwd=self._workdir_for_session(),
        )
        self._sessions[name] = store
        self._session = store
        self._session_projection = SessionProjection(store)
        self._harness.session = store
        self._harness.projection = self._session_projection
        self._harness.set_mode(name)
        self._sync_permission_context(name)

        restored = store.derive_messages()
        if restored:
            self._messages = restored
            if not any(m.get('role') == 'system' for m in self._messages):
                self._messages.insert(0, {
                    'role': 'system',
                    'content': self._build_system_prompt(),
                })
        else:
            self._reset_messages()
        self._harness.seed_history(self._messages)
        self.emoji_session.reset()
        self.logger.info(f"已切换会话: {target}")

    def delete_session(self, session_id: str) -> bool:
        """删除当前模式下的一个历史会话文件。

        正在使用的会话**拒绝删除**（它持有单写者登记，删了会让内核状态
        与磁盘不一致）：调用方先切到别的会话再删。
        返回 True 表示已删除，False 表示文件不存在（幂等）。
        """
        name = self._mode_name
        target = str(session_id or "").strip()
        if not target:
            raise ValueError("session_id 不能为空")

        current = self._sessions.get(name)
        if current is not None and getattr(current, "session_id", "") == target:
            raise ValueError(
                "不能删除正在使用的会话，请先切换到其它会话")

        path = self.sessions_dir / name / (target + ".jsonl")
        if not path.is_file():
            return False
        path.unlink()
        self.logger.info(f"已删除会话: {target}")
        return True

    def create_session(self) -> str:
        """在当前模式下新开一个会话（关闭旧的，返回新会话 id）。

        旧会话**必须落盘**：即便它还是空的，也要在磁盘上留一个文件，
        否则它会从历史列表里消失（全新会话没有文件 → sessions.list 扫不到），
        用户会觉得「刚建的对话不见了」。
        """
        name = self._mode_name
        current = self._sessions.get(name)
        if current is not None:
            if getattr(current, "dirty", False):
                current.flush("新建会话")
            else:
                # 空会话也写一次，保证它作为「空对话」出现在历史里
                try:
                    current.flush("新建会话（空会话占位）")
                except Exception as e:  # pragma: no cover - 落盘失败不阻断新建
                    self.logger.warning(f"旧会话落盘失败: {e}")
            current.close()
            self._sessions.pop(name, None)

        store = SessionStore(
            self.sessions_dir, name, cwd=self._workdir_for_session())
        self._sessions[name] = store
        self._session = store
        self._session_projection = SessionProjection(store)
        self._harness.session = store
        self._harness.projection = self._session_projection
        self._harness.set_mode(name)
        self._sync_permission_context(name)
        self._reset_messages()
        self._harness.seed_history(self._messages)
        self.emoji_session.reset()
        self.logger.info(f"已新建会话: {store.session_id}")
        return store.session_id

    def _workdir_for_session(self) -> Optional[str]:
        """会话不可变的工作目录（对齐 DSH 的 SessionHeader.cwd）"""
        return os.getcwd()

    def _activate_mode_session(self, name: str) -> None:
        """把内核切换到某个模式的会话，并把历史恢复进 self._messages"""
        store = self._sessions.get(name) or self._build_session(name)
        self._session = store
        self._session_projection = SessionProjection(store)
        self._harness.session = store
        self._harness.projection = self._session_projection
        self._harness.set_mode(name)
        self._session_projection.update(
            sandbox_mode=self.resolve_sandbox(name),
            notify=False,
        )

        # 权限网关 / 工具上下文跟随模式切换：沙箱与工作区都来自该模式
        self._sync_permission_context(name)

        restored = store.derive_messages()
        if restored:
            self._messages = restored
            if not any(m.get('role') == 'system' for m in self._messages):
                self._messages.insert(0, {
                    'role': 'system',
                    'content': self._build_system_prompt(),
                })
        else:
            self._reset_messages()
        self._harness.seed_history(self._messages)

    def _sync_permission_context(self, name: Optional[str] = None) -> None:
        """把权限网关与工具上下文切到某个模式的沙箱 / 工作区。

        失败安全：任何异常都不改变「默认 read-only」这一结论，
        因此最坏情况是工具被拒绝，而不是被放行。
        """
        mode_name = name or self._mode_name
        mode = self._mode_registry.get(mode_name)
        workdir = self._mode_registry.workdir(mode) or self._tool_context.workspace
        sandbox = self.resolve_sandbox(mode_name)
        gateway = getattr(self, '_permissions', None)
        if gateway is not None:
            try:
                gateway.configure(mode=mode_name, workdir=workdir, sandbox=sandbox)
            except Exception as e:
                self.logger.warning(f"权限上下文更新失败，维持原沙箱设置: {e}")
        # 工具执行出口的模式必须一起跟随：ToolRuntime.call 用 self.mode 构造
        # 二次复查请求，漏掉这一步会让 Work/Code/Learn 的工具全部被按 chat
        # 的可见性规则拒掉（"工具 read_file 在模式 chat 下不可见"）。
        kernel_tools = getattr(self, '_kernel_tools', None)
        if kernel_tools is not None:
            try:
                kernel_tools.set_mode(mode_name)
            except Exception as e:
                self.logger.warning(f"工具出口模式更新失败，维持原模式: {e}")
        # 前端投影也必须同步：它是 UI 判断「当前沙箱」的唯一来源，
        # 漏掉这一步会让配置改完之后面板仍显示旧值（网关按新配置跑、
        # 面板显示旧值的更糟不一致）。
        projection = getattr(self, '_session_projection', None)
        if projection is not None:
            try:
                projection.update(sandbox_mode=sandbox, notify=False)
            except Exception as e:
                self.logger.warning(f"会话投影更新失败，维持原沙箱展示: {e}")
        context = getattr(self, '_tool_context', None)
        if context is not None:
            context.workspace = str(workdir or "")
            context.mode = mode_name
            context.sandbox = sandbox
            context.roots = tuple(self._workdir_sources().get('roots') or ())
            context.session_id = getattr(self._harness.session, 'session_id', '')
            context.gateway = gateway
            context.policy = (
                gateway.path_policy if gateway is not None
                else cpermissions.PathPolicy(
                    roots=context.roots,
                    workspace=context.workspace or None,
                    sandbox=sandbox,
                )
            )

    # ---- 权限网关的对外接口（t7 的审批窗口消费） ----

    @property
    def tools_mode(self) -> str:
        """内核工具执行出口当前使用的模式（只读，供界面检查与测试断言）。

        正常情况下它与 AI.mode、harness.mode_name、permissions.mode 一致；
        四者不一致就意味着工具会在执行出口被按错误模式拒掉。
        """
        kernel_tools = getattr(self, '_kernel_tools', None)
        if kernel_tools is None:
            return self._mode_name
        return str(getattr(kernel_tools, 'mode', self._mode_name))

    @property
    def permissions(self) -> "cpermissions.PermissionGateway":
        """统一权限审批网关（判定 allow | ask | deny | unavailable）。"""
        return self._permissions

    def permission_check(
        self,
        tool: str,
        arguments: Optional[Dict[str, Any]] = None,
        *,
        mode: Optional[str] = None,
    ) -> str:
        """单次判定某个工具在当前（或指定）模式下的结论词（供界面预检）。"""
        return self._permissions.check(tool, arguments, mode=mode or self._mode_name)

    def permission_decision(
        self,
        tool: str,
        arguments: Optional[Dict[str, Any]] = None,
        *,
        mode: Optional[str] = None,
    ) -> "PermissionDecision":
        """单次判定的完整结果（含 reason 与结构化 info.code）。"""
        return self._permissions.decide(tool, arguments, mode=mode or self._mode_name)

    def set_approval_responder(self, callback: Optional[Callable[..., Any]]) -> None:
        """注入审批应答者。

        callback(request, request_id) 可以同步返回 allow/deny，也可以先调用
        broker.defer(request_id) 把请求投递到主线程、再返回 None
        （worker 侧阻塞等待，超时即按拒绝关闭）。
        """
        self._permissions.set_ask_callback(callback)

    @property
    def approval_broker(self) -> "cpermissions.ApprovalBroker":
        return self._permissions.broker

    def permission_audit(self) -> List[Dict[str, Any]]:
        """审批审计事件（只读，UI 可展示；不进模型可见文本）。"""
        return [entry.to_dict() for entry in self._permissions.audit_log()]

    # ---- 厂商能力（目标 7） ----

    @property
    def capabilities(self) -> Optional["cproviders.ModelCapabilities"]:
        """当前提供商的模型能力（未知厂商回落 custom 默认值）。"""
        return getattr(self, '_capabilities', None)

    @property
    def tool_protocol(self) -> str:
        """当前生效的工具协议级别：native | json | text。"""
        return getattr(self, '_tool_protocol', cproviders.TOOL_PROTOCOL_NATIVE)

    def provider_capabilities(
        self, provider_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """某个提供商的能力摘要（供设置界面展示与诊断）。"""
        name = provider_name or self._provider_name
        try:
            config = self.config_manager.get_provider_config(name)
        except ValueError:
            config = {}
        caps = cproviders.resolve_capabilities(name, config)
        payload = caps.to_dict()
        payload.update({
            'provider': name,
            'protocol': caps.protocol(tools_requested=True),
            'vendor': cproviders.vendor_key(name),
        })
        return payload

    def _refresh_mode_session_context(self) -> None:
        """工作目录变化后，刷新内核记录的工作目录并重新告知模型（P19）"""
        store = self._harness.session
        header = store.header
        self._harness.announced_reset()
        store.header = type(header)(
            session_id=header.session_id,
            mode=header.mode,
            cwd=self._mode_registry.workdir(self._mode_object()) or self._workdir_for_session(),
            created_at=header.created_at,
            format_version=header.format_version,
        )
        # 权限网关与工具上下文同步到新的工作目录（越界绑定已在 bind_mode_workdir 拒绝）
        self._sync_permission_context()


# ============================================================================
# 工厂函数
# ============================================================================

class _KernelTools:
    """把 ai.ToolRegistry 适配成内核需要的「schema + 执行入口」对象。

    内核只认识两件事：all_tools() -> [ToolSpec]（决定并行安全与结果上限）
    与 call(name, arguments) -> str（真正执行）。这里做一次适配，让 castorice/
    不需要认识 ai.Tool（红线 D：内核不得 import ai）。
    """

    def __init__(self, registry: "ToolRegistry", specs: Sequence["ToolSpec"],
                 gateway: Optional["cpermissions.PermissionGateway"] = None,
                 mode: str = "chat") -> None:
        self._registry = registry
        self._specs = list(specs)
        # 真正执行走 castorice.tools.ToolRuntime：它在调用 handler 之前再走
        # 一次**策略维度**的权限复查（deny/unavailable 一律拦下，ask 交给
        # harness 的完整判定），因此任何执行路径都无法跳过网关。
        #: harness 轮次内标记：harness 在派发前已经做过完整判定（含审批），
        #: 标记为真时出口不再重复走一遍完整判定（否则同一次调用会弹两次审批，
        #: 破坏 allowed-once 语义）；标记为假（直接调用）时出口自己走完整判定。
        self._turn_lock = threading.Lock()
        self._turn_depth = 0
        self._runtime = ctools.ToolRuntime(
            registry._registry, gateway, mode=mode, enforce=gateway is not None,
            already_authorized=self._in_harness_turn)

    def all_tools(self) -> List["ToolSpec"]:
        return list(self._specs)

    @property
    def mode(self) -> str:
        """执行出口当前按哪个模式复查权限（转发 ToolRuntime.mode）。

        这是「工具执行出口的模式」唯一可信来源：它必须与 AI.mode、
        harness.mode_name、gateway.mode 保持一致，否则内核工具会在执行出口
        被按错误模式的可见性规则拒掉。
        """
        return self._runtime.mode

    def set_mode(self, mode: str) -> None:
        self._runtime.set_mode(mode)

    # ---- harness 轮次标记 ----

    @contextlib.contextmanager
    def turn(self):
        """标记「当前处于 harness 轮次内」：期间的工具调用已由内核判定过。"""
        with self._turn_lock:
            self._turn_depth += 1
        try:
            yield
        finally:
            with self._turn_lock:
                self._turn_depth = max(0, self._turn_depth - 1)

    def _in_harness_turn(self) -> bool:
        with self._turn_lock:
            return self._turn_depth > 0

    def call(self, name: str, arguments: Dict[str, Any]) -> str:
        return self._runtime.call(name, dict(arguments or {}))


class AIFactory:
    """
    AI工厂类
    
    提供创建和配置AI实例的便捷方法。
    """
    
    @staticmethod
    def create_default_bot(provider_name: Optional[str] = None) -> AI:
        """
        创建默认配置的AI
        
        Args:
            provider_name: 指定提供商（可选）
            
        Returns:
            配置好的AI实例
        """
        return AI(provider_name=provider_name)
    
    @staticmethod
    def create_with_custom_config(
        config_path: str = "config.yaml",
        prompts_dir: str = "prompts",
        chroma_dir: str = "database",
        provider_name: Optional[str] = None
    ) -> AI:
        return AI(config_path, prompts_dir, chroma_dir, provider_name=provider_name)


# ============================================================================
# 向后兼容接口
# ============================================================================

# 全局实例（单例模式）
_bot_instance: Optional[AI] = None


def get_ai(provider_name: Optional[str] = None) -> AI:
    """
    获取AI实例（单例）
    
    Args:
        provider_name: 指定提供商（可选）
        
    Returns:
        AI实例
    """
    global _bot_instance
    if _bot_instance is None:
        _bot_instance = AI(provider_name=provider_name)
    elif provider_name and _bot_instance.provider_name != provider_name:
        # 如果指定了不同的提供商，切换
        _bot_instance.change_provider(provider_name)
    return _bot_instance


def get_ai_response(user_input: str, provider_name: Optional[str] = None) -> str:
    """
    获取AI响应（函数式调用，兼容原有代码）
    
    Args:
        user_input: 用户输入
        provider_name: 指定提供商（可选）
        
    Returns:
        AI响应
    """
    bot = get_ai(provider_name)
    return bot.get_response(user_input)


# ============================================================================
# 命令行入口
# ============================================================================

def main() -> None:
    """
    命令行交互入口
    
    用于测试AI功能。
    """
    print("=" * 50)
    print("CastoriceAgent 测试终端")
    print("命令:")
    print("  exit - 退出")
    print("  clear - 清空对话历史")
    print("  clear_memories - 清空所有记忆")
    print("  status - 显示状态")
    print("  provider <name> - 切换提供商 (如: provider deepseek)")
    print("  list_providers - 列出所有可用提供商")
    print("=" * 50)
    
    bot = AI()
    
    # 获取可用提供商列表
    available_providers = bot.config_manager.get('providers', {})
    
    while True:
        try:
            user_input = input("\n你: ").strip()
            
            if not user_input:
                continue
            
            # 处理命令
            if user_input.lower() in ['exit', 'quit', 'q']:
                print("再见！")
                break
            elif user_input.lower() == 'clear':
                bot.clear_history()
                print("对话历史已清空")
                continue
            elif user_input.lower() == 'clear_memories':
                bot.clear_memories()
                print("所有记忆已清空")
                continue
            elif user_input.lower() == 'status':
                print(f"当前提供商: {bot.provider_name}")
                print(f"记忆数量: {bot.get_memory_count()}")
                print(f"对话消息数: {len(bot.get_history())}")
                continue
            elif user_input.lower() == 'list_providers':
                print("可用提供商:")
                for name in available_providers.keys():
                    print(f"  - {name}")
                continue
            elif user_input.lower().startswith('provider '):
                provider_name = user_input.split(' ', 1)[1].strip()
                if provider_name in available_providers:
                    bot.change_provider(provider_name)
                    print(f"已切换到提供商: {provider_name}")
                else:
                    print(f"未找到提供商: {provider_name}")
                    print(f"可用提供商: {', '.join(available_providers.keys())}")
                continue
            
            response = bot.get_response(user_input)
            print(f"\n助理: {response}")
            
        except KeyboardInterrupt:
            print("\n\n再见！")
            break
        except Exception as e:
            print(f"\n错误: {e}")


if __name__ == "__main__":
    main()