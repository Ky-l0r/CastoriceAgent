"""
主程序入口
"""

import sys
import os
import logging
from pathlib import Path

# 导入UI模块
from ui import ChatWindow, Colors, ThemeSettings, app_root, create_palette

# 导入AI核心模块
try:
    from ai import AI, get_ai
except ImportError as e:
    print(f"错误: 无法导入AI模块 - {e}")
    print("请确保 ai.py 文件存在且没有语法错误")
    sys.exit(1)


def setup_file_logging(root: Path) -> None:
    """
    把日志同时写入程序目录下的 logs/app.log

    打包成 exe 后没有控制台，出了问题只能靠这个文件排查。

    Args:
        root: 程序根目录
    """
    try:
        log_dir = root / "logs"
        log_dir.mkdir(exist_ok=True)
        handler = logging.FileHandler(
            log_dir / "app.log", encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        ))
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        root_logger.addHandler(handler)
    except Exception as e:
        print(f"警告: 无法创建日志文件 - {e}")


def setup_environment(root: Path) -> None:
    """
    设置运行环境（创建必要目录、检查配置文件）

    Args:
        root: 程序根目录
    """
    # 创建必要的目录
    for dir_name in ("database", "prompts", "Image"):
        (root / dir_name).mkdir(exist_ok=True)
    
    # 检查配置文件：缺失时用示例配置自动补一份，降低首次使用门槛
    config_file = root / "config.yaml"
    if not config_file.exists():
        example = root / "example_config.yaml"
        if example.exists():
            config_file.write_text(
                example.read_text(encoding="utf-8"), encoding="utf-8"
            )
            print(f"已根据示例创建配置文件: {config_file}")
            print("请打开它填入你的 API Key（或在软件的「设置」里填写）")
        else:
            print("警告: 未找到 config.yaml 配置文件")


def find_avatar(root: Path) -> "str | None":
    """
    查找AI头像文件
    
    Args:
        root: 程序根目录

    Returns:
        头像文件路径，如果未找到则返回 None
    """
    avatar_dir = root / "Image"
    if not avatar_dir.exists():
        return None
    
    # 尝试不同的文件名和扩展名
    avatar_names = [
        "CastoriceAvatar",
        "avatar",
        "ai_avatar",
        "profile"
    ]
    extensions = ['.jpeg', '.jpg', '.png', '.gif', '.webp']
    
    for name in avatar_names:
        for ext in extensions:
            avatar_file = avatar_dir / f"{name}{ext}"
            if avatar_file.exists():
                return str(avatar_file)
    
    return None


def find_image_dir(root: Path) -> "Path | None":
    """
    查找表情包目录

    表情包放在 Image/表情包 子目录下（不存在时退回 Image 本身，
    兼容旧结构；头像与 _ 开头的界面资源始终放在 Image 根目录）。

    Args:
        root: 程序根目录

    Returns:
        表情包目录路径（不存在时返回 None，由界面自行处理空目录）
    """
    image_dir = root / "Image"
    if not image_dir.exists():
        return None
    sub_dir = image_dir / "表情包"
    return sub_dir if sub_dir.is_dir() else image_dir


def is_placeholder_key(bot) -> bool:
    """
    判断当前提供商的 API Key 是否还是示例里的占位值

    首次使用时配置文件里是 "your-xxx-api-key"，直接发消息只会得到
    鉴权错误；这里提前识别出来，好引导用户去「设置」里填写。

    Args:
        bot: AI 实例

    Returns:
        True 表示尚未配置真实 Key
    """
    try:
        config = bot.config_manager.get_provider_config(bot.provider_name)
    except Exception:
        return False
    key = str(config.get('api_key') or "").strip().lower()
    if not key:
        return True
    return key.startswith("your-") or key in ("sk-xxx", "api-key", "none")


def guide_first_run(window: ChatWindow, bot) -> None:
    """
    首次使用引导：Key 还没填时，在聊天区说明并自动打开设置窗口

    Args:
        window: 聊天主窗口
        bot: AI 实例
    """
    if not is_placeholder_key(bot):
        return
    
    window.add_message(
        "还没配置 API Key，现在还不能对话。\n"
        "已经为你打开了「设置」窗口，请选择模型提供商，"
        "填入 API Key（Base URL 与模型名一般保持默认即可），"
        "然后点「保存」。",
        is_user=False,
    )
    window._on_settings_clicked()


def main(root: Path):
    """
    应用程序主入口

    Args:
        root: 程序根目录

    Returns:
        聊天主窗口
    """
    # 设置环境
    setup_environment(root)
    
    # 创建AI机器人实例（显式传绝对路径，打包后也能正确定位资源）
    try:
        bot = AI(
            config_path=str(root / "config.yaml"),
            prompts_dir=str(root / "prompts"),
            chroma_dir=str(root / "database"),
            image_dir=str(root / "Image"),
        )
        logging.getLogger("AI").info(
            f"初始化成功 (提供商: {bot.provider_name})"
        )
    except Exception as e:
        logging.getLogger("AI").error(f"初始化失败: {e}", exc_info=True)
        raise RuntimeError(f"初始化失败：{e}") from e
    
    # 查找AI头像
    avatar_path = find_avatar(root)
    
    # 查找表情包目录
    emoji_dir = find_image_dir(root)
    

    # 创建聊天窗口
    window = ChatWindow(
        bot,
        ai_avatar_path=avatar_path,
        emoji_dir=emoji_dir
    )
    
    # 首次使用引导：Key 还是占位值时提示并打开设置窗口
    try:
        guide_first_run(window, bot)
    except Exception as e:
        logging.getLogger("Main").warning(f"首次引导失败: {e}")
    
    return window


def show_startup_error(message: str) -> None:
    """启动失败时用对话框提示（打包后没有控制台，用户需要看到原因）"""
    detail = (
        f"{message}\n\n"
        "常见原因：\n"
        "1. config.yaml 里的 API Key / 模型名称没填对；\n"
        "2. config.yaml 格式有误（缩进、引号）。\n\n"
        f"程序目录：{app_root()}\n"
        f"详细日志：{app_root() / 'logs' / 'app.log'}"
    )
    print(detail)
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
        if QApplication.instance() is None:
            QApplication(sys.argv)
        box = QMessageBox()
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("CastoriceAgent 启动失败")
        box.setText("程序启动失败")
        box.setInformativeText(detail)
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.exec()
    except Exception:
        pass  # 连对话框都起不来时，至少日志里有记录


if __name__ == "__main__":
    # 导入PySide6（在main函数外导入）
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QFont
    
    root = app_root()
    # 统一工作目录到程序目录，保证所有相对路径都指向程序自身
    try:
        os.chdir(root)
    except Exception:
        pass
    setup_file_logging(root)
    logging.getLogger("Main").info(f"程序启动，根目录: {root}")
    
    # 创建QApplication实例
    app = QApplication(sys.argv)
    
    # 设置应用程序样式（按已保存的显示模式，默认跟随系统）
    app.setStyle("Fusion")
    Colors.set_mode(ThemeSettings.load())
    app.setPalette(create_palette())
    
    # 设置字体
    font = QFont("Microsoft YaHei", 9)
    app.setFont(font)
    
    # 运行主程序
    try:
        window = main(root)
    except Exception as e:
        logging.getLogger("Main").error(f"启动失败: {e}", exc_info=True)
        show_startup_error(str(e))
        sys.exit(1)
    
    window.show()
    
    # 进入事件循环
    sys.exit(app.exec())
