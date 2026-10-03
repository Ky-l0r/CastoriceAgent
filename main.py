"""
主程序入口
"""

import sys
import os
from pathlib import Path

# 导入UI模块
from ui import ChatWindow

# 导入AI核心模块
try:
    from ai import AI, get_ai
except ImportError as e:
    print(f"错误: 无法导入AI模块 - {e}")
    print("请确保 ai.py 文件存在且没有语法错误")
    sys.exit(1)


def setup_environment():
    """
    设置运行环境
    
    创建必要的目录和检查配置文件
    """
    # 创建必要的目录
    directories = ["database", "prompts", "Image"]
    for dir_name in directories:
        Path(dir_name).mkdir(exist_ok=True)
    
    # 检查配置文件
    config_file = Path("config.yaml")
    if not config_file.exists():
        print("警告: 未找到 config.yaml 配置文件")
        print("请创建配置文件或使用示例配置")
        # 可以创建默认配置文件
        # create_default_config()
    
    # 检查提示词文件
    prompts_dir = Path("prompts")
    if not any(prompts_dir.iterdir()):
        print("提示: prompts 目录为空，请添加提示词文件 (.md 或 .txt)")


def find_avatar():
    """
    查找AI头像文件
    
    Returns:
        头像文件路径，如果未找到则返回 None
    """
    avatar_dir = Path("Image")
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


def find_image_dir():
    """
    查找表情包目录（Image 下除头像与界面资源外的图片都会被当作表情包）
    
    Returns:
        目录路径（不存在时也返回默认路径，由界面自行处理空目录）
    """
    image_dir = Path("Image")
    return image_dir if image_dir.exists() else None


def main():
    """
    应用程序主入口
    """
    # 设置环境
    setup_environment()
    
    # 创建AI机器人实例
    try:
        bot = AI()
        
        print(f"CastoriceAgent初始化成功 (提供商: {bot.provider_name})")
        
    except Exception as e:
        print(f"错误: CastoriceAgent初始化失败 - {e}")
        print("请检查 config.yaml 配置文件是否正确")
        sys.exit(1)
    
    # 查找AI头像
    avatar_path = find_avatar()
    if avatar_path:
        print(f"找到头像: {avatar_path}")
    else:
        print("提示: 未找到AI头像文件，将使用默认文字头像")
    
    # 查找表情包目录
    emoji_dir = find_image_dir()
    
    # 创建聊天窗口
    try:
        window = ChatWindow(
            bot,
            ai_avatar_path=avatar_path,
            emoji_dir=emoji_dir
        )
        return window
    except Exception as e:
        print(f"错误: 创建聊天窗口失败 - {e}")
        sys.exit(1)


if __name__ == "__main__":
    # 导入PySide6（在main函数外导入）
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QFont, QPalette, QColor
    
    # 创建QApplication实例
    app = QApplication(sys.argv)
    
    # 设置应用程序样式（暗色主题）
    app.setStyle("Fusion")
    
    # 设置暗色调色板
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#1A1A1A"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#FFFFFF"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#2D2D2D"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#FFFFFF"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#2D2D2D"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#FFFFFF"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#8B5CF6"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
    app.setPalette(palette)
    
    # 设置字体
    font = QFont("Microsoft YaHei", 9)
    app.setFont(font)
    
    # 运行主程序
    window = main()
    window.show()
    
    # 进入事件循环
    sys.exit(app.exec())