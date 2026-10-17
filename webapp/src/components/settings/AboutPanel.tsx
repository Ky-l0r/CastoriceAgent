import { SectionTitle } from "./controls";

/** 关于页：版本与构建信息（静态展示）。 */
export function AboutPanel() {
  return (
    <div>
      <SectionTitle text="关于" />
      <div className="mt-2 space-y-1 text-sm" style={{ color: "var(--c-bubble-text)" }}>
        <div className="text-lg font-semibold">CastoriceAgent</div>
        <div style={{ color: "var(--c-muted)" }}>版本 v1.4（第三期 · Web UI 改版）</div>
        <div className="mt-3 leading-relaxed" style={{ color: "var(--c-muted)" }}>
          Electron 壳 + Python 后端：主界面聊天流、分类设置中心、
          用户档案（喂给记忆与提示词）、NapCat 渠道、主动聊天调度。
          Qt 版已在 v1.4 退役，界面全部由 Web 前端承担。
        </div>
      </div>
    </div>
  );
}
