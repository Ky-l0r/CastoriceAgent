"""
UI模块
"""

import os
import re
import sys
import json
import math
from typing import Optional, Any, Dict, List, Tuple, Union, cast
from pathlib import Path

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QTextEdit, QScrollArea, QFrame, QLabel,
    QSizePolicy, QPushButton, QToolButton, QWidgetAction, QMenu,
    QGraphicsOpacityEffect, QRadioButton, QButtonGroup, QComboBox,
    QLineEdit, QCheckBox, QFileDialog
)
from PySide6.QtCore import (
    Qt, QTimer, QThread, Signal, QObject, QSize, QPoint, QPointF, QRectF, QRect,
    QPropertyAnimation, QEasingCurve
)
from PySide6.QtGui import (
    QFont, QFontMetrics, QPalette, QColor, QPixmap, QPainter, QPainterPath, QPen,
    QIcon, QGuiApplication, QAction, QActionGroup
)


# ============================================================================
# 主题系统（浅色偏紫 / 深色偏紫）
# ============================================================================

class ThemeMode:
    """显示模式"""
    SYSTEM = "system"   # 跟随系统
    LIGHT = "light"     # 浅色（白色偏紫）
    DARK = "dark"       # 深色（黑色偏紫）


def app_root() -> Path:
    """
    程序根目录

    打包成 exe 后返回 exe 所在目录（资源与配置都放在它旁边，
    方便用户直接修改 prompts / 表情包 / config.yaml）；
    源码运行时返回本文件所在目录。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).parent.resolve()


def system_prefers_dark() -> bool:
    """检测系统是否开启深色模式（Qt colorScheme 优先，Windows 注册表兜底）"""
    try:
        hints = QGuiApplication.styleHints()
        scheme = hints.colorScheme()
        name = str(scheme)
        if "Dark" in name:
            return True
        if "Light" in name:
            return False
    except Exception:
        pass
    if sys.platform == "win32":
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            )
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            winreg.CloseKey(key)
            return int(value) == 0
        except Exception:
            pass
    return False


def resolve_theme_mode(mode: str) -> str:
    """把显示模式（system/light/dark）解析为实际生效的主题（light/dark）"""
    if mode == ThemeMode.SYSTEM:
        return ThemeMode.DARK if system_prefers_dark() else ThemeMode.LIGHT
    return mode


class UiSettings:
    """
    界面设置（ui_settings.json）的读写

    同一个文件里既有显示模式（theme），也有界面模式（ui_mode），
    因此所有写入都是「读-改-写」，避免覆盖彼此的键。
    任何异常都静默降级：设置文件坏了不该让程序起不来。
    """

    THEME_KEY = "theme"
    MODE_KEY = "ui_mode"

    #: 用环境变量把设置文件指到别处（便携运行 / 多份配置并存时有用）
    ENV_PATH = "CASTORICE_UI_SETTINGS"

    @classmethod
    def _file(cls) -> Path:
        """设置文件路径（每次动态解析，兼容打包后的 exe 目录）"""
        override = os.environ.get(cls.ENV_PATH)
        if override:
            return Path(override).expanduser()
        return app_root() / "ui_settings.json"

    @classmethod
    def load(cls) -> Dict[str, Any]:
        """读取整份设置；文件缺失或损坏时返回空字典"""
        try:
            data = json.loads(cls._file().read_text(encoding="utf-8"))
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    @classmethod
    def save_values(cls, values: Dict[str, Any]) -> None:
        """合并写入若干键（保留文件里已有的其它键）"""
        data = cls.load()
        data.update(values)
        try:
            cls._file().write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass


class ThemeSettings:
    """显示模式设置的持久化（ui_settings.json，放在程序根目录旁边）"""

    @classmethod
    def _file(cls) -> Path:
        """设置文件路径（每次动态解析，兼容打包后的 exe 目录）"""
        return app_root() / "ui_settings.json"

    @classmethod
    def load(cls) -> str:
        raw = UiSettings.load().get(UiSettings.THEME_KEY, ThemeMode.SYSTEM)
        mode = str(raw).lower()
        if mode in (ThemeMode.SYSTEM, ThemeMode.LIGHT, ThemeMode.DARK):
            return mode
        return ThemeMode.SYSTEM

    @classmethod
    def save(cls, mode: str) -> None:
        UiSettings.save_values({UiSettings.THEME_KEY: mode})


class ModeSettings:
    """
    界面模式（chat/work/code/learn）在本地的记忆

    权威来源有两个：
    1. 内核 config.yaml 的 `mode` 键（跨进程恢复的真正依据）；
    2. ui_settings.json 的 ui_mode（内核不可用时的兜底，例如演示模式）。
    读取顺序是「config.yaml → ui_settings.json → chat」，写入时两者都尽量更新。
    """

    DEFAULT = "chat"

    @classmethod
    def load(cls) -> str:
        """读取上次使用的模式（非法值回退到 chat）"""
        mode = str(UiSettings.load().get(UiSettings.MODE_KEY, "")).strip().lower()
        return mode or cls.DEFAULT

    @classmethod
    def save(cls, mode: str) -> None:
        """记住当前模式（供下次启动恢复）"""
        name = str(mode or "").strip().lower()
        if not name:
            return
        UiSettings.save_values({UiSettings.MODE_KEY: name})


#: 界面模式的固定顺序与中文名（内核不可用时的兜底展示）
FALLBACK_MODES: Tuple[Dict[str, str], ...] = (
    {"name": "chat", "display_name": "聊天"},
    {"name": "work", "display_name": "工作"},
    {"name": "code", "display_name": "编程"},
    {"name": "learn", "display_name": "学习"},
)


def normalize_mode_name(name: Any) -> str:
    """把模式名归一化成合法值；未知值回退到 chat（失败安全，与内核同源）"""
    candidates = {item["name"] for item in FALLBACK_MODES}
    text = str(name or "").strip().lower()
    if text in candidates:
        return text
    return ModeSettings.DEFAULT


class _ColorsMeta(type):
    """Colors 的元类：让类属性访问按当前主题动态解析"""

    def __getattr__(cls, name: str) -> str:
        if name.startswith("__"):
            raise AttributeError(name)
        # 元类方法的 cls 在类型系统中是 _ColorsMeta（其子类），
        # 直接访问 Colors 的类属性会被 __getattr__ 自身推断为 str，
        # 这里 cast 成 Colors 的类对象以保留正确的属性类型。
        colors = cast("type[Colors]", cls)
        palette = colors._DARK if colors.current() == ThemeMode.DARK else colors._LIGHT
        if name in palette:
            return palette[name]
        raise AttributeError(f"Colors 没有属性 {name}")


class Colors(metaclass=_ColorsMeta):
    """
    颜色常量（按当前主题动态解析）

    所有颜色通过 Colors.XXX 访问，实际取值来自当前生效的主题调色板，
    因此切换主题后无需修改任何引用处。
    """

    # 深色（黑色偏紫）
    _DARK = {
        # 主窗口
        "WINDOW_BG": "#171420",
        # 输入框
        "INPUT_BG": "#241F33",
        "INPUT_TEXT": "#EFEBFA",
        "INPUT_BORDER": "#332C47",
        "INPUT_BORDER_FOCUS": "#8B5CF6",
        "INPUT_PLACEHOLDER": "#8A8299",
        # 消息气泡
        "USER_BUBBLE_BG": "#2C2444",
        "AI_BUBBLE_BG": "#221D30",
        "BUBBLE_TEXT": "#F1EDFA",
        "BUBBLE_BORDER": "transparent",
        # 加载指示器
        "LOADING_BUBBLE_BG": "#1E1A2A",
        "LOADING_DOT_COLOR": "#EFEBFA",
        # 滚动条
        "SCROLLBAR_BG": "#241F33",
        "SCROLLBAR_HANDLE": "#463C63",
        "SCROLLBAR_HANDLE_HOVER": "#5A4E7E",
        # 头像
        "AVATAR_BG": "#332C47",
        "AVATAR_BORDER": "#463C63",
        "AVATAR_TEXT": "#EFEBFA",
        # 高亮
        "HIGHLIGHT": "#8B5CF6",
        "HIGHLIGHT_TEXT": "#FFFFFF",
        # 标题栏（紫色不透明）
        "TITLEBAR_BG": "#5B34C9",
        "TITLEBAR_BG_HOVER": "#7C4DE8",
        "TITLEBAR_TEXT": "#FFFFFF",
        # 输入区按钮 / 表情包
        "SEND_BUTTON_BG": "#7C3AED",
        "SEND_BUTTON_BG_HOVER": "#8B5CF6",
        "SEND_BUTTON_BG_DISABLED": "#332C47",
        "SEND_BUTTON_ICON": "#FFFFFF",
        "EMOJI_BUTTON_HOVER": "#332C47",
        "EMOJI_BUTTON_TEXT": "#A69CB8",
        "EMOJI_BUTTON_TEXT_HOVER": "#EFEBFA",
        "EMOJI_PANEL_BG": "#1F1A2C",
        "EMOJI_PANEL_BORDER": "#332C47",
        "EMOJI_ITEM_HOVER": "#2C2444",
        "EMOJI_ITEM_TEXT": "#BDB4CC",
        "EMOJI_IMAGE_BG": "#221D30",
        # 工具状态气泡
        "TOOL_BG": "#1F1A2C",
        "TOOL_TEXT": "#8A8299",
        # 模式切换条（比标题栏深一档，形成层次）
        "MODE_BAR_BG": "#4A28A8",
        "MODE_TAB_BG_ACTIVE": "#FFFFFF",
        "MODE_TAB_TEXT_ACTIVE": "#5B34C9",
        "MODE_TAB_TEXT": "#D8CCFF",
        "MODE_TAB_TEXT_HOVER": "#FFFFFF",
        "MODE_TAB_BG_HOVER": "#6B41DA",
        # 右侧信息面板（Code / Learn 目录绑定）
        "PANEL_BG": "#1F1A2C",
        "PANEL_BORDER": "#332C47",
        "PANEL_TITLE": "#EFEBFA",
        "PANEL_TEXT": "#BDB4CC",
        "PANEL_MUTED": "#8A8299",
        "PANEL_KEY_BG": "#241F33",
        # 审批条（工具调用需要用户授权）
        "APPROVAL_BG": "#2A2136",
        "APPROVAL_BORDER": "#6D4AC4",
        "APPROVAL_TITLE": "#F3EEFF",
        "APPROVAL_TEXT": "#C7BEDB",
        "APPROVAL_MUTED": "#8A8299",
        "APPROVAL_ALLOW_BG": "#7C3AED",
        "APPROVAL_ALLOW_TEXT": "#FFFFFF",
        "APPROVAL_DENY_BG": "#3A3050",
        "APPROVAL_DENY_TEXT": "#D9D2EA",
    }

    # 浅色（白色偏紫）
    _LIGHT = {
        # 主窗口
        "WINDOW_BG": "#F4F1FB",
        # 输入框
        "INPUT_BG": "#FFFFFF",
        "INPUT_TEXT": "#2B2540",
        "INPUT_BORDER": "#E2DBF2",
        "INPUT_BORDER_FOCUS": "#7C3AED",
        "INPUT_PLACEHOLDER": "#9C93B4",
        # 消息气泡
        "USER_BUBBLE_BG": "#EDE6FC",
        "AI_BUBBLE_BG": "#FFFFFF",
        "BUBBLE_TEXT": "#2B2540",
        "BUBBLE_BORDER": "#E6DFF7",
        # 加载指示器
        "LOADING_BUBBLE_BG": "#FFFFFF",
        "LOADING_DOT_COLOR": "#5B4B8A",
        # 滚动条
        "SCROLLBAR_BG": "#F4F1FB",
        "SCROLLBAR_HANDLE": "#CFC7E4",
        "SCROLLBAR_HANDLE_HOVER": "#B6ABD4",
        # 头像
        "AVATAR_BG": "#EDE6FC",
        "AVATAR_BORDER": "#D9D0F0",
        "AVATAR_TEXT": "#6B5CA5",
        # 高亮
        "HIGHLIGHT": "#7C3AED",
        "HIGHLIGHT_TEXT": "#FFFFFF",
        # 标题栏（紫色不透明）
        "TITLEBAR_BG": "#8B5CF6",
        "TITLEBAR_BG_HOVER": "#A78BFA",
        "TITLEBAR_TEXT": "#FFFFFF",
        # 输入区按钮 / 表情包
        "SEND_BUTTON_BG": "#7C3AED",
        "SEND_BUTTON_BG_HOVER": "#8B5CF6",
        "SEND_BUTTON_BG_DISABLED": "#E2DBF2",
        "SEND_BUTTON_ICON": "#FFFFFF",
        "EMOJI_BUTTON_HOVER": "#EDE6FC",
        "EMOJI_BUTTON_TEXT": "#8A80A6",
        "EMOJI_BUTTON_TEXT_HOVER": "#4A3F6B",
        "EMOJI_PANEL_BG": "#FFFFFF",
        "EMOJI_PANEL_BORDER": "#E2DBF2",
        "EMOJI_ITEM_HOVER": "#F0EAFB",
        "EMOJI_ITEM_TEXT": "#4A3F6B",
        "EMOJI_IMAGE_BG": "#FFFFFF",
        # 工具状态气泡
        "TOOL_BG": "#F0EAFB",
        "TOOL_TEXT": "#6B5CA5",
        # 模式切换条（紫色实底，与标题栏同族但更深）
        "MODE_BAR_BG": "#7C4DE8",
        "MODE_TAB_BG_ACTIVE": "#FFFFFF",
        "MODE_TAB_TEXT_ACTIVE": "#6D28D9",
        "MODE_TAB_TEXT": "#EDE6FC",
        "MODE_TAB_TEXT_HOVER": "#FFFFFF",
        "MODE_TAB_BG_HOVER": "#9A76F0",
        # 右侧信息面板（Code / Learn 目录绑定）
        "PANEL_BG": "#FFFFFF",
        "PANEL_BORDER": "#E2DBF2",
        "PANEL_TITLE": "#4A3F6B",
        "PANEL_TEXT": "#6B5CA5",
        "PANEL_MUTED": "#9C93B4",
        "PANEL_KEY_BG": "#F7F4FD",
        # 审批条（工具调用需要用户授权）
        "APPROVAL_BG": "#F3EEFF",
        "APPROVAL_BORDER": "#C4B5FD",
        "APPROVAL_TITLE": "#4A3F6B",
        "APPROVAL_TEXT": "#5B4B8A",
        "APPROVAL_MUTED": "#8A80A6",
        "APPROVAL_ALLOW_BG": "#7C3AED",
        "APPROVAL_ALLOW_TEXT": "#FFFFFF",
        "APPROVAL_DENY_BG": "#EDE6FC",
        "APPROVAL_DENY_TEXT": "#4A3F6B",
    }

    _current = ThemeMode.SYSTEM  # 当前显示模式（system/light/dark）

    @classmethod
    def current(cls) -> str:
        """当前实际生效的主题名（light/dark）"""
        if cls._current == ThemeMode.SYSTEM:
            return ThemeMode.DARK if system_prefers_dark() else ThemeMode.LIGHT
        return cls._current

    @classmethod
    def set_mode(cls, mode: str) -> None:
        """切换显示模式（system/light/dark）"""
        if mode in (ThemeMode.SYSTEM, ThemeMode.LIGHT, ThemeMode.DARK):
            cls._current = mode



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
    EMOJI_BUBBLE_PADDING = 10   # 表情图气泡的内边距
    EMOJI_THUMB_SIZE = 88       # 选择面板缩略图边长
    EMOJI_GRID_COLUMNS = 4      # 选择面板每行个数
    EMOJI_PANEL_MAX_HEIGHT = 300  # 选择面板最大高度
    
    # 用户发送的图片
    USER_IMAGE_SIZE = 220       # 聊天区图片显示边长上限
    ATTACH_THUMB_SIZE = 60      # 待发送缩略图边长
    
    # 模式切换条（标题栏下方的独立一条）
    MODEBAR_HEIGHT = 34         # 模式条高度
    MODE_TAB_HEIGHT = 24        # 模式按钮高度
    MODE_TAB_PADDING = 12       # 模式按钮左右内边距
    
    # 右侧信息面板（Code / Learn 的目录绑定）
    SIDE_PANEL_WIDTH = 236      # 面板宽度
    
    # 审批条（工具调用需要用户授权，显示在输入框上方）
    APPROVAL_BUTTON_HEIGHT = 26  # 允许/拒绝按钮高度
    APPROVAL_DETAIL_LIMIT = 160  # 参数摘要的最大字符数
    
    # 设置窗口
    SETTINGS_WIDTH = 480
    SETTINGS_HEIGHT = 566
    SETTINGS_RADIUS = 12


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
        self._has_image = False
        
        self._setup_ui()
        
        if image_path:
            self.load_image(image_path)
        else:
            self._show_default_text()
    
    def refresh_theme(self) -> None:
        """主题切换后刷新（仅默认文字头像需要重新着色）"""
        if not self._has_image:
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
        self._has_image = False
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
            self._has_image = True
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


# 断行分词规则：英文/数字串整体成词，空白单独成段，其余（中文、标点）逐字
_WRAP_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’\-_.@#%&+/:]*|\s+|.")

# 字形/片段宽度缓存：{字体标识: {片段: 宽度}}，避免逐字重复测量
_GLYPH_WIDTH_CACHE: Dict[str, Dict[str, int]] = {}


def _measure(fm: QFontMetrics, font_key: str, text: str) -> int:
    """测量文本宽度（带缓存，逐字测量时不重复调用 Qt）"""
    cache = _GLYPH_WIDTH_CACHE.setdefault(font_key, {})
    width = cache.get(text)
    if width is None:
        width = fm.horizontalAdvance(text)
        cache[text] = width
    return width


class WrapLabel(QLabel):
    """
    按指定最大宽度自动换行的文本标签

    为什么不用 QLabel 自带的 wordWrap：
    Qt 在 wordWrap 模式下改用 QTextDocument 排版，而 QTextDocument 默认
    documentMargin 为 4px（左右各 4px），因此控件宽度必须比文本宽度多出
    约 9px 才能单行放下；否则末尾一两个字会被挤到下一行（表现为"莫名其妙
    的换行"），而补足这 9px 又会让气泡右侧多出一块空白。
    这里改为自己按最大宽度断行、把换行符写进文本，QLabel 只负责显示：
    宽度精确、换行位置可控，英文单词也不会被从中间截断。
    """

    _WIDTH_SAFETY = 2   # 抗字体度量取整的宽度余量
    _HEIGHT_SAFETY = 2  # 抗行高取整的高度余量（避免最后一行下沿被压掉）

    def __init__(self, text: str = "", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._raw_text: str = ""
        self._max_width: int = 0
        # QLabel 默认 indent=-1，会自动按字体中 "x" 的宽度（约 9px）留出左缩进，
        # 使控件宽度比文本宽出约 9px（短消息尤其明显）；置 0 后宽度才精确。
        self.setIndent(0)
        # 不获取焦点：避免点击消息后画出一圈虚线焦点框
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.set_raw_text(text)

    def setTextInteractionFlags(self, flags: Any) -> None:  # noqa: N802 (Qt 命名)
        """
        设置文本交互标志，并始终保持「不获取焦点」

        Qt 在设置 TextSelectableByMouse 时会顺带把焦点策略改成 ClickFocus，
        于是点击消息气泡就会让标签获得焦点，QLabel 随即在文字外画一圈虚线
        焦点框（浅色主题下是很显眼的黑虚线，深色主题下也不好看）。
        这里在设置后强制回到 NoFocus：鼠标拖选、复制文本依然可用，
        只是标签不会再获得焦点，也就不会绘制焦点框。
        """
        super().setTextInteractionFlags(flags)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    # ------------------------------------------------------------------
    # 文本设置
    # ------------------------------------------------------------------

    def setText(self, text: str) -> None:  # noqa: N802 (Qt 命名)
        """设置文本（自动按当前最大宽度断行）"""
        self.set_raw_text(text)

    def set_raw_text(self, text: str) -> None:
        """设置未断行的原始文本"""
        self._raw_text = text or ""
        super().setText(self._wrap_text(self._raw_text))

    def append_raw_text(self, text: str) -> None:
        """追加原始文本（流式输出用）"""
        if not text:
            return
        self.set_raw_text(self._raw_text + text)

    @property
    def raw_text(self) -> str:
        """未断行的原始文本"""
        return self._raw_text

    def set_max_width(self, width: int) -> None:
        """设置文本最大宽度（像素），变化时重新断行"""
        width = max(0, int(width))
        if width == self._max_width:
            return
        self._max_width = width
        super().setText(self._wrap_text(self._raw_text))

    # ------------------------------------------------------------------
    # 尺寸
    # ------------------------------------------------------------------

    def sizeHint(self) -> QSize:
        """
        返回断行后文本的尺寸（最长一行的宽度 × 全部行的高度）

        高度必须按行数累计：文本已被断成多行，若只报单行高度，
        布局分配的高度就会不够，第二行起会被裁掉（看不见）。
        同时先 ensurePolished，否则样式表给的上下内边距还没生效
        （contentsMargins 仍为 0），高度会少 2px 而压掉最后一行的下沿。
        """
        self.ensurePolished()
        fm = self.fontMetrics()
        lines = self.text().split("\n")
        width = max((fm.horizontalAdvance(line) for line in lines), default=0)
        if width <= 0:
            return super().sizeHint()
        
        margins = self.contentsMargins()
        height = max(1, len(lines)) * fm.lineSpacing()
        return QSize(
            width + margins.left() + margins.right() + self._WIDTH_SAFETY,
            height + margins.top() + margins.bottom() + self._HEIGHT_SAFETY
        )

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (Qt 命名)
        """
        最小尺寸与 sizeHint 一致

        文本已按最大宽度断行，无需再让布局压缩宽度；
        QLabel 默认的 minimumSizeHint 会按未断行文本计算并额外留出缩进，
        比 sizeHint 更大，会把气泡撑宽，因此这里直接复用 sizeHint。
        """
        return self.sizeHint()

    # ------------------------------------------------------------------
    # 断行
    # ------------------------------------------------------------------

    def _wrap_text(self, text: str) -> str:
        """按最大宽度把文本断成多行"""
        if not text or self._max_width <= 0:
            return text
        fm = self.fontMetrics()
        lines: List[str] = []
        for paragraph in text.split("\n"):
            lines.extend(self._wrap_paragraph(paragraph, self._max_width, fm))
        return "\n".join(lines)

    def _wrap_paragraph(
        self, paragraph: str, limit: int, fm: QFontMetrics
    ) -> List[str]:
        """把一段（不含换行符）文本按像素宽度断成多行"""
        if not paragraph:
            return [""]
        key = self._font_key()
        lines: List[str] = []
        current = ""
        current_w = 0

        for token in _WRAP_TOKEN_RE.findall(paragraph):
            token_w = _measure(fm, key, token)

            # 放不下就换行；行首不保留空白
            if current and current_w + token_w > limit:
                lines.append(current.rstrip())
                current, current_w = "", 0
                if token.isspace():
                    continue

            # 单个片段本身比整行还宽（超长单词/连续无空格字符）：逐字强制断开
            if not current and token_w > limit:
                for char in token:
                    char_w = _measure(fm, key, char)
                    if current and current_w + char_w > limit:
                        lines.append(current)
                        current, current_w = "", 0
                    current += char
                    current_w += char_w
                continue

            current += token
            current_w += token_w

        if current.rstrip():
            lines.append(current.rstrip())
        return lines or [""]

    def _font_key(self) -> str:
        """当前字体标识（宽度缓存的键）"""
        font = self.font()
        return (
            f"{font.family()}|{font.pointSizeF()}|"
            f"{font.pixelSize()}|{int(font.weight())}"
        )


class ChatBubble(QFrame):
    """
    聊天气泡组件
    
    包含头像和消息内容，支持用户/AI两种样式。
    内容既可以是文字，也可以是表情包图片（emoji_path 非空时）。
    show_avatar=False 时只显示气泡本身（用于同一侧连续多条消息）。
    """
    
    # 气泡外框比文本区多出的宽度：左右内边距 + 左右边框
    BUBBLE_EXTRA_WIDTH = Sizes.BUBBLE_PADDING_H * 2 + 2
    
    def __init__(
        self, 
        text: str, 
        is_user: bool = False,
        avatar_path: Optional[str] = None,
        parent: Optional[QWidget] = None,
        is_streaming: bool = False,  # 新增：是否为流式输出
        emoji_path: Optional[str] = None,
        emoji_name: str = "",
        show_avatar: bool = True,
        image_size: int = Sizes.EMOJI_MESSAGE_SIZE
    ):
        super().__init__(parent)
        self._text = text
        self._is_user = is_user
        self._avatar_path = avatar_path
        self._is_streaming = is_streaming
        self._emoji_path = emoji_path
        self._emoji_name = emoji_name
        self._show_avatar = show_avatar
        self._image_size = max(32, int(image_size))
        self._label = None  # 保存label引用以便更新
        self._bubble_container: Optional[QFrame] = None  # 保存气泡容器以便切换主题
        self._max_text_width: int = Sizes.BUBBLE_MAX_WIDTH  # 文本区最大宽度
        
        self._setup_ui()
    
    def refresh_theme(self) -> None:
        """主题切换后重新应用气泡样式"""
        if self._bubble_container is None:
            return
        self._style_bubble(self._bubble_container)
    
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
        
        padding_h = Sizes.EMOJI_BUBBLE_PADDING if is_picture else Sizes.BUBBLE_PADDING_H
        padding_v = Sizes.EMOJI_BUBBLE_PADDING if is_picture else Sizes.BUBBLE_PADDING_V
        
        layout = QVBoxLayout(container)
        layout.setContentsMargins(padding_h, padding_v, padding_h, padding_v)
        
        self._bubble_container = container
        self._style_bubble(container)
        
        if is_picture:
            layout.addWidget(self._create_emoji_label())
        else:
            # 文本标签：关闭 wordWrap，由 WrapLabel 自己按最大宽度断行
            self._label = WrapLabel(self._text)
            self._label.setFont(QFont(Fonts.FAMILY, Fonts.SIZE_BUBBLE))
            self._label.set_max_width(self._max_text_width)
            self._label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(self._label)
        
        return container
    
    def set_max_text_width(self, width: int) -> None:
        """设置文本最大宽度（窗口尺寸变化时由聊天窗口调用）"""
        self._max_text_width = max(1, int(width))
        if self._label is not None:
            self._label.set_max_width(self._max_text_width)
        # 文字气泡外框 = 文本区 + 左右内边距 + 边框（表情图气泡宽度固定，不参与）
        if self._bubble_container is not None and not self._emoji_path:
            self._bubble_container.setMaximumWidth(
                self._max_text_width + self.BUBBLE_EXTRA_WIDTH
            )
    
    def _style_bubble(self, container: QFrame) -> None:
        """
        应用气泡样式（创建时与主题切换后共用）

        表情包气泡与文字气泡底色、边框均取自当前主题。
        """
        is_picture = bool(self._emoji_path)
        
        if is_picture:
            container.setStyleSheet(f"""
                QFrame {{
                    background-color: {Colors.EMOJI_IMAGE_BG};
                    border: 1px solid {Colors.BUBBLE_BORDER};
                    border-radius: 10px;
                }}
            """)
            container.setMaximumWidth(
                self._image_size + Sizes.EMOJI_BUBBLE_PADDING * 2 + 8
            )
            return
        
        bg_color = Colors.USER_BUBBLE_BG if self._is_user else Colors.AI_BUBBLE_BG
        container.setStyleSheet(f"""
            QFrame {{
                background-color: {bg_color};
                border: 1px solid {Colors.BUBBLE_BORDER};
                border-radius: 10px;
            }}
            QLabel {{
                color: {Colors.BUBBLE_TEXT};
                background-color: transparent;
                border: none;
                outline: none;
            }}
        """)
        container.setMaximumWidth(
            self._max_text_width + self.BUBBLE_EXTRA_WIDTH
        )
    
    def _create_emoji_label(self) -> QLabel:
        """创建图片标签（等比缩放并居中）"""
        size = self._image_size
        label = QLabel()
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet("background-color: transparent; border: none;")
        label.setFixedSize(size, size)
        
        pixmap = QPixmap(self._emoji_path or "")
        if not pixmap.isNull():
            scaled = pixmap.scaled(
                size, size,
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
        """追加文本（用于流式输出，按原始文本追加后重新断行）"""
        if self._label:
            self._label.append_raw_text(text)
    
    def set_text(self, text: str) -> None:
        """设置完整文本"""
        if self._label:
            self._label.set_raw_text(text)


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
    
    def __init__(
        self,
        bot: Any,
        user_input: str,
        images: Optional[List[str]] = None,
        parent: Optional[QObject] = None
    ):
        super().__init__(parent)
        self._bot = bot
        self._user_input = user_input
        self._images = list(images or [])
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
            # 调用AI的流式响应方法（支持随消息附带图片）
            try:
                stream = self._bot.get_response_stream(
                    self._user_input, self._images
                )
            except TypeError:
                # 兼容不支持 images 参数的旧实现
                stream = self._bot.get_response_stream(self._user_input)
            
            for chunk in stream:
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
        self._hover_color = hover_color
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
            painter.setBrush(QColor(self._hover_color))
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
        elif self._glyph == "gear":
            # 设置：中心圆 + 8 个方向的短齿
            self._draw_gear(painter, rect, color)
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

    def _draw_gear(self, painter: QPainter, rect: QRect, color: str) -> None:
        """
        绘制齿轮图标（中心圆 + 8 个方向的短齿）

        Args:
            painter: 画笔
            rect: 按钮矩形
            color: 图标颜色
        """
        cx = rect.width() / 2.0
        cy = rect.height() / 2.0
        radius = rect.width() * 0.22      # 中心圆半径
        tooth_out = rect.width() * 0.42   # 齿的外端半径
        tooth_in = rect.width() * 0.30    # 齿的内端半径
        
        # 外圈齿：8 条由内向外的小线段
        painter.setPen(QPen(
            QColor(color), self._STROKE_W,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap
        ))
        for i in range(8):
            angle = math.radians(i * 45.0)
            painter.drawLine(
                QPointF(
                    cx + tooth_in * math.cos(angle),
                    cy + tooth_in * math.sin(angle)
                ),
                QPointF(
                    cx + tooth_out * math.cos(angle),
                    cy + tooth_out * math.sin(angle)
                )
            )
        
        # 中心圆
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QPointF(cx, cy), radius, radius)


class TitleBar(QWidget):
    """
    自定义标题栏

    显示应用标题，提供最小化/关闭按钮，并支持按住拖动窗口。
    """

    # 设置按钮被点击（由 ChatWindow 处理主题菜单）
    settings_clicked = Signal()

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        title: str = "CastoriceAgent",
        with_settings: bool = True,
        with_minimize: bool = True
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
        layout.addWidget(self._title_label)
        layout.addStretch()
        self.refresh_theme()

        # 设置按钮（最小化按钮左侧；设置窗口自身不需要）
        self._settings_button: Optional[TitleBarButton] = None
        if with_settings:
            self._settings_button = TitleBarButton(
                "gear", hover_color=Colors.TITLEBAR_BG_HOVER,
                tooltip="设置", parent=self
            )
            self._settings_button.clicked.connect(self.settings_clicked)
            layout.addWidget(self._settings_button)

        # 最小化按钮
        self._min_button: Optional[TitleBarButton] = None
        if with_minimize:
            self._min_button = TitleBarButton(
                "—", hover_color=Colors.TITLEBAR_BG_HOVER,
                tooltip="最小化", parent=self
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

    def refresh_theme(self) -> None:
        """主题切换后刷新标题文字颜色"""
        self._title_label.setStyleSheet(f"""
            QLabel {{
                color: {Colors.TITLEBAR_TEXT};
                font-size: 11pt;
                font-weight: bold;
                background-color: transparent;
                border: none;
            }}
        """)

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
# 模式切换条（标题栏下方的 Chat / Work / Code / Learn）
# ============================================================================

class ModeTabButton(QPushButton):
    """
    模式切换条上的一个模式按钮（自绘，保证两套主题下配色一致）

    自绘而不是用 QSS：模式条的底色来自标题栏色系，选中态是实心白底 + 紫色文字，
    用 QSS 在深色/浅色两套调色板里容易出现对比度不足的字，自绘可以直接取
    Colors.MODE_TAB_* 常量，与主题系统保持同一套来源。
    """

    def __init__(
        self,
        mode_name: str,
        display_name: str,
        parent: Optional[QWidget] = None
    ) -> None:
        super().__init__(display_name, parent)
        self.mode_name = mode_name
        self._hovered = False
        self._active = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedHeight(Sizes.MODE_TAB_HEIGHT)
        self.setToolTip(f"切换到「{display_name}」模式")
        self._refresh_size()

    # ---- 状态 ----

    def set_active(self, active: bool) -> None:
        """设置选中态（选中按钮为实心白底）"""
        if self._active == active:
            return
        self._active = active
        self._refresh_size()
        self.update()

    def is_active(self) -> bool:
        return self._active

    def refresh_theme(self) -> None:
        """主题切换后重绘"""
        self.update()

    def _refresh_size(self) -> None:
        """按文字宽度定尺寸：选中态字号加粗，需要重新测量"""
        font = QFont(Fonts.FAMILY, Fonts.SIZE_MAIN)
        font.setBold(self._active)
        metrics = QFontMetrics(font)
        width = metrics.horizontalAdvance(self.text()) + Sizes.MODE_TAB_PADDING * 2
        self.setFixedWidth(width)

    # ---- 交互 ----

    def enterEvent(self, event: Any) -> None:
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: Any) -> None:
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event: Any) -> None:
        """自绘圆角按钮：选中实心、悬停半亮、其余透明"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = rect.height() / 2

        if self._active:
            background = QColor(Colors.MODE_TAB_BG_ACTIVE)
            text_color = Colors.MODE_TAB_TEXT_ACTIVE
        elif self._hovered:
            background = QColor(Colors.MODE_TAB_BG_HOVER)
            text_color = Colors.MODE_TAB_TEXT_HOVER
        else:
            background = None
            text_color = Colors.MODE_TAB_TEXT

        if background is not None:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(background)
            painter.drawRoundedRect(rect, radius, radius)

        font = QFont(Fonts.FAMILY, Fonts.SIZE_MAIN)
        font.setBold(self._active)
        painter.setFont(font)
        painter.setPen(QColor(text_color))
        painter.drawText(
            self.rect(),
            int(Qt.AlignmentFlag.AlignCenter),
            self.text(),
        )
        painter.end()


