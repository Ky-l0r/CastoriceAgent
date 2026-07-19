import sys
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, 
    QHBoxLayout, QTextEdit, QScrollArea, QFrame, QLabel,
    QSizePolicy
)
from PySide6.QtCore import Qt, QTimer, QThread, Signal
from PySide6.QtGui import QFont

class ChatBubble(QFrame):
    """聊天气泡组件"""
    def __init__(self, text, is_user=False, parent=None):
        super().__init__(parent)
        self.is_user = is_user
        self.text = text
        self.setup_ui()
        
    def setup_ui(self):
        """设置气泡样式"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 10, 15, 10)
        
        # 创建文本标签
        label = QLabel(self.text)
        label.setWordWrap(True)
        # 允许文本被鼠标选择
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        
        # 设置字体
        font = QFont("Microsoft YaHei", 10)
        label.setFont(font)
        
        # 设置颜色
        if self.is_user:
            # 用户消息 - 绿色背景（仿微信）
            self.setStyleSheet("""
                QFrame {
                    background-color: #95EC69;
                    border-radius: 10px;
                    border: 1px solid #7AD44A;
                }
                QLabel {
                    color: #000000;
                }
            """)
        else:
            # AI消息 - 白色背景
            self.setStyleSheet("""
                QFrame {
                    background-color: #FFFFFF;
                    border-radius: 10px;
                    border: 1px solid #E0E0E0;
                }
                QLabel {
                    color: #000000;
                }
            """)
        
        layout.addWidget(label)
        
        # 设置固定宽度策略，但允许高度自适应
        self.setMaximumWidth(400)
        # 设置大小策略
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, 
            QSizePolicy.Policy.Minimum
        )

class ChatWindow(QMainWindow):
    """聊天主窗口"""
    def __init__(self, bot):
        super().__init__()
        self.bot = bot
        self.worker = None  # 初始化 worker
        self.init_ui()
        self.add_message("你好！我是遐蝶，有什么可以帮你的吗？", is_user=False)
        
    def init_ui(self):
        """初始化UI界面"""
        self.setWindowTitle("遐蝶 - AI聊天助手")
        self.setGeometry(100, 100, 800, 600)
        
        # 设置窗口样式
        self.setStyleSheet("""
            QMainWindow {
                background-color: #EDEDED;
            }
            QTextEdit {
                border: 2px solid #CCCCCC;
                border-radius: 8px;
                padding: 8px;
                font-size: 12pt;
                background-color: white;
            }
            QTextEdit:focus {
                border: 2px solid #4CAF50;
            }
            QScrollArea {
                border: none;
                background-color: transparent;
            }
        """)
        
        # 创建中心窗口部件
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)
        
        # 创建消息显示区域
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        
        # 消息容器
        self.message_container = QWidget()
        self.message_layout = QVBoxLayout(self.message_container)
        self.message_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.message_layout.setSpacing(10)
        self.message_layout.setContentsMargins(20, 20, 20, 20)
        
        self.scroll_area.setWidget(self.message_container)
        main_layout.addWidget(self.scroll_area, 1)
        
        # 创建输入区域
        input_widget = QWidget()
        input_layout = QVBoxLayout(input_widget)
        input_layout.setContentsMargins(0, 0, 0, 0)
        
        self.input_text = QTextEdit()
        self.input_text.setPlaceholderText("输入消息... (Shift+Enter换行，Enter发送)")
        self.input_text.setMaximumHeight(100)
        self.input_text.setMinimumHeight(50)
        
        # 绑定键盘事件
        self.input_text.installEventFilter(self)
        
        input_layout.addWidget(self.input_text)
        main_layout.addWidget(input_widget, 0)
        
        # 处理窗口大小变化
        self.resize_timer = QTimer()
        self.resize_timer.setSingleShot(True)
        self.resize_timer.timeout.connect(self.adjust_bubble_widths)
        
    def eventFilter(self, obj, event):
        """事件过滤器 - 处理键盘事件"""
        if obj == self.input_text and event.type() == event.Type.KeyPress:
            if event.key() == Qt.Key.Key_Return:
                if event.modifiers() == Qt.KeyboardModifier.ShiftModifier:
                    # Shift+Enter: 换行
                    return False
                else:
                    # Enter: 发送消息
                    self.send_message()
                    return True
        return super().eventFilter(obj, event)
    
    def send_message(self):
        """发送消息"""
        user_input = self.input_text.toPlainText().strip()
        if not user_input:
            return
        
        # 清空输入框
        self.input_text.clear()
        
        # 显示用户消息
        self.add_message(user_input, is_user=True)
        
        # 禁用输入
        self.input_text.setEnabled(False)
        
        # 创建后台线程处理AI响应
        self.worker = AIWorker(self.bot, user_input)
        self.worker.finished.connect(self.on_ai_response)
        self.worker.error.connect(self.on_ai_error)
        self.worker.start()
    
    def add_message(self, text, is_user=False):
        """添加消息到聊天界面"""
        bubble = ChatBubble(text, is_user)
        
        # 创建水平布局来对齐气泡
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 5, 0, 5)
        layout.setSpacing(0)
        
        if is_user:
            # 用户消息靠右
            layout.addStretch()
            layout.addWidget(bubble)
        else:
            # AI消息靠左
            layout.addWidget(bubble)
            layout.addStretch()
        
        # 将消息添加到容器中
        self.message_layout.addWidget(container)
        
        # 滚动到底部
        QTimer.singleShot(50, self.scroll_to_bottom)
        
        # 调整气泡宽度
        QTimer.singleShot(100, self.adjust_bubble_widths)
    
    def scroll_to_bottom(self):
        """滚动到底部"""
        scrollbar = self.scroll_area.verticalScrollBar()
        if scrollbar:
            scrollbar.setValue(scrollbar.maximum())
    
    def adjust_bubble_widths(self):
        """调整气泡宽度以适应窗口变化"""
        width = self.scroll_area.width() - 40
        max_width = min(400, int(width * 0.6))
        
        # 遍历所有消息容器
        for i in range(self.message_layout.count()):
            item = self.message_layout.itemAt(i)
            if item is not None:  # 确保 item 不为 None
                container = item.widget()
                if container is not None:  # 确保 container 不为 None
                    # 查找容器中的 ChatBubble
                    for child in container.children():
                        if isinstance(child, ChatBubble):
                            child.setMaximumWidth(max_width)
    
    def resizeEvent(self, event):
        """窗口大小改变事件"""
        super().resizeEvent(event)
        self.resize_timer.start(200)
    
    def on_ai_response(self, reply):
        """处理AI响应"""
        self.add_message(reply, is_user=False)
        self.input_text.setEnabled(True)
        self.input_text.setFocus()
        self.worker = None  # 清理worker
    
    def on_ai_error(self, error_msg):
        """处理AI错误"""
        self.add_message(f"错误：{error_msg}", is_user=False)
        self.input_text.setEnabled(True)
        self.input_text.setFocus()
        self.worker = None  # 清理worker

class AIWorker(QThread):
    """后台线程处理AI请求"""
    finished = Signal(str)
    error = Signal(str)
    
    def __init__(self, bot, user_input):
        super().__init__()
        self.bot = bot
        self.user_input = user_input
    
    def run(self):
        """运行AI请求"""
        try:
            reply = self.bot.get_response(self.user_input)
            self.finished.emit(reply)
        except Exception as e:
            self.error.emit(str(e))

# 如果直接运行此文件，可以进行测试
if __name__ == "__main__":
    app = QApplication(sys.argv)
    font = QFont("Microsoft YaHei", 9)
    app.setFont(font)
    
    # 创建一个模拟的bot用于测试
    class MockBot:
        def get_response(self, text):
            return f"这是对 '{text}' 的模拟回复"
    
    window = ChatWindow(MockBot())
    window.show()
    sys.exit(app.exec())