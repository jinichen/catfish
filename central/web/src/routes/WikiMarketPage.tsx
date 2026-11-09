/** /market/wiki — 部门知识库分享 (11/4 鸿波「为什么知识库的分享没有?」)。
 *
 * 员工在 Companion 知识库里点「分享到部门」, 笔记进中央 wiki-hub; 原来门户市场只有
 * Skills / MCP / 专家 / 组件四个页签, 分享出来的知识在网页上看不到, 也没处撤回。
 *
 * - 列表: 按部门分组 (员工只看得到本部门, 管理员看全部), 撤回的显灰
 * - 详情: 正文按 Markdown 排版; 原作者和管理员可以撤回 (中央清正文、标已撤回,
 *   已经装到同事电脑上的副本不动 —— wiki-hub README「公理 4」)
 * - 发布 / 安装都在 Companion 里: 笔记在员工自己电脑上, 网页够不着
 * - 11/08 分享治理 (§2.2 纯文字的): 发了即可见但标「待审」; 主管 / 管理员「看过, 没问题」打钩变「已审」,
 *   或「撤下」(要写原因, 等于撤回 + 标已撤回, 装过的人那边显示「已撤回」)。有「待我审核」筛选。
 * - 11/09 跟控制台统一样式: 按部门分组的卡片墙换成一张表 (部门一列, 按部门排), 「待我审核」从按钮变成分段切换
 * - 11/09 本部门 / 全公司: 「范围」一列; 作者可以申请全公司可见 / 收回到本部门; 管理员审全公司那一档
 *   (同意 / 驳回要写原因)。页面一直存在作者部门里, 全公司只是多一档可见范围 (wiki-hub review.py)。
 */
import { useEffect, useState } from "react";
import { Link, Route, Routes, useNavigate, useParams } from "react-router-dom";

import { Card } from "../components/Card";
import { Badge, BTN_DANGER, BTN_PRIMARY, DataTable, Section, Toolbar, type Column } from "../components/DataTable";
import { ConfirmDialog } from "../components/Dialog";
import { PageShell } from "../components/PageShell";
import { roleAllows } from "../components/RoleGate";
import { SimpleMarkdown } from "../components/SimpleMarkdown";
import {
  deptOf,
  getWikiDoc,
  KIND_LABEL,
  listReviewWikiDocs,
  listWikiDocs,
  REVIEW_LABEL,
  reviewWikiDoc,
  scopeLabel,
  setWikiScope,
  unpublishWikiDoc,
  wikiReviewStatus,
  type WikiDocDetail,
  type WikiDocSummary,
} from "../lib/wiki";
import { useAuthStore } from "../store/auth";
import { FilterBar, NameCell, SubPage, tabLabel } from "./marketShared";

export function WikiMarketPage() {
  return (
    <Routes>
      <Route index element={<WikiList />} />
      <Route path=":dept/:fileId" element={<SubPage back="/market/wiki" section="知识库" title="详情"><WikiDetail /></SubPage>} />
    </Routes>
  );
}

const muted = { fontSize: 12, color: "var(--text-muted)" } as const;

function day(iso?: string | null): string {
  return iso ? iso.slice(0, 10) : "";
}

function ReviewBadge({ d }: { d: Pick<WikiDocSummary, "review_status" | "stale_after_unpublish"> }) {
  const st = wikiReviewStatus(d);
  return <Badge tone={st === "approved" ? "ok" : st === "pending" ? "warn" : "neutral"}>{REVIEW_LABEL[st]}</Badge>;
}

function ScopeBadge({ d }: { d: Pick<WikiDocSummary, "scope" | "visible_to" | "company_status"> }) {
  const { text, tone } = scopeLabel(d);
  return <Badge tone={tone}>{text}</Badge>;
}

const HOW_TO = (
  <>
    分享：在鲶鱼 Companion「资料库」里打开一条笔记，点「📤 分享」，选本部门或全公司。装到自己电脑上：Companion「设置」→「技能」→「📚 部门知识库」，点「安装」。
  </>
);

type Tab = "all" | "review";