class ModeSwitchBar(QWidget):
    """
    顶部模式切换条

    贴在标题栏正下方，与标题栏共用圆角窗口的同一块背景色系。
    按钮组由 set_modes() 注入，选中态由 set_active_mode() 同步；
    点击发出 mode_selected(模式名)，由 ChatWindow 负责真正的切换。
    """

    mode_selected = Signal(str)

    def __init__(
        self,
        modes: Optional[List[Dict[str, Any]]] = None,
        active: str = ModeSettings.DEFAULT,
        parent: Optional[QWidget] = None
    ) -> None:
        super().__init__(parent)
        self._buttons: Dict[str, ModeTabButton] = {}
        self._active_mode = normalize_mode_name(active)

        self.setFixedHeight(Sizes.MODEBAR_HEIGHT)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(Sizes.TITLEBAR_MARGIN_H, 0, 8, 0)
        self._layout.setSpacing(2)

        # 当前模式的中文名提示（右侧留白处，不参与交互）
        self._hint = QLabel("")
        self._hint.setStyleSheet("background-color: transparent; border: none;")

        self.set_modes(modes or list(FALLBACK_MODES))
        self._layout.addStretch()
        self._layout.addWidget(self._hint)
        self.refresh_theme()

    # ---- 构建与状态 ----

    def set_modes(self, modes: List[Dict[str, Any]]) -> None:
        """按内核给出的模式元数据重建按钮组（幂等：重复调用先清空）"""
        for button in self._buttons.values():
            self._layout.removeWidget(button)
            button.setParent(None)
            button.deleteLater()
        self._buttons.clear()

        insert_at = 0
        for item in modes:
            name = normalize_mode_name(item.get("name"))
            label = str(item.get("display_name") or name)
            if name in self._buttons:
                continue
            button = ModeTabButton(name, label, self)
            button.clicked.connect(
                lambda _checked=False, n=name: self.mode_selected.emit(n)
            )
            self._layout.insertWidget(insert_at, button)
            insert_at += 1
            self._buttons[name] = button

        self.set_active_mode(self._active_mode)

    def mode_names(self) -> List[str]:
        """当前按钮组里的模式名（顺序即界面顺序）"""
        return list(self._buttons.keys())

    def button_for(self, mode: str) -> Optional[ModeTabButton]:
        return self._buttons.get(normalize_mode_name(mode))

    def active_mode(self) -> str:
        return self._active_mode

    def set_active_mode(self, mode: str) -> None:
        """同步选中态（未知模式名保持原选中项不变）"""
        name = normalize_mode_name(mode)
        if name not in self._buttons:
            return
        self._active_mode = name
        for key, button in self._buttons.items():
            button.set_active(key == name)
        self._update_hint()

    def _update_hint(self) -> None:
        button = self._buttons.get(self._active_mode)
        self._hint.setText(button.text() if button is not None else "")

    # ---- 主题 ----

    def refresh_theme(self) -> None:
        """主题切换后刷新按钮与提示文字"""
        for button in self._buttons.values():
            button.refresh_theme()
        self._hint.setStyleSheet(f"""
            QLabel {{
                color: {Colors.MODE_TAB_TEXT};
                font-size: 8pt;
                background-color: transparent;
                border: none;
            }}
        """)
        self.update()

    def paintEvent(self, event: Any) -> None:
        """绘制模式条底色（深一档的紫，与标题栏形成层次但不抢眼）"""
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(Colors.MODE_BAR_BG))
        painter.end()


