import { useEffect, useState } from "react";
import { useBridgeBinding } from "./lib/bridge-binding";
import { useBridgeStore } from "./store/bridge-store";
import { TitleBar } from "./components/TitleBar";
import { SideNav } from "./components/SideNav";
import { ModeTabs } from "./components/ModeTabs";
import { ChatMessageList } from "./components/ChatMessageList";
import { Composer } from "./components/Composer";
import { ApprovalCard } from "./components/ApprovalCard";
import { NotificationBanner } from "./components/NotificationBanner";
import { ModeWorkspace } from "./components/ModeWorkspace";
import { SettingsPage } from "./pages/SettingsPage";
import { bridgeActions } from "./lib/bridge-binding";
import { ConnectionBanner } from "./components/ConnectionBanner";

/**
 * 主界面组合根。
 *
 * 布局（第四期重排）：
 *   标题栏
 *   ├ 侧栏（会话列表，可折叠）
 *   └ 内容区
 *       ├ 模式分段选择器
 *       ├ 连接状态条（仅在非 ready 时出现）
 *       ├ 聊天流 / 欢迎页  ← 必须 min-h-0 才能正确吸收剩余高度
 *       ├ 输入框
 *       └ 模式工作区（work / code / learn 时右侧展开）
 */
export default function App() {
  useBridgeBinding();
  const theme = useBridgeStore((s) => s.theme);
  const entries = useBridgeStore((s) => s.entries);
  const chatStarted = useBridgeStore((s) => s.chatStarted);
  const mode = useBridgeStore((s) => s.mode);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  // 外观变量：气泡圆角 / 行距 / 段间距（设置中心外观页写入 ui_settings）。
  const appearance = useBridgeStore((s) => s.appearance);
  useEffect(() => {
    const root = document.documentElement;
    root.style.setProperty("--app-bubble-radius", appearance.bubbleRadius + "px");
    root.style.setProperty("--app-line-height", String(appearance.lineHeight));
    root.style.setProperty("--app-section-gap", appearance.sectionGap + "px");
    root.dataset.bubbles = appearance.bubbles ? "on" : "off";
  }, [appearance]);

  const hasChat = chatStarted || entries.length > 0;
  const [view, setView] = useState<"chat" | "settings">("chat");

  const newChat = () => {
    useBridgeStore.getState().reset();
    setView("chat");
  };

  return (
    <div
      className="window-shell flex h-full flex-col overflow-hidden"
      style={{ background: "var(--c-window-bg)" }}
    >
      <TitleBar
        theme={theme}
        onToggleTheme={() => void bridgeActions().setTheme(theme === "dark" ? "light" : "dark")}
      />

      <div className="flex min-h-0 flex-1">
        {view === "chat" && (
          <SideNav onNewChat={newChat} onOpenSettings={() => setView("settings")} />
        )}

        {view === "settings" ? (
          <div className="min-h-0 min-w-0 flex-1">
            <SettingsPage onBack={() => setView("chat")} />
          </div>
        ) : (
          <div className="flex min-h-0 min-w-0 flex-1 flex-col">
            <ModeTabs />
            <ConnectionBanner />
            {hasChat ? (
              <ChatMessageList />
            ) : (
              <div className="min-h-0 flex-1">
                <Composer docked={false} />
              </div>
            )}
            {hasChat && <Composer docked />}
          </div>
        )}

        {view === "chat" && mode !== "chat" && <ModeWorkspace mode={mode} />}
      </div>

      <ApprovalCard />
      <NotificationBanner />
    </div>
  );
}
