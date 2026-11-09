/** 技能审核弹窗 + 「我提交的」/「待我审核」的数据和表格列 (11/08 分享治理 §3.1)。
 *
 * 11/09 跟控制台统一样式: 原来是列表页顶上两张卡片 (📝 待我审核 / 📤 我提交的), 现在是列表页
 * 「已上架 / 待我审核 / 我提交的」三段切换里的两段, 用控制台的表格 —— 这里只给数据 (useSkillReviews)
 * 和列定义, 版式在 SkillsHubPage。
 *
 * 照专家包那页 (ExpertsMarketPage) 的样子: 审批人看 SKILL.md 全文、文件清单、提交说明、试用结果,
 * 通过 = 中央签名上架, 驳回要写原因回给提交人。试用在 Companion 里做, 这里只看回传。
 */
import { useCallback, useEffect, useState } from "react";

import { Badge, BTN, BTN_DANGER, BTN_PRIMARY, type Column } from "../components/DataTable";
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
import { NameCell, when as shortWhen } from "./marketShared";
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

/** 「我提交的」+ (主管 / 管理员) 「待我审核」两份列表。拉不到就带着错误, 不影响已上架那段。 */
export function useSkillReviews(reviewer: boolean) {
  const [mine, setMine] = useState<SkillReview[] | null>(null);
  const [pending, setPending] = useState<SkillReview[] | null>(null);
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
  return { mine, pending, error, reload: load, withdraw };
}

export const reviewKey = key;

const nameCol: Column<SkillReview> = {
  header: "技能",
  cell: (r) => <NameCell mono name={`${r.namespace}/${r.name}`} sub={r.submit_note ? `说明：${r.submit_note}` : null} />,
};

/** 待我审核: 谁、给谁、什么时候提交, 「审核」开弹窗 */
export function pendingColumns(onReview: (r: SkillReview) => void): Column<SkillReview>[] {
  return [
    nameCol,
    { header: "版本", width: 70, nowrap: true, cell: (r) => r.version },
    { header: "范围", width: "14%", truncate: true, cell: (r) => <span title={scopeText(r)}>{scopeText(r)}</span> },
    { header: "提交人", width: "20%", truncate: true, cell: (r) => <span title={r.submitted_by ?? ""}>{r.submitted_by}</span> },
    { header: "提交时间", width: 110, nowrap: true, cell: (r) => shortWhen(r.submitted_at) },
    {
      header: "", width: 60, cell: (r) => (
        <button type="button" style={BTN_PRIMARY} onClick={() => onReview(r)}>审核</button>
      ),
    },
  ];
}

/** 我提交的: 状态 + 谁什么时候处理的 (驳回带原因), 待审核的能撤回、已上架的能下架 */
export function mineColumns(onWithdraw: (r: SkillReview) => void): Column<SkillReview>[] {
  return [
    nameCol,
    { header: "版本", width: 70, nowrap: true, cell: (r) => r.version },
    { header: "范围", width: "14%", truncate: true, cell: (r) => <span title={scopeText(r)}>{scopeText(r)}</span> },
    { header: "状态", width: 70, cell: (r) => <ReviewBadge review={r} /> },
    {
      header: "处理", width: "26%", cell: (r) =>
        r.status === "rejected" && r.review_note ? (
          <span style={{ color: "var(--status-err)" }} title={r.review_note}>驳回原因 ({r.reviewed_by})：{r.review_note}</span>
        ) : r.reviewed_by ? (
          <span style={muted}>{r.reviewed_by} {shortWhen(r.reviewed_at)}</span>
        ) : (
          <span style={muted}>{shortWhen(r.submitted_at)} 提交</span>
        ),
    },
    {
      header: "", width: 60, cell: (r) =>
        r.status === "pending" || r.status === "approved" ? (
          <button type="button" style={BTN} onClick={() => onWithdraw(r)}>{r.status === "pending" ? "撤回" : "下架"}</button>
        ) : null,
    },
  ];
}
