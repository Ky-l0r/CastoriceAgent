"""用户档案（第三期 t4）：昵称 / 称呼偏好 / 性别 / 生日 / 城市 / 时区。

定位：档案是喂给系统的**用户侧事实**，不是又一份配置——config.yaml 是
内核参数（改它需要重启或明确的配置语义），档案则是「关于用户是谁」的
轻量事实（昵称变了、搬城市了，下一轮对话就该生效）。因此：

* 落盘 profile.json，放**数据目录**（castorice.paths 统一解析：
  生产环境 %APPDATA%/CastoriceAgent，开发环境回落到程序目录；
  不进发布包；ENV 覆盖支持便携 / 多份配置并存）；
* 读写是**读-改-写合并**（保留文件里已有的其它键），任何异常静默降级
  ——档案坏了不该让 bot 起不来（对齐 ui.py:UiSettings 的容错标准）；
* 零第三方依赖（红线 D 同标准：不 import ui / PySide6 / ai），提示词
  注入与 bridge RPC 都能直接消费。

喂给系统（ai.py 消费，见 system_prompt_section 与 memory_query_suffix）：

* 提示词注入：用户称呼（AI 该怎么叫用户）与所在城市（地理语境）；
* 时间提示：时区参与【当前时间】前缀（bot 的每轮 user 消息头）；
* 记忆检索：查询串附带城市（天气类问题的检索定位与工具入参兜底）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

from . import paths as cpaths

__all__ = [
    "UserProfile",
    "PROFILE_FIELDS",
    "FIELD_LABELS",
    "system_prompt_section",
    "memory_query_suffix",
]

#: 环境变量覆盖档案文件路径（便携运行 / 多份配置并存时有用；
#: 命名与 ui.py:UiSettings.ENV_PATH 同风格）
ENV_PATH = "CASTORICE_PROFILE_PATH"

#: 档案字段白名单（bridge profile.set 只接受这些键；未知键拒绝，
#: 防止前端把任意数据塞进落盘文件）
PROFILE_FIELDS = (
    "nickname",       # 昵称（AI 称呼用户时的首选）
    "appellation",    # 称呼偏好（如「阁下」；空 = 用昵称或通用称呼）
    "gender",         # 性别（自由文本，交由提示词层解释）
    "birthday",       # 生日（YYYY-MM-DD 形式的自由文本，不强制校验）
    "city",           # 城市（天气 / 地理语境 / 记忆检索定位）
    "timezone",       # 时区（IANA 名，如 Asia/Shanghai；用于时间提示）
)

#: 字段中文名（前端表单与错误信息共用）
FIELD_LABELS: Dict[str, str] = {
    "nickname": "昵称",
    "appellation": "称呼偏好",
    "gender": "性别",
    "birthday": "生日",
    "city": "城市",
    "timezone": "时区",
}


def _profile_file() -> Path:
    """档案文件路径（每次动态解析）。

    优先级：CASTORICE_PROFILE_PATH（细粒度覆盖/测试）> 数据目录。
    数据目录由 castorice.paths 统一解析——生产环境是 %APPDATA%，
    开发环境回落到程序目录，两者行为与改造前保持兼容。
    """
    return cpaths.resolve_data_file(
        "profile.json", os.environ.get(ENV_PATH))


class UserProfile:
    """profile.json 的读写门面。

    桌面单用户模型下文件级原子替换足以覆盖并发（bridge RPC 走线程池，
    同一时刻至多一个写请求在跑）；写失败静默降级——档案落盘失败不该
    让对话中断。
    """

    @staticmethod
    def _file() -> Path:
        return _profile_file()

    @classmethod
    def load(cls) -> Dict[str, Any]:
        """读取整份档案；文件缺失 / 损坏时返回空 dict（静默降级）。"""
        try:
            data = json.loads(cls._file().read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    @classmethod
    def save_values(cls, values: Dict[str, Any]) -> None:
        """合并写入若干字段（保留文件里已有的其它键）。

        白名单外的键不经由此接口写入——bridge 层已在入口拒绝，这里再
        兜一层防止直接调用绕过；文件里已有的未知键原样保留。
        """
        known = {k: v for k, v in values.items() if k in PROFILE_FIELDS}
        if not known:
            return
        data = cls.load()
        data.update(known)
        path = cls._file()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # 原子替换：写临时文件再 os.replace，断电不留半份档案
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp, path)
        except Exception:
            pass  # 档案写失败静默降级（与 UiSettings 同标准）

    # ---- 便捷读取（ai.py 消费面） ----

    @classmethod
    def get(cls, key: str, default: str = "") -> str:
        """读一个字段（强转 str；缺失 / None 回退 default）。"""
        value = cls.load().get(key, default)
        if value is None:
            return default
        return str(value)

    @classmethod
    def display_name(cls) -> str:
        """称呼用户的最佳名字：称呼偏好 > 昵称 > 空串。"""
        return cls.get("appellation") or cls.get("nickname")


def system_prompt_section() -> str:
    """把档案拼成系统提示词的一个小节（无有效字段时返回空串）。

    注入位置（ai.py._build_system_prompt）：与【时间信息】同级的纯文本
    小节——不进 prompts/*.md 是因为档案是运行期可变事实，提示词文件是
    静态人格设定，混放会破坏触发词筛选的字节统计与缓存语义。
    """
    profile = UserProfile.load()

    def _field(key: str) -> str:
        return str(profile.get(key) or "").strip()

    lines: list = []
    name = _field("appellation") or _field("nickname")
    if name:
        lines.append("1. 请用「" + name + "」称呼用户；这是用户偏好的称呼。")
    nickname = _field("nickname")
    appellation = _field("appellation")
    if nickname and appellation and nickname != appellation:
        lines.append(
            "   （用户的昵称是「" + nickname + "」，称呼偏好是「" + appellation + "」）")
    gender = _field("gender")
    if gender:
        lines.append("2. 用户性别：" + gender + "。")
    birthday = _field("birthday")
    if birthday:
        lines.append("3. 用户生日：" + birthday + "（生日临近时可以送上祝福）。")
    city = _field("city")
    if city:
        lines.append(
            "4. 用户所在城市：" + city + "（天气、地理、本地生活类问题以此为准）。")
    tz = _field("timezone")
    if tz:
        lines.append("5. 用户时区：" + tz + "（时间相关回答与日程以此为准）。")
    if not lines:
        return ""
    return "\n\n【用户档案】\n" + "\n".join(lines)


def memory_query_suffix() -> str:
    """记忆检索查询的定位后缀（城市非空时返回「 城市」，否则空串）。

    用途：天气类问题（「明天天气怎么样」）检索记忆 / 喂工具入参时，
    查询串带上城市能显著改善定位——检索侧是语义匹配，城市名是
    最有区分度的地理词。
    """
    city = UserProfile.get("city").strip()
    return " " + city if city else ""
