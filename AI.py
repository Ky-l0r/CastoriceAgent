import os
import yaml
from openai import OpenAI
from typing import Any

try:
    # 读取配置文件
    with open("config.yaml", "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)


    client = OpenAI(
        api_key=config['aliyun']['api_key'],
        base_url=config['aliyun']['base_url'],
    )

    #初始化对话历史列表，放入设定的 prompt
    messages : list[Any] = [
        {'role': 'system', 'content': config['prompt']['system_prompt']}
    ]

    #开启一个无限循环，实现持续对话
    while True:
        #获取用户输入
        user_input = input("用户: ")

        #判断是否退出
        if user_input.lower() in ['退出', 'exit', 'quit']:
            print("对话结束。")
            break

        #将用户输入添加到对话历史中
        messages.append({'role': 'user', 'content': user_input})

        # 调用大模型 API
        completion = client.chat.completions.create(
        model=config['aliyun']['model'],
        messages=messages
        )        
    
        # 打印模型的回复内容
        print(f"遐蝶: {completion.choices[0].message.content}")

    
except Exception as e:
    print(f"调用出现错误：{e}")