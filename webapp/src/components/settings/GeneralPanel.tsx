import { useEffect, useState } from "react";
import {
  GhostButton,
  PrimaryButton,
  SectionTitle,
  SettingRow,
  SwitchRow,
  Toast,
} from "./controls";
import type { ShellState } from "../../../electron/preload-api";

/**
 * 通用页：开机自启动、后台静默运行、关闭行为、数据目录。
 *
 * 为什么要在这里集中：自启动与「静默」是一对——只在开机时静默启动、
 * 不弹窗地驻留托盘，才谈得上「开机自动跑起来陪着你」。数据目录同理：
 * 出问题时用户需要能一键打开 config.yaml 所在目录。
 */
export function GeneralPanel() {
  const [shell, setShell] = useState<ShellState | null>(null);
  const [toast, setToast] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const notify = (ok: boolean, text: string) => {
    setToast({ ok, text });
    setTimeout(() => setToast(null), 4000);
  };

  useEffect(() => {
    void window.castorice.getShellState().then(setShell);
    const off = window.castorice.onShellStateChanged((state) => setShell(state));
    return off;
  }, []);

  if (!shell) {
    return <div className="p-6 text-sm" style={{ color: "var(--c-muted)" }}>加载中…</div>;
  }

  const toggleAutostart = async (enabled: boolean) => {
    setBusy(true);
    const result = await window.castorice.setAutostart(enabled);
    setBusy(false);
    if (result.shell) setShell(result.shell);
    if (result.enabled === enabled) {
      notify(true, enabled ? "已设置开机自动启动" : "已取消开机自动启动");
    } else {
      notify(false, "设置失败：" + (result.error ?? "无法写入启动项"));
    }
  };

  const toggleSilent = async (enabled: boolean) => {
    const state = await window.castorice.setSilentStart(enabled);
    setShell(state);
    notify(
      true,
      enabled
        ? "开机自启动时将不弹主窗口，直接驻留托盘后台运行"
        : "开机自启动时会正常显示主窗口",
    );
  };

  const toggleCloseToTray = async (enabled: boolean) => {
    await window.castorice.setCloseAction(enabled);
    setShell({ ...shell, closeToTray: enabled });
    notify(true, enabled ? "关闭窗口时将隐藏到托盘" : "关闭窗口时将退出程序");
  };

  const restart = async () => {
    setBusy(true);
    const result = await window.castorice.restartBackend();
    setBusy(false);
    notify(result.ok, result.ok ? "后端已重启" : "重启失败：" + (result.error ?? ""));
  };

  return (
    <div>
      <SectionTitle text="启动" />
      <SwitchRow
        title="开机自动启动"
        hint={
          shell.autostart.supported
            ? "登录 Windows 后自动运行（可在任务管理器的「启动」里看到）"
            : "当前系统不支持自动登记，请手动添加启动项"
        }
        checked={shell.autostart.enabled}
        onChange={(value) => void toggleAutostart(value)}
      />
      <SwitchRow
        title="后台静默启动"
        hint="开机自启动时不弹主窗口，直接驻留托盘；点托盘图标即可回到界面"
        checked={shell.silentStart}
        onChange={(value) => void toggleSilent(value)}
      />
      <SwitchRow
        title="关闭窗口时最小化到托盘"
        hint="关闭后程序继续在后台运行，主动聊天与 QQ 渠道不受影响；真正退出请用托盘菜单的「退出」"
        checked={shell.closeToTray}
        onChange={(value) => void toggleCloseToTray(value)}
      />
      {shell.autostart.error && (
        <Toast text={"启动项写入失败：" + shell.autostart.error} ok={false} />
      )}
      {shell.autostartLaunch && (
        <div className="mt-2 text-[11.5px]" style={{ color: "var(--c-muted)" }}>
          本次是由开机自启动拉起的
          {shell.silentLaunch ? "（已静默在后台运行）" : "（已显示主窗口）"}。
        </div>
      )}

      <SectionTitle text="数据与维护" />
      <SettingRow
        title="数据目录"
        hint="配置、用户档案、记忆、对话历史都在这里（覆盖更新不会丢失）"
      >
        <span
          className="max-w-[280px] truncate text-[11.5px]"
          style={{ color: "var(--c-muted)" }}
          title={shell.dataRoot}
        >
          {shell.dataRoot}
        </span>
      </SettingRow>
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <GhostButton onClick={() => void window.castorice.openDataDir()}>
          打开数据目录
        </GhostButton>
        <PrimaryButton onClick={() => void restart()} disabled={busy}>
          {busy ? "处理中…" : "重启后端"}
        </PrimaryButton>
        <span className="text-[11.5px]" style={{ color: "var(--c-muted)" }}>
          改动渠道（NapCat）配置后需要重启后端才生效
        </span>
      </div>

      {toast && <Toast text={toast.text} ok={toast.ok} />}
    </div>
  );
}
