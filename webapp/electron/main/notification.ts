import { Notification, BrowserWindow } from "electron";

/**
 * 桌面通知路由（t5）：DesktopNotifier 的 Electron 壳侧出口。
 *
 * 链路：调度器 -> NotifierRegistry（notify.register 注册的 WebNotifier）
 *   -> 桥接 send_event("notification") -> 主进程 WS 客户端 event 帧
 *   -> 本模块分两路投递：
 *     1. Electron 系统通知（Notification API，主进程）；
 *     2. 渲染进程主窗口横幅（bridge:notification IPC -> zustand banners）。
 *
 * 投递纪律对齐 castorice/notify.py：系统通知不可用是软失败（记日志
 * 不抛异常）——横幅仍在窗口内兜底；窗口可见时跳过系统通知（避免
 * 双重打扰，窗口内横幅足够），隐藏/最小化时才走系统通知。
 */

/** 通知载荷（对齐 WebNotifier.notify 的 send_event 载荷）。 */
export interface NotificationPayload {
  title: string;
  body: string;
  target_id: string;
}

let osNotificationsEnabled = true;

/** 关闭行为等壳配置由渲染侧/配置文件驱动；系统通知开关同型（预留）。 */
export function setOsNotificationsEnabled(enabled: boolean): void {
  osNotificationsEnabled = enabled;
}

/**
 * 投递一条通知：窗口可见时只发横幅（渲染进程）；否则系统通知 + 横幅
 * 双投（窗口不可见时横幅进队列，用户回到窗口仍能看到错过的通知）。
 */
export function deliverNotification(
  mainWindow: BrowserWindow | null,
  payload: NotificationPayload,
): void {
  const title = String(payload.title || "CastoriceAgent");
  const body = String(payload.body || "");

  // 横幅：始终广播（渲染进程自己决定展示策略）。
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send("bridge:notification", { title, body, target_id: payload.target_id });
  }

  // 系统通知：窗口最小化/隐藏/失焦到后台时才弹（陪伴应用的常规克制策略）。
  const windowVisible = !!mainWindow && !mainWindow.isDestroyed() && mainWindow.isVisible() && mainWindow.isFocused();
  if (windowVisible || !osNotificationsEnabled) return;

  try {
    if (!Notification.isSupported()) return; // 软失败：无系统通知能力
    const notification = new Notification({
      title,
      body,
      icon: undefined, // 用应用默认图标（打包后有 icon；dev 下系统兜底）
      silent: false,
    });
    // 点击通知：唤起主窗口（像托盘单击一样的召回路径）。
    notification.on("click", () => {
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.show();
        mainWindow.focus();
      }
    });
    notification.show();
  } catch (err) {
    // 通知是锦上添花：任何系统层失败都只记日志（对齐 DesktopNotifier 软失败纪律）。
    console.error("[notify] os notification failed:", err);
  }
}
