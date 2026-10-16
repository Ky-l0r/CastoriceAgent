"""桌面通知投递抽象（第二期 t4，面向第三期 Electron 前端复用）。

两层结构（05-channel-contract.md 的渠道思路在桌面侧的对应物）：

* `DesktopNotifier` —— **协议**（runtime_checkable Protocol）：

      notify(title, body, target_id="") -> DeliveryResult

  调度器只依赖该协议，**不 import 任何 Qt 类**；第三期 Electron 前端
  实现同一协议接入（Web 端 toast / 系统通知），不需要改调度器。
* `SystemTrayNotifier` —— 第一期默认实现：QSystemTrayIcon 气泡通知。
  Qt 是**延迟导入**（方法体里 import PySide6），因此 castorice/ 包
  整体仍可在无 Qt 环境下被测试与 CLI 驱动（红线 D：import 本模块
  本身不引入 PySide6 依赖）。托盘不可用时按 `fallback_log` 配置
  降级为仅记日志（fallback_log=False 时直接报失败）。

投递结果统一用 `DeliveryResult` 纯 Python dataclass 表达（不抛异常表达
「用户没看到通知」这类软失败；硬错误才抛）。

红线 D：本模块不 import ui / ai；Qt 导入只发生在 SystemTrayNotifier
运行期方法内。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Optional, Protocol, runtime_checkable

__all__ = [
    "DeliveryResult",
    "DesktopNotifier",
    "NotifierRegistry",
    "SystemTrayNotifier",
    "get_notifier_registry",
]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeliveryResult:
    """一次投递的结果（纯 Python 类型，Electron 期可直接跨进程序列化）。

    ok=True 表示投递方确认消息已提交到目标界面/平台；False 表示本次
    未送达（detail 说明原因），调用方（调度器）据此执行降级策略。
    """

    ok: bool
    target_id: str = ""
    detail: str = ""

    @classmethod
    def success(cls, target_id: str = "", detail: str = "") -> "DeliveryResult":
        return cls(ok=True, target_id=target_id, detail=detail)

    @classmethod
    def failure(cls, target_id: str = "", detail: str = "") -> "DeliveryResult":
        return cls(ok=False, target_id=target_id, detail=detail)


@runtime_checkable
class DesktopNotifier(Protocol):
    """桌面投递协议：第一期 SystemTrayNotifier，第三期 Electron 前端同型接入。

    约定：
    * notify() **同步返回** DeliveryResult（内部自行处理线程封送）；
    * target_id 为该通知对应的调度目标 id（schedules.json 的 targets[].id），
      抽象层只透传，不做语义解释；
    * 实现不得用异常表达「通知没弹出来」这类软失败——用 DeliveryResult(ok=False)；
    * close() 幂等。
    """

    def notify(self, title: str, body: str, target_id: str = "") -> DeliveryResult:
        ...

    def close(self) -> None:
        ...


class NotifierRegistry:
    """多实现注册表：按 target 前缀路由，或取默认实现兜底。

    第三期 Electron 前端接入时只需 register 一个新实现，不需要改调度器。
    resolve(target_id) 的路由规则：target_id 形如 "<channel>:<id>"（如
    "desktop:main"、"qq:12345"）时按前缀查已注册实现；无前缀或无匹配
    时返回默认实现。
    """

    def __init__(self, default: Optional[DesktopNotifier] = None) -> None:
        self._default = default
        self._notifiers: Dict[str, DesktopNotifier] = {}

    def register(self, name: str, notifier: DesktopNotifier,
                 *, default: bool = False) -> None:
        """注册一个实现；default=True 时同时设为默认（最后注册者生效）。"""
        key = str(name or "").strip()
        if not key:
            raise ValueError("notifier 注册名不能为空")
        self._notifiers[key] = notifier
        if default:
            self._default = notifier

    def set_default(self, notifier: Optional[DesktopNotifier]) -> None:
        self._default = notifier

    @property
    def default(self) -> Optional[DesktopNotifier]:
        return self._default

    def resolve(self, target_id: str = "") -> Optional[DesktopNotifier]:
        """按 target 前缀解析实现；找不到匹配返回默认（可能为 None）。"""
        text = str(target_id or "")
        prefix = text.split(":", 1)[0] if ":" in text else ""
        if prefix:
            notifier = self._notifiers.get(prefix)
            if notifier is not None:
                return notifier
        return self._default

    def notify(self, title: str, body: str, target_id: str = "") -> DeliveryResult:
        """便捷入口：解析实现并投递；无任何实现注册时返回失败结果。"""
        notifier = self.resolve(target_id)
        if notifier is None:
            return DeliveryResult.failure(
                target_id=target_id, detail="没有已注册的桌面通知实现")
        return notifier.notify(title, body, target_id=target_id)

    def close(self) -> None:
        for notifier in list(self._notifiers.values()):
            try:
                notifier.close()
            except Exception as exc:  # pragma: no cover - 关闭容错
                logger.warning("关闭通知实现 %r 失败: %s", notifier, exc)
        self._notifiers.clear()


_registry: Optional[NotifierRegistry] = None


def get_notifier_registry() -> NotifierRegistry:
    """进程级默认注册表（装配方在启动时注册默认实现）。"""
    global _registry
    if _registry is None:
        _registry = NotifierRegistry()
    return _registry


class SystemTrayNotifier:
    """第一期默认桌面实现：QSystemTrayIcon.showMessage 气泡通知。

    * Qt 在首次 notify 时**延迟导入**——import castorice.notify 本身
      不需要 PySide6（无 Qt 测试环境仍然可用）；
    * 托盘不可用（系统托盘缺失 / QApplication 未创建）时按 fallback_log
      降级：True（默认）记日志并返回 ok=True（消息已在日志留痕，不算
      投递失败），False 直接返回 ok=False（调用方走降级策略）；
    * 实现内部异常不外抛（通知是锦上添花），统一翻译成失败结果。
    """

    def __init__(self, *, fallback_log: bool = True,
                 app_icon_title: str = "Castorice") -> None:
        self._fallback_log = bool(fallback_log)
        self._app_icon_title = app_icon_title
        self._tray = None  # QSystemTrayIcon，延迟创建
        self._tray_failed = False

    # ---- 内部：Qt 懒加载 ------------------------------------------------

    def _ensure_tray(self):
        """创建/复用 QSystemTrayIcon；不可用时返回 None（并按需记日志）。"""
        if self._tray is not None:
            return self._tray
        if self._tray_failed:
            return None
        try:
            from PySide6.QtWidgets import QApplication
        except Exception as exc:
            self._tray_failed = True
            logger.warning("PySide6 不可用，桌面通知降级: %s", exc)
            return None
        app = QApplication.instance()
        if app is None:
            self._tray_failed = True
            logger.warning("QApplication 未创建，桌面通知降级为日志")
            return None
        try:
            from PySide6.QtWidgets import QSystemTrayIcon
            tray = QSystemTrayIcon(app)
            icon = app.windowIcon()
            if icon is not None and not icon.isNull():
                tray.setIcon(icon)
            if not tray.isSystemTrayAvailable():
                self._tray_failed = True
                logger.warning("系统托盘不可用，桌面通知降级为日志")
                return None
            tray.show()
            self._tray = tray
            return tray
        except Exception as exc:
            self._tray_failed = True
            logger.warning("QSystemTrayIcon 初始化失败，桌面通知降级: %s", exc)
            return None

    # ---- DesktopNotifier 协议 -------------------------------------------

    def notify(self, title: str, body: str, target_id: str = "") -> DeliveryResult:
        try:
            return self._notify_impl(title, body, target_id)
        except Exception as exc:
            logger.warning("桌面通知异常: %s", exc)
            return DeliveryResult.failure(target_id=target_id, detail=str(exc))

    def _notify_impl(self, title: str, body: str, target_id: str) -> DeliveryResult:
        tray = self._ensure_tray()
        if tray is None:
            if self._fallback_log:
                logger.info("[桌面通知降级-仅日志] target=%s title=%s body=%s",
                            target_id or "-", title, body)
                return DeliveryResult.success(target_id=target_id,
                                              detail="托盘不可用，降级为日志")
            return DeliveryResult.failure(target_id=target_id, detail="托盘不可用")
        from PySide6.QtWidgets import QSystemTrayIcon
        tray.showMessage(title, body, QSystemTrayIcon.Information, 8000)
        return DeliveryResult.success(target_id=target_id)

    def close(self) -> None:
        tray = self._tray
        self._tray = None
        if tray is not None:
            try:
                tray.hide()
            except Exception:  # pragma: no cover - Qt 已析构等竞态
                pass
