/** /market/experts — 专家包 (10/18, docs/EXPERT-PACK-PLAN.md §5)。
 *
 * 11/09 跟控制台统一样式: 三张卡片 (📝 待审核 / 🧑‍💼 已上架 / 📤 我提交的) 变成一页里的三段切换 + 控制台表格;
 * 原来每条下面「看详情」展开, 表格行里放不下, 改成点名字弹出详情 (跟审核弹窗同一份内容)。
 *
 * 三块:
 *   · 已上架: 本部门 / 全公司能装的 (服务器按部门过滤, 下载接口也拦); 在 Companion 里装
 *   · 待审核 (主管 / 管理员才有): 看清单、选路、每个工作流的步骤、脚本里会打开的网站 —— 都是服务器自己从包里拆的,
 *     不是提交人自报; 通过 = 中央签名上架, 驳回要写原因, 回到提交人
 *   · 我提交的: 状态、驳回原因、撤回 / 下架
 *   · 11/08 分享治理 §3.3: 审核弹窗里多「试用」一块 —— 审批人在 Companion 资源市场点「装到我这里试用」,
 *     试跑结果 (哪条工作流、哪步成了、产出什么文件) 回传到 hub, 这里展示 (trials)
 */
import { useCallback, useEffect, useState } from "react";

import { BTN, BTN_DANGER, BTN_PRIMARY, DataTable, Section, Toolbar, type Column } from "../components/DataTable";
import { Modal } from "../components/Dialog";
import { PageShell } from "../components/PageShell";
import { roleAllows } from "../components/RoleGate";
import {
  BLOCK_LABEL, STATUS_LABEL, listMarketExperts, listMyExperts, listReviewExperts, reviewExpert, withdrawExpert,
  type ExpertPackMeta,
} from "../lib/experts";
import { useAuthStore } from "../store/auth";
import { errText, FilterBar, NameCell, StatusBadge, tabLabel, useRefreshMarketCounts } from "./marketShared";
import { TrialHint, TrialResults } from "./TrialResults";

const muted = { fontSize: 12, color: "var(--text-muted)" } as const;
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
      {m.allow_code && <div style={{ fontSize: 12, color: "var(--status-err)" }}>要用终端 / 执行代码</div>}
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
      <div style={{ marginTop: 12 }}>
        <b style={{ fontSize: 13 }}>试用</b>
        <div style={{ marginTop: 4, display: "flex", flexDirection: "column", gap: 6 }}>
          <TrialHint what="这个专家包" />
          <TrialResults trials={m.trials} />
        </div>
      </div>
      <textarea rows={3} value={note} onChange={(e) => setNote(e.target.value)} placeholder="审核意见 (驳回必填, 会回给提交人)"
                style={{ width: "100%", marginTop: 12, boxSizing: "border-box", fontFamily: "inherit", fontSize: 13, padding: 8 }} />
      {error && <div style={{ color: "var(--status-err)", fontSize: 12 }}>{error}</div>}
    </Modal>
  );
}

/** 只看详情 (已上架 / 我提交的): 跟审核弹窗同一份内容, 没有通过 / 驳回 */
function DetailDialog({ m, onClose }: { m: ExpertPackMeta; onClose: () => void }) {
  return (
    <Modal title={`${m.name} ${m.version}`} width={720} onClose={onClose}
           footer={<div style={{ display: "flex", justifyContent: "flex-end" }}><button type="button" style={BTN} onClick={onClose}>关闭</button></div>}>
      {m.description && <div style={{ fontSize: 13, marginBottom: 8 }}>{m.description}</div>}
      <Detail m={m} />
      {m.status === "rejected" && m.review_note && (
        <div style={{ fontSize: 12, color: "var(--status-err)", marginTop: 8 }}>驳回原因 ({m.reviewed_by}): {m.review_note}</div>
      )}
    </Modal>
  );
}

/** 「用到」那一格: 对外 / 执行代码这类要人留意的放前面, 全部在详情里 */
function usesText(m: ExpertPackMeta): string {
  const s = m.summary ?? {};
  const parts = [
    m.allow_code ? "执行代码" : "",
    s.outward?.length ? `对外 ${s.outward.length} 项` : "",
    m.sites.length ? `${m.sites.length} 个网站` : "",
    s.read?.length ? `查 ${s.read.length} 类` : "",
    m.allow_web ? "上网搜索" : "",
  ].filter(Boolean);
  return parts.join(" · ") || "—";
}

type Tab = "market" | "review" | "mine";

