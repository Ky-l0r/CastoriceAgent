import { useEffect, useState } from "react";
import {
  PROFILE_FIELDS,
  PROFILE_FIELD_HINTS,
  PROFILE_FIELD_LABELS,
  fetchProfile,
  saveProfile,
  type ProfileData,
  type ProfileField,
} from "../../lib/settings-api";
import { PrimaryButton, SectionTitle, TextFieldRow, Toast } from "./controls";

/**
 * 用户档案页（t4 核心新增）：昵称 / 称呼偏好 / 性别 / 生日 / 城市 / 时区。
 * 保存走 profile.set（后端白名单校验），档案喂给提示词与记忆检索。
 */
export function ProfilePanel() {
  const [profile, setProfile] = useState<ProfileData | null>(null);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    void fetchProfile().then(setProfile);
  }, []);

  if (!profile) {
    return <div className="p-6 text-sm" style={{ color: "var(--c-muted)" }}>加载中…</div>;
  }

  const update = (field: ProfileField, value: string) => {
    setProfile((prev) => (prev ? { ...prev, [field]: value } : prev));
    setDirty(true);
  };

  const save = async () => {
    setSaving(true);
    const updates: Partial<Record<ProfileField, string | null>> = {};
    for (const field of PROFILE_FIELDS) {
      const value = (profile[field] ?? "").trim();
      updates[field] = value === "" ? null : value;
    }
    const ok = await saveProfile(updates);
    setSaving(false);
    setDirty(false);
    setToast({
      ok,
      text: ok ? "档案已保存，下一轮对话开始生效" : "保存失败：后端拒绝了本次修改",
    });
    setTimeout(() => setToast(null), 2500);
  };

  return (
    <div>
      <SectionTitle text="我是谁（AI 会据此称呼你并在对话中使用这些信息）" />
      {PROFILE_FIELDS.map((field) => (
        <TextFieldRow
          key={field}
          title={PROFILE_FIELD_LABELS[field]}
          hint={PROFILE_FIELD_HINTS[field]}
          value={profile[field] ?? ""}
          placeholder={field === "timezone" ? "Asia/Shanghai" : "可选"}
          onChange={(value) => update(field, value)}
        />
      ))}
      <div className="mt-6 flex items-center gap-3">
        <PrimaryButton onClick={() => void save()} disabled={!dirty || saving}>
          {saving ? "保存中…" : "保存档案"}
        </PrimaryButton>
        {!dirty && (
          <span className="text-xs" style={{ color: "var(--c-muted)" }}>
            无未保存修改
          </span>
        )}
      </div>
      {toast && <Toast text={toast.text} ok={toast.ok} />}
    </div>
  );
}
