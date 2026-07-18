import os
import yaml
from openai import OpenAI
from typing import Any

try:
    # 读取配置文件
    with open("config.yaml", "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    # 初始化 OpenAI 客户端
    client = OpenAI(
        api_key=config['aliyun']['api_key'],
        base_url=config['aliyun']['base_url'],
    )

    #自动读取 prompts 文件夹下的所有 .md 文件
    prompt_parts = []
    prompts_dir = "prompts"
    for filename in os.listdir(prompts_dir):
        if filename.endswith(".md"):
            filepath = os.path.join(prompts_dir, filename)
            with open(filepath, "r", encoding="utf-8") as f:
                prompt_parts.append(f.read())

    # 将所有读取到的内容拼接成一个完整的 Prompt
    prompt = "\n\n".join(prompt_parts)

    #初始化对话历史列表，放入设定的 prompt
    messages : list[Any] = [
        {'role': 'system', 'content': prompt}
    ]

    #开启一个无限循环，实现持续对话
    while True:
        #获取用户输入
        user_input = input("\n用户: ")

        #判断是否退出
        if user_input.lower() in ['退出', 'exit', 'quit']:
            print("对话结束。")
            break

        #将用户输入添加到对话历史中
        messages.append({'role': 'user', 'content': user_input})

        # 调用大模型 API,开启流式输出
        stream = client.chat.completions.create(
        model=config['aliyun']['model'],
        messages=messages,
        stream=True
        )
        


        # 用于收集完整的回复，以便存入历史记录
        full_reply = ""

        #在 AI 开始输出前，打印前缀
        print("遐蝶：", end="", flush=True)
    
        # 遍历流式返回的数据块
        for chunk in stream:
            # 安全检查：确保 chunk 里有 choices 列表，且列表不为空
            if chunk.choices and len(chunk.choices) > 0:
                # 提取当前块中的文本内容
                content = chunk.choices[0].delta.content
                # 确保内容有值再打印和拼接
                if content:
                    print(content, end="", flush=True)
                    full_reply += content
        
        # 打印完一行后换行
        print()

        # 将 AI 的完整回复加入到对话历史中，供下一轮使用
        messages.append({'role': 'assistant', 'content': full_reply})

    
except Exception as e:
    print(f"请求错误：{e}")