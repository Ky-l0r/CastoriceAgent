# ai.py - 你原有的AI核心逻辑（保持基本不变，只做少量调整）
import os
import yaml
import chromadb
from openai import OpenAI
from typing import Any

# 定义向量数据库的持久化存储文件夹
CHROMA_DIR = "database"

class AIBot:
    """AI聊天机器人核心类"""
    
    def __init__(self):
        """初始化AI机器人"""
        # 初始化 ChromaDB
        self.chroma_client = chromadb.PersistentClient(path=CHROMA_DIR)
        self.memory_collection = self.chroma_client.get_or_create_collection(name="memories")
        
        # 读取配置文件
        with open("config.yaml", "r", encoding="utf-8") as f:
            self.config = yaml.safe_load(f)
        
        # 读取 prompts
        prompt_parts = []
        prompts_dir = "prompts"
        if os.path.exists(prompts_dir):
            for filename in os.listdir(prompts_dir):
                if filename.endswith(".md"):
                    filepath = os.path.join(prompts_dir, filename)
                    with open(filepath, "r", encoding="utf-8") as f:
                        prompt_parts.append(f.read())
        
        self.prompt = "\n\n".join(prompt_parts)
        
        # 初始化 OpenAI 客户端
        self.client = OpenAI(
            api_key=self.config['aliyun']['api_key'],
            base_url=self.config['aliyun']['base_url'],
        )
        
        # 初始化对话历史
        self.messages: list[Any] = [
            {'role': 'system', 'content': self.prompt}
        ]
    
    def get_response(self, user_input: str) -> str:
        """
        获取AI响应
        
        Args:
            user_input: 用户输入的文字
            
        Returns:
            AI的回复文字
        """
        # 在向量库搜索相关记忆
        relevant_memories = self.memory_collection.query(
            query_texts=[user_input],
            n_results=5
        )
        
        # 处理记忆上下文
        documents = None
        if isinstance(relevant_memories, dict):
            documents = relevant_memories.get('documents')
        
        if documents and len(documents) > 0 and documents[0]:
            memory_context = "\n\n【联想到的相关记忆】：\n" + "\n".join([f"- {doc}" for doc in documents[0]])
            self.messages[0]['content'] = self.prompt + memory_context
        else:
            self.messages[0]['content'] = self.prompt
        
        # 添加用户消息到历史
        self.messages.append({'role': 'user', 'content': user_input})
        
        # 调用大模型API
        stream = self.client.chat.completions.create(
            model=self.config['aliyun']['model'],
            messages=self.messages,
            stream=True
        )
        
        # 收集完整回复
        full_reply = ""
        for chunk in stream:
            if chunk.choices and len(chunk.choices) > 0:
                content = chunk.choices[0].delta.content
                if content:
                    content = content.replace('\n\n', '\n')
                    full_reply += content
        
        # 添加AI回复到历史
        self.messages.append({'role': 'assistant', 'content': full_reply})
        
        # 保存到向量数据库
        current_id = str(self.memory_collection.count() + 1)
        self.memory_collection.add(
            documents=[f"用户: {user_input}\n遐蝶: {full_reply}"],
            ids=[current_id]
        )
        
        return full_reply

# 保持向后兼容 - 原有的函数式调用
def get_ai_response(user_input: str) -> str:
    """获取AI响应（函数式调用，兼容原有代码）"""
    bot = AIBot()
    return bot.get_response(user_input)