# ============================================================================
# 表情包（贴纸）
# ============================================================================

# 不参与表情包的图片（头像与界面资源）
_EMOJI_SKIP_PREFIXES = ("_", ".")
_EMOJI_AVATAR_NAME = "CastoriceAvatar"
_EMOJI_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}

# 表情包专用子目录名：表情包统一放在 Image/表情包 下，
# 头图与 _ 开头的界面资源仍放在 Image 根目录
EMOJI_SUBDIR_NAME = "表情包"


def resolve_emoji_dir(image_dir: Union[str, Path]) -> Path:
    """
    解析表情包实际所在目录

    优先使用 <image_dir>/表情包 子目录；该子目录不存在时退回 <image_dir> 本身，
    以兼容「表情包直接放在 Image 目录下」的旧结构。

    Args:
        image_dir: 图片根目录（通常是 Image）

    Returns:
        真正存放表情包的目录
    """
    directory = Path(image_dir)
    sub_directory = directory / EMOJI_SUBDIR_NAME
    if sub_directory.is_dir():
        return sub_directory
    return directory


def scan_emoji_dir(image_dir: Union[str, Path] = "Image") -> List[Dict[str, str]]:
    """
    扫描表情包目录

    表情包放在 Image/表情包 子目录里；文件名主干即表情包标签名
    （跳过头像与 _ 开头的界面资源）。

    Args:
        image_dir: 图片根目录（会自动解析到表情包子目录）

    Returns:
        [{'name': 标签名, 'path': 图片绝对路径}, ...]
    """
    directory = resolve_emoji_dir(image_dir)
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
    """定位表情包目录（默认基于程序根目录，避免受启动路径影响）"""
    root = base_dir or app_root()
    return resolve_emoji_dir(root / "Image")


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
        elif self._glyph == "image":
            self._draw_image(painter, rect)
        
        painter.end()
    
    def _draw_image(self, painter: QPainter, rect: QRectF) -> None:
        """绘制图片图标（相框 + 山 + 太阳）"""
        side = min(rect.width(), rect.height()) * 0.72
        frame = QRectF(
            rect.center().x() - side / 2,
            rect.center().y() - side / 2,
            side, side
        )
        
        # 相框
        painter.drawRoundedRect(frame, side * 0.16, side * 0.16)
        
        # 太阳
        sun_r = side * 0.09
        painter.setBrush(QColor(self._icon_color()))
        painter.drawEllipse(
            QPointF(
                frame.left() + side * 0.28,
                frame.top() + side * 0.28
            ),
            sun_r, sun_r
        )
        painter.setBrush(Qt.BrushStyle.NoBrush)
        
        # 山（两条斜线构成的折线）
        painter.drawPolyline([
            QPointF(frame.left() + side * 0.10, frame.bottom() - side * 0.16),
            QPointF(frame.left() + side * 0.42, frame.bottom() - side * 0.52),
            QPointF(frame.left() + side * 0.66, frame.bottom() - side * 0.26),
            QPointF(frame.left() + side * 0.82, frame.bottom() - side * 0.40),
            QPointF(frame.right() - side * 0.08, frame.bottom() - side * 0.16),
        ])
    
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

    点击输入框右侧的笑脸按钮弹出，网格展示 Image/表情包 目录下的所有表情包，
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
        self._apply_theme()
        
        self._build(emojis)
    
    def _apply_theme(self) -> None:
        """按当前主题设置面板与滚动区样式"""
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
        for button in getattr(self, "_buttons", []):
            self._style_item(button)
    
    def _style_item(self, button: QPushButton) -> None:
        """按当前主题设置单个表情按钮样式"""
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
                f"还没有表情包\n"
                f"把图片放进 Image/{EMOJI_SUBDIR_NAME} 后重启即可（文件名即表情名称）"
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
        self._style_item(button)
        
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
# 待发送图片
# ============================================================================

