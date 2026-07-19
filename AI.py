import os
import yaml
import chromadb
from openai import OpenAI
from typing import Any

# 定义向量数据库的持久化存储文件夹
CHROMA_DIR = "database"

try:
    # 初始化 ChromaDB 客户端
    chroma_client = chromadb.PersistentClient(path=CHROMA_DIR)

    # 创建"memories集合
    memory_collection = chroma_client.get_or_create_collection(name="memories")

    # 读取配置文件
    with open("config.yaml", "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    # 自动读取 prompts 文件夹下的所有 .md 文件
    prompt_parts = []
    prompts_dir = "prompts"
    for filename in os.listdir(prompts_dir):
        if filename.endswith(".md"):
            filepath = os.path.join(prompts_dir, filename)
            with open(filepath, "r", encoding="utf-8") as f:
                prompt_parts.append(f.read())

    # 将所有读取到的内容拼接成一个完整的 Prompt
    prompt = "\n\n".join(prompt_parts)


    # 初始化 OpenAI 客户端
    client = OpenAI(
        api_key=config['aliyun']['api_key'],
        base_url=config['aliyun']['base_url'],
    )

    # 初始化对话历史列表
    messages : list[Any] = [
        {'role': 'system', 'content': prompt}
    ]

    # 开启一个无限循环，实现持续对话
    while True:
        # 获取用户输入
        user_input = input("\n用户: ")

        # 判断是否退出
        if user_input.lower() in ['退出', 'exit', 'quit']:
            print("对话结束。")
            break

        # 在向量库搜索最相关的5条记忆
        relevant_memories = memory_collection.query(
            query_texts=[user_input],
            n_results=5
        )

        # 搜到了相关记忆，临时加入到Prompt中
        documents = None
        if isinstance(relevant_memories, dict):
            documents = relevant_memories.get('documents')

        if documents and len(documents) > 0 and documents[0]:
            memory_context = "\n\n【联想到的相关记忆】：\n" + "\n".join([f"- {doc}" for doc in documents[0]])
            # 动态更新当前对话的Prompt
            messages[0]['content'] = prompt + memory_context
        else:
            messages[0]['content'] = prompt

        # 将用户输入添加到对话历史中
        messages.append({'role': 'user', 'content': user_input})

        # 调用大模型 API,开启流式输出
        stream = client.chat.completions.create(
        model=config['aliyun']['model'],
        messages=messages,
        stream=True
        )

        # 用于收集完整的回复，以便存入历史记录
        full_reply = ""

        # 在 AI 开始输出前，打印前缀
        print("遐蝶：", end="", flush=True)
    
        # 遍历流式返回的数据块
        for chunk in stream:
            # 安全检查：确保 chunk 里有 choices 列表，且列表不为空
            if chunk.choices and len(chunk.choices) > 0:
                content = chunk.choices[0].delta.content
                if content:
                    content = content.replace('\n\n', '\n')
                    print(content, end="", flush=True)
                    full_reply += content

        print()

        # 将AI回复加入到对话历史中
        messages.append({'role': 'assistant', 'content': full_reply})

        # 将用户输入和AI回复存入向量数据库,给每条记忆生成一个唯一的 ID
        current_id = str(memory_collection.count() + 1)
        memory_collection.add(
            documents=[f"用户: {user_input}\n遐蝶: {full_reply}"],
            ids=[current_id]
        )
    
except Exception as e:
    print(f"请求错误：{e}")