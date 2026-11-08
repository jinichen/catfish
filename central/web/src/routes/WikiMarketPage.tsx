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
 */
import { useEffect, useState } from "react";
import { Link, Route, Routes, useNavigate, useParams } from "react-router-dom";

import { Card } from "../components/Card";
import { Badge, BTN, BTN_DANGER, BTN_PRIMARY } from "../components/DataTable";
import { ConfirmDialog } from "../components/Dialog";
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
  unpublishWikiDoc,
  wikiReviewStatus,
  type WikiDocDetail,
  type WikiDocSummary,
} from "../lib/wiki";
import { useAuthStore } from "../store/auth";

export function WikiMarketPage() {
  return (
    <Routes>
      <Route index element={<WikiList />} />
      <Route path=":dept/:fileId" element={<WikiDetail />} />
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

const HOW_TO = (
  <>
    分享：在鲶鱼 Companion「知识体系」里打开一条笔记，点「📤 分享到部门」。装到自己电脑上：Companion「设置」→「技能」→「📚 部门知识库」，点「安装」。
  </>
);

function WikiList() {
  const me = useAuthStore((s) => s.me);
  const reviewer = !!me && roleAllows(me.role, "manager");
  const [docs, setDocs] = useState<WikiDocSummary[]>([]);
  const [pending, setPending] = useState<WikiDocSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("");
  const [onlyPending, setOnlyPending] = useState(false);

  useEffect(() => {
    listWikiDocs()
      .then((r) => setDocs(r.documents || []))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, []);
  useEffect(() => {
    if (!reviewer) return;
    // 待我审的单独一条接口 (后端按 managed_depts / admin 过滤); 拉不到就不显示筛选, 不影响列表
    listReviewWikiDocs().then((r) => setPending(r.documents || [])).catch(() => setPending(null));
  }, [reviewer]);

  const q = filter.trim().toLowerCase();
  const base = onlyPending && pending ? pending : docs;
  const shown = q
    ? base.filter((d) =>
        [d.title, d.description_preview, d.namespace, d.published_by]
          .some((v) => (v || "").toLowerCase().includes(q)),
      )
    : base;
  const grouped: Record<string, WikiDocSummary[]> = {};
  for (const d of shown) (grouped[d.namespace] ||= []).push(d);
  for (const list of Object.values(grouped)) {
    // 有效的在前, 新的在前; 撤回的沉底
    list.sort((a, b) =>
      Number(!!a.stale_after_unpublish) - Number(!!b.stale_after_unpublish) ||
      (b.updated_at || b.published_at || "").localeCompare(a.updated_at || a.published_at || ""),
    );
  }
  const namespaces = Object.keys(grouped).sort();
  const live = docs.filter((d) => !d.stale_after_unpublish).length;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
      <Card title={`知识库分享 · ${live} 条 / ${new Set(docs.map((d) => d.namespace)).size} 个部门`}>
        <div style={{ ...muted, marginBottom: "var(--space-2)" }}>
          {HOW_TO}
          分享出来的页面同事马上能看到、能装，但带「待审」标；部门主管看过打钩后变「已审」，有问题可以撤下。
        </div>
        {reviewer && pending && (
          <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: "var(--space-2)", fontSize: 13 }}>
            <button
              type="button"
              style={onlyPending ? BTN_PRIMARY : BTN}
              onClick={() => setOnlyPending((v) => !v)}
            >
              📝 待我审核{pending.length ? ` (${pending.length})` : ""}
            </button>
            {onlyPending && <span style={muted}>只列你管的部门里还没看过的；点进去「看过, 没问题」或「撤下」。</span>}
          </div>
        )}
        <input
          type="text"
          placeholder="搜标题 / 摘要 / 部门 / 作者…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          style={{
            width: "100%",
            padding: "6px 10px",
            border: "1px solid var(--border)",
            borderRadius: "var(--radius-sm)",
            fontSize: 13,
            boxSizing: "border-box",
          }}
        />
      </Card>

      {loading && <div>加载中…</div>}
      {error && <div style={{ color: "var(--status-err)" }}>{error}</div>}
      {!loading && !error && docs.length === 0 && (
        <Card title="还没人分享过知识">
          <div style={muted}>
            {me && !me.department && !roleAllows(me.role, "admin")
              ? "你的账号还没设置部门，看不到部门知识库，请联系管理员在用户管理里补上部门。"
              : HOW_TO}
          </div>
        </Card>
      )}

      {namespaces.map((ns) => (
        <div key={ns}>
          <div style={{ ...muted, marginBottom: "var(--space-2)", fontFamily: "var(--font-mono)" }}>
            {deptOf(ns)} ({ns}) · {grouped[ns].length} 条
          </div>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
              gap: "var(--space-2)",
            }}
          >
            {grouped[ns].map((d) => (
              <Link
                key={`${d.namespace}/${d.file_id}`}
                to={`/market/wiki/${encodeURIComponent(deptOf(d.namespace))}/${encodeURIComponent(d.file_id)}`}
                style={{
                  background: "var(--bg-elev)",
                  border: "1px solid var(--border)",
                  borderRadius: "var(--radius-sm)",
                  padding: "var(--space-3)",
                  fontSize: 13,
                  color: "var(--text)",
                  textDecoration: "none",
                  opacity: d.stale_after_unpublish ? 0.55 : 1,
                }}
                onMouseEnter={(e) => (e.currentTarget.style.borderColor = "var(--accent)")}
                onMouseLeave={(e) => (e.currentTarget.style.borderColor = "var(--border)")}
              >
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "baseline",
                    gap: 8,
                    marginBottom: "var(--space-1)",
                  }}
                >
                  <span style={{ fontWeight: 500 }}>{d.title}</span>
                  <span style={{ fontSize: 11, color: "var(--text-muted)", whiteSpace: "nowrap", display: "flex", gap: 6, alignItems: "center" }}>
                    <ReviewBadge d={d} />
                    {!d.stale_after_unpublish && (KIND_LABEL[d.kind] ?? d.kind)}
                  </span>
                </div>
                {d.description_preview && !d.stale_after_unpublish && (
                  <div
                    style={{
                      ...muted,
                      marginBottom: "var(--space-1)",
                      display: "-webkit-box",
                      WebkitLineClamp: 2,
                      WebkitBoxOrient: "vertical",
                      overflow: "hidden",
                    }}
                  >
                    {d.description_preview}
                  </div>
                )}
                <div style={{ fontSize: 11, color: "var(--text-muted)", display: "flex", justifyContent: "space-between" }}>
                  <span>👤 {d.published_by || "?"}</span>
                  <span>{day(d.updated_at || d.published_at)}</span>
                </div>
              </Link>
            ))}
          </div>
        </div>
      ))}
    </div>
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
