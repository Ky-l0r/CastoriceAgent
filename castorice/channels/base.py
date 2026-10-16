"""渠道层接口约定（第一期只定义协议，不实现任何具体渠道）。

目标（02-architecture.md §1 的 channels/ 层）：第二期接入飞书 / 微信 iLink /
QQ 时，渠道适配层实现本文件的 Channel 协议，把各平台的消息事件翻译成对
ai.AI 冻结接口（get_response_stream / set_mode / set_approval_responder）
的调用，**不需要改动 castorice/harness.py 的核心签名**。

设计基线：
* 红线 D：本包不 import ui / PySide6 / ai；只依赖标准库。
* 事件协议对齐 ui.AIWorker 消费的元组流（('text'|'tool'|'emoji'|'done'),
  payload），恒 2 元组、done 恰好一次且最后（t19 已实测冻结）——渠道层
  直接消费同一形态，不再发明第三种协议。
* 审批纪律与 ui.ChatWindow._install_approval_responder 一致：worker 侧
  只做 broker.defer + 投递 + return None，交互在渠道自己的用户界面完成。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, Optional, Protocol, runtime_checkable

__all__ = [
    "Channel",
    "ChannelCapabilities",
    "ChannelContext",
    "ChannelError",
    "ChannelMessage",
    "OutboundMessage",
    "normalize_target",
]


class ChannelError(RuntimeError):
    """渠道层错误基类（平台断线、鉴权失败、消息格式不支持等）。"""


@dataclass(frozen=True)
class ChannelContext:
    """装配一个渠道所需的最小上下文（由组合根注入，渠道不自己拼）。

    bot 是 ai.AI 实例（鸭子类型引用，避免 import ai 造成循环）。
    sessions_root 来自 castorice.app.AppPaths.sessions——同一进程内，
    渠道会话与桌面 UI 会话共享同一落盘约定。
    """

    bot: Any
    sessions_root: str
    #: 渠道自己的数据目录（channels/<name>/），组合根保证存在。
    data_dir: str = ""
    #: 透传给渠道的附加配置（config.yaml 的 channels.<name> 节）。
    options: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ChannelMessage:
    """一条入站消息（平台侧 -> agent）。

    sender_id / chat_id 由渠道保证为平台内的稳定标识；text 已按平台
    语义剥掉命令前缀（命令由渠道自己处理，不进 agent）。
    """

    channel: str            # "feishu" | "wechat_ilink" | "qq" | ...
    sender_id: str
    chat_id: str
    text: str
    #: 平台原生消息 id（用于幂等去重；同 id 重复投递必须被渠道丢弃）。
    message_id: str = ""
    #: 图片等附件的平台临时引用（第二期视觉输入再启用）。
    attachments: tuple = ()


@dataclass(frozen=True)
class OutboundMessage:
    """一条出站消息（agent -> 平台侧）。

    text 为最终文本；emoji_tag 是表情标签（可空）；工具事件不直接出
    渠道——渠道层按自己的节奏把 tool 事件折叠成「正在执行 xx…」提示。
    """

    chat_id: str
    text: str
    emoji_tag: str = ""
    #: 回复目标消息（平台的 reply 语义），空串表示新消息。
    reply_to: str = ""


@dataclass(frozen=True)
class ChannelCapabilities:
    """渠道能力声明（组合根与 UI 据此决定可见功能）。"""

    name: str
    display_name: str
    supports_markdown: bool = False
    supports_image_input: bool = False
    supports_emoji: bool = False
    #: 主动聊天（第二期）依赖平台是否允许机器人先手发消息。
    supports_proactive: bool = False


def normalize_target(value: Any) -> str:
    """把配置里的目标 id 归一化成非空字符串（空值抛 ChannelError）。

    渠道配置里的 chat_id / allowed_senders 统一过这一关，避免「空串
    匹配一切」这类静默放行。
    """
    text = str(value if value is not None else "").strip()
    if not text:
        raise ChannelError("渠道目标 id 不能为空")
    return text


@runtime_checkable
class Channel(Protocol):
    """渠道协议：第二期的飞书 / 微信 iLink / QQ 适配层都实现它。

    生命周期（与 SessionStore 的写者纪律对齐）：
        ctx = ChannelContext(bot=..., sessions_root=..., ...)
        ch = FeishuChannel(ctx);  ch.start()
        ...  # on_message 由渠道的事件循环回调
        ch.close()   # 幂等；close 后 start 复用同一实例是允许的
    """

    #: 渠道能力（类属性；组合根读取后决定功能可见性）。
    capabilities: ChannelCapabilities

    def start(self) -> None:
        """连接平台并开始接收消息（阻塞与否由实现决定，文档写清）。

        重复 start 必须幂等；连接失败抛 ChannelError（fail-fast，
        不静默降级成「看起来在线但其实收不到」）。
        """
        ...

    def close(self) -> None:
        """断开平台并释放资源；幂等，close 后再 close 不抛。"""
        ...

    def on_message(self, message: ChannelMessage) -> Iterator[tuple]:
        """把一条入站消息交给 agent，产出 (kind, payload) 事件流。

        返回的迭代器与 ai.AI.get_response_stream 同构：
        ('text', str) / ('tool', dict) / ('emoji', (tag, path)) /
        ('done', full_text)，恒 2 元组，done 恰好一次且最后。
        实现负责把异常翻译成 ('done', 错误说明文本) 而不是让平台侧
        看到裸异常栈。
        """
        ...

    def send(self, message: OutboundMessage) -> str:
        """发送一条出站消息，返回平台消息 id（失败抛 ChannelError）。

        分段、限速、重试是渠道实现的职责；agent 侧只看到一次成功。
        """
        ...
