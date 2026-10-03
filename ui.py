"""
UI模块
"""

import os
import re
import sys
from typing import Optional, Any, Dict, List, Union
from pathlib import Path

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QTextEdit, QScrollArea, QFrame, QLabel,
    QSizePolicy, QPushButton, QToolButton, QWidgetAction, QMenu
)
from PySide6.QtCore import (
    Qt, QTimer, QThread, Signal, QObject, QSize, QPoint, QPointF, QRectF, QRect
)
from PySide6.QtGui import (
    QFont, QPalette, QColor, QPixmap, QPainter, QPainterPath, QPen, QIcon
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
    
    # 输入区按钮 / 表情包
    SEND_BUTTON_BG = "#8B5CF6"          # 发送按钮常态
    SEND_BUTTON_BG_HOVER = "#9F75F8"    # 发送按钮悬停
    SEND_BUTTON_BG_DISABLED = "#3A3A3A" # 输入为空时
    SEND_BUTTON_ICON = "#FFFFFF"
    EMOJI_BUTTON_HOVER = "#3D3D3D"      # 表情按钮悬停底色
    EMOJI_BUTTON_TEXT = "#BBBBBB"
    EMOJI_BUTTON_TEXT_HOVER = "#FFFFFF"
    EMOJI_PANEL_BG = "#232323"          # 表情选择面板
    EMOJI_PANEL_BORDER = "#3D3D3D"
    EMOJI_ITEM_HOVER = "#3A3A3A"        # 单个表情悬停
    EMOJI_ITEM_TEXT = "#CCCCCC"
    EMOJI_IMAGE_BG = "#2A2A2A"          # 表情图片气泡底色
    
    # 亚克力透明度（0-255）
    WINDOW_BG_ALPHA = 225       # 窗口背景透明度
    INPUT_BG_ALPHA = 175        # 输入框背景透明度


class Sizes:
    """尺寸常量"""
    WINDOW_WIDTH = 960
    WINDOW_HEIGHT = 768   # 5:4 比例 (960 * 4 / 5 = 768)
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
    
    # 输入区按钮
    SEND_BUTTON_SIZE = 34       # 发送按钮直径
    EMOJI_BUTTON_SIZE = 30      # 表情按钮直径
    
    # 表情包
    EMOJI_MESSAGE_SIZE = 160    # 聊天区表情图显示边长
    EMOJI_THUMB_SIZE = 88       # 选择面板缩略图边长
    EMOJI_GRID_COLUMNS = 4      # 选择面板每行个数
    EMOJI_PANEL_MAX_HEIGHT = 300  # 选择面板最大高度


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
    内容既可以是文字，也可以是表情包图片（emoji_path 非空时）。
    show_avatar=False 时只显示气泡本身（用于同一侧连续多条消息）。
    """
    
    def __init__(
        self, 
        text: str, 
        is_user: bool = False,
        avatar_path: Optional[str] = None,
        parent: Optional[QWidget] = None,
        is_streaming: bool = False,  # 新增：是否为流式输出
        emoji_path: Optional[str] = None,
        emoji_name: str = "",
        show_avatar: bool = True
    ):
        super().__init__(parent)
        self._text = text
        self._is_user = is_user
        self._avatar_path = avatar_path
        self._is_streaming = is_streaming
        self._emoji_path = emoji_path
        self._emoji_name = emoji_name
        self._show_avatar = show_avatar
        self._label = None  # 保存label引用以便更新
        
        self._setup_ui()
    
    def _setup_ui(self) -> None:
        """初始化UI"""
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(8)
        
        # 创建气泡
        bubble = self._create_bubble()
        
        # 创建头像（同侧连续消息里可以不显示）
        if self._show_avatar:
            if self._is_user:
                avatar = AvatarLabel(self._avatar_path, default_text="我")
            else:
                avatar = AvatarLabel(self._avatar_path, default_text="?")
        else:
            avatar = None
        
        # 布局排列（bubble 加 stretch factor，使气泡背景包住文本、宽度随内容）
        if self._is_user:
            main_layout.addWidget(bubble, 1)
            if avatar is not None:
                main_layout.addWidget(avatar)
                main_layout.setAlignment(avatar, Qt.AlignmentFlag.AlignTop)
        else:
            if avatar is not None:
                main_layout.addWidget(avatar)
                main_layout.setAlignment(avatar, Qt.AlignmentFlag.AlignTop)
            main_layout.addWidget(bubble, 1)
        
        # 水平方向不强制拉伸：气泡宽度随文本内容自适应（上限由 _adjust_bubble_widths 限定）
        self.setSizePolicy(
            QSizePolicy.Policy.Maximum,
            QSizePolicy.Policy.Minimum
        )
    
    def _create_bubble(self) -> QFrame:
        """
        创建消息气泡
        
        文字消息显示文本，表情包消息显示图片。
        
        Returns:
            气泡容器
        """
        container = QFrame()
        is_picture = bool(self._emoji_path)
        
        padding_h = 10 if is_picture else Sizes.BUBBLE_PADDING_H
        padding_v = 10 if is_picture else Sizes.BUBBLE_PADDING_V
        
        layout = QVBoxLayout(container)
        layout.setContentsMargins(padding_h, padding_v, padding_h, padding_v)
        
        if is_picture:
            # 表情包：直接显示图片，不显示文字
            layout.addWidget(self._create_emoji_label())
            container.setStyleSheet(f"""
                QFrame {{
                    background-color: {Colors.EMOJI_IMAGE_BG};
                    border-radius: 10px;
                    border: none;
                }}
            """)
            container.setMaximumWidth(
                Sizes.EMOJI_MESSAGE_SIZE + padding_h * 2 + 8
            )
            return container
        
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
    
    def _create_emoji_label(self) -> QLabel:
        """创建表情包图片标签（等比缩放并居中）"""
        label = QLabel()
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet("background-color: transparent; border: none;")
        label.setFixedSize(Sizes.EMOJI_MESSAGE_SIZE, Sizes.EMOJI_MESSAGE_SIZE)
        
        pixmap = QPixmap(self._emoji_path or "")
        if not pixmap.isNull():
            scaled = pixmap.scaled(
                Sizes.EMOJI_MESSAGE_SIZE, Sizes.EMOJI_MESSAGE_SIZE,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            label.setPixmap(scaled)
            if self._emoji_name:
                label.setToolTip(self._emoji_name)
        else:
            label.setText("🖼")
            label.setStyleSheet(f"""
                QLabel {{
                    color: {Colors.EMOJI_ITEM_TEXT};
                    background-color: transparent;
                    font-size: 24pt;
                }}
            """)
        
        return label
    
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
    # 表情包事件信号：name, path
    emoji_event = Signal(str, str)
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
        - agent事件元组 ('text', ...) / ('tool', {...}) / ('emoji', {...}) / ('done', ...)
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
                    elif kind == 'emoji':
                        # 发送表情包事件到主线程
                        self.emoji_event.emit(
                            payload.get('name', ''),
                            payload.get('path', '')
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

        # 关闭按钮（高亮颜色与最小化一致）
        self._close_button = TitleBarButton(
            "✕", hover_color=Colors.TITLEBAR_BG_HOVER, tooltip="关闭", parent=self
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
# 表情包（贴纸）
# ============================================================================

# 不参与表情包的图片（头像与界面资源）
_EMOJI_SKIP_PREFIXES = ("_", ".")
_EMOJI_AVATAR_NAME = "CastoriceAvatar"
_EMOJI_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}


def scan_emoji_dir(image_dir: Union[str, Path] = "Image") -> List[Dict[str, str]]:
    """
    扫描表情包目录

    文件名主干即表情包标签名（跳过头像与 _ 开头的界面资源）。

    Args:
        image_dir: 图片目录

    Returns:
        [{'name': 标签名, 'path': 图片绝对路径}, ...]
    """
    directory = Path(image_dir)
    if not directory.exists():
        return []
    
    items: List[Dict[str, str]] = []
    for filepath in sorted(directory.iterdir()):
        if not filepath.is_file():
            continue
        if filepath.suffix.lower() not in _EMOJI_SUFFIXES:
            continue
        if filepath.name.startswith(_EMOJI_SKIP_PREFIXES):
            continue
        if filepath.stem == _EMOJI_AVATAR_NAME:
            continue
        items.append({'name': filepath.stem, 'path': str(filepath)})
    
    return items


def find_emoji_dir(base_dir: Optional[Path] = None) -> Path:
    """定位 Image 目录（以本文件所在目录为基准，避免受启动路径影响）"""
    root = base_dir or Path(__file__).parent.absolute()
    return root / "Image"


def load_scaled_pixmap(
    path: str,
    size: int,
    device_ratio: float = 1.0
) -> QPixmap:
    """
    加载图片并按比例缩放到指定正方形边长内

    Args:
        path: 图片路径
        size: 目标边长（像素）
        device_ratio: 设备像素比（高分屏下更清晰）

    Returns:
        缩放后的图片；加载失败返回空 QPixmap
    """
    pixmap = QPixmap(path)
    if pixmap.isNull():
        return pixmap
    
    target = max(1, int(size * device_ratio))
    return pixmap.scaled(
        target, target,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation
    )


class InputIconButton(QPushButton):
    """
    输入区圆形图标按钮（表情包按钮 / 发送按钮）

    使用 QPainter 自绘图标，不依赖字体字形与 emoji 字体回退：
    - glyph="send"  ：向上箭头（发送）
    - glyph="smile" ：笑脸（打开表情包面板）

    可设置 accent=True 使常态为高亮实心底色（发送按钮）。
    """

    _STROKE_W = 1.8

    def __init__(
        self,
        glyph: str,
        tooltip: str = "",
        size: int = Sizes.SEND_BUTTON_SIZE,
        accent: bool = False,
        parent: Optional[QWidget] = None
    ):
        super().__init__("", parent)
        self._glyph = glyph
        self._accent = accent
        self._hovered = False
        
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFlat(True)
        self.setStyleSheet("background-color: transparent; border: none;")
        if tooltip:
            self.setToolTip(tooltip)
    
    # ------------------------------------------------------------------------
    # 事件
    # ------------------------------------------------------------------------
    
    def enterEvent(self, event: Any) -> None:
        self._hovered = True
        self.update()
        super().enterEvent(event)
    
    def leaveEvent(self, event: Any) -> None:
        self._hovered = False
        self.update()
        super().leaveEvent(event)
    
    def setEnabled(self, enabled: bool) -> None:  # noqa: N802 (Qt 命名)
        super().setEnabled(enabled)
        self.update()
    
    # ------------------------------------------------------------------------
    # 绘制
    # ------------------------------------------------------------------------
    
    def _background_color(self) -> Optional[QColor]:
        """按状态返回底色；None 表示不画底色"""
        if not self.isEnabled():
            return QColor(Colors.SEND_BUTTON_BG_DISABLED) if self._accent else None
        
        if self._accent:
            color = Colors.SEND_BUTTON_BG_HOVER if (
                self._hovered or self.isDown()
            ) else Colors.SEND_BUTTON_BG
            return QColor(color)
        
        if self._hovered or self.isDown():
            return QColor(Colors.EMOJI_BUTTON_HOVER)
        return None
    
    def _icon_color(self) -> str:
        """按状态返回图标颜色"""
        if not self.isEnabled():
            return "#777777" if self._accent else "#555555"
        if self._accent:
            return Colors.SEND_BUTTON_ICON
        return (
            Colors.EMOJI_BUTTON_TEXT_HOVER
            if self._hovered else Colors.EMOJI_BUTTON_TEXT
        )
    
    def paintEvent(self, event: Any) -> None:
        """自绘背景与图标"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        rect = QRectF(self.rect())
        
        # 1. 圆形底色
        bg = self._background_color()
        if bg is not None:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(bg)
            painter.drawEllipse(rect)
        
        # 2. 图标
        color = QColor(self._icon_color())
        painter.setPen(QPen(
            color, self._STROKE_W,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin
        ))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        
        if self._glyph == "send":
            self._draw_send(painter, rect)
        elif self._glyph == "smile":
            self._draw_smile(painter, rect)
        
        painter.end()
    
    def _draw_send(self, painter: QPainter, rect: QRectF) -> None:
        """绘制向上箭头（发送）"""
        cx = rect.center().x()
        cy = rect.center().y()
        half_h = rect.height() * 0.20
        half_w = rect.width() * 0.17
        
        # 箭头竖线
        painter.drawLine(
            QPointF(cx, cy - half_h),
            QPointF(cx, cy + half_h)
        )
        # 箭头两侧斜线
        painter.drawLine(
            QPointF(cx - half_w, cy - half_h * 0.25),
            QPointF(cx, cy - half_h)
        )
        painter.drawLine(
            QPointF(cx + half_w, cy - half_h * 0.25),
            QPointF(cx, cy - half_h)
        )
    
    def _draw_smile(self, painter: QPainter, rect: QRectF) -> None:
        """绘制笑脸（表情包面板入口）"""
        radius = min(rect.width(), rect.height()) * 0.33
        center = rect.center()
        
        # 脸
        painter.drawEllipse(center, radius, radius)
        
        # 眼睛
        eye_dx = radius * 0.42
        eye_dy = radius * 0.28
        eye_r = max(0.9, radius * 0.10)
        painter.setBrush(QColor(self._icon_color()))
        painter.drawEllipse(
            QPointF(center.x() - eye_dx, center.y() - eye_dy), eye_r, eye_r
        )
        painter.drawEllipse(
            QPointF(center.x() + eye_dx, center.y() - eye_dy), eye_r, eye_r
        )
        painter.setBrush(Qt.BrushStyle.NoBrush)
        
        # 嘴（下半圆弧）
        mouth_w = radius * 1.05
        mouth_h = radius * 0.95
        mouth_rect = QRectF(
            center.x() - mouth_w / 2,
            center.y() - mouth_h / 2 + radius * 0.10,
            mouth_w, mouth_h
        )
        painter.drawArc(mouth_rect, 200 * 16, 140 * 16)


class EmojiPicker(QMenu):
    """
    表情包选择面板（弹出菜单）

    点击输入框右侧的笑脸按钮弹出，网格展示 Image 目录下的所有表情包，
    点击某个表情即把该表情包作为一条消息发出。

    信号:
        emoji_selected(str, str): (标签名, 图片路径)
    """

    emoji_selected = Signal(str, str)

    def __init__(
        self,
        emojis: List[Dict[str, str]],
        parent: Optional[QWidget] = None,
        columns: int = Sizes.EMOJI_GRID_COLUMNS
    ):
        super().__init__(parent)
        self._columns = max(1, columns)
        self._buttons: List[QPushButton] = []
        self._empty_label: Optional[QLabel] = None
        
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setStyleSheet(f"""
            QMenu {{
                background-color: {Colors.EMOJI_PANEL_BG};
                border: 1px solid {Colors.EMOJI_PANEL_BORDER};
                border-radius: 10px;
                padding: 8px;
            }}
            QScrollArea {{
                border: none;
                background-color: transparent;
            }}
        """)
        
        self._build(emojis)
    
    def _build(self, emojis: List[Dict[str, str]]) -> None:
        """构建面板内容"""
        container = QWidget(self)
        container.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        container.setStyleSheet("background-color: transparent;")
        
        grid = QGridLayout(container)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(6)
        
        if not emojis:
            self._empty_label = QLabel(
                "Image 目录里还没有表情包\n"
                "放入图片后重启即可（文件名即表情名称）"
            )
            self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._empty_label.setStyleSheet(f"""
                QLabel {{
                    color: {Colors.EMOJI_ITEM_TEXT};
                    padding: 18px 10px;
                    font-size: 9pt;
                }}
            """)
            grid.addWidget(self._empty_label, 0, 0)
        else:
            for index, item in enumerate(emojis):
                button = self._create_item(item)
                row, column = divmod(index, self._columns)
                grid.addWidget(button, row, column)
                self._buttons.append(button)
        
        # 放在可滚动区域里，表情包变多也不会撑爆面板
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        scroll.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        scroll.viewport().setStyleSheet("background-color: transparent;")
        scroll.setWidget(container)
        
        # 计算面板尺寸：不超过 N 列宽度与最大高度
        item = Sizes.EMOJI_THUMB_SIZE + 8
        width = self._columns * item + (self._columns + 1) * 6 + 16
        if not emojis:
            width = 260
        scroll.setFixedWidth(width)
        
        rows = max(1, (len(emojis) + self._columns - 1) // self._columns)
        height = min(
            rows * item + (rows + 1) * 6,
            Sizes.EMOJI_PANEL_MAX_HEIGHT
        )
        scroll.setFixedHeight(max(120, height))
        
        action = QWidgetAction(self)
        action.setDefaultWidget(scroll)
        self.addAction(action)
    
    def _create_item(self, item: Dict[str, str]) -> QPushButton:
        """创建单个表情按钮（缩略图，加载失败回退为名称文字）"""
        button = QPushButton()
        button.setFixedSize(Sizes.EMOJI_THUMB_SIZE, Sizes.EMOJI_THUMB_SIZE)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setToolTip(item['name'])
        button.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                border: 1px solid transparent;
                border-radius: 8px;
                color: {Colors.EMOJI_ITEM_TEXT};
                font-size: 9pt;
            }}
            QPushButton:hover {{
                background-color: {Colors.EMOJI_ITEM_HOVER};
                border: 1px solid {Colors.HIGHLIGHT};
            }}
        """)
        
        pixmap = load_scaled_pixmap(
            item['path'],
            Sizes.EMOJI_THUMB_SIZE,
            min(2.0, float(self.devicePixelRatioF() or 1.0))
        )
        if pixmap.isNull():
            button.setText(item['name'])
        else:
            button.setIcon(QIcon(pixmap))
            button.setIconSize(QSize(
                Sizes.EMOJI_THUMB_SIZE, Sizes.EMOJI_THUMB_SIZE
            ))
        
        button.clicked.connect(
            lambda _=False, n=item['name'], p=item['path']:
            self._on_item_clicked(n, p)
        )
        
        return button
    
    def _on_item_clicked(self, name: str, path: str) -> None:
        """点击表情：发出信号并收起面板"""
        self.close()
        self.emoji_selected.emit(name, path)


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
    
    # 表情包标记：[表情:名称]，同时兼容中文方括号与全角冒号
    EMOJI_TAG_RE = re.compile(
        r"[\[【]\s*(?:表情包?|贴纸|emoji|sticker)\s*[:：]\s*([^\]】\n]{1,24}?)\s*[\]】]",
        re.IGNORECASE
    )
    # 标记前缀（用于流式解析：判断缓冲是否「正好停在标记起始处」）
    # 注意：外层开括号不加 \Z，让正则引擎在最后一个满足条件的开括号处闭合，
    # 这样 "你好[表" 能命中，而 "重来[表情:脸红]再想想" 不会误判。
    EMOJI_TAG_PREFIX_RE = re.compile(
        r"[\[【]\s*(?:表|表情|表情包|贴|贴纸|e|em|emo|emoj|emoji|s|st|sti|stic|stick|sticke|sticker)"
        r"\s*[:：]?\s*\Z",
        re.IGNORECASE
    )
    # 标记的开括号
    EMOJI_TAG_OPENERS = ("[", "【")
    # 标记关键字（用于定位标记起点）
    EMOJI_TAG_MARKERS = ("[表情", "[贴纸", "[emoji", "[sticker",
                         "【表情", "【贴纸", "【emoji", "【sticker")
    # 折叠因移除标记而多出来的连续空格
    MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")
    # 暂存长度上限：超出的普通文本立即显示，避免正文被卡住
    EMOJI_BUFFER_LIMIT = 40
    
    def __init__(
        self, 
        bot: Any, 
        ai_avatar_path: Optional[str] = None,
        emoji_dir: Optional[Union[str, Path]] = None
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
        self._typewriter_chars: List[str] = []   # 打字机待显示字符队列
        self._typewriter_timer: Optional[QTimer] = None
        # 表情包
        self._emoji_dir = emoji_dir or find_emoji_dir()
        self._emojis: List[Dict[str, str]] = scan_emoji_dir(self._emoji_dir)
        self._emoji_map: Dict[str, str] = {
            item['name']: item['path'] for item in self._emojis
        }
        self._emoji_picker: Optional[EmojiPicker] = None
        self._emoji_parse_buffer: str = ""       # 流式解析时暂存的疑似标记文本
        self._ai_emoji_shown: int = 0            # 本轮AI已发出的表情包数量（每轮最多1个）
        
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
        """设置输入区域：多行输入框 + 右下角表情包/发送按钮"""
        input_widget = QWidget()
        input_widget.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        
        layout = QVBoxLayout(input_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        
        # 输入框（回车发送、Shift+Enter 换行由 eventFilter 处理，说明放在按钮提示里）
        self._input_text = QTextEdit()
        self._input_text.setPlaceholderText("输入消息…")
        self._input_text.setMaximumHeight(Sizes.INPUT_MAX_HEIGHT)
        self._input_text.setMinimumHeight(Sizes.INPUT_MIN_HEIGHT)
        self._input_text.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self._input_text.installEventFilter(self)
        self._input_text.textChanged.connect(self._on_input_changed)
        
        # 底部按钮行（右对齐）：表情包按钮 + 发送按钮
        button_row = QWidget()
        button_row.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        button_layout = QHBoxLayout(button_row)
        button_layout.setContentsMargins(0, 0, 2, 0)
        button_layout.setSpacing(6)
        
        self._emoji_button = InputIconButton(
            "smile",
            tooltip="表情包（选中后插入输入框，可继续补充文字）",
            size=Sizes.EMOJI_BUTTON_SIZE,
            parent=button_row
        )
        self._emoji_button.clicked.connect(self._toggle_emoji_picker)
        
        self._send_button = InputIconButton(
            "send",
            tooltip="发送\nEnter 发送 · Shift+Enter 换行",
            size=Sizes.SEND_BUTTON_SIZE,
            accent=True,
            parent=button_row
        )
        self._send_button.clicked.connect(self._send_message)
        self._send_button.setEnabled(False)  # 输入为空时不可点
        
        button_layout.addStretch()
        button_layout.addWidget(self._emoji_button)
        button_layout.addWidget(self._send_button)
        
        layout.addWidget(self._input_text)
        layout.addWidget(button_row)
        self._content_layout.addWidget(input_widget, 0)
    
    def _on_input_changed(self) -> None:
        """输入内容变化：空内容或AI回复中时禁用发送按钮"""
        has_text = (
            self._input_text.isEnabled()
            and bool(self._input_text.toPlainText().strip())
        )
        if self._send_button.isEnabled() != has_text:
            self._send_button.setEnabled(has_text)
    
    def _setup_timer(self) -> None:
        """设置定时器"""
        self._resize_timer = QTimer()
        self._resize_timer.setSingleShot(True)
        self._resize_timer.timeout.connect(self._adjust_bubble_widths)
        
        # 打字机定时器：逐字显示AI回复，实现流式输出效果
        self._typewriter_timer = QTimer(self)
        self._typewriter_timer.setInterval(20)
        self._typewriter_timer.timeout.connect(self._typewriter_tick)
    
    # ------------------------------------------------------------------------
    # 事件处理
    # ------------------------------------------------------------------------
    
    def eventFilter(self, obj: QObject, event: Any) -> bool:
        """
        事件过滤器
        
        处理输入框的键盘事件：Enter 发送，Shift+Enter 换行
        （按键说明同样写在发送按钮的 Tooltip 中）。
        """
        if obj == self._input_text and event.type() == event.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    return False  # 交给 QTextEdit：插入换行
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
        if self._typewriter_timer is not None:
            self._typewriter_timer.stop()
        
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
        
        self._hide_emoji_picker()
        self._input_text.clear()
        self.add_message(user_input, is_user=True)
        
        # 禁用输入
        self._input_text.setEnabled(False)
        self._send_button.setEnabled(False)
        
        # 重置流式解析状态
        self._emoji_parse_buffer = ""
        self._ai_emoji_shown = 0
        
        # 显示加载指示器
        self._show_loading_indicator()
        
        # 创建AI工作线程（流式）
        self._worker = AIWorker(self._bot, user_input)
        self._worker.chunk_received.connect(self._on_chunk_received)
        self._worker.tool_event.connect(self._on_tool_event)
        self._worker.emoji_event.connect(self._on_emoji_event)
        self._worker.finished.connect(self._on_stream_finished)
        self._worker.error.connect(self._on_ai_error)
        self._worker.start()
    
    # ------------------------------------------------------------------------
    # 表情包
    # ------------------------------------------------------------------------
    
    def _toggle_emoji_picker(self) -> None:
        """展开/收起表情包面板"""
        if self._emoji_picker is not None and self._emoji_picker.isVisible():
            self._hide_emoji_picker()
            return
        self._show_emoji_picker()
    
    def _show_emoji_picker(self) -> None:
        """在输入区上方弹出表情包面板（右边缘与发送按钮对齐）"""
        if self._emoji_picker is None:
            self._emoji_picker = EmojiPicker(self._emojis, self)
            self._emoji_picker.emoji_selected.connect(self._on_emoji_selected)
        
        self._emoji_picker.adjustSize()
        size_hint = self._emoji_picker.sizeHint()
        width = max(size_hint.width(), self._emoji_picker.width())
        height = max(size_hint.height(), self._emoji_picker.height())
        
        # 右对齐发送按钮，放在按钮行上方
        anchor = self._send_button.mapToGlobal(
            self._send_button.rect().topRight()
        )
        x = anchor.x() - width
        y = anchor.y() - height - 6
        
        # 防止面板超出屏幕
        screen = QApplication.screenAt(anchor) or QApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            x = max(area.left() + 4, min(x, area.right() - width - 4))
            y = max(area.top() + 4, min(y, area.bottom() - height - 4))
        
        self._emoji_picker.popup(QPoint(x, y))
    
    def _hide_emoji_picker(self) -> None:
        """收起表情包面板"""
        if self._emoji_picker is not None and self._emoji_picker.isVisible():
            self._emoji_picker.close()
    
    def _on_emoji_selected(self, name: str, path: str) -> None:
        """用户在面板里选了表情包：插入到输入框，等用户按 Enter 再发出"""
        self.insert_emoji(name, path)
    
    def insert_emoji(self, name: str, path: str) -> None:
        """
        把表情包标记插入输入框（不立即发送）

        插入后光标停在标记之后，用户可以在标记前后继续输入文字，
        按 Enter / 点发送按钮时才会连同文字一起发出。

        Args:
            name: 表情包名称
            path: 图片路径（保留参数以便调用方统一传递）
        """
        if not self._input_text.isEnabled():
            return  # AI 回复中，输入框暂不可用
        
        self._hide_emoji_picker()
        
        cursor = self._input_text.textCursor()
        cursor.insertText(f"[表情:{name}]")
        self._input_text.setTextCursor(cursor)
        self._input_text.setFocus()
        self._on_input_changed()
    
    def send_emoji(self, name: str, path: str) -> None:
        """
        兼容入口：把表情包插入输入框（等价于 insert_emoji）

        Args:
            name: 表情包名称
            path: 图片路径
        """
        self.insert_emoji(name, path)
    
    def _append_message_row(
        self,
        widgets: List[QWidget],
        is_user: bool,
        tight: bool = False
    ) -> QWidget:
        """
        把一组气泡按方向排成一行加入聊天区

        Args:
            widgets: 该行内的气泡（用户消息靠右，AI 消息靠左）
            is_user: 是否为用户消息
            tight: 是否使用更小行距（同一条消息拆出的多行之间用）

        Returns:
            行容器
        """
        container = QWidget()
        container.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        if tight:
            # 用下边距收窄行距，让同一条消息的多行看起来仍是一组
            container.setContentsMargins(
                0, 0, 0,
                max(0, Sizes.MESSAGE_SPACING - 6)
            )
        
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        
        if is_user:
            layout.addStretch()
            for widget in widgets:
                layout.addWidget(widget)
        else:
            for widget in widgets:
                layout.addWidget(widget)
            layout.addStretch()
        
        self._message_layout.addWidget(container)
        QTimer.singleShot(50, self._scroll_to_bottom)
        return container
    
    def add_emoji_message(
        self,
        name: str,
        path: str,
        is_user: bool = False,
        show_avatar: bool = True
    ) -> None:
        """把表情包作为一行消息加入聊天区"""
        avatar_path = None if is_user else self._ai_avatar_path
        bubble = ChatBubble(
            "", is_user, avatar_path,
            emoji_path=path, emoji_name=name,
            show_avatar=show_avatar
        )
        self._append_message_row([bubble], is_user)
    
    def _on_emoji_event(self, name: str, path: str) -> None:
        """AI 发来表情包事件：作为一张图片消息追加到聊天区（每轮最多一个）"""
        if not path or not os.path.exists(path):
            return
        self._remove_loading_indicator()  # 整轮可能只有表情包、没有文字
        if self._ai_emoji_shown >= 1:
            return  # 本轮已经发过表情包，不再重复
        self._ai_emoji_shown += 1
        # 文字部分先全部落地，再显示图片，顺序更自然
        self._flush_typewriter()
        self.add_emoji_message(name, path, is_user=False)
    
    # ------------------------------------------------------------------------
    # 流式文本中的表情包标记解析
    # ------------------------------------------------------------------------
    
    def _push_text(self, text: str) -> None:
        """把已确认的文本加入打字机队列"""
        if not text:
            return
        assert self._typewriter_timer is not None
        self._typewriter_chars.extend(text)
        if not self._typewriter_timer.isActive():
            self._typewriter_timer.start()
    
    def _flush_typewriter(self) -> None:
        """把打字机队列里剩余的字符一次性补齐"""
        if self._streaming_bubble is not None and self._typewriter_chars:
            self._streaming_bubble.append_text("".join(self._typewriter_chars))
        self._typewriter_chars.clear()
        if self._typewriter_timer is not None:
            self._typewriter_timer.stop()
    
    def _feed_text(self, chunk: str) -> None:
        """
        解析 AI 流式文本中的表情包标记

        流式文本是一个字一个字到来的，标记会被拆成多段，因此需要暂存：
        - 普通文本：立即进入打字机队列显示
        - 遇到「[」「【」或「[表」这类可能是标记开头的片段：暂存等后续字符
        - 收到完整标记 [表情:开心]：把标记之前的文本显示出来，再插入图片
        - 本轮已有表情包时，多余的标记直接丢弃（保证每轮只有一个）
        """
        self._emoji_parse_buffer += chunk
        
        while self._emoji_parse_buffer:
            buffer = self._emoji_parse_buffer
            
            # 迟迟等不到结尾标记：按普通文本处理，避免正文被卡住
            if len(buffer) > self.EMOJI_BUFFER_LIMIT:
                self._push_text(buffer)
                self._emoji_parse_buffer = ""
                return
            
            match = self.EMOJI_TAG_RE.search(buffer)
            
            # 1. 缓冲末尾正好停在标记起始处（如 "[", "[表", "[表情:"）：
            #    标记之前的文本先显示，标记部分留下等后续字符。
            #    必须先把这类情况挑出来，否则 "[表" 匹配不到完整标记，
            #    会被当成普通文本提前显示出去。
            prefix = self._is_tag_prefix(buffer)
            if prefix is not None:
                if prefix > 0:
                    self._push_text(buffer[:prefix])
                    self._emoji_parse_buffer = buffer[prefix:]
                return
            
            marker_index = max(
                (buffer.rfind(marker) for marker in self.EMOJI_TAG_MARKERS),
                default=-1
            )
            
            # 2. 完整标记，且它之前没有可能是标记开头的碎片
            if match is not None and (
                marker_index < 0 or marker_index >= match.start()
            ):
                plain = buffer[:match.start()]
                name = match.group(1).strip()
                path = self._emoji_map.get(name)
                self._emoji_parse_buffer = buffer[match.end():]
                self._push_text(plain)
                
                if path is None:
                    # 名称对不上：原样显示，避免吞掉正文
                    self._push_text(match.group(0))
                elif self._ai_emoji_shown >= 1:
                    pass  # 本轮已发过表情包：丢弃多余标记，只保留文字
                else:
                    self._flush_typewriter()
                    self._ai_emoji_shown += 1
                    self.add_emoji_message(name, path, is_user=False)
                continue
            
            # 3. 标记尚未收完：保留标记部分，之前的文本先显示
            if marker_index >= 0:
                if marker_index > 0:
                    self._push_text(buffer[:marker_index])
                    self._emoji_parse_buffer = buffer[marker_index:]
                return
            
            # 4. 没有标记痕迹：全部直接显示
            self._push_text(buffer)
            self._emoji_parse_buffer = ""
            return
    
    def _is_tag_prefix(self, text: str) -> Optional[int]:
        r"""
        判断 text 的末尾是否正好停在标记起始处（尚未收完）

        例如 "你好[表"、"[表情"、"[表情：" 都会命中，
        返回标记起点下标；未命中返回 None。

        实现要点：只给「关键字……冒号」这段加 \Z 锚点，开括号不加，
        正则引擎于是会在最后一个满足条件的开括号处闭合，
        既能识别 "你好[表"，也不会误判 "重来[表情:脸红]再想想"
        （后者的开括号之后是完整标记加正文，不满足前缀模式）。
        单个开括号（"[" / "【"）关键字还没到，正则匹配不到，单独判断。
        """
        if not text:
            return None
        match = self.EMOJI_TAG_PREFIX_RE.search(text)
        if match is not None:
            return match.start()
        # 只有开括号到了：同样需要暂存，等关键字
        if text.endswith(self.EMOJI_TAG_OPENERS):
            tail = text[:-1]
            return len(tail)
        return None
    
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
        """收到流式chunk：解析表情包标记后，逐字加入打字机队列"""
        if self._streaming_bubble is None:
            # 第一次收到chunk，移除加载指示器，创建流式气泡
            self._remove_loading_indicator()
            self._create_streaming_bubble()
            self._adjust_bubble_widths()
        
        self._feed_text(chunk)
    
    def _typewriter_tick(self) -> None:
        """打字机：每个 tick 追加一个字符"""
        assert self._typewriter_timer is not None
        if self._streaming_bubble is None:
            self._typewriter_timer.stop()
            self._typewriter_chars.clear()
            return
        if self._typewriter_chars:
            self._streaming_bubble.append_text(self._typewriter_chars.pop(0))
        else:
            self._typewriter_timer.stop()
        self._scroll_to_bottom()
    
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
        # 收尾：把解析缓冲里剩下的一并显示
        if self._emoji_parse_buffer:
            self._push_text(self._emoji_parse_buffer)
            self._emoji_parse_buffer = ""
        
        # 停止打字机，并把剩余字符一次性补齐
        if self._typewriter_timer is not None:
            self._typewriter_timer.stop()
        if self._streaming_bubble is not None and self._typewriter_chars:
            self._streaming_bubble.append_text("".join(self._typewriter_chars))
        self._typewriter_chars.clear()
        
        # 整轮没有任何文字（例如只发了一个表情包）：撤掉空文字气泡
        if self._streaming_bubble is not None:
            label = self._streaming_bubble._label
            if label is not None and not label.text().strip():
                container = self._streaming_container
                if container is not None:
                    self._message_layout.removeWidget(container)
                    container.deleteLater()
        
        # 兜底：整轮既没有文字也没有表情包时，别把加载指示器留下
        if not self._ai_emoji_shown:
            self._remove_loading_indicator()
        
        self._streaming_bubble = None
        self._streaming_container = None
        self._finish_pending_tool_labels()
        
        # 恢复输入
        self._input_text.setEnabled(True)
        self._input_text.setFocus()
        self._on_input_changed()
        self._worker = None
    
    def _on_ai_error(self, error_msg: str) -> None:
        """处理AI错误"""
        if self._typewriter_timer is not None:
            self._typewriter_timer.stop()
        self._typewriter_chars.clear()
        self._emoji_parse_buffer = ""
        self._remove_loading_indicator()
        self._finish_pending_tool_labels()
        
        # 如果已经有流式气泡，先清理
        if self._streaming_bubble:
            self._streaming_bubble = None
            self._streaming_container = None
        
        self.add_message(f"错误：{error_msg}", is_user=False)
        
        self._input_text.setEnabled(True)
        self._input_text.setFocus()
        self._on_input_changed()
        self._worker = None
    
    def add_message(
        self,
        text: str,
        is_user: bool = False,
        single_emoji: Optional[bool] = None
    ) -> None:
        """
        添加消息到聊天界面

        文本里若含 [表情:名称] 标记，会把标记渲染成表情图，
        并且**文字与表情图分行显示**（各占一行，顺序与书写顺序一致）：
        「[表情:开心] 你好」会显示成 一张表情图（一行）+ 一条文字（一行）。

        Args:
            text: 消息文本
            is_user: 是否为用户消息
            single_emoji: 是否只保留一个表情包。
                默认 AI 消息为 True（每轮最多一个），用户消息为 False（用户自己发的不限制）。
        """
        if single_emoji is None:
            single_emoji = not is_user
        
        avatar_path = None if is_user else self._ai_avatar_path
        bubbles: List[QWidget] = []
        position = 0
        rendered = 0
        
        for match in self.EMOJI_TAG_RE.finditer(text or ""):
            name = match.group(1).strip()
            path = self._emoji_map.get(name)
            
            # 只保留一个表情包：多余的标记直接丢弃，文字照常显示
            if path and single_emoji and rendered >= 1:
                position = match.end()
                continue
            
            plain = text[position:match.start()].strip()
            if plain:
                bubbles.append(ChatBubble(plain, is_user, avatar_path))
            
            if path:
                bubbles.append(ChatBubble(
                    "", is_user, avatar_path,
                    emoji_path=path, emoji_name=name
                ))
                rendered += 1
            else:
                # 名称对不上：原样显示文字
                bubbles.append(ChatBubble(match.group(0), is_user, avatar_path))
            
            position = match.end()
        
        tail = (text or "")[position:].strip()
        if tail:
            bubbles.append(ChatBubble(tail, is_user, avatar_path))
        
        if not bubbles:
            return
        
        # 丢弃多余表情标记后可能留下连续空格，折叠一下
        for bubble in bubbles:
            if isinstance(bubble, ChatBubble) and bubble._label is not None:
                current = bubble._label.text()
                fixed = self.MULTI_SPACE_RE.sub(" ", current).strip()
                if fixed != current:
                    bubble._label.setText(fixed)
        
        self._append_message_rows(bubbles, is_user)
        QTimer.singleShot(100, self._adjust_bubble_widths)
    
    def _append_message_rows(
        self,
        bubbles: List[QWidget],
        is_user: bool,
        tight: bool = True
    ) -> None:
        """每条气泡各占一行加入聊天区（文字与表情图因此分行显示）"""
        for bubble in bubbles:
            self._append_message_row([bubble], is_user, tight=tight)
    
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
    """模拟AI（含表情包事件，便于离线预览界面效果）"""
    
    # 供演示的模拟回复（含内联表情包标记）
    _REPLIES = [
        "这是对 '{text}' 的模拟回复，我会一个字一个字地显示出来。[表情:开心]",
        "收到啦，[表情:卖萌] 人家一直在听你说呢。",
        "唔……[表情:无语] 你是认真的吗？",
    ]
    
    def __init__(self) -> None:
        self._turn = 0
    
    def get_response_stream(self, text: str):
        import time
        response = self._REPLIES[self._turn % len(self._REPLIES)].format(text=text)
        self._turn += 1
        for char in response:
            yield ('text', char)
            time.sleep(0.05)  # 模拟打字效果
        yield ('done', response)


def main() -> int:
    """应用程序入口函数"""
    app = QApplication(sys.argv)
    
    app.setStyle("Fusion")
    app.setPalette(create_dark_palette())
    app.setFont(QFont(Fonts.FAMILY, Fonts.SIZE_MAIN))
    
    avatar_path = get_avatar_path()
    window = ChatWindow(
        bot=MockBot(),
        ai_avatar_path=avatar_path,
        emoji_dir=find_emoji_dir()
    )
    window.show()
    
    return sys.exit(app.exec())


if __name__ == "__main__":
    sys.exit(main())