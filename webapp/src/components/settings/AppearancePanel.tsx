import { useEffect, useState } from "react";
import {
  fetchAppearance,
  saveAppearance,
  type AppearanceSettings,
  type ThemeMode,
} from "../../lib/settings-api";
import { useBridgeStore } from "../../store/bridge-store";
import {
  GhostButton,
  SectionTitle,
  SelectRow,
  SliderRow,
  SwitchRow,
} from "./controls";

/**
 * 外观页：深浅主题 / 圆角半径 / 行间距 / 段间距滑块 / 气泡开关。
 * 所有调整实时预览（CSS 变量 + data-theme），同时防抖写 ui_settings.json
 * （兼容既有 theme / ui_mode 键——settings-api 只合并写自己的键）。
 */
export function AppearancePanel() {
  const [settings, setSettings] = useState<AppearanceSettings | null>(null);
  const [saved, setSaved] = useState<null | "ok">(null);

  useEffect(() => {
    void fetchAppearance().then(setSettings);
  }, []);

  // 防抖落库（300ms）；加载完成前不写。
  useEffect(() => {
    if (!settings) return;
    const timer = setTimeout(() => {
      void saveAppearance({
        theme: settings.theme,
        bubbleRadius: settings.bubbleRadius,
        lineHeight: settings.lineHeight,
        sectionGap: settings.sectionGap,
        bubbles: settings.bubbles,
      }).then(() => {
        setSaved("ok");
        setTimeout(() => setSaved(null), 1500);
      });
    }, 300);
    return () => clearTimeout(timer);
  }, [settings]);

  const update = (patch: Partial<AppearanceSettings>) =>
    setSettings((prev) => (prev ? { ...prev, ...patch } : prev));

  // 主题：沿用主界面 data-theme 约定（store.theme 只认 dark|light）。
  const applyTheme = (mode: ThemeMode) => {
    const effective = mode === "system"
      ? (window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark")
      : mode;
    document.documentElement.dataset.theme = effective;
    update({ theme: mode });
  };

  // 外观变量实时预览：同步进 store，由 App 根组件统一挂到 documentElement。
  // 这样离开设置页后预览仍然生效（原来只在面板挂载期间生效）。
  //
  // 注意：这个 effect 必须在下面的「加载中」提前 return **之前**声明。
  // 放在 return 之后会让首帧（settings 为 null）与后续帧的 hook 数量不一致，
  // React 会直接抛 error #310 并白屏。
  useEffect(() => {
    if (!settings) return;
    useBridgeStore.getState().setAppearance({
      bubbleRadius: settings.bubbleRadius,
      lineHeight: settings.lineHeight,
      sectionGap: settings.sectionGap,
      bubbles: settings.bubbles,
    });
  }, [settings]);

  if (!settings) {
    return <div className="p-6 text-sm" style={{ color: "var(--c-muted)" }}>加载中…</div>;
  }

  return (
    <div>
      <SectionTitle text="主题" />
      <SelectRow<ThemeMode>
        title="深浅主题"
        hint="跟随系统在启动时解析为深色或浅色"
        value={settings.theme}
        options={[
          { value: "dark", label: "深色" },
          { value: "light", label: "浅色" },
          { value: "system", label: "跟随系统" },
        ]}
        onChange={applyTheme}
      />

      <SectionTitle text="排版" />
      <SliderRow
        title="圆角半径"
        hint="消息气泡与输入框的圆角（0 = 直角）"
        value={settings.bubbleRadius}
        min={0}
        max={32}
        step={1}
        format={(v) => v + "px"}
        onChange={(v) => update({ bubbleRadius: v })}
      />
      <SliderRow
        title="行间距"
        hint="消息文本的行高倍数"
        value={settings.lineHeight}
        min={1.2}
        max={2.2}
        step={0.05}
        format={(v) => v.toFixed(2)}
        onChange={(v) => update({ lineHeight: v })}
      />
      <SliderRow
        title="段间距"
        hint="相邻消息气泡之间的距离"
        value={settings.sectionGap}
        min={8}
        max={40}
        step={1}
        format={(v) => v + "px"}
        onChange={(v) => update({ sectionGap: v })}
      />
      <SwitchRow
        title="消息气泡"
        hint="关闭后消息以无底色文本流展示"
        checked={settings.bubbles}
        onChange={(v) => update({ bubbles: v })}
      />

      <div className="mt-6 flex items-center gap-3">
        <GhostButton
          onClick={() => {
            applyTheme("dark");
            update({ bubbleRadius: 16, lineHeight: 1.6, sectionGap: 16, bubbles: true });
          }}
        >
          恢复默认
        </GhostButton>
        {saved && (
          <span className="text-xs" style={{ color: "#4ade80" }}>
            已保存
          </span>
        )}
      </div>
    </div>
  );
}
