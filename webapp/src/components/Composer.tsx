import { useEffect, useRef, useState } from "react";
import { ImagePlus, ArrowUp, Square, ChevronDown } from "lucide-react";
import { useBridgeStore } from "../store/bridge-store";
import { bridgeActions } from "../lib/bridge-binding";
import { Avatar } from "./Avatar";
import { WELCOME_CHIBI_URL } from "../lib/assets";
import { Popover, PopoverItem } from "./ui/Popover";
import { StickerPickerButton } from "./ui/StickerPicker";

/**
 * 输入框：欢迎页居中（头像 + 欢迎语 + 大输入框）↔ 聊天态下沉为底部栏。
 *
 * 对齐问题的根因（第四期修复）：原来外层用 items-end 让 36px 的发送按钮
 * 与单行 textarea 底边对齐，textarea 只有一行高时，按钮把整行撑到 36px，
 * 文字被推到下方，视觉上就是「文字上方多了一块空白」。
 * 现在改为 items-center，并把发送按钮缩到 30px 与单行文字视觉居中。
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

const THINKING_LEVELS = [
  { value: "低", hint: "更快" },
  { value: "中", hint: "平衡" },
  { value: "高", hint: "更深入" },
];

const STYLE_PRESETS = [
  { value: "默认", hint: "自然" },
  { value: "温柔", hint: "耐心体贴" },
  { value: "简洁", hint: "少废话" },
  { value: "活泼", hint: "俏皮" },
  { value: "学术", hint: "严谨" },
];

/** 输入框下方的状态条：风格 / 模型 / 模式 / 推理等级（四个都可点）。 */
function StatusStrip() {
  const mode = useBridgeStore((s) => s.mode);
  const thinkingLevel = useBridgeStore((s) => s.thinkingLevel);
  const style = useBridgeStore((s) => s.style);
  const phase = useBridgeStore((s) => s.phase);
  const [model, setModel] = useState<string>("…");
  const [providers, setProviders] = useState<string[]>([]);
  const [activeProvider, setActiveProvider] = useState<string>("");

  const fetchedRef = useRef("");
  useEffect(() => {
    if (phase === "ready" && fetchedRef.current !== "done") {
      fetchedRef.current = "done";
      void fetchModelInfo(setModel, setProviders, setActiveProvider);
    }
  }, [phase]);

  const chip =
    "flex items-center gap-1 rounded-full border px-2.5 py-[3px] text-[11px] transition-colors hover:brightness-110";
  const chipStyle = {
    background: "var(--c-status-chip-bg)",
    borderColor: "var(--c-status-chip-border)",
    color: "var(--c-status-text)",
  };

  return (
    <div className="mt-2 flex flex-wrap items-center gap-1.5">
      {/* 风格 */}
      <Popover
        side="top"
        width={160}
        trigger={({ toggle }) => (
          <button type="button" className={chip} style={chipStyle} onClick={toggle} title="语气风格">
            风格 · {style}
            <ChevronDown size={10} className="opacity-60" />
          </button>
        )}
      >
        {({ close }) => (
          <>
            {STYLE_PRESETS.map((p) => (
              <PopoverItem
                key={p.value}
                label={p.value}
                hint={p.hint}
                active={p.value === style}
                onClick={() => {
                  void bridgeActions().setStyle(p.value);
                  close();
                }}
              />
            ))}
          </>
        )}
      </Popover>

      {/* 模型：列出已配置的提供商，点选即切换 */}
      <Popover
        side="top"
        width={200}
        trigger={({ toggle }) => (
          <button type="button" className={chip} style={chipStyle} onClick={toggle} title="切换模型提供商">
            模型 · {model}
            <ChevronDown size={10} className="opacity-60" />
          </button>
        )}
      >
        {({ close }) => (
          <>
            {providers.length === 0 && (
              <div className="px-2.5 py-1.5 text-[12px]" style={{ color: "var(--c-muted)" }}>
                未配置提供商
              </div>
            )}
            {providers.map((name) => (
              <PopoverItem
                key={name}
                label={name}
                active={name === activeProvider}
                onClick={() => {
                  void switchProvider(name, setActiveProvider, setModel);
                  close();
                }}
              />
            ))}
          </>
        )}
      </Popover>

      {/* 模式 */}
      <span className={chip} style={chipStyle} title="当前模式（在顶部切换）">
        模式 · {modeLabel(mode)}
      </span>

      {/* 推理等级：三档内联选择 */}
      <span className={chip} style={{ ...chipStyle, cursor: "pointer" }} title="推理投入档位">
        {THINKING_LEVELS.map((lv, i) => (
          <span key={lv.value}>
            <button
              type="button"
              className="transition-opacity"
              style={{
                color: "var(--c-status-text)",
                fontWeight: lv.value === thinkingLevel ? 700 : 400,
                opacity: lv.value === thinkingLevel ? 1 : 0.55,
              }}
              onClick={() => void bridgeActions().setThinkingLevel(lv.value)}
            >
              {lv.value}
            </button>
            {i < THINKING_LEVELS.length - 1 && <span className="opacity-40"> / </span>}
          </span>
        ))}
      </span>
    </div>
  );
}

