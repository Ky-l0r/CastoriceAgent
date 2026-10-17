import { useEffect, useRef, useState } from "react";
import { motion } from "motion/react";
import { ImagePlus, ArrowUp, Square } from "lucide-react";
import { useBridgeStore } from "../store/bridge-store";
import { bridgeActions } from "../lib/bridge-binding";
import { Avatar } from "./Avatar";

/**
 * 输入框：欢迎页居中（头像 + 欢迎语 + 大输入框）↔ 聊天态下沉为底部栏。
 * 状态条：style（占位）/ 模型（config active）/ thinking 档位（占位三档）。
 * vision 开启时显示图片附件按钮（本地文件路径直传 chat images 契约）。
 */

const GREETINGS: Record<string, string> = {
  morningEarly: "早啊，今天想做点什么？",
  morning: "上午好，有什么我可以帮忙的？",
  noon: "中午好，午饭吃了吗？",
  afternoon: "下午好，需要我做什么？",
  evening: "晚上好，聊聊天吧。",
  lateNight: "夜深了，还没睡呀？",
};

function greeting(): string {
  const h = new Date().getHours();
  if (h >= 5 && h < 9) return GREETINGS.morningEarly;
  if (h >= 9 && h < 12) return GREETINGS.morning;
  if (h >= 12 && h < 14) return GREETINGS.noon;
  if (h >= 14 && h < 18) return GREETINGS.afternoon;
  if (h >= 18 && h < 23) return GREETINGS.evening;
  return GREETINGS.lateNight;
}

const THINKING_LEVELS = ["低", "中", "高"];

/** 输入框下方的状态条（style / 模型 / thinking 档位）。 */
function StatusStrip() {
  const mode = useBridgeStore((s) => s.mode);
  const thinkingLevel = useBridgeStore((s) => s.thinkingLevel);
  const phase = useBridgeStore((s) => s.phase);
  const { setThinkingLevel } = bridgeActions();
  const [model, setModel] = useState<string>("…");

  // 连接就绪后拉一次模型名（active_provider + model 键）；失败显示占位。
  const fetchedRef = useRef("");
  useEffect(() => {
    if (phase === "ready" && fetchedRef.current !== "done") {
      fetchedRef.current = "done";
      void fetchModel(setModel);
    }
  }, [phase]);

  const chip =
    "flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-[11px] transition-colors";
  const chipStyle = {
    background: "var(--c-status-chip-bg)",
    borderColor: "var(--c-status-chip-border)",
    color: "var(--c-status-text)",
  };

  return (
    <div className="mt-2 flex flex-wrap items-center gap-1.5">
      <span className={chip} style={chipStyle} title="语气风格（占位，接样式采样后启用）">
        风格 · 默认
      </span>
      <span className={chip} style={chipStyle} title="当前模型（来自 config active_provider）">
        模型 · {model}
      </span>
      <span className={chip} style={chipStyle} title="当前模式">
        模式 · {mode}
      </span>
      <span className={chip} style={{ ...chipStyle, cursor: "pointer" }} title="thinking 档位（点击切换）">
        {THINKING_LEVELS.map((lv, i) => (
          <span key={lv}>
            <button
              type="button"
              className={lv === thinkingLevel ? "font-semibold" : "opacity-60"}
              style={{ color: "var(--c-status-text)" }}
              onClick={() => void setThinkingLevel(lv)}
            >
              {lv}
            </button>
            {i < THINKING_LEVELS.length - 1 && <span className="opacity-40"> / </span>}
          </span>
        ))}
      </span>
    </div>
  );
}

async function fetchModel(set: (v: string) => void) {
  try {
    const provider = (await window.castorice.invoke("config.get", {
      key: "active_provider",
    })) as { value?: unknown };
    const section = (await window.castorice.invoke("config.get", {
      key: "providers." + String(provider.value ?? ""),
    })) as { value?: Record<string, unknown> };
    const model = String(section.value?.model ?? "");
    if (model) set(model);
    else set("未知");
  } catch {
    set("离线");
  }
}

interface ComposerProps {
  /** docked=true：底部输入栏；false：欢迎页居中形态。 */
  docked: boolean;
}

