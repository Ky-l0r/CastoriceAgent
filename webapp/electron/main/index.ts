import { app, BrowserWindow, ipcMain, Tray, Menu, nativeImage } from "electron";
import path from "node:path";
import { Sidecar } from "./sidecar";
import { BridgeClient } from "./ws-client";
import { deliverNotification } from "./notification";

/**
 * Electron 主进程组合根。
 *
 * 职责：窗口管理（无边框圆角）、托盘、单实例锁、sidecar 生命周期、
 * WS 客户端与渲染进程之间的 IPC 桥。token / 端口等握手信息只在本
 * 进程流转；渲染进程只见到类型化 API（preload）。
 */

const PROJECT_ROOT = path.join(__dirname, "..", "..");
const RENDERER_DEV_URL = process.env.VITE_DEV_SERVER_URL;
const RENDERER_FILE = path.join(PROJECT_ROOT, "dist", "renderer", "index.html");
const SIDECAR_SCRIPT = path.join(PROJECT_ROOT, "electron", "sidecar", "spawn-bridge.py");
const isDev = process.env.VITE_DEV === "1" || !!process.env.VITE_DEV_SERVER_URL;
// 图标：dev 从仓库 Image/ 取；prod 由 extraResources 放在 resources/CastoriceAgent.ico
//（process.resourcesPath 在 dev 也存在，必须按 isDev 分流）。
const ICON_PATH = isDev
  ? path.join(PROJECT_ROOT, "..", "Image", "CastoriceAgent.ico")
  : path.join(process.resourcesPath, "CastoriceAgent.ico");

let mainWindow: BrowserWindow | null = null;
let tray: Tray | null = null;
let quitting = false;

/**
 * 关闭行为（t5 可配置项）：true = 关闭窗口隐藏到托盘（桌面陪伴默认），
 * false = 关闭窗口即退出。持久化在 userData/shell-prefs.json，渲染侧
 * 设置中心经 IPC 修改（window:set-close-action）。
 */
let closeToTray = true;
const SHELL_PREFS_FILE = "shell-prefs.json";

function loadShellPrefs(): void {
  try {
    const fs = require("node:fs") as typeof import("node:fs");
    const file = path.join(app.getPath("userData"), SHELL_PREFS_FILE);
    if (fs.existsSync(file)) {
      const prefs = JSON.parse(fs.readFileSync(file, "utf-8")) as { closeToTray?: boolean };
      if (typeof prefs.closeToTray === "boolean") closeToTray = prefs.closeToTray;
    }
  } catch {
    // 读取失败用默认值（关闭到托盘）。
  }
}

function saveShellPrefs(): void {
  try {
    const fs = require("node:fs") as typeof import("node:fs");
    const file = path.join(app.getPath("userData"), SHELL_PREFS_FILE);
    fs.writeFileSync(file, JSON.stringify({ closeToTray }, null, 2), "utf-8");
  } catch (err) {
    console.error("[main] save shell prefs failed:", err);
  }
}

// dev：解释器 + 仓库源码树脚本；prod：PyInstaller 产的 sidecar 可执行文件
//（resources/backend-runtime/，绿色便携不依赖用户机器 Python），
// scriptPath 传空串让 Sidecar 进入「直接 spawn 可执行文件」模式。
const sidecarScript = SIDECAR_SCRIPT;
const sidecarExe = path.join(process.resourcesPath, "backend-runtime", "castorice-sidecar.exe");

/**
 * 可写数据目录：Windows 上的 %APPDATA%/CastoriceAgent。
 *
 * 与程序目录分离是为了让「覆盖更新」不影响用户数据（详见
 * castorice/paths.py）。dev 与 prod 用同一个位置，避免「开发时数据在
 * 仓库里、发布后在 APPDATA」的割裂——也防止开发期的测试数据混进发布包。
 *
 * app.getPath("userData") 取的就是「用户数据目录/产品名」，
 * 与 productName（CastoriceAgent）一致，无需再拼子目录。
 */
const DATA_ROOT = app.getPath("userData");

const sidecar = new Sidecar(isDev ? sidecarScript : "", {
  // prod：pythonPath 直接指 PyInstaller 产的 sidecar 可执行文件（exe 模式，
  // scriptPath 传空——Sidecar 据此走「直接 spawn 可执行文件」分支）；
  // 资源根 = resources/backend（extraResources 布局）。
  pythonPath: isDev ? undefined : sidecarExe,
  castoriceRoot: isDev ? path.join(PROJECT_ROOT, "..") : path.join(process.resourcesPath, "backend"),
  dataRoot: DATA_ROOT,
  logDir: path.join(DATA_ROOT, "logs"),
});

