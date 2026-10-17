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

export interface ModeInfo {
  name: string;
  display_name?: string;
}

export type ThemeMode = "dark" | "light";

export interface UiSettings {
  theme: ThemeMode;
  thinkingLevel: string;
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
  /** thinking 档位占位（低/中/高）。 */
  thinkingLevel: string;
  /** 审批卡当前展示的请求（null = 无待决审批）。 */
  approval: ApprovalDetail | null;
  /** 会话是否已开始（首条用户消息后输入框下沉）。 */
  chatStarted: boolean;
  /** 主窗口内横幅队列（notification 事件）。 */
  banners: NotificationBanner[];

  setPhase: (phase: ConnectionPhase) => void;
  pushUser: (text: string) => void;
  applyEvent: (event: BridgeEvent) => void;
  dismissBanner: (id: string) => void;
  setMode: (mode: ModeName) => void;
  setModes: (modes: ModeInfo[], active: ModeName) => void;
  setVision: (vision: boolean) => void;
  setTheme: (theme: ThemeMode) => void;
  setThinkingLevel: (level: string) => void;
  setApproval: (detail: ApprovalDetail | null) => void;
  markChatStarted: () => void;
  reset: () => void;
}

let seq = 0;
const nextId = () => "e" + ++seq;
let bannerSeq = 0;

const normalizeMode = (value: unknown): ModeName => {
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
  approval: null,
  chatStarted: false,
  banners: [],

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
        // emoji 载荷：{name, path}（harness 契约）。
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
    set({ modes, mode: normalizeMode(active) }),

  setVision: (vision) => set({ vision }),

  setTheme: (theme) => set({ theme }),

  setThinkingLevel: (thinkingLevel) => set({ thinkingLevel }),

  setApproval: (approval) => set({ approval }),

  reset: () => set({ entries: [], busy: false, chatStarted: false }),
}));
