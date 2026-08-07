"""
UI模块
"""

import os
import sys
from typing import Optional, Any
from pathlib import Path

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, 
    QHBoxLayout, QTextEdit, QScrollArea, QFrame, QLabel,
    QSizePolicy
)
from PySide6.QtCore import Qt, QTimer, QThread, Signal, QObject, QSize
from PySide6.QtGui import QFont, QPalette, QColor, QPixmap, QPainter


# ============================================================================
# 常量定义
# ============================================================================

class Colors:
    """颜色常量"""
    # 主窗口
    WINDOW_BG = "#1A1A1A"
    
    # 输入框
    INPUT_BG = "#2D2D2D"
    INPUT_TEXT = "#FFFFFF"
    INPUT_BORDER = "#3D3D3D"
    INPUT_BORDER_FOCUS = "#3A9E4A"
    INPUT_PLACEHOLDER = "#888888"
    
    # 消息气泡
    USER_BUBBLE_BG = "#2D7D3A"
    AI_BUBBLE_BG = "#2A2A2A"
    BUBBLE_TEXT = "#FFFFFF"
    
    # 加载指示器
    LOADING_BUBBLE_BG = "#1E1E1E"
    LOADING_DOT_COLOR = "#FFFFFF"
    
    # 滚动条
    SCROLLBAR_BG = "#2D2D2D"
    SCROLLBAR_HANDLE = "#4A4A4A"
    SCROLLBAR_HANDLE_HOVER = "#5A5A5A"
    
    # 头像
    AVATAR_BG = "#3D3D3D"
    AVATAR_BORDER = "#4A4A4A"
    AVATAR_TEXT = "#FFFFFF"
    
    # 高亮
    HIGHLIGHT = "#3A9E4A"
    HIGHLIGHT_TEXT = "#FFFFFF"


class Sizes:
    """尺寸常量"""
    WINDOW_WIDTH = 800
    WINDOW_HEIGHT = 600
    AVATAR_SIZE = 40
    BUBBLE_MAX_WIDTH = 450
    INPUT_MAX_HEIGHT = 100
    INPUT_MIN_HEIGHT = 50
    SCROLLBAR_WIDTH = 12
    MESSAGE_SPACING = 8
    BUBBLE_PADDING_H = 12
    BUBBLE_PADDING_V = 8


class Fonts:
    """字体常量"""
    FAMILY = "Microsoft YaHei"
    SIZE_MAIN = 9
    SIZE_BUBBLE = 10


# ============================================================================
# 基础组件
# ============================================================================

class AvatarLabel(QLabel):
    """
    圆形头像组件
    
    支持加载图片或显示默认文字。
    图片会被自动裁剪为圆形并适配到指定大小。
    """
    
    def __init__(
        self, 
        image_path: Optional[str] = None, 
        avatar_size: int = Sizes.AVATAR_SIZE,
        parent: Optional[QWidget] = None,
        default_text: str = "?"
    ):
        super().__init__(parent)
        self._avatar_size = avatar_size
        self._image_path = image_path
        self._default_text = default_text
        
        self._setup_ui()
        
        if image_path:
            self.load_image(image_path)
        else:
            self._show_default_text()
    
    def _setup_ui(self) -> None:
        """初始化UI"""
        self.setFixedSize(self._avatar_size, self._avatar_size)
        self.setScaledContents(False)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("""
            QLabel {
                background-color: transparent;
                border: none;
            }
        """)
    
    def _show_default_text(self) -> None:
        """显示默认文字"""
        self.setText(self._default_text)
        self.setStyleSheet(f"""
            QLabel {{
                color: {Colors.AVATAR_TEXT};
                font-size: 16px;
                font-weight: bold;
                background-color: transparent;
            }}
        """)
    
    def load_image(self, image_path: str) -> None:
        """
        加载并裁剪图片为圆形
        
        Args:
            image_path: 图片文件路径
        """
        pixmap = QPixmap(image_path)
        if not pixmap.isNull():
            self.setPixmap(self._create_round_pixmap(pixmap))
        else:
            self._show_default_text()
    
    def _create_round_pixmap(self, pixmap: QPixmap) -> QPixmap:
        """
        将图片裁剪为圆形
        
        Args:
            pixmap: 原始图片
            
        Returns:
            圆形裁剪后的图片
        """
        # 缩放图片
        scaled = pixmap.scaled(
            self._avatar_size, self._avatar_size,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation
        )
        
        # 创建圆形裁剪
        size = min(scaled.width(), scaled.height())
        result = QPixmap(size, size)
        result.fill(Qt.GlobalColor.transparent)
        
        painter = QPainter(result)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # 应用圆形裁剪路径
        path = painter.clipPath()
        path.addEllipse(0, 0, size, size)
        painter.setClipPath(path)
        
        painter.drawPixmap(0, 0, scaled)
        painter.end()
        
        return result


