import { useEffect, useState } from "react";
import {
  fetchPermissions,
  savePermissions,
  type PermissionSettings,
} from "../../lib/settings-api";
import {
  PrimaryButton,
  SectionTitle,
  SelectRow,
  TextFieldRow,
  Toast,
} from "./controls";

/** 权限页：沙箱缺省 / 审批策略 / 命令白名单（config.yaml permissions 节）。 */
export function PermissionsPanel() {
  const [settings, setSettings] = useState<PermissionSettings | null>(null);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    void fetchPermissions().then(setSettings);
  }, []);

  if (!settings) {
    return <div className="p-6 text-sm" style={{ color: "var(--c-muted)" }}>加载中…</div>;
  }

  const update = (patch: Partial<PermissionSettings>) => {
    setSettings((prev) => (prev ? { ...prev, ...patch } : prev));
    setDirty(true);
  };

  const save = async () => {
    setSaving(true);
    const ok = await savePermissions(settings);
    setSaving(false);
    setDirty(false);
    setToast({
      ok,
      text: ok ? "权限配置已保存（下次工具调用开始生效）" : "保存失败",
    });
    setTimeout(() => setToast(null), 2500);
  };

  return (
    <div>
      <SectionTitle text="沙箱与审批" />
      <SelectRow<PermissionSettings["sandbox_default"]>
        title="默认沙箱"
        hint="工具执行环境的缺省边界"
        value={settings.sandbox_default}
        options={[
          { value: "read-only", label: "只读（最安全）" },
          { value: "workspace-write", label: "工作区可写" },
          { value: "danger-full-access", label: "完全访问（不推荐）" },
        ]}
        onChange={(sandbox_default) => update({ sandbox_default })}
      />
      <SelectRow<PermissionSettings["approval_policy"]>
        title="审批策略"
        hint="越界操作弹出审批卡或直接拒绝"
        value={settings.approval_policy}
        options={[
          { value: "ask", label: "逐次询问（ask）" },
          { value: "never", label: "从不询问，直接拒绝（never）" },
        ]}
        onChange={(approval_policy) => update({ approval_policy })}
      />

      <SectionTitle text="命令白名单" />
      <TextFieldRow
        title="追加只读命令"
        hint="逗号分隔；白名单命令免审批直接执行"
        value={settings.command_allowlist}
        placeholder="如 git status, ls"
        onChange={(command_allowlist) => update({ command_allowlist })}
      />

      <div className="mt-6">
        <PrimaryButton onClick={() => void save()} disabled={!dirty || saving}>
          {saving ? "保存中…" : "保存权限配置"}
        </PrimaryButton>
      </div>
      {toast && <Toast text={toast.text} ok={toast.ok} />}
    </div>
  );
}