class AttachmentItem(QWidget):
    """
    待发送图片的缩略图

    显示图片缩略图与一个移除按钮。

    信号:
        removed(str): 要移除的图片路径
    """

    removed = Signal(str)

    def __init__(self, path: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._path = path
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        
        self._thumb = QLabel()
        self._thumb.setFixedSize(Sizes.ATTACH_THUMB_SIZE, Sizes.ATTACH_THUMB_SIZE)
        self._thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thumb.setToolTip(os.path.basename(path))
        
        pixmap = QPixmap(path)
        if pixmap.isNull():
            self._thumb.setText("?")
        else:
            self._thumb.setPixmap(pixmap.scaled(
                Sizes.ATTACH_THUMB_SIZE - 8, Sizes.ATTACH_THUMB_SIZE - 8,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            ))
        
        self._close_button = QPushButton("×")
        self._close_button.setFixedSize(18, 18)
        self._close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._close_button.setToolTip("移除这张图片")
        self._close_button.clicked.connect(lambda: self.removed.emit(self._path))
        
        layout.addWidget(self._thumb)
        layout.addWidget(self._close_button, 0, Qt.AlignmentFlag.AlignTop)
        
        self.refresh_theme()
    
    @property
    def path(self) -> str:
        """图片路径"""
        return self._path
    
    def refresh_theme(self) -> None:
        """主题切换后刷新样式"""
        self._thumb.setStyleSheet(f"""
            QLabel {{
                background-color: {Colors.INPUT_BG};
                color: {Colors.INPUT_PLACEHOLDER};
                border: 1px solid {Colors.INPUT_BORDER};
                border-radius: 8px;
            }}
        """)
        self._close_button.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.TITLEBAR_BG};
                color: #FFFFFF;
                border: none;
                border-radius: 9px;
                font-size: 11pt;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {Colors.TITLEBAR_BG_HOVER};
            }}
        """)


# ============================================================================
# 右侧信息面板（Code / Learn 的目录绑定）
# ============================================================================

#: 面板上「未绑定」时的可读提示（每种模式各自说明要绑定什么）
UNBOUND_HINTS: Dict[str, str] = {
    "work": "目录：未绑定\n可直接在输入框里描述任务，绑定目录后工具才能读写文件。",
    "code": "代码目录：未绑定\n点下面的按钮选一个目录，之后读写与命令都在它之内。",
    "learn": "笔记库：未绑定\n点下面的按钮选一个 Obsidian Vault 目录，笔记会存进去。",
    "chat": "聊天模式不需要绑定目录。",
}


class SidePanel(QWidget):
    """
    右侧信息面板：Code / Learn 模式的目录绑定入口

    这是「侧栏而非弹窗」的选择：绑定目录属于模式的长期状态，
    放在常驻面板上可以直接看到当前绑的是哪个目录；Chat 模式隐藏面板，
    避免把工具相关的东西暴露给纯聊天用户（第一期验收条款之一）。
    """

    bind_requested = Signal(str)     # 请求绑定目录（模式名）
    unbind_requested = Signal(str)   # 请求解绑目录（模式名）

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._mode = ModeSettings.DEFAULT
        self.setFixedWidth(Sizes.SIDE_PANEL_WIDTH)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        self._title = QLabel("")
        self._title.setWordWrap(True)

        self._sandbox_label = QLabel("")
        self._sandbox_label.setWordWrap(True)

        self._dir_label = QLabel("")
        self._dir_label.setWordWrap(True)

        self._hint_label = QLabel("")
        self._hint_label.setWordWrap(True)

        self._bind_button = QPushButton("绑定目录…")
        self._bind_button.clicked.connect(self._on_bind_clicked)

        self._unbind_button = QPushButton("解除绑定")
        self._unbind_button.clicked.connect(self._on_unbind_clicked)

        layout.addWidget(self._title)
        layout.addWidget(self._make_divider())
        layout.addWidget(self._sandbox_label)
        layout.addWidget(self._dir_label)
        layout.addWidget(self._hint_label)
        layout.addWidget(self._bind_button)
        layout.addWidget(self._unbind_button)
        layout.addStretch()

        self.refresh_theme()

    # ---- 构建 ----

    def _make_divider(self) -> QFrame:
        divider = QFrame()
        divider.setFixedHeight(1)
        divider.setObjectName("sidePanelDivider")
        return divider

    # ---- 状态 ----

    def set_mode_info(self, info: Optional[Dict[str, Any]]) -> None:
        """
        用模式元数据刷新面板

        Args:
            info: ai.AI.available_modes() 里的一项；None 表示内核不可用，
                  此时只保留「未绑定」的可读提示，按钮禁用。
        """
        data = dict(info or {})
        self._mode = normalize_mode_name(data.get("name") or self._mode)
        display_name = str(data.get("display_name") or self._mode)
        policy = data.get("workdir_policy") or {}
        bindable = bool(data.get("bindable", policy.get("bindable", False)))
        workdir = data.get("workdir") or ""
        sandbox = str(data.get("sandbox") or policy.get("default_sandbox") or "")

        self._title.setText(f"{display_name}模式")
        self._sandbox_label.setText(f"沙箱：{sandbox}" if sandbox else "")
        self._sandbox_label.setVisible(bool(sandbox))

        if workdir:
            self._dir_label.setText("已绑定：\n" + str(workdir))
            self._dir_label.setToolTip(str(workdir))
        else:
            self._dir_label.setText(
                UNBOUND_HINTS.get(self._mode, "目录：未绑定")
            )
            self._dir_label.setToolTip("")

        if bindable:
            self._hint_label.setText("提示：只能绑定到可信根（配置里登记的目录）之内。")
        else:
            self._hint_label.setText("")
        self._hint_label.setVisible(bindable)

        self._bind_button.setVisible(bindable)
        self._bind_button.setEnabled(bindable)
        self._bind_button.setText("更换目录…" if workdir else "绑定目录…")
        self._unbind_button.setVisible(bindable and bool(workdir))
        self._unbind_button.setEnabled(bindable and bool(workdir))

    def mode(self) -> str:
        return self._mode

    def workdir_text(self) -> str:
        """目录区当前显示的文字（供测试与可访问性检查读取）"""
        return self._dir_label.text()

    def hint_text(self) -> str:
        """提示区当前显示的文字（绑定失败原因等）"""
        return self._hint_label.text()

    def show_message(self, text: str) -> None:
        """在提示区显示一条临时信息（如绑定失败的原因）"""
        self._hint_label.setText(str(text))
        self._hint_label.setVisible(bool(text))

    # ---- 交互 ----

    def _on_bind_clicked(self) -> None:
        self.bind_requested.emit(self._mode)

    def _on_unbind_clicked(self) -> None:
        self.unbind_requested.emit(self._mode)

    # ---- 主题 ----

    def refresh_theme(self) -> None:
        """主题切换后刷新配色（面板底色用主题常量，不写死颜色）"""
        self.setStyleSheet(f"""
            SidePanel {{
                background-color: {Colors.PANEL_BG};
                border: 1px solid {Colors.PANEL_BORDER};
                border-radius: 10px;
            }}
            QFrame#sidePanelDivider {{
                background-color: {Colors.PANEL_BORDER};
                border: none;
            }}
            QPushButton {{
                background-color: {Colors.PANEL_KEY_BG};
                color: {Colors.PANEL_TEXT};
                border: 1px solid {Colors.PANEL_BORDER};
                border-radius: 6px;
                padding: 4px 8px;
                font-size: 9pt;
            }}
            QPushButton:hover {{
                border: 1px solid {Colors.HIGHLIGHT};
            }}
            QPushButton:disabled {{
                color: {Colors.PANEL_MUTED};
            }}
        """)
        self._title.setStyleSheet(self._label_style(Colors.PANEL_TITLE, bold=True))
        self._sandbox_label.setStyleSheet(self._label_style(Colors.PANEL_MUTED))
        self._dir_label.setStyleSheet(self._label_style(Colors.PANEL_TEXT))
        self._hint_label.setStyleSheet(self._label_style(Colors.PANEL_MUTED))
        self.update()

    @staticmethod
    def _label_style(color: str, bold: bool = False) -> str:
        weight = "bold" if bold else "normal"
        return (
            "QLabel {"
            f"color: {color};"
            "background-color: transparent;"
            "border: none;"
            "font-size: 9pt;"
            f"font-weight: {weight};"
            "}"
        )

# ============================================================================
# 审批条（工具调用需要用户授权）
# ============================================================================

def describe_permission_request(request: Any) -> str:
    """把一次审批请求翻成一句人话（不 import 内核类型，按鸭子类型读取）。

    读不到的字段一律跳过，绝不因为字段缺失而抛异常——它跑在展示路径上，
    出问题只需要显示得含糊一点，而不是把审批窗口弄崩。
    """
    tool = str(getattr(request, "tool", "") or "未知工具")
    arguments = getattr(request, "arguments", None) or {}
    try:
        detail = json.dumps(arguments, ensure_ascii=False, default=str)
    except Exception:
        detail = str(arguments)
    if len(detail) > Sizes.APPROVAL_DETAIL_LIMIT:
        detail = detail[:Sizes.APPROVAL_DETAIL_LIMIT] + "…"
    return tool + " " + detail


class ApprovalBar(QWidget):
    """
    审批条：有副作用的工具在真正执行前需要用户点「允许」或「拒绝」

    为什么做成常驻区域而不是模态对话框：
    * 审批由 **worker 线程**发起，主线程只是被投递通知。模态 exec() 会开启
      嵌套事件循环，把「等待用户点击」和「worker 阻塞等待」缠在一起，
      一旦用户关窗或超时，嵌套循环的退出路径极难保证不留悬挂状态；
    * 常驻区域天然可测：offscreen 平台下没有真实用户，测试直接调用
      respond() 就能确定性走完 allow / deny 两条路径。

    Countdown 只做展示（让用户知道还有多久会自动拒绝），真正的超时判定
    由内核 ApprovalBroker 的阻塞等待负责，二者不重复计时。
    """

    #: 用户点了允许（携带 request_id）
    approved = Signal(str)
    #: 用户点了拒绝 / 倒计时归零（携带 request_id）
    denied = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._request_id = ""
        self._remaining = 0.0
        self._seconds_total = 0
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)

        self._title_label = QLabel("工具调用需要授权")
        self._detail_label = QLabel("")
        self._detail_label.setWordWrap(True)
        self._countdown_label = QLabel("")

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.setSpacing(6)
        self._allow_button = QPushButton("允许")
        self._allow_button.setFixedHeight(Sizes.APPROVAL_BUTTON_HEIGHT)
        self._allow_button.clicked.connect(self._on_allow_clicked)
        self._deny_button = QPushButton("拒绝")
        self._deny_button.setFixedHeight(Sizes.APPROVAL_BUTTON_HEIGHT)
        self._deny_button.clicked.connect(self._on_deny_clicked)
        button_row.addWidget(self._allow_button)
        button_row.addWidget(self._deny_button)
        button_row.addStretch()

        layout.addWidget(self._title_label)
        layout.addWidget(self._detail_label)
        layout.addWidget(self._countdown_label)
        layout.addLayout(button_row)

        self._timer = QTimer(self)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._tick)

        self.hide()
        self.refresh_theme()

    # ---- 生命周期 ----

    def start(self, request: Any, request_id: str, timeout: float) -> None:
        """开始等待某个请求的授权；重复调用会替换当前请求。"""
        self._request_id = str(request_id)
        self._seconds_total = max(1, int(round(float(timeout))))
        self._remaining = max(0.0, float(timeout))
        self._detail_label.setText(describe_permission_request(request))
        self._update_countdown()
        self.show()
        if self._seconds_total > 0 and timeout > 0:
            self._timer.start()

    def cancel(self) -> None:
        """收起审批条（请求已被别处答复，或整轮已结束）。"""
        self._timer.stop()
        self._request_id = ""
        self._remaining = 0.0
        self.hide()

    def respond(self, allow: bool) -> bool:
        """以编程方式答复当前请求（按钮回调与测试共用同一条路径）。"""
        request_id = self._request_id
        if not request_id:
            return False
        self.cancel()
        if allow:
            self.approved.emit(request_id)
        else:
            self.denied.emit(request_id)
        return True

    # ---- 状态 ----

    def request_id(self) -> str:
        """当前正在等待答复的 request_id（空闲时为空串）"""
        return self._request_id

    def is_waiting(self) -> bool:
        return bool(self._request_id)

    def countdown_text(self) -> str:
        return self._countdown_label.text()

    def detail_text(self) -> str:
        return self._detail_label.text()

    def _update_countdown(self) -> None:
        if self._seconds_total <= 0:
            self._countdown_label.setText("等待你的决定（不会自动超时）")
            return
        seconds = str(max(0, int(self._remaining + 0.999)))
        self._countdown_label.setText(
            "还剩 " + seconds + " 秒，超时后自动按拒绝处理"
        )

    def _tick(self) -> None:
        """倒计时推进；归零即按拒绝处理（与内核超时的语义一致）。"""
        self._remaining -= self._timer.interval() / 1000.0
        if self._remaining <= 0:
            self.respond(False)
            return
        self._update_countdown()

    # ---- 交互 ----

    def _on_allow_clicked(self) -> None:
        self.respond(True)

    def _on_deny_clicked(self) -> None:
        self.respond(False)

    # ---- 主题 ----

    def refresh_theme(self) -> None:
        """主题切换后刷新配色（全部取自 Colors，不写死颜色）。"""
        self.setStyleSheet(f"""
            ApprovalBar {{
                background-color: {Colors.APPROVAL_BG};
                border: 1px solid {Colors.APPROVAL_BORDER};
                border-radius: 10px;
            }}
        """)
        self._title_label.setStyleSheet(self._label_style(
            Colors.APPROVAL_TITLE, bold=True))
        self._detail_label.setStyleSheet(self._label_style(Colors.APPROVAL_TEXT))
        self._countdown_label.setStyleSheet(self._label_style(Colors.APPROVAL_MUTED))
        self._allow_button.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.APPROVAL_ALLOW_BG};
                color: {Colors.APPROVAL_ALLOW_TEXT};
                border: none;
                border-radius: 6px;
                padding: 3px 14px;
                font-size: 9pt;
            }}
        """)
        self._deny_button.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.APPROVAL_DENY_BG};
                color: {Colors.APPROVAL_DENY_TEXT};
                border: 1px solid {Colors.APPROVAL_BORDER};
                border-radius: 6px;
                padding: 3px 14px;
                font-size: 9pt;
            }}
        """)
        self.update()

    @staticmethod
    def _label_style(color: str, bold: bool = False) -> str:
        weight = "bold" if bold else "normal"
        return (
            "QLabel {"
            f"color: {color};"
            "background-color: transparent;"
            "border: none;"
            "font-size: 9pt;"
            f"font-weight: {weight};"
            "}"
        )


# ============================================================================
# 设置窗口
# ============================================================================

def _to_bool_value(value: Any, default: bool = False) -> bool:
    """宽松地把配置值转成 bool（与 ai.py 中的解析保持一致）"""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in ("0", "false", "no", "off", "none", "")


class SettingsDialog(QWidget):
    """
    独立设置窗口：外观 + 模型提供商与 API

    - 无边框 + 自绘标题栏，风格与主窗口一致，可按住标题栏拖动；
    - 主题选项即时生效并持久化（所改即所见）；
    - 「保存」把提供商配置写回 config.yaml（保留原有注释与排版），
      并让 AI 立刻使用新配置，无需重启。
    """

    theme_selected = Signal(str)   # 主题模式变化（实时预览）
    config_saved = Signal()        # 配置已保存

    _positioned = False            # 是否已按设置按钮定位过

    def __init__(
        self,
        bot: Any,
        theme_mode: str,
        parent: Optional[QWidget] = None
    ):
        super().__init__(parent)
        self._bot = bot
        self._theme_mode = theme_mode
        self._status_error = False
        
        self.setWindowTitle("设置")
        self.setWindowFlags(
            Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(Sizes.SETTINGS_WIDTH, Sizes.SETTINGS_HEIGHT)
        
        self._build()
        self.apply_theme()
        self.reload_fields()
    
    # ------------------------------------------------------------------
    # 构建
    # ------------------------------------------------------------------
    
    def _build(self) -> None:
        """构建界面"""
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        
        self._title_bar = TitleBar(
            self, title="设置", with_settings=False, with_minimize=False
        )
        outer.addWidget(self._title_bar)
        
        body = QWidget()
        body.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        outer.addWidget(body, 1)
        
        layout = QVBoxLayout(body)
        layout.setContentsMargins(24, 12, 24, 16)
        layout.setSpacing(7)
        
        # ---------------- 外观 ----------------
        layout.addWidget(self._section_label("外观"))
        
        self._theme_buttons: Dict[str, QRadioButton] = {}
        for mode, text in (
            (ThemeMode.SYSTEM, "跟随系统"),
            (ThemeMode.LIGHT, "浅色（白色偏紫）"),
            (ThemeMode.DARK, "深色（黑色偏紫）"),
        ):
            radio = QRadioButton(text)
            radio.setChecked(mode == self._theme_mode)
            radio.toggled.connect(
                lambda checked, m=mode: checked and self._select_theme(m)
            )
            self._theme_buttons[mode] = radio
            layout.addWidget(radio)
        
        self._theme_hint = QLabel()
        self._theme_hint.setObjectName("hintText")
        self._theme_hint.setWordWrap(True)
        layout.addWidget(self._theme_hint)
        
        layout.addSpacing(3)
        layout.addWidget(self._divider())
        layout.addSpacing(3)
        
        # ---------------- 模型提供商 ----------------
        layout.addWidget(self._section_label("模型提供商与 API"))
        
        self._provider_combo = QComboBox()
        self._provider_combo.addItems(self._provider_names())
        self._provider_combo.currentTextChanged.connect(self._load_provider)
        layout.addLayout(self._form_row("提供商", self._provider_combo))
        
        self._api_key_edit = QLineEdit()
        self._api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key_edit.setPlaceholderText("sk-…")
        
        self._api_key_toggle = QPushButton("显示")
        self._api_key_toggle.setObjectName("ghostButton")
        self._api_key_toggle.setCheckable(True)
        self._api_key_toggle.setFixedWidth(58)
        self._api_key_toggle.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._api_key_toggle.toggled.connect(self._on_toggle_api_key)
        
        api_row = QWidget()
        api_row.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        api_layout = QHBoxLayout(api_row)
        api_layout.setContentsMargins(0, 0, 0, 0)
        api_layout.setSpacing(6)
        api_layout.addWidget(self._api_key_edit, 1)
        api_layout.addWidget(self._api_key_toggle)
        layout.addLayout(self._form_row("API Key", api_row))
        
        self._base_url_edit = QLineEdit()
        self._base_url_edit.setPlaceholderText("https://api.example.com/v1")
        layout.addLayout(self._form_row("Base URL", self._base_url_edit))
        
        self._model_edit = QLineEdit()
        self._model_edit.setPlaceholderText("模型名称，如 gpt-4o")
        layout.addLayout(self._form_row("模型", self._model_edit))
        
        self._temperature_edit = QLineEdit()
        self._temperature_edit.setPlaceholderText("0.0 ~ 2.0，留空则不改动")
        layout.addLayout(self._form_row("Temperature", self._temperature_edit))
        
        self._vision_check = QCheckBox("该模型支持图片输入（可发图片给它识别）")
        layout.addWidget(self._vision_check)
        
        self._vision_hint = QLabel()
        self._vision_hint.setObjectName("hintText")
        self._vision_hint.setWordWrap(True)
        layout.addWidget(self._vision_hint)
        
        layout.addStretch()
        
        # ---------------- 状态与按钮 ----------------
        self._status_label = QLabel()
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)
        
        button_row = QWidget()
        button_row.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        button_layout = QHBoxLayout(button_row)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(8)
        
        self._close_button = QPushButton("关闭")
        self._close_button.setObjectName("ghostButton")
        self._close_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._close_button.clicked.connect(self.close)
        
        self._save_button = QPushButton("保存")
        self._save_button.setObjectName("primaryButton")
        self._save_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._save_button.clicked.connect(self._on_save)
        
        button_layout.addWidget(self._status_label, 1)
        button_layout.addWidget(self._close_button)
        button_layout.addWidget(self._save_button)
        layout.addWidget(button_row)
    
    def _section_label(self, text: str) -> QLabel:
        """分组标题"""
        label = QLabel(text)
        label.setObjectName("sectionTitle")
        return label
    
    def _divider(self) -> QFrame:
        """分隔线"""
        line = QFrame()
        line.setObjectName("divider")
        line.setFixedHeight(1)
        return line
    
    def _form_row(self, label_text: str, widget: QWidget) -> QHBoxLayout:
        """一行表单：左侧标签 + 右侧控件"""
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        
        label = QLabel(label_text)
        label.setFixedWidth(84)
        label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        
        row.addWidget(label)
        row.addWidget(widget, 1)
        return row
    
    # ------------------------------------------------------------------
    # 样式
    # ------------------------------------------------------------------
    
    def apply_theme(self) -> None:
        """按当前主题刷新整个窗口样式"""
        self.setStyleSheet(f"""
            QLabel {{
                color: {Colors.INPUT_TEXT};
                background-color: transparent;
            }}
            QLabel#sectionTitle {{
                color: {Colors.HIGHLIGHT};
                font-size: 10pt;
                font-weight: bold;
            }}
            QLabel#hintText {{
                color: {Colors.INPUT_PLACEHOLDER};
                font-size: 8pt;
            }}
            QLineEdit {{
                background-color: {Colors.INPUT_BG};
                color: {Colors.INPUT_TEXT};
                border: 1px solid {Colors.INPUT_BORDER};
                border-radius: 8px;
                padding: 6px 10px;
                selection-background-color: {Colors.HIGHLIGHT};
                selection-color: {Colors.HIGHLIGHT_TEXT};
            }}
            QLineEdit:focus {{
                border: 1px solid {Colors.INPUT_BORDER_FOCUS};
            }}
            QComboBox {{
                background-color: {Colors.INPUT_BG};
                color: {Colors.INPUT_TEXT};
                border: 1px solid {Colors.INPUT_BORDER};
                border-radius: 8px;
                padding: 6px 10px;
            }}
            QComboBox:hover, QComboBox:focus {{
                border: 1px solid {Colors.INPUT_BORDER_FOCUS};
            }}
            QComboBox::drop-down {{
                border: none;
                width: 22px;
            }}
            QComboBox QAbstractItemView {{
                background-color: {Colors.EMOJI_PANEL_BG};
                color: {Colors.INPUT_TEXT};
                border: 1px solid {Colors.EMOJI_PANEL_BORDER};
                border-radius: 8px;
                selection-background-color: {Colors.HIGHLIGHT};
                selection-color: {Colors.HIGHLIGHT_TEXT};
                outline: none;
                padding: 4px;
            }}
            QRadioButton, QCheckBox {{
                color: {Colors.INPUT_TEXT};
                spacing: 8px;
                background-color: transparent;
            }}
            QRadioButton::indicator {{
                width: 14px;
                height: 14px;
                border-radius: 8px;
                border: 2px solid {Colors.SCROLLBAR_HANDLE};
                background-color: transparent;
            }}
            QRadioButton::indicator:checked {{
                border: 4px solid {Colors.HIGHLIGHT};
                background-color: {Colors.INPUT_BG};
            }}
            QCheckBox::indicator {{
                width: 14px;
                height: 14px;
                border-radius: 4px;
                border: 2px solid {Colors.SCROLLBAR_HANDLE};
                background-color: transparent;
            }}
            QCheckBox::indicator:checked {{
                border: 2px solid {Colors.HIGHLIGHT};
                background-color: {Colors.HIGHLIGHT};
            }}
            QPushButton#primaryButton {{
                background-color: {Colors.SEND_BUTTON_BG};
                color: #FFFFFF;
                border: none;
                border-radius: 8px;
                padding: 7px 24px;
                font-weight: bold;
            }}
            QPushButton#primaryButton:hover {{
                background-color: {Colors.SEND_BUTTON_BG_HOVER};
            }}
            QPushButton#ghostButton {{
                background-color: transparent;
                color: {Colors.INPUT_TEXT};
                border: 1px solid {Colors.INPUT_BORDER};
                border-radius: 8px;
                padding: 7px 16px;
            }}
            QPushButton#ghostButton:hover {{
                background-color: {Colors.EMOJI_BUTTON_HOVER};
            }}
            QFrame#divider {{
                background-color: {Colors.INPUT_BORDER};
                border: none;
            }}
        """)
        
        self._title_bar.refresh_theme()
        self._update_theme_hint()
        self._apply_status_style()
        self.update()
    
    def _apply_status_style(self) -> None:
        """状态文字颜色（错误为红色，正常为提示色）"""
        if not self._status_label.text():
            self._status_label.setStyleSheet("")
            return
        color = "#E5484D" if self._status_error else Colors.HIGHLIGHT
        self._status_label.setStyleSheet(
            f"color: {color}; font-size: 8pt; background-color: transparent;"
        )
    
    def _set_status(self, text: str, error: bool = False) -> None:
        """更新底部状态文字"""
        self._status_error = error
        self._status_label.setText(text)
        self._apply_status_style()
    
    def paintEvent(self, event: Any) -> None:
        """绘制圆角不透明窗口背景"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(
            rect, Sizes.SETTINGS_RADIUS, Sizes.SETTINGS_RADIUS
        )
        painter.fillPath(path, QColor(Colors.WINDOW_BG))
        painter.end()
    
    # ------------------------------------------------------------------
    # 主题
    # ------------------------------------------------------------------
    
    def set_theme_mode(self, mode: str) -> None:
        """同步外部（主窗口）的主题状态到单选框"""
        self._theme_mode = mode
        button = self._theme_buttons.get(mode)
        if button is not None and not button.isChecked():
            button.setChecked(True)
        self._update_theme_hint()
    
    def _select_theme(self, mode: str) -> None:
        """选中主题：立即生效并持久化"""
        self._theme_mode = mode
        ThemeSettings.save(mode)
        self._update_theme_hint()
        self.theme_selected.emit(mode)
    
    def _update_theme_hint(self) -> None:
        """更新「跟随系统」的说明文字"""
        system_dark = system_prefers_dark()
        effective = "深色（黑色偏紫）" if system_dark else "浅色（白色偏紫）"
        self._theme_hint.setText(
            f"跟随系统：当前系统为{'深色' if system_dark else '浅色'}模式，"
            f"将使用「{effective}」。"
        )
    
    # ------------------------------------------------------------------
    # 提供商配置
    # ------------------------------------------------------------------
    
    def _provider_names(self) -> List[str]:
        """config.yaml 中已配置的提供商"""
        try:
            names = list(self._bot.config_manager.provider_names)
        except Exception:
            names = []
        if not names:
            current = self._current_provider_name()
            names = [current] if current else []
        return names
    
    def _current_provider_name(self) -> str:
        """当前生效的提供商名称"""
        try:
            return str(self._bot.provider_name)
        except Exception:
            return ""
    
    def reload_fields(self) -> None:
        """重新从配置加载表单（窗口再次打开时调用）"""
        names = self._provider_names()
        current = self._current_provider_name()
        
        self._provider_combo.blockSignals(True)
        self._provider_combo.clear()
        self._provider_combo.addItems(names)
        if current in names:
            self._provider_combo.setCurrentText(current)
        self._provider_combo.blockSignals(False)
        
        self._load_provider(self._provider_combo.currentText())
    
    def _load_provider(self, name: str) -> None:
        """把某个提供商的配置填入表单"""
        if not name:
            return
        try:
            config = self._bot.config_manager.get_provider_config(name)
        except Exception as e:
            self._set_status(f"读取配置失败：{e}", error=True)
            return
        
        self._api_key_edit.setText(str(config.get('api_key') or ""))
        self._base_url_edit.setText(str(config.get('base_url') or ""))
        model = str(config.get('model') or "")
        self._model_edit.setText(model)
        temperature = config.get('temperature')
        self._temperature_edit.setText(
            "" if temperature is None else str(temperature)
        )
        
        explicit = config.get('vision')
        inferred = False
        try:
            inferred = bool(self._bot.supports_vision(name))
        except Exception:
            inferred = False
        
        if explicit is None:
            self._vision_check.setChecked(inferred)
            self._vision_hint.setText(
                f"config.yaml 未显式配置 vision，当前按模型名"
                f"（{model or '未填写'}）自动判断为"
                f"「{'支持' if inferred else '不支持'}」图片输入；"
                f"保存后会写入显式配置。"
            )
        else:
            self._vision_check.setChecked(_to_bool_value(explicit, False))
            self._vision_hint.setText(
                "config.yaml 已显式配置 vision，可随时修改。"
            )
        
        self._set_status("")
    
    def _on_toggle_api_key(self, checked: bool) -> None:
        """切换 API Key 的明文显示"""
        self._api_key_edit.setEchoMode(
            QLineEdit.EchoMode.Normal if checked
            else QLineEdit.EchoMode.Password
        )
        self._api_key_toggle.setText("隐藏" if checked else "显示")
    
    def _on_save(self) -> None:
        """保存配置：写回 config.yaml 并让 AI 立即生效"""
        provider = self._provider_combo.currentText().strip()
        if not provider:
            self._set_status("请先选择提供商。", error=True)
            return
        
        values: Dict[str, Any] = {
            "api_key": self._api_key_edit.text().strip(),
            "base_url": self._base_url_edit.text().strip(),
            "model": self._model_edit.text().strip(),
            "vision": self._vision_check.isChecked(),
        }
        
        temperature_text = self._temperature_edit.text().strip()
        if temperature_text:
            try:
                values["temperature"] = float(temperature_text)
            except ValueError:
                self._set_status("Temperature 需要是数字，例如 0.7。", error=True)
                return
        
        updates: Dict[Tuple[str, ...], Any] = {("active_provider",): provider}
        for key, value in values.items():
            updates[("providers", provider, key)] = value
        
        try:
            self._bot.config_manager.set_values(updates)
            self._bot.change_provider(
                provider, reload_config=True, keep_history=True
            )
        except Exception as e:
            self._set_status(f"保存失败：{e}", error=True)
            return
        
        self.reload_fields()
        self._set_status(f"已保存，当前使用「{provider}」。")
        self.config_saved.emit()


