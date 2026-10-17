/**
 * 工作流对外步骤的确认卡 (10/17, docs/WORKFLOW-SKILL-PLAN.md §5)。
 *
 * 挂在每个页面顶上: 定时任务跑到「发收集表 / 推送到微信 / 发布知识库」这类对外步骤会停下等人,
 * 员工在哪个页面都要看得到。卡上写清**将要发什么** (解析好的参数), 点「确认发送」才继续 ——
 * 这是唯一的确认入口, 小鲶的工具里没有确认动作。
 */
import { useState } from "react";

import { dropPending, refreshPending, useWorkflowPending } from "../lib/workflowStore";
import { workflowConfirm, type RunSummary } from "../lib/tauri_workflows";

function previewLines(v: unknown): [string, string][] {
  if (v && typeof v === "object" && !Array.isArray(v)) {
    return Object.entries(v as Record<string, unknown>).map(([k, x]) => [
      k,
      typeof x === "string" ? x : JSON.stringify(x),
    ]);
  }
  return v == null ? [] : [["内容", typeof v === "string" ? v : JSON.stringify(v)]];
}

function Item({ run }: { run: RunSummary }) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const p = run.pending;
  async function decide(approve: boolean) {
    setBusy(true);
    setMsg(null);
    try {
      const r = await workflowConfirm(run.run_id, approve);
      dropPending(run.run_id);
      setMsg(r.summary ?? (approve ? "已确认" : "没发"));
      void refreshPending();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  if (!p) return null;
  return (
    <div style={{ border: "1px solid var(--status-warn, #d97706)", borderRadius: "var(--radius-sm, 6px)",
                  padding: "8px 12px", background: "rgba(217, 119, 6, 0.06)", display: "flex", flexDirection: "column", gap: 6 }}>
      <div style={{ fontSize: 13, fontWeight: 600 }}>
        工作流《{run.title}》第 {p.index + 1} 步「{p.label}」要对外发送{p.for_each_count ? ` (共 ${p.for_each_count} 次)` : ""}
      </div>
      <div style={{ fontSize: 12, display: "grid", gridTemplateColumns: "max-content 1fr", gap: "2px 10px" }}>
        {previewLines(p.preview).map(([k, v]) => (
          <div key={k} style={{ display: "contents" }}>
            <span style={{ color: "var(--catfish-text-muted)" }}>{k}</span>
            <span style={{ whiteSpace: "pre-wrap", wordBreak: "break-all", maxHeight: 120, overflow: "auto" }}>{v}</span>
          </div>
        ))}
      </div>
      <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", alignItems: "center" }}>
        {msg && <span style={{ fontSize: 11, color: "var(--catfish-text-muted)", marginRight: "auto" }}>{msg}</span>}
        <button type="button" disabled={busy} onClick={() => void decide(false)}
                style={{ padding: "4px 12px", fontSize: 12, borderRadius: "var(--radius-sm)", border: "1px solid var(--catfish-border)",
                         background: "transparent", color: "var(--catfish-text)", cursor: "pointer" }}>不发</button>
        <button type="button" disabled={busy} onClick={() => void decide(true)}
                style={{ padding: "4px 12px", fontSize: 12, borderRadius: "var(--radius-sm)", border: "none",
                         background: "var(--catfish-cyan)", color: "white", cursor: "pointer" }}>确认发送</button>
      </div>
    </div>
  );
}

export default function WorkflowConfirmBanner() {
  const pending = useWorkflowPending();
  if (pending.length === 0) return null;
  return (
    <div style={{ padding: "8px 16px", display: "flex", flexDirection: "column", gap: 8 }} role="region" aria-label="等你确认的工作流">
      {pending.map((r) => <Item key={r.run_id} run={r} />)}
    </div>
  );
}
