/** 连接器「我提交的」+ (主管 / 管理员) 「待我审核」+ 审核弹窗 (11/08 分享治理 §3.4)。
 *
 * 跟专家包 / 技能那两页一个样子: 审批人看 manifest 原文、工具清单、提交说明、测试账号怎么拿、试用结果;
 * 通过 = 写进 registry (allowed_dept 按范围填), 驳回要写原因回给提交人。
 * 试用在 Companion 里做 (订阅时带 trial), 只有提交人写了「测试账号怎么拿」才能试。
 */
import { useCallback, useEffect, useState } from "react";

import { Card } from "../components/Card";
import { Badge, BTN, BTN_DANGER, BTN_PRIMARY } from "../components/DataTable";
import { Modal } from "../components/Dialog";
import { REVIEW_STATUS_LABEL, SCOPE_LABEL } from "../lib/hub";
import { getSubmission, listMySubmissions, listReviewSubmissions, reviewSubmission, type McpSubmission } from "../lib/mcp";
import { TrialHint, TrialResults, when } from "./TrialResults";

const muted = { fontSize: 12, color: "var(--text-muted)" } as const;
const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));
const scopeText = (s: McpSubmission) => (s.scope === "company" ? SCOPE_LABEL.company : s.departments.join("、") || SCOPE_LABEL.department);

function StatusBadge({ s }: { s: McpSubmission }) {
  const tone = s.status === "approved" ? "ok" : s.status === "rejected" ? "err" : s.status === "pending" ? "warn" : "neutral";
  return <Badge tone={tone}>{REVIEW_STATUS_LABEL[s.status] ?? s.status}</Badge>;
}

export function McpReviewDialog({ s, onClose, onDone }: { s: McpSubmission; onClose: () => void; onDone: () => void }) {
  const [full, setFull] = useState<{ submission: McpSubmission; manifest_yaml: string } | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getSubmission(s.id).then(setFull).catch((e) => setLoadErr(errText(e)));
  }, [s.id]);

  const go = async (decision: "approve" | "reject") => {
    if (decision === "reject" && !note.trim()) {
      setError("驳回要写原因, 会回给提交人");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await reviewSubmission(s.id, decision, note.trim());
      onDone();
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  };

  const sub = full?.submission ?? s;
  return (
    <Modal
      title={`审核连接器 · ${s.name} ${s.version}`}
      width={760}
      onClose={onClose}
      ready={!!full || !!loadErr}
      lockReason={busy ? "正在提交审核结果" : undefined}
      footer={
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
          <button type="button" style={BTN} disabled={busy} onClick={onClose}>取消</button>
          <button type="button" style={BTN_DANGER} disabled={busy} onClick={() => void go("reject")}>驳回</button>
          <button type="button" style={BTN_PRIMARY} disabled={busy} onClick={() => void go("approve")}>通过</button>
        </div>
      }
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <div style={muted}>
          {sub.submitted_by}{sub.submitted_dept ? ` (${sub.submitted_dept})` : ""} {when(sub.submitted_at)} 提交 · 给 {scopeText(sub)} ·
          {" "}{sub.tools?.length ?? 0} 个工具 · 授权方式 {sub.auth_type}
        </div>
        {sub.description && <div style={{ fontSize: 13 }}>{sub.description}</div>}
        {sub.submit_note && <div style={{ fontSize: 13 }}>提交说明：{sub.submit_note}</div>}
        <div style={{ fontSize: 13 }}>
          <b>测试账号怎么拿：</b>
          {sub.trial_credentials ? (
            <span>{sub.trial_credentials}</span>
          ) : (
            <span style={{ color: "var(--status-warn)" }}>提交人没写 —— 只能看清单，不能试用。</span>
          )}
        </div>
        {sub.tools?.length ? (
          <div>
            <b style={{ fontSize: 13 }}>工具 ({sub.tools.length})</b>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 4 }}>
              {sub.tools.map((t) => (
                <code key={t} style={{ background: "var(--bg-secondary)", padding: "1px 6px", borderRadius: 3, fontSize: 12 }}>{t}</code>
              ))}
            </div>
          </div>
        ) : null}
        <div>
          <b style={{ fontSize: 13 }}>试用</b>
          <div style={{ marginTop: 4, display: "flex", flexDirection: "column", gap: 6 }}>
            {sub.trial_credentials ? <TrialHint what="这个连接器" /> : null}
            <TrialResults trials={sub.trials} />
          </div>
        </div>
        {loadErr && <div style={{ color: "var(--status-err)", fontSize: 12 }}>拿不到清单原文：{loadErr}</div>}
        {full && (
          <div>
            <b style={{ fontSize: 13 }}>清单原文 (manifest)</b>
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
              {full.manifest_yaml}
            </pre>
          </div>
        )}
        {!full && !loadErr && <div style={muted}>加载清单…</div>}
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