# ============================================================================
# 消息行入场动画
# ============================================================================

class RowEntranceAnimator(QObject):
    """
    新消息行的入场动画：淡入 + 高度展开

    发送消息后新气泡不再瞬间出现，而是从上方淡入并向下展开，
    让聊天区的更新更流畅。动画结束后自动清理。
    """

    def __init__(self, container: QWidget, duration: int = 220) -> None:
        super().__init__(container)
        self._container = container
        self._duration = max(60, duration)
        self._elapsed = 0
        self._target_height = max(1, container.sizeHint().height())
        self._effect = QGraphicsOpacityEffect(container)
        self._effect.setOpacity(0.0)
        container.setGraphicsEffect(self._effect)
        container.setMaximumHeight(0)
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _tick(self) -> None:
        """推进动画：更新高度与透明度"""
        self._elapsed += 16
        t = min(1.0, self._elapsed / self._duration)
        eased = 1.0 - (1.0 - t) ** 3  # OutCubic 缓动
        self._container.setMaximumHeight(int(self._target_height * eased))
        self._effect.setOpacity(eased)
        if t >= 1.0:
            self._timer.stop()
            self._container.setMaximumHeight(16777215)  # 恢复无限制
            # setGraphicsEffect(None) 用于移除特效；PySide6 存根未声明 Optional，
            # 但运行时 Qt 允许传 None，故此处忽略类型检查。
            self._container.setGraphicsEffect(None)  # type: ignore[arg-type]
            self.deleteLater()


# ============================================================================
# 主窗口
# ============================================================================

