/**
 * 设置中心专用 RPC 封装（t4）：profile / ui_settings / config / memory 四组
 * 方法面的类型化薄包装。所有调用 fail-open 语义由调用方决定——这里只做
 * 类型收敛与字段白名单，让页面组件不用拼裸 params。
 */
import { bridgeActions } from "./bridge-binding";

export type ThemeMode = "dark" | "light" | "system";

/** castorice/profile.py PROFILE_FIELDS 的前端镜像（顺序即表单顺序）。 */
export const PROFILE_FIELDS = [
  "nickname",
  "appellation",
  "gender",
  "birthday",
  "city",
  "timezone",
] as const;

export type ProfileField = (typeof PROFILE_FIELDS)[number];

export const PROFILE_FIELD_LABELS: Record<ProfileField, string> = {
  nickname: "昵称",
  appellation: "称呼偏好",
  gender: "性别",
  birthday: "生日",
  city: "城市",
  timezone: "时区",
};

export const PROFILE_FIELD_HINTS: Record<ProfileField, string> = {
  nickname: "AI 平时怎么叫你",
  appellation: "优先使用的称呼（如「阁下」），留空则用昵称",
  gender: "可选，自由填写",
  birthday: "格式如 01-01 或 2000-01-01，生日临近时 AI 会送上祝福",
  city: "天气、地理与本地生活问题的定位依据",
  timezone: "IANA 时区名（如 Asia/Shanghai），影响时间提示",
};

export type ProfileData = Partial<Record<ProfileField, string | null>>;

/** 外观偏好（ui_settings.json 的 Web 视图；theme 兼容旧值 system）。 */
export interface AppearanceSettings {
  theme: ThemeMode;
  bubbleRadius: number;   // 圆角半径 px（气泡 / 输入框共用一个变量）
  lineHeight: number;     // 行间距（倍数）
  sectionGap: number;     // 段间距 px
  bubbles: boolean;       // 气泡开关
}

export const APPEARANCE_DEFAULTS: AppearanceSettings = {
  theme: "dark",
  bubbleRadius: 16,
  lineHeight: 1.6,
  sectionGap: 16,
  bubbles: true,
};

const clamp = (value: unknown, min: number, max: number, fallback: number): number => {
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n)) return fallback;
  return Math.min(max, Math.max(min, n));
};

/** 读取档案（未知形态一律回退空对象）。 */
export async function fetchProfile(): Promise<ProfileData> {
  const { invoke } = bridgeActions();
  try {
    const result = (await invoke("profile.get")) as {
      profile?: Record<string, unknown>;
    };
    const raw = result.profile ?? {};
    const profile: ProfileData = {};
    for (const key of PROFILE_FIELDS) {
      const value = raw[key];
      if (typeof value === "string") profile[key] = value;
      else if (value === null) profile[key] = null;
    }
    return profile;
  } catch {
    return {};
  }
}

/** 保存档案（白名单字段；返回 false 表示后端拒绝）。 */
export async function saveProfile(
  updates: Partial<Record<ProfileField, string | null>>,
): Promise<boolean> {
  const { invoke } = bridgeActions();
  try {
    await invoke("profile.set", { updates });
    return true;
  } catch {
    return false;
  }
}

/** 读取外观偏好（ui_settings.get + 缺省合并）。 */
export async function fetchAppearance(): Promise<AppearanceSettings> {
  const { invoke } = bridgeActions();
  try {
    const result = (await invoke("ui_settings.get")) as {
      settings?: Record<string, unknown>;
    };
    const s = result.settings ?? {};
    const theme = s.theme === "light" || s.theme === "dark" ? s.theme : "dark";
    return {
      theme,
      bubbleRadius: clamp(s.bubbleRadius, 0, 32, APPEARANCE_DEFAULTS.bubbleRadius),
      lineHeight: clamp(s.lineHeight, 1.2, 2.2, APPEARANCE_DEFAULTS.lineHeight),
      sectionGap: clamp(s.sectionGap, 8, 40, APPEARANCE_DEFAULTS.sectionGap),
      bubbles: s.bubbles === undefined ? APPEARANCE_DEFAULTS.bubbles : Boolean(s.bubbles),
    };
  } catch {
    return { ...APPEARANCE_DEFAULTS };
  }
}

/** 保存外观偏好（合并写回 ui_settings.json，不碰 theme/ui_mode 之外的旧键）。 */
export async function saveAppearance(patch: Partial<AppearanceSettings>): Promise<void> {
  const { invoke } = bridgeActions();
  try {
    await invoke("ui_settings.set", { updates: patch });
  } catch {
    /* 落库失败：本地预览仍然生效 */
  }
}

/** NapCat 渠道配置（config.yaml channels.napcat 节）。 */
export interface NapcatConfig {
  configured: boolean;
  url: string;
  access_token: string;
  allowed_senders: string;
  allowed_groups: string;
  default_chat_type: "private" | "group";
  max_chunk_len: number;
  report_tools: boolean;
}

const NAPCAT_DEFAULTS: NapcatConfig = {
  configured: false,
  url: "",
  access_token: "",
  allowed_senders: "",
  allowed_groups: "",
  default_chat_type: "private",
  max_chunk_len: 4500,
  report_tools: false,
};

