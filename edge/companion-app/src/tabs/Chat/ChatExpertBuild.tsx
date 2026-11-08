/**
 * 对话里「一句话搭专家」的两处 (11/08, docs/EXPERT-PACK-PLAN.md §5):
 *   - 小鲶调 catfish_workflow action=build_expert 起草好总览卡 → 「打开总览卡」按钮 (保存生效只能在卡上点)
 *   - 教「还不会」的那件, 凝固成功 (catfish_freeze_skill 结果里有 reapply_experts) → 专家自动重新生效一次,
 *     新学会的技能马上能用。每次凝固只做一次 (记在本机, 翻历史对话不会再触发)。
 */
import { useEffect, useState } from "react";

import { refreshExperts } from "../../lib/expertChat";
import { expertApply } from "../../lib/tauri_experts";
import { useUIStore } from "../../store/ui";

function parse(text: string): Record<string, unknown> | null {
  try {
    const v = JSON.parse(text);
    return v && typeof v === "object" ? (v as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

export function BuildOverviewButton({ resultText }: { resultText: string }) {
  const r = parse(resultText);
  const id = typeof r?.build_id === "string" ? r.build_id : null;
  if (!id) return null;
  return (
    <div style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 12, padding: "6px 0" }}>
      <button type="button" onClick={() => useUIStore.getState().openExpertBuild(id)}
              style={{ padding: "4px 12px", fontSize: 12, borderRadius: "var(--radius-sm)", border: "none",
                       background: "var(--catfish-cyan)", color: "white", cursor: "pointer" }}>
        打开总览卡
      </button>
      <span style={{ color: "var(--catfish-text-muted)" }}>在「设置 → 技能 → 专家」里看过、勾选, 点「全部保存并生效」</span>
    </div>
  );
}

const DONE_KEY = "catfish:expert-reapplied:v1";

function doneSet(): Set<string> {
  try {
    return new Set(JSON.parse(localStorage.getItem(DONE_KEY) || "[]") as string[]);
  } catch {
    return new Set();
  }
}

function markDone(key: string) {
  try {
    const s = [...doneSet(), key].slice(-200);
    localStorage.setItem(DONE_KEY, JSON.stringify(s));
  } catch {
    /* 记不住也只是可能多重新生效一次 */
  }
}

/** 凝固结果里的 reapply_experts → 自动重新生效 (一次) */
export function TaughtReapply({ callId, resultText }: { callId: string; resultText: string }) {
  const r = parse(resultText);
  const ids = Array.isArray(r?.reapply_experts) ? (r!.reapply_experts as string[]) : [];
  const attached = Array.isArray(r?.attached) ? (r!.attached as string[]) : [];
  const key = `${callId}:${ids.join(",")}`;
  const [state, setState] = useState<"idle" | "busy" | "done" | "error">(() => (doneSet().has(key) ? "done" : "idle"));
  const [error, setError] = useState<string | null>(null);

  const apply = async () => {
    setState("busy");
    setError(null);
    try {
      for (const id of ids) await expertApply(id);
      await refreshExperts();
      markDone(key);
      setState("done");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setState("error");
    }
  };
  useEffect(() => {
    if (ids.length && state === "idle") void apply();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  if (typeof r?.attach_error === "string") {
    return <div style={{ fontSize: 12, padding: "6px 0", color: "var(--status-warn, #d97706)" }}>{r.attach_error}</div>;
  }
  if (!attached.length) return null;
  return (
    <div style={{ fontSize: 12, padding: "6px 0", color: state === "error" ? "var(--status-error, #dc2626)" : "var(--status-ok, #16a34a)",
                  display: "flex", gap: 8, alignItems: "center" }}>
      {state === "busy" && <span style={{ color: "var(--catfish-text-muted)" }}>已接进 {attached.join("、")}, 专家重新生效中…</span>}
      {state === "done" && <span>✓ 已接进 {attached.join("、")}, 专家已经会了</span>}
      {state === "idle" && !ids.length && <span>✓ 已接进 {attached.join("、")} (专家还没保存, 保存时一起生效)</span>}
      {state === "error" && (
        <>
          <span>接上了, 但专家没重新生效: {error}</span>
          <button type="button" onClick={() => void apply()}
                  style={{ border: "none", background: "transparent", color: "var(--catfish-cyan)", cursor: "pointer", fontSize: 12, padding: 0 }}>
            重试
          </button>
        </>
      )}
    </div>
  );
}
