"""
UI模块
"""

import os
import sys
from typing import Optional, Any, Dict
from pathlib import Path

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, 
    QHBoxLayout, QTextEdit, QScrollArea, QFrame, QLabel,
    QSizePolicy, QPushButton
)
from PySide6.QtCore import Qt, QTimer, QThread, Signal, QObject, QSize, QPoint, QPointF, QRectF
from PySide6.QtGui import (
    QFont, QPalette, QColor, QPixmap, QPainter, QPainterPath, QPen
)


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
    INPUT_BORDER_FOCUS = "#8B5CF6"
    INPUT_PLACEHOLDER = "#888888"
    
    # 消息气泡
    USER_BUBBLE_BG = "#2A2A2A"
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
    HIGHLIGHT = "#8B5CF6"       
    HIGHLIGHT_TEXT = "#FFFFFF"   
    
    # 标题栏（紫色不透明）
    TITLEBAR_BG = "#7C3AED"
    TITLEBAR_BG_HOVER = "#8B5CF6"
    TITLEBAR_TEXT = "#FFFFFF"
    
    # 亚克力透明度（0-255）
    WINDOW_BG_ALPHA = 150       # 窗口背景透明度
    INPUT_BG_ALPHA = 130        # 输入框背景透明度


class Sizes:
    """尺寸常量"""
    WINDOW_WIDTH = 800
    WINDOW_HEIGHT = 600
    WINDOW_RADIUS = 12          # 窗口圆角半径
    TITLEBAR_HEIGHT = 36        # 标题栏高度
    TITLE_BUTTON_SIZE = 26      # 标题栏按钮大小
    TITLEBAR_MARGIN_H = 14      # 标题栏左右边距
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


