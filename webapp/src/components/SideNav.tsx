import {
  SquarePen,
  Settings,
  PanelLeftClose,
  PanelLeftOpen,
  MessageSquare,
} from "lucide-react";
import { useBridgeStore } from "../store/bridge-store";
import { AVATAR_URL } from "../lib/assets";

/**
 * 左侧栏：会话列表（可折叠）。
 *
 * 数据来源：sessions（sessions.list RPC，后端 SessionStore.list_sessions）。
 * 后端未实现该 RPC 时列表为空，只显示「新建对话」入口——不阻塞主流程。
 * 折叠状态存在 store 里，纯本地偏好。
 */
export function SideNav({
  onNewChat,
  onOpenSettings,
}: {
  onNewChat: () => void;
  onOpenSettings?: () => void;
}) {
  const sessions = useBridgeStore((s) => s.sessions);
  const activeId = useBridgeStore((s) => s.activeSessionId);
  const collapsed = useBridgeStore((s) => s.sidebarCollapsed);
  const toggle = useBridgeStore((s) => s.toggleSidebar);

  const item =
    "flex h-9 w-9 items-center justify-center rounded-[var(--r-sm)] transition-colors text-[var(--c-nav-text)] hover:bg-[var(--c-nav-active-bg)] hover:text-[var(--c-nav-text-hover)]";

  if (collapsed) {
    return (
      <nav
        className="flex h-full w-14 shrink-0 flex-col items-center justify-between border-r py-3"
        style={{ borderColor: "var(--c-divider)", background: "var(--c-surface-bg)" }}
      >
        <div className="flex flex-col items-center gap-2">
          <button type="button" className={item} title="展开侧栏" onClick={toggle}>
            <PanelLeftOpen size={18} strokeWidth={1.8} />
          </button>
          <button type="button" className={item} title="新建对话" onClick={onNewChat}>
            <SquarePen size={18} strokeWidth={1.8} />
          </button>
        </div>
        <div className="flex flex-col items-center gap-2">
          <button type="button" className={item} title="设置" onClick={onOpenSettings}>
            <Settings size={18} strokeWidth={1.8} />
          </button>
          <button
            type="button"
            className="h-8 w-8 overflow-hidden rounded-full border transition-opacity hover:opacity-80"
            style={{ borderColor: "var(--c-avatar-border)" }}
            title="用户档案"
            onClick={onOpenSettings}
          >
            <img src={AVATAR_URL} alt="" className="h-full w-full object-cover" />
          </button>
        </div>
      </nav>
    );
  }

  return (
    <nav
      className="flex h-full w-60 shrink-0 flex-col border-r"
      style={{ borderColor: "var(--c-divider)", background: "var(--c-surface-bg)" }}
    >
      <div className="flex items-center gap-2 px-3 py-3">
        <button
          type="button"
          onClick={onNewChat}
          className="flex flex-1 items-center gap-2 rounded-[var(--r-sm)] px-2.5 py-2 text-[13px] font-medium transition-colors hover:bg-[var(--c-nav-active-bg)]"
          style={{ color: "var(--c-nav-text-hover)" }}
        >
          <SquarePen size={15} strokeWidth={1.8} />
          新建对话
        </button>
        <button type="button" className={item} title="折叠侧栏" onClick={toggle}>
          <PanelLeftClose size={16} strokeWidth={1.8} />
        </button>
      </div>

      <div
        className="px-4 pb-1 text-[11px] font-semibold uppercase tracking-wider"
        style={{ color: "var(--c-muted)" }}
      >
        最近对话
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
        {sessions.length === 0 ? (
          <div className="px-2 py-3 text-[12px] leading-relaxed" style={{ color: "var(--c-muted)" }}>
            还没有历史对话
          </div>
        ) : (
          sessions.map((s) => {
            const isActive = s.id === activeId;
            return (
              <button
                key={s.id}
                type="button"
                className="group mb-0.5 flex w-full items-center gap-2 rounded-[var(--r-sm)] px-2.5 py-2 text-left text-[13px] transition-colors hover:bg-[var(--c-nav-active-bg)]"
                style={{
                  background: isActive ? "var(--c-nav-active-bg)" : "transparent",
                  color: isActive ? "var(--c-nav-text-hover)" : "var(--c-nav-text)",
                }}
                title={s.title}
              >
                <MessageSquare size={14} strokeWidth={1.8} className="shrink-0 opacity-70" />
                <span className="min-w-0 flex-1 truncate">{s.title}</span>
              </button>
            );
          })
        )}
      </div>

      <div
        className="flex items-center justify-between gap-2 border-t px-3 py-2.5"
        style={{ borderColor: "var(--c-divider)" }}
      >
        <button
          type="button"
          className="flex items-center gap-2 rounded-[var(--r-sm)] px-1.5 py-1 text-[13px] transition-colors hover:bg-[var(--c-nav-active-bg)]"
          style={{ color: "var(--c-nav-text)" }}
          onClick={onOpenSettings}
        >
          <Settings size={16} strokeWidth={1.8} />
          设置
        </button>
        <button
          type="button"
          className="h-8 w-8 overflow-hidden rounded-full border transition-opacity hover:opacity-80"
          style={{ borderColor: "var(--c-avatar-border)", background: "var(--c-avatar-bg)" }}
          title="用户档案"
          onClick={onOpenSettings}
        >
          <img src={AVATAR_URL} alt="" className="h-full w-full object-cover" />
        </button>
      </div>
    </nav>
  );
}
