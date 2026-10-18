import { useEffect } from "react";
import {
  useBridgeStore,
  type ApprovalDetail,
  type ModeName,
  type TranscriptEntry,
} from "../store/bridge-store";

/**
 * 把 preload 暴露的 API 绑定到 zustand store。
 * 挂载一次（App 根组件 useEffect）；组件树只读 store。
 *
 * 连接就绪时做一次性装配：mode.list / vision 能力 / ui_settings；
 * 审批卡通过低频轮询 approval.pending 拉取（桥接层没有推送帧）。
 */
export function useBridgeBinding(): void {
  const setPhase = useBridgeStore((s) => s.setPhase);
  const applyEvent = useBridgeStore((s) => s.applyEvent);

  useEffect(() => {
    const offState = window.castorice.onConnectionState((phase) => {
      setPhase(phase);
      if (phase === "ready") void bootstrapOnce();
    });
    const offEvent = window.castorice.onEvent(applyEvent);
    return () => {
      offState();
      offEvent();
    };
  }, [setPhase, applyEvent]);

  // 审批轮询：桥接层 approval 状态无推送帧，低频拉取（1.5s）。
  // pending 集合非空且当前无卡片时，取最早一个 id 拉 detail 并弹卡。
  useEffect(() => {
    const timer = setInterval(() => {
      void pollApproval();
    }, 1500);
    return () => clearInterval(timer);
  }, []);
}

let bootstrapped = false;

/** 一次性装配（连接抖动后幂等）。 */
async function bootstrapOnce(): Promise<void> {
  if (bootstrapped) return;
  bootstrapped = true;
  const { invoke } = bridgeActions();

  // 模式清单（失败静默：保持默认 chat + 空 Tab 集）。
  try {
    const listing = (await invoke("mode.list")) as {
      modes?: { name?: string; display_name?: string }[];
      active?: string;
    };
    const modes = (listing.modes ?? []).map((m) => ({
      name: String(m.name ?? ""),
      display_name: m.display_name ? String(m.display_name) : undefined,
    }));
    useBridgeStore.getState().setModes(modes, (listing.active ?? "chat") as ModeName);
  } catch {
    /* fail-open：模式 Tab 退化为本地四模式 */
  }

  // vision 能力：active_provider 下是否 vision（config.get 无键时默认关闭）。
  try {
    const provider = (await invoke("config.get", { key: "active_provider" })) as {
      value?: unknown;
    };
    const name = String(provider.value ?? "");
    if (name) {
      const section = (await invoke("config.get", { key: "providers." + name })) as {
        value?: Record<string, unknown>;
      };
      useBridgeStore.getState().setVision(Boolean(section.value?.vision));
    }
  } catch {
    /* fail-open */
  }

  // 会话列表（侧栏「最近对话」；后端未实现该 RPC 时保持空列表）。
  await refreshSessions();

  // 界面设置（theme / thinking 档位；fail-closed 默认值）。
  try {
    const settings = (await invoke("ui_settings.get")) as {
      settings?: Record<string, unknown>;
    };
    const s = settings.settings ?? {};
    if (s.theme === "light") useBridgeStore.getState().setTheme("light");
    if (typeof s.thinkingLevel === "string" && s.thinkingLevel) {
      useBridgeStore.getState().setThinkingLevel(s.thinkingLevel);
    }
    if (typeof s.style === "string" && s.style) {
      useBridgeStore.getState().setStyle(s.style);
    }
    // 外观偏好（缺省项保持默认，不做 fail-closed 清零）。
    const num = (v: unknown, min: number, max: number, fb: number) => {
      const n = typeof v === "number" ? v : Number(v);
      return Number.isFinite(n) ? Math.min(max, Math.max(min, n)) : fb;
    };
    useBridgeStore.getState().setAppearance({
      bubbleRadius: num(s.bubbleRadius, 0, 32, 16),
      lineHeight: num(s.lineHeight, 1.2, 2.2, 1.6),
      sectionGap: num(s.sectionGap, 8, 40, 16),
      bubbles: s.bubbles === undefined ? true : Boolean(s.bubbles),
    });
  } catch {
    /* fail-closed：保持默认 dark */
  }
}

/** 重新拉会话列表（新建/删除/切换后调用）。 */
async function refreshSessions(): Promise<void> {
  try {
    const listing = (await window.castorice.invoke("sessions.list")) as {
      sessions?: { id?: string; mode?: string; title?: string; updatedAt?: number; messageCount?: number }[];
      active?: string;
      workdirs?: Record<string, string>;
    };
    const sessions = (listing.sessions ?? [])
      .map((s) => ({
        id: String(s.id ?? ""),
        mode: String(s.mode ?? ""),
        title: String(s.title ?? "新对话"),
        updatedAt: s.updatedAt,
        messageCount: s.messageCount,
      }))
      .filter((s) => s.id);
    const store = useBridgeStore.getState();
    store.setSessions(sessions);
    if (listing.active) store.setActiveSession(String(listing.active));
    if (listing.workdirs) store.setWorkdirs(listing.workdirs);
  } catch {
    /* fail-open */
  }
}

/**
 * 把某个会话的历史事件转成界面条目。
 *
 * 走 sessions.history RPC（后端读 JSONL 并投影）；后端未实现时返回空数组，
 * 界面显示空对话而不是报错——切换会话至少不会白屏。
 */
