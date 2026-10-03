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

class PromptManager:
    """
    提示词管理器
    
    负责加载和管理AI提示词模板。
    """
    
    DEFAULT_PROMPTS_DIR = "prompts"
    
    def __init__(self, prompts_dir: str = DEFAULT_PROMPTS_DIR):

        self.prompts_dir = Path(prompts_dir)
        self._prompt: Optional[str] = None
        self._load_prompts()
    
    def _load_prompts(self) -> None:
        """加载所有提示词文件"""
        if not self.prompts_dir.exists():
            self._prompt = ""
            return
        
        prompt_parts = []
        
        for filepath in sorted(self.prompts_dir.iterdir()):
            if filepath.suffix.lower() in ['.md', '.txt']:
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        content = f.read().strip()
                        if content:
                            prompt_parts.append(content)
                except Exception as e:
                    print(f"警告: 无法读取提示词文件 {filepath}: {e}")
        
        self._prompt = "\n\n".join(prompt_parts)
    
    def get_prompt(self, memory_context: Optional[str] = None) -> str:

        prompt = self._prompt or ""
        
        if memory_context:
            prompt += f"\n\n【联想到的相关记忆】：\n{memory_context}"
        
        return prompt
    
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
    工具定义

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


class ToolRegistry:
    """
    工具注册表：注册、描述、调用工具
    """

    def __init__(self) -> None:
        self._tools: Dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """注册一个工具"""
        self._tools[tool.name] = tool

    def to_openai_specs(self) -> List[Dict[str, Any]]:
        """生成 OpenAI function calling 格式的工具列表"""
        return [
            {
                'type': 'function',
                'function': {
                    'name': t.name,
                    'description': t.description,
                    'parameters': t.parameters,
                }
            }
            for t in self._tools.values()
        ]

    def call(self, name: str, arguments: Dict[str, Any]) -> str:
        """
        调用工具并返回结果文本

        Args:
            name: 工具名称
            arguments: 工具参数

        Returns:
            工具执行结果（字符串）；失败时返回错误信息
        """
        tool = self._tools.get(name)
        if tool is None:
            return f"错误: 未知工具 {name}，可用工具: {', '.join(self._tools)}"
        try:
            result = tool.function(**arguments)
            return str(result)
        except Exception as e:
            return f"错误: 调用工具 {name} 失败: {e}"


# ----------------------------------------------------------------------------
# 内置工具实现
# ----------------------------------------------------------------------------

def tool_get_current_time() -> str:
    """查询当前日期和时间"""
    now = datetime.now()
    return now.strftime("当前本地时间：%Y-%m-%d %H:%M:%S（%A）")


# ----------------------------------------------------------------------------
# 搜索系统（多后端、配置驱动、自动降级）
# ----------------------------------------------------------------------------

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0 Safari/537.36"
)


