"""
pytest 全局引导：把仓库根目录加入 sys.path

本仓库采用「脚本式」布局（ai.py / ui.py / main.py 直接放在根目录），
因此测试统一由根目录的 conftest.py 把仓库根注入模块搜索路径，
保证无论从哪个工作目录、无论用 `pytest` 还是 `python -m pytest` 运行，
`import ai` 都能成功。
"""

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