class ChatBubble(QFrame):
    """
    聊天气泡组件
    
    包含头像和消息内容，支持用户/AI两种样式。
    """
    
    def __init__(
        self, 
        text: str, 
        is_user: bool = False,
        avatar_path: Optional[str] = None,
        parent: Optional[QWidget] = None
    ):
        super().__init__(parent)
        self._text = text
        self._is_user = is_user
        self._avatar_path = avatar_path
        
        self._setup_ui()
    
    def _setup_ui(self) -> None:
        """初始化UI"""
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(8)
        
        # 创建头像
        if self._is_user:
            # 用户头像：显示"我"
            avatar = AvatarLabel(self._avatar_path, default_text="我")
        else:
            # AI头像：显示"?"（如果没有图片的话）
            avatar = AvatarLabel(self._avatar_path, default_text="?")
        
        # 创建气泡
        bubble = self._create_bubble()
        
        # 布局排列 - 使用顶部对齐，使头像与第一行对齐
        if self._is_user:
            main_layout.addWidget(bubble)
            main_layout.addWidget(avatar)
            main_layout.setAlignment(avatar, Qt.AlignmentFlag.AlignTop)
        else:
            main_layout.addWidget(avatar)
            main_layout.addWidget(bubble)
            main_layout.setAlignment(avatar, Qt.AlignmentFlag.AlignTop)
        
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, 
            QSizePolicy.Policy.Minimum
        )
    
    def _create_bubble(self) -> QFrame:
        """
        创建消息气泡
        
        Returns:
            气泡容器
        """
        container = QFrame()
        container.setStyleSheet("background-color: transparent; border: none;")
        
        layout = QVBoxLayout(container)
        layout.setContentsMargins(
            Sizes.BUBBLE_PADDING_H, 
            Sizes.BUBBLE_PADDING_V, 
            Sizes.BUBBLE_PADDING_H, 
            Sizes.BUBBLE_PADDING_V
        )
        
        # 文本标签
        label = QLabel(self._text)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setFont(QFont(Fonts.FAMILY, Fonts.SIZE_BUBBLE))
        
        # 设置样式
        bg_color = Colors.USER_BUBBLE_BG if self._is_user else Colors.AI_BUBBLE_BG
        container.setStyleSheet(f"""
            QFrame {{
                background-color: {bg_color};
                border-radius: 10px;
                border: none;
            }}
            QLabel {{
                color: {Colors.BUBBLE_TEXT};
                background-color: transparent;
            }}
        """)
        
        layout.addWidget(label)
        container.setMaximumWidth(Sizes.BUBBLE_MAX_WIDTH)
        
        return container


# ============================================================================
# AI工作线程
# ============================================================================

