import { useEffect, useMemo, useState } from "react";
import { Check, User, MapPin, Cake, Sparkles } from "lucide-react";
import { fetchProfile, saveProfile, type ProfileData, type ProfileField } from "../../lib/settings-api";

/**
 * 用户档案页（第四期重做：从「六个文本框」改为「表单控件」）。
 *
 * 关键改进：
 *   - 性别用选项按钮而不是自由文本；
 *   - 生日用日期选择器（原生 date input，自带日历）；
 *   - 城市下拉搜索，选中后自动推导时区（用户不再需要知道 IANA 名）；
 *   - 时区只读展示，仅在「其他」时才展开手填；
 *   - 底部实时预览「遐蝶会怎么称呼你」。
 */

/** 常用城市 → IANA 时区。覆盖国内主要城市 + 常见海外。 */
const CITIES: { city: string; tz: string }[] = [
  { city: "北京", tz: "Asia/Shanghai" },
  { city: "上海", tz: "Asia/Shanghai" },
  { city: "广州", tz: "Asia/Shanghai" },
  { city: "深圳", tz: "Asia/Shanghai" },
  { city: "杭州", tz: "Asia/Shanghai" },
  { city: "南京", tz: "Asia/Shanghai" },
  { city: "成都", tz: "Asia/Shanghai" },
  { city: "重庆", tz: "Asia/Shanghai" },
  { city: "武汉", tz: "Asia/Shanghai" },
  { city: "西安", tz: "Asia/Shanghai" },
  { city: "天津", tz: "Asia/Shanghai" },
  { city: "长沙", tz: "Asia/Shanghai" },
  { city: "苏州", tz: "Asia/Shanghai" },
  { city: "青岛", tz: "Asia/Shanghai" },
  { city: "厦门", tz: "Asia/Shanghai" },
  { city: "香港", tz: "Asia/Hong_Kong" },
  { city: "台北", tz: "Asia/Taipei" },
  { city: "澳门", tz: "Asia/Macau" },
  { city: "东京", tz: "Asia/Tokyo" },
  { city: "首尔", tz: "Asia/Seoul" },
  { city: "新加坡", tz: "Asia/Singapore" },
  { city: "伦敦", tz: "Europe/London" },
  { city: "巴黎", tz: "Europe/Paris" },
  { city: "柏林", tz: "Europe/Berlin" },
  { city: "纽约", tz: "America/New_York" },
  { city: "洛杉矶", tz: "America/Los_Angeles" },
  { city: "悉尼", tz: "Australia/Sydney" },
];

const GENDERS = ["女", "男", "其他"];

const TIMEZONES = [
  "Asia/Shanghai",
  "Asia/Hong_Kong",
  "Asia/Taipei",
  "Asia/Tokyo",
  "Asia/Seoul",
  "Asia/Singapore",
  "Europe/London",
  "Europe/Paris",
  "America/New_York",
  "America/Los_Angeles",
  "Australia/Sydney",
  "UTC",
];