async function loadSessionEntries(sessionId: string): Promise<TranscriptEntry[]> {
  try {
    const r = (await window.castorice.invoke("sessions.history", {
      session_id: sessionId,
    })) as { events?: { kind?: string; role?: string; text?: string; name?: string; path?: string; status?: string }[] };
    const events = r.events ?? [];
    const entries: TranscriptEntry[] = [];
    let seq = 0;
    for (const e of events) {
      const kind = String(e.kind ?? "");
      const id = "h" + ++seq;
      if (kind === "user") {
        entries.push({ id, role: "user", kind: "text", content: String(e.text ?? "") });
      } else if (kind === "assistant") {
        entries.push({
          id,
          role: "assistant",
          kind: "text",
          content: String(e.text ?? ""),
        });
      } else if (kind === "emoji") {
        entries.push({
          id,
          role: "assistant",
          kind: "emoji",
          content: String(e.name ?? "表情"),
          meta: { name: e.name, path: e.path },
        });
      } else if (kind === "tool") {
        entries.push({
          id,
          role: "assistant",
          kind: "tool",
          content: String(e.name ?? "tool"),
          meta: { status: e.status ?? "done" },
        });
      }
    }
    return entries;
  } catch {
    return [];
  }
}

/** 审批轮询单步：pending 非空且无卡片 -> 弹卡；pending 空 -> 收卡。 */
async function pollApproval(): Promise<void> {
  const store = useBridgeStore.getState();
  if (store.phase !== "ready") return;
  const { invoke } = bridgeActions();
  try {
    const result = (await invoke("approval.pending")) as { pending?: string[] };
    const ids = result.pending ?? [];
    if (ids.length === 0) {
      if (store.approval !== null) store.setApproval(null);
      return;
    }
    if (store.approval !== null) return; // 已有卡片在展示
    // 待决多个时取最早一个（pending_ids 是 tuple，顺序即登记序）。
    const detailResult = (await invoke("approval.detail", {
      request_id: ids[0],
    })) as { detail?: ApprovalDetail | null };
    if (detailResult.detail) {
      store.setApproval(detailResult.detail);
    } else {
      // detail 不可用（扩展未注册等）：退化渲染仅 id 的卡片。
      store.setApproval({
        request_id: ids[0],
        tool: "未知工具",
        mode: "",
        reason: "详情不可用（approval.detail 未注册）",
        target_paths: [],
        arguments: {},
      });
    }
  } catch {
    /* 桥接忙/断线：下个周期再试 */
  }
}

/** 组件侧动作包装。 */
export function bridgeActions() {
  return {
    chat: (text: string, images?: string[]) => {
      useBridgeStore.getState().pushUser(text);
      useBridgeStore.getState().markChatStarted();
      return window.castorice.chat(text, images);
    },
    interrupt: () => window.castorice.interrupt(),
    invoke: (method: string, params?: Record<string, unknown>) =>
      window.castorice.invoke(method, params),
    /** 模式切换（乐观更新 + RPC 落库；失败回滚由 mode.list 下次装配修正）。 */
    switchMode: async (mode: ModeName) => {
      useBridgeStore.getState().setMode(mode);
      try {
        await window.castorice.invoke("mode.switch", { mode });
      } catch {
        /* 静默：桥接层显式拒绝未知模式 */
      }
    },
    /** 审批决定（allow/deny 一次性语义；resolve 后立即收卡）。 */
    resolveApproval: async (requestId: string, decision: "allow" | "deny") => {
      useBridgeStore.getState().setApproval(null);
      try {
        await window.castorice.invoke("approval.resolve", {
          request_id: requestId,
          decision,
        });
      } catch {
        /* 请求已超时/结束：卡片已收，无副作用 */
      }
    },
    /** 主题切换（本地立即生效 + ui_settings.set 落库）。 */
    setTheme: async (theme: "dark" | "light") => {
      useBridgeStore.getState().setTheme(theme);
      try {
        await window.castorice.invoke("ui_settings.set", {
          updates: { theme },
        });
      } catch {
        /* 落库失败不影响本次预览 */
      }
    },
    setThinkingLevel: async (level: string) => {
      useBridgeStore.getState().setThinkingLevel(level);
      try {
        await window.castorice.invoke("ui_settings.set", {
          updates: { thinkingLevel: level },
        });
      } catch {
        /* 同上 */
      }
    },
    /** 新建会话（后端关闭旧会话、开新文件）。 */
    newSession: async () => {
      try {
        const r = (await window.castorice.invoke("session.new")) as { session_id?: string };
        useBridgeStore.getState().reset();
        if (r.session_id) useBridgeStore.getState().setActiveSession(r.session_id);
        await refreshSessions();
        return true;
      } catch {
        return false;
      }
    },

    /** 切换会话（后端恢复历史；成功后重载消息流）。 */
    switchSession: async (sessionId: string) => {
      try {
        await window.castorice.invoke("session.switch", { session_id: sessionId });
        useBridgeStore.getState().setActiveSession(sessionId);
        const entries = await loadSessionEntries(sessionId);
        useBridgeStore.getState().loadEntries(entries);
        return true;
      } catch {
        return false;
      }
    },

    /** 删除会话（后端拒绝删正在用的那个）。 */
    deleteSession: async (sessionId: string) => {
      try {
        await window.castorice.invoke("session.delete", { session_id: sessionId });
        await refreshSessions();
        return true;
      } catch {
        return false;
      }
    },

    /** 语气风格（本地立即生效 + ui_settings.set 落库）。 */
    setStyle: async (style: string) => {
      useBridgeStore.getState().setStyle(style);
      try {
        await window.castorice.invoke("ui_settings.set", {
          updates: { style },
        });
      } catch {
        /* 落库失败不影响本次预览 */
      }
    },
  };
}
