"""渠道层（第二期接入飞书 / 微信 iLink / QQ 的落点，第一期只有接口约定）。

接口与生命周期约定见 docs/refactor/05-channel-contract.md。
"""

from .base import (
    Channel,
    ChannelCapabilities,
    ChannelContext,
    ChannelError,
    ChannelMessage,
    OutboundMessage,
    normalize_target,
)

__all__ = [
    "Channel",
    "ChannelCapabilities",
    "ChannelContext",
    "ChannelError",
    "ChannelMessage",
    "OutboundMessage",
    "normalize_target",
]
