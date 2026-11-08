/** 技能审核弹窗 + 「我提交的」/「待我审核」两块 (11/08 分享治理 §3.1)。
 *
 * 照专家包那页 (ExpertsMarketPage) 的样子: 审批人看 SKILL.md 全文、文件清单、提交说明、试用结果,
 * 通过 = 中央签名上架, 驳回要写原因回给提交人。试用在 Companion 里做, 这里只看回传。
 */
import { useCallback, useEffect, useState } from "react";

import { Card } from "../components/Card";
import { Badge, BTN, BTN_DANGER, BTN_PRIMARY } from "../components/DataTable";
import { Modal } from "../components/Dialog";
import {
  getSkill,
  listMySkills,
  listReviewSkills,
  reviewSkill,
  reviewStatusLabel,
  SCOPE_LABEL,
  withdrawSkill,
  type SkillDetail,
  type SkillReview,
} from "../lib/hub";
import { TrialHint, TrialResults, when } from "./TrialResults";

const muted = { fontSize: 12, color: "var(--text-muted)" } as const;
const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));

export const scopeText = (r: Pick<SkillReview, "scope" | "departments">) =>
  r.scope === "company" ? SCOPE_LABEL.company : (r.departments || []).join("、") || SCOPE_LABEL.department;

export function ReviewBadge({ review }: { review?: SkillReview | null }) {
  const status = review && !review.legacy ? review.status : "approved";
  const tone = status === "approved" ? "ok" : status === "rejected" ? "err" : status === "pending" ? "warn" : "neutral";
  return <Badge tone={tone}>{reviewStatusLabel(review)}</Badge>;
}

const key = (r: SkillReview) => `${r.namespace}/${r.name}@${r.version}`;