let bridge: BridgeClient | null = null;

function broadcast(channel: string, ...args: unknown[]): void {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send(channel, ...args);
  }
}

function startBridge(port: number, token: string): void {
  // ws 包在主进程内延迟加载，保持无 WS 依赖的模块可测试。
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  const { WebSocket } = require("ws") as { WebSocket: ConstructorParameters<typeof BridgeClient>[1] };
  bridge = new BridgeClient(
    {
      url: () => "ws://127.0.0.1:" + port,
      token: () => token,
    },
    WebSocket,
  );
  bridge.on("event", (event: unknown) => {
    broadcast("bridge:event", event);
    // 通知事件（WebNotifier 主动聊天投递）：主进程系统通知 + 渲染横幅双路。
    const e = event as { kind?: string; payload?: unknown };
    if (e.kind === "notification" && e.payload && typeof e.payload === "object") {
      const p = e.payload as { title?: unknown; body?: unknown; target_id?: unknown };
      deliverNotification(mainWindow, {
        title: String(p.title ?? "CastoriceAgent"),
        body: String(p.body ?? ""),
        target_id: String(p.target_id ?? ""),
      });
    }
  });
  bridge.on("state", (state: string) => broadcast("bridge:connection-state", state));
  bridge.on("exit", () => broadcast("bridge:connection-state", "offline"));
  bridge.connect();
}

