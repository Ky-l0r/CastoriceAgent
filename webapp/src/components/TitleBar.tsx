import { Minus, X } from "lucide-react";

/**
 * 自绘标题栏（无边框窗口）：drag-region + 窗口控制。
 * 主进程约定 frame:false + roundedCorners，这里只负责观感。
 */
export function TitleBar({ theme, onToggleTheme }: { theme: "dark" | "light"; onToggleTheme: () => void }) {
  return (
    <header
      className="drag-region flex h-9 shrink-0 items-center justify-between pl-4 select-none"
      style={{ background: "var(--c-titlebar-bg)", color: "var(--c-titlebar-text)" }}
    >
      <span className="text-sm font-semibold tracking-wide">CastoriceAgent</span>
      <div className="drag-region flex h-full items-center">
        <button
          type="button"
          title="切换深浅主题"
          className="flex h-full w-11 items-center justify-center transition-colors hover:bg-white/15"
          onClick={onToggleTheme}
        >
          {theme === "dark" ? "☀" : "☾"}
        </button>
        <button
          type="button"
          title="最小化"
          className="flex h-full w-11 items-center justify-center transition-colors hover:bg-white/15"
          onClick={() => window.castorice.minimize()}
        >
          <Minus size={15} />
        </button>
        <button
          type="button"
          title="关闭（隐藏到托盘）"
          className="flex h-full w-11 items-center justify-center transition-colors hover:bg-rose-500/80"
          onClick={() => window.castorice.hide()}
        >
          <X size={15} />
        </button>
      </div>
    </header>
  );
}