export function ExpertsMarketPage() {
  const me = useAuthStore((s) => s.me);
  const reviewer = !!me && roleAllows(me.role, "manager");
  const refreshCounts = useRefreshMarketCounts();
  const [market, setMarket] = useState<ExpertPackMeta[] | null>(null);
  const [mine, setMine] = useState<ExpertPackMeta[] | null>(null);
  const [review, setReview] = useState<ExpertPackMeta[] | null>(null);
  const [reviewing, setReviewing] = useState<ExpertPackMeta | null>(null);
  const [viewing, setViewing] = useState<ExpertPackMeta | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [tab, setTab] = useState<Tab>("market");

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

  const needle = q.trim().toLowerCase();
  const hit = (m: ExpertPackMeta) =>
    !needle || [m.name, m.id, m.description, m.submitted_by].some((v) => (v || "").toLowerCase().includes(needle));
  const tabs = [
    { key: "market" as const, label: tabLabel("已上架", market?.length) },
    ...(reviewer ? [{ key: "review" as const, label: tabLabel("待我审核", review?.length) }] : []),
    { key: "mine" as const, label: tabLabel("我提交的", mine?.length) },
  ];
  const current = tabs.some((t) => t.key === tab) ? tab : "market";
  const rows = (current === "review" ? review : current === "mine" ? mine : market) ?? [];

  const cols: Column<ExpertPackMeta>[] = [
    {
      header: "专家包",
      cell: (m) => (
        <NameCell
          name={<button type="button" onClick={() => setViewing(m)} style={LINK_BTN}>{m.name}</button>}
          sub={m.description}
        />
      ),
    },
    { header: "范围", width: "14%", truncate: true, cell: (m) => <span title={scopeText(m)}>{scopeText(m)}</span> },
    { header: "用到", width: "22%", truncate: true, cell: (m) => <span title={usesText(m)}>{usesText(m)}</span> },
    { header: "状态", width: 70, cell: (m) => <StatusBadge status={m.status} label={STATUS_LABEL[m.status]} /> },
    { header: "版本", width: 70, nowrap: true, cell: (m) => m.version },
    ...(current === "market"
      ? []
      : [{ header: current === "review" ? "提交人" : "提交时间", width: current === "review" ? "18%" : 110, truncate: true,
           cell: (m: ExpertPackMeta) => (current === "review" ? <span title={m.submitted_by}>{m.submitted_by}</span> : when(m.submitted_at)) }]),
    {
      header: "", width: 60, cell: (m) =>
        current === "review" ? (
          <button type="button" style={BTN_PRIMARY} onClick={() => setReviewing(m)}>审核</button>
        ) : current === "mine" && (m.status === "pending" || m.status === "approved") ? (
          <button type="button" style={BTN} onClick={() => void withdraw(m)}>{m.status === "pending" ? "撤回" : "下架"}</button>
        ) : null,
    },
  ];

  const hint =
    current === "review"
      ? "你管的部门 (管理员: 全公司) 有人提交的专家包。看清楚它会用到什么、会打开哪些网站, 通过才上架。"
      : current === "mine"
        ? "在 Companion 里搭好专家后点「发布到资源市场」提交。"
        : "你能装的专家 (本部门的 + 全公司的)。在 Companion 设置 → 技能 →「专家」→「资源市场」里安装, 装之前会验中央签名。点名字看详情。";

  return (
    <PageShell scroll="data">
      <Toolbar title="专家包" />
      <FilterBar tabs={tabs} tab={current} onTab={setTab} q={q} onQ={setQ} placeholder="搜专家包名 / 说明 / 提交人…" hint={hint} />
      {error && <div style={{ color: "var(--status-err)", fontSize: 12 }}>{error}</div>}
      <Section fill>
        <DataTable
          fill
          columns={cols}
          rows={rows.filter(hit)}
          rowKey={(m) => `${m.id}@${m.version}`}
          empty={
            (current === "review" ? review : current === "mine" ? mine : market) === null
              ? "加载中…"
              : rows.length === 0
                ? current === "review" ? "没有待审核的。" : current === "mine" ? "还没提交过。" : "还没有。"
                : "没有匹配的"
          }
        />
      </Section>
      {reviewing && (
        <ReviewDialog m={reviewing} onClose={() => setReviewing(null)}
                      onDone={() => { setReviewing(null); void load(); refreshCounts(); }} />
      )}
      {viewing && <DetailDialog m={viewing} onClose={() => setViewing(null)} />}
    </PageShell>
  );
}

const LINK_BTN = {
  background: "none", border: "none", padding: 0, font: "inherit", color: "var(--accent)", cursor: "pointer", textAlign: "left",
} as const;
