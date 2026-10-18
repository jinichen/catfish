/** /market/experts — 专家包 (10/18, docs/EXPERT-PACK-PLAN.md §5)。
 *
 * 三块:
 *   · 已上架: 本部门 / 全公司能装的 (服务器按部门过滤, 下载接口也拦); 在 Companion 里装
 *   · 待审核 (主管 / 管理员才有): 看清单、选路、每个工作流的步骤、脚本里会打开的网站 —— 都是服务器自己从包里拆的,
 *     不是提交人自报; 通过 = 中央签名上架, 驳回要写原因, 回到提交人
 *   · 我提交的: 状态、驳回原因、撤回 / 下架
 */
import { useCallback, useEffect, useState } from "react";

import { Card } from "../components/Card";
import { Badge, BTN, BTN_DANGER, BTN_PRIMARY } from "../components/DataTable";
import { Modal } from "../components/Dialog";
import { roleAllows } from "../components/RoleGate";
import {
  BLOCK_LABEL, STATUS_LABEL, listMarketExperts, listMyExperts, listReviewExperts, reviewExpert, withdrawExpert,
  type ExpertPackMeta,
} from "../lib/experts";
import { useAuthStore } from "../store/auth";

const muted = { fontSize: 12, color: "var(--text-muted)" } as const;
const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));
const scopeText = (m: ExpertPackMeta) => (m.scope === "company" ? "全公司" : m.departments.join("、") || "—");
/** 中央存的是 UTC (…+00:00), 显示成本地时间 */
const when = (t?: string) => (t ? new Date(t).toLocaleString("zh-CN", { hour12: false }).slice(0, -3) : "");

function Perms({ m }: { m: ExpertPackMeta }) {
  const s = m.summary ?? {};
  const row = (label: string, items: string[] | undefined, color?: string) =>
    items?.length ? <div style={{ fontSize: 12 }}><b style={{ color }}>{label}:</b> {items.join("、")}</div> : null;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
      {row("查", s.read)}
      {row("写员工电脑上的东西", s.local)}
      {row("对外 (跑到时停下等员工确认)", s.outward, "var(--status-warn)")}
      {row("会打开的网站", m.sites)}
      {m.allow_code && <div style={{ fontSize: 12, color: "var(--status-err)" }}>⚠️ 要用终端 / 执行代码</div>}
      {m.allow_web && <div style={{ fontSize: 12 }}>要上网搜索</div>}
    </div>
  );
}

/** 审核看的全部: 选路、核对、工作流步骤、网站。 */
function Detail({ m }: { m: ExpertPackMeta }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={muted}>
        {m.submitted_by} ({m.submitted_dept}) {when(m.submitted_at)} 提交 · 给 {scopeText(m)} · {m.file_count} 个文件 ·
        sha256 {m.sha256.slice(0, 12)}…
      </div>
      {m.submit_note && <div style={{ fontSize: 13 }}>提交说明: {m.submit_note}</div>}
      <div><b style={{ fontSize: 13 }}>它会用到</b><Perms m={m} /></div>
      <div>
        <b style={{ fontSize: 13 }}>选路 (什么事走哪条路)</b>
        {m.routes.map((r, i) => (
          <div key={i} style={{ fontSize: 12 }}>· {r.when} → <code>{r.use}</code>{r.ask?.length ? ` (先问: ${r.ask.join("; ")})` : ""}</div>
        ))}
      </div>
      {m.checks.length > 0 && (
        <div>
          <b style={{ fontSize: 13 }}>交付前核对</b>
          {m.checks.map((c, i) => <div key={i} style={{ fontSize: 12 }}>· {c.after ? `${c.after}: ` : ""}{c.verify}</div>)}
        </div>
      )}
      {Object.entries(m.workflows).map(([name, steps]) => (
        <div key={name}>
          <b style={{ fontSize: 13 }}>工作流 {name}</b>
          {steps.map((s, i) => (
            <div key={i} style={{ fontSize: 12 }}>
              {i + 1}. [{BLOCK_LABEL[s.block]}] {s.what}
              {s.confirm || (s.block === "action" && m.summary?.outward_actions?.includes(s.what)) ? " · 对外, 跑到这步停下等员工确认" : ""}
            </div>
          ))}
        </div>
      ))}
      {m.skills.length > 0 && <div style={muted}>自带技能: {m.skills.join("、")}</div>}
    </div>
  );
}

