/**
 * 技能的「审批人试用」界面零件 (11/08, docs/SHARE-GOVERNANCE-PLAN.md §2.3):
 *   PendingSkillsSection —— 技能分享卡里「待我审」的技能 + 「装到我这里试用」
 *   TrialSkillsList      —— 我的技能卡里「试用中」的技能: 「试用」标 + 回传试用结果 + 卸掉试用
 *   syncSkillTrials      —— 市场刷新时调: 不在待审了的试用自动卸掉, 返回给员工看的一句话
 *
 * 中央那边只给审批人回 `/skills/review` (不是审批人就是空列表), 所以这里拿到空就什么都不显示。
 */
import { useEffect, useState } from "react";

import {
  TRIAL_STATUS_CN, trialDaysLeft, trialSkillInstall, trialSkillList, trialSkillPending, trialSkillResult, trialSkillSync,
  trialSkillUninstall, type PendingSkill, type RemovedSkillTrial, type SkillTrial,
} from "../../lib/tauri_trial";
import { muted } from "../Collab/formsUi";
import { Btn, ErrorLine, itemStyle } from "../Collab/roomLinkUi";
import { TrialResultForm } from "./TrialResultForm";

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** 「试用」小标 (技能 / 专家共用) */
export function TrialTag({ expires, by }: { expires?: number; by?: string }) {
  const days = expires ? trialDaysLeft(expires) : null;
  return (
    <span title={`审批人试用安装${by ? ` (${by})` : ""}${days !== null ? `, 还有 ${days} 天到期` : ""}; 只有你能用, 不会给同事, 也不能再发布`}
          style={{ fontSize: 10, padding: "0 6px", borderRadius: 999, border: "1px solid var(--status-warn, #d97706)",
                   color: "var(--status-warn, #d97706)", whiteSpace: "nowrap" }}>
      试用{days !== null && days <= 2 ? ` · ${days} 天到期` : ""}
    </span>
  );
}

/** 刷新时自动卸掉的试用 → 给员工看的一句话 (通过的提示可转正式) */
export function removedSkillText(r: RemovedSkillTrial): string {
  const what = `${r.title || r.name} ${r.version}`;
  if (r.status === "approved") return `${what} 审核通过了, 试用安装已卸掉 —— 在技能分享里点「安装」转成正式安装`;
  return `${what} ${TRIAL_STATUS_CN[r.status] ?? r.status}, 试用安装已自动卸掉`;
}

export async function syncSkillTrials(): Promise<RemovedSkillTrial[]> {
  try {
    return (await trialSkillSync()).removed;
  } catch (e) {
    console.warn("[trial] 同步技能试用失败:", e);
    return [];
  }
}

/** 待我审的技能 (只有部门主管 / 管理员拿得到) */
export function PendingSkillsSection({ refreshKey, onInstalled }: { refreshKey?: number; onInstalled?: (msg: string) => void }) {
  const [items, setItems] = useState<PendingSkill[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    trialSkillPending().then((r) => setItems(r.skills)).catch(() => setItems([]));
  }, [refreshKey]);

  if (!items || !items.length) return null;

  async function tryIt(s: PendingSkill) {
    const key = `${s.namespace}/${s.name}@${s.version}`;
    setBusy(key);
    setError(null);
    try {
      const r = await trialSkillInstall(s.namespace, s.name, s.version);
      onInstalled?.(r.summary || `已以试用身份装上 ${s.name} ${s.version}`);
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6, marginBottom: "var(--space-3)" }}>
      <div style={{ fontSize: 12, fontWeight: 600 }}>
        待我审核 <span style={muted}>· {items.length} 个 · 同事还看不到; 可以先装到自己电脑上试, 通过 / 驳回在门户里点</span>
      </div>
      {error && <ErrorLine>{error}</ErrorLine>}
      {items.map((s) => {
        const key = `${s.namespace}/${s.name}@${s.version}`;
        return (
          <div key={key} style={{ ...itemStyle, gap: 4 }}>
            <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "baseline" }}>
              <span style={{ fontSize: 13, fontWeight: 600, fontFamily: "var(--font-mono)" }}>
                {s.namespace}/{s.name} <span style={muted}>v{s.version}</span>
              </span>
              <span style={muted}>{s.scope === "company" ? "全公司" : (s.departments ?? []).join("、") || "本部门"} · {s.submitted_by} 提交</span>
            </div>
            {s.submit_note && <span style={muted}>提交人说: {s.submit_note}</span>}
            <div>
              <Btn kind="ghost" disabled={!!busy} onClick={() => void tryIt(s)}>
                {busy === key ? "装试用中…" : "装到我这里试用"}
              </Btn>
            </div>
          </div>
        );
      })}
    </div>
  );
}

/** 我的技能卡里的「试用中」区 */
export function TrialSkillsList({ refreshKey, onChanged }: { refreshKey?: number; onChanged?: (msg: string) => void }) {
  const [items, setItems] = useState<SkillTrial[]>([]);
  const [reporting, setReporting] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    trialSkillList().then((r) => setItems(r.trials)).catch(() => setItems([]));
  }, [refreshKey, tick]);

  if (!items.length) return null;

  async function remove(t: SkillTrial) {
    setBusy(t.skill_path);
    setError(null);
    try {
      const r = await trialSkillUninstall(t.skill_path);
      onChanged?.(`已卸掉试用 ${t.title || t.name}${r.restored ? " (试用前的正式版本已恢复)" : ""}`);
      setTick((k) => k + 1);
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div style={{ marginTop: "var(--space-3)", paddingTop: "var(--space-3)", borderTop: "1px solid var(--catfish-border)",
                  display: "flex", flexDirection: "column", gap: 6 }}>
      <div style={{ fontSize: 12, fontWeight: 600 }}>
        试用中 <span style={muted}>· 审批人试用安装, 只在你这台电脑; 审核有结果 (通过 / 驳回) 后会自动卸掉</span>
      </div>
      {error && <ErrorLine>{error}</ErrorLine>}
      {items.map((t) => (
        <div key={t.skill_path} style={{ ...itemStyle, gap: 4 }}>
          <div style={{ display: "flex", gap: 8, alignItems: "baseline", flexWrap: "wrap" }}>
            <span style={{ fontSize: 13, fontWeight: 600 }}>{t.title || t.name}</span>
            <TrialTag expires={t.expires} by={t.by} />
            <span style={{ ...muted, fontFamily: "var(--font-mono)" }}>{t.skill_path} v{t.version}</span>
            {t.expired && <span style={{ fontSize: 11, color: "var(--status-error, #dc2626)" }}>已到期, 刷新技能分享会自动卸掉</span>}
          </div>
          {t.summary && <span style={muted}>{t.summary}</span>}
          <div style={{ display: "flex", gap: 8 }}>
            <Btn kind="ghost" disabled={!!busy || reporting === t.skill_path} onClick={() => setReporting(t.skill_path)}>回传试用结果</Btn>
            <Btn kind="ghost" disabled={!!busy} onClick={() => void remove(t)}>{busy === t.skill_path ? "卸载中…" : "卸掉试用"}</Btn>
          </div>
          {reporting === t.skill_path && (
            <TrialResultForm runPrefix={t.skill_path} onCancel={() => setReporting(null)}
                             onSubmit={async (ok, summary, runId) => {
                               const r = await trialSkillResult(t.skill_path, ok, summary, runId);
                               setReporting(null);
                               onChanged?.(r.summary || "试用结果已回传");
                             }} />
          )}
        </div>
      ))}
    </div>
  );
}