export function Composer({ docked }: ComposerProps) {
  const busy = useBridgeStore((s) => s.busy);
  const vision = useBridgeStore((s) => s.vision);
  const phase = useBridgeStore((s) => s.phase);
  const [value, setValue] = useState("");
  const [images, setImages] = useState<string[]>([]);
  const fileRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const submit = () => {
    const text = value.trim();
    if (!text || busy || phase !== "ready") return;
    setValue("");
    setImages([]);
    void bridgeActions().chat(text, images.length ? images : undefined);
  };

  const pickImages = () => {
    fileRef.current?.click();
  };

  const shellStyle = {
    background: "var(--c-input-bg)",
    borderColor: "var(--c-input-border)",
    color: "var(--c-input-text)",
  };

  const inner = (
    <div className="rounded-2xl border px-4 py-3 transition-colors focus-within:border-[var(--c-input-border-focus)]" style={shellStyle}>
      {images.length > 0 && (
        <div className="mb-2 flex flex-wrap gap-1.5">
          {images.map((p, i) => (
            <span
              key={p + i}
              className="max-w-[220px] truncate rounded-md px-2 py-0.5 text-[11px]"
              style={{ background: "var(--c-tool-bg)", color: "var(--c-tool-text)" }}
              title={p}
            >
              {p.split(/[\\/]/).pop()}
            </span>
          ))}
        </div>
      )}
      <div className="flex items-end gap-2">
        <textarea
          ref={textareaRef}
          rows={docked ? 1 : 3}
          className="flex-1 resize-none bg-transparent text-sm outline-none placeholder:text-[var(--c-input-placeholder)]"
          style={{ color: "var(--c-input-text)" }}
          placeholder={busy ? "思考中…" : "给昔涟发消息…"}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              submit();
            }
          }}
        />
        {busy ? (
          <button
            type="button"
            title="停止"
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full transition-colors"
            style={{ background: "var(--c-send-bg)", color: "var(--c-highlight-text)" }}
            onClick={() => bridgeActions().interrupt()}
          >
            <Square size={14} />
          </button>
        ) : (
          <button
            type="button"
            title="发送"
            disabled={!value.trim() || phase !== "ready"}
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full transition-colors disabled:opacity-50"
            style={{
              background: value.trim() ? "var(--c-send-bg)" : "var(--c-send-bg-disabled)",
              color: "var(--c-highlight-text)",
            }}
            onClick={submit}
          >
            <ArrowUp size={16} />
          </button>
        )}
      </div>
      {docked && (
        <div className="mt-2 flex items-center gap-1">
          {vision && (
            <button
              type="button"
              title="发送图片（视觉模型）"
              className="flex h-7 w-7 items-center justify-center rounded-lg transition-colors hover:bg-[var(--c-nav-active-bg)]"
              style={{ color: "var(--c-muted)" }}
              onClick={pickImages}
            >
              <ImagePlus size={15} />
            </button>
          )}
        </div>
      )}
      <input
        ref={fileRef}
        type="file"
        accept="image/*"
        multiple
        className="hidden"
        onChange={(e) => {
          // Electron 的 File 对象带 path（本地绝对路径，chat images 契约直传）。
          const files = Array.from(e.currentTarget.files ?? []) as (File & { path?: string })[];
          if (files.length) setImages(files.map((f) => f.path || f.name));
          e.currentTarget.value = "";
        }}
      />
    </div>
  );

  if (!docked) {
    // 欢迎页居中：头像 + 欢迎语 + 大输入框 + 状态条
    return (
      <motion.div
        layout
        className="flex flex-1 flex-col items-center justify-center px-6 pb-16"
        transition={{ type: "spring", stiffness: 170, damping: 22 }}
      >
        <Avatar size={84} />
        <p className="mb-6 mt-4 text-lg font-medium" style={{ color: "var(--c-bubble-text)" }}>
          {greeting()}
        </p>
        <motion.div layout className="w-full max-w-2xl">
          {inner}
          <StatusStrip />
        </motion.div>
      </motion.div>
    );
  }

  // 聊天态：底部输入栏
  return (
    <motion.div
      layout
      className="shrink-0 px-6 pb-4"
      transition={{ type: "spring", stiffness: 170, damping: 22 }}
    >
      <div className="mx-auto max-w-3xl">
        {inner}
        <StatusStrip />
      </div>
    </motion.div>
  );
}
