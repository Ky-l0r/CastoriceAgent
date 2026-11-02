import { app, BrowserWindow, ipcMain, Tray, Menu, nativeImage } from "electron";
import fs from "node:fs";
import path from "node:path";
import { Sidecar } from "./sidecar";
import { BridgeClient } from "./ws-client";
import { deliverNotification } from "./notification";
import {
  getAutostart,
  isAutostartLaunch,
  isSilentLaunch,
  setAutostart,
} from "./autostart";

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
 * 本次启动是否静默（不弹主窗口）。
 *
 * 判定：命令行带 --castorice-silent（自启动登记项就带这个参数），
 * 或者带了 --castorice-autostart 且用户在设置里开了「静默启动」。
 * 源码运行时也支持直接加 --castorice-silent 手动验证。
 */
let startSilent = isSilentLaunch();

/**
 * 托盘 / 通知 / 二次启动的统一召回入口。
 *
 * 窗口可能根本不存在：用户关掉了「关闭时最小化到托盘」后，关闭窗口
 * 会真的销毁它（进程靠托盘常驻）。此时再双击图标必须**重建**窗口
 * ——否则用户看到的是「程序打不开、任务管理器里却有一个进程」，
 * 也就是「反复打开再关闭几次之后就再也起不来」的那种现象。
 */
function showMainWindow(): void {
  if (!mainWindow || mainWindow.isDestroyed()) {
    // 静默启动的窗口没 show 过；重建时按显式召回处理，直接显示。
    startSilent = false;
    createMainWindow();
    return;
  }
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.show();
  mainWindow.focus();
}

/**
 * 壳层偏好（userData/shell-prefs.json）：
 *
 *   closeToTray  关闭窗口时隐藏到托盘（否则直接退出）
 *   silentStart  开机自启时不弹主窗口，直接驻留托盘后台运行
 *
 * 渲染侧的「设置 · 通用」页经 IPC 读写（app:get-shell-state /
 * app:set-silent-start / app:set-autostart）；托盘菜单里的
 * 「关闭时最小化到托盘」「开机自启动」也写这一份。
 */
interface ShellPrefs {
  closeToTray: boolean;
  silentStart: boolean;
}

const SHELL_PREFS_FILE = "shell-prefs.json";
const shellPrefs: ShellPrefs = { closeToTray: true, silentStart: true };

function shellPrefsPath(): string {
  return path.join(app.getPath("userData"), SHELL_PREFS_FILE);
}

function loadShellPrefs(): void {
  try {
    if (fs.existsSync(shellPrefsPath())) {
      const prefs = JSON.parse(fs.readFileSync(shellPrefsPath(), "utf-8")) as
        Partial<ShellPrefs> & { closeToTray?: unknown };
      if (typeof prefs.closeToTray === "boolean") shellPrefs.closeToTray = prefs.closeToTray;
      if (typeof prefs.silentStart === "boolean") shellPrefs.silentStart = prefs.silentStart;
    }
  } catch {
    // 读取失败用默认值（关闭到托盘、自启静默）。
  }
}

