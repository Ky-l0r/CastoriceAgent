import sys
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont

# 导入AI和UI模块
from ai import ChatBot
from ui import ChatWindow

def main():
    try:
        # 创建应用程序
        app = QApplication(sys.argv)
        
        # 设置应用程序字体
        font = QFont("Microsoft YaHei", 9)
        app.setFont(font)
        
        # 初始化AI
        bot = ChatBot()
        
        # 创建并显示主窗口
        window = ChatWindow(bot)
        window.show()
        
        # 运行应用程序
        sys.exit(app.exec())
        
    except FileNotFoundError as e:
        print(f"错误：找不到必要的文件 - {e}")
        print("请确保以下文件存在：")
        print("  - config.yaml (配置文件)")
        print("  - prompts/ 文件夹 (提示词模板)")
        input("按回车键退出...")
        
    except Exception as e:
        print(f"启动失败：{e}")
        input("按回车键退出...")

if __name__ == "__main__":
    main()