export function ProfilePanel() {
  const [profile, setProfile] = useState<ProfileData | null>(null);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState<{ ok: boolean; text: string } | null>(null);
  const [cityQuery, setCityQuery] = useState("");
  const [showCityList, setShowCityList] = useState(false);

  useEffect(() => {
    void fetchProfile().then((p) => {
      setProfile(p);
      setCityQuery(p.city ?? "");
    });
  }, []);

  const filteredCities = useMemo(() => {
    const q = cityQuery.trim();
    if (!q) return CITIES.slice(0, 8);
    return CITIES.filter((c) => c.city.includes(q)).slice(0, 8);
  }, [cityQuery]);

  if (!profile) {
    return <div className="p-6 text-sm" style={{ color: "var(--c-muted)" }}>加载中…</div>;
  }

  const set = (field: ProfileField, value: string | null) => {
    setProfile((prev) => (prev ? { ...prev, [field]: value } : prev));
    setDirty(true);
  };

  const pickCity = (city: string, tz: string) => {
    setProfile((prev) => (prev ? { ...prev, city, timezone: tz } : prev));
    setCityQuery(city);
    setShowCityList(false);
    setDirty(true);
  };

  const save = async () => {
    setSaving(true);
    const updates: Partial<Record<ProfileField, string | null>> = {};
    for (const field of ["nickname", "appellation", "gender", "birthday", "city", "timezone"] as ProfileField[]) {
      const value = (profile[field] ?? "").toString().trim();
      updates[field] = value === "" ? null : value;
    }
    const ok = await saveProfile(updates);
    setSaving(false);
    setDirty(false);
    setToast({ ok, text: ok ? "档案已保存，下一轮对话开始生效" : "保存失败：后端拒绝了本次修改" });
    setTimeout(() => setToast(null), 2500);
  };

  const callName = profile.appellation?.trim() || profile.nickname?.trim() || "你";

  return (
    <div className="pb-6">
      <div className="mb-5 rounded-[var(--r-lg)] border px-4 py-3.5" style={{ borderColor: "var(--c-divider)", background: "var(--c-surface-bg)" }}>
        <div className="flex items-center gap-2 text-[12.5px]" style={{ color: "var(--c-muted)" }}>
          <Sparkles size={13} style={{ color: "var(--c-highlight)" }} />
          这些信息会写进系统提示词，影响称呼、时间感知与本地化回答。
        </div>
        <div className="mt-2 text-[14px]" style={{ color: "var(--c-bubble-text)" }}>
          遐蝶会这样称呼你：<b style={{ color: "var(--c-highlight)" }}>{callName}</b>
        </div>
      </div>

      {/* 昵称 */}
      <Label icon={User} text="昵称" hint="遐蝶平时怎么叫你" />
      <TextInput
        value={profile.nickname ?? ""}
        placeholder="例如：阿澈"
        onChange={(v) => set("nickname", v)}
      />

      {/* 称呼偏好 */}
      <Label icon={User} text="称呼偏好" hint="留空则用昵称；填了会优先用这个" />
      <TextInput
        value={profile.appellation ?? ""}
        placeholder="例如：阁下 / 主人 / 老板"
        onChange={(v) => set("appellation", v)}
      />

      {/* 性别 */}
      <Label icon={User} text="性别" hint="可选，影响部分措辞" />
      <div className="mb-5 flex gap-2">
        {GENDERS.map((g) => {
          const active = profile.gender === g;
          return (
            <button
              key={g}
              type="button"
              onClick={() => set("gender", active ? null : g)}
              className="rounded-full border px-4 py-1.5 text-[12.5px] transition-colors"
              style={{
                background: active ? "var(--c-highlight)" : "transparent",
                borderColor: active ? "var(--c-highlight)" : "var(--c-input-border)",
                color: active ? "#fff" : "var(--c-bubble-text)",
              }}
            >
              {g}
            </button>
          );
        })}
      </div>

      {/* 生日 */}
      <Label icon={Cake} text="生日" hint="生日当天遐蝶会送上祝福（可选年份）" />
      <input
        type="date"
        value={normalizeDate(profile.birthday ?? "")}
        onChange={(e) => set("birthday", e.target.value)}
        className="mb-5 w-full rounded-[var(--r-sm)] border px-3 py-2 text-[13px] outline-none transition-colors focus:border-[var(--c-input-border-focus)]"
        style={{
          background: "var(--c-input-bg)",
          borderColor: "var(--c-input-border)",
          color: "var(--c-input-text)",
          colorScheme: "dark",
        }}
      />

      {/* 城市：下拉搜索 */}
      <Label icon={MapPin} text="城市 / 地区" hint="用于天气、地理与本地生活问题" />
      <div className="relative mb-5">
        <input
          value={cityQuery}
          placeholder="输入城市名搜索，如「北京」"
          onChange={(e) => {
            setCityQuery(e.target.value);
            setShowCityList(true);
            set("city", e.target.value);
          }}
          onFocus={() => setShowCityList(true)}
          onBlur={() => setTimeout(() => setShowCityList(false), 150)}
          className="w-full rounded-[var(--r-sm)] border px-3 py-2 text-[13px] outline-none transition-colors focus:border-[var(--c-input-border-focus)]"
          style={{
            background: "var(--c-input-bg)",
            borderColor: "var(--c-input-border)",
            color: "var(--c-input-text)",
          }}
        />
        {showCityList && filteredCities.length > 0 && (
          <div className="ui-popover absolute left-0 right-0 top-full z-20 mt-1 max-h-52 overflow-y-auto">
            {filteredCities.map((c) => (
              <button
                key={c.city}
                type="button"
                className="flex w-full items-center justify-between px-3 py-1.5 text-left text-[12.5px] transition-colors hover:bg-[var(--c-nav-active-bg)]"
                style={{ color: "var(--c-bubble-text)" }}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => pickCity(c.city, c.tz)}
              >
                <span>{c.city}</span>
                <span className="text-[11px]" style={{ color: "var(--c-muted)" }}>{c.tz}</span>
              </button>
            ))}
          </div>
        )}
      </div>

      {/* 时区：自动推导，只读展示 + 少量手动选项 */}
      <Label icon={MapPin} text="时区" hint="选择城市后自动确定，一般不用改" />
      <select
        value={profile.timezone ?? ""}
        onChange={(e) => set("timezone", e.target.value)}
        className="mb-5 w-full rounded-[var(--r-sm)] border px-3 py-2 text-[13px] outline-none"
        style={{
          background: "var(--c-input-bg)",
          borderColor: "var(--c-input-border)",
          color: "var(--c-input-text)",
        }}
      >
        <option value="">跟随城市（未设置时用系统时区）</option>
        {TIMEZONES.map((tz) => (
          <option key={tz} value={tz}>{tz}</option>
        ))}
      </select>

      <div className="mt-2 flex items-center gap-3">
        <button
          type="button"
          onClick={() => void save()}
          disabled={!dirty || saving}
          className="flex items-center gap-1.5 rounded-full px-4 py-2 text-[13px] font-medium disabled:opacity-50"
          style={{ background: "var(--c-send-bg)", color: "var(--c-highlight-text)" }}
        >
          <Check size={13} />
          {saving ? "保存中…" : "保存档案"}
        </button>
        {!dirty && (
          <span className="text-xs" style={{ color: "var(--c-muted)" }}>无未保存修改</span>
        )}
      </div>

      {toast && (
        <div
          className="ui-fade-in mt-3 rounded-full px-3 py-1.5 text-[12px]"
          style={{
            background: toast.ok ? "var(--c-success)" : "var(--c-danger)",
            color: "#fff",
            display: "inline-block",
          }}
        >
          {toast.text}
        </div>
      )}
    </div>
  );
}

