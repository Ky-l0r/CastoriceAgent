import { useState } from "react";
import {
  clearMemories,
  searchMemories,
  type MemoryHit,
} from "../../lib/settings-api";
import { DangerButton, GhostButton, PrimaryButton, SectionTitle, TextFieldRow, Toast } from "./controls";

/**
 * 记忆页：检索测试（memory.search）+ 清空（memory.clear）。
 * 清空是危险操作：两段式确认（点一下变成确认态，再点才执行，
 * 3 秒不点自动复位），不做浏览器 confirm（Electron 里观感割裂）。
 */
export function MemoryPanel() {
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<MemoryHit[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [toast, setToast] = useState<{ ok: boolean; text: string } | null>(null);

  const search = async () => {
    const text = query.trim();
    if (!text) return;
    setSearching(true);
    const result = await searchMemories(text, 5);
    setSearching(false);
    setHits(result);
  };

  const doClear = async () => {
    const ok = await clearMemories();
    setConfirming(false);
    setToast({
      ok,
      text: ok ? "记忆已全部清空" : "清空失败：后端拒绝了请求",
    });
    setHits(null);
    setTimeout(() => setToast(null), 2500);
  };

  // 二次确认自动复位
  if (confirming) {
    setTimeout(() => setConfirming(false), 3000);
  }

  return (
    <div>
      <SectionTitle text="检索测试（看 AI 能想起什么）" />
      <div className="flex items-end gap-3">
        <div className="min-w-0 flex-1">
          <TextFieldRow
            title="检索关键词"
            hint="模拟一轮记忆检索，返回最相关的若干条"
            value={query}
            placeholder="如：上周聊过的电影"
            onChange={setQuery}
          />
        </div>
        <div className="pb-3">
          <PrimaryButton onClick={() => void search()} disabled={searching || !query.trim()}>
            {searching ? "检索中…" : "检索"}
          </PrimaryButton>
        </div>
      </div>
      {hits !== null && (
        <div className="mt-2 space-y-2">
          {hits.length === 0 ? (
            <div className="text-sm" style={{ color: "var(--c-muted)" }}>
              没有命中任何记忆。
            </div>
          ) : (
            hits.map((hit) => (
              <div
                key={hit.index}
                className="rounded-xl border px-3 py-2 text-sm"
                style={{
                  background: "var(--c-tool-bg)",
                  borderColor: "var(--c-input-border)",
                  color: "var(--c-bubble-text)",
                }}
              >
                {hit.text}
              </div>
            ))
          )}
        </div>
      )}

      <SectionTitle text="危险区" />
      <div
        className="mt-2 rounded-xl border border-rose-500/30 p-4"
        style={{ background: "var(--c-tool-bg)" }}
      >
        <div className="text-sm font-medium text-rose-400">清空全部记忆</div>
        <div className="mt-1 text-xs" style={{ color: "var(--c-muted)" }}>
          删除向量库中的所有记忆条目，不可撤销。
        </div>
        <div className="mt-3 flex items-center gap-3">
          {confirming ? (
            <>
              <DangerButton onClick={() => void doClear()}>
                确认清空（不可撤销）
              </DangerButton>
              <GhostButton onClick={() => setConfirming(false)}>取消</GhostButton>
            </>
          ) : (
            <DangerButton onClick={() => setConfirming(true)}>清空记忆…</DangerButton>
          )}
        </div>
      </div>
      {toast && <Toast text={toast.text} ok={toast.ok} />}
    </div>
  );
}
