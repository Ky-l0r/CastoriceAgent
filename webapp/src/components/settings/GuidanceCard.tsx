import { Info, Lightbulb, AlertTriangle } from "lucide-react";

/**
 * 设置页的说明卡片：统一「这是什么 / 怎么用 / 注意什么」三段式引导。
 *
 * 用户反馈「设置引导做得再好一点」——原来多数面板只有一行 hint，
 * 首次配置时不知道该填什么、填错了会怎样。这个组件把引导做成
 * 视觉上可扫读的块，而不是散落在各字段下的小字。
 */
export function GuidanceCard({
  tone = "info",
  title,
  children,
}: {
  tone?: "info" | "tip" | "warn";
  title: string;
  children: React.ReactNode;
}) {
  const palette = {
    info: { icon: Info, color: "var(--c-highlight)", bg: "var(--c-tool-bg)" },
    tip: { icon: Lightbulb, color: "var(--c-success)", bg: "var(--c-tool-bg)" },
    warn: { icon: AlertTriangle, color: "var(--c-warning)", bg: "var(--c-tool-bg)" },
  }[tone];
  const Icon = palette.icon;

  return (
    <div
      className="mb-4 rounded-[var(--r-md)] border px-3.5 py-3"
      style={{ borderColor: "var(--c-divider)", background: palette.bg }}
    >
      <div className="mb-1 flex items-center gap-1.5">
        <Icon size={13} style={{ color: palette.color }} />
        <span className="text-[12.5px] font-semibold" style={{ color: "var(--c-bubble-text)" }}>
          {title}
        </span>
      </div>
      <div className="text-[12px] leading-relaxed" style={{ color: "var(--c-muted)" }}>
        {children}
      </div>
    </div>
  );
}

/** 空状态块：一个图标 + 说明 + 主操作按钮。 */
export function EmptyState({
  icon: Icon,
  title,
  hint,
  actionLabel,
  onAction,
}: {
  icon: typeof Info;
  title: string;
  hint?: string;
  actionLabel?: string;
  onAction?: () => void;
}) {
  return (
    <div
      className="rounded-[var(--r-lg)] border border-dashed px-4 py-9 text-center"
      style={{ borderColor: "var(--c-input-border)" }}
    >
      <Icon size={26} style={{ color: "var(--c-muted)" }} className="mx-auto mb-2" />
      <p className="text-[13px]" style={{ color: "var(--c-bubble-text)" }}>{title}</p>
      {hint && (
        <p className="mx-auto mt-1 max-w-sm text-[12px] leading-relaxed" style={{ color: "var(--c-muted)" }}>
          {hint}
        </p>
      )}
      {actionLabel && onAction && (
        <button
          type="button"
          onClick={onAction}
          className="mt-3 rounded-full px-4 py-1.5 text-[12.5px] font-medium"
          style={{ background: "var(--c-send-bg)", color: "var(--c-highlight-text)" }}
        >
          {actionLabel}
        </button>
      )}
    </div>
  );
}