class AIWorker(QThread):
    """
    AI请求工作线程
    
    在后台处理AI请求，避免阻塞UI。
    """
    
    finished = Signal(str)
    error = Signal(str)
    
    def __init__(self, bot: Any, user_input: str, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._bot = bot
        self._user_input = user_input
    
    def run(self) -> None:
        """
        执行AI请求
        """
        try:
            reply = self._bot.get_response(self._user_input)
            self.finished.emit(reply)
        except Exception as e:
            self.error.emit(str(e))


# ============================================================================
# 主窗口
# ============================================================================

class ChatWindow(QMainWindow):
    """
    聊天主窗口
    
    提供完整的聊天界面，支持消息发送、显示和AI响应。
    """
    
    def __init__(
        self, 
        bot: Any, 
        ai_avatar_path: Optional[str] = None
    ):
        super().__init__()
        self._bot = bot
        self._ai_avatar_path = ai_avatar_path
        self._worker: Optional[AIWorker] = None
        self._loading_container: Optional[QWidget] = None
        self._loading_dots: Optional[QLabel] = None
        self._loading_timer: Optional[QTimer] = None
        
        self._init_ui()
    
    def _init_ui(self) -> None:
        """初始化UI"""
        self.setWindowTitle("CastoriceAgent")
        self.setGeometry(100, 100, Sizes.WINDOW_WIDTH, Sizes.WINDOW_HEIGHT)
        
        self._setup_style()
        self._setup_palette()
        self._setup_central_widget()
        self._setup_message_area()
        self._setup_input_area()
        self._setup_timer()
    
    def _setup_style(self) -> None:
        """设置全局样式"""
        self.setStyleSheet(f"""
            QMainWindow {{
                background-color: {Colors.WINDOW_BG};
            }}
            QTextEdit {{
                background-color: {Colors.INPUT_BG};
                color: {Colors.INPUT_TEXT};
                border: 2px solid {Colors.INPUT_BORDER};
                border-radius: 8px;
                padding: 8px;
                font-size: 12pt;
                selection-background-color: {Colors.HIGHLIGHT};
                selection-color: {Colors.HIGHLIGHT_TEXT};
            }}
            QTextEdit:focus {{
                border: 2px solid {Colors.INPUT_BORDER_FOCUS};
            }}
            QTextEdit::placeholder {{
                color: {Colors.INPUT_PLACEHOLDER};
            }}
            QScrollArea {{
                border: none;
                background-color: transparent;
            }}
            QScrollBar:vertical {{
                background-color: {Colors.SCROLLBAR_BG};
                width: {Sizes.SCROLLBAR_WIDTH}px;
                border-radius: 6px;
            }}
            QScrollBar::handle:vertical {{
                background-color: {Colors.SCROLLBAR_HANDLE};
                border-radius: 6px;
                min-height: 20px;
            }}
            QScrollBar::handle:vertical:hover {{
                background-color: {Colors.SCROLLBAR_HANDLE_HOVER};
            }}
            QScrollBar::add-line:vertical, 
            QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
            QScrollBar::add-page:vertical, 
            QScrollBar::sub-page:vertical {{
                background: none;
            }}
        """)
    
    def _setup_palette(self) -> None:
        """设置调色板（暗色主题）"""
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor(Colors.WINDOW_BG))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(Colors.INPUT_TEXT))
        palette.setColor(QPalette.ColorRole.Base, QColor(Colors.INPUT_BG))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#1E1E1E"))
        palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(Colors.INPUT_BG))
        palette.setColor(QPalette.ColorRole.ToolTipText, QColor(Colors.INPUT_TEXT))
        palette.setColor(QPalette.ColorRole.Text, QColor(Colors.INPUT_TEXT))
        palette.setColor(QPalette.ColorRole.Button, QColor(Colors.INPUT_BG))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor(Colors.INPUT_TEXT))
        palette.setColor(QPalette.ColorRole.BrightText, QColor(Colors.INPUT_TEXT))
        palette.setColor(QPalette.ColorRole.Highlight, QColor(Colors.HIGHLIGHT))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor(Colors.HIGHLIGHT_TEXT))
        self.setPalette(palette)
    
    def _setup_central_widget(self) -> None:
        """设置中心部件"""
        central = QWidget()
        self.setCentralWidget(central)
        
        layout = QVBoxLayout(central)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)
        
        self._central_layout = layout
    
    def _setup_message_area(self) -> None:
        """设置消息显示区域"""
        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        
        # 消息容器
        self._message_container = QWidget()
        self._message_container.setStyleSheet("background-color: transparent;")
        
        self._message_layout = QVBoxLayout(self._message_container)
        self._message_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._message_layout.setSpacing(Sizes.MESSAGE_SPACING)
        self._message_layout.setContentsMargins(10, 10, 10, 10)
        
        self._scroll_area.setWidget(self._message_container)
        self._central_layout.addWidget(self._scroll_area, 1)
    
    def _setup_input_area(self) -> None:
        """设置输入区域"""
        input_widget = QWidget()
        input_widget.setStyleSheet("background-color: transparent;")
        
        layout = QVBoxLayout(input_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        
        self._input_text = QTextEdit()
        self._input_text.setPlaceholderText(
            "输入消息... (Shift+Enter换行，Enter发送)"
        )
        self._input_text.setMaximumHeight(Sizes.INPUT_MAX_HEIGHT)
        self._input_text.setMinimumHeight(Sizes.INPUT_MIN_HEIGHT)
        self._input_text.installEventFilter(self)
        
        layout.addWidget(self._input_text)
        self._central_layout.addWidget(input_widget, 0)
    
    def _setup_timer(self) -> None:
        """设置定时器"""
        self._resize_timer = QTimer()
        self._resize_timer.setSingleShot(True)
        self._resize_timer.timeout.connect(self._adjust_bubble_widths)
    
    # ------------------------------------------------------------------------
    # 事件处理
    # ------------------------------------------------------------------------
    
    def eventFilter(self, obj: QObject, event: Any) -> bool:
        """
        事件过滤器
        
        处理输入框的键盘事件，实现Shift+Enter换行，Enter发送。
        
        Args:
            obj: 事件源对象
            event: 事件对象
            
        Returns:
            是否已处理事件
        """
        if obj == self._input_text and event.type() == event.Type.KeyPress:
            if event.key() == Qt.Key.Key_Return:
                if event.modifiers() == Qt.KeyboardModifier.ShiftModifier:
                    return False
                else:
                    self._send_message()
                    return True
        return super().eventFilter(obj, event)
    
    def resizeEvent(self, event: Any) -> None:
        """窗口大小改变事件"""
        super().resizeEvent(event)
        self._resize_timer.start(200)
    
    # ------------------------------------------------------------------------
    # 核心功能
    # ------------------------------------------------------------------------
    
    def _send_message(self) -> None:
        """发送消息"""
        user_input = self._input_text.toPlainText().strip()
        if not user_input:
            return
        
        self._input_text.clear()
        self.add_message(user_input, is_user=True)
        
        # 显示加载指示器
        self._show_loading_indicator()
        
        # 禁用输入
        self._input_text.setEnabled(False)
        
        # 创建AI工作线程
        self._worker = AIWorker(self._bot, user_input)
        self._worker.finished.connect(self._on_ai_response)
        self._worker.error.connect(self._on_ai_error)
        self._worker.start()
    
    def _show_loading_indicator(self) -> None:
        """显示加载指示器"""
        # 创建加载容器
        self._loading_container = QWidget()
        self._loading_container.setStyleSheet("background-color: transparent;")
        
        layout = QHBoxLayout(self._loading_container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        
        # 创建AI头像
        avatar = AvatarLabel(self._ai_avatar_path, default_text="?")
        
        # 创建加载气泡
        bubble_container = QFrame()
        bubble_container.setStyleSheet(f"""
            QFrame {{
                background-color: {Colors.LOADING_BUBBLE_BG};
                border-radius: 10px;
                border: none;
            }}
        """)
        
        bubble_layout = QVBoxLayout(bubble_container)
        bubble_layout.setContentsMargins(
            Sizes.BUBBLE_PADDING_H,
            Sizes.BUBBLE_PADDING_V,
            Sizes.BUBBLE_PADDING_H,
            Sizes.BUBBLE_PADDING_V
        )
        
        # 创建点标签
        self._loading_dots = QLabel("...")
        self._loading_dots.setFont(QFont(Fonts.FAMILY, Fonts.SIZE_BUBBLE))
        self._loading_dots.setStyleSheet(f"""
            QLabel {{
                color: {Colors.LOADING_DOT_COLOR};
                background-color: transparent;
                font-weight: bold;
            }}
        """)
        self._loading_dots.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        bubble_layout.addWidget(self._loading_dots)
        bubble_container.setMaximumWidth(80)
        
        # 布局：头像在左，气泡在右，顶部对齐
        layout.addWidget(avatar)
        layout.addWidget(bubble_container)
        layout.setAlignment(avatar, Qt.AlignmentFlag.AlignTop)  # 顶部对齐
        layout.addStretch()
        
        # 添加到消息布局
        self._message_layout.addWidget(self._loading_container)
        
        # 启动点动画
        self._dot_count = 0
        self._loading_timer = QTimer(self)
        self._loading_timer.timeout.connect(self._update_loading_dots)
        self._loading_timer.start(500)
        
        # 滚动到底部
        QTimer.singleShot(50, self._scroll_to_bottom)
    
    def _update_loading_dots(self) -> None:
        """更新加载点动画"""
        if self._loading_dots:
            self._dot_count = (self._dot_count + 1) % 4
            dots = "." * self._dot_count
            if len(dots) < 3:
                dots += " " * (3 - len(dots))
            self._loading_dots.setText(dots)
    
    def _remove_loading_indicator(self) -> None:
        """移除加载指示器"""
        if self._loading_timer:
            self._loading_timer.stop()
            self._loading_timer.deleteLater()
            self._loading_timer = None
        
        if self._loading_container:
            self._loading_container.deleteLater()
            self._loading_container = None
        
        self._loading_dots = None
    
    def add_message(self, text: str, is_user: bool = False) -> None:
        """
        添加消息到聊天界面
        
        Args:
            text: 消息内容
            is_user: 是否为用户消息
        """
        avatar_path = None if is_user else self._ai_avatar_path
        bubble = ChatBubble(text, is_user, avatar_path)
        
        # 创建容器对齐
        container = QWidget()
        container.setStyleSheet("background-color: transparent;")
        
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        if is_user:
            # 用户消息靠右
            layout.addStretch()
            layout.addWidget(bubble)
        else:
            # AI消息靠左
            layout.addWidget(bubble)
            layout.addStretch()
        
        self._message_layout.addWidget(container)
        
        # 滚动到底部
        QTimer.singleShot(50, self._scroll_to_bottom)
        QTimer.singleShot(100, self._adjust_bubble_widths)
    
    # ------------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------------
    
    def _scroll_to_bottom(self) -> None:
        """滚动到底部"""
        scrollbar = self._scroll_area.verticalScrollBar()
        if scrollbar:
            scrollbar.setValue(scrollbar.maximum())
    
    def _adjust_bubble_widths(self) -> None:
        """调整气泡宽度以适应窗口变化"""
        width = self._scroll_area.width() - 60
        max_width = min(
            Sizes.BUBBLE_MAX_WIDTH, 
            int(width * 0.7)
        )
        
        for i in range(self._message_layout.count()):
            item = self._message_layout.itemAt(i)
            if item is None:
                continue
                
            container = item.widget()
            if container is None:
                continue
            
            # 查找气泡并调整宽度
            for child in container.children():
                if isinstance(child, ChatBubble):
                    for sub_child in child.children():
                        if isinstance(sub_child, QFrame) and sub_child.layout():
                            sub_child.setMaximumWidth(max_width)
    
    # ------------------------------------------------------------------------
    # 信号处理
    # ------------------------------------------------------------------------
    
    def _on_ai_response(self, reply: str) -> None:
        """处理AI响应"""
        # 移除加载指示器
        self._remove_loading_indicator()
        
        # 显示AI回复
        self.add_message(reply, is_user=False)
        
        # 恢复输入
        self._input_text.setEnabled(True)
        self._input_text.setFocus()
        self._worker = None
    
    def _on_ai_error(self, error_msg: str) -> None:
        """处理AI错误"""
        # 移除加载指示器
        self._remove_loading_indicator()
        
        # 显示错误消息
        self.add_message(f"错误：{error_msg}", is_user=False)
        
        # 恢复输入
        self._input_text.setEnabled(True)
        self._input_text.setFocus()
        self._worker = None


# ============================================================================
# 应用程序入口
# ============================================================================

def create_dark_palette() -> QPalette:
    """
    创建暗色调色板
    
    Returns:
        QPalette: 暗色调色板
    """
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(Colors.WINDOW_BG))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(Colors.INPUT_TEXT))
    palette.setColor(QPalette.ColorRole.Base, QColor(Colors.INPUT_BG))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#1E1E1E"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(Colors.INPUT_BG))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(Colors.INPUT_TEXT))
    palette.setColor(QPalette.ColorRole.Text, QColor(Colors.INPUT_TEXT))
    palette.setColor(QPalette.ColorRole.Button, QColor(Colors.INPUT_BG))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(Colors.INPUT_TEXT))
    palette.setColor(QPalette.ColorRole.BrightText, QColor(Colors.INPUT_TEXT))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(Colors.HIGHLIGHT))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(Colors.HIGHLIGHT_TEXT))
    return palette


