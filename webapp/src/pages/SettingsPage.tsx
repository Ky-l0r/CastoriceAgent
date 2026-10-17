import { useState, type ComponentType } from "react";
import type { CSSProperties } from "react";
import {
  ArrowLeft,
  Palette,
  Cpu,
  Cable,
  Brain,
  ShieldCheck,
  UserRound,
  Info,
} from "lucide-react";
import { AppearancePanel } from "../components/settings/AppearancePanel";
import { ProvidersPanel } from "../components/settings/ProvidersPanel";
import { NapcatPanel } from "../components/settings/NapcatPanel";
import { MemoryPanel } from "../components/settings/MemoryPanel";
import { PermissionsPanel } from "../components/settings/PermissionsPanel";
import { ProfilePanel } from "../components/settings/ProfilePanel";
import { AboutPanel } from "../components/settings/AboutPanel";

/**
 * 设置中心（参考图 2+3 观感）：左侧分类导航 + 右侧内容区。
 * 独立路由页形态——App 根组件用 view state（"chat" | "settings"）切换，
 * 不弹独立窗口（无边框壳里弹窗管理成本高，页面切换与聊天共享主题变量）。
 */

type SettingsTab =
  | "appearance"
  | "providers"
  | "channels"
  | "memory"
  | "permissions"
  | "profile"
  | "about";

const TABS: {
  id: SettingsTab;
  label: string;
  icon: ComponentType<{
    size?: number | string;
    strokeWidth?: number | string;
    style?: CSSProperties;
  }>;
  title: string;
}[] = [
  { id: "appearance", label: "外观", icon: Palette, title: "外观" },
  { id: "providers", label: "模型 API", icon: Cpu, title: "模型 API" },
  { id: "channels", label: "渠道", icon: Cable, title: "渠道（NapCat）" },
  { id: "memory", label: "记忆", icon: Brain, title: "记忆" },
  { id: "permissions", label: "权限", icon: ShieldCheck, title: "权限" },
  { id: "profile", label: "用户档案", icon: UserRound, title: "用户档案" },
  { id: "about", label: "关于", icon: Info, title: "关于" },
];

export function SettingsPage({ onBack }: { onBack: () => void }) {
  const [tab, setTab] = useState<SettingsTab>("profile");

  const activeTab = TABS.find((t) => t.id === tab) ?? TABS[0];
  const ActiveIcon = activeTab.icon;

  return (
    <div className="flex h-full min-h-0" style={{ background: "var(--c-window-bg)" }}>
      {/* 左侧分类导航 */}
      <aside
        className="flex w-52 shrink-0 flex-col border-r py-4"
        style={{ borderColor: "var(--c-input-border)", background: "var(--c-window-bg)" }}
      >
        <button
          type="button"
          onClick={onBack}
          className="mx-3 mb-3 flex items-center gap-2 rounded-xl px-3 py-2 text-sm transition-colors hover:opacity-80"
          style={{ color: "var(--c-nav-text)" }}
        >
          <ArrowLeft size={16} strokeWidth={1.8} />
          返回对话
        </button>
        <div className="px-3 pb-2 text-xs font-semibold uppercase tracking-wider" style={{ color: "var(--c-muted)" }}>
          设置
        </div>
        <nav className="flex flex-col gap-0.5 px-2">
          {TABS.map(({ id, label, icon: Icon }) => {
            const active = id === tab;
            return (
              <button
                key={id}
                type="button"
                onClick={() => setTab(id)}
                className="flex items-center gap-2.5 rounded-xl px-3 py-2 text-sm transition-colors"
                style={{
                  background: active ? "var(--c-nav-active-bg)" : "transparent",
                  color: active ? "var(--c-nav-text-hover)" : "var(--c-nav-text)",
                }}
              >
                <Icon size={16} strokeWidth={1.8} />
                {label}
              </button>
            );
          })}
        </nav>
      </aside>

      {/* 右侧内容区 */}
      <section className="min-w-0 flex-1 overflow-y-auto">
        <header
          className="sticky top-0 z-10 flex items-center gap-2 border-b px-8 py-4"
          style={{
            borderColor: "var(--c-input-border)",
            background: "var(--c-window-bg)",
          }}
        >
          <ActiveIcon size={18} strokeWidth={1.8} style={{ color: "var(--c-highlight)" }} />
          <h2 className="text-base font-semibold" style={{ color: "var(--c-bubble-text)" }}>
            {activeTab.title}
          </h2>
        </header>
        <div className="mx-auto max-w-2xl px-8 pb-12 pt-2">
          {tab === "appearance" && <AppearancePanel />}
          {tab === "providers" && <ProvidersPanel />}
          {tab === "channels" && <NapcatPanel />}
          {tab === "memory" && <MemoryPanel />}
          {tab === "permissions" && <PermissionsPanel />}
          {tab === "profile" && <ProfilePanel />}
          {tab === "about" && <AboutPanel />}
        </div>
      </section>
    </div>
  );
}
