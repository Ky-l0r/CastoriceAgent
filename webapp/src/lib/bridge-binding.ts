import { useEffect } from "react";
import {
  useBridgeStore,
  type ApprovalDetail,
  type ModeName,
  type TranscriptEntry,
} from "../store/bridge-store";
import { enqueueChat, resetChatQueue } from "./chat-queue";

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
      // 后端重新就绪 = 可能刚重启过（例如用户点了「重启后端」）：
      // 每次都重新装配，配置/模式/会话列表跟着刷新（首次也是这条路径）。
      if (phase === "ready") void bootstrapOnce();
    });
    const offEvent = window.castorice.onEvent(applyEvent);
    const offError = window.castorice.onBackendError?.((info) => {
      useBridgeStore.getState().setBackendError(info ? info.message : null);
    });
    return () => {
      offState();
      offEvent();
      offError?.();
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

/**
 * 装配（每次连接就绪都跑一遍）。
 *
 * 为什么不再「只跑一次」：后端重启（保存渠道配置后点「重启后端」、
 * sidecar 崩溃自愈）会重新触发 ready，这时必须重新拉配置与模式，
 * 否则界面还显示重启前的旧值。每个步骤都是 fail-open 的读操作，
 * 重复执行的代价可以忽略。
 */
async function bootstrapOnce(): Promise<void> {
  const { invoke } = bridgeActions();

  // mode.list 会同时给出模式清单与各模式的沙箱/工作目录，原实现调了两次
  // （串行往返 × 2）。这里合成一次，之后并发拉其它独立数据。
  const modesPromise = (async () => {
    try {
      const listing = (await invoke("mode.list")) as {
        modes?: {
          name?: string;
          display_name?: string;
          sandbox?: string;
          workdir?: string;
        }[];
        active?: string;
      };
      const raw = listing.modes ?? [];
      useBridgeStore.getState().setModes(
        raw.map((m) => ({
          name: String(m.name ?? ""),
          display_name: m.display_name ? String(m.display_name) : undefined,
          sandbox: m.sandbox ? String(m.sandbox) : undefined,
          workdir: m.workdir ? String(m.workdir) : undefined,
        })),
        (listing.active ?? "chat") as ModeName,
      );
      const sandboxes: Record<string, string> = {};
      for (const m of raw) {
        const name = String(m.name ?? "");
        if (name && m.sandbox) sandboxes[name] = String(m.sandbox);
      }
      useBridgeStore.getState().setSandboxes(sandboxes);
    } catch {
      /* fail-open：模式 Tab 退化为本地四模式、工具条显示「跟随默认」 */
    }
  })();

  const visionPromise = (async () => {
    // vision 能力：active_provider 下是否 vision（config.get 无键时默认关闭）。
    try {
      const provider = (await invoke("config.get", { key: "active_provider" })) as {
        value?: unknown;
      };
      const name = String(provider.value ?? "");
      if (!name) return;
      const section = (await invoke("config.get", { key: "providers." + name })) as {
        value?: Record<string, unknown>;
      };
      useBridgeStore.getState().setVision(Boolean(section.value?.vision));
    } catch {
      /* fail-open */
    }
  })();

  // 三组读取彼此独立：并发发出，总耗时约等于最长的那一个。
  await Promise.all([modesPromise, visionPromise, refreshSessions(), refreshUiSettings()]);
}

/**
 * 拉取界面设置（主题 / 思考档位 / 风格 / 外观 / 连发合并参数）。
 *
 * 独立成函数是因为它有两个调用点：连接就绪时装配一次，设置中心保存后
 * 再拉一次——否则在主界面调整的「连发合并」参数要等重启后端才生效。
 * 缺省项一律保持默认值（不做 fail-closed 清零），读失败静默。
 */
export async function refreshUiSettings(): Promise<void> {
  const { invoke } = bridgeActions();
  const num = (v: unknown, min: number, max: number, fb: number) => {
    const n = typeof v === "number" ? v : Number(v);
    return Number.isFinite(n) ? Math.min(max, Math.max(min, n)) : fb;
  };
  const bool = (v: unknown, fb: boolean) =>
    typeof v === "boolean" ? v : v === undefined ? fb : Boolean(v);
  try {
    const settings = (await invoke("ui_settings.get")) as {
      settings?: Record<string, unknown>;
    };
    const s = settings.settings ?? {};
    if (s.theme === "light") useBridgeStore.getState().setTheme("light");
    else if (s.theme === "dark") useBridgeStore.getState().setTheme("dark");
    if (typeof s.thinkingLevel === "string" && s.thinkingLevel) {
      useBridgeStore.getState().setThinkingLevel(s.thinkingLevel);
    }
    if (typeof s.style === "string" && s.style) {
      useBridgeStore.getState().setStyle(s.style);
    }
    // 外观偏好（缺省项保持默认，不做 fail-closed 清零）。
    useBridgeStore.getState().setAppearance({
      bubbleRadius: num(s.bubbleRadius, 0, 32, 16),
      lineHeight: num(s.lineHeight, 1.2, 2.2, 1.6),
      sectionGap: num(s.sectionGap, 8, 40, 16),
      bubbles: s.bubbles === undefined ? true : Boolean(s.bubbles),
    });
    // 连发合并（聊天模式的「整理后再回复」）。
    const batch = (s.chatBatch && typeof s.chatBatch === "object")
      ? (s.chatBatch as Record<string, unknown>)
      : {};
    useBridgeStore.getState().setChatBatch({
      enabled: bool(batch.enabled, true),
      threshold: Math.round(num(batch.threshold, 2, 10, 3)),
      windowSeconds: Math.round(num(batch.windowSeconds, 5, 600, 180)),
      quietSeconds: Math.round(num(batch.quietSeconds, 2, 60, 8)),
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
    /**
     * 发送一条用户消息：先画进聊天流，再交给出站队列决定何时真正发出。
     *
     * 为什么不再直接 window.castorice.chat：busy 期间桥接层会拒绝请求，
     * 消息画出来了却没有回复，用户就会重复发送（「反复回答」的根因）。
     * 队列在本轮 done 之后自动补发，并对连发的多条消息做合并。
     */
    chat: (text: string, images?: string[]) => {
      const store = useBridgeStore.getState();
      store.pushUser(text);
      store.markChatStarted();
      enqueueChat({ text, images });
      return Promise.resolve({ ok: true });
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
    /** 重新拉模式能力（沙箱 / 工作目录），绑定或改权限后调用。 */
    refreshModeInfo: async () => {
      try {
        const listing = (await window.castorice.invoke("mode.list")) as {
          modes?: {
            name?: string;
            display_name?: string;
            sandbox?: string;
            workdir?: string;
            bindable?: boolean;
          }[];
        };
        const list = (listing.modes ?? []).map((m) => ({
          name: String(m.name ?? ""),
          display_name: m.display_name ? String(m.display_name) : undefined,
          sandbox: m.sandbox ? String(m.sandbox) : undefined,
          workdir: m.workdir ? String(m.workdir) : undefined,
          bindable: m.bindable,
        }));
        useBridgeStore.getState().setModes(list);
        const sandboxes: Record<string, string> = {};
        for (const m of list) {
          if (m.sandbox) sandboxes[m.name] = m.sandbox;
        }
        useBridgeStore.getState().setSandboxes(sandboxes);
      } catch {
        /* 保留旧值 */
      }
    },

    /** 新建会话（后端关闭旧会话、开新文件）。 */
    newSession: async () => {
      try {
        resetChatQueue();
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
        // 旧会话里还没发出的消息不能发到新会话（会话语义），直接丢弃。
        resetChatQueue();
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
