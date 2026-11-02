import { useEffect, useState } from "react";
import {
  CHAT_BATCH_DEFAULTS,
  fetchChatBatch,
  saveChatBatch,
  type ChatBatchSettings,
} from "../../lib/settings-api";
import { refreshUiSettings } from "../../lib/bridge-binding";
import { SectionTitle, SliderRow, SwitchRow, Toast } from "./controls";

/**
 * 聊天页：连发合并（「我发几条消息后，过一会儿再一起回复」）。
 *
 * 为什么放在设置里：不同人对「即时回复 / 攒一攒再回」的偏好差异很大，
 * 而整理窗口的时长直接影响体感（默认 3 分钟）。这里给出三个可调项：
 * 开关、触发条数、整理窗口上限，另加「停止输入多久算说完」。
 *
 * 保存后立即刷新 store（refreshUiSettings），排队逻辑马上按新参数工作，
 * 不需要重启后端。
 */
export function ChatPanel() {
  const [settings, setSettings] = useState<ChatBatchSettings | null>(null);
  const [toast, setToast] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    void fetchChatBatch().then(setSettings);
  }, []);

  const update = (patch: Partial<ChatBatchSettings>) =>
    setSettings((prev) => (prev ? { ...prev, ...patch } : prev));

  // 防抖落库（400ms）：拖动滑块过程中不反复写文件。
  useEffect(() => {
    if (!settings) return;
    const timer = setTimeout(() => {
      void saveChatBatch(settings).then((ok) => {
        void refreshUiSettings();
        setToast({
          ok,
          text: ok ? "已保存，下一条消息起按新参数合并" : "保存失败：后端未连接",
        });
        setTimeout(() => setToast(null), 2600);
      });
    }, 400);
    return () => clearTimeout(timer);
  }, [settings]);

  if (!settings) {
    return <div className="p-6 text-sm" style={{ color: "var(--c-muted)" }}>加载中…</div>;
  }

  return (
    <div>
      <SectionTitle text="连发消息合并" />
      <SwitchRow
        title="合并连发的消息再回复"
        hint="只影响聊天模式：连着发好几条时先攒起来，等你说完再整体理解、逐条回应"
        checked={settings.enabled}
        onChange={(v) => update({ enabled: v })}
      />
      <SliderRow
        title="攒够几条开始整理"
        hint="没攒够时照常逐条回复（只多等 1 秒左右，用来合并紧随其后的第二条）"
        value={settings.threshold}
        min={2}
        max={10}
        step={1}
        format={(v) => v + " 条"}
        onChange={(v) => update({ threshold: v })}
      />
      <SliderRow
        title="最长等多久再回复"
        hint="整理窗口上限：到点就算你还在发，也会先回一轮"
        value={settings.windowSeconds}
        min={5}
        max={600}
        step={5}
        format={(v) => (v >= 60 ? Math.round(v / 60) + " 分钟" : v + " 秒")}
        onChange={(v) => update({ windowSeconds: v })}
      />
      <SliderRow
        title="停止输入多久算说完"
        hint="在这段时间内没有新消息就提前发送，不必等到窗口到期"
        value={settings.quietSeconds}
        min={2}
        max={60}
        step={1}
        format={(v) => v + " 秒"}
        onChange={(v) => update({ quietSeconds: v })}
      />

      <div className="mt-4 rounded-xl px-3 py-2 text-[11.5px] leading-relaxed"
        style={{ background: "var(--c-tool-bg)", color: "var(--c-tool-text)" }}
      >
        默认：攒够 {CHAT_BATCH_DEFAULTS.threshold} 条 → 停止输入{" "}
        {CHAT_BATCH_DEFAULTS.quietSeconds} 秒后合并回复，最多等{" "}
        {Math.round(CHAT_BATCH_DEFAULTS.windowSeconds / 60)} 分钟。
        AI 正在回复时你继续发的消息会自动排队，等这一轮结束再发，
        不会再出现「同一句话问两遍、AI 反复回答」。
      </div>

      {toast && <Toast text={toast.text} ok={toast.ok} />}
    </div>
  );
}
