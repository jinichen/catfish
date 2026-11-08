/**
 * 专家包的「审批人试用」界面零件 (11/08, docs/SHARE-GOVERNANCE-PLAN.md §2.3 / §3.3):
 *   PendingExpertsSection —— 专家卡「待我审」面板: 待审的专家包 + 权限摘要 + 「装到我这里试用」
 *   ExpertTrialActions    —— 专家列表里试用装的那条: 「试用」标 + 回传试用结果 + 卸掉试用
 *   syncExpertTrials      —— 刷新时调: 不在待审了的试用自动卸掉 (profile 由 Rust 一起删)
 *
 * 试用安装跟正式安装走同一个确认框 (ExpertPackUi.InstallDialog, source.from = "trial"): 先看权限摘要再装。
 */
import { useEffect, useState } from "react";

import type { ExpertSummary, HubExpert } from "../../lib/tauri_experts";
import { TRIAL_STATUS_CN, trialExpertPending, trialExpertResult, trialExpertSync, type RemovedExpertTrial } from "../../lib/tauri_trial";
import { muted } from "../Collab/formsUi";
import { Btn, ErrorLine, itemStyle } from "../Collab/roomLinkUi";
import { PermissionSummary } from "./ExpertPackUi";
import { TrialTag } from "./SkillTrialUi";
import { TrialResultForm } from "./TrialResultForm";

export function removedExpertText(r: RemovedExpertTrial): string {
  const what = `${r.name} ${r.version}`;
  if (r.status === "approved") return `${what} 审核通过了, 试用安装已卸掉 —— 在「资源市场」里点「安装」转成正式安装`;
  return `${what} ${TRIAL_STATUS_CN[r.status] ?? r.status}, 试用安装已自动卸掉`;
}

export async function syncExpertTrials(): Promise<RemovedExpertTrial[]> {
  try {
    return (await trialExpertSync()).removed;
  } catch (e) {
    console.warn("[trial] 同步专家试用失败:", e);
    return [];
  }
}

/** 待我审的专家包 (只有部门主管 / 管理员拿得到) */
export function PendingExpertsSection({ onTry }: { onTry: (m: HubExpert) => void }) {
  const [items, setItems] = useState<HubExpert[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    trialExpertPending().then((r) => setItems(r.experts)).catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);
  if (error) return <ErrorLine>{error}</ErrorLine>;
  if (!items) return <span style={muted}>读取待审列表…</span>;
  if (!items.length) return <span style={muted}>没有等你审核的专家包。</span>;
  return (
    <>
      <span style={muted}>同事还看不到这些; 可以先装到自己电脑上试 (「找谁」里带「(试用)」), 通过 / 驳回在门户里点。</span>
      {items.map((m) => (
        <div key={`${m.id}@${m.version}`} style={itemStyle}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
            <span style={{ fontSize: 13, fontWeight: 600 }}>{m.name} <span style={muted}>{m.version}</span></span>
            <span style={muted}>{m.scope === "company" ? "全公司" : m.departments.join("、")} · {m.submitted_by} 提交</span>
          </div>
          <span style={muted}>{m.description}</span>
          {m.submit_note && <span style={muted}>提交人说: {m.submit_note}</span>}
          {m.summary && <PermissionSummary p={m.summary} />}
          <div><Btn kind="primary" onClick={() => onTry(m)}>装到我这里试用</Btn></div>
        </div>
      ))}
    </>
  );
}

/** 专家列表里试用装的那条的标和动作 */
export function ExpertTrialActions({ e, busy, onRemove, onDone }: {
  e: ExpertSummary; busy: boolean; onRemove: () => void; onDone: (msg: string) => void;
}) {
  const [reporting, setReporting] = useState(false);
  const trial = e.pack?.trial;
  if (!trial) return null;
  return (
    <>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <TrialTag expires={trial.expires} by={trial.by} />
        <span style={muted}>审批人试用安装 · 不会给同事, 审核有结果后自动卸掉</span>
        <Btn kind="ghost" disabled={busy || reporting} onClick={() => setReporting(true)}>回传试用结果</Btn>
        <Btn kind="ghost" disabled={busy} onClick={onRemove}>{busy ? "处理中…" : "卸掉试用"}</Btn>
      </div>
      {reporting && (
        <TrialResultForm runPrefix={`pack-${e.id}/`} onCancel={() => setReporting(false)}
                         onSubmit={async (ok, summary, runId) => {
                           const r = await trialExpertResult(e.id, ok, summary, runId);
                           setReporting(false);
                           onDone(r.summary || "试用结果已回传");
                         }} />
      )}
    </>
  );
}
