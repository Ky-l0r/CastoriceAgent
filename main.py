"""
主程序入口（第三期 v1.4：Electron 优先）

第三期把界面从 PySide6 桌面窗体换成 Electron 壳 + Web UI：

* 源码运行：`python main.py` 检测到 webapp 工程后，优先拉起 Electron 壳
  （Electron 主进程自己 spawn Python sidecar：webapp/electron/sidecar/
  spawn-bridge.py，经 stdout ready 帧握手，WebSocket/JSON-RPC 与后端通信）。
* 打包运行：发布包形态为 Electron 产物（CastoriceAgent.exe + resources/
  backend），入口是 exe 本身，本脚本只服务源码开发者。
* webapp 依赖缺失（未安装 node/pnpm 依赖或未构建）时，打印可读指引后退出，
  不再回退 Qt 界面（Qt 版自 v1.4 起退役）。

本文件不 import ui / PySide6——桥接层（castorice/bridge.py）与通知
（castorice/notify.py）保持纯 Python；Electron 与 sidecar 的全部接线
都在 webapp/ 壳工程里。
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
WEBAPP_DIR = REPO_ROOT / "webapp"
WEBAPP_ENTRY = WEBAPP_DIR / "dist" / "main" / "index.js"

logger = logging.getLogger("Main")


def setup_file_logging(root: Path) -> None:
    """把日志同时写入程序目录下的 logs/app.log（出问题靠文件排查）。"""
    try:
        log_dir = root / "logs"
        log_dir.mkdir(exist_ok=True)
        handler = logging.FileHandler(log_dir / "app.log", encoding="utf-8")
        handler.setFormatter(
            logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        )
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        root_logger.addHandler(handler)
    except Exception as e:  # noqa: BLE001 - 日志失败不挡启动
        print(f"警告: 无法创建日志文件 - {e}")


def webapp_ready() -> bool:
    """webapp 产物是否存在（renderer + main 双产物齐备才算 ready）。"""
    return (
        WEBAPP_ENTRY.is_file()
        and (WEBAPP_DIR / "dist" / "renderer" / "index.html").is_file()
        and (WEBAPP_DIR / "dist" / "preload" / "preload.js").is_file()
    )


def find_electron() -> "str | None":
    """定位 Electron 可执行入口（node_modules 本地优先，再退 PATH）。"""
    local = WEBAPP_DIR / "node_modules" / ".bin" / "electron.cmd"
    if local.is_file():
        return str(local)
    return shutil.which("electron")


def launch_electron() -> int:
    """拉起 Electron 壳（主进程随后自行 spawn Python sidecar）。"""
    electron = find_electron()
    if electron is None:
        print(
            "未找到 Electron。请先在 webapp/ 下安装依赖：\n"
            "  cd webapp\n"
            "  pnpm install --registry=https://registry.npmmirror.com"
        )
        return 1

    env = dict(os.environ)
    env.setdefault("ELECTRON_ENABLE_LOGGING", "1")
    # Electron 主进程按 VITE_DEV 判定 dev 形态（仓库源码树 sidecar）。
    env.setdefault("VITE_DEV", "1")

    proc = subprocess.Popen(  # noqa: S603 - 参数全部来自仓库内路径
        [electron, "."],
        cwd=str(WEBAPP_DIR),
        env=env,
    )
    try:
        return proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        return 0


def main() -> int:
    """源码运行的组合根：环境准备 + Electron 优先拉起。"""
    setup_file_logging(REPO_ROOT)
    logger.info("程序启动，根目录: %s", REPO_ROOT)

    # 统一工作目录到程序目录，保证相对路径落点不随启动 CWD 漂移
    try:
        os.chdir(REPO_ROOT)
    except Exception:  # noqa: BLE001
        pass

    if not webapp_ready():
        print(
            "CastoriceAgent v1.4：桌面界面已迁移到 Electron 壳 + Web UI。\n"
            "当前 webapp 前端产物未构建，请执行：\n"
            "  cd webapp\n"
            "  pnpm install --registry=https://registry.npmmirror.com\n"
            "  pnpm run build\n"
            "然后重新运行 python main.py。\n"
            "（发布版直接运行 CastoriceAgent.exe，无需本步骤。）"
        )
        return 1

    return launch_electron()


if __name__ == "__main__":
    sys.exit(main())
