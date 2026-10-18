import { useEffect, useState } from "react";
import {
  Wrench,
  FolderTree,
  NotebookPen,
  FileCode2,
  Terminal,
  RefreshCw,
  ChevronRight,
  FolderSearch,
  X,
  Loader2,
} from "lucide-react";
import { useBridgeStore, type ModeName } from "../store/bridge-store";
import { bridgeActions } from "../lib/bridge-binding";

/**
 * 模式工作区（右侧面板）：四个模式在这里体现差异。
 *
 * 数据来自 mode.list（sandbox / workdir / bindable）与 store.workdirs
 * （sessions.list 的附带字段）。工作目录可在这里直接绑定 / 解绑——
 * 不用绕到设置页去改（用户反馈的「没有地方改绑定目录」）。
 */

interface ModeCapability {
  sandbox?: string;
  workdir?: string;
  bindable?: boolean;
}

const SANDBOX_LABEL: Record<string, string> = {
  "read-only": "只读",
  "workspace-write": "工作区可写",
  "danger-full-access": "完全访问",
};

function Section({
  icon: Icon,
  title,
  action,
  children,
}: {
  icon: typeof Wrench;
  title: string;
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="mb-4">
      <div className="mb-1.5 flex items-center justify-between">
        <div className="flex items-center gap-1.5" style={{ color: "var(--c-muted)" }}>
          <Icon size={13} strokeWidth={1.8} />
          <span className="text-[11px] font-semibold uppercase tracking-wider">{title}</span>
        </div>
        {action}
      </div>
      {children}
    </div>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-3 py-1 text-[12.5px]">
      <span className="shrink-0" style={{ color: "var(--c-muted)" }}>
        {label}
      </span>
      <span className="min-w-0 truncate text-right" style={{ color: "var(--c-bubble-text)" }} title={String(value ?? "")}>
        {value}
      </span>
    </div>
  );
}

