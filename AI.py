"""
AI核心模块
"""

import os
import logging
from typing import Optional, List, Dict, Any, Union, Sequence
from pathlib import Path

import yaml
import chromadb
from chromadb.api.models.Collection import Collection
from openai import OpenAI
from openai.types.chat import ChatCompletionMessageParam


# ============================================================================
# 日志配置
# ============================================================================

def setup_logger(name: str = "AIBot") -> logging.Logger:
    """
    设置日志记录器
    
    Args:
        name: 日志记录器名称
        
    Returns:
        配置好的日志记录器
    """
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
    """
    配置管理器
    
    负责加载和管理应用程序配置。
    """
    
    DEFAULT_CONFIG_PATH = "config.yaml"
    
    def __init__(self, config_path: str = DEFAULT_CONFIG_PATH):
        """
        初始化配置管理器
        
        Args:
            config_path: 配置文件路径
        """
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
        """
        获取配置值
        
        Args:
            key: 配置键名（支持点号分隔的嵌套键）
            default: 默认值
            
        Returns:
            配置值
        """
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
        """
        获取指定提供商的配置
        
        Args:
            provider_name: 提供商名称（如: openai, deepseek, aliyun）
            
        Returns:
            提供商配置字典
        """
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
        """
        初始化提示词管理器
        
        Args:
            prompts_dir: 提示词文件夹路径
        """
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
        """
        获取提示词
        
        Args:
            memory_context: 可选的记忆上下文
            
        Returns:
            完整的提示词
        """
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
        """
        搜索相关记忆
        
        Args:
            query: 查询文本
            n_results: 返回结果数量
            
        Returns:
            相关记忆列表
        """
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
        """
        初始化LLM提供商
        
        Args:
            config: 提供商配置
        """
        self.config = config
        self._client = None
        self._setup_client()
    
    def _setup_client(self) -> None:
        """设置客户端"""
        raise NotImplementedError("子类必须实现 _setup_client 方法")
    
    def chat_completion(
        self,
        messages: Sequence[Dict[str, str]],
        stream: bool = True,
        **kwargs
    ) -> str:
        """
        执行聊天补全
        
        Args:
            messages: 消息列表
            stream: 是否使用流式输出
            **kwargs: 额外参数
            
        Returns:
            生成的文本
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
        messages: Sequence[Dict[str, str]],
        stream: bool = True,
        **kwargs
    ) -> str:
        """
        执行聊天补全
        
        Args:
            messages: 消息列表
            stream: 是否使用流式输出
            **kwargs: 额外参数
            
        Returns:
            生成的文本
        """
        try:
            # 构建请求参数
            request_params = {
                'model': self._model,
                'messages': messages,  # Sequence[Dict[str, str]] 可以被 OpenAI API 接受
                'stream': stream,
                **kwargs
            }
            
            # 添加可选参数
            if 'temperature' in self.config:
                request_params['temperature'] = self.config['temperature']
            
            if stream:
                # 流式输出
                stream_response = self._client.chat.completions.create(**request_params)
                
                full_reply = ""
                for chunk in stream_response:
                    if chunk.choices and len(chunk.choices) > 0:
                        content = chunk.choices[0].delta.content
                        if content:
                            content = content.replace('\n\n', '\n')
                            full_reply += content
                
                return full_reply
            else:
                # 非流式输出
                response = self._client.chat.completions.create(**request_params)
                return response.choices[0].message.content or ""
                
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
# AI聊天机器人
# ============================================================================

class AIBot:
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
        """
        初始化AI聊天机器人
        
        Args:
            config_path: 配置文件路径
            prompts_dir: 提示词文件夹路径
            chroma_dir: ChromaDB数据目录
            collection_name: 记忆集合名称
            provider_name: 指定使用的提供商（不指定则使用active_provider）
        """
        # 初始化日志
        self.logger = setup_logger("AIBot")
        
        # 初始化配置管理器
        self.config_manager = ConfigManager(config_path)
        
        # 初始化提示词管理器
        self.prompt_manager = PromptManager(prompts_dir)
        
        # 初始化记忆管理器
        self.memory_manager = MemoryManager(chroma_dir, collection_name)
        
        # 初始化LLM提供商
        self._provider_name = provider_name or self.config_manager.get_active_provider()
        self._setup_provider()
        
        # 初始化对话历史
        self._messages: List[Dict[str, str]] = []
        self._reset_messages()
        
        self.logger.info(f"AI聊天机器人初始化完成 (提供商: {self._provider_name})")
    
    def _setup_provider(self) -> None:
        """设置LLM提供商"""
        provider_config = self.config_manager.get_provider_config(self._provider_name)
        self._provider = LLMProviderFactory.create_provider(
            self._provider_name,
            provider_config
        )
    
    def _reset_messages(self) -> None:
        """重置对话历史"""
        system_prompt = self.prompt_manager.get_prompt()
        self._messages = [
            {'role': 'system', 'content': system_prompt}
        ]
    
    def _update_system_prompt(self, memory_context: Optional[str] = None) -> None:
        """
        更新系统提示词
        
        Args:
            memory_context: 记忆上下文
        """
        system_prompt = self.prompt_manager.get_prompt(memory_context)
        
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
    
    def get_response(self, user_input: str) -> str:
        """
        获取AI响应
        
        Args:
            user_input: 用户输入的文字
            
        Returns:
            AI的回复文字
        """
        if not user_input or not user_input.strip():
            return "请输入有效的问题。"
        
        try:
            # 1. 搜索相关记忆
            memories = self.memory_manager.search_memories(user_input)
            memory_context = self._format_memory_context(memories)
            
            # 2. 更新系统提示词
            self._update_system_prompt(memory_context if memory_context else None)
            
            # 3. 添加用户消息到历史
            self._messages.append({'role': 'user', 'content': user_input})
            
            # 4. 调用大模型API
            full_reply = self._provider.chat_completion(
                self._messages,
                stream=True
            )
            
            # 5. 添加AI回复到历史
            self._messages.append({'role': 'assistant', 'content': full_reply})
            
            # 6. 保存对话到记忆
            memory_content = f"用户: {user_input}\n助理: {full_reply}"
            self.memory_manager.add_memory(memory_content)
            
            self.logger.info(f"用户: {user_input[:50]}... -> AI: {full_reply[:50]}...")
            
            return full_reply
            
        except Exception as e:
            error_msg = f"处理请求时出错: {str(e)}"
            self.logger.error(error_msg)
            return f"抱歉，处理您的请求时出现了错误：{str(e)}"
    
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
    
    def get_history(self) -> List[Dict[str, str]]:
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
    def messages(self) -> List[Dict[str, str]]:
        """获取对话历史（只读）"""
        return self._messages.copy()
    
    @property
    def provider_name(self) -> str:
        """获取当前提供商名称"""
        return self._provider_name


# ============================================================================
# 工厂函数
# ============================================================================

class AIBotFactory:
    """
    AIBot工厂类
    
    提供创建和配置AIBot实例的便捷方法。
    """
    
    @staticmethod
    def create_default_bot(provider_name: Optional[str] = None) -> AIBot:
        """
        创建默认配置的AI机器人
        
        Args:
            provider_name: 指定提供商（可选）
            
        Returns:
            配置好的AIBot实例
        """
        return AIBot(provider_name=provider_name)
    
    @staticmethod
    def create_with_custom_config(
        config_path: str = "config.yaml",
        prompts_dir: str = "prompts",
        chroma_dir: str = "database",
        provider_name: Optional[str] = None
    ) -> AIBot:
        """
        使用自定义配置创建AI机器人
        
        Args:
            config_path: 配置文件路径
            prompts_dir: 提示词文件夹路径
            chroma_dir: ChromaDB数据目录
            provider_name: 指定提供商（可选）
            
        Returns:
            配置好的AIBot实例
        """
        return AIBot(config_path, prompts_dir, chroma_dir, provider_name=provider_name)


# ============================================================================
# 向后兼容接口
# ============================================================================

# 全局实例（单例模式）
_bot_instance: Optional[AIBot] = None


def get_ai_bot(provider_name: Optional[str] = None) -> AIBot:
    """
    获取AI机器人实例（单例）
    
    Args:
        provider_name: 指定提供商（可选）
        
    Returns:
        AIBot实例
    """
    global _bot_instance
    if _bot_instance is None:
        _bot_instance = AIBot(provider_name=provider_name)
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
    bot = get_ai_bot(provider_name)
    return bot.get_response(user_input)


# ============================================================================
# 命令行入口
# ============================================================================

def main() -> None:
    """
    命令行交互入口
    
    用于测试AI机器人功能。
    """
    print("=" * 50)
    print("CastoriceAgent AI 测试终端")
    print("命令:")
    print("  exit - 退出")
    print("  clear - 清空对话历史")
    print("  clear_memories - 清空所有记忆")
    print("  status - 显示状态")
    print("  provider <name> - 切换提供商 (如: provider deepseek)")
    print("  list_providers - 列出所有可用提供商")
    print("=" * 50)
    
    bot = AIBot()
    
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