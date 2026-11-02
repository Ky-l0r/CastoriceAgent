import { memo, useEffect, useMemo, useRef, useState } from "react";
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
import { splitStickers } from "../lib/stickers";

/**
 * 聊天流：气泡布局（用户右侧 / AI 左侧带头像）。
 *
 * 布局要点（修复「输入框上方出现空白」的观感问题）：
 * 外层必须是 min-h-0 的 flex 列，滚动容器才能正确吸收剩余空间；
 * 缺 min-h-0 时 flex 子项不会收缩，内容被挤到底部、顶部出现空白。
 *
 * 只允许纵向滚动：滚动容器锁 overflow-x-hidden，气泡内文本走
 * break-words / whitespace-pre-wrap。此前长内容会把内容区撑宽，底部就
 * 出现一条用不到的横向滚动条（用户反馈的「左右拉的条子」）。
 *
 * 性能要点（第五期「打开时卡卡的」修复）：
 * 1. 每条消息都是 memo 化的独立组件——流式输出时只有正在追加的那一条
 *    重渲染，而不是整列几百条一起重算；
 * 2. 去掉了 motion 的 AnimatePresence 包列表（每条一个布局动画实例，
 *    条目一多就是明显的首帧卡顿），入场动画交给 CSS；
 * 3. 滚动改为「直接设置 scrollTop」：原来每个流式分片都触发一次
 *    scrollIntoView({behavior:"smooth"})，几十毫秒一次平滑滚动是持续
 *    掉帧的主要来源。
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
  const rich = useMemo(() => renderRich(shown), [shown]);
  if (!typing) {
    return <span style={{ whiteSpace: "pre-wrap" }}>{rich}</span>;
  }
  return (
    <span className="typing-cursor" style={{ whiteSpace: "pre-wrap" }}>
      {shown}
    </span>
  );
}

/** 复制按钮（悬停显示）。 */
const CopyButton = memo(function CopyButton({ text }: { text: string }) {
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
});

/**
 * 表情包图片块（用户与 AI 共用）。
 *
 * 用户消息里的贴纸来自输入框的 `[表情:名]` 标记，AI 的贴纸来自后端
 * emoji 事件（带图片路径）。两者最终都映射到内置贴纸目录里的同名图片，
 * 因此显示层只认「名字」，不依赖宿主机路径。
 */
function StickerImage({
  name,
  size = 132,
  onDarkBubble,
}: {
  name: string;
  size?: number;
  onDarkBubble?: boolean;
}) {
  const [failed, setFailed] = useState(false);
  const url = useMemo(() => stickerUrl(name.endsWith(".png") ? name : name + ".png"), [name]);

  if (!url || failed) {
    return (
      <span
        className="inline-flex items-center rounded-[var(--r-sm)] px-2 py-1 text-[12px]"
        style={{
          background: onDarkBubble ? "rgba(255,255,255,0.10)" : "var(--c-tool-bg)",
          color: onDarkBubble ? "var(--c-bubble-text)" : "var(--c-tool-text)",
        }}
      >
        [表情：{name.replace(/\.png$/i, "")}]
      </span>
    );
  }

  return (
    <img
      src={url}
      alt={name}
      title={name.replace(/\.png$/i, "")}
      className="ui-pop-in block w-auto select-none rounded-[var(--r-sm)]"
      style={{ maxWidth: size, maxHeight: size }}
      draggable={false}
      loading="lazy"
      onError={() => setFailed(true)}
    />
  );
}

/**
 * 用户气泡内容：正文 + 贴纸。
 *
 * 历史实现把整段 `[表情:喜欢]` 当普通文字渲染，用户在自己的气泡里只能
 * 看到一句文字描述、看不到图（用户反馈的第一个问题）。这里把标记拆出来，
 * 贴纸按图片渲染，正文剥掉标记后正常显示。
 */
function UserBubbleContent({ text }: { text: string }) {
  const { text: plain, stickers } = useMemo(() => splitStickers(text), [text]);
  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      {plain && (
        <span className="break-words" style={{ whiteSpace: "pre-wrap" }}>
          {plain}
        </span>
      )}
      {stickers.length > 0 && (
        <span className="flex flex-wrap gap-1.5">
          {stickers.map((name, i) => (
            <StickerImage key={name + i} name={name} size={148} onDarkBubble />
          ))}
        </span>
      )}
    </div>
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
    <div
      className="w-fit max-w-full overflow-hidden text-xs"
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
    </div>
  );
}

