import { useEffect, useState } from "react";
import { motion } from "motion/react";
import { useBridgeBinding } from "./lib/bridge-binding";
import { useBridgeStore } from "./store/bridge-store";
import { TitleBar } from "./components/TitleBar";
import { SideNav } from "./components/SideNav";
import { ModeTabs } from "./components/ModeTabs";
import { ChatMessageList } from "./components/ChatMessageList";
import { Composer } from "./components/Composer";
import { ApprovalCard } from "./components/ApprovalCard";
import { NotificationBanner } from "./components/NotificationBanner";
import { SettingsPage } from "./pages/SettingsPage";

/**
 * 主界面组合根（参考图 1 观感复刻）：
 * 左窄导航 + 顶部模式 Tab + 中央欢迎页（头像/欢迎语/居中输入框）
 * ↕ 开始聊天后输入框下沉为底部栏 + 聊天流；审批卡中央浮层。
 */
export default function App() {
  useBridgeBinding();
  const phase = useBridgeStore((s) => s.phase);
  const theme = useBridgeStore((s) => s.theme);
  const entries = useBridgeStore((s) => s.entries);
  const chatStarted = useBridgeStore((s) => s.chatStarted);

  // 主题 -> data-theme（global.css 变量切换）。
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  const hasChat = chatStarted || entries.length > 0;

  // 顶层路由：聊天主界面 <-> 设置中心（t4）。设置页返回聊天时保留会话状态。
  const [view, setView] = useState<"chat" | "settings">("chat");

  const newChat = () => {
    useBridgeStore.getState().reset();
  };

  if (view === "settings") {
    return (
      <div className="flex h-full flex-col" style={{ background: "var(--c-window-bg)" }}>
        <TitleBar
          theme={theme}
          onToggleTheme={() => void import("./lib/bridge-binding").then(({ bridgeActions }) => bridgeActions().setTheme(theme === "dark" ? "light" : "dark"))}
        />
        <div className="min-h-0 flex-1">
          <SettingsPage onBack={() => setView("chat")} />
        </div>
        <ApprovalCard />
        <NotificationBanner />
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col" style={{ background: "var(--c-window-bg)" }}>
      <TitleBar
        theme={theme}
        onToggleTheme={() => void import("./lib/bridge-binding").then(({ bridgeActions }) => bridgeActions().setTheme(theme === "dark" ? "light" : "dark"))}
      />
      <div className="flex min-h-0 flex-1">
        <SideNav onNewChat={newChat} onOpenSettings={() => setView("settings")} />
        <div className="flex min-w-0 flex-1 flex-col">
          <ModeTabs />
          {phase !== "ready" && (
            <div
              className="flex items-center justify-center gap-2 py-1.5 text-xs"
              style={{ background: "var(--c-tool-bg)", color: "var(--c-tool-text)" }}
            >
              <span
                className={
                  "inline-block h-1.5 w-1.5 rounded-full " +
                  (phase === "offline" ? "bg-rose-400" : "animate-pulse bg-amber-300")
                }
              />
              {phase === "offline"
                ? "桥接离线：后端未启动或连接断开"
                : "正在连接后端…"}
            </div>
          )}
          {hasChat ? (
            <>
              <ChatMessageList />
              <Composer docked />
            </>
          ) : (
            <motion.div layout className="flex min-h-0 flex-1 flex-col">
              <Composer docked={false} />
            </motion.div>
          )}
        </div>
      </div>
      <ApprovalCard />
      <NotificationBanner />
    </div>
  );
}
