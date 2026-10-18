"""旧数据迁移（第五期）：把程序目录里的可写数据搬到数据目录。

**为什么需要它**

v1.4 及更早版本把 config.yaml / profile.json / ui_settings.json /
database / sessions 全放在程序目录（resources/backend）。第五期改成
数据目录（%APPDATA%\\CastoriceAgent）后，老用户一升级就会「配置全没了」
——数据其实还在旧位置，只是程序不再去那里读。

这个模块在 sidecar 启动时跑一次：**目的位置不存在**才从旧位置复制，
已存在的文件绝不覆盖（用户升级后改过的新配置必须保住）。

**迁移清单**（只搬可写的用户数据，不搬代码与素材）：

| 项 | 说明 |
| :--- | :--- |
| config.yaml | API Key / 模型配置 |
| profile.json | 用户档案 |
| ui_settings.json | 外观与风格偏好 |
| database/ | 记忆向量库 |
| sessions/ | 对话历史 |
| channels/ | 渠道数据（QQ 会话映射） |

Image / prompts 是随程序发布的只读素材，不迁移（新版本自带）。
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import List

from . import paths as cpaths

__all__ = ["MIGRATED_FILES", "MIGRATED_DIRS", "migrate_legacy_data"]

logger = logging.getLogger(__name__)

#: 需要迁移的单个文件
MIGRATED_FILES = (
    "config.yaml",
    "profile.json",
    "ui_settings.json",
)

#: 需要迁移的目录
MIGRATED_DIRS = (
    "database",
    "sessions",
    "channels",
)


def migrate_legacy_data(code_root: Path, data_root: Path) -> List[str]:
    """把 code_root 里的用户数据搬到 data_root（仅当目标不存在）。

    Returns:
        实际迁移的项名列表（空列表表示无需迁移）。

    安全约束：**绝不覆盖**已存在的目标文件。迁移是为了补救老用户，
    不能把用户在新版本里已经改好的配置冲掉。
    """
    moved: List[str] = []
    try:
        if code_root.resolve() == data_root.resolve():
            return moved
    except OSError:
        return moved

    for name in MIGRATED_FILES:
        source = code_root / name
        target = data_root / name
        if not source.is_file() or target.exists():
            continue
        try:
            data_root.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            moved.append(name)
        except OSError as exc:
            logger.warning("迁移 %s 失败: %s", name, exc)

    for name in MIGRATED_DIRS:
        source = code_root / name
        target = data_root / name
        if not source.is_dir() or target.exists():
            continue
        try:
            data_root.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, target)
            moved.append(name + "/")
        except OSError as exc:
            logger.warning("迁移 %s 失败: %s", name, exc)

    if moved:
        logger.info("已从旧版本迁移用户数据: %s", ", ".join(moved))
    return moved


def ensure_and_migrate(code_root: Path) -> Path:
    """确保数据目录就绪：先播种模板配置，再迁移老用户数据。

    顺序很重要：**先播种再迁移**。若先迁移，老用户有 config.yaml 时
    播种会因为「目标已存在」跳过，这没问题；但新用户（无旧数据）时
    必须靠播种拿到模板配置。两者不会互相干扰。
    """
    data_root = cpaths.ensure_data_root(seed=True)
    migrate_legacy_data(code_root, data_root)
    return data_root
