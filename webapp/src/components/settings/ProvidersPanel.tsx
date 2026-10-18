import { useEffect, useState } from "react";
import {
  Cpu,
  Plus,
  ChevronDown,
  Check,
  X,
  Loader2,
  Eye,
  Zap,
} from "lucide-react";
import {
  fetchProviders,
  saveProvider,
  setActiveProvider,
  type ProviderEntry,
} from "../../lib/settings-api";
import { bridgeActions } from "../../lib/bridge-binding";
import { GuidanceCard, EmptyState } from "./GuidanceCard";

/**
 * 模型 API 页（第四期重做：从「字段平铺」改为「预设卡片」）。
 *
 * 交互目标：最常见路径——选厂商、贴 Key、点保存——不超过三步。
 * base_url / 模型名由预设自动填好，只有自定义提供商才需要手填。
 */

/** 常见厂商预设：选中后自动填 base_url 与常用模型名。 */
const PRESETS: {
  id: string;
  name: string;
  baseUrl: string;
  models: string[];
  note?: string;
}[] = [
  {
    id: "deepseek",
    name: "DeepSeek",
    baseUrl: "https://api.deepseek.com/v1",
    models: ["deepseek-chat", "deepseek-reasoner"],
  },
  {
    id: "qwen",
    name: "通义千问（DashScope）",
    baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    models: ["qwen-plus", "qwen-max", "qwen-turbo"],
  },
  {
    id: "glm",
    name: "智谱 GLM",
    baseUrl: "https://open.bigmodel.cn/api/paas/v4",
    models: ["glm-4-plus", "glm-4-flash"],
  },
  {
    id: "kimi",
    name: "Kimi（月之暗面）",
    baseUrl: "https://api.moonshot.cn/v1",
    models: ["moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"],
  },
  {
    id: "openai",
    name: "OpenAI",
    baseUrl: "https://api.openai.com/v1",
    models: ["gpt-4o", "gpt-4o-mini"],
  },
  {
    id: "custom",
    name: "自定义（OpenAI 兼容）",
    baseUrl: "",
    models: [],
    note: "任何兼容 OpenAI 接口的服务都可以填在这里",
  },
];

function presetOf(name: string) {
  return PRESETS.find((p) => p.id === name);
}