class ChatWindow(QMainWindow):
    """
    聊天主窗口
    """
    
    #: 审批请求投递信号（worker 线程 -> 主线程）。
    #: 参数是 (PermissionRequest, request_id)：内核类型用 object 承载，
    #: 界面不强依赖 castorice 的类型定义（鸭子类型读取，见
    #: describe_permission_request）。
    approval_requested = Signal(object, str)
    
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
    # 消息区最多保留的行数（超出后从最早的行开始回收）
    MAX_MESSAGE_ROWS = 200
    # 界面审批等待上限（秒）。内核的 DEFAULT_APPROVAL_TIMEOUT 是 120s，
    # 那对坐在屏幕前的人来说太久了：等待期间 worker 线程是被阻塞的，
    # 用户去开个会回来只会看到一条「超时」错误。60s 是 A+B 裁决值，
    # 只在真实 GUI 路径设置；测试路径不注册应答者，因此不受它影响。
    APPROVAL_TIMEOUT_S = 60.0
    # 关闭窗口时若线程仍未结束，先挂在这里保命（销毁运行中的 QThread 会崩溃）
    _orphan_workers: List["AIWorker"] = []
    
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
        self._scroll_anim: Optional[QPropertyAnimation] = None  # 平滑滚动动画
        self._theme_mode: str = ThemeSettings.load()  # 显示模式（system/light/dark）
        self._settings_dialog: Optional[SettingsDialog] = None  # 设置窗口
        self._pending_images: List[str] = []      # 待发送的图片路径
        self._attachment_items: List[AttachmentItem] = []
        # 模式（Chat / Work / Code / Learn）
        self._mode_name: str = ModeSettings.DEFAULT   # 当前界面模式
        self._mode_infos: List[Dict[str, Any]] = []    # 内核给出的模式元数据
        self._mode_synced: bool = False                # 是否已与内核建立模式联系
        self._local_workdirs: Dict[str, str] = {}      # 内核不可用时的目录记忆
        self._mode_bar: Optional[ModeSwitchBar] = None
        self._side_panel: Optional[SidePanel] = None
        # 审批接线（应答者注册在 _init_ui 之后、用 hasattr 探测）
        self._approval_bar: Optional[ApprovalBar] = None
        self._approval_responder_installed: bool = False
        # 跨线程投递：worker 线程 emit，Qt 自动排队到主线程执行槽函数
        self.approval_requested.connect(self._on_approval_requested)
        
        self._init_ui()
        
        # 界面搭好后接上审批应答者：它需要信号已经连到槽函数，
        # 否则 worker 线程投递过来的第一件事就会落空。
        self._install_approval_responder()
    
    def _init_ui(self) -> None:
        """初始化UI"""
        self.setWindowTitle("CastoriceAgent")
        self.setFixedSize(Sizes.WINDOW_WIDTH, Sizes.WINDOW_HEIGHT)
        self._center_on_screen()
        
        # 先应用已保存的显示模式，再构建界面，避免首帧主题错误
        Colors.set_mode(self._theme_mode)
        
        # 已保存的模式要在建界面之前读到，否则首帧会先亮起 Chat 再跳走
        self._mode_name = normalize_mode_name(self._load_saved_mode())
        
        self._setup_window_flags()
        self._setup_style()
        self._setup_palette()
        self._setup_central_widget()
        self._setup_title_bar()
        self._setup_message_area()
        self._setup_input_area()
        self._setup_mode_bar()
        self._setup_timer()
        
        # 界面搭好后再与内核同步一次（失败时静默降级为纯界面模式）
        self.refresh_modes()
    
    def _setup_style(self) -> None:
        """设置全局样式（背景不透明，颜色随当前主题解析）"""
        self.setStyleSheet(f"""
            QMainWindow {{
                background-color: {Colors.WINDOW_BG};
            }}
            QTextEdit {{
                background-color: {Colors.INPUT_BG};
                color: {Colors.INPUT_TEXT};
                border: 1px solid {Colors.INPUT_BORDER};
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
        """设置调色板（随当前主题）"""
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor(Colors.WINDOW_BG))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(Colors.INPUT_TEXT))
        palette.setColor(QPalette.ColorRole.Base, QColor(Colors.INPUT_BG))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor(Colors.INPUT_BG))
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
        
        # 横向主体：对话区（左） + 信息面板（右）
        ## 面板与消息区同一行，因此模式条 + 对话区 + 面板构成完整一屏；
        ## 面板默认隐藏，只有可绑定目录的模式才占位（Chat 不显示）。
        body = QWidget()
        body.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self._body_widget = body
        
        self._body_layout = QHBoxLayout(body)
        self._body_layout.setContentsMargins(0, 0, 0, 0)
        self._body_layout.setSpacing(10)
        
        self._conversation_widget = content
        self._body_layout.addWidget(content, 1)
        
        self._side_panel = SidePanel(body)
        self._side_panel.bind_requested.connect(self._on_bind_workdir)
        self._side_panel.unbind_requested.connect(self._on_unbind_workdir)
        self._side_panel.hide()
        self._body_layout.addWidget(self._side_panel, 0)
        
        outer.addWidget(body, 1)
    
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
        """设置自定义标题栏（含设置/最小化/关闭按钮）"""
        self._title_bar = TitleBar(
            self._central_widget, title=self.windowTitle()
        )
        self._title_bar.settings_clicked.connect(self._on_settings_clicked)
        self._outer_layout.insertWidget(0, self._title_bar)    
    def _setup_mode_bar(self) -> None:
        """设置模式切换条（紧贴标题栏下方，与标题栏同宽）"""
        self._mode_bar = ModeSwitchBar(
            list(FALLBACK_MODES), self._mode_name, self._central_widget
        )
        self._mode_bar.mode_selected.connect(self._on_mode_selected)
        self._outer_layout.insertWidget(1, self._mode_bar)    
    # ------------------------------------------------------------------------
    # 审批接线（工具调用需要用户授权）
    #
    # 为什么必须接线：内核的默认审批策略是 ask，而 ApprovalBroker 在
    # 「没有应答者」时的判定是 unavailable —— 按拒绝关闭。也就是说界面不接，
    # Work/Code/Learn 下**每一个有副作用的工具**都会被静默拒绝，
    # 文件写不出去、命令跑不起来，用户还看不到任何提示。
    #
    # 线程纪律（本节的硬约束）：
    #   * 应答者由 **AIWorker 线程**调用，因此它只能做两件事：
    #     broker.defer(request_id) 登记 + emit 信号投递主线程，然后 return None；
    #   * 绝不在 worker 线程里创建、显示或 exec() 任何 Qt 控件——
    #     Qt 的控件只能在主线程创建，跨线程碰控件是随机崩溃的经典来源；
    #   * 真正的用户交互发生在主线程的槽函数里，答复通过
    #     broker.resolve(request_id, decision) 唤醒 worker 的等待。
    # ------------------------------------------------------------------------
    
    def _install_approval_responder(self) -> bool:
        """
        在真实 GUI 启动路径注册审批应答者

        用 hasattr 探测上游接口而不是假定它存在：演示用的 MockBot、以及
        任何早于 t4 的旧实现都没有 set_approval_responder（也没有
        approval_broker），此时界面照常工作，只是不会有工具需要授权
        （Chat 模式本来就没有可见工具）。

        Returns:
            是否成功接上（探测失败或注册抛异常时为 False，绝不向上抛）
        """
        if self._approval_responder_installed:
            return True
        setter = getattr(self._bot, "set_approval_responder", None)
        if not callable(setter):
            # 不强制：没有审批接口的 bot 仍然可以用界面（演示模式）
            return False
        try:
            setter(self._on_permission_request)
        except Exception:
            return False
        self._apply_approval_timeout()
        self._approval_responder_installed = True
        return True

    def _apply_approval_timeout(self) -> bool:
        """把界面审批的等待上限压到 APPROVAL_TIMEOUT_S（broker.timeout 可写）

        内核默认 120s；界面路径用 60s，与审批条上的倒计时保持一致。
        探测不到 broker / timeout 时静默跳过，不影响功能。
        """
        broker = getattr(self._bot, "approval_broker", None)
        if broker is None:
            return False
        try:
            if hasattr(broker, "timeout"):
                broker.timeout = float(ChatWindow.APPROVAL_TIMEOUT_S)
                return True
        except Exception:
            return False
        return False

    # ---- worker 线程侧（只登记 + 投递，不碰控件） ----

    def _on_permission_request(self, request: Any, request_id: str = ""):
        """审批应答者：**运行在 AIWorker 线程**

        允许的写法只有一种：
          1. 拿 broker，调 defer(request_id) 登记待决请求；
          2. emit approval_requested 把 (request, request_id) 交给主线程；
          3. return None —— 表示「等主线程 resolve」，broker 会阻塞等待，
             超时即按 unavailable（拒绝）关闭。

        任何一步失败都返回 None：宁可让内核按超时拒绝，也绝不在
        worker 线程里放行一次未经用户确认的副作用。
        """
        broker = getattr(self._bot, "approval_broker", None)
        if broker is None:
            return None
        try:
            defer = getattr(broker, "defer", None)
            if callable(defer):
                defer(request_id)
            else:
                # 没有 defer 就没有等待机制：直接放弃（fail-closed）
                return None
            # 信号投递是队列式的：这里不会阻塞，也不会碰控件。
            self.approval_requested.emit(request, str(request_id))
        except Exception:
            return None
        return None

    # ---- 主线程侧（真正与用户交互） ----

    def _on_approval_requested(self, request: Any, request_id: str) -> None:
        """主线程槽：在审批条上显示这次请求（控件只在这里创建/更新）"""
        bar = self._approval_bar
        if bar is None:
            # 界面没建好就没有答复能力，让 broker 按超时拒绝
            return
        bar.start(request, request_id, ChatWindow.APPROVAL_TIMEOUT_S)

    def _on_approval_allow(self, request_id: str) -> None:
        """用户点了「允许」：只批准这一次（allowed-once，不记忆、不缓存）"""
        self._resolve_approval(request_id, "allow")

    def _on_approval_deny(self, request_id: str) -> None:
        """用户点了「拒绝」或倒计时归零"""
        self._resolve_approval(request_id, "deny")

    def _resolve_approval(self, request_id: str, decision: str) -> bool:
        """把答复送回内核的 broker；返回是否成功投递。

        投递失败（例如请求已被别处以超时结束）不算错误：
        broker.resolve 找不到待决 id 时返回 False，这条路径本来就是竞态。
        """
        broker = getattr(self._bot, "approval_broker", None)
        resolver = getattr(broker, "resolve", None)
        if not callable(resolver):
            return False
        try:
            return bool(resolver(str(request_id), decision))
        except Exception:
            return False

    def _approval_waiting(self) -> bool:
        """是否有审批正在等待用户决定（供测试与守卫使用）"""
        bar = self._approval_bar
        return bool(bar is not None and bar.is_waiting())
    
    def _cancel_pending_approval(self) -> None:
        """收起审批条并按拒绝答复——回复轮结束时调用

        轮次结束时若还有悬空的审批条，说明答复已经不可能被消费
        （worker 已经离开），此时必须显式拒绝并把条收起来，
        否则它会一直挂在界面上骗用户点。
        """
        bar = self._approval_bar
        if bar is None or not bar.is_waiting():
            return
        bar.respond(False)
    
    def approval_timeout(self) -> float:
        """当前生效的审批等待上限（秒）"""
        broker = getattr(self._bot, "approval_broker", None)
        value = getattr(broker, "timeout", None)
        if isinstance(value, (int, float)):
            return float(value)
        return float(ChatWindow.APPROVAL_TIMEOUT_S)
    
    # ------------------------------------------------------------------------
    # 显示模式（主题）
    # ------------------------------------------------------------------------
    
    def _on_settings_clicked(self) -> None:
        """点击设置按钮：打开独立设置窗口"""
        if self._settings_dialog is None:
            self._settings_dialog = SettingsDialog(
                self._bot, self._theme_mode, self
            )
            self._settings_dialog.theme_selected.connect(self._on_theme_selected)
            self._settings_dialog.config_saved.connect(self._on_config_saved)
        
        # 每次打开都同步一次当前状态与配置
        self._settings_dialog.set_theme_mode(self._theme_mode)
        self._settings_dialog.reload_fields()
        self._settings_dialog.apply_theme()
        
        self._settings_dialog.show()
        self._settings_dialog.raise_()
        self._settings_dialog.activateWindow()
        
        # 首次打开时定位到设置按钮下方（右对齐）
        if not getattr(self._settings_dialog, "_positioned", False):
            self._settings_dialog._positioned = True
            button = self._title_bar._settings_button
            if button is not None:
                anchor = button.mapToGlobal(
                    QPoint(0, button.height() + 6)
                )
                anchor.setX(
                    anchor.x() - self._settings_dialog.width() + button.width()
                )
                screen = QApplication.screenAt(anchor) or QApplication.primaryScreen()
                if screen is not None:
                    area = screen.availableGeometry()
                    x = max(
                        area.left() + 4,
                        min(anchor.x(), area.right() - self._settings_dialog.width() - 4)
                    )
                    y = max(
                        area.top() + 4,
                        min(anchor.y(), area.bottom() - self._settings_dialog.height() - 4)
                    )
                    anchor = QPoint(x, y)
                self._settings_dialog.move(anchor)
    
    def _on_theme_selected(self, mode: str) -> None:
        """设置窗口里切换了主题：立即应用"""
        self._theme_mode = mode
        self.apply_theme()
    
    def _on_config_saved(self) -> None:
        """设置窗口保存了模型配置：同步输入区的图片按钮可用状态"""
        self._update_image_button_visibility()
    
    def apply_theme(self) -> None:
        """按当前显示模式刷新整个界面"""
        Colors.set_mode(self._theme_mode)
        
        # 全局样式与调色板
        self._setup_style()
        self._setup_palette()
        
        # 标题栏
        self._title_bar.refresh_theme()
        
        # 模式切换条、右侧面板与审批条
        if self._mode_bar is not None:
            self._mode_bar.refresh_theme()
        if self._side_panel is not None:
            self._side_panel.refresh_theme()
        if self._approval_bar is not None:
            self._approval_bar.refresh_theme()
        
        # 消息区已有气泡
        for i in range(self._message_layout.count()):
            item = self._message_layout.itemAt(i)
            if item is None:
                continue
            container = item.widget()
            if container is None:
                continue
            for child in container.children():
                if isinstance(child, ChatBubble):
                    child.refresh_theme()
                elif isinstance(child, AvatarLabel):
                    child.refresh_theme()
        
        # 加载指示器（若正在显示）
        if self._loading_dots is not None:
            self._loading_dots.setStyleSheet(f"""
                QLabel {{
                    color: {Colors.LOADING_DOT_COLOR};
                    background-color: transparent;
                    font-weight: bold;
                }}
            """)
        
        # 工具状态气泡（若正在显示）
        for name in list(self._tool_labels.keys()):
            label = self._tool_labels[name]
            label.setStyleSheet(f"""
                QLabel {{
                    color: {Colors.TOOL_TEXT};
                    background-color: {Colors.TOOL_BG};
                    border-radius: 6px;
                    padding: 3px 10px;
                    font-size: 9pt;
                }}
            """)
        
        # 表情包选择面板（若已创建）
        if self._emoji_picker is not None:
            self._emoji_picker._apply_theme()
        
        # 待发送图片缩略图
        for item in self._attachment_items:
            item.refresh_theme()
        
        # 设置窗口（若已打开）
        if self._settings_dialog is not None:
            self._settings_dialog.apply_theme()
        
        # 强制重绘（标题栏背景由 paintEvent 自绘）
        self.update()
        self._title_bar.update()
        if self._mode_bar is not None:
            self._mode_bar.update()
    
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
        
        # 视口显式透明：让消息区透出窗口的不透明主题背景
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
        """设置输入区域：待发送图片 + 输入框 + 底部按钮行"""
        input_widget = QWidget()
        input_widget.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        
        layout = QVBoxLayout(input_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        
        # 待发送图片预览区（有图片时才显示，位于输入框上方）
        self._attachment_bar = QWidget()
        self._attachment_bar.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self._attachment_layout = QHBoxLayout(self._attachment_bar)
        self._attachment_layout.setContentsMargins(2, 0, 2, 0)
        self._attachment_layout.setSpacing(8)
        self._attachment_layout.addStretch()
        self._attachment_bar.hide()
        layout.addWidget(self._attachment_bar)
        
        # 审批条（有副作用的工具在执行前需要用户授权，默认隐藏）
        self._approval_bar = ApprovalBar(input_widget)
        self._approval_bar.approved.connect(self._on_approval_allow)
        self._approval_bar.denied.connect(self._on_approval_deny)
        layout.addWidget(self._approval_bar)
        
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
        
        # 底部按钮行（右对齐）：图片按钮 + 表情包按钮 + 发送按钮
        button_row = QWidget()
        button_row.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        button_layout = QHBoxLayout(button_row)
        button_layout.setContentsMargins(0, 0, 2, 0)
        button_layout.setSpacing(6)
        
        # 图片按钮（仅当前模型支持图片输入时显示）
        self._image_button = InputIconButton(
            "image",
            tooltip="发送图片（仅视觉模型可用；选中后显示在输入框上方）",
            size=Sizes.EMOJI_BUTTON_SIZE,
            parent=button_row
        )
        self._image_button.clicked.connect(self._on_pick_image)
        
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
        button_layout.addWidget(self._image_button)
        button_layout.addWidget(self._emoji_button)
        button_layout.addWidget(self._send_button)
        
        layout.addWidget(self._input_text)
        layout.addWidget(button_row)
        self._content_layout.addWidget(input_widget, 0)
        
        self._update_image_button_visibility()
    
    # ------------------------------------------------------------------------
    # 图片发送
    # ------------------------------------------------------------------------
    
    def _update_image_button_visibility(self) -> None:
        """按当前模型是否支持图片输入，决定图片按钮是否显示"""
        supported = False
        try:
            supported = bool(self._bot.supports_vision())
        except Exception:
            supported = False
        
        button = getattr(self, "_image_button", None)
        if button is None:
            return
        button.setVisible(supported)
        if not supported:
            self._clear_attachments()
    
    def _on_pick_image(self) -> None:
        """选择要发送的图片（可多选）"""
        if not self._input_text.isEnabled():
            return  # AI 回复中
        
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "选择图片",
            "",
            "图片文件 (*.png *.jpg *.jpeg *.webp *.gif *.bmp);;所有文件 (*)"
        )
        if not paths:
            return
        
        for path in paths:
            if path not in self._pending_images:
                self._pending_images.append(path)
        self._refresh_attachments()
        self._input_text.setFocus()
    
    def _refresh_attachments(self) -> None:
        """重建待发送图片预览区"""
        # 清掉旧控件
        for item in self._attachment_items:
            self._attachment_layout.removeWidget(item)
            item.deleteLater()
        self._attachment_items.clear()
        
        for path in self._pending_images:
            item = AttachmentItem(path, self._attachment_bar)
            item.removed.connect(self._remove_attachment)
            # 插在 stretch 之前，保持左对齐
            self._attachment_layout.insertWidget(
                self._attachment_layout.count() - 1, item
            )
            self._attachment_items.append(item)
        
        self._attachment_bar.setVisible(bool(self._pending_images))
        self._on_input_changed()
    
    def _remove_attachment(self, path: str) -> None:
        """移除一张待发送图片"""
        if path in self._pending_images:
            self._pending_images.remove(path)
        self._refresh_attachments()
    
    def _clear_attachments(self) -> None:
        """清空待发送图片"""
        if not self._pending_images:
            return
        self._pending_images.clear()
        self._refresh_attachments()
    

    # ------------------------------------------------------------------------
    # 模式（Chat / Work / Code / Learn）
    #
    # 分工：
    #   * 模式的权威状态在内核（config.yaml 的 mode 键 + castorice 的会话日志）；
    #   * 界面只负责「显示哪一个模式、显示哪一份历史、显示哪些工具 UI」；
    #   * 内核不可用时（例如离线演示），界面仍能切换并给出可读提示。
    # ------------------------------------------------------------------------

    def _load_saved_mode(self) -> str:
        """读取上次使用的模式：内核 config.yaml 优先，其次 ui_settings.json"""
        config = getattr(self._bot, "config_manager", None)
        getter = getattr(config, "get", None)
        if callable(getter):
            try:
                raw = getter("mode")
            except Exception:
                raw = None
            if isinstance(raw, str) and raw.strip():
                return normalize_mode_name(raw)
        return normalize_mode_name(ModeSettings.load())

    def current_mode(self) -> str:
        """当前界面模式（chat | work | code | learn）"""
        return self._mode_name

    def mode_names(self) -> List[str]:
        """模式切换条上的模式名（顺序即界面顺序）"""
        return [item["name"] for item in self._mode_infos]

    def mode_info(self, mode: Optional[str] = None) -> Dict[str, Any]:
        """某模式的元数据（内核给出的优先，缺失时用内置兜底）"""
        name = normalize_mode_name(mode or self._mode_name)
        for item in self._mode_infos:
            if normalize_mode_name(item.get("name")) == name:
                merged = dict(item)
                break
        else:
            merged = next(
                (dict(item) for item in FALLBACK_MODES if item["name"] == name),
                dict(FALLBACK_MODES[0]),
            )
        local = self._local_workdirs.get(name)
        if local and not merged.get("workdir"):
            merged["workdir"] = local
        return merged

    def mode_display_name(self, mode: Optional[str] = None) -> str:
        """模式的中文名（界面文案用）"""
        info = self.mode_info(mode)
        return str(info.get("display_name") or info.get("name") or self._mode_name)

    def mode_bindable(self, mode: Optional[str] = None) -> bool:
        """该模式是否允许绑定目录（Chat 为 False，因此不显示目录绑定入口）"""
        info = self.mode_info(mode)
        if "bindable" in info:
            return bool(info["bindable"])
        policy = info.get("workdir_policy")
        if isinstance(policy, dict) and "bindable" in policy:
            return bool(policy["bindable"])
        return normalize_mode_name(info.get("name")) != ModeSettings.DEFAULT

    def mode_workdir(self, mode: Optional[str] = None) -> Optional[str]:
        """该模式当前绑定的目录（未绑定返回 None）"""
        value = self.mode_info(mode).get("workdir")
        return str(value) if value else None

    def visible_tools(self, mode: Optional[str] = None) -> List[str]:
        """该模式下对模型可见的工具名；Chat 恒为空列表（不显示任何工具 UI）"""
        name = normalize_mode_name(mode or self._mode_name)
        if name == ModeSettings.DEFAULT:
            return []
        tools = self.mode_info(name).get("visible_tools")
        if isinstance(tools, (list, tuple)):
            return [str(item) for item in tools]
        return []

    def tools_visible(self, mode: Optional[str] = None) -> bool:
        """该模式下是否显示工具调用提示行"""
        return bool(self.visible_tools(mode))

    # ---- 刷新与切换 ----

    def refresh_modes(self) -> None:
        """
        与内核同步一次模式列表

        内核不可用时退回内置的四种模式，界面依旧可切换（只是没有内核行为）。
        这是「失败安全」的做法：宁可少给工具，也不要让界面卡住。
        """
        self._reload_mode_infos()
        names = [item["name"] for item in self._mode_infos]
        if self._mode_name not in names:
            self._mode_name = names[0] if names else ModeSettings.DEFAULT
        self._mode_synced = self._sync_kernel_mode()
        if self._mode_bar is not None:
            self._mode_bar.set_modes(self._mode_infos)
            self._mode_bar.set_active_mode(self._mode_name)
        self._apply_mode_visibility()

    def _sync_kernel_mode(self) -> bool:
        """
        启动时把内核拉到界面记录的模式

        为什么以界面为准：config.yaml 里可能根本没有 mode 键
        （update_yaml_scalars 不会创建顶层新键），此时内核只会停在 chat，
        而用户上次的选择只能从 ui_settings.json 读出来——若不对齐，
        界面显示「学习」而内核还在 chat，点回「学习」也会被当成「没变化」。

        Returns:
            界面与内核是否已经一致
        """
        current = getattr(self._bot, "mode", None)
        if not isinstance(current, str):
            return False
        if normalize_mode_name(current) == self._mode_name:
            return True
        return self._activate_kernel_mode(self._mode_name, persist=True)

    def _reload_mode_infos(self) -> None:
        """只刷新模式元数据缓存，不重建模式条按钮（切换模式时用）"""
        self._mode_infos = self._fetch_mode_infos()

    def _fetch_mode_infos(self) -> List[Dict[str, Any]]:
        """取模式元数据列表（顺序固定为 chat/work/code/learn，未知模式追加在后）"""
        provider = getattr(self._bot, "available_modes", None)
        if callable(provider):
            try:
                raw = provider()
            except Exception:
                raw = None
            if isinstance(raw, (list, tuple)):
                infos = [
                    dict(item) for item in raw
                    if isinstance(item, dict) and item.get("name")
                ]
                if infos:
                    return self._order_mode_infos(infos)
        return [dict(item) for item in FALLBACK_MODES]

    @staticmethod
    def _order_mode_infos(infos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """按固定顺序重排模式（未知模式保持原有相对顺序追加在后）"""
        order = [item["name"] for item in FALLBACK_MODES]
        ordered = [
            item for name in order for item in infos if item["name"] == name
        ]
        ordered.extend(item for item in infos if item["name"] not in order)
        return ordered

    def _on_mode_selected(self, mode: str) -> None:
        """模式条上点了某个模式"""
        self.switch_mode(mode)

    def switch_mode(self, mode: str, *, persist: bool = True) -> bool:
        """
        切换模式（界面入口）

        切换会：
        1. 让内核切到该模式自己的会话（四个模式的对话因此互不串台）；
        2. 清空当轮消息区，并从新会话的事件日志重建历史；
        3. 提交旧会话的改动并按单写者约定关闭它——
           内核每个 session_id 只允许一个写入方，不 close 会让会话无法再打开；
        4. 刷新模式条选中态与右侧面板，并按新模式调整工具类 UI 的可见性。

        Returns:
            是否真的发生了切换（已在该模式、或回复进行中时返回 False）
        """
        target = normalize_mode_name(mode)
        if target == self._mode_name and self._mode_synced:
            return False
        if not self._mode_switch_allowed():
            return False

        previous = self._mode_name
        self._mode_name = target
        self._commit_pending_session(previous)
        activated = self._activate_kernel_mode(target, persist=persist)
        self._mode_synced = activated
        # 新模式可能有自己的工作目录绑定，元数据缓存要重新取一次
        self._reload_mode_infos()

        # 先收拾流式状态，再重建消息区，避免残留字符落进新会话
        self._reset_stream_state()
        self._clear_messages()
        self._replay_session_history()

        if self._mode_bar is not None:
            self._mode_bar.set_active_mode(target)
        self._apply_mode_visibility()
        ModeSettings.save(target)
        if not activated:
            self._show_mode_notice(
                f"已切换到「{self.mode_display_name(target)}」模式（未连接到 AI 内核，仅界面生效）"
            )
        return True

    def _mode_switch_allowed(self) -> bool:
        """回复进行中不允许切模式：工作线程正在写旧会话，切换会打乱状态"""
        worker = self._worker
        return not (worker is not None and worker.isRunning())

    def _activate_kernel_mode(self, mode: str, *, persist: bool = True) -> bool:
        """让内核切到目标模式；返回是否成功"""
        switcher = getattr(self._bot, "set_mode", None)
        if not callable(switcher):
            return False
        try:
            switcher(mode, persist=persist)
        except TypeError:
            # 兼容不支持 persist 关键字的实现
            try:
                switcher(mode)
            except Exception:
                return False
        except Exception:
            return False
        return True

    def _commit_pending_session(self, mode: Optional[str] = None) -> None:
        """
        提交并释放某个模式的会话

        三步：
        1. flush —— 持久性屏障，未落盘的事件不该随着切换丢掉；
        2. close —— 释放单写者登记（SessionStore 对同一份日志只允许一个写入方，
           不释放的话「打开其它会话」会直接抛 SessionInUseError）；
        3. 让内核忘掉这条缓存，下次激活该模式时重新从磁盘恢复。

        任何一步失败都不阻断切换：界面必须始终可用，内核有自己的重试策略。
        """
        name = normalize_mode_name(mode or self._mode_name)
        store = self._mode_session(name)
        if store is None:
            return
        try:
            if bool(getattr(store, "dirty", False)):
                store.flush("mode-switch")
        except Exception:
            pass
        try:
            store.close()
        except Exception:
            pass
        self._forget_kernel_session(name, store)

    def _mode_session(self, mode: str) -> Any:
        """取某模式的会话对象（演示模式或旧版 ai.py 返回 None）"""
        sessions = getattr(self._bot, "sessions", None)
        if isinstance(sessions, dict):
            store = sessions.get(mode)
            if store is not None and hasattr(store, "close"):
                return store
        store = getattr(self._bot, "session", None)
        if store is None or not hasattr(store, "close"):
            return None
        return store

    def _forget_kernel_session(self, mode: str, store: Any) -> None:
        """
        把已关闭的会话从内核的会话缓存里摘掉

        内核按「模式名 → 会话」做缓存，关闭后若仍留在缓存里，
        下次切回该模式会拿到一个已关闭的会话（写入直接报「会话已关闭」）。
        摘除后内核会按 latest_session_id 重新打开最近一份日志，历史照样恢复。
        只在缓存里确为同一个对象时才摘，避免误伤内核新建的会话。
        """
        sessions = getattr(self._bot, "_sessions", None)
        if not isinstance(sessions, dict):
            return
        if sessions.get(mode) is store:
            sessions.pop(mode, None)

    def _reset_stream_state(self) -> None:
        """清空流式解析与打字机状态"""
        if self._typewriter_timer is not None:
            self._typewriter_timer.stop()
        self._typewriter_chars.clear()
        self._emoji_parse_buffer = ""
        self._ai_emoji_shown = 0
        self._remove_loading_indicator()
        self._finish_pending_tool_labels()
        self._streaming_bubble = None
        self._streaming_container = None
        self._cancel_pending_approval()

    def _clear_messages(self) -> None:
        """清空消息区（切换模式与「清空聊天记录」共用）"""
        layout = self._message_layout
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._loading_container = None
        self._loading_dots = None
        self._streaming_bubble = None
        self._streaming_container = None
        self._tool_labels.clear()

    def _replay_session_history(self) -> None:
        """
        重新加载当前模式自己的会话历史

        历史来自内核（从事件日志派生），因此四个模式的对话互不串台；
        历史为空时消息区保持空白，不显示任何占位内容。
        system 消息属于提示词而不是对话，跳过不显示。
        """
        for message in self._mode_history():
            role = str(message.get("role") or "")
            if role == "system":
                continue
            content = message.get("content")
            if isinstance(content, list):
                content = self._flatten_content(content)
            text = str(content or "").strip()
            if not text:
                continue
            self.add_message(text, is_user=(role == "user"))

    def _mode_history(self) -> List[Dict[str, Any]]:
        """当前模式的历史消息（只读；取不到时返回空列表）"""
        getter = getattr(self._bot, "get_history", None)
        if not callable(getter):
            return []
        try:
            history = getter()
        except Exception:
            return []
        if not isinstance(history, (list, tuple)):
            return []
        return [dict(item) for item in history if isinstance(item, dict)]

    @staticmethod
    def _flatten_content(content: List[Any]) -> str:
        """把多模态内容的文本片段拼起来（图片只留一个数量占位）"""
        parts: List[str] = []
        images = 0
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text" and item.get("text"):
                parts.append(str(item["text"]))
            elif item.get("type") == "image_url":
                images += 1
        text = " ".join(part.strip() for part in parts if part.strip())
        if images:
            text = (text + " " if text else "") + f"[图片 x {images}]"
        return text.strip()

    def _apply_mode_visibility(self) -> None:
        """按当前模式调整工具类 UI 的可见性（Chat 一律不显示）"""
        bindable = self.mode_bindable(self._mode_name)
        panel = self._side_panel
        if panel is not None:
            panel.set_mode_info(self.mode_info(self._mode_name) if bindable else None)
            panel.setVisible(bindable)
        if self._mode_bar is not None:
            self._mode_bar.set_active_mode(self._mode_name)
        if not self.tools_visible(self._mode_name):
            self._finish_pending_tool_labels()

    def _show_mode_notice(self, text: str) -> None:
        """在消息区顶部之外显示一条居中的浅色提示（模式反馈用）"""
        label = QLabel(str(text))
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setWordWrap(True)
        label.setStyleSheet(f"""
            QLabel {{
                color: {Colors.PANEL_MUTED};
                background-color: transparent;
                border: none;
                font-size: 9pt;
                padding: 2px 8px;
            }}
        """)
        self._message_layout.addWidget(label)

    # ---- 目录绑定 ----

    def _on_bind_workdir(self, mode: str) -> None:
        """面板上点了「绑定目录」：选目录后交给内核校验并记录"""
        target = normalize_mode_name(mode or self._mode_name)
        current = self.mode_workdir(target)
        start_dir = current if current and os.path.isdir(current) else str(app_root())
        chosen = QFileDialog.getExistingDirectory(
            self, f"为「{self.mode_display_name(target)}」模式选择目录", start_dir
        )
        if not chosen:
            return
        self.bind_workdir(target, chosen)

    def _on_unbind_workdir(self, mode: str) -> None:
        """面板上点了「解除绑定」"""
        self.bind_workdir(mode or self._mode_name, None)

    def bind_workdir(self, mode: str, path: Optional[str]) -> bool:
        """
        绑定（path 为空表示解绑）某模式的目录

        校验交给内核（只有可信根之内的路径才被接受）；内核不可用时只更新界面，
        不改动任何文件。失败会把原因写回面板提示区，而不是静默吞掉。
        """
        target = normalize_mode_name(mode)
        binder = getattr(self._bot, "bind_mode_workdir", None)
        if callable(binder):
            try:
                binder(target, path)
            except Exception as error:
                message = self._workdir_error_text(error)
                if self._side_panel is not None and self._side_panel.isVisible():
                    self._side_panel.show_message(message)
                return False
        else:
            self._local_workdirs[target] = str(path) if path else ""
        self._reload_mode_infos()
        self._apply_mode_visibility()
        return True

    @staticmethod
    def _workdir_error_text(error: Exception) -> str:
        """把内核的工作目录错误翻成一句可读提示"""
        to_dict = getattr(error, "to_dict", None)
        payload = None
        if callable(to_dict):
            try:
                payload = to_dict()
            except Exception:
                payload = None
        if isinstance(payload, dict) and payload.get("message"):
            return str(payload["message"])
        return f"绑定失败：{error}"

    def _on_input_changed(self) -> None:
        """输入内容变化：无文字且无图片，或 AI 回复中时禁用发送按钮"""
        can_send = self._input_text.isEnabled() and (
            bool(self._input_text.toPlainText().strip())
            or bool(self._pending_images)
        )
        if self._send_button.isEnabled() != can_send:
            self._send_button.setEnabled(can_send)
    
    def _setup_timer(self) -> None:
        """设置定时器"""
        self._resize_timer = QTimer(self)  # 挂到窗口下，窗口销毁时一并回收
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
        """窗口首次显示时关闭系统默认圆角（保持自绘圆角的干净背景）"""
        super().showEvent(event)
        if not self._region_applied:
            self._region_applied = True
            self._apply_window_region()
            self.update()

    def _apply_window_region(self) -> None:
        """
        关闭 Windows 系统默认圆角，避免与自绘圆角叠加出方框。

        窗口背景由 paintEvent 自绘为不透明圆角（圆角外透明），
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
        """绘制圆角不透明窗口背景（圆角外透明，形成干净圆角）"""
        super().paintEvent(event)
        
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, Sizes.WINDOW_RADIUS, Sizes.WINDOW_RADIUS)
        
        # 不透明：直接填充主题背景色（圆角外因 WA_TranslucentBackground 保持透明）
        painter.fillPath(path, QColor(Colors.WINDOW_BG))
        painter.end()
    
    def closeEvent(self, event: Any) -> None:
        """窗口关闭时停止后台线程并关闭设置窗口，避免残留"""
        if self._typewriter_timer is not None:
            self._typewriter_timer.stop()
        
        if self._loading_timer is not None:
            self._loading_timer.stop()
        
        if self._resize_timer is not None:
            self._resize_timer.stop()
        
        if self._scroll_anim is not None:
            self._scroll_anim.stop()
        
        if self._settings_dialog is not None:
            self._settings_dialog.close()
            self._settings_dialog = None
        
        # 关窗时若还有审批在等：先按拒绝答复再收条。
        # 应答者本身不用注销——窗口销毁后信号发不出去，broker 会按超时拒绝，
        # 而它等的是一个已经离开主线程的事件，不会留下悬挂控件。
        self._cancel_pending_approval()
        
        if self._worker is not None and self._worker.isRunning():
            self._worker.stop()
        
        self._release_worker()
        
        # 会话是单写者的：进程退出前必须提交并释放写入权，
        # 否则下次启动打开同一份会话会被拒绝（SessionInUseError）。
        self._commit_pending_session()
        
        super().closeEvent(event)
    
    def _release_worker(self, wait_ms: int = 5000) -> None:
        """
        安全释放当前工作线程

        QThread 在 run() 尚未返回时被销毁会直接让程序崩溃
        （Qt 报 "Destroyed while thread is still running"），
        因此必须等线程真正结束后再丢弃引用；若等待超时，
        就把引用挂到孤儿列表里，交给线程自己的 finished 信号清理。
        """
        worker = self._worker
        self._worker = None
        if worker is None:
            return
        
        if worker.isRunning() and not worker.wait(wait_ms):
            # 仍在运行：保住引用，等它结束再回收
            ChatWindow._orphan_workers.append(worker)
            worker.finished.connect(
                lambda w=worker: ChatWindow._drop_orphan_worker(w)
            )
            return
        
        worker.deleteLater()
    
    @staticmethod
    def _drop_orphan_worker(worker: "AIWorker") -> None:
        """线程结束后从孤儿列表移除，允许其被回收"""
        try:
            ChatWindow._orphan_workers.remove(worker)
        except ValueError:
            pass
        worker.deleteLater()
    
    # ------------------------------------------------------------------------
    # 核心功能
    # ------------------------------------------------------------------------
    
    def _send_message(self) -> None:
        """发送消息（文字与图片可同时发送）"""
        # 上一轮仍在回复中：直接忽略
        # 否则新线程会覆盖 self._worker 的引用，让仍在运行的旧线程被析构，
        # Qt 会以 "Destroyed while thread is still running" 直接终止进程。
        if self._worker is not None and self._worker.isRunning():
            return
        
        user_input = self._input_text.toPlainText().strip()
        images = list(self._pending_images)
        if not user_input and not images:
            return
        
        self._hide_emoji_picker()
        self._input_text.clear()
        self._clear_attachments()
        
        # 先显示文字，再按选择顺序显示图片（与发给模型的顺序一致）
        if user_input:
            self.add_message(user_input, is_user=True)
        for path in images:
            self.add_image_message(path, is_user=True)
        
        # 禁用输入
        self._input_text.setEnabled(False)
        self._send_button.setEnabled(False)
        
        # 重置流式解析状态
        self._emoji_parse_buffer = ""
        self._ai_emoji_shown = 0
        
        # 显示加载指示器
        self._show_loading_indicator()
        
        # 创建AI工作线程（流式，携带图片）
        self._worker = AIWorker(self._bot, user_input, images)
        self._worker.chunk_received.connect(self._on_chunk_received)
        self._worker.tool_event.connect(self._on_tool_event)
        self._worker.emoji_event.connect(self._on_emoji_event)
        self._worker.finished.connect(self._on_stream_finished)
        self._worker.error.connect(self._on_ai_error)
        self._worker.start()
    
    def add_image_message(self, path: str, is_user: bool = True) -> None:
        """
        把一张图片作为一行消息加入聊天区

        Args:
            path: 图片路径
            is_user: 是否为用户发送的图片
        """
        if not path or not os.path.exists(path):
            return
        
        bubble = ChatBubble(
            "",
            is_user,
            None if is_user else self._ai_avatar_path,
            emoji_path=path,
            emoji_name=os.path.basename(path),
            image_size=Sizes.USER_IMAGE_SIZE,
        )
        self._append_message_row([bubble], is_user)
    
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
        self._animate_row_in(container)
        self._trim_message_rows()
        return container
    
    def _trim_message_rows(self) -> None:
        """
        限制消息区行数，避免长时间使用后界面变卡

        切换模式会重建消息区，但同一模式连续对话仍然会不断累积行；
        这里按「按行裁剪」而不是按消息裁剪，因为一条消息可能占多行
        （文字与表情图分行显示）。被裁掉的行只影响显示，不影响会话日志。
        """
        layout = self._message_layout
        excess = layout.count() - ChatWindow.MAX_MESSAGE_ROWS
        for _ in range(max(0, excess)):
            item = layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is None:
                continue
            widget.setParent(None)
            widget.deleteLater()
    
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
        
        self._animate_scroll_to_bottom()
    
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
        if not self.tools_visible():
            # Chat 模式不显示工具调用提示行（验收条款）；
            # 事件照常消费，避免上游认为没人处理。
            self._finish_pending_tool_labels()
            return
        if status == 'start':
            label = QLabel(f"🔧 正在调用 {name} …")
            label.setStyleSheet(f"""
                QLabel {{
                    color: {Colors.TOOL_TEXT};
                    background-color: {Colors.TOOL_BG};
                    border-radius: 6px;
                    padding: 3px 10px;
                    font-size: 9pt;
                }}
            """)
            self._tool_labels[name] = label
            self._message_layout.addWidget(label, 0, Qt.AlignmentFlag.AlignLeft)
        else:
            # 工具调用完成：移除状态气泡（不再显示"完成"提示）
            label = self._tool_labels.pop(name, None)
            if label is not None:
                self._message_layout.removeWidget(label)
                label.deleteLater()
        self._animate_scroll_to_bottom()

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
        self._animate_row_in(self._streaming_container)

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
        # 整轮结束：悬空的审批条已经不会被消费，收起并按拒绝答复
        self._cancel_pending_approval()
        
        # 恢复输入
        self._input_text.setEnabled(True)
        self._input_text.setFocus()
        self._on_input_changed()
        # 等线程真正结束再释放，避免销毁运行中的 QThread 导致崩溃
        self._release_worker()
    
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
        
        # 出错时同样别把审批条留在界面上
        self._cancel_pending_approval()
        
        self.add_message(f"错误：{error_msg}", is_user=False)
        
        self._input_text.setEnabled(True)
        self._input_text.setFocus()
        self._on_input_changed()
        # 等线程真正结束再释放，避免销毁运行中的 QThread 导致崩溃
        self._release_worker()
    
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
        
        # 丢弃多余表情标记后可能留下连续空格，折叠一下（对原始文本处理，避免动到断行结果）
        for bubble in bubbles:
            if isinstance(bubble, ChatBubble) and bubble._label is not None:
                current = bubble._label.raw_text
                fixed = self.MULTI_SPACE_RE.sub(" ", current).strip()
                if fixed != current:
                    bubble._label.set_raw_text(fixed)
        
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
        """滚动到底部（瞬时，用于逐字输出跟随）"""
        scrollbar = self._scroll_area.verticalScrollBar()
        if scrollbar:
            scrollbar.setValue(scrollbar.maximum())
    
    def _animate_scroll_to_bottom(self) -> None:
        """
        平滑滚动到底部（带缓动）

        新消息插入后内容高度变化，用缓动滚动代替瞬时跳变，
        视觉上更流畅；动画结束再确保对齐到底部。
        """
        scrollbar = self._scroll_area.verticalScrollBar()
        if scrollbar is None:
            return
        
        target = scrollbar.maximum()
        if scrollbar.value() >= target:
            return
        
        if self._scroll_anim is not None:
            self._scroll_anim.stop()
        
        anim = QPropertyAnimation(scrollbar, b"value", self)
        anim.setDuration(240)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.setStartValue(scrollbar.value())
        anim.setEndValue(target)
        anim.finished.connect(self._scroll_to_bottom)
        anim.start()
        self._scroll_anim = anim
    
    def _animate_row_in(self, container: QWidget) -> None:
        """新消息行入场：淡入 + 由上向下展开"""
        # 布局尚未完成时 sizeHint 可能不准，先激活一次
        self._message_layout.activate()
        RowEntranceAnimator(container)
        # 展开过程中滚动最大值会变化，动画结束后补一次平滑滚动
        QTimer.singleShot(240, self._animate_scroll_to_bottom)
    
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
                    # 外层气泡（含头像）的宽度上限必须把气泡自身的内边距与边框算进去，
                    # 否则文本区会比 max_width 少掉约 26px，长行的末尾一两个字被裁掉。
                    child.setMaximumWidth(
                        max_width + ChatBubble.BUBBLE_EXTRA_WIDTH
                        + Sizes.AVATAR_SIZE + 8
                    )
                    # 文本按新的上限重新断行（气泡外框宽度由 ChatBubble 内部同步）
                    child.set_max_text_width(max_width)