def get_avatar_path(relative_path: str = "Image/CastoriceAvatar.jpeg") -> Optional[str]:
    """
    获取头像文件路径
    
    Args:
        relative_path: 相对于脚本目录的路径
        
    Returns:
        头像文件路径，如果文件不存在则返回None
    """
    script_dir = Path(__file__).parent.absolute()
    avatar_path = script_dir / relative_path
    
    if avatar_path.exists():
        return str(avatar_path)
    else:
        print(f"警告: 未找到头像文件: {avatar_path}")
        return None


class MockBot:
    """模拟AI机器人（用于测试）"""
    
    @staticmethod
    def get_response(text: str) -> str:
        """模拟响应"""
        import time
        time.sleep(2)
        return f"这是对 '{text}' 的模拟回复"


def main() -> int:
    """
    应用程序入口函数
    
    Returns:
        退出码
    """
    app = QApplication(sys.argv)
    
    # 应用配置
    app.setStyle("Fusion")
    app.setPalette(create_dark_palette())
    app.setFont(QFont(Fonts.FAMILY, Fonts.SIZE_MAIN))
    
    # 创建主窗口
    avatar_path = get_avatar_path()
    window = ChatWindow(
        bot=MockBot(),
        ai_avatar_path=avatar_path
    )
    window.show()
    
    return sys.exit(app.exec())


if __name__ == "__main__":
    sys.exit(main())