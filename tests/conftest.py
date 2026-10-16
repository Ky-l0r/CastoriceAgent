"""
tests 公共夹具

约定：
- 所有用例默认禁止真实网络连接（不联网也能全绿）；
  个别确实需要联网的用例请加 @pytest.mark.allow_network。
- 需要配置文件时用 config_file 夹具，它会写进 pytest 的临时目录，
  绝不触碰仓库里的 config.yaml。
"""

import socket
import textwrap
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def block_network(request, monkeypatch):
    """默认拦截所有 socket 连接，保证测试离线可跑"""
    if request.node.get_closest_marker("allow_network"):
        yield
        return

    def _blocked(*args, **kwargs):
        raise RuntimeError(
            "测试期间禁止联网；确需联网请标注 @pytest.mark.allow_network"
        )

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked)
    yield


@pytest.fixture
def write_config(tmp_path):
    """生成一个临时 config.yaml，返回其路径"""

    def _write(text: str, name: str = "config.yaml") -> Path:
        path = tmp_path / name
        path.write_text(textwrap.dedent(text).lstrip("\n"), encoding="utf-8")
        return path

    return _write
