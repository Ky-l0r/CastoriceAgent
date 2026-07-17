import os
import yaml
from openai import OpenAI

try:
    # 读取配置文件
    with open("config.yaml", "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)


    client = OpenAI(
        api_key=config['aliyun']['api_key'],
        base_url=config['aliyun']['base_url'],
    )

    #初始化对话历史列表，放入设定的 prompt
    messages = [
        {'role': 'system', 'content': config['prompt']['system_prompt']}
    ]

    

    # 调用大模型 API
    completion = client.chat.completions.create(
        model=config['aliyun']['model'],
        messages=[
            {'role': 'system', 'content': config['prompt']['system_prompt']},
            {'role': 'user', 'content': '你是谁？'}
        ]
    )
    
    # 打印模型的回复内容
    print(completion.choices[0].message.content)

except Exception as e:
    print(f"调用出现错误：{e}")