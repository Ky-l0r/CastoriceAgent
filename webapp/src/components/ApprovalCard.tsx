import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { ShieldAlert } from "lucide-react";
import { useBridgeStore } from "../store/bridge-store";
import { bridgeActions } from "../lib/bridge-binding";

/**
 * 审批卡（复用第一期语义）：ask 时中央浮层。
 * - 数据来自 approval.detail RPC（工具名 / 参数预览 / 原因 / 目标路径）；
 * - 决定走 approval.resolve（allow / deny 一次性语义）；
 * - 60s 倒计时与后端 broker.timeout 对齐——到点后端按 unavailable 拒绝，
 *   前端倒计时归零即收卡（下轮轮询发现 pending 清空）。
 */
const COUNTDOWN_S = 60;

export function ApprovalCard() {
  const approval = useBridgeStore((s) => s.approval);
  const [left, setLeft] = useState(COUNTDOWN_S);

  useEffect(() => {
    if (!approval) return;
    setLeft(COUNTDOWN_S);
    const timer = setInterval(() => {
      setLeft((v) => {
        if (v <= 1) {
          clearInterval(timer);
          // 后端超时拒绝即将发生：本地同步收卡。
          useBridgeStore.getState().setApproval(null);
          return 0;
        }
        return v - 1;
      });
    }, 1000);
    return () => clearInterval(timer);
  }, [approval]);

  const progress = approval ? left / COUNTDOWN_S : 0;

  return (
    <AnimatePresence>
      {approval && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          className="fixed inset-0 z-50 flex items-center justify-center"
          style={{ background: "var(--c-overlay-bg)" }}
        >
          <motion.div
            initial={{ scale: 0.94, y: 10 }}
            animate={{ scale: 1, y: 0 }}
            exit={{ scale: 0.96, opacity: 0 }}
            transition={{ type: "spring", stiffness: 240, damping: 22 }}
            className="w-[440px] max-w-[90vw] rounded-2xl border-2 shadow-2xl"
            style={{
              background: "var(--c-approval-bg)",
              borderColor: "var(--c-approval-border)",
            }}
          >
            <div className="flex items-center gap-2 px-5 pt-4">
              <ShieldAlert size={18} style={{ color: "var(--c-approval-border)" }} />
              <span className="text-sm font-semibold" style={{ color: "var(--c-approval-title)" }}>
                工具调用请求审批
              </span>
              <span
                className="ml-auto text-xs tabular-nums"
                style={{ color: left <= 10 ? "#f87171" : "var(--c-muted)" }}
              >
                {left}s 后自动拒绝
              </span>
            </div>
            {/* 倒计时进度条 */}
            <div className="mx-5 mt-2 h-1 overflow-hidden rounded-full" style={{ background: "var(--c-status-chip-bg)" }}>
              <div
                className="h-full rounded-full transition-[width] ease-linear"
                style={{
                  width: progress * 100 + "%",
                  background: left <= 10 ? "#f87171" : "var(--c-approval-border)",
                  transitionDuration: "1s",
                }}
              />
            </div>

            <div className="px-5 py-3 text-sm" style={{ color: "var(--c-approval-text)" }}>
              <div className="mb-1">
                <span style={{ color: "var(--c-muted)" }}>工具：</span>
                <span className="font-mono font-medium">{approval.tool}</span>
                {approval.mode && (
                  <span className="ml-2 text-xs" style={{ color: "var(--c-muted)" }}>
                    模式 {approval.mode}
                  </span>
                )}
              </div>
              {approval.reason && (
                <div className="mb-2 text-xs" style={{ color: "var(--c-muted)" }}>
                  {approval.reason}
                </div>
              )}
              {Object.keys(approval.arguments).length > 0 && (
                <div
                  className="mb-2 max-h-40 overflow-auto rounded-lg px-3 py-2 text-xs"
                  style={{ background: "var(--c-status-chip-bg)" }}
                >
                  {Object.entries(approval.arguments).map(([k, v]) => (
                    <div key={k} className="leading-relaxed">
                      <span className="font-mono font-medium">{k}</span>
                      <span className="opacity-50"> = </span>
                      <span className="break-all font-mono">{v}</span>
                    </div>
                  ))}
                </div>
              )}
              {approval.target_paths.length > 0 && (
                <div className="text-xs" style={{ color: "var(--c-muted)" }}>
                  目标路径：{approval.target_paths.join("、")}
                </div>
              )}
            </div>

            <div className="flex gap-2 px-5 pb-4">
              <button
                type="button"
                className="flex-1 rounded-xl py-2 text-sm font-medium text-white transition-colors"
                style={{ background: "var(--c-approval-allow-bg)" }}
                onClick={() => void bridgeActions().resolveApproval(approval.request_id, "allow")}
              >
                允许一次
              </button>
              <button
                type="button"
                className="flex-1 rounded-xl py-2 text-sm font-medium transition-colors"
                style={{
                  background: "var(--c-approval-deny-bg)",
                  color: "var(--c-approval-deny-text)",
                }}
                onClick={() => void bridgeActions().resolveApproval(approval.request_id, "deny")}
              >
                拒绝
              </button>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
