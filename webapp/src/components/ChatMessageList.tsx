import { useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import {
  ChevronDown,
  ChevronRight,
  Wrench,
  AlertCircle,
  Copy,
  Check,
} from "lucide-react";
import { useBridgeStore, type TranscriptEntry } from "../store/bridge-store";
import { Avatar } from "./Avatar";
import { stickerUrl } from "../lib/assets";

/**
 * 聊天流：气泡布局（用户右侧 / AI 左侧带头像）。
 *
 * 布局要点（修复「输入框上方出现空白」的观感问题）：
 * 外层必须是 min-h-0 的 flex 列，滚动容器才能正确吸收剩余空间；
 * 缺 min-h-0 时 flex 子项不会收缩，内容被挤到底部、顶部出现空白。
 */

/** 极简 Markdown：只处理围栏代码块、行内代码、**粗体**、*斜体*。 */
function renderRich(text: string): React.ReactNode[] {
  const blocks = text.split(/```/);
  const nodes: React.ReactNode[] = [];
  blocks.forEach((block, bi) => {
    if (bi % 2 === 1) {
      const nl = block.indexOf("\n");
      const body = nl >= 0 ? block.slice(nl + 1) : block;
      nodes.push(
        <pre key={"p" + bi} className="msg-pre">
          <code>{body.replace(/\n$/, "")}</code>
        </pre>,
      );
      return;
    }
    block.split("\n").forEach((line, li) => {
      const parts = line.split(/(`[^`]+`|\*\*[^*]+\*\*)/g).filter(Boolean);
      nodes.push(
        <span key={bi + "-" + li}>
          {parts.map((p, pi) => {
            if (p.startsWith("`") && p.endsWith("`") && p.length > 2) {
              return (
                <code key={pi} className="msg-code">
                  {p.slice(1, -1)}
                </code>
              );
            }
            if (p.startsWith("**") && p.endsWith("**") && p.length > 4) {
              return <strong key={pi}>{p.slice(2, -2)}</strong>;
            }
            return <span key={pi}>{p}</span>;
          })}
          {li < block.split("\n").length - 1 && <br />}
        </span>,
      );
    });
  });
  return nodes;
}

