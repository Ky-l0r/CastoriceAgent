import { MessageCircle, Monitor, FolderCode, BookOpen } from "lucide-react";
import { useBridgeStore, MODE_NAMES, type ModeName } from "../store/bridge-store";
import { bridgeActions } from "../lib/bridge-binding";

/**
 * 模式切换：分段选择器（分段滑动的指示器 + 图标），
 * 参考昔涟的 SegmentedControl 观感——不再是第一期的紫色通栏胶囊。
 *
 * 指示器用绝对定位的滑动块实现，不引第三方库。
 */

const MODE_META: Record<ModeName, { label: string; icon: typeof MessageCircle; hint: string }> = {
  chat: { label: "聊天", icon: MessageCircle, hint: "闲聊与情感陪伴，不启用工具" },
  work: { label: "工作", icon: Monitor, hint: "文件读写、命令执行等通用生产力工具" },
  code: { label: "编程", icon: FolderCode, hint: "代码目录内读写与运行，绑定项目根目录" },
  learn: { label: "学习", icon: BookOpen, hint: "知识检索与笔记整理，绑定学习目录" },
};

export function ModeTabs() {
  const modes = useBridgeStore((s) => s.modes);
  const active = useBridgeStore((s) => s.mode);

  // 后端清单优先（保留自定义 display_name），但顺序固定为四模式，
  // 避免后端返回顺序变化导致指示器跳动。
  const labelOf = (name: ModeName): string => {
    const found = modes.find((m) => m.name === name);
    return found?.display_name || MODE_META[name].label;
  };

  const activeIndex = Math.max(0, MODE_NAMES.indexOf(active));

  return (
    <div className="flex shrink-0 items-center justify-center py-2">
      <div className="relative flex items-center gap-0.5 rounded-full p-1"
        style={{ background: "var(--c-tool-bg)", border: "1px solid var(--c-divider)" }}
      >
        {/* 滑动指示器 */}
        <div
          className="absolute top-1 bottom-1 rounded-full transition-transform duration-200 ease-out"
          style={{
            width: `calc((100% - 8px) / ${MODE_NAMES.length})`,
            transform: `translateX(calc(${activeIndex} * 100%))`,
            background: "var(--c-mode-tab-active-bg)",
            border: "1px solid var(--c-divider)",
            boxShadow: "var(--shadow-md)",
            left: 4,
          }}
        />
        {MODE_NAMES.map((name) => {
          const Icon = MODE_META[name].icon;
          const isActive = name === active;
          return (
            <button
              key={name}
              type="button"
              title={MODE_META[name].hint}
              className="relative z-10 flex flex-1 items-center justify-center gap-1.5 rounded-full px-4 py-[5px] text-[13px] transition-colors"
              style={{
                color: isActive ? "var(--c-mode-tab-active-text)" : "var(--c-mode-tab-text)",
                fontWeight: isActive ? 600 : 400,
              }}
              onClick={() => void bridgeActions().switchMode(name)}
            >
              <Icon size={14} strokeWidth={1.8} />
              <span>{labelOf(name)}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
