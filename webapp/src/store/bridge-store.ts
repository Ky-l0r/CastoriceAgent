import { create } from "zustand";
import type { BridgeEvent } from "../../electron/shared/bridge-protocol";

/**
 * 桥接状态 store：事件分发与连接状态的单一家园。
 *
 * 设计要点（对齐 bridge.py 协议）：
 * - text 事件按轮次累积到 transcript；tool/emoji 作为独立条目；
 * - done 事件闭合一轮（ok=False 时标注被 interrupt / 异常终止）；
 * - busy 状态驱动输入框禁用与「停止」按钮显隐；
 * - mode / theme / uiSettings 走 RPC（mode.list / config.get / ui_settings.get）。
 */

export type ConnectionPhase = "connecting" | "ready" | "reconnecting" | "offline";

export type ModeName = "chat" | "work" | "code" | "learn";

export const MODE_NAMES: ModeName[] = ["chat", "work", "code", "learn"];

export interface ModeInfo {
  name: string;
  display_name?: string;
  /** 后端 available_modes() 给出的能力描述（可选，用于模式说明）。 */
  sandbox?: string;
  workdir?: string;
  bindable?: boolean;
  supports_vision_input?: boolean;
}

export type ThemeMode = "dark" | "light";

/** 外观偏好（ui_settings.json 的 Web 视图；设置中心外观页可调）。 */
export interface AppearanceSettings {
  bubbleRadius: number;
  lineHeight: number;
  sectionGap: number;
  bubbles: boolean;
}

export const APPEARANCE_DEFAULTS: AppearanceSettings = {
  bubbleRadius: 16,
  lineHeight: 1.6,
  sectionGap: 16,
  bubbles: true,
};

export interface UiSettings {
  theme: ThemeMode;
  thinkingLevel: string;
  /** 语气风格（默认 / 温柔 / 简洁 / 毒舌 / 学术）。 */
  style?: string;
}

export interface ApprovalDetail {
  request_id: string;
  tool: string;
  mode: string;
  reason: string;
  target_paths: string[];
  arguments: Record<string, string>;
}

/** 主窗口内通知横幅（主动聊天投递，WebNotifier → 桥接 notification 事件）。 */
export interface NotificationBanner {
  id: string;
  title: string;
  body: string;
  targetId: string;
}

export interface TranscriptEntry {
  id: string;
  role: "user" | "assistant" | "system";
  /** text 条目是流式累积的文本；tool 是工具调用摘要；emoji 是表情包。 */
  kind: "text" | "tool" | "emoji" | "image";
  content: string;
  /** emoji / tool / image 事件的额外载荷（图片路径、工具名等）。 */
  meta?: Record<string, unknown>;
  /** done(ok=false) 闭合时标记。 */
  interrupted?: boolean;
  /** streaming=true 表示 assistant 文本轮仍在接收。 */
  streaming?: boolean;
}

/** 会话列表条目（SessionsPanel 渲染用）。 */
export interface SessionSummary {
  id: string;
  mode: string;
  /** 显示标题（首条用户消息截断，后端或前端派生）。 */
  title: string;
  updatedAt?: number;
  messageCount?: number;
}

interface BridgeStore {
  phase: ConnectionPhase;
  busy: boolean;
  entries: TranscriptEntry[];
  /** 当前模式（mode.list 的 active，或本地切换的乐观值）。 */
  mode: ModeName;
  modes: ModeInfo[];
  /** 图片输入可用（provider vision，config 读取；无键时默认关闭）。 */
  vision: boolean;
  /** 深浅主题（ui_settings.get；fail-closed 默认 dark）。 */
  theme: ThemeMode;
  /** thinking 档位（低/中/高）。 */
  thinkingLevel: string;
  /** 语气风格。 */
  style: string;
  /** 外观偏好（气泡圆角 / 行距 / 段间距 / 气泡开关）。 */
  appearance: AppearanceSettings;

  /** 审批卡当前展示的请求（null = 无待决审批）。 */
  approval: ApprovalDetail | null;
  /** 会话是否已开始（首条用户消息后输入框下沉）。 */
  chatStarted: boolean;
  /** 主窗口内横幅队列（notification 事件）。 */
  banners: NotificationBanner[];

  /** 侧栏会话列表。 */
  sessions: SessionSummary[];
  /** 当前会话 id（新建/切换时变化）。 */
  activeSessionId: string | null;
  /** 侧栏是否折叠。 */
  sidebarCollapsed: boolean;
  /** 各模式当前绑定的工作目录（模式名 -> 绝对路径）。 */
  workdirs: Record<string, string>;

  setPhase: (phase: ConnectionPhase) => void;
  pushUser: (text: string) => void;
  applyEvent: (event: BridgeEvent) => void;
  dismissBanner: (id: string) => void;
  setMode: (mode: ModeName) => void;
  /**
   * 更新模式清单。
   *
   * active 省略时**不覆盖**当前 mode——这一点很关键：面板刷新（如
   * ModeWorkspace 拉能力信息）会在用户切换模式的同一帧发生，
   * 若把后端返回的旧 active 写回去，用户的点击会被静默回滚。
   */
  setModes: (modes: ModeInfo[], active?: ModeName) => void;
  setVision: (vision: boolean) => void;
  setTheme: (theme: ThemeMode) => void;
  setThinkingLevel: (level: string) => void;
  setStyle: (style: string) => void;
  setAppearance: (patch: Partial<AppearanceSettings>) => void;
  setApproval: (detail: ApprovalDetail | null) => void;
  markChatStarted: () => void;
  setSessions: (sessions: SessionSummary[]) => void;
  setActiveSession: (id: string | null) => void;
  setWorkdirs: (workdirs: Record<string, string>) => void;
  toggleSidebar: () => void;
  /** 载入历史会话（entries 整体替换）。 */
  loadEntries: (entries: TranscriptEntry[]) => void;
  reset: () => void;
}