export async function fetchNapcat(): Promise<NapcatConfig> {
  const { invoke } = bridgeActions();
  try {
    const result = (await invoke("config.get", { key: "channels.napcat" })) as {
      value?: Record<string, unknown> | null;
    };
    const node = result.value;
    if (!node || typeof node !== "object") {
      return { ...NAPCAT_DEFAULTS };
    }
    const list = (v: unknown): string =>
      Array.isArray(v) ? v.map(String).join(", ") : String(v ?? "");
    return {
      configured: true,
      url: String(node.url ?? ""),
      access_token: String(node.access_token ?? ""),
      allowed_senders: list(node.allowed_senders),
      allowed_groups: list(node.allowed_groups),
      default_chat_type: node.default_chat_type === "group" ? "group" : "private",
      max_chunk_len: clamp(node.max_chunk_len, 1, 20000, NAPCAT_DEFAULTS.max_chunk_len),
      report_tools: Boolean(node.report_tools),
    };
  } catch {
    return { ...NAPCAT_DEFAULTS };
  }
}

/** 保存 NapCat 配置（dotted 路径写回；白名单先转空则整节留空）。 */
export async function saveNapcat(config: NapcatConfig): Promise<boolean> {
  const { invoke } = bridgeActions();
  const toList = (text: string): string[] =>
    text.split(/[,，\s]+/).map((s) => s.trim()).filter(Boolean);
  try {
    await invoke("config.set", {
      updates: {
        "channels.napcat.url": config.url,
        "channels.napcat.access_token": config.access_token,
        "channels.napcat.allowed_senders": toList(config.allowed_senders),
        "channels.napcat.allowed_groups": toList(config.allowed_groups),
        "channels.napcat.default_chat_type": config.default_chat_type,
        "channels.napcat.max_chunk_len": config.max_chunk_len,
        "channels.napcat.report_tools": config.report_tools,
      },
    });
    return true;
  } catch {
    return false;
  }
}

/** 模型 API（providers 节 + active_provider）。 */
export interface ProviderEntry {
  name: string;
  api_key: string;
  base_url: string;
  model: string;
  temperature: number;
  vision: boolean;
}

export async function fetchProviders(): Promise<{
  active: string;
  providers: ProviderEntry[];
}> {
  const { invoke } = bridgeActions();
  try {
    const root = (await invoke("config.get", { key: "providers" })) as {
      value?: Record<string, Record<string, unknown>>;
    };
    const activeResult = (await invoke("config.get", {
      key: "active_provider",
    })) as { value?: unknown };
    const active = String(activeResult.value ?? "");
    const providers: ProviderEntry[] = Object.entries(root.value ?? {}).map(
      ([name, node]) => ({
        name,
        api_key: String(node?.api_key ?? ""),
        base_url: String(node?.base_url ?? ""),
        model: String(node?.model ?? ""),
        temperature: clamp(node?.temperature, 0, 2, 0.7),
        vision: Boolean(node?.vision),
      }),
    );
    return { active, providers };
  } catch {
    return { active: "", providers: [] };
  }
}

export async function saveProvider(
  name: string,
  patch: Partial<Pick<ProviderEntry, "api_key" | "base_url" | "model" | "temperature" | "vision">>,
): Promise<boolean> {
  const { invoke } = bridgeActions();
  try {
    const updates: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(patch)) {
      updates[`providers.${name}.${k}`] = v;
    }
    await invoke("config.set", { updates });
    return true;
  } catch {
    return false;
  }
}

export async function setActiveProvider(name: string): Promise<boolean> {
  const { invoke } = bridgeActions();
  try {
    await invoke("config.set", { updates: { active_provider: name } });
    return true;
  } catch {
    return false;
  }
}

/** 记忆检索测试（memory.search 的前端形态）。 */
export interface MemoryHit {
  index: number;
  text: string;
}

export async function searchMemories(
  query: string,
  nResults = 5,
): Promise<MemoryHit[]> {
  const { invoke } = bridgeActions();
  try {
    const result = (await invoke("memory.search", {
      query,
      n_results: nResults,
    })) as { memories?: unknown[] };
    return (result.memories ?? []).map((m, index) => ({
      index,
      text: typeof m === "string" ? m : JSON.stringify(m),
    }));
  } catch {
    return [];
  }
}

/** 清空记忆（危险操作；页面层负责二次确认）。 */
export async function clearMemories(): Promise<boolean> {
  const { invoke } = bridgeActions();
  try {
    await invoke("memory.clear");
    return true;
  } catch {
    return false;
  }
}

/** 权限配置（permissions 节）。 */
export interface PermissionSettings {
  sandbox_default: "read-only" | "workspace-write" | "danger-full-access";
  approval_policy: "ask" | "never";
  command_allowlist: string;
}

export async function fetchPermissions(): Promise<PermissionSettings> {
  const { invoke } = bridgeActions();
  try {
    const result = (await invoke("config.get", { key: "permissions" })) as {
      value?: Record<string, unknown>;
    };
    const node = result.value ?? {};
    const sandbox = String(node.sandbox_default ?? "read-only");
    const policy = String(node.approval_policy ?? "ask");
    const list = Array.isArray(node.command_allowlist)
      ? node.command_allowlist.map(String).join(", ")
      : "";
    return {
      sandbox_default:
        sandbox === "workspace-write" || sandbox === "danger-full-access"
          ? sandbox
          : "read-only",
      approval_policy: policy === "never" ? "never" : "ask",
      command_allowlist: list,
    };
  } catch {
    return { sandbox_default: "read-only", approval_policy: "ask", command_allowlist: "" };
  }
}

export async function savePermissions(settings: PermissionSettings): Promise<boolean> {
  const { invoke } = bridgeActions();
  const toList = (text: string): string[] =>
    text.split(/[,，\s]+/).map((s) => s.trim()).filter(Boolean);
  try {
    await invoke("config.set", {
      updates: {
        "permissions.sandbox_default": settings.sandbox_default,
        "permissions.approval_policy": settings.approval_policy,
        "permissions.command_allowlist": toList(settings.command_allowlist),
      },
    });
    return true;
  } catch {
    return false;
  }
}
