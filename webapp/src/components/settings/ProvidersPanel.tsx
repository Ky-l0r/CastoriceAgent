import { useEffect, useState } from "react";
import {
  fetchProviders,
  saveProvider,
  setActiveProvider,
  type ProviderEntry,
} from "../../lib/settings-api";
import {
  GhostButton,
  PrimaryButton,
  SectionTitle,
  SelectRow,
  SwitchRow,
  TextFieldRow,
  Toast,
} from "./controls";

/**
 * 模型 API 页：providers 节的查看与编辑（api_key / base_url / model /
 * temperature / vision）+ active_provider 切换。改动按提供商逐个保存。
 */
export function ProvidersPanel() {
  const [state, setState] = useState<{
    active: string;
    providers: ProviderEntry[];
  } | null>(null);
  const [dirtyNames, setDirtyNames] = useState<Set<string>>(new Set());
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    void fetchProviders().then(setState);
  }, []);

  if (!state) {
    return <div className="p-6 text-sm" style={{ color: "var(--c-muted)" }}>加载中…</div>;
  }

  const updateProvider = (name: string, patch: Partial<ProviderEntry>) => {
    setState((prev) =>
      prev
        ? {
            ...prev,
            providers: prev.providers.map((p) =>
              p.name === name ? { ...p, ...patch } : p,
            ),
          }
        : prev,
    );
    setDirtyNames((prev) => new Set(prev).add(name));
  };

  const saveProviderFields = async (name: string) => {
    const entry = state.providers.find((p) => p.name === name);
    if (!entry) return false;
    return saveProvider(name, {
      api_key: entry.api_key,
      base_url: entry.base_url,
      model: entry.model,
      temperature: entry.temperature,
      vision: entry.vision,
    });
  };

  const saveAll = async () => {
    setSaving(true);
    let allOk = true;
    for (const name of dirtyNames) {
      const ok = await saveProviderFields(name);
      allOk = allOk && ok;
    }
    setSaving(false);
    setDirtyNames(new Set());
    setToast({
      ok: allOk,
      text: allOk
        ? "已保存。切换提供商后立即生效（当前对话上下文保留）"
        : "部分提供商保存失败，请检查配置",
    });
    setTimeout(() => setToast(null), 3000);
  };

  const switchActive = async (name: string) => {
    const ok = await setActiveProvider(name);
    if (ok) setState((prev) => (prev ? { ...prev, active: name } : prev));
    setToast({
      ok,
      text: ok ? "激活提供商已切换：" + name : "切换失败",
    });
    setTimeout(() => setToast(null), 2500);
  };

  return (
    <div>
      <SectionTitle text="激活提供商" />
      <SelectRow
        title="当前使用"
        hint="对话请求走这个提供商的 base_url + model"
        value={state.active || (state.providers[0]?.name ?? "")}
        options={state.providers.map((p) => ({ value: p.name, label: p.name }))}
        onChange={(name) => void switchActive(name)}
      />

      {state.providers.map((provider) => (
        <div key={provider.name}>
          <SectionTitle text={"提供商：" + provider.name} />
          <TextFieldRow
            title="API Key"
            value={provider.api_key}
            type="password"
            placeholder="sk-..."
            onChange={(api_key) => updateProvider(provider.name, { api_key })}
          />
          <TextFieldRow
            title="Base URL"
            value={provider.base_url}
            placeholder="https://api.example.com/v1"
            onChange={(base_url) => updateProvider(provider.name, { base_url })}
          />
          <TextFieldRow
            title="模型名"
            value={provider.model}
            placeholder="deepseek-chat"
            onChange={(model) => updateProvider(provider.name, { model })}
          />
          <SwitchRow
            title="支持图片输入（vision）"
            hint="开启后输入区显示「发送图片」按钮"
            checked={provider.vision}
            onChange={(vision) => updateProvider(provider.name, { vision })}
          />
        </div>
      ))}

      <div className="mt-6 flex items-center gap-3">
        <PrimaryButton onClick={() => void saveAll()} disabled={dirtyNames.size === 0 || saving}>
          {saving ? "保存中…" : "保存修改（" + dirtyNames.size + " 个提供商）"}
        </PrimaryButton>
        <GhostButton
          onClick={() => {
            void fetchProviders().then(setState);
            setDirtyNames(new Set());
          }}
        >
          放弃修改
        </GhostButton>
      </div>
      {toast && <Toast text={toast.text} ok={toast.ok} />}
    </div>
  );
}