function ReviewDialog({ m, onClose, onDone }: { m: ExpertPackMeta; onClose: () => void; onDone: () => void }) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const go = async (decision: "approve" | "reject") => {
    if (decision === "reject" && !note.trim()) {
      setError("驳回要写原因, 会回给提交人");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await reviewExpert(m.id, m.version, decision, note);
      onDone();
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal title={`审核 · ${m.name} ${m.version}`} width={720} onClose={onClose}
           lockReason={busy ? "正在提交审核结果" : undefined}
           footer={
             <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
               <button type="button" style={BTN} disabled={busy} onClick={onClose}>取消</button>
               <button type="button" style={BTN_DANGER} disabled={busy} onClick={() => void go("reject")}>驳回</button>
               <button type="button" style={BTN_PRIMARY} disabled={busy} onClick={() => void go("approve")}>通过并上架</button>
             </div>
           }>
      <Detail m={m} />
      <textarea rows={3} value={note} onChange={(e) => setNote(e.target.value)} placeholder="审核意见 (驳回必填, 会回给提交人)"
                style={{ width: "100%", marginTop: 12, boxSizing: "border-box", fontFamily: "inherit", fontSize: 13, padding: 8 }} />
      {error && <div style={{ color: "var(--status-err)", fontSize: 12 }}>{error}</div>}
    </Modal>
  );
}

function Item({ m, children }: { m: ExpertPackMeta; children?: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div style={{ borderTop: "1px solid var(--border)", padding: "10px 0", display: "flex", flexDirection: "column", gap: 6 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "baseline" }}>
        <span style={{ fontSize: 14, fontWeight: 600 }}>
          {m.name} <span style={muted}>{m.version}</span>
        </span>
        <span style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <Badge tone={m.status === "approved" ? "ok" : m.status === "rejected" ? "err" : m.status === "pending" ? "warn" : "neutral"}>
            {STATUS_LABEL[m.status]}
          </Badge>
          <span style={muted}>{scopeText(m)}</span>
        </span>
      </div>
      {m.description && <span style={{ fontSize: 13 }}>{m.description}</span>}
      <Perms m={m} />
      {m.status === "rejected" && m.review_note && (
        <div style={{ fontSize: 12, color: "var(--status-err)" }}>驳回原因 ({m.reviewed_by}): {m.review_note}</div>
      )}
      <div style={{ display: "flex", gap: 8 }}>
        <button type="button" style={BTN} onClick={() => setOpen(!open)}>{open ? "收起" : "看详情"}</button>
        {children}
      </div>
      {open && <Detail m={m} />}
    </div>
  );
}

export function ExpertsMarketPage() {
  const me = useAuthStore((s) => s.me);
  const reviewer = !!me && roleAllows(me.role, "manager");
  const [market, setMarket] = useState<ExpertPackMeta[] | null>(null);
  const [mine, setMine] = useState<ExpertPackMeta[] | null>(null);
  const [review, setReview] = useState<ExpertPackMeta[] | null>(null);
  const [reviewing, setReviewing] = useState<ExpertPackMeta | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [a, b, c] = await Promise.all([
        listMarketExperts(), listMyExperts(), reviewer ? listReviewExperts() : Promise.resolve({ experts: [] }),
      ]);
      setMarket(a.experts);
      setMine(b.experts);
      setReview(c.experts);
      setError(null);
    } catch (e) {
      setError(errText(e));
    }
  }, [reviewer]);
  useEffect(() => { void load(); }, [load]);

  const withdraw = async (m: ExpertPackMeta) => {
    try {
      await withdrawExpert(m.id, m.version);
      await load();
    } catch (e) {
      setError(errText(e));
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
      {error && <div style={{ color: "var(--status-err)", fontSize: 13 }}>{error}</div>}
      {reviewer && (
        <Card title={`📝 待审核${review?.length ? ` (${review.length})` : ""}`}>
          <div style={muted}>你管的部门 (管理员: 全公司) 有人提交的专家包。看清楚它会用到什么、会打开哪些网站, 通过才上架。</div>
          {review?.length === 0 && <div style={{ ...muted, marginTop: 8 }}>没有待审核的。</div>}
          {review?.map((m) => (
            <Item key={`${m.id}@${m.version}`} m={m}>
              <button type="button" style={BTN_PRIMARY} onClick={() => setReviewing(m)}>审核</button>
            </Item>
          ))}
        </Card>
      )}
      <Card title="🧑‍💼 专家 · 已上架">
        <div style={muted}>你能装的专家 (本部门的 + 全公司的)。在 Companion 设置 → 技能 →「专家」→「资源市场」里安装, 装之前会验中央签名。</div>
        {market?.length === 0 && <div style={{ ...muted, marginTop: 8 }}>还没有。</div>}
        {market?.map((m) => <Item key={m.id} m={m} />)}
      </Card>
      <Card title="📤 我提交的">
        <div style={muted}>在 Companion 里搭好专家后点「发布到资源市场」提交。</div>
        {mine?.length === 0 && <div style={{ ...muted, marginTop: 8 }}>还没提交过。</div>}
        {mine?.map((m) => (
          <Item key={`${m.id}@${m.version}`} m={m}>
            {(m.status === "pending" || m.status === "approved") && (
              <button type="button" style={BTN} onClick={() => void withdraw(m)}>{m.status === "pending" ? "撤回" : "下架"}</button>
            )}
          </Item>
        ))}
      </Card>
      {reviewing && (
        <ReviewDialog m={reviewing} onClose={() => setReviewing(null)} onDone={() => { setReviewing(null); void load(); }} />
      )}
    </div>
  );
}