def _html_to_text(html: str, max_len: int = 500) -> str:
    """把 HTML 粗略提取为纯文本（去掉脚本/样式/标签）"""
    text = re.sub(r"<script.*?</script>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_module.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_len]


class SearchBackend:
    """搜索后端基类"""
    name = "base"

    def __init__(self, api_key: str = "", base_url: str = ""):
        self.api_key = api_key
        self.base_url = base_url

    def search(self, query: str, max_results: int) -> List[Dict[str, str]]:
        raise NotImplementedError("子类必须实现 search 方法")


class BingHTMLSearch(SearchBackend):
    """Bing 网页搜索（HTML 解析，无需 key）"""
    name = "bing_html"

    @staticmethod
    def parse_html(html: str, max_results: int) -> List[Dict[str, str]]:
        results = []
        # 每个结果块: <li class="b_algo"> <h2><a href="URL">标题</a></h2> <p>摘要</p>
        for block in re.findall(r'<li class="b_algo".*?</li>', html, re.S):
            m = re.search(
                r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
                block, re.S
            )
            if not m:
                continue
            url = m.group(1)
            title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
            sm = re.search(r'<p[^>]*>(.*?)</p>', block, re.S)
            snippet = re.sub(r"<[^>]+>", "", sm.group(1)).strip() if sm else ""
            results.append({
                "title": title, "url": url, "snippet": snippet,
            })
            if len(results) >= max_results:
                break
        return results

    def search(self, query: str, max_results: int) -> List[Dict[str, str]]:
        import requests
        resp = requests.get(
            "https://www.bing.com/search",
            params={
                "q": query, "setlang": "zh-hans",
                "cc": "CN", "count": max_results,
            },
            headers={"User-Agent": _UA},
            timeout=10,
        )
        resp.raise_for_status()
        return self.parse_html(resp.text, max_results)


class DDGHTMLSearch(SearchBackend):
    """DuckDuckGo 网页搜索（HTML 解析，无需 key，作为兜底）"""
    name = "ddg_html"

    @staticmethod
    def parse_html(html: str, max_results: int) -> List[Dict[str, str]]:
        results = []
        pattern = re.compile(
            r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            re.S
        )
        for m in pattern.finditer(html):
            link = m.group(1)
            title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
            # DuckDuckGo 的结果链接是重定向，提取真实地址
            parsed = urlparse(html_module.unescape(link))
            real_url = link
            if parsed.netloc == "duckduckgo.com" and parsed.path.startswith("/l/"):
                uddg = parse_qs(parsed.query).get("uddg")
                if uddg:
                    real_url = unquote(uddg[0])
            elif link.startswith("//"):
                real_url = "https:" + link
            results.append({"title": title, "url": real_url, "snippet": ""})
            if len(results) >= max_results:
                break
        return results

    def search(self, query: str, max_results: int) -> List[Dict[str, str]]:
        import requests
        resp = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers={"User-Agent": _UA},
            timeout=10,
        )
        resp.raise_for_status()
        return self.parse_html(resp.text, max_results)


class TavilySearch(SearchBackend):
    """Tavily（专为 LLM 设计的搜索 API，直接返回正文摘要）"""
    name = "tavily"

    def search(self, query: str, max_results: int) -> List[Dict[str, str]]:
        import requests
        if not self.api_key:
            raise RuntimeError("未配置 Tavily api_key")
        resp = requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key": self.api_key,
                "query": query,
                "max_results": max_results,
                "search_depth": "basic",
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        return [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": (r.get("content") or "")[:300],
            }
            for r in data.get("results", [])
        ]


class SerperSearch(SearchBackend):
    """Serper.dev（Google 搜索结果 API）"""
    name = "serper"

    def search(self, query: str, max_results: int) -> List[Dict[str, str]]:
        import requests
        if not self.api_key:
            raise RuntimeError("未配置 Serper api_key")
        resp = requests.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": self.api_key, "Content-Type": "application/json"},
            json={"q": query, "num": max_results},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        return [
            {
                "title": r.get("title", ""),
                "url": r.get("link", ""),
                "snippet": r.get("snippet", ""),
            }
            for r in data.get("organic", [])
        ]


