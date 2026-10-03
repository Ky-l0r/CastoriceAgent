"""
依赖下载脚本

不想手动敲 pip 命令的话，直接运行本文件即可：
    python requirement_install.py

（等价的 pip 命令见 requirements.txt）
"""

import subprocess
import sys

# 需要安装的依赖包
packages = [
    "openai",
    "pyyaml",
    "chromadb",
    "requests",
    "pyside6",
    "pillow",       # 发送图片给视觉模型时需要
    "pyinstaller",  # 打包成 exe 时需要
]

for package in packages:
    # 自动调用 pip 进行安装
    subprocess.check_call([sys.executable, "-m", "pip", "install", package])
    print(f"[OK] {package} 安装完成")
