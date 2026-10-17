import { useState } from "react";
import { motion } from "motion/react";
import { stickerUrl } from "../../lib/assets";
import { Popover } from "./Popover";

/**
 * 表情包选择面板。
 *
 * 贴纸清单来源：public/castorice-assets/表情包 下的内置贴纸（与后端
 * EmojiCatalog 同名）。这里不扫盘——渲染进程没有 fs 权限，清单在后端
 * 通过 emoji.list RPC 或本地的固定清单提供；后端不可用时退化为常量表。
 */

/** 与 Image/表情包 对齐的内置贴纸标签（文件名主干 = 触发标签）。 */
export const STICKER_LABELS = [
  "哭泣",
  "喜欢",
  "喜欢1",
  "困倦",
  "害羞",
  "害羞1",
  "惊讶",
  "惊讶1",
  "欣赏",
  "生气",
  "疑问",
  "疑问1",
  "称赞",
  "耍帅",
  "苦恼",
  "苦恼1",
] as const;

export function StickerGrid({
  onPick,
  columns = 4,
  size = 56,
}: {
  onPick: (label: string) => void;
  columns?: number;
  size?: number;
}) {
  const [failed, setFailed] = useState<Record<string, boolean>>({});

  return (
    <div
      className="grid gap-1"
      style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}
    >
      {STICKER_LABELS.map((label) => {
        const url = stickerUrl(label + ".png");
        const broken = failed[label];
        return (
          <button
            key={label}
            type="button"
            title={label}
            className="group flex items-center justify-center rounded-[var(--r-sm)] p-1 transition-colors hover:bg-[var(--c-nav-active-bg)]"
            onClick={() => onPick(label)}
          >
            {broken ? (
              <span
                className="flex items-center justify-center text-[10px]"
                style={{ width: size, height: size, color: "var(--c-muted)" }}
              >
                {label}
              </span>
            ) : (
              <img
                src={url}
                alt={label}
                width={size}
                height={size}
                className="h-auto w-full select-none object-contain"
                style={{ maxHeight: size }}
                draggable={false}
                onError={() => setFailed((p) => ({ ...p, [label]: true }))}
              />
            )}
          </button>
        );
      })}
    </div>
  );
}

/** 输入框里的贴纸按钮 + 弹出面板。 */
export function StickerPickerButton({
  onPick,
}: {
  onPick: (label: string) => void;
}) {
  return (
    <Popover
      side="top"
      align="left"
      width={296}
      trigger={({ toggle }) => (
        <button
          type="button"
          title="发表情包"
          className="flex h-7 w-7 items-center justify-center rounded-lg transition-colors hover:bg-[var(--c-nav-active-bg)]"
          style={{ color: "var(--c-muted)" }}
          onClick={toggle}
        >
          <SmileIcon />
        </button>
      )}
    >
      {({ close }) => (
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="p-1">
          <div
            className="px-1.5 pb-1.5 text-[11px] font-medium"
            style={{ color: "var(--c-muted)" }}
          >
            选择一个表情包
          </div>
          <StickerGrid
            columns={5}
            size={48}
            onPick={(label) => {
              onPick(label);
              close();
            }}
          />
        </motion.div>
      )}
    </Popover>
  );
}

function SmileIcon() {
  return (
    <svg
      width="15"
      height="15"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="9" />
      <path d="M8.5 14.5s1.3 1.8 3.5 1.8 3.5-1.8 3.5-1.8" />
      <line x1="9" y1="9.5" x2="9.01" y2="9.5" />
      <line x1="15" y1="9.5" x2="15.01" y2="9.5" />
    </svg>
  );
}