function WikiList() {
  const me = useAuthStore((s) => s.me);
  const reviewer = !!me && roleAllows(me.role, "manager");
  const [docs, setDocs] = useState<WikiDocSummary[]>([]);
  const [pending, setPending] = useState<WikiDocSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("");
  const [tab, setTab] = useState<Tab>("all");

  useEffect(() => {
    listWikiDocs()
      .then((r) => setDocs(r.documents || []))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, []);
  useEffect(() => {
    if (!reviewer) return;
    // 待我审的单独一条接口 (后端按 managed_depts / admin 过滤); 拉不到就不显示这一段, 不影响列表
    listReviewWikiDocs().then((r) => setPending(r.documents || [])).catch(() => setPending(null));
  }, [reviewer]);

  const tabs = [
    { key: "all" as const, label: tabLabel("全部", loading ? null : docs.length) },
    ...(reviewer && pending ? [{ key: "review" as const, label: tabLabel("待我审核", pending.length) }] : []),
  ];
  const current = tabs.some((t) => t.key === tab) ? tab : "all";

  const q = filter.trim().toLowerCase();
  const base = current === "review" && pending ? pending : docs;
  const shown = (q
    ? base.filter((d) =>
        [d.title, d.description_preview, d.namespace, deptOf(d.namespace), d.published_by]
          .some((v) => (v || "").toLowerCase().includes(q)),
      )
    : [...base]
  ).sort((a, b) =>
    // 按部门排; 部门内有效的在前、新的在前, 撤回的沉底
    a.namespace.localeCompare(b.namespace) ||
    Number(!!a.stale_after_unpublish) - Number(!!b.stale_after_unpublish) ||
    (b.updated_at || b.published_at || "").localeCompare(a.updated_at || a.published_at || ""),
  );
  const live = docs.filter((d) => !d.stale_after_unpublish).length;
  const depts = new Set(docs.map((d) => d.namespace)).size;

  const cols: Column<WikiDocSummary>[] = [
    {
      header: "标题",
      cell: (d) => (
        <NameCell
          name={
            <Link to={`/market/wiki/${encodeURIComponent(deptOf(d.namespace))}/${encodeURIComponent(d.file_id)}`}>{d.title}</Link>
          }
          sub={d.stale_after_unpublish ? null : d.description_preview}
        />
      ),
    },
    { header: "部门", width: "14%", truncate: true, cell: (d) => <span title={d.namespace}>{deptOf(d.namespace)}</span> },
    {
      header: "范围",
      width: 96,
      cell: (d) =>
        current === "review" && d.needs ? (
          <span style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
            {d.needs.includes("company") && <Badge tone="warn">审全公司</Badge>}
            {d.needs.includes("department") && <Badge tone="neutral">审部门</Badge>}
          </span>
        ) : d.stale_after_unpublish ? (
          "—"
        ) : (
          <ScopeBadge d={d} />
        ),
    },
    { header: "类型", width: 70, cell: (d) => (d.stale_after_unpublish ? "—" : KIND_LABEL[d.kind] ?? d.kind) },
    { header: "状态", width: 70, cell: (d) => <ReviewBadge d={d} /> },
    { header: "作者", width: "18%", truncate: true, cell: (d) => <span title={d.published_by ?? ""}>{d.published_by || "?"}</span> },
    { header: "更新", width: 90, nowrap: true, cell: (d) => day(d.updated_at || d.published_at) },
  ];

  const noDept = !!me && !me.department && !roleAllows(me.role, "admin");
  const hint =
    current === "review"
      ? "部门主管：你管的部门里还没看过的，点进去「看过，没问题」或「撤下」。管理员另外审申请全公司可见的（「审全公司」），同意后全公司都能看到。"
      : <>{HOW_TO} 分享出来的页面本部门同事马上能看到、能装, 带「待审」标, 部门主管看过打钩后变「已审」; 选了全公司的, 管理员审过后其他部门才看得到。</>;

  return (
    <PageShell scroll="data">
      <Toolbar title={`知识库 · ${live} 条 / ${depts} 个部门`} />
      <FilterBar
        tabs={tabs}
        tab={current}
        onTab={setTab}
        q={filter}
        onQ={setFilter}
        placeholder="搜标题 / 摘要 / 部门 / 作者…"
        hint={hint}
      />
      {error && <div style={{ color: "var(--status-err)", fontSize: 12 }}>{error}</div>}
      <Section fill>
        <DataTable
          fill
          columns={cols}
          rows={shown}
          rowKey={(d) => `${d.namespace}/${d.file_id}`}
          rowStyle={(d) => (d.stale_after_unpublish ? { opacity: 0.55 } : undefined)}
          empty={
            loading ? "加载中…"
              : base.length === 0
                ? current === "review" ? "没有待你审核的。" : noDept
                  ? "你的账号还没设置部门，看不到部门知识库，请联系管理员在用户管理里补上部门。"
                  : "还没人分享过知识。"
                : "没有匹配的"
          }
        />
      </Section>
    </PageShell>
  );
}

function WikiDetail() {
  const { dept, fileId } = useParams<{ dept: string; fileId: string }>();
  const navigate = useNavigate();
  const me = useAuthStore((s) => s.me);
  const [doc, setDoc] = useState<WikiDocDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [actErr, setActErr] = useState<string | null>(null);
  // 11/08 审核: 撤下要写原因 (弹窗), 看过打钩直接提交
  const [withdrawing, setWithdrawing] = useState(false);
  const [withdrawNote, setWithdrawNote] = useState("");
  const [reviewErr, setReviewErr] = useState<string | null>(null);
  // 11/09 全公司: 管理员驳回要写原因; 作者申请 / 收回
  const [rejecting, setRejecting] = useState(false);
  const [rejectNote, setRejectNote] = useState("");
  const [companyErr, setCompanyErr] = useState<string | null>(null);
  const [scopeErr, setScopeErr] = useState<string | null>(null);

  useEffect(() => {
    if (!dept || !fileId) return;
    getWikiDoc(dept, fileId)
      .then(setDoc)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [dept, fileId]);

  if (!dept || !fileId) return <div>路径错</div>;
  if (error) return <div style={{ color: "var(--status-err)" }}>{error}</div>;
  if (!doc) return <div>加载中…</div>;

  // 跟 wiki-hub 的判断一致: 原作者本人, 或 admin / sysadmin (roleAllows 把 sysadmin 算进 admin)
  const canUnpublish =
    !doc.stale_after_unpublish && !!me && (me.email === doc.published_by || roleAllows(me.role, "admin"));
  // 审批人: 部门主管管这个部门, 或 admin。后端 can_review 再拦一道; 这里只决定显不显示按钮
  const status = wikiReviewStatus(doc);
  const canReview =
    status !== "withdrawn" && !!me &&
    (roleAllows(me.role, "admin") ||
      (roleAllows(me.role, "manager") && (me.managed_departments || []).includes(deptOf(doc.namespace))));

  const isAdmin = !!me && roleAllows(me.role, "admin");
  const isAuthor = !!me && me.email === doc.published_by;
  const live = status !== "withdrawn" && !doc.stale_after_unpublish;
  const companyPending = doc.scope === "company" && doc.company_status === "pending";
  const canReviewCompany = live && isAdmin && companyPending;
  const canChangeScope = live && (isAuthor || isAdmin);

  const doCompany = async (decision: "approve_company" | "reject_company") => {
    const note = rejectNote.trim();
    if (decision === "reject_company" && !note) {
      setCompanyErr("驳回要写原因，作者会看到");
      return;
    }
    setBusy(true);
    setCompanyErr(null);
    try {
      await reviewWikiDoc(dept, fileId, decision, decision === "reject_company" ? note : "");
      const approved = decision === "approve_company";
      setDoc({
        ...doc,
        company_status: approved ? "approved" : "rejected",
        visible_to: approved ? "company" : "department",
        company_reviewed_by: me?.email,
        company_reviewed_at: new Date().toISOString(),
        company_note: approved ? "" : note,
      });
      setRejecting(false);
    } catch (e) {
      setCompanyErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const changeScope = async (scope: "department" | "company") => {
    setBusy(true);
    setScopeErr(null);
    try {
      const r = await setWikiScope(dept, fileId, scope);
      setDoc({ ...doc, ...r });
    } catch (e) {
      setScopeErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const doReview = async (decision: "approve" | "withdraw") => {
    const note = withdrawNote.trim();
    if (decision === "withdraw" && !note) {
      setReviewErr("撤下要写原因，同事和作者都会看到");
      return;
    }
    setBusy(true);
    setReviewErr(null);
    try {
      await reviewWikiDoc(dept, fileId, decision, decision === "withdraw" ? note : "");
      if (decision === "withdraw") {
        navigate("/market/wiki");
      } else {
        setDoc({ ...doc, review_status: "approved", reviewed_by: me?.email, reviewed_at: new Date().toISOString(), legacy: false });
      }
    } catch (e) {
      setReviewErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const handleUnpublish = async () => {
    setBusy(true);
    setActErr(null);
    try {
      await unpublishWikiDoc(dept, fileId, reason.trim());
      navigate("/market/wiki");
    } catch (e) {
      setActErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
      <Card
        title={doc.title}
        action={
          <span style={{ ...muted, display: "flex", gap: 8, alignItems: "center" }}>
            <ReviewBadge d={doc} />
            <ScopeBadge d={doc} />
            {KIND_LABEL[doc.kind] ?? doc.kind}
          </span>
        }
      >
        <div style={{ ...muted, marginTop: -8, marginBottom: "var(--space-3)", fontFamily: "var(--font-mono)" }}>
          {deptOf(doc.namespace)} · {doc.filename}
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-4)", ...muted }}>
          <span>分享人：{doc.published_by || "?"}</span>
          {doc.published_at && <span>首次分享：{day(doc.published_at)}</span>}
          {doc.updated_at && <span>更新：{day(doc.updated_at)}</span>}
          {status === "approved" && doc.reviewed_by && <span>审核：{doc.reviewed_by} {day(doc.reviewed_at)}</span>}
        </div>
        {doc.review_status === "withdrawn" ? (
          <div style={{ marginTop: "var(--space-3)", color: "var(--status-warn)", fontSize: 13 }}>
            {doc.withdrawn_by || "主管"} 已于 {day(doc.withdrawn_at || doc.unpublished_at)} 撤下
            {doc.review_note ? `（${doc.review_note}）` : ""}，正文已从中央清掉。装过的同事那边会标「已撤回」。
          </div>
        ) : doc.stale_after_unpublish ? (
          <div style={{ marginTop: "var(--space-3)", color: "var(--status-warn)", fontSize: 13 }}>
            原作者已于 {day(doc.unpublished_at)} 撤回{doc.unpublished_reason ? `（${doc.unpublished_reason}）` : ""}，
            正文已从中央清掉。已经装到同事电脑上的副本不受影响。
          </div>
        ) : (
          <>
            {status === "pending" && (
              <div style={{ marginTop: "var(--space-3)", color: "var(--status-warn)", fontSize: 13 }}>
                这一页还没经部门主管看过，同事已经能看到、能装，页面上带「待审」标。
              </div>
            )}
            {doc.visible_to === "company" ? (
              <div style={{ marginTop: "var(--space-3)", fontSize: 13 }}>
                全公司可见{doc.company_reviewed_by ? `（${doc.company_reviewed_by} ${day(doc.company_reviewed_at)} 审过）` : ""}。
              </div>
            ) : companyPending ? (
              <div style={{ marginTop: "var(--space-3)", color: "var(--status-warn)", fontSize: 13 }}>
                作者申请了全公司可见，等管理员审；审过前只有{deptOf(doc.namespace)}看得到。
              </div>
            ) : doc.scope === "company" && doc.company_status === "rejected" ? (
              <div style={{ marginTop: "var(--space-3)", color: "var(--text-muted)", fontSize: 13 }}>
                管理员没通过全公司可见{doc.company_note ? `：${doc.company_note}` : ""}；只在{deptOf(doc.namespace)}可见。
              </div>
            ) : null}
            <div style={{ ...muted, marginTop: "var(--space-3)" }}>{HOW_TO}</div>
          </>
        )}
      </Card>

      {!doc.stale_after_unpublish && (
        <Card title="正文">
          {(doc.body_md || "").trim() ? (
            <SimpleMarkdown source={doc.body_md || ""} />
          ) : (
            <div style={muted}>（没有正文）</div>
          )}
        </Card>
      )}

      {canReview && (
        <Card title="审核">
          <div style={{ ...muted, marginBottom: "var(--space-2)" }}>
            知识页发了就可见，不卡人；你看一眼没问题就打钩，有错或不该分享的就撤下（要写原因）。
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "var(--space-3)", flexWrap: "wrap" }}>
            {status === "pending" ? (
              <button type="button" style={BTN_PRIMARY} disabled={busy} onClick={() => void doReview("approve")}>
                看过，没问题
              </button>
            ) : (
              <span style={{ fontSize: 13, color: "var(--status-ok)" }}>✓ 已审</span>
            )}
            <button type="button" style={BTN_DANGER} disabled={busy} onClick={() => setWithdrawing(true)}>
              撤下
            </button>
            {reviewErr && !withdrawing && <span style={{ fontSize: 12, color: "var(--status-err)" }}>{reviewErr}</span>}
          </div>
          {withdrawing && (
            <ConfirmDialog
              danger
              title={`撤下「${doc.title}」?`}
              confirmLabel="撤下"
              busy={busy}
              onCancel={() => {
                setWithdrawing(false);
                setReviewErr(null);
              }}
              onConfirm={() => void doReview("withdraw")}
            >
              中央会清掉正文、标成「已撤回」；已经装到同事电脑上的那份会显示「已撤回」，搜索默认不再列出。作者能看到原因。
              <input
                type="text"
                placeholder="撤下原因（必填）"
                value={withdrawNote}
                onChange={(e) => setWithdrawNote(e.target.value)}
                style={{
                  width: "100%",
                  marginTop: 8,
                  padding: "6px 10px",
                  border: "1px solid var(--border)",
                  borderRadius: "var(--radius-sm)",
                  fontSize: 13,
                  boxSizing: "border-box",
                }}
              />
              {reviewErr && <div style={{ marginTop: 8, color: "var(--status-err)" }}>{reviewErr}</div>}
            </ConfirmDialog>
          )}
        </Card>
      )}

      {canReviewCompany && (
        <Card title="全公司可见">
          <div style={{ ...muted, marginBottom: "var(--space-2)" }}>
            作者想让全公司都能看到这一页。同意后其他部门的同事也能搜到、能装；驳回后页面仍留在{deptOf(doc.namespace)}。
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "var(--space-3)", flexWrap: "wrap" }}>
            <button type="button" style={BTN_PRIMARY} disabled={busy} onClick={() => void doCompany("approve_company")}>
              同意全公司可见
            </button>
            <button type="button" style={BTN_DANGER} disabled={busy} onClick={() => setRejecting(true)}>
              驳回
            </button>
            {companyErr && !rejecting && <span style={{ fontSize: 12, color: "var(--status-err)" }}>{companyErr}</span>}
          </div>
          {rejecting && (
            <ConfirmDialog
              danger
              title={`不让「${doc.title}」全公司可见?`}
              confirmLabel="驳回"
              busy={busy}
              onCancel={() => {
                setRejecting(false);
                setCompanyErr(null);
              }}
              onConfirm={() => void doCompany("reject_company")}
            >
              页面仍留在{deptOf(doc.namespace)}，本部门同事照常能看。作者能看到原因。
              <input
                type="text"
                placeholder="驳回原因（必填）"
                value={rejectNote}
                onChange={(e) => setRejectNote(e.target.value)}
                style={{
                  width: "100%",
                  marginTop: 8,
                  padding: "6px 10px",
                  border: "1px solid var(--border)",
                  borderRadius: "var(--radius-sm)",
                  fontSize: 13,
                  boxSizing: "border-box",
                }}
              />
              {companyErr && <div style={{ marginTop: 8, color: "var(--status-err)" }}>{companyErr}</div>}
            </ConfirmDialog>
          )}
        </Card>
      )}

      {canChangeScope && (
        <Card title="分享范围">
          <div style={{ display: "flex", alignItems: "center", gap: "var(--space-3)", flexWrap: "wrap" }}>
            {doc.scope === "company" && doc.company_status !== "rejected" ? (
              <>
                <button type="button" style={BTN_DANGER} disabled={busy} onClick={() => void changeScope("department")}>
                  收回到只在{deptOf(doc.namespace)}
                </button>
                <span style={muted}>其他部门就看不到了；已经装走的副本不受影响。</span>
              </>
            ) : (
              <>
                <button type="button" style={BTN_PRIMARY} disabled={busy} onClick={() => void changeScope("company")}>
                  申请全公司可见
                </button>
                <span style={muted}>管理员审过后全公司都能看到、能装；审之前只有{deptOf(doc.namespace)}看得到。</span>
              </>
            )}
            {scopeErr && <span style={{ fontSize: 12, color: "var(--status-err)" }}>{scopeErr}</span>}
          </div>
        </Card>
      )}

      {canUnpublish && (
        <Card title="管理">
          <div style={{ display: "flex", alignItems: "center", gap: "var(--space-3)" }}>
            <button
              onClick={() => setConfirming(true)}
              style={{
                background: "transparent",
                border: "1px solid var(--status-err)",
                color: "var(--status-err)",
                padding: "4px 12px",
                borderRadius: "var(--radius-sm)",
                fontSize: 12,
                cursor: "pointer",
              }}
            >
              撤回分享
            </button>
            <span style={muted}>撤回后同事在中央看不到正文；已经装到同事电脑上的副本不受影响。</span>
          </div>
          {confirming && (
            <ConfirmDialog
              danger
              title={`撤回「${doc.title}」?`}
              confirmLabel="撤回"
              busy={busy}
              onCancel={() => {
                setConfirming(false);
                setActErr(null);
              }}
              onConfirm={() => void handleUnpublish()}
            >
              中央会清掉这条的正文、标成「已撤回」，部门同事就装不到了。以后想再分享，在 Companion 里重新点「📤 分享到部门」。
              <input
                type="text"
                placeholder="撤回原因（选填，同事能看到）"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                style={{
                  width: "100%",
                  marginTop: 8,
                  padding: "6px 10px",
                  border: "1px solid var(--border)",
                  borderRadius: "var(--radius-sm)",
                  fontSize: 13,
                  boxSizing: "border-box",
                }}
              />
              {actErr && <div style={{ marginTop: 8, color: "var(--status-err)" }}>撤回失败：{actErr}</div>}
            </ConfirmDialog>
          )}
        </Card>
      )}
    </div>
  );
}