/** 表情包（AI 侧）：只取文件名拼内置贴纸目录（不依赖宿主机路径）。 */
function EmojiEntry({ entry }: { entry: TranscriptEntry }) {
  const path = String(entry.meta?.path ?? "");
  const name = String(entry.meta?.name ?? entry.content ?? "");
  // 后端给了绝对路径时优先用文件名（与内置贴纸目录同名）；没有则用标签名。
  const fileName = path ? (path.split(/[\\/]/).pop() || name) : name;
  return (
    <div className="w-fit max-w-[200px] overflow-hidden" style={{ borderRadius: "var(--r-md)" }}>
      <StickerImage name={fileName || name} size={176} />
    </div>
  );
}

/** 单条消息（memo 化：流式追加时只有最后一条会重新渲染）。 */
const MessageRow = memo(function MessageRow({ entry }: { entry: TranscriptEntry }) {
  if (entry.role === "user") {
    return (
      <div className="ui-fade-in group flex w-full min-w-0 justify-end">
        {/* 这一层必须能收缩（min-w-0），否则长文本会把复制按钮挤出视口 */}
        <div className="flex min-w-0 max-w-[min(78%,640px)] items-end gap-1">
          <div className="shrink-0 opacity-0 transition-opacity group-hover:opacity-100">
            <CopyButton text={entry.content} />
          </div>
          <div
            className="msg-bubble min-w-0 break-words px-4 py-2.5 text-sm"
            style={{
              background: "var(--c-user-bubble-bg)",
              color: "var(--c-bubble-text)",
            }}
          >
            <UserBubbleContent text={entry.content} />
          </div>
        </div>
      </div>
    );
  }
  return (
    <div className="ui-fade-in group flex min-w-0 items-start gap-2.5">
      <Avatar size={32} />
      {/* flex-1 是关键：缺了它这一列是 fit-content，内部元素的
          max-w-[xx%] 会与父级宽度互相依赖而塌缩成很窄的一条
          （工具条被压成「rea… / get_curr…」就是这么来的）。 */}
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        {entry.kind === "text" && (
          <>
            <div
              className="msg-bubble max-w-[min(78%,680px)] break-words px-4 py-2.5 text-sm"
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
              </div>
            )}
          </>
        )}
        {entry.kind === "tool" && <ToolEntry entry={entry} />}
        {entry.kind === "emoji" && <EmojiEntry entry={entry} />}
      </div>
    </div>
  );
});

/** 空状态：欢迎页（无消息时由 Composer 独占，这里只处理「有会话但为空」）。 */
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
  const scrollRef = useRef<HTMLDivElement>(null);
  /** 用户是否停在底部附近（向上翻历史时不要被流式输出拽回来）。 */
  const stickToBottom = useRef(true);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const onScroll = () => {
      const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
      stickToBottom.current = distance < 80;
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, []);

  // 滚动跟随：直接写 scrollTop（不做平滑动画）。
  // 原来每个流式分片都调一次 scrollIntoView({behavior:"smooth"})，浏览器
  // 会把上一次平滑滚动打断重来，表现为持续掉帧 + 页面「发抖」。
  useEffect(() => {
    const el = scrollRef.current;
    if (!el || !stickToBottom.current) return;
    el.scrollTop = el.scrollHeight;
  }, [entries]);

  if (entries.length === 0) return <EmptyChat />;

  return (
    <div
      ref={scrollRef}
      className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden px-8 py-5"
    >
      {/* 不设 max-width：最大化窗口时气泡分别贴住左右边缘（用户要求）。
          可读性由气泡自身的宽度上限保证，而不是靠压缩整个内容区。 */}
      <div className="flex w-full min-w-0 flex-col msg-gap">
        {entries.map((entry) => (
          <MessageRow key={entry.id} entry={entry} />
        ))}
      </div>
    </div>
  );
}