function Row({ s, children }: { s: McpSubmission; children?: React.ReactNode }) {
  return (
    <div style={{ borderTop: "1px solid var(--border)", padding: "8px 0", display: "flex", flexDirection: "column", gap: 4 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "baseline" }}>
        <span style={{ fontSize: 13, fontWeight: 600 }}>
          {s.name} <span style={{ ...muted, fontFamily: "var(--font-mono)" }}>{s.id} · {s.version}</span>
        </span>
        <span style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <StatusBadge s={s} />
          <span style={muted}>{scopeText(s)}</span>
        </span>
      </div>
      {s.description && <div style={{ fontSize: 12 }}>{s.description}</div>}
      <div style={muted}>
        {s.submitted_by} {when(s.submitted_at)} 提交
        {s.reviewed_by && ` · ${s.reviewed_by} ${when(s.reviewed_at)} ${s.status === "approved" ? "通过" : s.status === "rejected" ? "驳回" : "处理"}`}
        {!s.trial_credentials && s.status === "pending" && " · 没写测试账号, 审批人不能试用"}
      </div>
      {s.status === "rejected" && s.review_note && (
        <div style={{ fontSize: 12, color: "var(--status-err)" }}>驳回原因 ({s.reviewed_by})：{s.review_note}</div>
      )}
      {children && <div style={{ display: "flex", gap: 8 }}>{children}</div>}
    </div>
  );
}

export function McpReviewSections({ reviewer }: { reviewer: boolean }) {
  const [mine, setMine] = useState<McpSubmission[] | null>(null);
  const [pending, setPending] = useState<McpSubmission[] | null>(null);
  const [reviewing, setReviewing] = useState<McpSubmission | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [a, b] = await Promise.all([
        listMySubmissions(),
        reviewer ? listReviewSubmissions() : Promise.resolve({ submissions: [] as McpSubmission[] }),
      ]);
      setMine(a.submissions || []);
      setPending(b.submissions || []);
      setError(null);
    } catch (e) {
      setError(errText(e));
    }
  }, [reviewer]);
  useEffect(() => { void load(); }, [load]);

  if (!error && mine?.length === 0 && !reviewer) return null;

  return (
    <>
      {error && <div style={{ color: "var(--status-err)", fontSize: 13 }}>{error}</div>}
      {reviewer && (
        <Card title={`📝 待我审核${pending?.length ? ` (${pending.length})` : ""}`}>
          <div style={muted}>
            你管的部门 (管理员: 全公司范围) 有人提交的连接器。看清楚它连的是什么系统、暴露哪些工具, 通过后同事才能订阅。
          </div>
          {pending?.length === 0 && <div style={{ ...muted, marginTop: 8 }}>没有待审核的。</div>}
          {pending?.map((s) => (
            <Row key={s.id} s={s}>
              <button type="button" style={BTN_PRIMARY} onClick={() => setReviewing(s)}>审核</button>
            </Row>
          ))}
        </Card>
      )}
      {mine && mine.length > 0 && (
        <Card title="📤 我提交的">
          <div style={muted}>待审核的要等部门主管 (全公司范围: 管理员) 通过后同事才能订阅; 被驳回的看原因改好再提交。</div>
          {mine.map((s) => <Row key={s.id} s={s} />)}
        </Card>
      )}
      {reviewing && (
        <McpReviewDialog s={reviewing} onClose={() => setReviewing(null)} onDone={() => { setReviewing(null); void load(); }} />
      )}
    </>
  );
}