# ============================================================================
# 应用程序入口
# ============================================================================

def create_palette() -> QPalette:
    """按当前主题创建调色板"""
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(Colors.WINDOW_BG))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(Colors.INPUT_TEXT))
    palette.setColor(QPalette.ColorRole.Base, QColor(Colors.INPUT_BG))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(Colors.INPUT_BG))
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
    """获取头像文件路径（基于程序根目录，兼容打包后的 exe）"""
    avatar_path = app_root() / relative_path
    
    if avatar_path.exists():
        return str(avatar_path)
    else:
        print(f"警告: 未找到头像文件: {avatar_path}")
        return None


class MockConfigManager:
    """演示用的模拟配置管理器（设置窗口在 MockBot 下也能正常显示）"""

    provider_names = ["mock"]

    @staticmethod
    def get_provider_config(name: str) -> Dict[str, Any]:
        return {
            "api_key": "demo-api-key",
            "base_url": "https://example.com/v1",
            "model": "demo-model",
            "temperature": 0.7,
        }

    @staticmethod
    def set_values(updates: Dict[Any, Any]) -> None:
        """演示模式不写文件"""
        return None


class MockBot:
    """
    模拟AI（含表情包事件，便于离线预览界面效果）

    它同时实现了一份最小的四模式接口，使界面在**没有真实内核**时
    依然能完整走通模式切换、会话隔离与目录绑定（只是不产生真实动作）。
    真实运行时这些接口来自 ai.AI。
    """
    
    # 供演示的模拟回复（含内联表情包标记，表情名对应 Image/表情包 里的文件）
    _REPLIES = [
        "这是对 '{text}' 的模拟回复，我会一个字一个字地显示出来。[表情:欣赏]",
        "收到啦，[表情:害羞] 我一直在听你说呢。",
        "唔……[表情:疑问] 你是认真的吗？",
    ]

    # 演示模式不支持绑定的模式（与内核一致：Chat 模式不给工具与目录）
    _NON_BINDABLE = ("chat",)
    
    def __init__(self) -> None:
        self._turn = 0
        self._config_manager = MockConfigManager()
        self._mode = "chat"
        self._workdirs: Dict[str, str] = {}
        # 每个模式各自一份历史，演示「切换模式互不串台」
        self._history: Dict[str, List[Dict[str, Any]]] = {
            name: [] for name in ("chat", "work", "code", "learn")
        }
    
    @property
    def provider_name(self) -> str:
        """模拟提供商名称"""
        return "mock"
    
    @property
    def config_manager(self) -> Any:
        """模拟配置管理器（供设置窗口读取）"""
        return self._config_manager
    
    def supports_vision(self, provider_name: Optional[str] = None) -> bool:
        """演示用：模拟视觉模型，使图片按钮显示"""
        return True
    
    def change_provider(self, *args: Any, **kwargs: Any) -> None:
        """演示模式无需切换"""
        return None

    # ---- 四模式（最小实现，供界面离线演示） ----

    @property
    def mode(self) -> str:
        return self._mode

    def available_modes(self) -> List[Dict[str, Any]]:
        """模式元数据（字段与 ai.AI.available_modes 对齐）"""
        visible = {
            "chat": [],
            "work": ["get_current_time", "web_search"],
            "code": ["read_file", "write_file", "run_command"],
            "learn": ["search_notes", "read_note", "write_note"],
        }
        return [
            {
                "name": item["name"],
                "display_name": item["display_name"],
                "bindable": item["name"] not in self._NON_BINDABLE,
                "workdir": self._workdirs.get(item["name"]) or None,
                "sandbox": "read-only" if item["name"] == "chat" else "workspace-write",
                "visible_tools": visible.get(item["name"], []),
                "active": item["name"] == self._mode,
            }
            for item in FALLBACK_MODES
        ]

    def set_mode(self, mode: str, *, persist: bool = True) -> None:
        """切换模式（演示模式不写磁盘）"""
        name = normalize_mode_name(mode)
        self._mode = name
        self._history.setdefault(name, [])

    def bind_mode_workdir(self, mode: str, path: Optional[str] = None) -> str:
        """绑定目录：Chat 模式拒绝（与内核同一套语义）"""
        name = normalize_mode_name(mode)
        if name in self._NON_BINDABLE:
            raise ValueError("模式 " + repr(name) + " 不支持绑定工作目录")
        if path in (None, ""):
            self._workdirs.pop(name, None)
            return ""
        normalized = os.path.normpath(str(Path(str(path)).expanduser()))
        self._workdirs[name] = normalized
        return normalized

    def workdir(self, mode: Optional[str] = None) -> Optional[str]:
        name = normalize_mode_name(mode or self._mode)
        return self._workdirs.get(name)

    def get_history(self) -> List[Dict[str, Any]]:
        """当前模式的历史（各模式独立）"""
        return list(self._history.get(self._mode, []))
    
    def get_response_stream(self, text: str, images: Optional[List[str]] = None):
        import time
        image_note = f"[共收到 {len(images)} 张图片]" if images else ""
        response = (
            self._REPLIES[self._turn % len(self._REPLIES)].format(text=text)
            + image_note
        )
        self._turn += 1
        history = self._history.setdefault(self._mode, [])
        history.append({"role": "user", "content": text})
        for char in response:
            yield ('text', char)
            time.sleep(0.05)  # 模拟打字效果
        history.append({"role": "assistant", "content": response})
        yield ('done', response)


def main() -> int:
    """应用程序入口函数"""
    app = QApplication(sys.argv)
    
    app.setStyle("Fusion")
    # 应用已保存的显示模式（默认跟随系统）
    Colors.set_mode(ThemeSettings.load())
    app.setPalette(create_palette())
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