import { useBridgeStore, type ModeName } from "../store/bridge-store";
import { bridgeActions } from "../lib/bridge-binding";

const FALLBACK_MODES: { name: ModeName; label: string }[] = [
  { name: "chat", label: "聊天" },
  { name: "work", label: "工作" },
  { name: "code", label: "编程" },
  { name: "learn", label: "学习" },
];

/**
 * 顶部模式 Tab（chat / work / code / learn）：
 * mode.list RPC 装配（display_name 优先），失败退化为本地四模式。
 * 观感对齐一期 MODE_BAR_BG：紫底白胶囊（active）。
 */
export function ModeTabs() {
  const modes = useBridgeStore((s) => s.modes);
  const active = useBridgeStore((s) => s.mode);

  const tabs = modes.length
    ? modes.map((m) => ({
        name: (["chat", "work", "code", "learn"].includes(m.name) ? m.name : "chat") as ModeName,
        label: m.display_name ?? m.name,
      }))
    : FALLBACK_MODES;

  return (
    <div
      className="flex h-10 shrink-0 items-center gap-1 px-4"
      style={{ background: "var(--c-mode-bar-bg)" }}
    >
      {tabs.map((tab) => {
        const isActive = tab.name === active;
        return (
          <button
            key={tab.name}
            type="button"
            className="rounded-full px-4 py-1 text-sm font-medium transition-colors"
            style={
              isActive
                ? {
                    background: "var(--c-mode-tab-active-bg)",
                    color: "var(--c-mode-tab-active-text)",
                  }
                : { color: "var(--c-mode-tab-text)" }
            }
            onMouseEnter={(e) => {
              if (!isActive) e.currentTarget.style.background = "var(--c-mode-tab-hover-bg)";
            }}
            onMouseLeave={(e) => {
              if (!isActive) e.currentTarget.style.background = "transparent";
            }}
            onClick={() => void bridgeActions().switchMode(tab.name)}
          >
            {tab.label}
          </button>
        );
      })}
    </div>
  );
}
