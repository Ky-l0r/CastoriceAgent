import { useEffect } from "react";
import { Check, AlertCircle } from "lucide-react";
import { useBridgeStore } from "../store/bridge-store";

/** 全局轻提示：2.5s 自动消失，用于绑定失败等一次性反馈。 */
export function Toast() {
  const toast = useBridgeStore((s) => s.toast);
  const clear = useBridgeStore((s) => s.clearToast);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(clear, 2500);
    return () => clearTimeout(t);
  }, [toast, clear]);

  if (!toast) return null;

  return (
    <div
      className="ui-fade-in pointer-events-none fixed bottom-24 left-1/2 z-[60] -translate-x-1/2 rounded-full px-3.5 py-1.5 text-[12px]"
      style={{
        background: toast.ok ? "var(--c-success)" : "var(--c-danger)",
        color: "#fff",
        boxShadow: "var(--shadow-lg)",
        maxWidth: "70vw",
      }}
    >
      <span className="flex items-center gap-1.5">
        {toast.ok ? <Check size={12} /> : <AlertCircle size={12} />}
        {toast.text}
      </span>
    </div>
  );
}