function createMainWindow(): void {
  mainWindow = new BrowserWindow({
    width: 1080,
    height: 720,
    minWidth: 860,
    minHeight: 560,
    show: false,
    frame: false, // 无边框：自绘标题栏（参考图 1 观感）
    titleBarStyle: "hidden",
    roundedCorners: true, // Win11 系统圆角
    backgroundColor: "#101014",
    icon: ICON_PATH,
    webPreferences: {
      preload: path.join(__dirname, "..", "preload", "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false, // preload 需要完整 require 能力（contextBridge）
    },
  });

  if (RENDERER_DEV_URL) {
    void mainWindow.loadURL(RENDERER_DEV_URL);
  } else {
    void mainWindow.loadFile(RENDERER_FILE);
  }

  mainWindow.once("ready-to-show", () => mainWindow?.show());

  // 关闭行为可配置（t5）：close_to_tray=true 隐藏到托盘（默认）；
  // false 直接放行关闭（配合 before-quit 收尾）。托盘退出始终真正退出。
  mainWindow.on("close", (event) => {
    if (!quitting && closeToTray) {
      event.preventDefault();
      mainWindow?.hide();
    }
  });

  // 最大化状态广播：渲染层的图标切换与「最大化时不加圆角」布局都依赖它。
  // 双击标题栏 / 系统快捷键（Win+↑）也要能同步，所以监听窗口事件而非只靠按钮。
  const pushWindowState = () => {
    broadcast("window:maximized-changed", mainWindow?.isMaximized() ?? false);
  };
  mainWindow.on("maximize", pushWindowState);
  mainWindow.on("unmaximize", pushWindowState);
  mainWindow.on("enter-full-screen", pushWindowState);
  mainWindow.on("leave-full-screen", pushWindowState);

  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

function createTray(): void {
  const image = nativeImage.createFromPath(ICON_PATH);
  tray = new Tray(image.isEmpty() ? nativeImage.createEmpty() : image);
  tray.setToolTip("CastoriceAgent");
  // 托盘菜单（t5）：显示主窗 / 关闭行为勾选（即时生效并持久化）/ 退出。
  tray.setContextMenu(
    Menu.buildFromTemplate([
      {
        label: "显示主窗口",
        click: () => {
          mainWindow?.show();
          mainWindow?.focus();
        },
      },
      {
        label: "关闭时最小化到托盘",
        type: "checkbox",
        checked: closeToTray,
        click: (item) => {
          closeToTray = item.checked;
          saveShellPrefs();
        },
      },
      { type: "separator" },
      {
        label: "退出",
        click: () => {
          quitting = true;
          app.quit();
        },
      },
    ]),
  );
  tray.on("double-click", () => {
    mainWindow?.show();
    mainWindow?.focus();
  });
}

// ---- IPC 面（preload 直通包装的目标） ----

function registerIpc(): void {
  ipcMain.handle("bridge:invoke", (_event, method: string, params?: Record<string, unknown>) => {
    if (!bridge) throw new Error("bridge not started");
    return bridge.invoke(method, params);
  });

  ipcMain.handle("bridge:chat", (_event, text: string, images?: string[]) => {
    if (!bridge || bridge.connectionState !== "ready") {
      return { ok: false, error: "bridge not connected" };
    }
    bridge.chat(text, images);
    return { ok: true };
  });

  ipcMain.on("bridge:interrupt", () => bridge?.interrupt());

  // 窗口控制（自绘标题栏直通；close 行为见 closeToTray 配置）。
  ipcMain.on("window:minimize", () => mainWindow?.minimize());
  ipcMain.on("window:hide", () => mainWindow?.hide());
  // 最大化/还原：无边框窗口必须自绘按钮（系统标题栏不存在）。
  ipcMain.on("window:toggle-maximize", () => {
    if (!mainWindow) return;
    if (mainWindow.isMaximized()) mainWindow.unmaximize();
    else mainWindow.maximize();
  });
  ipcMain.handle("window:is-maximized", () => mainWindow?.isMaximized() ?? false);

  // 目录选择（工作目录绑定用）：renderer 没有 fs 能力，必须走主进程。
  ipcMain.handle("dialog:pick-directory", async () => {
    if (!mainWindow) return null;
    const { dialog } = require("electron") as typeof import("electron");
    const result = await dialog.showOpenDialog(mainWindow, {
      title: "选择工作目录",
      properties: ["openDirectory", "createDirectory"],
    });
    if (result.canceled || result.filePaths.length === 0) return null;
    return result.filePaths[0];
  });

  // 关闭行为读写（t5：设置中心「关闭时最小化到托盘」开关的后端）。
  ipcMain.handle("window:get-close-action", () => closeToTray);
  ipcMain.handle("window:set-close-action", (_event, value: boolean) => {
    closeToTray = Boolean(value);
    saveShellPrefs();
    return closeToTray;
  });
}

// ---- 单实例锁 ----

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    mainWindow?.show();
    mainWindow?.focus();
  });

  app.whenReady().then(async () => {
    loadShellPrefs();
    registerIpc();
    createTray();
    createMainWindow();

    // sidecar 失败不白屏：窗口先打开，桥状态走 reconnecting/offline 提示。
    try {
      const ready = await sidecar.start();
      startBridge(ready.port, ready.token);
    } catch (err) {
      console.error("[main] sidecar start failed:", err);
    }

    // sidecar 意外退出：等退避后由用户下次交互或直接重启拉回。
    sidecar.on("exit", ({ wasReady }: { wasReady: boolean }) => {
      if (wasReady && !quitting) {
        bridge?.close();
        bridge = null;
        // 简单兜底：5s 后重启 sidecar（正式重试策略归 ui-pages 阶段细化）。
        setTimeout(() => {
          if (quitting) return;
          sidecar
            .start()
            .then((r) => startBridge(r.port, r.token))
            .catch((err) => console.error("[main] sidecar restart failed:", err));
        }, 5000);
      }
    });
  });

  // 退出收尾。三层保证 sidecar 不会变孤儿：
  //   1. before-quit —— 正常退出的第一站；
  //   2. will-quit —— 若 first 因异常未跑完，这里再收一次（stop 幂等）；
  //   3. exit —— 最后一道同步兜底。
  // 另有 sidecar 自身的 stdin-EOF / PPID 轮询，覆盖「主进程被强杀、
  // 这些钩子根本不执行」的情况。
  app.on("before-quit", () => {
    quitting = true;
    bridge?.close();
    sidecar.stop();
  });

  app.on("will-quit", () => {
    quitting = true;
    sidecar.stop();
  });

  process.on("exit", () => {
    // 同步阶段只能尽力而为：stop() 内部已是「发信号 + 定时强杀」，
    // 这里再调一次确保至少发出了终止信号。
    try {
      sidecar.stop();
    } catch {
      /* 退出阶段不抛 */
    }
  });

  app.on("window-all-closed", () => {
    // 托盘常驻：不随窗口关闭退出（与 close->hide 呼应）。
  });
}
