import { FilePlus2, Wrench, Sparkles, Cpu, Settings, UserRound } from "lucide-react";

/**
 * 左侧窄导航图标列（参考图 1 观感）：
 * 新建对话 / 工具 / 技能 / 模型 + 底部设置与用户头像。
 * 本任务只做欢迎页/聊天流的入口观感；工具/技能/设置页由后续任务展开
 * （点击目前是无操作的占位，悬浮有 title 提示）。
 */
export function SideNav({
  avatarUrl,
  onNewChat,
  onOpenSettings,
}: {
  avatarUrl?: string;
  onNewChat: () => void;
  /** 打开设置中心（t4）；点击头像同样进入设置页的用户档案分类。 */
  onOpenSettings?: () => void;
}) {
  const item =
    "flex h-10 w-10 items-center justify-center rounded-xl transition-colors cursor-pointer hover:bg-[var(--c-nav-active-bg)] text-[var(--c-nav-text)] hover:text-[var(--c-nav-text-hover)]";
  return (
    <nav
      className="flex h-full w-16 shrink-0 flex-col items-center justify-between border-r py-4"
      style={{ borderColor: "var(--c-input-border)", background: "var(--c-window-bg)" }}
    >
      <div className="flex flex-col items-center gap-3">
        <button type="button" className={item} title="新建对话" onClick={onNewChat}>
          <FilePlus2 size={19} strokeWidth={1.8} />
        </button>
        <button type="button" className={item} title="工具（建设中）">
          <Wrench size={19} strokeWidth={1.8} />
        </button>
        <button type="button" className={item} title="技能（建设中）">
          <Sparkles size={19} strokeWidth={1.8} />
        </button>
        <button type="button" className={item} title="模型（建设中）">
          <Cpu size={19} strokeWidth={1.8} />
        </button>
      </div>
      <div className="flex flex-col items-center gap-3">
        <button
          type="button"
          className={item}
          title="设置"
          onClick={onOpenSettings}
        >
          <Settings size={19} strokeWidth={1.8} />
        </button>
        <div
          className="flex h-10 w-10 cursor-pointer items-center justify-center overflow-hidden rounded-full border transition-colors hover:opacity-80"
          style={{ background: "var(--c-avatar-bg)", borderColor: "var(--c-avatar-border)" }}
          title="用户档案"
          onClick={onOpenSettings}
        >
          {avatarUrl ? (
            <img src={avatarUrl} alt="头像" className="h-full w-full object-cover" />
          ) : (
            <UserRound size={18} className="text-[var(--c-nav-text)]" />
          )}
        </div>
      </div>
    </nav>
  );
}
