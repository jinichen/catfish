/**
 * 「回传试用结果」小表单 (11/08, docs/SHARE-GOVERNANCE-PLAN.md §2.3): 行不行 + 一句话结论 + (可选) 挑一次工作流运行附上每步结果 / 产出文件。
 * 技能和专家包共用; 真正 POST 到中央的在 tool-bridge skill_trial.py。
 */
import { useEffect, useState } from "react";

import { trialRuns } from "../../lib/tauri_trial";
import type { RunSummary } from "../../lib/tauri_workflows";
import { muted } from "../Collab/formsUi";
import { Btn, ErrorLine, inputStyle } from "../Collab/roomLinkUi";

const RUN_STATUS: Record<string, string> = {
  done: "跑完了", failed: "失败", waiting_confirm: "停下等确认", running: "还在跑", declined: "没确认", invalid: "没跑起来",
};

export function TrialResultForm({ runPrefix, onSubmit, onCancel }: {
  /** 运行记录按 skill_path 前缀筛 (技能 `ns/name`, 专家包 `pack-<id>/`); 空字符串 = 不提供挑运行记录 */
  runPrefix: string;
  onSubmit: (ok: boolean, summary: string, runId: string | null) => Promise<void>;
  onCancel: () => void;
}) {
  const [ok, setOk] = useState<boolean | null>(null);
  const [summary, setSummary] = useState("");
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [runId, setRunId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!runPrefix) return;
    trialRuns(runPrefix).then((r) => {
      setRuns(r.runs);
      if (r.runs[0]) setRunId(r.runs[0].run_id); // 默认附最近一次
    }).catch(() => setRuns([]));
  }, [runPrefix]);

  async function go() {
    if (ok === null) {
      setError("先选「能用」还是「不行」");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await onSubmit(ok, summary.trim(), runId || null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6, padding: "8px 10px", border: "1px dashed var(--catfish-border)", borderRadius: 6 }}>
      <strong style={{ fontSize: 12 }}>回传试用结果 (记进中央的审核记录, 审核页能看到)</strong>
      <div style={{ display: "flex", gap: 16, fontSize: 12 }}>
        <label style={{ display: "flex", gap: 4 }}>
          <input type="radio" checked={ok === true} onChange={() => setOk(true)} />能用
        </label>
        <label style={{ display: "flex", gap: 4 }}>
          <input type="radio" checked={ok === false} onChange={() => setOk(false)} />不行
        </label>
      </div>
      <input value={summary} onChange={(e) => setSummary(e.target.value)} style={inputStyle} maxLength={300}
             placeholder="一句话结论, 例「登录那步能过, 导出表格少了一列」" />
      {runs.length > 0 && (
        <label style={{ fontSize: 12, display: "flex", flexDirection: "column", gap: 2 }}>
          <span style={muted}>附上哪次运行的每步结果和产出文件 (可不附)</span>
          <select value={runId} onChange={(e) => setRunId(e.target.value)} style={{ ...inputStyle, padding: "4px 8px", fontSize: 12 }}>
            <option value="">不附</option>
            {runs.map((r) => (
              <option key={r.run_id} value={r.run_id}>
                {r.title || r.skill_path} · {RUN_STATUS[r.status] ?? r.status} · {new Date(r.created_at).toLocaleString("zh-CN", { hour12: false }).slice(0, -3)}
              </option>
            ))}
          </select>
        </label>
      )}
      {error && <ErrorLine>{error}</ErrorLine>}
      <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
        <Btn kind="ghost" disabled={busy} onClick={onCancel}>取消</Btn>
        <Btn kind="primary" disabled={busy} onClick={() => void go()}>{busy ? "回传中…" : "回传"}</Btn>
      </div>
    </div>
  );
}