class BingAPISearch(SearchBackend):
    """Bing Web Search API（Azure，免费额度）"""
    name = "bing_api"

    def search(self, query: str, max_results: int) -> List[Dict[str, str]]:
        import requests
        if not self.api_key:
            raise RuntimeError("未配置 Bing API key")
        resp = requests.get(
            "https://api.bing.microsoft.com/v7.0/search",
            params={"q": query, "count": max_results},
            headers={"Ocp-Apim-Subscription-Key": self.api_key},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        return [
            {
                "title": r.get("name", ""),
                "url": r.get("url", ""),
                "snippet": r.get("snippet", ""),
            }
            for r in (data.get("webPages") or {}).get("value", [])
        ]


class BochaSearch(SearchBackend):
    """博查（国内可用的中文搜索 API，专为 LLM agent 设计）"""
    name = "bocha"

    def search(self, query: str, max_results: int) -> List[Dict[str, str]]:
        import requests
        if not self.api_key:
            raise RuntimeError("未配置 Bocha api_key")
        resp = requests.post(
            "https://api.bochaai.com/v1/web-search",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={"query": query, "count": max_results, "summary": True},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        pages = (data.get("data") or {}).get("webPages") or {}
        return [
            {
                "title": r.get("name", ""),
                "url": r.get("url", ""),
                "snippet": r.get("summary") or r.get("snippet", ""),
            }
            for r in pages.get("value", [])
        ]


class SearchManager:
    """
    搜索管理器：按配置选择后端，失败自动降级；可选抓取结果网页正文

    配置项（config.yaml 的 search 节，api_key 也可用环境变量 SEARCH_API_KEY）：
        provider: auto | tavily | serper | bing_api | bocha | bing_html | ddg_html
        api_key: 使用 key 型后端时填写
        max_results: 默认返回条数（默认 5）
        fetch_content: 是否抓取结果页面正文（默认 true）
        fetch_top_n: 抓取前 N 条正文（默认 2）
    """

    _KEY_BACKENDS = {
        "tavily": TavilySearch,
        "serper": SerperSearch,
        "bing_api": BingAPISearch,
        "bocha": BochaSearch,
    }
    _HTML_BACKENDS = {
        "bing_html": BingHTMLSearch,
        "ddg_html": DDGHTMLSearch,
    }

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        config = config or {}
        self.max_results = max(1, int(config.get("max_results", 5) or 5))
        self.fetch_content = bool(config.get("fetch_content", True))
        self.fetch_top_n = max(0, int(config.get("fetch_top_n", 2) or 2))
        self._backends = self._build_backends(config)

    def _build_backends(self, config) -> List[SearchBackend]:
        api_key = config.get("api_key") or os.environ.get("SEARCH_API_KEY", "")
        base_url = config.get("base_url") or ""
        provider = (config.get("provider") or "auto").lower()

        if provider in self._KEY_BACKENDS:
            return [self._KEY_BACKENDS[provider](api_key, base_url)]
        if provider in self._HTML_BACKENDS:
            return [self._HTML_BACKENDS[provider]()]

        # auto：有 key 时 key 型优先，HTML 型兜底
        backends: List[SearchBackend] = []
        if api_key:
            for cls in (TavilySearch, SerperSearch, BingAPISearch, BochaSearch):
                backends.append(cls(api_key, base_url))
        backends.append(BingHTMLSearch())
        backends.append(DDGHTMLSearch())
        return backends

    def search(
        self, query: str, max_results: Optional[int] = None
    ) -> List[Dict[str, str]]:
        """依次尝试各后端，返回第一条成功的非空结果"""
        limit = max(1, max_results or self.max_results)
        errors = []
        for backend in self._backends:
            try:
                results = backend.search(query, limit)
                if results:
                    return self._enrich(results, limit)
            except Exception as e:
                errors.append(f"{backend.name}: {e}")
        if errors:
            raise RuntimeError("；".join(errors))
        return []

    def _enrich(
        self, results: List[Dict[str, str]], limit: int
    ) -> List[Dict[str, str]]:
        """给前 fetch_top_n 条结果抓取网页正文，提升信息量"""
        enriched = []
        for i, r in enumerate(results[:limit]):
            item = dict(r)
            if self.fetch_content and i < self.fetch_top_n:
                item["content"] = self._fetch_content(r["url"])
            enriched.append(item)
        return enriched

    def _fetch_content(self, url: str) -> str:
        try:
            import requests
            resp = requests.get(
                url, headers={"User-Agent": _UA},
                timeout=8, allow_redirects=True,
            )
            resp.raise_for_status()
            return _html_to_text(resp.text, max_len=600)
        except Exception:
            return ""


def format_search_results(results: List[Dict[str, str]]) -> str:
    """把搜索结果格式化为给模型看的文本"""
    lines = []
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r.get('title', '')}\n   {r.get('url', '')}")
        snippet = (r.get("snippet") or "").strip()
        if snippet:
            lines.append(f"   简介: {snippet[:300]}")
        content = (r.get("content") or "").strip()
        if content:
            lines.append(f"   正文摘要: {content[:600]}")
    return "\n".join(lines)


class WebSearchTool:
    """web_search 工具的可调用对象（携带搜索配置）"""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self._manager = SearchManager(config)

    def __call__(self, query: str, max_results: int = 5) -> str:
        try:
            results = self._manager.search(query, max_results)
        except Exception as e:
            return f"错误: 搜索失败（{e}）"
        if not results:
            return f"未找到与「{query}」相关的搜索结果。"
        return format_search_results(results)


def create_builtin_tools(
    search_config: Optional[Dict[str, Any]] = None
) -> ToolRegistry:
    """创建内置工具注册表"""
    registry = ToolRegistry()
    
    registry.register(Tool(
        name="get_current_time",
        description="查询当前的日期和时间（含星期几）。当用户问现在几点、今天几号等时使用。",
        parameters={"type": "object", "properties": {}, "additionalProperties": False},
        function=tool_get_current_time,
    ))
    registry.register(Tool(
        name="web_search",
        description=(
            "在互联网上搜索最新信息，返回标题、链接、摘要以及部分网页正文。"
            "当用户询问实时、新闻、未知知识时使用。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"},
                "max_results": {"type": "integer", "description": "返回结果数量，默认5"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        function=WebSearchTool(search_config or {}),
    ))
    
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
        image_dir: str = "Image"
    ):  
        
        # 初始化日志
        self.logger = setup_logger("AI")
        
        # 初始化配置管理器
        self.config_manager = ConfigManager(config_path)
        
        # 初始化提示词管理器
        self.prompt_manager = PromptManager(prompts_dir)
        
        # 初始化记忆管理器
        self.memory_manager = MemoryManager(chroma_dir, collection_name)
        
        # 初始化LLM提供商
        self._provider_name = provider_name or self.config_manager.get_active_provider()
        self._setup_provider()
        
        # 初始化工具系统（传入搜索配置）
        self.tool_registry = create_builtin_tools(
            self.config_manager.get('search', {})
        )
        
        # 初始化表情包系统（扫描 Image 目录 + 读取 emoji 配置）
        self._init_emoji(image_dir)
        
        # 初始化对话历史（消息可包含 content=None 与 tool_calls，故用 Any 值）
        self._messages: List[Dict[str, Any]] = []
        self._reset_messages()
        
        self.logger.info(
            f"AI聊天机器人初始化完成 (提供商: {self._provider_name}, "
            f"表情包: {self.emoji_catalog.count} 个 @ {self.emoji_catalog.image_dir})"
        )
    
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

    # 注入系统提示词的时间说明
    TIME_INSTRUCTIONS = (
        "\n\n【时间信息】\n"
        "1. 每条用户消息开头的【当前时间】就是这条消息发出时的真实本地日期与时间，"
        "回答「现在几点 / 今天几号 / 今天星期几」这类问题时以它为准，不要凭空猜测或说过时的时间；\n"
        "2. 【相关记忆】里每条记忆都以 [日期 时间] 开头，表示那件事发生的时刻，"
        "可以据此判断事情的先后顺序与相隔多久；\n"
        "3. 需要更精确的时间时也可以调用 get_current_time 工具。"
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
        """设置LLM提供商"""
        provider_config = self.config_manager.get_provider_config(self._provider_name)
        self._provider = LLMProviderFactory.create_provider(
            self._provider_name,
            provider_config
        )
    
    def _build_system_prompt(self, memory_context: Optional[str] = None) -> str:
        """构建系统提示词：固定部分（基础提示词 + 工具说明 + 表情包规则），不拼接记忆"""
        # 记忆已改放到 user 消息中（见 get_response_stream），系统提示词保持不变以节省 token
        prompt = self.prompt_manager.get_prompt()
        if self.emoji_catalog:
            prompt += self._build_emoji_instructions()
        return prompt + self.TOOL_INSTRUCTIONS + self.TIME_INSTRUCTIONS

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
                    resample = getattr(Image, "Resampling", Image).LANCZOS
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

    def _reset_messages(self) -> None:
        """重置对话历史"""
        system_prompt = self._build_system_prompt()
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

        Args:
            moment: 指定时刻，默认取当前时间

        Returns:
            形如「2026-10-03 16:20:15（星期六）」
        """
        now = moment or datetime.now()
        weekday = "星期" + "一二三四五六日"[now.weekday()]
        return f"{now.strftime('%Y-%m-%d %H:%M:%S')}（{weekday}）"
    
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
        
        if not has_text and not images:
            yield ('text', "请输入有效的问题。")
            yield ('done', "请输入有效的问题。")
            return
        
        try:
            # 0. 本轮表情包回合计数（用于概率/冷却判定）
            self.emoji_session.mark_turn()
            
            # 1. 搜索相关记忆（只发图片时不检索）
            query = (user_input or "").strip()
            memories = self.memory_manager.search_memories(query) if query else []
            
            # 2. 当前时间 + 记忆都放到 user 消息里，而不是系统提示词（节省 token）
            #    每轮都重新取时间，保证模型知道「现在」是什么时候
            time_prefix = f"【当前时间】{self._format_now()}\n\n"
            
            memory_prefix = ""
            if memories:
                memory_context = self._format_memory_context(memories)
                memory_prefix = f"【相关记忆】\n{memory_context}\n\n"
            
            user_message = time_prefix + memory_prefix + (user_input or "")
            self._messages.append(
                self._build_user_message(user_message, images)
            )
            
            tool_specs = self.tool_registry.to_openai_specs()
            full_reply = ""
            
            # 3. agent 循环
            for _ in range(self.MAX_ITERATIONS):
                text, tool_calls = self._provider.chat_completion(
                    self._messages,
                    stream=True,
                    tools=tool_specs
                )
                
                if text:
                    full_reply += text
                    yield ('text', text)
                
                if not tool_calls:
                    break  # 没有工具调用 -> 最终回答
                
                # 4. 记录 assistant 的工具调用消息
                self._messages.append({
                    'role': 'assistant',
                    'content': text or None,
                    'tool_calls': tool_calls,
                })
                
                # 5. 依次执行工具，把结果作为 tool 消息放回历史
                for call in tool_calls:
                    name = call['function']['name']
                    yield ('tool', {'name': name, 'status': 'start'})
                    
                    try:
                        arguments = json.loads(
                            call['function']['arguments'] or '{}'
                        )
                        result = self.tool_registry.call(name, arguments)
                    except Exception as e:
                        result = f"错误: 解析或调用工具 {name} 失败: {e}"
                    
                    yield ('tool', {'name': name, 'status': 'done'})
                    self._messages.append({
                        'role': 'tool',
                        'tool_call_id': call['id'],
                        'content': result,
                    })
            else:
                # 达到步数上限仍未结束
                note = "\n\n（已达到处理步数上限，以上是当前进展。）"
                full_reply += note
                yield ('text', note)
            
            # 6. 表情包：每条回复固定附带一个（模型没自己发标记时由这里补上）
            sticker = self._pick_emoji_for_reply(full_reply)
            if sticker is not None:
                self.emoji_session.mark_sent()
                self.logger.info(f"附带表情包: {sticker.file_name}")
                yield ('emoji', {'name': sticker.name, 'path': sticker.path})
            
            # 7. 保存 assistant 回复到历史（去掉表情包标记，历史保持纯文本）
            plain_reply = self._strip_emoji_tags(full_reply)
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
        """清空对话历史"""
        self._reset_messages()
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
    
    def get_history(self) -> List[Dict[str, Any]]:
        """
        获取对话历史
        
        Returns:
            对话历史列表
        """
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
        return self._messages.copy()
    
    @property
    def provider_name(self) -> str:
        """获取当前提供商名称"""
        return self._provider_name


# ============================================================================
# 工厂函数
# ============================================================================

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