import { useBridgeStore } from "../store/bridge-store";

/**
 * 连接状态条：只在非 ready 时出现（ready 时完全不占高度）。
 *
 * 后端报错时优先显示**具体原因**——例如「配置文件格式错误：... line 102」
 * 用户能直接照着修；笼统的「后端未连接」只会让人无从下手。
 * 原因来自主进程的 bridge:backend-error 推送（sidecar 握手的 stderr 摘要）。
 */
export function ConnectionBanner() {
  const phase = useBridgeStore((s) => s.phase);
  const backendError = useBridgeStore((s) => s.backendError);
  if (phase === "ready") return null;

  const map = {
    connecting: { text: "正在启动后端…", dot: "bg-amber-400 animate-pulse" },
    reconnecting: { text: "连接中断，正在重连…", dot: "bg-amber-400 animate-pulse" },
    offline: { text: "后端未连接：正在后台重试，请稍候", dot: "bg-rose-400" },
  } as const;

  const item = map[phase];

  return (
    <div
      className="flex shrink-0 flex-col items-center gap-0.5 px-4 py-1.5 text-[11.5px]"
      style={{ background: "var(--c-tool-bg)", color: "var(--c-tool-text)" }}
    >
      <div className="flex items-center gap-2">
        <span className={"inline-block h-1.5 w-1.5 rounded-full " + item.dot} />
        {item.text}
      </div>
      {backendError && (
        <div
          className="max-w-full break-words text-center text-[11px]"
          style={{ color: "var(--c-danger)" }}
          title={backendError}
        >
          {backendError}
        </div>
      )}
    </div>
  );
}