class WrapLabel(QLabel):
    """
    按内容自适应的换行文本标签

    sizeHint 返回单行文本宽度，使气泡宽度随内容增长；
    超过气泡最大宽度后由 wordWrap 自动换行。
    """

    def sizeHint(self) -> QSize:
        text = self.text()
        fm = self.fontMetrics()
        width = 0
        for line in text.split("\n"):
            width = max(width, fm.horizontalAdvance(line))
        if width <= 0:
            return super().sizeHint()
        return QSize(width, fm.height())


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
        parent: Optional[QWidget] = None,
        is_streaming: bool = False  # 新增：是否为流式输出
    ):
        super().__init__(parent)
        self._text = text
        self._is_user = is_user
        self._avatar_path = avatar_path
        self._is_streaming = is_streaming
        self._label = None  # 保存label引用以便更新
        
        self._setup_ui()
    
    def _setup_ui(self) -> None:
        """初始化UI"""
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(8)
        
        # 创建头像
        if self._is_user:
            avatar = AvatarLabel(self._avatar_path, default_text="我")
        else:
            avatar = AvatarLabel(self._avatar_path, default_text="?")
        
        # 创建气泡
        bubble = self._create_bubble()
        
        # 布局排列（bubble 加 stretch factor，使气泡背景包住文本、宽度随内容）
        if self._is_user:
            main_layout.addWidget(bubble, 1)
            main_layout.addWidget(avatar)
            main_layout.setAlignment(avatar, Qt.AlignmentFlag.AlignTop)
        else:
            main_layout.addWidget(avatar)
            main_layout.addWidget(bubble, 1)
            main_layout.setAlignment(avatar, Qt.AlignmentFlag.AlignTop)
        
        # 水平方向不强制拉伸：气泡宽度随文本内容自适应（上限由 _adjust_bubble_widths 限定）
        self.setSizePolicy(
            QSizePolicy.Policy.Maximum,
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
        self._label = WrapLabel(self._text)
        self._label.setWordWrap(True)
        self._label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._label.setFont(QFont(Fonts.FAMILY, Fonts.SIZE_BUBBLE))
        
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
        
        layout.addWidget(self._label)
        container.setMaximumWidth(Sizes.BUBBLE_MAX_WIDTH)
        
        return container
    
    def append_text(self, text: str) -> None:
        """追加文本（用于流式输出）"""
        if self._label:
            current_text = self._label.text()
            self._label.setText(current_text + text)
    
    def set_text(self, text: str) -> None:
        """设置完整文本"""
        if self._label:
            self._label.setText(text)


# ============================================================================
# AI工作线程（支持流式输出）
# ============================================================================

class AIWorker(QThread):
    """
    AI请求工作线程 - 支持流式输出与工具调用事件
    """
    
    # 流式输出信号：每次收到新的chunk
    chunk_received = Signal(str)
    # 工具事件信号：name, status('start'/'done')
    tool_event = Signal(str, str)
    # 完成信号
    finished = Signal()
    # 错误信号
    error = Signal(str)
    
    def __init__(self, bot: Any, user_input: str, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._bot = bot
        self._user_input = user_input
        self._is_running = True
    
    def stop(self) -> None:
        """停止线程"""
        self._is_running = False
    
    def run(self) -> None:
        """
        执行AI请求 - 流式输出

        兼容两种流：
        - 普通字符串chunk（MockBot）
        - agent事件元组 ('text', ...) / ('tool', {...}) / ('done', ...)
        """
        try:
            # 调用AI的流式响应方法
            for chunk in self._bot.get_response_stream(self._user_input):
                if not self._is_running:
                    break
                
                if isinstance(chunk, tuple) and len(chunk) == 2:
                    kind, payload = chunk
                    if kind == 'text':
                        # 发送文本chunk到主线程
                        self.chunk_received.emit(payload)
                    elif kind == 'tool':
                        # 发送工具事件到主线程
                        self.tool_event.emit(
                            payload.get('name', ''),
                            payload.get('status', '')
                        )
                else:
                    # 兼容旧式流：直接是文本chunk
                    self.chunk_received.emit(chunk)
            
            # 发送完成信号
            self.finished.emit()
            
        except Exception as e:
            self.error.emit(str(e))


class TitleBarButton(QPushButton):
    """
    标题栏按钮（最小化/关闭）

    使用自定义绘制：
    - 悬停/按下时绘制圆形高亮背景；
    - 图形符号（— / ✕）由 QPainter 手工绘制，
      所有坐标围绕按钮中心对称生成，保证精确居中，
      不依赖字体基线或字形度量（避免字体回退导致符号歪斜）。
    """

    # 符号几何比例（相对按钮尺寸）
    _STROKE_W = 2.0        # 笔画宽度
    _DASH_RATIO = 0.27     # 短横线半长 = 按钮宽度 * 0.27
    _X_INSET_RATIO = 0.23  # X 端点内缩 = 按钮尺寸 * 0.23

    def __init__(
        self,
        glyph: str,
        hover_color: str,
        tooltip: str,
        parent: Optional[QWidget] = None
    ):
        # 文本由 paintEvent 自行绘制，这里不设置按钮文本
        super().__init__("", parent)
        self._glyph = glyph
        self._hover_color = QColor(hover_color)
        self._hovered = False

        self.setFixedSize(Sizes.TITLE_BUTTON_SIZE, Sizes.TITLE_BUTTON_SIZE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setToolTip(tooltip)

    # ------------------------------------------------------------------------
    # 事件处理
    # ------------------------------------------------------------------------

    def enterEvent(self, event: Any) -> None:
        """鼠标进入：高亮并重绘"""
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: Any) -> None:
        """鼠标离开：取消高亮并重绘"""
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def _is_active(self) -> bool:
        """是否处于悬停或按下状态"""
        return self._hovered or self.isDown()

    # ------------------------------------------------------------------------
    # 绘制
    # ------------------------------------------------------------------------

    def paintEvent(self, event: Any) -> None:
        """绘制按钮"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._draw(painter)
        painter.end()

    def _draw(self, painter: QPainter) -> None:
        """绘制圆形背景与居中符号"""
        rect = self.rect()

        # 1. 高亮圆形背景（悬停/按下时）
        if self._is_active():
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self._hover_color)
            painter.drawEllipse(rect)

        # 2. 居中符号
        color = "#FFFFFF" if self._is_active() else "#CCCCCC"
        pen = QPen(
            QColor(color), self._STROKE_W,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin
        )
        painter.setPen(pen)

        cx = rect.width() / 2.0
        cy = rect.height() / 2.0

        if self._glyph == "—":
            # 最小化：水平短横线，居中
            half = rect.width() * self._DASH_RATIO
            painter.drawLine(
                QPointF(cx - half, cy),
                QPointF(cx + half, cy)
            )
        elif self._glyph == "✕":
            # 关闭：两条对角线构成 X，中心对称
            inset = rect.width() * self._X_INSET_RATIO
            painter.drawLine(
                QPointF(inset, inset),
                QPointF(rect.width() - inset, rect.height() - inset)
            )
            painter.drawLine(
                QPointF(rect.width() - inset, inset),
                QPointF(inset, rect.height() - inset)
            )
        else:
            # 兜底：按字形墨迹居中绘制文本
            font = QFont(Fonts.FAMILY)
            font.setPixelSize(13)
            font.setBold(True)
            path = QPainterPath()
            path.addText(QPointF(0, 0), font, self._glyph)
            ink = path.boundingRect()
            painter.save()
            painter.translate(cx - ink.center().x(), cy - ink.center().y())
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(color))
            painter.drawPath(path)
            painter.restore()


class TitleBar(QWidget):
    """
    自定义标题栏

    显示应用标题，提供最小化/关闭按钮，并支持按住拖动窗口。
    """

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        title: str = "CastoriceAgent"
    ):
        super().__init__(parent)
        self._drag_offset: Optional[QPoint] = None

        self.setFixedHeight(Sizes.TITLEBAR_HEIGHT)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(Sizes.TITLEBAR_MARGIN_H, 0, 8, 0)
        layout.setSpacing(4)

        # 标题
        self._title_label = QLabel(title)
        self._title_label.setStyleSheet(f"""
            QLabel {{
                color: {Colors.INPUT_TEXT};
                font-size: 11pt;
                font-weight: bold;
                background-color: transparent;
                border: none;
            }}
        """)
        layout.addWidget(self._title_label)
        layout.addStretch()

        # 最小化按钮
        self._min_button = TitleBarButton(
            "—", hover_color=Colors.TITLEBAR_BG_HOVER, tooltip="最小化", parent=self
        )
        self._min_button.clicked.connect(self._on_minimize_clicked)
        layout.addWidget(self._min_button)

        # 关闭按钮
        self._close_button = TitleBarButton(
            "✕", hover_color="#E81123", tooltip="关闭", parent=self
        )
        self._close_button.clicked.connect(self._on_close_clicked)
        layout.addWidget(self._close_button)

    # ------------------------------------------------------------------------
    # 槽函数
    # ------------------------------------------------------------------------

    def _on_minimize_clicked(self) -> None:
        """最小化窗口"""
        window = self.window()
        if window is not None:
            window.showMinimized()

    def _on_close_clicked(self) -> None:
        """关闭窗口"""
        window = self.window()
        if window is not None:
            window.close()

    # ------------------------------------------------------------------------
    # 拖动窗口
    # ------------------------------------------------------------------------

    def mousePressEvent(self, event: Any) -> None:
        """记录拖动起点"""
        if event.button() == Qt.MouseButton.LeftButton:
            window = self.window()
            if window is not None:
                self._drag_offset = (
                    event.globalPosition().toPoint()
                    - window.frameGeometry().topLeft()
                )
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: Any) -> None:
        """拖动窗口"""
        if self._drag_offset is not None and (
            event.buttons() & Qt.MouseButton.LeftButton
        ):
            window = self.window()
            if window is not None:
                window.move(
                    event.globalPosition().toPoint() - self._drag_offset
                )
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: Any) -> None:
        """结束拖动"""
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    # ------------------------------------------------------------------------
    # 绘制
    # ------------------------------------------------------------------------

    def paintEvent(self, event: Any) -> None:
        """绘制紫色不透明标题栏（顶部圆角与窗口圆角一致）"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillPath(
            self._build_path(QRectF(self.rect())),
            QColor(Colors.TITLEBAR_BG)
        )
        painter.end()

    def _build_path(self, rect: QRectF) -> QPainterPath:
        """构建顶部圆角、底部直角的圆角路径"""
        r = float(Sizes.WINDOW_RADIUS)
        path = QPainterPath()
        path.moveTo(rect.left(), rect.bottom())
        path.lineTo(rect.left(), rect.top() + r)
        path.quadTo(rect.left(), rect.top(), rect.left() + r, rect.top())
        path.lineTo(rect.right() - r, rect.top())
        path.quadTo(rect.right(), rect.top(), rect.right(), rect.top() + r)
        path.lineTo(rect.right(), rect.bottom())
        path.closeSubpath()
        return path


# ============================================================================
# 平台工具
# ============================================================================


# ============================================================================
# 主窗口
# ============================================================================

class ChatWindow(QMainWindow):
    """
    聊天主窗口
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
        self._streaming_bubble: Optional[ChatBubble] = None
        self._streaming_container: Optional[QWidget] = None
        self._tool_labels: Dict[str, QLabel] = {}  # 工具状态气泡
        self._region_applied = False
        
        self._init_ui()
    
    def _init_ui(self) -> None:
        """初始化UI"""
        self.setWindowTitle("CastoriceAgent")
        self.setFixedSize(Sizes.WINDOW_WIDTH, Sizes.WINDOW_HEIGHT)
        self._center_on_screen()
        
        self._setup_window_flags()
        self._setup_style()
        self._setup_palette()
        self._setup_central_widget()
        self._setup_title_bar()
        self._setup_message_area()
        self._setup_input_area()
        self._setup_timer()
    
    def _setup_style(self) -> None:
        """设置全局样式"""
        self.setStyleSheet(f"""
            QMainWindow {{
                background-color: transparent;
            }}
            QTextEdit {{
                background-color: rgba(45, 45, 45, {Colors.INPUT_BG_ALPHA});
                color: {Colors.INPUT_TEXT};
                border: 1px solid rgba(255, 255, 255, 45);
                border-radius: 12px;
                padding: 8px;
                font-size: 12pt;
                selection-background-color: {Colors.HIGHLIGHT};
                selection-color: {Colors.HIGHLIGHT_TEXT};
            }}
            QTextEdit:focus {{
                border: 1px solid {Colors.INPUT_BORDER_FOCUS};
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
        central.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setCentralWidget(central)
        self._central_widget = central
        
        # 外层布局：标题栏通栏显示
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._outer_layout = outer
        
        # 内容区：消息区 + 输入区（保留四周内边距）
        content = QWidget()
        content.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self._content_widget = content
        
        self._content_layout = QVBoxLayout(content)
        self._content_layout.setContentsMargins(10, 10, 10, 10)
        self._content_layout.setSpacing(10)
        
        outer.addWidget(content, 1)
    
    def _center_on_screen(self) -> None:
        """将窗口居中显示"""
        screen = QApplication.primaryScreen()
        if screen is not None:
            geo = screen.availableGeometry()
            self.move(geo.center() - self.rect().center())
    
    def _setup_window_flags(self) -> None:
        """设置窗口为无边框，便于绘制圆角"""
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    
    def _setup_title_bar(self) -> None:
        """设置自定义标题栏（含最小化/关闭按钮）"""
        self._title_bar = TitleBar(
            self._central_widget, title=self.windowTitle()
        )
        self._outer_layout.insertWidget(0, self._title_bar)
    
    def _setup_message_area(self) -> None:
        """设置消息显示区域"""
        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        
        # 消息容器
        self._message_container = QWidget()
        self._message_container.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        
        # 视口显式透明：避免渲染器跳过窗口背景（保证亚克力/暗色背景可见）
        self._scroll_area.viewport().setStyleSheet(
            "background-color: transparent;"
        )
        
        self._message_layout = QVBoxLayout(self._message_container)
        self._message_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._message_layout.setSpacing(Sizes.MESSAGE_SPACING)
        self._message_layout.setContentsMargins(10, 10, 10, 10)
        
        self._scroll_area.setWidget(self._message_container)
        self._content_layout.addWidget(self._scroll_area, 1)
    
    def _setup_input_area(self) -> None:
        """设置输入区域"""
        input_widget = QWidget()
        input_widget.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        
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
        self._content_layout.addWidget(input_widget, 0)
    
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
    
    def showEvent(self, event: Any) -> None:
        """窗口首次显示时关闭系统默认圆角（保持自绘圆角的干净半透明背景）"""
        super().showEvent(event)
        if not self._region_applied:
            self._region_applied = True
            self._apply_window_region()
            self.update()

    def _apply_window_region(self) -> None:
        """
        关闭 Windows 系统默认圆角，避免与自绘圆角叠加出方框。

        窗口背景由 paintEvent 自绘为半透明圆角（圆角外透明），
        不使用系统毛玻璃 blur，因此四角保持干净。
        """
        if sys.platform != "win32":
            return
        try:
            import ctypes
            hwnd = int(self.winId())
            dwmapi = ctypes.windll.dwmapi
            DWMWA_WINDOW_CORNER_PREFERENCE = 33
            DWMWCP_DONOTROUND = 1
            dwmapi.DwmSetWindowAttribute(
                ctypes.c_void_p(hwnd),
                DWMWA_WINDOW_CORNER_PREFERENCE,
                ctypes.byref(ctypes.c_int(DWMWCP_DONOTROUND)),
                ctypes.sizeof(ctypes.c_int),
            )
        except Exception:
            pass

    def paintEvent(self, event: Any) -> None:
        """绘制圆角半透明窗口背景（圆角外透明，形成干净圆角）"""
        super().paintEvent(event)
        
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, Sizes.WINDOW_RADIUS, Sizes.WINDOW_RADIUS)
        
        bg = QColor(Colors.WINDOW_BG)
        bg.setAlpha(Colors.WINDOW_BG_ALPHA)
        painter.fillPath(path, bg)
        painter.end()
    
    def closeEvent(self, event: Any) -> None:
        """窗口关闭时停止后台线程，避免残留"""
        if self._loading_timer is not None:
            self._loading_timer.stop()
        
        if self._worker is not None and self._worker.isRunning():
            self._worker.stop()
            self._worker.wait(1000)
        
        super().closeEvent(event)
    
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
        
        # 禁用输入
        self._input_text.setEnabled(False)
        
        # 显示加载指示器
        self._show_loading_indicator()
        
        # 创建AI工作线程（流式）
        self._worker = AIWorker(self._bot, user_input)
        self._worker.chunk_received.connect(self._on_chunk_received)
        self._worker.tool_event.connect(self._on_tool_event)
        self._worker.finished.connect(self._on_stream_finished)
        self._worker.error.connect(self._on_ai_error)
        self._worker.start()
    
    def _show_loading_indicator(self) -> None:
        """显示加载指示器"""
        self._loading_container = QWidget()
        self._loading_container.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        
        layout = QHBoxLayout(self._loading_container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        
        avatar = AvatarLabel(self._ai_avatar_path, default_text="?")
        
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
        
        layout.addWidget(avatar)
        layout.addWidget(bubble_container)
        layout.setAlignment(avatar, Qt.AlignmentFlag.AlignTop)
        layout.addStretch()
        
        self._message_layout.addWidget(self._loading_container)
        
        self._dot_count = 0
        self._loading_timer = QTimer(self)
        self._loading_timer.timeout.connect(self._update_loading_dots)
        self._loading_timer.start(500)
        
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
    
    def _on_tool_event(self, name: str, status: str) -> None:
        """处理工具调用事件：显示/更新工具状态气泡"""
        if status == 'start':
            label = QLabel(f"🔧 正在调用 {name} …")
            label.setStyleSheet("""
                QLabel {
                    color: #888888;
                    background-color: #232323;
                    border-radius: 6px;
                    padding: 3px 10px;
                    font-size: 9pt;
                }
            """)
            self._tool_labels[name] = label
            self._message_layout.addWidget(
                label, 0, Qt.AlignmentFlag.AlignLeft
            )
        else:
            # 工具调用完成：移除状态气泡（不再显示"完成"提示）
            label = self._tool_labels.pop(name, None)
            if label is not None:
                self._message_layout.removeWidget(label)
                label.deleteLater()
        QTimer.singleShot(50, self._scroll_to_bottom)

    def _finish_pending_tool_labels(self) -> None:
        """流结束/出错时清理未完成的工具气泡"""
        for name, label in list(self._tool_labels.items()):
            self._message_layout.removeWidget(label)
            label.deleteLater()
        self._tool_labels.clear()

    def _on_chunk_received(self, chunk: str) -> None:
        """收到流式chunk"""
        if self._streaming_bubble is None:
            # 第一次收到chunk，移除加载指示器，创建流式气泡
            self._remove_loading_indicator()
            self._create_streaming_bubble()
        
        # 追加文本
        assert self._streaming_bubble is not None
        self._streaming_bubble.append_text(chunk)
        
        # 滚动到底部
        QTimer.singleShot(10, self._scroll_to_bottom)
    
    def _create_streaming_bubble(self) -> None:
        """创建流式输出气泡"""
        avatar_path = self._ai_avatar_path
        self._streaming_bubble = ChatBubble(
            "",  # 初始为空
            is_user=False,
            avatar_path=avatar_path,
            is_streaming=True
        )
        
        self._streaming_container = QWidget()
        self._streaming_container.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        
        layout = QHBoxLayout(self._streaming_container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        layout.addWidget(self._streaming_bubble)
        layout.addStretch()
        
        self._message_layout.addWidget(self._streaming_container)
        QTimer.singleShot(50, self._scroll_to_bottom)
    
    def _on_stream_finished(self) -> None:
        """流式输出完成"""
        self._streaming_bubble = None
        self._streaming_container = None
        self._finish_pending_tool_labels()
        
        # 恢复输入
        self._input_text.setEnabled(True)
        self._input_text.setFocus()
        self._worker = None
    
    def _on_ai_error(self, error_msg: str) -> None:
        """处理AI错误"""
        self._remove_loading_indicator()
        self._finish_pending_tool_labels()
        
        # 如果已经有流式气泡，先清理
        if self._streaming_bubble:
            self._streaming_bubble = None
            self._streaming_container = None
        
        self.add_message(f"错误：{error_msg}", is_user=False)
        
        self._input_text.setEnabled(True)
        self._input_text.setFocus()
        self._worker = None
    
    def add_message(self, text: str, is_user: bool = False) -> None:
        """
        添加消息到聊天界面
        """
        avatar_path = None if is_user else self._ai_avatar_path
        bubble = ChatBubble(text, is_user, avatar_path)
        
        container = QWidget()
        container.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        if is_user:
            layout.addStretch()
            layout.addWidget(bubble)
        else:
            layout.addWidget(bubble)
            layout.addStretch()
        
        self._message_layout.addWidget(container)
        
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
        """调整气泡最大宽度以适应窗口变化（气泡本身按内容自适应）"""
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
            
            for child in container.children():
                if isinstance(child, ChatBubble):
                    # 外层气泡（含头像）最大宽度 = 内容上限 + 头像 + 间距
                    child.setMaximumWidth(
                        max_width + Sizes.AVATAR_SIZE + 8
                    )
                    for sub_child in child.children():
                        if isinstance(sub_child, QFrame) and sub_child.layout():
                            sub_child.setMaximumWidth(max_width)


# ============================================================================
# 应用程序入口
# ============================================================================

def create_dark_palette() -> QPalette:
    """创建暗色调色板"""
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
    """获取头像文件路径"""
    script_dir = Path(__file__).parent.absolute()
    avatar_path = script_dir / relative_path
    
    if avatar_path.exists():
        return str(avatar_path)
    else:
        print(f"警告: 未找到头像文件: {avatar_path}")
        return None


class MockBot:
    """模拟AI"""
    
    @staticmethod
    def get_response_stream(text: str):
        import time
        response = f"这是对 '{text}' 的模拟回复，我会一个字一个字地显示出来。"
        for char in response:
            yield char
            time.sleep(0.05)  # 模拟打字效果


def main() -> int:
    """应用程序入口函数"""
    app = QApplication(sys.argv)
    
    app.setStyle("Fusion")
    app.setPalette(create_dark_palette())
    app.setFont(QFont(Fonts.FAMILY, Fonts.SIZE_MAIN))
    
    avatar_path = get_avatar_path()
    window = ChatWindow(
        bot=MockBot(),
        ai_avatar_path=avatar_path
    )
    window.show()
    
    return sys.exit(app.exec())


if __name__ == "__main__":
    sys.exit(main())