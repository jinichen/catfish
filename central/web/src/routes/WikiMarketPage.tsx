/** /market/wiki — 部门知识库分享 (11/4 鸿波「为什么知识库的分享没有?」)。
 *
 * 员工在 Companion 知识库里点「分享到部门」, 笔记进中央 wiki-hub; 原来门户市场只有
 * Skills / MCP / 专家 / 组件四个页签, 分享出来的知识在网页上看不到, 也没处撤回。
 *
 * - 列表: 按部门分组 (员工只看得到本部门, 管理员看全部), 撤回的显灰
 * - 详情: 正文按 Markdown 排版; 原作者和管理员可以撤回 (中央清正文、标已撤回,
 *   已经装到同事电脑上的副本不动 —— wiki-hub README「公理 4」)
 * - 发布 / 安装都在 Companion 里: 笔记在员工自己电脑上, 网页够不着
 */
import { useEffect, useState } from "react";
import { Link, Route, Routes, useNavigate, useParams } from "react-router-dom";

import { Card } from "../components/Card";
import { ConfirmDialog } from "../components/Dialog";
import { roleAllows } from "../components/RoleGate";
import { SimpleMarkdown } from "../components/SimpleMarkdown";
import {
  deptOf,
  getWikiDoc,
  KIND_LABEL,
  listWikiDocs,
  unpublishWikiDoc,
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

const HOW_TO = (
  <>
    分享：在鲶鱼 Companion「知识体系」里打开一条笔记，点「📤 分享到部门」。装到自己电脑上：Companion「设置」→「技能」→「📚 部门知识库」，点「安装」。
  </>
);

function WikiList() {
  const me = useAuthStore((s) => s.me);
  const [docs, setDocs] = useState<WikiDocSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("");

  useEffect(() => {
    listWikiDocs()
      .then((r) => setDocs(r.documents || []))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, []);

  const q = filter.trim().toLowerCase();
  const shown = q
    ? docs.filter((d) =>
        [d.title, d.description_preview, d.namespace, d.published_by]
          .some((v) => (v || "").toLowerCase().includes(q)),
      )
    : docs;
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
        <div style={{ ...muted, marginBottom: "var(--space-2)" }}>{HOW_TO}</div>
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
                  <span style={{ fontSize: 11, color: "var(--text-muted)", whiteSpace: "nowrap" }}>
                    {d.stale_after_unpublish ? "已撤回" : KIND_LABEL[d.kind] ?? d.kind}
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
        action={<span style={muted}>{KIND_LABEL[doc.kind] ?? doc.kind}</span>}
      >
        <div style={{ ...muted, marginTop: -8, marginBottom: "var(--space-3)", fontFamily: "var(--font-mono)" }}>
          {deptOf(doc.namespace)} · {doc.filename}
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-4)", ...muted }}>
          <span>分享人：{doc.published_by || "?"}</span>
          {doc.published_at && <span>首次分享：{day(doc.published_at)}</span>}
          {doc.updated_at && <span>更新：{day(doc.updated_at)}</span>}
        </div>
        {doc.stale_after_unpublish ? (
          <div style={{ marginTop: "var(--space-3)", color: "var(--status-warn)", fontSize: 13 }}>
            原作者已于 {day(doc.unpublished_at)} 撤回{doc.unpublished_reason ? `（${doc.unpublished_reason}）` : ""}，
            正文已从中央清掉。已经装到同事电脑上的副本不受影响。
          </div>
        ) : (
          <div style={{ ...muted, marginTop: "var(--space-3)" }}>{HOW_TO}</div>
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
