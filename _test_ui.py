"""临时验证脚本：验证自适应输入框、工具气泡移除、标题栏/布局渲染（offscreen）"""
import os
import sys

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QPoint
from ui import ChatWindow, MockBot

app = QApplication([])
w = ChatWindow(MockBot())
w.show()
app.processEvents()

print("window size:", w.width(), "x", w.height())
print("acrylic enabled:", w._acrylic_enabled)
print("input size (empty):", w._input_text.width(), "x", w._input_text.height())

w._input_text.setPlainText("1")
app.processEvents()
print("input size ('1'):", w._input_text.width())

w._input_text.setPlainText("一二三四五六七八九十")
app.processEvents()
print("input size (10 CJK):", w._input_text.width())

w._input_text.setPlainText("a" * 60)
app.processEvents()
print("input size (60 a):", w._input_text.width())

w._input_text.setPlainText("a" * 200)
app.processEvents()
print("input size (200 a):", w._input_text.width())

w._input_text.setPlainText("")
app.processEvents()
print("input size (cleared):", w._input_text.width())

# 工具气泡：start 显示，done 移除（不再显示"完成"）
w._on_tool_event("web_search", "start")
app.processEvents()
print("labels after start:", list(w._tool_labels.keys()), "| msg count:", w._message_layout.count())
w._on_tool_event("web_search", "done")
app.processEvents()
print("labels after done:", list(w._tool_labels.keys()), "| msg count:", w._message_layout.count())

# 截图
w.grab().save(os.path.join(os.path.dirname(os.path.abspath(__file__)), "_test_empty.png"))
w._input_text.setPlainText("你好")
app.processEvents()
w.grab().save(os.path.join(os.path.dirname(os.path.abspath(__file__)), "_test_input.png"))

# 采样像素
img = w.grab().toImage()
def px(x, y):
    c = img.pixelColor(QPoint(x, y))
    return (c.red(), c.green(), c.blue(), c.alpha())
print("corner (2,2):", px(2, 2), "(应为透明/背景)")
print("title bar (20,18):", px(20, 18), "(应为紫色)")
print("title bar (400,18):", px(400, 18), "(应为紫色)")
print("content area (400,300):", px(400, 300), "(应为暗色背景)")
print("input box interior (30,560):", px(30, 560), "(应为半透明深色输入框)")
print("done")
