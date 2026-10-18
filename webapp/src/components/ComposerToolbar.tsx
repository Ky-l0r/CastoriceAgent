import { FolderOpen, X, ShieldCheck, ChevronDown, Globe, Lock, Pencil } from "lucide-react";
import { useBridgeStore, type ModeName } from "../store/bridge-store";
import { bridgeActions } from "../lib/bridge-binding";
import { Popover } from "./ui/Popover";

/**
 * 输入框上方的工作区与权限控件（对齐昔涟 ChatComposer 的 footer 布局）。
 *
 * 为什么放在这里：用户改工作目录 / 调权限是为了「马上发下一句话」，
 * 跑去右侧面板或设置页会打断输入。两个控件都做成点开即改、立即生效。
 */

const SANDBOX_OPTIONS: {
  value: string;
  label: string;
  hint: string;
  icon: typeof Globe;
}[] = [
  {
    value: "read-only",
    label: "只读",
    hint: "只能读取，不改动任何文件",
    icon: Lock,
  },
  {
    value: "workspace-write",
    label: "工作区可写",
    hint: "可在工作目录内读写文件",
    icon: Pencil,
  },
  {
    value: "danger-full-access",
    label: "完全访问",
    hint: "不受目录限制（谨慎）",
    icon: Globe,
  },
];

const SANDBOX_LABEL: Record<string, string> = {
  "read-only": "只读",
  "workspace-write": "工作区可写",
  "danger-full-access": "完全访问",
};

/** 当前模式是否支持绑定工作目录（chat 不支持）。 */
function isBindable(mode: ModeName): boolean {
  return mode !== "chat";
}

export function ComposerToolbar() {
  const mode = useBridgeStore((s) => s.mode);
  const workdirs = useBridgeStore((s) => s.workdirs);
  const modes = useBridgeStore((s) => s.modes);
  const sandboxes = useBridgeStore((s) => s.sandboxes);

  const workdir = workdirs[mode] ?? "";
  const sandbox = sandboxes[mode] ?? modes.find((m) => m.name === mode)?.sandbox ?? "";

  const bindable = isBindable(mode);

  const bind = async () => {
    const picked = await window.castorice.pickDirectory?.();
    if (!picked) return;
    try {
      const r = (await bridgeActions().invoke("workdir.bind", {
        mode,
        path: picked,
        grant: true,
      })) as { workdir?: string };
      useBridgeStore.getState().setWorkdirs({
        ...workdirs,
        [mode]: String(r.workdir ?? picked),
      });
      // 绑定后沙箱的写边界随之变化，拉一次最新值
      void bridgeActions().refreshModeInfo();
    } catch (e) {
      useBridgeStore.getState().pushToast?.({
        ok: false,
        text: String((e as Error).message || "绑定失败"),
      });
    }
  };

  const unbind = async () => {
    try {
      await bridgeActions().invoke("workdir.bind", { mode, path: "" });
      const next = { ...workdirs };
      delete next[mode];
      useBridgeStore.getState().setWorkdirs(next);
    } catch {
      /* 忽略 */
    }
  };

  const setSandbox = async (value: string) => {
    try {
      await bridgeActions().invoke("config.set", {
        updates: { ["modes." + mode + ".sandbox"]: value },
      });
      useBridgeStore.getState().setSandbox(mode, value);
      void bridgeActions().refreshModeInfo();
    } catch {
      /* 忽略 */
    }
  };

  const chip =
    "flex items-center gap-1.5 rounded-full border px-2.5 py-[4px] text-[11.5px] transition-colors hover:brightness-110";

  return (
    <div className="mb-1.5 flex flex-wrap items-center gap-1.5">
      {bindable && (
        <div className="flex items-center gap-0.5">
          <button
            type="button"
            onClick={() => void bind()}
            title={workdir || "选择工作目录"}
            className={chip}
            style={{
              background: "var(--c-status-chip-bg)",
              borderColor: "var(--c-status-chip-border)",
              color: "var(--c-status-text)",
            }}
          >
            <FolderOpen size={11} />
            <span className="max-w-[180px] truncate">
              {workdir ? shortenPath(workdir) : "选择工作目录"}
            </span>
          </button>
          {workdir && (
            <button
              type="button"
              title="解绑工作目录"
              className="flex h-5 w-5 items-center justify-center rounded-full transition-colors hover:bg-[var(--c-nav-active-bg)]"
              style={{ color: "var(--c-muted)" }}
              onClick={() => void unbind()}
            >
              <X size={9} />
            </button>
          )}
        </div>
      )}

      <Popover
        side="top"
        width={230}
        trigger={({ toggle }) => (
          <button
            type="button"
            onClick={toggle}
            title="文件访问权限：只读 / 工作区可写 / 完全访问"
            className={chip}
            style={{
              background: "var(--c-status-chip-bg)",
              borderColor: "var(--c-status-chip-border)",
              color: "var(--c-status-text)",
            }}
          >
            <ShieldCheck size={11} />
            {SANDBOX_LABEL[sandbox] ?? "跟随默认"}
            <ChevronDown size={9} className="opacity-60" />
          </button>
        )}
      >
        {({ close }) => (
          <>
            <div
              className="px-2.5 py-1 text-[10.5px] font-medium"
              style={{ color: "var(--c-muted)" }}
            >
              文件访问权限（{modeLabel(mode)} 模式）
            </div>
            {SANDBOX_OPTIONS.map((opt) => {
              const Icon = opt.icon;
              const active = opt.value === sandbox;
              return (
                <button
                  key={opt.value}
                  type="button"
                  onClick={() => {
                    void setSandbox(opt.value);
                    close();
                  }}
                  className="flex w-full items-start gap-2 rounded-[var(--r-sm)] px-2.5 py-1.5 text-left transition-colors hover:bg-[var(--c-nav-active-bg)]"
                >
                  <Icon
                    size={12}
                    className="mt-0.5 shrink-0"
                    style={{ color: active ? "var(--c-highlight)" : "var(--c-muted)" }}
                  />
                  <span className="min-w-0 flex-1">
                    <span
                      className="block text-[12.5px]"
                      style={{
                        color: active ? "var(--c-highlight)" : "var(--c-bubble-text)",
                        fontWeight: active ? 600 : 400,
                      }}
                    >
                      {opt.label}
                    </span>
                    <span className="block text-[10.5px] leading-tight" style={{ color: "var(--c-muted)" }}>
                      {opt.hint}
                    </span>
                  </span>
                </button>
              );
            })}
          </>
        )}
      </Popover>
    </div>
  );
}

function modeLabel(mode: string): string {
  return ({ chat: "聊天", work: "工作", code: "编程", learn: "学习" } as Record<string, string>)[mode] ?? mode;
}

/** 路径缩略：保留最后两段，避免长路径把工具条撑开。 */
function shortenPath(path: string): string {
  const parts = path.replace(/\\/g, "/").split("/").filter(Boolean);
  if (parts.length <= 2) return path;
  return "…/" + parts.slice(-2).join("/");
}