function modeLabel(mode: string): string {
  return ({ chat: "聊天", work: "工作", code: "编程", learn: "学习" } as Record<string, string>)[mode] ?? mode;
}

async function fetchModelInfo(
  setModel: (v: string) => void,
  setProviders: (v: string[]) => void,
  setActive: (v: string) => void,
) {
  try {
    const active = (await window.castorice.invoke("config.get", { key: "active_provider" })) as {
      value?: unknown;
    };
    const activeName = String(active.value ?? "");
    setActive(activeName);

    // config.get 的 providers 节拿不到全量清单，用 config.get("providers") 尝试；
    // 失败时至少把 active_provider 作为唯一条目（不阻塞切换入口）。
    let names: string[] = activeName ? [activeName] : [];
    try {
      const all = (await window.castorice.invoke("config.get", { key: "providers" })) as {
        value?: Record<string, unknown>;
      };
      if (all.value && typeof all.value === "object") {
        names = Object.keys(all.value);
      }
    } catch {
      /* 退化为单条 */
    }
    setProviders(names);

    if (activeName) {
      const section = (await window.castorice.invoke("config.get", {
        key: "providers." + activeName,
      })) as { value?: Record<string, unknown> };
      const model = String(section.value?.model ?? "");
      setModel(model || "未知");
    } else {
      setModel("未配置");
    }
  } catch {
    setModel("离线");
  }
}

