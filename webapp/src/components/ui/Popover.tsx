import { useEffect, useRef, useState } from "react";
import { motion, AnimatePresence } from "motion/react";

/**
 * 轻量弹层：点击外部 / Esc 关闭，向上或向下展开。
 *
 * 不引第三方 popper：这里只有「状态条下拉」和「贴纸面板」两处用，
 * 用 absolute 定位 + 点击穿透判定就够，省一个依赖。
 */
export function Popover({
  trigger,
  children,
  align = "left",
  side = "top",
  panelClassName,
  width = 220,
}: {
  trigger: (props: { open: boolean; toggle: () => void }) => React.ReactNode;
  children: (props: { close: () => void }) => React.ReactNode;
  align?: "left" | "right";
  side?: "top" | "bottom";
  panelClassName?: string;
  width?: number;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div ref={rootRef} className="relative">
      {trigger({ open, toggle: () => setOpen((v) => !v) })}
      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ opacity: 0, y: side === "top" ? 6 : -6, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: side === "top" ? 4 : -4, scale: 0.98 }}
            transition={{ duration: 0.14, ease: "easeOut" }}
            className={
              "ui-popover absolute z-50 overflow-hidden p-1 " + (panelClassName ?? "")
            }
            style={{
              width,
              ...(side === "top" ? { bottom: "calc(100% + 6px)" } : { top: "calc(100% + 6px)" }),
              ...(align === "left" ? { left: 0 } : { right: 0 }),
            }}
          >
            {children({ close: () => setOpen(false) })}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

/** 弹层里的一行选项。 */
export function PopoverItem({
  label,
  hint,
  active,
  onClick,
}: {
  label: React.ReactNode;
  hint?: string;
  active?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex w-full items-center justify-between gap-2 rounded-[var(--r-sm)] px-2.5 py-1.5 text-left text-[13px] transition-colors hover:bg-[var(--c-nav-active-bg)]"
      style={{
        color: active ? "var(--c-highlight)" : "var(--c-bubble-text)",
        fontWeight: active ? 600 : 400,
      }}
    >
      <span className="truncate">{label}</span>
      {hint && (
        <span className="shrink-0 text-[11px]" style={{ color: "var(--c-muted)" }}>
          {hint}
        </span>
      )}
    </button>
  );
}