function TypewriterText({ text, streaming }: { text: string; streaming?: boolean }) {
  const [shown, setShown] = useState(streaming ? "" : text);

  useEffect(() => {
    if (!streaming) {
      setShown(text);
      return;
    }
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
  if (!typing) {
    return <span style={{ whiteSpace: "pre-wrap" }}>{renderRich(shown)}</span>;
  }
  return (
    <span className="typing-cursor" style={{ whiteSpace: "pre-wrap" }}>
      {shown}
    </span>
  );
}

/** 复制按钮（悬停显示）。 */
function CopyButton({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      title={done ? "已复制" : "复制"}
      className="flex h-6 w-6 items-center justify-center rounded-md opacity-0 transition-opacity hover:bg-[var(--c-nav-active-bg)] group-hover:opacity-100"
      style={{ color: "var(--c-muted)" }}
      onClick={() => {
        void navigator.clipboard?.writeText(text).then(() => {
          setDone(true);
          setTimeout(() => setDone(false), 1400);
        });
      }}
    >
      {done ? <Check size={12} /> : <Copy size={12} />}
    </button>
  );
}

function ToolEntry({ entry }: { entry: TranscriptEntry }) {
  const [open, setOpen] = useState(false);
  const status = String(entry.meta?.status ?? "start");
  const statusLabel =
    status === "done" ? "完成" : status === "error" ? "出错" : "执行中";
  const statusColor =
    status === "error"
      ? "text-[var(--c-danger)]"
      : status === "done"
        ? "text-[var(--c-success)]"
        : "text-[var(--c-warning)]";

  return (
    <motion.div
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      className="max-w-[85%] overflow-hidden text-xs"
      style={{
        background: "var(--c-tool-bg)",
        border: "1px solid var(--c-divider)",
        borderRadius: "var(--r-sm)",
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
        <span className="truncate font-medium">{entry.content}</span>
        <span className={"shrink-0 whitespace-nowrap " + statusColor}>· {statusLabel}</span>
      </button>
      {open && (
        <pre
          className="max-h-40 overflow-auto border-t px-3 py-2 text-[11px] leading-relaxed"
          style={{ borderColor: "var(--c-divider)" }}
        >
          {JSON.stringify(entry.meta ?? {}, null, 2)}
        </pre>
      )}
    </motion.div>
  );
}

/** 表情包：只取文件名拼内置贴纸目录（不依赖宿主机路径）。 */
function EmojiEntry({ entry }: { entry: TranscriptEntry }) {
  const [failed, setFailed] = useState(false);
  const path = String(entry.meta?.path ?? "");
  const url = useMemo(() => stickerUrl(path), [path]);

  if (!url || failed) {
    return (
      <div
        className="px-3 py-2 text-sm"
        style={{
          background: "var(--c-ai-bubble-bg)",
          color: "var(--c-tool-text)",
          borderRadius: "var(--r-md)",
          border: "1px solid var(--c-divider)",
        }}
      >
        [表情：{entry.content}]
      </div>
    );
  }

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.92 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ type: "spring", stiffness: 260, damping: 20 }}
      className="max-w-[168px] overflow-hidden"
      style={{ borderRadius: "var(--r-md)" }}
    >
      <img
        src={url}
        alt={entry.content}
        className="block h-auto w-full select-none"
        draggable={false}
        onError={() => setFailed(true)}
      />
    </motion.div>
  );
}

/** 空状态：欢迎页（无消息时由 Composer 独占，这里只处理“有会话但为空”）。 */
function EmptyChat() {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-2">
      <p className="text-sm" style={{ color: "var(--c-muted)" }}>
        还没有消息，先聊点什么吧
      </p>
    </div>
  );
}

export function ChatMessageList() {
  const entries = useBridgeStore((s) => s.entries);
  const thinkingLevel = useBridgeStore((s) => s.thinkingLevel);
  const bottomRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [entries]);

  if (entries.length === 0) return <EmptyChat />;

  return (
    <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
      <div className="mx-auto flex w-full max-w-3xl flex-col msg-gap">
        <AnimatePresence initial={false}>
          {entries.map((entry) => {
            if (entry.role === "user") {
              return (
                <motion.div
                  key={entry.id}
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  className="group flex justify-end"
                >
                  <div className="flex items-end gap-1">
                    <div className="opacity-0 transition-opacity group-hover:opacity-100">
                      <CopyButton text={entry.content} />
                    </div>
                    <div
                      className="msg-bubble max-w-[70%] px-4 py-2.5 text-sm"
                      style={{
                        background: "var(--c-user-bubble-bg)",
                        color: "var(--c-bubble-text)",
                      }}
                    >
                      <span style={{ whiteSpace: "pre-wrap" }}>{entry.content}</span>
                    </div>
                  </div>
                </motion.div>
              );
            }
            return (
              <motion.div
                key={entry.id}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                className="group flex items-start gap-2.5"
              >
                <Avatar size={32} />
                <div className="flex min-w-0 flex-col gap-1">
                  {entry.kind === "text" && (
                    <>
                      <div
                        className="msg-bubble max-w-[min(70%,640px)] px-4 py-2.5 text-sm"
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
                      {!entry.streaming && (
                        <div className="flex items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100">
                          <CopyButton text={entry.content} />
                          <span className="text-[11px]" style={{ color: "var(--c-muted)" }}>
                            {thinkingLevel && entry.content.length > 0 ? "" : ""}
                          </span>
                        </div>
                      )}
                    </>
                  )}
                  {entry.kind === "tool" && <ToolEntry entry={entry} />}
                  {entry.kind === "emoji" && <EmojiEntry entry={entry} />}
                </div>
              </motion.div>
            );
          })}
        </AnimatePresence>
        <div ref={bottomRef} className="h-1" />
      </div>
    </div>
  );
}