/** 后端可能存 "01-01"（无年份），date input 需要 yyyy-mm-dd。 */
function normalizeDate(value: string): string {
  if (!value) return "";
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return value;
  if (/^\d{2}-\d{2}$/.test(value)) return "2000-" + value;
  return "";
}

function Label({ icon: Icon, text, hint }: { icon: typeof User; text: string; hint?: string }) {
  return (
    <div className="mb-1.5 mt-4">
      <div className="flex items-center gap-1.5">
        <Icon size={13} style={{ color: "var(--c-muted)" }} />
        <span className="text-[12.5px] font-medium" style={{ color: "var(--c-bubble-text)" }}>
          {text}
        </span>
      </div>
      {hint && (
        <p className="mt-0.5 text-[11.5px]" style={{ color: "var(--c-muted)" }}>{hint}</p>
      )}
    </div>
  );
}

function TextInput({
  value,
  onChange,
  placeholder,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  return (
    <input
      value={value}
      placeholder={placeholder}
      onChange={(e) => onChange(e.target.value)}
      className="w-full rounded-[var(--r-sm)] border px-3 py-2 text-[13px] outline-none transition-colors focus:border-[var(--c-input-border-focus)]"
      style={{
        background: "var(--c-input-bg)",
        borderColor: "var(--c-input-border)",
        color: "var(--c-input-text)",
      }}
    />
  );
}