async function switchProvider(
  name: string,
  setActive: (v: string) => void,
  setModel: (v: string) => void,
) {
  try {
    await window.castorice.invoke("config.set", {
      updates: { active_provider: name },
    });
    setActive(name);
    const section = (await window.castorice.invoke("config.get", {
      key: "providers." + name,
    })) as { value?: Record<string, unknown> };
    setModel(String(section.value?.model ?? "未知"));
  } catch {
    /* 静默：保持原值 */
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

  // 自适应高度：随内容增长，上限 200px（超出内部滚动）。
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 200) + "px";
  }, [value]);

  const submit = () => {
    const text = value.trim();
    const hasImages = images.length > 0;
    if ((!text && !hasImages) || busy || phase !== "ready") return;
    setValue("");
    setImages([]);
    void bridgeActions().chat(text, hasImages ? images : undefined);
  };

  /**
   * 插入表情包：贴纸名以 [表情:xx] 标记进正文（模型能读到语义），
   * 同时不当作图片附件——表情包的图片由后端在回复侧匹配，用户侧
   * 只需要让模型知道发了哪个表情即可（避免 vision 未开时报错）。
   */
  const insertSticker = (label: string) => {
    setValue((v) => (v ? v + " [表情:" + label + "]" : "[表情:" + label + "]"));
    textareaRef.current?.focus();
  };

  const shellStyle = {
    background: "var(--c-input-bg)",
    borderColor: "var(--c-input-border)",
    color: "var(--c-input-text)",
    borderRadius: "var(--r-xl)",
  };

  const iconBtn =
    "flex h-7 w-7 shrink-0 items-center justify-center rounded-lg transition-colors hover:bg-[var(--c-nav-active-bg)]";

  const inner = (
    <div
      className="border px-3 py-2.5 transition-colors focus-within:border-[var(--c-input-border-focus)]"
      style={shellStyle}
    >
      {images.length > 0 && (
        <div className="mb-2 flex flex-wrap gap-1.5">
          {images.map((p, i) => (
            <span
              key={p + i}
              className="flex max-w-[220px] items-center gap-1 rounded-[var(--r-sm)] px-2 py-1 text-[11px]"
              style={{ background: "var(--c-tool-bg)", color: "var(--c-tool-text)" }}
              title={p}
            >
              <span className="truncate">{p.split(/[\\/]/).pop()}</span>
              <button
                type="button"
                className="shrink-0 opacity-60 hover:opacity-100"
                title="移除"
                onClick={() => setImages((prev) => prev.filter((_, idx) => idx !== i))}
              >
                ×
              </button>
            </span>
          ))}
        </div>
      )}

      <textarea
        ref={textareaRef}
        rows={1}
        className="w-full flex-1 resize-none bg-transparent text-[13.5px] leading-6 outline-none placeholder:text-[var(--c-input-placeholder)]"
        style={{ color: "var(--c-input-text)", maxHeight: 200 }}
        placeholder={busy ? "遐蝶正在思考…" : "给遐蝶发消息…"}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            submit();
          }
        }}
      />

      {/* 工具行与发送按钮在同一行：底部对齐，避免单行时上方留白 */}
      <div className="mt-1 flex items-center gap-1">
        {vision && (
          <button
            type="button"
            title="发送图片（视觉模型）"
            className={iconBtn}
            style={{ color: "var(--c-muted)" }}
            onClick={() => fileRef.current?.click()}
          >
            <ImagePlus size={15} />
          </button>
        )}
        <StickerPickerButton onPick={insertSticker} />

        <span className="flex-1" />

        <span className="mr-1 text-[10.5px]" style={{ color: "var(--c-muted)" }}>
          Enter 发送 · Shift+Enter 换行
        </span>

        {busy ? (
          <button
            type="button"
            title="停止"
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full transition-colors"
            style={{ background: "var(--c-send-bg)", color: "var(--c-highlight-text)" }}
            onClick={() => bridgeActions().interrupt()}
          >
            <Square size={12} />
          </button>
        ) : (
          <button
            type="button"
            title="发送"
            disabled={(!value.trim() && images.length === 0) || phase !== "ready"}
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full transition-colors disabled:opacity-40"
            style={{
              background:
                value.trim() || images.length > 0
                  ? "var(--c-send-bg)"
                  : "var(--c-send-bg-disabled)",
              color: "var(--c-highlight-text)",
            }}
            onClick={submit}
          >
            <ArrowUp size={15} />
          </button>
        )}
      </div>

      <input
        ref={fileRef}
        type="file"
        accept="image/*"
        multiple
        className="hidden"
        onChange={(e) => {
          // Electron 的 File 对象带 path（本地绝对路径，chat images 契约直传）。
          const files = Array.from(e.currentTarget.files ?? []) as (File & { path?: string })[];
          if (files.length) setImages((prev) => [...prev, ...files.map((f) => f.path || f.name)]);
          e.currentTarget.value = "";
        }}
      />
    </div>
  );

  if (!docked) {
    // 欢迎页居中：头像 + 欢迎语 + 大输入框 + 状态条
    return (
      <div className="flex h-full flex-col items-center justify-center px-6 pb-10">
        <Avatar size={76} />
        <div className="mb-5 mt-3 flex items-end gap-1">
          <p className="text-[17px] font-medium" style={{ color: "var(--c-bubble-text)" }}>
            {greeting()}
          </p>
          <img
            src={WELCOME_CHIBI_URL}
            alt=""
            draggable={false}
            className="chibi-art h-[72px] w-auto select-none"
          />
        </div>
        <div className="w-full max-w-2xl">
          {inner}
          <StatusStrip />
        </div>
      </div>
    );
  }

  return (
    <div className="shrink-0 px-6 pb-4 pt-1">
      <div className="mx-auto w-full max-w-3xl">
        {inner}
        <StatusStrip />
      </div>
    </div>
  );
}
