"""离线与隔离性检查

保证测试套件本身满足「不配置 API Key、不联网也能全绿」的硬性要求：
- 仓库里不留下测试写坏的 config.yaml；
- ai.py / ui.py 的业务逻辑未被测试改动（此处只做只读校验）；
- 测试用例文件名与仓库约定一致。
"""

import re
import socket
from pathlib import Path

import pytest

import ai

PROJECT_ROOT = Path(ai.__file__).resolve().parent


def test_network_is_blocked_by_default():
    """默认夹具必须让真实连接失败，证明套件是离线的"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.05)
        with pytest.raises(RuntimeError, match="禁止联网"):
            sock.connect(("127.0.0.1", 9))


def test_repository_config_yaml_is_not_written_by_tests():
    """本用例只证明我们不会把测试配置写进仓库根目录"""
    root_config = PROJECT_ROOT / "config.yaml"
    if not root_config.exists():
        pytest.skip("仓库未提供 config.yaml")
    before = root_config.read_bytes()
    # 走一遍正常的读路径：不应产生任何写入
    manager = ai.ConfigManager(str(root_config))
    manager.get_active_provider()
    assert root_config.read_bytes() == before


PATCH_PATTERN = re.compile(r"monkeypatch\.setattr\(\s*([A-Za-z_][\w.]*)")

# 允许被打补丁的目标：只限随机性与网络层，以及测试自己造的局部对象
ALLOWED_PATCH_TARGETS = {"ai.random", "socket.socket", "time.time"}
ALLOWED_LOCAL_PREFIXES = ("catalog.", "session.", "manager.", "bot.", "self.")


def _patch_targets(path):
    return PATCH_PATTERN.findall(path.read_text(encoding="utf-8"))


def test_tests_only_patch_approved_targets():
    """测试只允许 patch 随机数、socket 与测试内自造对象，不允许替换业务实现"""
    checked = 0
    for path in sorted((PROJECT_ROOT / "tests").glob("test_*.py")):
        for target in _patch_targets(path):
            checked += 1
            assert target in ALLOWED_PATCH_TARGETS or target.startswith(
                ALLOWED_LOCAL_PREFIXES
            ), f"{path.name} 里出现了越界的 patch 目标: {target}"
    assert checked > 0, "自检本身要能扫到 patch 调用，否则形同虚设"


def test_patch_scanner_detects_ai_module_patching(tmp_path):
    """反向用例：扫描器必须能抓出对 ai 模块业务实现的替换"""
    fake = tmp_path / "test_fake.py"
    # 用字符串拼接构造，避免本文件自身的源码被扫描器误判
    fake.write_text(
        "def test_x(monkeypatch):\n"
        "    monkeypatch." + "setattr(ai.AI, 'get_response', lambda self, x: 'stub')\n",
        encoding="utf-8",
    )
    assert _patch_targets(fake) == ["ai.AI"]


def test_no_test_writes_into_repository(tmp_path):
    """pytest 自带的 tmp_path 必须落在临时目录，而不是仓库内"""
    assert PROJECT_ROOT not in tmp_path.resolve().parents


@pytest.mark.parametrize("module_name", ["ai", "main"])
def test_expected_modules_exist(module_name):
    assert (PROJECT_ROOT / f"{module_name}.py").exists()


def test_tests_directory_layout():
    tests_dir = PROJECT_ROOT / "tests"
    assert tests_dir.is_dir()
    assert (tests_dir / "conftest.py").is_file()
    assert (tests_dir / "README.md").is_file()
    assert len(list(tests_dir.glob("test_*.py"))) >= 4


def test_root_conftest_adds_project_root_to_sys_path():
    import sys

    assert str(PROJECT_ROOT) in sys.path