function saveShellPrefs(): void {
  try {
    fs.writeFileSync(shellPrefsPath(), JSON.stringify(shellPrefs, null, 2), "utf-8");
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

/** 启动横幅去重标志（静默启动只提示一次）。 */
let startupSilentHintShown = false;
/** 后端启动失败次数（用于退避重试与界面提示）。 */
let backendFailures = 0;
let backendRetryTimer: ReturnType<typeof setTimeout> | null = null;
/**
 * 后端正在启动的互斥标志。
 *
 * 为什么需要：失败重试定时器与 sidecar exit 事件可能同时触发启动，
 * 两次 spawn 会留下两个后端进程（都去抢 NapCat 的 WS 连接）。
 */
let backendStarting = false;
/**
 * 「预期内的退出」计数。
 *
 * sidecar.stop() 同样会触发 exit 事件（wasReady=true），若不屏蔽，
 * 退出处理会自己再拉一个后端——重启时就会留下两个后端进程
 * （两个都在连 NapCat，消息重复回复）。stop() 之前先 +1，
 * exit 处理里先抵消再决定要不要重启。
 */
let expectedStops = 0;

/**
 * 主动停止后端（计数后 stop，避免 exit 处理误重启）。
 *
 * 只有在真的有子进程时才计数：进程早就退出了还 +1，会让下一次
 * **真正的崩溃**被误判成「我们自己停的」，从此不再自动重启。
 */
function stopSidecar(): void {
  if (!sidecar.isRunning) return;
  expectedStops += 1;
  sidecar.stop();
}

/**
 * 拉起后端并把失败原因同步给界面。
 *
 * 为什么要有重试节奏：sidecar 启动失败的原因常常是**瞬时**的
 * （配置文件被外部编辑器占用、上一次进程还没完全退出、端口刚释放），
 * 之前只有「sidecar 成功启动后又崩了」才重试——首启失败就永久停在
 * 「后端未连接」，用户只能重启程序。这里对首启失败同样退避重试。
 */
async function bootSidecar(attempt = 0): Promise<void> {
  if (backendStarting) return;
  backendStarting = true;
  try {
    const ready = await sidecar.start();
    backendFailures = 0;
    broadcast("bridge:backend-error", null);
    startBridge(ready.port, ready.token);
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    console.error("[main] sidecar start failed (attempt " + attempt + "):", err);
    broadcast("bridge:backend-error", { message });
    if (quitting) return;
    backendFailures += 1;
    // 1s / 2s / 4s / 8s / 10s 封顶；界面同时显示具体原因。
    const delay = Math.min(1000 * 2 ** (backendFailures - 1), 10000);
    if (backendRetryTimer) clearTimeout(backendRetryTimer);
    backendRetryTimer = setTimeout(() => {
      backendRetryTimer = null;
      if (!quitting) void bootSidecar(attempt + 1);
    }, delay);
  } finally {
    backendStarting = false;
  }
}

/** sidecar 生命周期事件（只注册一次）。 */
function registerStartupEvents(): void {
  sidecar.on("exit", ({ wasReady }: { wasReady: boolean }) => {
    if (expectedStops > 0) {
      expectedStops -= 1;
      return;                       // 我们自己停的：不重启
    }
    if (quitting) return;
    bridge?.close();
    bridge = null;
    broadcast("bridge:connection-state", "offline");
    if (wasReady) {
      // 起来了又崩：退避后重启（这里沿用引擎里的 retry 节奏）。
      void bootSidecar(1);
    }
  });
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
    // 与深色主题的 --c-window-bg 保持一致：窗口在渲染进程首帧之前显示的是
    // 这个底色，原来用 #101014 会先闪一下灰黑再变紫黑，观感上就是「闪一下」。
    backgroundColor: "#14121c",
    // 首帧绘制完成前先隐藏窗口（见下方 ready-to-show），避免看到白底。
    paintWhenInitiallyHidden: true,
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

  // 开机自启 + 开了「静默启动」时不弹窗：窗口照常创建并加载，
  // 只是不 show()——后端（sidecar）与主动聊天调度器照常在后台跑。
  // 用户点托盘图标 / 双击图标即可召回（见 showMainWindow）。
  mainWindow.once("ready-to-show", () => {
    if (startSilent) {
      console.info("[main] 静默启动：不显示主窗口，驻留托盘后台运行");
      return;
    }
    mainWindow?.show();
  });

  // 关闭行为可配置：closeToTray=true 隐藏到托盘（默认）；
  // false 直接放行关闭（配合 before-quit 收尾）。托盘退出始终真正退出。
  mainWindow.on("close", (event) => {
    if (!quitting && shellPrefs.closeToTray) {
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

function rebuildTrayMenu(): void {
  if (!tray) return;
  tray.setContextMenu(
    Menu.buildFromTemplate([
      { label: "显示主窗口", click: () => showMainWindow() },
      {
        label: "关闭时最小化到托盘",
        type: "checkbox",
        checked: shellPrefs.closeToTray,
        click: (item) => {
          shellPrefs.closeToTray = item.checked;
          saveShellPrefs();
          broadcast("app:shell-prefs-changed", getShellState());
        },
      },
      {
        label: "开机自启动",
        type: "checkbox",
        checked: getAutostart().enabled,
        click: (item) => {
          setAutostart(item.checked, shellPrefs.silentStart);
          rebuildTrayMenu();
          broadcast("app:shell-prefs-changed", getShellState());
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
}

function createTray(): void {
  const image = nativeImage.createFromPath(ICON_PATH);
  tray = new Tray(image.isEmpty() ? nativeImage.createEmpty() : image);
  tray.setToolTip("CastoriceAgent");
  rebuildTrayMenu();
  // 单击/双击都召回窗口：静默启动（开机自启不弹窗）就是这个入口回去的。
  tray.on("click", () => showMainWindow());
  tray.on("double-click", () => showMainWindow());
}

/** 壳层状态的统一视图（渲染侧「设置 · 通用」页一次拿全）。 */
function getShellState() {
  return {
    closeToTray: shellPrefs.closeToTray,
    silentStart: shellPrefs.silentStart,
    autostart: getAutostart(),
    autostartLaunch: isAutostartLaunch(),
    silentLaunch: startSilent,
    dataRoot: DATA_ROOT,
    version: app.getVersion(),
    packaged: app.isPackaged,
    platform: process.platform,
  };
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

  /**
   * 标题栏「×」：走真正的关闭语义，由 closeToTray 决定隐藏还是退出。
   *
   * 历史实现直接调 window:hide，绕过了 closeToTray —— 用户取消勾选
   * 「关闭时最小化到托盘」后点 ×，窗口仍然只是藏起来（用户反馈的问题）。
   * 现在统一交给 win.close()：close 事件里按配置分流，两条路径都与
   * 系统 Alt+F4 / 任务栏关闭完全一致。
   */
  ipcMain.on("window:request-close", () => {
    if (!mainWindow || mainWindow.isDestroyed()) {
      if (!shellPrefs.closeToTray) {
        quitting = true;
        app.quit();
      }
      return;
    }
    mainWindow.close();
  });
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

  // 关闭行为读写（设置中心「关闭时最小化到托盘」开关的后端）。
  ipcMain.handle("window:get-close-action", () => shellPrefs.closeToTray);
  ipcMain.handle("window:set-close-action", (_event, value: boolean) => {
    shellPrefs.closeToTray = Boolean(value);
    saveShellPrefs();
    rebuildTrayMenu();
    return shellPrefs.closeToTray;
  });

  // ---- 壳层设置（自启动 / 静默启动 / 数据目录）----
  ipcMain.handle("app:get-shell-state", () => getShellState());

  ipcMain.handle(
    "app:set-autostart",
    (_event, enabled: boolean) => {
      const before = getAutostart().enabled;
      const state = setAutostart(Boolean(enabled), shellPrefs.silentStart);
      console.info(
        "[main] 开机自启动: %s -> %s (supported=%s%s)",
        before, state.enabled, state.supported,
        state.error ? ", error=" + state.error : "",
      );
      rebuildTrayMenu();
      broadcast("app:shell-prefs-changed", getShellState());
      return { ...state, shell: getShellState() };
    },
  );

  ipcMain.handle(
    "app:set-silent-start",
    (_event, enabled: boolean) => {
      shellPrefs.silentStart = Boolean(enabled);
      saveShellPrefs();
      // 已经登记了自启动的用户：立即重写启动项，否则这一项要等
      // 下次「关掉再打开自启动」才生效，与界面提示不一致。
      if (getAutostart().enabled) {
        setAutostart(true, shellPrefs.silentStart);
      }
      rebuildTrayMenu();
      broadcast("app:shell-prefs-changed", getShellState());
      return getShellState();
    },
  );

  // 打开数据目录（配置/记忆/会话都在这儿；排障时让用户能直接看到文件）
  ipcMain.handle("app:open-data-dir", async () => {
    const { shell } = require("electron") as typeof import("electron");
    try {
      fs.mkdirSync(DATA_ROOT, { recursive: true });
    } catch {
      /* 目录已存在或无权限：交给 shell.openPath 报错 */
    }
    const error = await shell.openPath(DATA_ROOT);
    return { ok: !error, error: error || undefined };
  });

  // 立即重启后端：渠道配置写回后要重启 sidecar 才生效，
  // 让用户在界面上一步完成，而不是「请手动关闭程序再开」。
  ipcMain.handle("app:restart-backend", async () => {
    if (backendStarting) {
      return { ok: false, error: "后端正在启动中，请稍后" };
    }
    try {
      if (backendRetryTimer) {
        clearTimeout(backendRetryTimer);
        backendRetryTimer = null;
      }
      bridge?.close();
      bridge = null;
      broadcast("bridge:connection-state", "connecting");
      stopSidecar();
      // 等旧进程真的退出（stop 内部 3s 强杀兜底），避免端口/配置文件锁冲突
      await new Promise((resolve) => setTimeout(resolve, 900));
      backendFailures = 0;
      await bootSidecar(0);
      return { ok: true };
    } catch (err) {
      console.error("[main] restart backend failed:", err);
      return { ok: false, error: String(err) };
    }
  });
}

// ---- 单实例锁 ----

if (!app.requestSingleInstanceLock()) {
  // 第二次启动（例如用户重复双击图标、或开机自启后用户又手动点了一次）：
  // 不新建窗口，把已存在的那个召回到前台即可。
  app.quit();
} else {
  app.on("second-instance", () => {
    showMainWindow();
  });

  app.whenReady().then(async () => {
    loadShellPrefs();

    // 静默启动的两条路径：命令行显式带 --castorice-silent（自启动项本身
    // 就带），或者本次是自启动且用户勾了「后台静默启动」。
    startSilent = isSilentLaunch() ||
      (isAutostartLaunch() && shellPrefs.silentStart);
    if (startSilent && !startupSilentHintShown) {
      startupSilentHintShown = true;
      console.info("[main] 本次为后台静默启动（不显示主窗口）");
    }

    registerIpc();
    createTray();
    createMainWindow();
    registerStartupEvents();

    // sidecar 失败不白屏：窗口先打开，桥状态走 reconnecting/offline。
    // 失败原因同时推到渲染进程——用户看到的是「配置文件格式错误」这类
    // 具体原因，而不是永远一句「后端未连接」。
    void bootSidecar(0);
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
    // 「关闭时最小化到托盘」= 窗口只是隐藏，不会走到这里；
    // 用户关掉了那一项时，关闭窗口就该真的退出——否则进程会变成
    // 用户看不见也关不掉的僵尸（托盘图标还在，但他以为已经退出了）。
    if (!shellPrefs.closeToTray) {
      quitting = true;
      app.quit();
    }
  });
}
