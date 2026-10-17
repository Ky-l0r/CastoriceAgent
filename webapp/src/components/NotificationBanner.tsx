import { useEffect } from "react";
import { AnimatePresence, motion } from "motion/react";
import { Bell, X } from "lucide-react";
import { useBridgeStore } from "../store/bridge-store";

/**
 * 主窗口内通知横幅（t5）：WebNotifier 主动聊天投递的窗口内一路。
 * 主进程在窗口不可见时会同时发系统通知；回到窗口后横幅队列仍在，
 * 手动关闭（或 6s 自动消失）才移除——错过的通知不静默丢失。
 */
const AUTO_DISMISS_MS = 6000;

function Banner({ id, title, body }: { id: string; title: string; body: string }) {
  const dismiss = useBridgeStore((s) => s.dismissBanner);

  useEffect(() => {
    const timer = setTimeout(() => dismiss(id), AUTO_DISMISS_MS);
    return () => clearTimeout(timer);
  }, [id, dismiss]);

  return (
    <motion.div
      layout
      initial={{ opacity: 0, x: 60 }}
      animate={{ opacity: 1, x: 0 }}
      exit={{ opacity: 0, x: 60 }}
      transition={{ type: "spring", stiffness: 260, damping: 24 }}
      className="pointer-events-auto flex w-80 items-start gap-2.5 rounded-xl border p-3 shadow-lg backdrop-blur"
      style={{
        background: "var(--c-tool-bg)",
        borderColor: "var(--c-approval-border)",
      }}
    >
      <div
        className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full"
        style={{ background: "var(--c-avatar-bg)" }}
      >
        <Bell size={15} style={{ color: "var(--c-highlight)" }} />
      </div>
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium" style={{ color: "var(--c-approval-title)" }}>
          {title}
        </p>
        <p
          className="mt-0.5 line-clamp-3 text-xs leading-relaxed"
          style={{ color: "var(--c-approval-text)" }}
        >
          {body}
        </p>
      </div>
      <button
        type="button"
        title="关闭"
        className="shrink-0 rounded-md p-1 transition-colors hover:bg-[var(--c-nav-active-bg)]"
        style={{ color: "var(--c-muted)" }}
        onClick={() => dismiss(id)}
      >
        <X size={13} />
      </button>
    </motion.div>
  );
}

export function NotificationBanner() {
  const banners = useBridgeStore((s) => s.banners);
  return (
    <div className="pointer-events-none fixed right-4 top-12 z-40 flex flex-col gap-2">
      <AnimatePresence>
        {banners.map((b) => (
          <Banner key={b.id} id={b.id} title={b.title} body={b.body} />
        ))}
      </AnimatePresence>
    </div>
  );
}
