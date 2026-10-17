/**
 * 设置中心通用控件（t4）：基于 radix-ui 的受控包装，统一观感与键盘语义。
 * 颜色一律走 var(--c-*)（global.css 主题变量），组件自身不感知主题。
 */
import { Slider as RSlider, Switch as RSwitch } from "radix-ui";
import type { ReactNode } from "react";

/** 一行设置项：标题 + 描述 + 右侧控件区。 */
export function SettingRow({
  title,
  hint,
  children,
}: {
  title: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <div className="flex items-center justify-between gap-6 py-3">
      <div className="min-w-0">
        <div className="text-sm font-medium" style={{ color: "var(--c-bubble-text)" }}>
          {title}
        </div>
        {hint && (
          <div className="mt-0.5 text-xs" style={{ color: "var(--c-muted)" }}>
            {hint}
          </div>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-3">{children}</div>
    </div>
  );
}

/** 分组卡片标题。 */
export function SectionTitle({ text }: { text: string }) {
  return (
    <h3
      className="mb-1 mt-6 text-xs font-semibold uppercase tracking-wider first:mt-0"
      style={{ color: "var(--c-muted)" }}
    >
      {text}
    </h3>
  );
}

/** 数值滑块行（radix Slider）：标签 + 滑块 + 实时数值。 */
export function SliderRow({
  title,
  hint,
  value,
  min,
  max,
  step,
  format,
  onChange,
}: {
  title: string;
  hint?: string;
  value: number;
  min: number;
  max: number;
  step: number;
  format?: (v: number) => string;
  onChange: (v: number) => void;
}) {
  return (
    <div className="py-3">
      <div className="flex items-center justify-between gap-6">
        <div className="min-w-0">
          <div className="text-sm font-medium" style={{ color: "var(--c-bubble-text)" }}>
            {title}
          </div>
          {hint && (
            <div className="mt-0.5 text-xs" style={{ color: "var(--c-muted)" }}>
              {hint}
            </div>
          )}
        </div>
        <span
          className="w-14 shrink-0 text-right text-sm tabular-nums"
          style={{ color: "var(--c-highlight)" }}
        >
          {format ? format(value) : value}
        </span>
      </div>
      <RSlider.Root
        className="relative mt-3 flex h-5 w-full touch-none select-none items-center"
        value={[value]}
        min={min}
        max={max}
        step={step}
        aria-label={title}
        onValueChange={(values) => onChange(values[0] ?? value)}
      >
        <RSlider.Track
          className="relative h-1.5 w-full grow rounded-full"
          style={{ background: "var(--c-input-border)" }}
        >
          <RSlider.Range
            className="absolute h-full rounded-full"
            style={{ background: "var(--c-highlight)" }}
          />
        </RSlider.Track>
        <RSlider.Thumb
          className="block h-4 w-4 rounded-full border-2 shadow-sm outline-none transition-transform hover:scale-110 focus-visible:ring-2"
          style={{
            background: "var(--c-highlight-text)",
            borderColor: "var(--c-highlight)",
          }}
          aria-label={title}
        />
      </RSlider.Root>
    </div>
  );
}

/** 开关行（radix Switch）。 */
export function SwitchRow({
  title,
  hint,
  checked,
  onChange,
}: {
  title: string;
  hint?: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <SettingRow title={title} hint={hint}>
      <RSwitch.Root
        className="relative h-6 w-11 shrink-0 rounded-full outline-none transition-colors focus-visible:ring-2"
        style={{
          background: checked ? "var(--c-highlight)" : "var(--c-input-border)",
        }}
        checked={checked}
        onCheckedChange={onChange}
        aria-label={title}
      >
        <RSwitch.Thumb className="block h-5 w-5 translate-x-0.5 rounded-full bg-white shadow transition-transform data-[state=checked]:translate-x-[22px]" />
      </RSwitch.Root>
    </SettingRow>
  );
}

/** 文本输入行（label + input；档案表单 / API 配置共用）。 */
export function TextFieldRow({
  title,
  hint,
  value,
  placeholder,
  type = "text",
  onChange,
}: {
  title: string;
  hint?: string;
  value: string;
  placeholder?: string;
  type?: "text" | "password";
  onChange: (value: string) => void;
}) {
  return (
    <div className="py-3">
      <div className="text-sm font-medium" style={{ color: "var(--c-bubble-text)" }}>
        {title}
      </div>
      {hint && (
        <div className="mt-0.5 text-xs" style={{ color: "var(--c-muted)" }}>
          {hint}
        </div>
      )}
      <input
        type={type}
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        className="mt-2 w-full rounded-xl border px-3 py-2 text-sm outline-none transition-colors focus:border-[var(--c-input-border-focus)]"
        style={{
          background: "var(--c-input-bg)",
          color: "var(--c-input-text)",
          borderColor: "var(--c-input-border)",
        }}
      />
    </div>
  );
}

/** 下拉选择行（原生 select，观感对齐输入框）。 */
export function SelectRow<T extends string>({
  title,
  hint,
  value,
  options,
  onChange,
}: {
  title: string;
  hint?: string;
  value: T;
  options: { value: T; label: string }[];
  onChange: (value: T) => void;
}) {
  return (
    <SettingRow title={title} hint={hint}>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value as T)}
        className="rounded-xl border px-3 py-1.5 text-sm outline-none"
        style={{
          background: "var(--c-input-bg)",
          color: "var(--c-input-text)",
          borderColor: "var(--c-input-border)",
        }}
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </SettingRow>
  );
}

/** 主操作按钮（紫色填充）。 */
export function PrimaryButton({
  children,
  onClick,
  disabled,
}: {
  children: ReactNode;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="rounded-xl px-4 py-2 text-sm font-medium text-white transition-colors disabled:opacity-50"
      style={{ background: disabled ? "var(--c-send-bg-disabled)" : "var(--c-send-bg)" }}
    >
      {children}
    </button>
  );
}

/** 危险操作按钮（描边红）。 */
export function DangerButton({
  children,
  onClick,
}: {
  children: ReactNode;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="rounded-xl border border-rose-500/60 px-4 py-2 text-sm font-medium text-rose-400 transition-colors hover:bg-rose-500/10"
    >
      {children}
    </button>
  );
}

/** 次级按钮（描边）。 */
export function GhostButton({
  children,
  onClick,
}: {
  children: ReactNode;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="rounded-xl border px-4 py-2 text-sm transition-colors hover:opacity-80"
      style={{
        borderColor: "var(--c-input-border)",
        color: "var(--c-bubble-text)",
        background: "var(--c-input-bg)",
      }}
    >
      {children}
    </button>
  );
}

/** 轻提示条（保存成功 / 失败等瞬态反馈）。 */
export function Toast({ text, ok }: { text: string; ok: boolean }) {
  return (
    <div
      className="mt-3 rounded-xl px-3 py-2 text-xs"
      style={{
        background: "var(--c-tool-bg)",
        color: ok ? "#4ade80" : "#fb7185",
      }}
    >
      {text}
    </div>
  );
}
