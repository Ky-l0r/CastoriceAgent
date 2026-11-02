import type { BridgeEvent } from "./shared/bridge-protocol";

/** 渲染进程可见的类型化 API（contextBridge 暴露形态的唯一事实源）。 */
export interface CastoriceBridgeApi {
  /** JSON-RPC 调用（config.get / mode.list / memory.search / ...）。 */
  invoke(method: string, params?: Record<string, unknown>): Promise<unknown>;
  /** 发起一轮对话。busy 期间调用返回 { ok: false, error: "busy" }。 */
  chat(text: string, images?: string[]): Promise<{ ok: boolean; error?: string }>;
  /** 打断当前轮次（尽力语义）。 */
  interrupt(): void;
  /** 订阅桥接事件帧；返回退订函数。 */
  onEvent(listener: (event: BridgeEvent) => void): () => void;
  /** 订阅连接状态；返回退订函数。 */
  onConnectionState(
    listener: (state: "connecting" | "ready" | "reconnecting" | "offline") => void,
  ): () => void;
  /** 窗口控制：最小化 / 隐藏到托盘 / 最大化切换（自绘标题栏用）。 */
  minimize(): void;
  hide(): void;
  /** 关闭窗口（标题栏「×」）：按 closeToTray 设置隐藏到托盘或退出程序。 */
  requestClose(): void;
  toggleMaximize(): void;
  /** 目录选择对话框（工作目录绑定）；取消返回 null。 */
  pickDirectory(): Promise<string | null>;
  isMaximized(): Promise<boolean>;
  /** 订阅最大化状态变化（按钮 / 双击标题栏 / 系统快捷键）；返回退订函数。 */
  onMaximizedChanged(listener: (maximized: boolean) => void): () => void;
  /** 订阅主窗口内通知横幅（WebNotifier 主动聊天投递）；返回退订函数。 */
  onNotification(
    listener: (payload: { title: string; body: string; target_id: string }) => void,
  ): () => void;
  /** 关闭行为：true = 关闭时最小化到托盘（默认）；false = 直接退出。 */
  getCloseAction(): Promise<boolean>;
  setCloseAction(value: boolean): Promise<boolean>;

  // ---- 壳层设置（设置中心「通用」页）----
  getShellState(): Promise<ShellState>;
  setAutostart(enabled: boolean): Promise<AutostartState & { shell?: ShellState }>;
  setSilentStart(enabled: boolean): Promise<ShellState>;
  openDataDir(): Promise<{ ok: boolean; error?: string }>;
  restartBackend(): Promise<{ ok: boolean; error?: string }>;
  onShellStateChanged(listener: (state: ShellState) => void): () => void;
  onBackendError(listener: (info: { message: string } | null) => void): () => void;
}

/** 开机自启动的登记状态。 */
export interface AutostartState {
  enabled: boolean;
  supported: boolean;
  error?: string;
}

/** 壳层完整状态（主进程是唯一事实源）。 */
export interface ShellState {
  closeToTray: boolean;
  /** 开机自启时不弹主窗口（后台静默运行）。 */
  silentStart: boolean;
  autostart: AutostartState;
  /** 本次启动是否由自启动触发。 */
  autostartLaunch: boolean;
  /** 本次启动是否静默（没有弹出主窗口）。 */
  silentLaunch: boolean;
  /** 可写数据目录（配置 / 记忆 / 会话）。 */
  dataRoot: string;
  version: string;
  packaged: boolean;
  platform: string;
}

/** approval.detail RPC 返回的请求详情（前端审批卡渲染数据）。 */
export interface ApprovalRequestDetail {
  request_id: string;
  tool: string;
  mode: string;
  reason: string;
  target_paths: string[];
  /** 参数值已 JSON 序列化截断（仅展示用）。 */
  arguments: Record<string, string>;
}
