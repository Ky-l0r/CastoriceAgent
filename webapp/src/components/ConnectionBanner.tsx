import { useBridgeStore } from "../store/bridge-store";

/**
 * 连接状态条：只在非 ready 时出现（ready 时完全不占高度）。
 * 三种非 ready 态各有明确文案，避免用户对着白屏猜。
 */
export function ConnectionBanner() {
  const phase = useBridgeStore((s) => s.phase);
  if (phase === "ready") return null;

  const map = {
    connecting: { text: "正在启动后端…", dot: "bg-amber-400 animate-pulse" },
    reconnecting: { text: "连接中断，正在重连…", dot: "bg-amber-400 animate-pulse" },
    offline: { text: "后端未连接：请检查是否已安装并允许本地服务运行", dot: "bg-rose-400" },
  } as const;

  const item = map[phase];

  return (
    <div
      className="flex shrink-0 items-center justify-center gap-2 py-1.5 text-[11.5px]"
      style={{ background: "var(--c-tool-bg)", color: "var(--c-tool-text)" }}
    >
      <span className={"inline-block h-1.5 w-1.5 rounded-full " + item.dot} />
      {item.text}
    </div>
  );
}
