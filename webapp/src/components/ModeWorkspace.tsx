import { useEffect, useState } from "react";
import {
  Wrench,
  NotebookPen,
  FileCode2,
  Terminal,
  RefreshCw,
  ChevronRight,
  FolderOpen,
} from "lucide-react";
import { useBridgeStore, type ModeName } from "../store/bridge-store";
import { bridgeActions } from "../lib/bridge-binding";

/**
 * 模式工作区面板（右侧）。
 *
 * 职责收窄：目录绑定与权限已移到输入框旁的工具条（User 反馈「像昔涟一样
 * 放在对话框旁边」），这里只保留**只读的执行现场**：当前工作目录、沙箱、
 * 本轮工具调用记录。改配置去工具条或设置页，看进展留在这里。
 */

const SANDBOX_LABEL: Record<string, string> = {
  "read-only": "只读",
  "workspace-write": "工作区可写",
  "danger-full-access": "完全访问",
};

function Row({ label, value, mono }: { label: string; value: React.ReactNode; mono?: boolean }) {
  return (
    <div className="flex items-start justify-between gap-3 py-1 text-[12.5px]">
      <span className="shrink-0" style={{ color: "var(--c-muted)" }}>
        {label}
      </span>
      <span
        className={"min-w-0 text-right " + (mono ? "break-all font-mono text-[11px]" : "truncate")}
        style={{ color: "var(--c-bubble-text)" }}
        title={typeof value === "string" ? value : undefined}
      >
        {value}
      </span>
    </div>
  );
}

export function ModeWorkspace({ mode }: { mode: ModeName }) {
  const modes = useBridgeStore((s) => s.modes);
  const entries = useBridgeStore((s) => s.entries);
  const workdirs = useBridgeStore((s) => s.workdirs);
  const sandboxes = useBridgeStore((s) => s.sandboxes);
  const [refreshing, setRefreshing] = useState(false);

  const info: { workdir?: string; sandbox?: string } =
    modes.find((m) => m.name === mode) ?? {};
  const workdir = workdirs[mode] || info.workdir || "";
  const sandbox = sandboxes[mode] || info.sandbox || "";
  const toolCalls = entries.filter((e) => e.kind === "tool");

  const refresh = async () => {
    setRefreshing(true);
    await bridgeActions().refreshModeInfo();
    setRefreshing(false);
  };

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode]);

  const title = mode === "work" ? "工作区" : mode === "code" ? "代码区" : "笔记本";
  const Icon = mode === "work" ? Terminal : mode === "code" ? FileCode2 : NotebookPen;

  return (
    <aside
      className="flex h-full w-[264px] shrink-0 flex-col border-l"
      style={{ borderColor: "var(--c-divider)", background: "var(--c-surface-bg)" }}
    >
      <div
        className="flex items-center justify-between border-b px-3.5 py-2.5"
        style={{ borderColor: "var(--c-divider)" }}
      >
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
        <div className="mb-4">
          <div className="mb-1.5 flex items-center gap-1.5" style={{ color: "var(--c-muted)" }}>
            <FolderOpen size={13} strokeWidth={1.8} />
            <span className="text-[11px] font-semibold uppercase tracking-wider">执行现场</span>
          </div>
          <Row label="目录" value={workdir || "未绑定"} mono />
          <Row label="沙箱" value={SANDBOX_LABEL[sandbox] ?? (sandbox || "跟随默认")} />
          <p className="mt-1.5 text-[11px] leading-relaxed" style={{ color: "var(--c-muted)" }}>
            要改目录或权限，用输入框上方那两个按钮——不用离开对话。
          </p>
        </div>

        <div className="mb-4">
          <div className="mb-1.5 flex items-center gap-1.5" style={{ color: "var(--c-muted)" }}>
            <Wrench size={13} strokeWidth={1.8} />
            <span className="text-[11px] font-semibold uppercase tracking-wider">本轮工具调用</span>
          </div>
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
        </div>
      </div>
    </aside>
  );
}