let seq = 0;
const nextId = () => "e" + ++seq;
let bannerSeq = 0;

export const normalizeMode = (value: unknown): ModeName => {
  const name = String(value ?? "").toLowerCase();
  return name === "work" || name === "code" || name === "learn" ? name : "chat";
};

export const useBridgeStore = create<BridgeStore>((set) => ({
  phase: "connecting",
  busy: false,
  entries: [],
  mode: "chat",
  modes: [],
  vision: false,
  theme: "dark",
  thinkingLevel: "中",
  style: "默认",
  appearance: { ...APPEARANCE_DEFAULTS },
  approval: null,
  chatStarted: false,
  banners: [],
  sessions: [],
  activeSessionId: null,
  sidebarCollapsed: false,
  workdirs: {},

  setPhase: (phase) => set({ phase }),

  markChatStarted: () => set({ chatStarted: true }),

  pushUser: (text) =>
    set((s) => ({
      entries: [
        ...s.entries,
        { id: nextId(), role: "user", kind: "text", content: text },
      ],
    })),

  applyEvent: (event) =>
    set((s) => {
      if (event.kind === "text") {
        // 流式文本：追加到当前 streaming 的 assistant 条目；没有则新开。
        const entries = [...s.entries];
        const last = entries[entries.length - 1];
        const chunk = typeof event.payload === "string" ? event.payload : String(event.payload);
        if (last && last.role === "assistant" && last.kind === "text" && last.streaming) {
          entries[entries.length - 1] = { ...last, content: last.content + chunk };
        } else {
          entries.push({
            id: nextId(),
            role: "assistant",
            kind: "text",
            content: chunk,
            streaming: true,
          });
        }
        return { entries, busy: true };
      }

      if (event.kind === "emoji") {
        // emoji 载荷：{name, path}（harness 契约）。path 是宿主机绝对路径，
        // 渲染侧只用文件名拼内置贴纸目录（见 lib/assets.ts stickerUrl）。
        const meta = (event.payload && typeof event.payload === "object")
          ? (event.payload as Record<string, unknown>)
          : {};
        const name = typeof meta.name === "string" ? meta.name : "表情";
        return {
          entries: [
            ...s.entries,
            { id: nextId(), role: "assistant", kind: "emoji", content: name, meta },
          ],
          busy: true,
        };
      }

      if (event.kind === "tool") {
        const meta = (event.payload && typeof event.payload === "object")
          ? (event.payload as Record<string, unknown>)
          : {};
        const name = typeof meta.name === "string" ? meta.name : "tool";
        return {
          entries: [
            ...s.entries,
            { id: nextId(), role: "assistant", kind: "tool", content: name, meta },
          ],
          busy: true,
        };
      }

      if (event.kind === "notification") {
        // 主动聊天通知（WebNotifier 经桥接广播）：进横幅队列，不进聊天流。
        const meta = (event.payload && typeof event.payload === "object")
          ? (event.payload as Record<string, unknown>)
          : {};
        const banner: NotificationBanner = {
          id: "n" + ++bannerSeq,
          title: String(meta.title ?? "CastoriceAgent"),
          body: String(meta.body ?? ""),
          targetId: String(meta.target_id ?? ""),
        };
        return { banners: [...s.banners, banner] };
      }

      if (event.kind !== "done") {
        // 未知事件种类：忽略（绝不误触 done 的闭合语义）。
        return {};
      }

      // done：闭合当前轮（streaming 标记清掉；ok=false 标注中断）。
      const ok = event.ok !== false;
      const entries = s.entries.map((e) =>
        e.streaming ? { ...e, streaming: false, interrupted: !ok } : e,
      );
      return { entries, busy: false };
    }),

  dismissBanner: (id) =>
    set((s) => ({ banners: s.banners.filter((b) => b.id !== id) })),

  setMode: (mode) => set({ mode }),

  setModes: (modes, active) =>
    set(active === undefined ? { modes } : { modes, mode: normalizeMode(active) }),

  setVision: (vision) => set({ vision }),

  setTheme: (theme) => set({ theme }),

  setThinkingLevel: (thinkingLevel) => set({ thinkingLevel }),

  setStyle: (style) => set({ style }),

  setAppearance: (patch) =>
    set((s) => ({ appearance: { ...s.appearance, ...patch } })),

  setApproval: (approval) => set({ approval }),

  setSessions: (sessions) => set({ sessions }),

  setActiveSession: (activeSessionId) => set({ activeSessionId }),

  setWorkdirs: (workdirs) => set({ workdirs }),

  toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),

  loadEntries: (entries) => set({ entries, chatStarted: entries.length > 0 }),

  reset: () => set({ entries: [], busy: false, chatStarted: false }),
}));

/*
 * 调试钩子：把 store 挂到 window 上，便于 DevTools 与自动化截图脚本
 * 直接读写状态（不引入任何生产逻辑，只是暴露已有对象）。
 */
if (typeof window !== "undefined") {
  (window as unknown as { __CA_STORE__?: unknown }).__CA_STORE__ = useBridgeStore;
}