export function ProvidersPanel() {
  const [state, setState] = useState<{ active: string; providers: ProviderEntry[] } | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [testing, setTesting] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<Record<string, boolean>>({});
  const [toast, setToast] = useState<{ ok: boolean; text: string } | null>(null);

  const load = () => void fetchProviders().then(setState);

  useEffect(load, []);

  const notify = (ok: boolean, text: string) => {
    setToast({ ok, text });
    setTimeout(() => setToast(null), 3000);
  };

  if (!state) {
    return <div className="p-6 text-sm" style={{ color: "var(--c-muted)" }}>加载中…</div>;
  }

  const patchLocal = (name: string, patch: Partial<ProviderEntry>) =>
    setState((prev) =>
      prev
        ? { ...prev, providers: prev.providers.map((x) => (x.name === name ? { ...x, ...patch } : x)) }
        : prev,
    );

  const save = async (name: string) => {
    const entry = state.providers.find((p) => p.name === name);
    if (!entry) return;
    const ok = await saveProvider(name, {
      api_key: entry.api_key,
      base_url: entry.base_url,
      model: entry.model,
      temperature: entry.temperature,
      vision: entry.vision,
    });
    if (ok) {
      notify(true, "已保存：" + name);
      setEditing(null);
    } else {
      notify(false, "保存失败，请检查配置");
    }
  };

  const test = async (name: string) => {
    setTesting(name);
    const result = await testConnection(name);
    setTesting(null);
    setTestResult((prev) => ({ ...prev, [name]: result }));
    notify(result, result ? "配置可用" : "不可用：请检查 Key、地址与模型名");
  };

  return (
    <div>
      <div className="mb-4 flex items-start justify-between gap-4">
        <div>
          <h3 className="text-[15px] font-semibold" style={{ color: "var(--c-bubble-text)" }}>
            模型提供商
          </h3>
          <p className="mt-1 text-[12.5px] leading-relaxed" style={{ color: "var(--c-muted)" }}>
            选一个厂商，填上 API Key 就能用。对话请求会走这里配置的地址与模型。
          </p>
        </div>
        <button
          type="button"
          onClick={() => setAdding(true)}
          className="flex shrink-0 items-center gap-1.5 rounded-full px-3 py-1.5 text-[12.5px] font-medium transition-opacity hover:opacity-90"
          style={{ background: "var(--c-send-bg)", color: "var(--c-highlight-text)" }}
        >
          <Plus size={13} /> 添加
        </button>
      </div>

      <GuidanceCard tone="info" title="三步就能开始对话">
        <ol className="ml-3.5 list-decimal space-y-0.5">
          <li>点右上角「添加」，选一家你已有账号的厂商（地址和模型会自动填好）</li>
          <li>把该厂商控制台里的 API Key 粘进来</li>
          <li>点「保存」→ 回到对话发一句试试；失败多半是 Key 或模型名不对</li>
        </ol>
      </GuidanceCard>

      {state.providers.length === 0 && !adding && (
        <EmptyState
          icon={Cpu}
          title="还没有配置任何模型提供商"
          hint="没有模型就无法对话。选一家厂商并填入 Key 即可，配置只保存在本地，不会上传。"
          actionLabel="添加第一个"
          onAction={() => setAdding(true)}
        />
      )}

      <div className="flex flex-col gap-2">
        {state.providers.map((p) => {
          const isActive = p.name === state.active;
          const preset = presetOf(p.name);
          const isEditing = editing === p.name;
          return (
            <div
              key={p.name}
              className="ui-card overflow-hidden"
              style={isActive ? { borderColor: "var(--c-highlight)" } : undefined}
            >
              <div className="flex items-center gap-3 px-4 py-3">
                <div
                  className="flex h-8 w-8 shrink-0 items-center justify-center rounded-[var(--r-sm)]"
                  style={{ background: "var(--c-tool-bg)", color: "var(--c-highlight)" }}
                >
                  <Cpu size={15} />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-[13.5px] font-medium" style={{ color: "var(--c-bubble-text)" }}>
                      {preset?.name ?? p.name}
                    </span>
                    {isActive && (
                      <span
                        className="shrink-0 rounded-full px-1.5 py-[1px] text-[10px] font-semibold"
                        style={{ background: "var(--c-highlight)", color: "#fff" }}
                      >
                        使用中
                      </span>
                    )}
                  </div>
                  <div className="mt-0.5 truncate text-[11.5px]" style={{ color: "var(--c-muted)" }}>
                    {p.model || "未设置模型"} · {p.api_key ? "已填 Key" : "缺少 Key"}
                  </div>
                </div>

                {!isActive && (
                  <button
                    type="button"
                    className="shrink-0 rounded-full px-2.5 py-1 text-[11.5px] transition-colors hover:bg-[var(--c-nav-active-bg)]"
                    style={{ color: "var(--c-highlight)" }}
                    onClick={() =>
                      void setActiveProvider(p.name).then((ok) => {
                        if (ok) {
                          setState((prev) => (prev ? { ...prev, active: p.name } : prev));
                          notify(true, "已切换到 " + (preset?.name ?? p.name));
                        } else {
                          notify(false, "切换失败");
                        }
                      })
                    }
                  >
                    设为当前
                  </button>
                )}
                <button
                  type="button"
                  className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md transition-colors hover:bg-[var(--c-nav-active-bg)]"
                  style={{ color: "var(--c-muted)" }}
                  title={isEditing ? "收起" : "编辑"}
                  onClick={() => setEditing(isEditing ? null : p.name)}
                >
                  <ChevronDown
                    size={14}
                    style={{ transform: isEditing ? "rotate(180deg)" : undefined, transition: "transform .15s" }}
                  />
                </button>
              </div>

              {isEditing && (
                <div className="border-t px-4 py-3" style={{ borderColor: "var(--c-divider)" }}>
                  <Field
                    label="API Key"
                    value={p.api_key ?? ""}
                    type="password"
                    placeholder="sk-…"
                    onChange={(v) => patchLocal(p.name, { api_key: v })}
                  />
                  <Field
                    label="Base URL"
                    value={p.base_url ?? ""}
                    placeholder="https://api.example.com/v1"
                    onChange={(v) => patchLocal(p.name, { base_url: v })}
                  />
                  <Field
                    label="模型名"
                    value={p.model ?? ""}
                    placeholder={preset?.models[0] ?? "gpt-4o"}
                    list={preset?.models}
                    onChange={(v) => patchLocal(p.name, { model: v })}
                  />

                  <label className="mt-3 flex cursor-pointer items-center gap-2 text-[12.5px]" style={{ color: "var(--c-bubble-text)" }}>
                    <input
                      type="checkbox"
                      checked={Boolean(p.vision)}
                      onChange={(e) => patchLocal(p.name, { vision: e.target.checked })}
                    />
                    <Eye size={13} style={{ color: "var(--c-muted)" }} />
                    支持图片输入（vision）
                  </label>

                  <div className="mt-3 flex items-center gap-2">
                    <button
                      type="button"
                      className="flex items-center gap-1.5 rounded-full px-3 py-1.5 text-[12px] font-medium"
                      style={{ background: "var(--c-send-bg)", color: "var(--c-highlight-text)" }}
                      onClick={() => void save(p.name)}
                    >
                      <Check size={12} /> 保存
                    </button>
                    <button
                      type="button"
                      disabled={testing === p.name}
                      className="flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-[12px] disabled:opacity-60"
                      style={{ borderColor: "var(--c-input-border)", color: "var(--c-bubble-text)" }}
                      onClick={() => void test(p.name)}
                    >
                      {testing === p.name ? <Loader2 size={12} className="animate-spin" /> : <Zap size={12} />}
                      检查配置
                    </button>
                    {testResult[p.name] !== undefined && (
                      <span className="text-[11.5px]" style={{ color: testResult[p.name] ? "var(--c-success)" : "var(--c-danger)" }}>
                        {testResult[p.name] ? "可用" : "不可用"}
                      </span>
                    )}
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>

      {adding && (
        <AddProviderDialog
          existing={state.providers.map((p) => p.name)}
          onCancel={() => setAdding(false)}
          onCreate={(name, patch) => {
            void saveProvider(name, {
              api_key: patch.api_key ?? "",
              base_url: patch.base_url ?? "",
              model: patch.model ?? "",
              temperature: 0.7,
              vision: false,
            }).then((ok) => {
              if (ok) {
                setAdding(false);
                load();
                notify(true, "已添加 " + name);
              } else {
                notify(false, "添加失败（同名提供商可能已存在）");
              }
            });
          }}
        />
      )}

      {toast && (
        <div
          className="ui-fade-in fixed bottom-6 left-1/2 z-50 -translate-x-1/2 rounded-full px-4 py-2 text-[12.5px]"
          style={{
            background: toast.ok ? "var(--c-success)" : "var(--c-danger)",
            color: "#fff",
            boxShadow: "var(--shadow-lg)",
          }}
        >
          {toast.text}
        </div>
      )}
    </div>
  );
}

/** 单行输入（带可选 datalist 建议）。 */
function Field({
  label,
  value,
  onChange,
  placeholder,
  type = "text",
  list,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  type?: string;
  list?: string[];
}) {
  const id = "dl-" + label.replace(/[^a-zA-Z0-9]/g, "");
  return (
    <label className="mb-2 block">
      <span className="mb-1 block text-[11.5px] font-medium" style={{ color: "var(--c-muted)" }}>
        {label}
      </span>
      <input
        type={type}
        value={value}
        placeholder={placeholder}
        list={list ? id : undefined}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-[var(--r-sm)] border px-2.5 py-1.5 text-[13px] outline-none transition-colors focus:border-[var(--c-input-border-focus)]"
        style={{
          background: "var(--c-input-bg)",
          borderColor: "var(--c-input-border)",
          color: "var(--c-input-text)",
        }}
      />
      {list && (
        <datalist id={id}>
          {list.map((m) => (
            <option key={m} value={m} />
          ))}
        </datalist>
      )}
    </label>
  );
}

/** 添加提供商：先选厂商，自动填好地址与模型。 */
function AddProviderDialog({
  existing,
  onCancel,
  onCreate,
}: {
  existing: string[];
  onCancel: () => void;
  onCreate: (name: string, patch: Partial<ProviderEntry>) => void;
}) {
  const [presetId, setPresetId] = useState(PRESETS[0].id);
  const [customName, setCustomName] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState(PRESETS[0].baseUrl);
  const [model, setModel] = useState(PRESETS[0].models[0] ?? "");

  const preset = PRESETS.find((p) => p.id === presetId) ?? PRESETS[0];
  const finalName = presetId === "custom" ? customName.trim() : presetId;
  const duplicate = existing.includes(finalName);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-6"
      style={{ background: "var(--c-overlay-bg)" }}
      onClick={onCancel}
    >
      <div
        className="ui-card ui-fade-in w-full max-w-md overflow-hidden"
        onClick={(e) => e.stopPropagation()}
        style={{ boxShadow: "var(--shadow-lg)" }}
      >
        <div className="flex items-center justify-between border-b px-4 py-3" style={{ borderColor: "var(--c-divider)" }}>
          <span className="text-[13.5px] font-semibold" style={{ color: "var(--c-bubble-text)" }}>
            添加模型提供商
          </span>
          <button type="button" onClick={onCancel} style={{ color: "var(--c-muted)" }}>
            <X size={15} />
          </button>
        </div>

        <div className="px-4 py-3">
          <label className="mb-2 block">
            <span className="mb-1 block text-[11.5px] font-medium" style={{ color: "var(--c-muted)" }}>
              选择厂商
            </span>
            <select
              value={presetId}
              onChange={(e) => {
                const next = PRESETS.find((p) => p.id === e.target.value) ?? PRESETS[0];
                setPresetId(next.id);
                setBaseUrl(next.baseUrl);
                setModel(next.models[0] ?? "");
              }}
              className="w-full rounded-[var(--r-sm)] border px-2.5 py-1.5 text-[13px] outline-none"
              style={{
                background: "var(--c-input-bg)",
                borderColor: "var(--c-input-border)",
                color: "var(--c-input-text)",
              }}
            >
              {PRESETS.map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
          </label>

          {presetId === "custom" && (
            <Field
              label="提供商标识（英文，用作配置键）"
              value={customName}
              onChange={setCustomName}
              placeholder="my-provider"
            />
          )}
          <Field label="API Key" value={apiKey} onChange={setApiKey} type="password" placeholder="sk-…" />
          <Field label="Base URL" value={baseUrl} onChange={setBaseUrl} placeholder="https://…/v1" />
          <Field label="模型名" value={model} onChange={setModel} placeholder="模型 id" list={preset.models} />

          {preset.note && (
            <p className="mt-1 text-[11.5px]" style={{ color: "var(--c-muted)" }}>{preset.note}</p>
          )}
          {duplicate && (
            <p className="mt-1 text-[11.5px]" style={{ color: "var(--c-danger)" }}>
              已存在同名提供商，请换个标识
            </p>
          )}
        </div>

        <div className="flex justify-end gap-2 border-t px-4 py-3" style={{ borderColor: "var(--c-divider)" }}>
          <button
            type="button"
            onClick={onCancel}
            className="rounded-full px-3 py-1.5 text-[12.5px]"
            style={{ color: "var(--c-muted)" }}
          >
            取消
          </button>
          <button
            type="button"
            disabled={!finalName || duplicate}
            className="rounded-full px-4 py-1.5 text-[12.5px] font-medium disabled:opacity-50"
            style={{ background: "var(--c-send-bg)", color: "var(--c-highlight-text)" }}
            onClick={() => onCreate(finalName, { api_key: apiKey, base_url: baseUrl, model })}
          >
            添加
          </button>
        </div>
      </div>
    </div>
  );
}

/**
 * 配置完整性检查。
 *
 * 后端没有真实的连通性探测 RPC（provider.test 未实现时），这里退化为
 * 「Key / Base URL / 模型名三者非空」的本地检查。文案如实说明是配置检查，
 * 不谎称已验证网络连通。
 */
async function testConnection(name: string): Promise<boolean> {
  try {
    const section = (await bridgeActions().invoke("config.get", {
      key: "providers." + name,
    })) as { value?: Record<string, unknown> };
    const v = section.value ?? {};
    return Boolean(v.api_key) && Boolean(v.base_url) && Boolean(v.model);
  } catch {
    return false;
  }
}
