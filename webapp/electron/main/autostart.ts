import { app } from "electron";
import { spawnSync } from "node:child_process";

/**
 * 开机自启动（Windows：HKCU 的 Run 键；其它平台：Electron 自带 API）。
 *
 * 为什么不用 app.setLoginItemSettings：它在 Windows 上写的是
 * 「启动项」注册表值，但在**未安装的绿色目录包**里有已知问题（部分
 * 版本会写到 Update.exe 的路径，或者因为 exe 没签名被系统忽略），
 * 而且读回来的状态与注册表实际值可能不一致。这里直接写 HKCU\...\Run，
 * 行为可在 regedit 里核对，删除也干净。
 *
 * 注册表值的形态（Run 键由 CreateProcess 解析，路径含空格必须带引号）：
 *
 *     "C:\...\CastoriceAgent.exe" --castorice-autostart --castorice-silent
 *
 * 两个自定义开关由主进程自己识别（Electron 不消费未知参数）：
 *   --castorice-autostart   本次启动来自开机自启（用于决定是否静默）
 *   --castorice-silent      不显示主窗口，直接驻留托盘
 */

const RUN_KEY = "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run";
const REG_VALUE_NAME = "CastoriceAgent";

export const AUTOSTART_FLAG = "--castorice-autostart";
export const SILENT_FLAG = "--castorice-silent";

export interface AutostartState {
  /** 当前是否已登记开机自启动。 */
  enabled: boolean;
  /** 当前平台是否支持（非 Windows 走 Electron API，通常也可用）。 */
  supported: boolean;
  /** 写入/读取失败时的可读原因。 */
  error?: string;
}

/**
 * 该由系统执行的启动命令（打包版与源码版形态不同）。
 *
 * silent=true 时额外带上 --castorice-silent：开机自启后不弹窗口，
 * 直接驻留托盘（用户可在设置里关掉「后台静默启动」）。
 */
function launcherCommand(silent: boolean): string {
  const flags = [AUTOSTART_FLAG, ...(silent ? [SILENT_FLAG] : [])];
  const suffix = " " + flags.join(" ");
  if (app.isPackaged) return '"' + process.execPath + '"' + suffix;
  // 源码运行：electron.exe <webapp 目录> <开关...>
  return '"' + process.execPath + '" "' + app.getAppPath() + '"' + suffix;
}

function runReg(args: string[]): { ok: boolean; stdout: string; stderr: string } {
  const result = spawnSync("reg", args, {
    encoding: "utf-8",
    windowsHide: true,
  });
  return {
    ok: result.status === 0,
    stdout: String(result.stdout ?? ""),
    stderr: String(result.stderr ?? result.error?.message ?? ""),
  };
}

/** 读取当前自启动登记状态。 */
export function getAutostart(): AutostartState {
  if (process.platform !== "win32") {
    try {
      const settings = app.getLoginItemSettings();
      return { enabled: Boolean(settings.openAtLogin), supported: true };
    } catch (err) {
      return { enabled: false, supported: false, error: String(err) };
    }
  }
  const result = runReg(["query", RUN_KEY, "/v", REG_VALUE_NAME]);
  if (result.ok) return { enabled: true, supported: true };
  // 查询失败通常是「值不存在」（错误码 1），不是真的不支持。
  return { enabled: false, supported: true };
}

/**
 * 开 / 关开机自启动（返回写入后的真实状态）。
 *
 * silent 决定命令行里是否带 --castorice-silent（「后台静默启动」开关）。
 * 已启用时改这个开关，需要重新登记一次才能生效——见主进程
 * app:set-silent-start 的处理。
 */
export function setAutostart(enabled: boolean, silent = true): AutostartState {
  if (process.platform !== "win32") {
    try {
      app.setLoginItemSettings({
        openAtLogin: enabled,
        args: [AUTOSTART_FLAG, ...(enabled && silent ? [SILENT_FLAG] : [])],
      });
      return getAutostart();
    } catch (err) {
      return { enabled: false, supported: false, error: String(err) };
    }
  }

  const command = launcherCommand(silent);
  const result = enabled
    ? runReg([
        "add", RUN_KEY, "/v", REG_VALUE_NAME, "/t", "REG_SZ",
        "/d", command, "/f",
      ])
    : runReg(["delete", RUN_KEY, "/v", REG_VALUE_NAME, "/f"]);

  // 删除一个本来就不存在的值会返回非零，这是正常情况；
  // 以「读回来的真实状态」为准，而不是以退出码为准。
  const state = getAutostart();
  if (enabled && !state.enabled) {
    return {
      enabled: false,
      supported: true,
      error: (result.stderr || "写入注册表失败").trim(),
    };
  }
  return state;
}

/** 本次启动是否来自开机自启。 */
export function isAutostartLaunch(argv: string[] = process.argv): boolean {
  return argv.includes(AUTOSTART_FLAG);
}

/** 本次启动是否要求静默（不弹窗，直接驻留托盘）。 */
export function isSilentLaunch(argv: string[] = process.argv): boolean {
  return argv.includes(SILENT_FLAG);
}
