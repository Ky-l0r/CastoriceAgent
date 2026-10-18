import {
  SquarePen,
  Settings,
  PanelLeftClose,
  PanelLeftOpen,
  MessageSquare,
  Trash2,
  Palette,
  Cpu,
  Brain,
  ShieldCheck,
  UserRound,
  Cable,
} from "lucide-react";
import { useBridgeStore, type SessionSummary } from "../store/bridge-store";
import { AVATAR_URL } from "../lib/assets";
import type { SettingsTab } from "../pages/SettingsPage";

/**
 * 左侧栏：会话列表（可折叠）+ 设置快捷入口。
 *
 * 设置入口做成「侧栏底部一排图标 + 点击直达对应分页」——不用先进设置页
 * 再找分类，常用配置（模型 / 档案 / 权限）一步到位。
 */

const QUICK_SETTINGS: { id: SettingsTab; icon: typeof Settings; label: string }[] = [
  { id: "appearance", icon: Palette, label: "外观" },
  { id: "providers", icon: Cpu, label: "模型 API" },
  { id: "profile", icon: UserRound, label: "用户档案" },
  { id: "permissions", icon: ShieldCheck, label: "权限" },
  { id: "memory", icon: Brain, label: "记忆" },
  { id: "channels", icon: Cable, label: "渠道" },
];

export function SideNav({
  onNewChat,
  onOpenSettings,
  onOpenSession,
  onDeleteSession,
}: {
  onNewChat: () => void;
  /** 打开设置中心；带 tab 时直接定位到该分类。 */
  onOpenSettings: (tab?: SettingsTab) => void;
  onOpenSession: (id: string) => void;
  onDeleteSession: (id: string) => void;
}) {
  const sessions = useBridgeStore((s) => s.sessions);
  const activeId = useBridgeStore((s) => s.activeSessionId);
  const collapsed = useBridgeStore((s) => s.sidebarCollapsed);
  const toggle = useBridgeStore((s) => s.toggleSidebar);

  const iconBtn =
    "flex h-9 w-9 items-center justify-center rounded-[var(--r-sm)] transition-colors text-[var(--c-nav-text)] hover:bg-[var(--c-nav-active-bg)] hover:text-[var(--c-nav-text-hover)]";

  if (collapsed) {
    return (
      <nav
        className="flex h-full w-14 shrink-0 flex-col items-center justify-between border-r py-3"
        style={{ borderColor: "var(--c-divider)", background: "var(--c-surface-bg)" }}
      >
        <div className="flex flex-col items-center gap-2">
          <button type="button" className={iconBtn} title="展开侧栏" onClick={toggle}>
            <PanelLeftOpen size={18} strokeWidth={1.8} />
          </button>
          <button type="button" className={iconBtn} title="新建对话" onClick={onNewChat}>
            <SquarePen size={18} strokeWidth={1.8} />
          </button>
        </div>
        <div className="flex flex-col items-center gap-2">
          <button type="button" className={iconBtn} title="设置" onClick={() => onOpenSettings()}>
            <Settings size={18} strokeWidth={1.8} />
          </button>
          <button
            type="button"
            className="h-8 w-8 overflow-hidden rounded-full border transition-opacity hover:opacity-80"
            style={{ borderColor: "var(--c-avatar-border)" }}
            title="用户档案"
            onClick={() => onOpenSettings("profile")}
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
        <button type="button" className={iconBtn} title="折叠侧栏" onClick={toggle}>
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
            还没有历史对话。发一条消息就会自动保存。
          </div>
        ) : (
          sessions.map((s) => (
            <SessionRow
              key={s.id}
              session={s}
              active={s.id === activeId}
              onOpen={() => onOpenSession(s.id)}
              onDelete={() => onDeleteSession(s.id)}
            />
          ))
        )}
      </div>

      {/* 设置快捷入口 */}
      <div className="border-t px-2 py-2" style={{ borderColor: "var(--c-divider)" }}>
        <div
          className="px-2 pb-1.5 text-[11px] font-semibold uppercase tracking-wider"
          style={{ color: "var(--c-muted)" }}
        >
          设置
        </div>
        <div className="grid grid-cols-3 gap-1">
          {QUICK_SETTINGS.map(({ id, icon: Icon, label }) => (
            <button
              key={id}
              type="button"
              title={label}
              onClick={() => onOpenSettings(id)}
              className="flex flex-col items-center gap-1 rounded-[var(--r-sm)] px-1 py-1.5 transition-colors hover:bg-[var(--c-nav-active-bg)]"
              style={{ color: "var(--c-nav-text)" }}
            >
              <Icon size={15} strokeWidth={1.8} />
              <span className="text-[10px] leading-none">{label}</span>
            </button>
          ))}
        </div>
        <div className="mt-1 flex items-center justify-between px-1">
          <button
            type="button"
            className="flex items-center gap-1.5 rounded-[var(--r-sm)] px-1.5 py-1 text-[12px] transition-colors hover:bg-[var(--c-nav-active-bg)]"
            style={{ color: "var(--c-muted)" }}
            onClick={() => onOpenSettings()}
          >
            <Settings size={13} strokeWidth={1.8} />
            全部设置
          </button>
          <button
            type="button"
            className="h-7 w-7 overflow-hidden rounded-full border transition-opacity hover:opacity-80"
            style={{ borderColor: "var(--c-avatar-border)", background: "var(--c-avatar-bg)" }}
            title="用户档案"
            onClick={() => onOpenSettings("profile")}
          >
            <img src={AVATAR_URL} alt="" className="h-full w-full object-cover" />
          </button>
        </div>
      </div>
    </nav>
  );
}

function SessionRow({
  session,
  active,
  onOpen,
  onDelete,
}: {
  session: SessionSummary;
  active: boolean;
  onOpen: () => void;
  onDelete: () => void;
}) {
  return (
    <div
      className="group mb-0.5 flex w-full items-center gap-2 rounded-[var(--r-sm)] px-2.5 py-2 text-left text-[13px] transition-colors hover:bg-[var(--c-nav-active-bg)]"
      style={{
        background: active ? "var(--c-nav-active-bg)" : "transparent",
        color: active ? "var(--c-nav-text-hover)" : "var(--c-nav-text)",
      }}
    >
      <button
        type="button"
        className="flex min-w-0 flex-1 items-center gap-2 text-left"
        title={session.title}
        onClick={onOpen}
      >
        <MessageSquare size={14} strokeWidth={1.8} className="shrink-0 opacity-70" />
        <span className="min-w-0 flex-1 truncate">{session.title}</span>
      </button>
      <button
        type="button"
        title="删除这个对话"
        className="flex h-5 w-5 shrink-0 items-center justify-center rounded opacity-0 transition-opacity hover:bg-[var(--c-danger)] hover:text-white group-hover:opacity-100"
        onClick={(e) => {
          e.stopPropagation();
          onDelete();
        }}
      >
        <Trash2 size={11} />
      </button>
    </div>
  );
}
