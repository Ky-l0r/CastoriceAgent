import { useEffect, useState } from "react";
import { Minus, Square, Copy, X, Sun, Moon } from "lucide-react";

/**
 * 自绘标题栏（无边框窗口）：drag-region + 窗口控制。
 *
 * 最大化：主进程发 window:toggle-maximize；状态由 main 的 maximize /
 * unmaximize 事件广播回来（双击标题栏与系统快捷键也能同步），
 * 所以图标永远反映真实状态，而不是本地翻转的乐观值。
 */
export function TitleBar({
  theme,
  onToggleTheme,
}: {
  theme: "dark" | "light";
  onToggleTheme: () => void;
}) {
  const [maximized, setMaximized] = useState(false);

  useEffect(() => {
    let alive = true;
    void window.castorice.isMaximized?.().then((v) => {
      if (alive) setMaximized(Boolean(v));
    }).catch(() => undefined);
    const off = window.castorice.onMaximizedChanged?.((v) => setMaximized(v));
    return () => {
      alive = false;
      off?.();
    };
  }, []);

  // 最大化时去掉顶部的圆角贴合（CSS 里按 data-maximized 关掉窗口圆角）。
  useEffect(() => {
    document.documentElement.dataset.maximized = maximized ? "true" : "false";
  }, [maximized]);

  const btn =
    "flex h-full w-11 items-center justify-center transition-colors text-[var(--c-titlebar-text)]";

  return (
    <header
      className="drag-region flex h-10 shrink-0 items-center justify-between select-none"
      style={{
        background: "var(--c-titlebar-bg)",
        borderBottom: "1px solid var(--c-divider)",
      }}
    >
      <span
        className="pl-4 text-[13px] font-medium tracking-wide"
        style={{ color: "var(--c-titlebar-text)" }}
      >
        CastoriceAgent
      </span>
      <div className="drag-region flex h-full items-center">
        <button
          type="button"
          title={theme === "dark" ? "切换到浅色主题" : "切换到深色主题"}
          className={btn + " hover:bg-[var(--c-nav-active-bg)]"}
          onClick={onToggleTheme}
        >
          {theme === "dark" ? <Sun size={14} /> : <Moon size={14} />}
        </button>
        <button
          type="button"
          title="最小化"
          className={btn + " hover:bg-[var(--c-nav-active-bg)]"}
          onClick={() => window.castorice.minimize()}
        >
          <Minus size={14} />
        </button>
        <button
          type="button"
          title={maximized ? "还原" : "最大化"}
          className={btn + " hover:bg-[var(--c-nav-active-bg)]"}
          onClick={() => window.castorice.toggleMaximize()}
        >
          {maximized ? <Copy size={12} /> : <Square size={12} />}
        </button>
        <button
          type="button"
          title="关闭（隐藏到托盘）"
          className={btn + " hover:bg-rose-500 hover:text-white"}
          onClick={() => window.castorice.hide()}
        >
          <X size={14} />
        </button>
      </div>
    </header>
  );
}
