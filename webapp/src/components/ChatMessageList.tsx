import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { ChevronDown, ChevronRight, Wrench, AlertCircle } from "lucide-react";
import { useBridgeStore, type TranscriptEntry } from "../store/bridge-store";
import { Avatar } from "./Avatar";

/**
 * 聊天流：气泡布局（用户右侧 / AI 左侧带头像，观感对齐一期截图）。
 * - text：打字机（streaming 条目逐字显现，motion 驱动）；
 * - tool：折叠条（start/done/error 状态 + call_id）；
 * - emoji：贴图显示（Image/表情包 本地路径 -> asset 协议不可达时显示标签占位）。
 */

/** 本地图片路径 -> 可加载 URL 的映射（Vite 静态目录 /castorice-assets）。 */
function localImageUrl(path: string): string {
  const normalized = path.replace(/\\/g, "/");
  const marker = "/Image/";
  const idx = normalized.lastIndexOf(marker);
  if (idx >= 0) {
    return "/castorice-assets" + normalized.slice(idx + marker.length - 1);
  }
  return "";
}

function TypewriterText({ text, streaming }: { text: string; streaming?: boolean }) {
  const [shown, setShown] = useState(streaming ? "" : text);

  useEffect(() => {
    if (!streaming) {
      setShown(text);
      return;
    }
    // 打字机：目标文本增长时按速率追赶（每帧 3 字符），流结束整段落定。
    let raf = 0;
    const tick = () => {
      setShown((cur) => {
        if (cur.length >= text.length) return text;
        return text.slice(0, Math.min(text.length, cur.length + 3));
      });
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [text, streaming]);

  const typing = streaming && shown.length < text.length;
  return (
    <span className={typing ? "typing-cursor" : undefined} style={{ whiteSpace: "pre-wrap" }}>
      {shown}
    </span>
  );
}

function ToolEntry({ entry }: { entry: TranscriptEntry }) {
  const [open, setOpen] = useState(false);
  const status = String(entry.meta?.status ?? "start");
  const statusLabel =
    status === "done" ? "完成" : status === "error" ? "出错" : "执行中";
  const statusColor =
    status === "error"
      ? "text-rose-400"
      : status === "done"
        ? "text-emerald-400"
        : "text-amber-300";

  return (
    <motion.div
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      className="max-w-[85%] rounded-xl border text-xs"
      style={{
        background: "var(--c-tool-bg)",
        borderColor: "var(--c-input-border)",
        color: "var(--c-tool-text)",
      }}
    >
      <button
        type="button"
        className="flex w-full items-center gap-2 px-3 py-2 text-left"
        onClick={() => setOpen((v) => !v)}
      >
        {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
        <Wrench size={13} />
        <span className="font-medium">{entry.content}</span>
        <span className={statusColor}>· {statusLabel}</span>
      </button>
      {open && (
        <pre
          className="max-h-40 overflow-auto border-t px-3 py-2 text-[11px] leading-relaxed"
          style={{ borderColor: "var(--c-input-border)" }}
        >
          {JSON.stringify(entry.meta ?? {}, null, 2)}
        </pre>
      )}
    </motion.div>
  );
}

function EmojiEntry({ entry }: { entry: TranscriptEntry }) {
  const path = String(entry.meta?.path ?? "");
  const url = path ? localImageUrl(path) : "";
  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.92 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ type: "spring", stiffness: 260, damping: 20 }}
      className="max-w-[180px] overflow-hidden rounded-xl border"
      style={{ background: "var(--c-ai-bubble-bg)", borderColor: "var(--c-bubble-border)" }}
    >
      {url ? (
        <img src={url} alt={entry.content} className="block h-auto w-full" draggable={false} />
      ) : (
        <div className="px-3 py-2 text-sm" style={{ color: "var(--c-tool-text)" }}>
          [表情：{entry.content}]
        </div>
      )}
    </motion.div>
  );
}

export function ChatMessageList() {
  const entries = useBridgeStore((s) => s.entries);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [entries]);

  return (
    <div className="flex-1 overflow-y-auto px-6 py-4">
      <div className="mx-auto flex max-w-3xl flex-col gap-4">
        <AnimatePresence initial={false}>
          {entries.map((entry) => {
            if (entry.role === "user") {
              return (
                <motion.div
                  key={entry.id}
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  className="flex justify-end"
                >
                  <div
                    className="max-w-[70%] rounded-2xl rounded-br-md px-4 py-2.5 text-sm leading-relaxed"
                    style={{
                      background: "var(--c-user-bubble-bg)",
                      color: "var(--c-bubble-text)",
                    }}
                  >
                    {entry.content}
                  </div>
                </motion.div>
              );
            }
            // assistant：左侧带头像
            return (
              <motion.div
                key={entry.id}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                className="flex items-start gap-2.5"
              >
                <Avatar size={34} />
                {entry.kind === "text" && (
                  <div
                    className="max-w-[70%] rounded-2xl rounded-bl-md px-4 py-2.5 text-sm leading-relaxed"
                    style={{
                      background: "var(--c-ai-bubble-bg)",
                      color: "var(--c-bubble-text)",
                      border: "1px solid var(--c-bubble-border)",
                    }}
                  >
                    <TypewriterText text={entry.content} streaming={entry.streaming} />
                    {entry.interrupted && (
                      <span
                        className="mt-1 flex items-center gap-1 text-xs"
                        style={{ color: "var(--c-muted)" }}
                      >
                        <AlertCircle size={12} /> 已打断
                      </span>
                    )}
                  </div>
                )}
                {entry.kind === "tool" && <ToolEntry entry={entry} />}
                {entry.kind === "emoji" && <EmojiEntry entry={entry} />}
              </motion.div>
            );
          })}
        </AnimatePresence>
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
