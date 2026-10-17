import { useEffect, useState } from "react";
import {
  fetchNapcat,
  saveNapcat,
  type NapcatConfig,
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
 * 渠道页：NapCat（OneBot 11 反向 WS）配置表单，读写 config.yaml
 * channels.napcat 节（config.set 注释保持写回）。未配置该节时表单
 * 以空值起步，保存即创建。
 */
export function NapcatPanel() {
  const [config, setConfig] = useState<NapcatConfig | null>(null);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    void fetchNapcat().then(setConfig);
  }, []);

  if (!config) {
    return <div className="p-6 text-sm" style={{ color: "var(--c-muted)" }}>加载中…</div>;
  }

  const update = (patch: Partial<NapcatConfig>) => {
    setConfig((prev) => (prev ? { ...prev, ...patch, configured: true } : prev));
    setDirty(true);
  };

  const save = async () => {
    setSaving(true);
    const ok = await saveNapcat(config);
    setSaving(false);
    setDirty(false);
    setToast({
      ok,
      text: ok
        ? "渠道配置已写入 config.yaml（新配置在下次后端重启后生效）"
        : "保存失败：config.yaml 写回被拒绝",
    });
    setTimeout(() => setToast(null), 3000);
  };

  return (
    <div>
      <SectionTitle text="NapCat 连接（OneBot 11 反向 WebSocket）" />
      <TextFieldRow
        title="反向 WS 地址"
        hint="NapCat 作为服务端，本程序作为客户端连入（ws://127.0.0.1:3001）"
        value={config.url}
        placeholder="ws://127.0.0.1:3001"
        onChange={(url) => update({ url })}
      />
      <TextFieldRow
        title="Access Token"
        hint="NapCat 的 access token，空串 = 不鉴权（建议必填）"
        value={config.access_token}
        type="password"
        placeholder="留空表示不鉴权"
        onChange={(access_token) => update({ access_token })}
      />

      <SectionTitle text="白名单" />
      <TextFieldRow
        title="发送者白名单（QQ 号）"
        hint="逗号分隔；空 = 不限制"
        value={config.allowed_senders}
        placeholder="如 123456, 234567"
        onChange={(allowed_senders) => update({ allowed_senders })}
      />
      <TextFieldRow
        title="群白名单（群号）"
        hint="逗号分隔；空 = 不限制"
        value={config.allowed_groups}
        placeholder="如 987654"
        onChange={(allowed_groups) => update({ allowed_groups })}
      />

      <SectionTitle text="行为" />
      <SelectRow<"private" | "group">
        title="裸数字 chat_id 归属"
        hint="纯数字目标按私聊还是群聊处理"
        value={config.default_chat_type}
        options={[
          { value: "private", label: "私聊" },
          { value: "group", label: "群聊" },
        ]}
        onChange={(default_chat_type) => update({ default_chat_type })}
      />
      <SwitchRow
        title="折叠工具提示"
        hint="把工具事件折叠成「正在执行 xx…」提示"
        checked={config.report_tools}
        onChange={(report_tools) => update({ report_tools })}
      />

      <div className="mt-6 flex items-center gap-3">
        <PrimaryButton onClick={() => void save()} disabled={!dirty || saving}>
          {saving ? "保存中…" : "保存渠道配置"}
        </PrimaryButton>
        <GhostButton
          onClick={() => {
            void fetchNapcat().then(setConfig);
            setDirty(false);
          }}
        >
          放弃修改
        </GhostButton>
      </div>
      {toast && <Toast text={toast.text} ok={toast.ok} />}
    </div>
  );
}