/** 工作 / 编程 / 学习三个模式的右侧面板。 */
export function ModeWorkspace({ mode }: { mode: ModeName }) {
  const modes = useBridgeStore((s) => s.modes);
  const entries = useBridgeStore((s) => s.entries);
  const workdirs = useBridgeStore((s) => s.workdirs);
  const [refreshing, setRefreshing] = useState(false);
  const [binding, setBinding] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const info: ModeCapability = modes.find((m) => m.name === mode) ?? {};
  // store.workdirs 是最新事实（绑定后立即可见），mode.list 的 workdir 作兜底
  const workdir = workdirs[mode] || info.workdir || "";

  const toolCalls = entries.filter((e) => e.kind === "tool");

  const refresh = async () => {
    setRefreshing(true);
    try {
      const listing = (await bridgeActions().invoke("mode.list")) as {
        modes?: { name?: string; display_name?: string; sandbox?: string; workdir?: string; bindable?: boolean }[];
      };
      const list = (listing.modes ?? []).map((m) => ({
        name: String(m.name ?? ""),
        display_name: m.display_name ? String(m.display_name) : undefined,
        sandbox: m.sandbox ? String(m.sandbox) : undefined,
        workdir: m.workdir ? String(m.workdir) : undefined,
        bindable: m.bindable,
      }));
      // 只更新清单，不传 active：这里是在用户切换模式后挂载时触发的，
      // 把后端返回的 active 写回去会把刚点的模式回滚成旧值。
      useBridgeStore.getState().setModes(list);
    } catch {
      /* 保留旧值 */
    }
    setRefreshing(false);
  };

  const bindDirectory = async () => {
    setError(null);
    const picked = await window.castorice.pickDirectory?.();
    if (!picked) return; // 用户取消
    setBinding(true);
    try {
      const r = (await bridgeActions().invoke("workdir.bind", {
        mode,
        path: picked,
      })) as { workdir?: string };
      useBridgeStore.getState().setWorkdirs({
        ...workdirs,
        [mode]: String(r.workdir ?? picked),
      });
    } catch (e) {
      setError(String((e as Error).message || "绑定失败"));
    }
    setBinding(false);
  };

  const unbind = async () => {
    setError(null);
    setBinding(true);
    try {
      await bridgeActions().invoke("workdir.bind", { mode, path: "" });
      const next = { ...workdirs };
      delete next[mode];
      useBridgeStore.getState().setWorkdirs(next);
    } catch (e) {
      setError(String((e as Error).message || "解绑失败"));
    }
    setBinding(false);
  };

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode]);

  const title = mode === "work" ? "工作区" : mode === "code" ? "代码区" : "笔记本";
  const Icon = mode === "work" ? Terminal : mode === "code" ? FileCode2 : NotebookPen;

  return (
    <aside
      className="flex h-full w-[272px] shrink-0 flex-col border-l"
      style={{ borderColor: "var(--c-divider)", background: "var(--c-surface-bg)" }}
    >
      <div className="flex items-center justify-between border-b px-3.5 py-2.5" style={{ borderColor: "var(--c-divider)" }}>
        <div className="flex items-center gap-1.5" style={{ color: "var(--c-bubble-text)" }}>
          <Icon size={14} strokeWidth={1.8} />
          <span className="text-[13px] font-semibold">{title}</span>
        </div>
        <button
          type="button"
          title="刷新"
          className="flex h-6 w-6 items-center justify-center rounded-md transition-colors hover:bg-[var(--c-nav-active-bg)]"
          style={{ color: "var(--c-muted)" }}
          onClick={() => void refresh()}
        >
          <RefreshCw size={12} className={refreshing ? "animate-spin" : undefined} />
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-3.5 py-3">
        <Section
          icon={FolderTree}
          title="工作目录"
          action={
            <button
              type="button"
              disabled={binding}
              className="flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] transition-colors hover:bg-[var(--c-nav-active-bg)] disabled:opacity-50"
              style={{ color: "var(--c-highlight)" }}
              onClick={() => void bindDirectory()}
            >
              {binding ? <Loader2 size={10} className="animate-spin" /> : <FolderSearch size={10} />}
              {workdir ? "更换" : "选择目录"}
            </button>
          }
        >
          {workdir ? (
            <div className="flex items-start gap-1">
              <div
                className="min-w-0 flex-1 break-all rounded-[var(--r-sm)] px-2 py-1.5 text-[11.5px]"
                style={{ background: "var(--c-tool-bg)", color: "var(--c-tool-text)" }}
              >
                {workdir}
              </div>
              <button
                type="button"
                title="解绑"
                disabled={binding}
                className="mt-1 flex h-5 w-5 shrink-0 items-center justify-center rounded transition-colors hover:bg-[var(--c-danger)] hover:text-white disabled:opacity-50"
                style={{ color: "var(--c-muted)" }}
                onClick={() => void unbind()}
              >
                <X size={10} />
              </button>
            </div>
          ) : (
            <p className="text-[12px] leading-relaxed" style={{ color: "var(--c-muted)" }}>
              {info.bindable
                ? "还没绑定目录。绑定后本模式的工具只能在该目录内工作。"
                : "本模式不使用工作目录。"}
            </p>
          )}
          {error && (
            <p className="mt-1.5 text-[11.5px] leading-relaxed" style={{ color: "var(--c-danger)" }}>
              {error}
            </p>
          )}
          {info.bindable && (
            <p className="mt-1.5 text-[11px] leading-relaxed" style={{ color: "var(--c-muted)" }}>
              只允许选择可信根目录（默认是项目目录）内的路径，越界会被拒绝。
            </p>
          )}
        </Section>

        <Section icon={Wrench} title="权限级别">
          <Row
            label="沙箱"
            value={SANDBOX_LABEL[info.sandbox ?? ""] ?? info.sandbox ?? "跟随默认"}
          />
        </Section>

        <Section icon={Terminal} title="本轮工具调用">
          {toolCalls.length === 0 ? (
            <p className="text-[12px] leading-relaxed" style={{ color: "var(--c-muted)" }}>
              本轮还没有调用工具。直接描述任务即可，遐蝶会自动选用可用工具。
            </p>
          ) : (
            <div className="flex flex-col gap-1">
              {toolCalls.map((t) => (
                <div
                  key={t.id}
                  className="flex items-center gap-1.5 rounded-[var(--r-sm)] px-2 py-1 text-[11.5px]"
                  style={{ background: "var(--c-tool-bg)", color: "var(--c-tool-text)" }}
                >
                  <ChevronRight size={10} />
                  <span className="truncate">{t.content}</span>
                </div>
              ))}
            </div>
          )}
        </Section>
      </div>
    </aside>
  );
}
