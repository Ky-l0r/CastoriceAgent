"""可写数据目录的单一解析点（第五期：程序与数据分离）。

**为什么需要这个模块**

更新方式是「整个程序文件夹替换」。如果 config.yaml / profile.json /
database / sessions 还放在程序目录里，覆盖更新的那一刻用户的 API Key、
记忆、对话历史会被一起抹掉。

所以把所有**可写**数据挪到独立目录，程序目录只留**只读**资源
（代码、prompts、Image、模板 config）。这样「替换程序目录」与
「保留用户数据」就是两件互不干扰的事。

**解析顺序**（第一个命中者生效）

1. 环境变量 `CASTORICE_DATA_ROOT` —— 壳层（Electron）显式传入，
   生产环境就是 %APPDATA%\\CastoriceAgent；
2. 程序目录 —— dev / 源码直接跑时的行为，与改造前完全一致。

**只读资源不进这里**：prompts 与 Image 属于随程序发布的素材，跟着
程序目录走；它们不该被用户改写，也不该占用户数据目录的空间。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

__all__ = [
    "DATA_ROOT_ENV",
    "CODE_ROOT_ENV",
    "data_root",
    "code_root",
    "data_path",
    "template_path",
    "ensure_data_root",
]

#: 数据目录覆盖（壳层传入 %APPDATA%\CastoriceAgent）
DATA_ROOT_ENV = "CASTORICE_DATA_ROOT"
#: 程序目录覆盖（壳层传入 resources/backend）
CODE_ROOT_ENV = "CASTORICE_ROOT"

#: 首次启动需要从模板复制到数据目录的文件
SEED_FILES = ("config.yaml",)


def code_root() -> Path:
    """程序目录：代码与只读资源所在处。

    源码运行 = 仓库根（本文件在 castorice/ 下，父目录的父目录）；
    打包后 = resources/backend（壳层经 CASTORICE_ROOT 显式传入）。
    """
    override = os.environ.get(CODE_ROOT_ENV)
    if override:
        return Path(override).expanduser().resolve()
    return Path(__file__).resolve().parent.parent


def data_root() -> Path:
    """可写数据目录：配置、档案、记忆、会话都在这里。

    未设置环境变量时回落到程序目录（开发期行为不变）。
    """
    override = os.environ.get(DATA_ROOT_ENV)
    if override:
        return Path(override).expanduser().resolve()
    return code_root()


def data_path(name: str) -> Path:
    """数据目录下的某个路径（不存在时不会自动创建）。"""
    return data_root() / name


def template_path(name: str) -> Path:
    """程序目录下的模板文件（首次启动复制用）。"""
    return code_root() / name


def ensure_data_root(seed: bool = True) -> Path:
    """确保数据目录存在，并按需从模板播种配置。

    只播种**不存在**的文件：用户改过的 config.yaml 绝不能被模板覆盖，
    否则「升级后配置重置」这个 bug 会以另一种方式回来。
    """
    root = data_root()
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        # 目录建不出来（权限问题）：返回原路径，让上层按只读降级处理
        return root

    if seed:
        import shutil

        for name in SEED_FILES:
            target = root / name
            if target.exists():
                continue
            source = template_path(name)
            if not source.is_file():
                continue
            try:
                shutil.copy2(source, target)
            except OSError:
                pass
    return root


def resolve_data_file(name: str, env_override: Optional[str] = None) -> Path:
    """单个数据文件的路径：环境变量（最高优先）-> 数据目录。

    env_override 让既有的 CASTORICE_PROFILE_PATH / CASTORICE_UI_SETTINGS
    这类细粒度覆盖继续生效（测试与高级用法依赖它们）。
    """
    if env_override:
        return Path(env_override).expanduser()
    return data_path(name)