export function SkillReviewDialog({ rec, onClose, onDone }: { rec: SkillReview; onClose: () => void; onDone: () => void }) {
  const ns = rec.namespace ?? "";
  const name = rec.name ?? "";
  const ver = rec.version ?? "";
  const [detail, setDetail] = useState<SkillDetail | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getSkill(ns, name, ver).then(setDetail).catch((e) => setLoadErr(errText(e)));
  }, [ns, name, ver]);

  const go = async (decision: "approve" | "reject") => {
    if (decision === "reject" && !note.trim()) {
      setError("驳回要写原因, 会回给提交人");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await reviewSkill(ns, name, ver, decision, note.trim());
      onDone();
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  };

  const trials = detail?.review?.trials ?? rec.trials;
  return (
    <Modal
      title={`审核技能 · ${name} ${ver}`}
      width={760}
      onClose={onClose}
      ready={!!detail || !!loadErr}
      lockReason={busy ? "正在提交审核结果" : undefined}
      footer={
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
          <button type="button" style={BTN} disabled={busy} onClick={onClose}>取消</button>
          <button type="button" style={BTN_DANGER} disabled={busy} onClick={() => void go("reject")}>驳回</button>
          <button type="button" style={BTN_PRIMARY} disabled={busy} onClick={() => void go("approve")}>通过并上架</button>
        </div>
      }
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <div style={muted}>
          {rec.submitted_by}{rec.submitted_dept ? ` (${rec.submitted_dept})` : ""} {when(rec.submitted_at)} 提交 · 给 {scopeText(rec)}
          {rec.file_count != null && ` · ${rec.file_count} 个文件`}
          {rec.bundle_sha256 && ` · sha256 ${rec.bundle_sha256.slice(0, 12)}…`}
        </div>
        {rec.submit_note && <div style={{ fontSize: 13 }}>提交说明：{rec.submit_note}</div>}

        <div>
          <b style={{ fontSize: 13 }}>试用</b>
          <div style={{ marginTop: 4, display: "flex", flexDirection: "column", gap: 6 }}>
            <TrialHint what="这个技能" />
            <TrialResults trials={trials} />
          </div>
        </div>

        {loadErr && <div style={{ color: "var(--status-err)", fontSize: 12 }}>拿不到技能内容：{loadErr}</div>}
        {detail && (
          <>
            <div>
              <b style={{ fontSize: 13 }}>文件 ({detail.files.length})</b>
              <ul style={{ margin: "4px 0 0", paddingLeft: 18, fontFamily: "var(--font-mono)", fontSize: 12 }}>
                {detail.files.map((f) => (
                  <li key={f}>
                    {f}
                    {detail.files_sha256?.[f] && (
                      <span style={{ marginLeft: 8, color: "var(--text-muted)", fontSize: 10 }}>
                        sha256:{detail.files_sha256[f].slice(0, 12)}…
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
            <div>
              <b style={{ fontSize: 13 }}>SKILL.md 全文</b>
              <pre
                style={{
                  marginTop: 4,
                  fontFamily: "var(--font-mono)",
                  fontSize: 12,
                  background: "var(--bg-secondary)",
                  padding: "var(--space-3)",
                  borderRadius: "var(--radius-sm)",
                  maxHeight: 320,
                  overflow: "auto",
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-word",
                }}
              >
                {detail.skill_md}
              </pre>
            </div>
          </>
        )}
        {!detail && !loadErr && <div style={muted}>加载技能内容…</div>}

        <textarea
          rows={3}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="审核意见 (驳回必填, 会回给提交人)"
          style={{ width: "100%", boxSizing: "border-box", fontFamily: "inherit", fontSize: 13, padding: 8 }}
        />
        {error && <div style={{ color: "var(--status-err)", fontSize: 12 }}>{error}</div>}
      </div>
    </Modal>
  );
}

function ReviewRow({ r, children }: { r: SkillReview; children?: React.ReactNode }) {
  return (
    <div style={{ borderTop: "1px solid var(--border)", padding: "8px 0", display: "flex", flexDirection: "column", gap: 4 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "baseline" }}>
        <span style={{ fontSize: 13, fontWeight: 600, fontFamily: "var(--font-mono)" }}>
          {r.namespace}/{r.name} <span style={muted}>{r.version}</span>
        </span>
        <span style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <ReviewBadge review={r} />
          <span style={muted}>{scopeText(r)}</span>
        </span>
      </div>
      <div style={muted}>
        {r.submitted_by} {when(r.submitted_at)} 提交
        {r.reviewed_by && ` · ${r.reviewed_by} ${when(r.reviewed_at)} ${r.status === "approved" ? "通过" : r.status === "rejected" ? "驳回" : "处理"}`}
      </div>
      {r.submit_note && <div style={{ fontSize: 12 }}>说明：{r.submit_note}</div>}
      {r.status === "rejected" && r.review_note && (
        <div style={{ fontSize: 12, color: "var(--status-err)" }}>驳回原因 ({r.reviewed_by})：{r.review_note}</div>
      )}
      {children && <div style={{ display: "flex", gap: 8 }}>{children}</div>}
    </div>
  );
}

/** 「我提交的」+ (主管 / 管理员) 「待我审核」。放在技能列表页顶部。 */
export function SkillReviewSections({ reviewer }: { reviewer: boolean }) {
  const [mine, setMine] = useState<SkillReview[] | null>(null);
  const [pending, setPending] = useState<SkillReview[] | null>(null);
  const [reviewing, setReviewing] = useState<SkillReview | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [a, b] = await Promise.all([
        listMySkills(),
        reviewer ? listReviewSkills() : Promise.resolve({ skills: [] as SkillReview[] }),
      ]);
      setMine(a.skills || []);
      setPending(b.skills || []);
      setError(null);
    } catch (e) {
      setError(errText(e));
    }
  }, [reviewer]);
  useEffect(() => { void load(); }, [load]);

  const withdraw = async (r: SkillReview) => {
    try {
      await withdrawSkill(r.namespace ?? "", r.name ?? "", r.version ?? "");
      await load();
    } catch (e) {
      setError(errText(e));
    }
  };

  // 两块都空、也不是审批人时不占地方 —— 大多数员工从没提交过
  if (!error && mine?.length === 0 && !reviewer) return null;

  return (
    <>
      {error && <div style={{ color: "var(--status-err)", fontSize: 13 }}>{error}</div>}
      {reviewer && (
        <Card title={`📝 待我审核${pending?.length ? ` (${pending.length})` : ""}`}>
          <div style={muted}>
            你管的部门 (管理员: 全公司范围) 有人提交的技能。看清楚 SKILL.md 写了什么、脚本会做什么, 通过才上架; 没通过的同事看不到。
          </div>
          {pending?.length === 0 && <div style={{ ...muted, marginTop: 8 }}>没有待审核的。</div>}
          {pending?.map((r) => (
            <ReviewRow key={key(r)} r={r}>
              <button type="button" style={BTN_PRIMARY} onClick={() => setReviewing(r)}>审核</button>
            </ReviewRow>
          ))}
        </Card>
      )}
      {mine && mine.length > 0 && (
        <Card title="📤 我提交的">
          <div style={muted}>待审核的要等部门主管 (全公司范围: 管理员) 通过后同事才能看到; 被驳回的看原因改好再发一版。</div>
          {mine.map((r) => (
            <ReviewRow key={key(r)} r={r}>
              {(r.status === "pending" || r.status === "approved") && (
                <button type="button" style={BTN} onClick={() => void withdraw(r)}>
                  {r.status === "pending" ? "撤回" : "下架"}
                </button>
              )}
            </ReviewRow>
          ))}
        </Card>
      )}
      {reviewing && (
        <SkillReviewDialog rec={reviewing} onClose={() => setReviewing(null)} onDone={() => { setReviewing(null); void load(); }} />
      )}
    </>
  );
}
