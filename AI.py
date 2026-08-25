"""
AI核心模块
"""

import os
import re
import json
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
    
    def add_memory(self, content: str) -> str:
        """
        添加记忆到数据库
        
        Args:
            content: 记忆内容
            
        Returns:
            记忆ID
        """
        memory_id = str(self._collection.count() + 1)
        
        self._collection.add(
            documents=[content],
            ids=[memory_id]
        )
        
        return memory_id
    
    def search_memories(
        self,
        query: str,
        n_results: int = DEFAULT_SEARCH_RESULTS
    ) -> List[str]:

        try:
            results = self._collection.query(
                query_texts=[query],
                n_results=n_results
            )
            
            # 提取文档内容
            if results and 'documents' in results:
                documents = results['documents']
                if documents and len(documents) > 0:
                    return [doc for doc in documents[0] if doc]
            
            return []
            
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
        provider_name: Optional[str] = None
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
        
        # 初始化对话历史（消息可包含 content=None 与 tool_calls，故用 Any 值）
        self._messages: List[Dict[str, Any]] = []
        self._reset_messages()
        
        self.logger.info(f"AI聊天机器人初始化完成 (提供商: {self._provider_name})")
    
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

    def _setup_provider(self) -> None:
        """设置LLM提供商"""
        provider_config = self.config_manager.get_provider_config(self._provider_name)
        self._provider = LLMProviderFactory.create_provider(
            self._provider_name,
            provider_config
        )
    
    def _build_system_prompt(self, memory_context: Optional[str] = None) -> str:
        """构建系统提示词：基础提示词 + 记忆上下文 + 工具使用说明"""
        return (
            self.prompt_manager.get_prompt(memory_context)
            + self.TOOL_INSTRUCTIONS
        )

    def _reset_messages(self) -> None:
        """重置对话历史"""
        system_prompt = self._build_system_prompt()
        self._messages = [
            {'role': 'system', 'content': system_prompt}
        ]
    
    def _update_system_prompt(self, memory_context: Optional[str] = None) -> None:
        """
        更新系统提示词
        
        Args:
            memory_context: 记忆上下文
        """
        system_prompt = self._build_system_prompt(memory_context)
        
        if self._messages and self._messages[0]['role'] == 'system':
            self._messages[0]['content'] = system_prompt
        else:
            self._messages.insert(0, {'role': 'system', 'content': system_prompt})
    
    def _format_memory_context(self, memories: List[str]) -> str:
        """
        格式化记忆上下文
        
        Args:
            memories: 记忆列表
            
        Returns:
            格式化后的上下文字符串
        """
        if not memories:
            return ""
        
        return "\n".join([f"- {memory}" for memory in memories])
    
    def get_response_stream(self, user_input: str):
        """
        流式 agent 循环（ReAct：思考 → 调工具 → 观察 → 继续）

        生成事件元组：
            ('text', 文本片段)          -> 流式文本
            ('tool', {'name':..., 'status':'start'|'done'})
                                       -> 工具调用开始/结束
            ('done', 完整回复)          -> 循环结束

        Args:
            user_input: 用户输入

        Yields:
            Tuple[str, Any]: 事件元组
        """
        if not user_input or not user_input.strip():
            yield ('text', "请输入有效的问题。")
            yield ('done', "请输入有效的问题。")
            return
        
        try:
            # 1. 搜索相关记忆并更新系统提示词
            memories = self.memory_manager.search_memories(user_input)
            memory_context = self._format_memory_context(memories)
            self._update_system_prompt(memory_context if memory_context else None)
            
            # 2. 添加用户消息到历史
            self._messages.append({'role': 'user', 'content': user_input})
            
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
            
            # 6. 保存 assistant 回复到历史
            self._messages.append({'role': 'assistant', 'content': full_reply})
            
            # 7. 保存对话到记忆
            memory_content = f"用户: {user_input}\n助理: {full_reply}"
            self.memory_manager.add_memory(memory_content)
            
            self.logger.info(
                f"用户: {user_input[:50]}... -> AI: {full_reply[:50]}... "
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
    
    def change_provider(self, provider_name: str) -> None:
        """
        切换LLM提供商
        
        Args:
            provider_name: 提供商名称
        """
        self._provider_name = provider_name
        self._setup_provider()
        self._reset_messages()
        self.logger.info(f"已切换到提供商: {provider_name}")
    
    def clear_history(self) -> None:
        """清空对话历史"""
        self._reset_messages()
        self.logger.info("对话历史已清空")
    
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