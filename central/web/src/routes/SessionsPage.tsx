/** /sessions — 跨日会话历史 + 搜索 + 详情 (5/12 借鉴 hermes-desktop A 路线).
 *
 * 数据源: 员工自己 mac ~/.hermes/state.db (read-only sqlite, gateway 暴露).
 * RBAC: 谁登录看谁 (db 物理隔离), 不需二次校验.
 *
 * UI 布局:
 *   左 33%: session 列表 + 搜索框 + 时间窗口 + 分页
 *   右 67%: 选中 session 详情 (messages 数组 + 元数据)
 */

import { useEffect, useState } from "react";

import { Card } from "../components/Card";
import {
  fetchSessionDetail,
  fetchSessionsList,
  fetchSessionsSearch,
  type SearchMatch,
  type SessionDetail,
  type SessionRow,
} from "../lib/me";

const PAGE_SIZE = 30;

export function SessionsPage() {
  // 列表态
  const [sessions, setSessions] = useState<SessionRow[]>([]);
  const [total, setTotal] = useState(0);
  const [listError, setListError] = useState<string | null>(null);
  const [listLoading, setListLoading] = useState(false);
  // 筛选
  const [daysBack, setDaysBack] = useState(30);
  const [q, setQ] = useState("");
  const [page, setPage] = useState(0);
  // 全文搜索 (跨 session 命中行模式)
  const [searchMatches, setSearchMatches] = useState<SearchMatch[] | null>(null);
  const [searchLoading, setSearchLoading] = useState(false);
  // 详情
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<SessionDetail | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const loadList = () => {
    setListLoading(true);
    setListError(null);
    fetchSessionsList({
      days_back: daysBack,
      q: q || undefined,
      limit: PAGE_SIZE,
      offset: page * PAGE_SIZE,
    })
      .then((r) => {
        if (r.ok) {
          setSessions(r.data.sessions);
          setTotal(r.data.total);
        } else {
          setSessions([]);
          setTotal(0);
          setListError(r.error);
        }
      })
      .finally(() => setListLoading(false));
  };

  // page 改变时自动重查 (筛选改变手动按"查询")
  useEffect(() => {
    loadList();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page]);

  const onQuery = () => {
    setPage(0);
    setSearchMatches(null);  // 退出全文模式
    loadList();
  };

  const onFullTextSearch = () => {
    if (!q.trim()) return;
    setSearchLoading(true);
    fetchSessionsSearch(q, daysBack, 50)
      .then((r) => {
        if (r.ok) {
          setSearchMatches(r.data.matches);
        } else {
          setSearchMatches([]);
          setListError(r.error);
        }
      })
      .finally(() => setSearchLoading(false));
  };

  const onSelectSession = (id: string) => {
    setSelectedId(id);
    setDetail(null);
    setDetailError(null);
    setDetailLoading(true);
    fetchSessionDetail(id)
      .then((r) => {
        if (r.ok) setDetail(r.data);
        else setDetailError(r.error);
      })
      .finally(() => setDetailLoading(false));
  };

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "minmax(380px, 1fr) 2fr",
        gap: "var(--space-4)",
        height: "calc(100vh - 80px)",
      }}
    >
      {/* 左: 列表 + 搜索 */}
      <Card title="📚 会话历史 (跨日)">
        <div style={{ color: "var(--text-muted)", fontSize: 12, marginBottom: 8 }}>
          数据源: 你 mac 上 <code>~/.hermes/state.db</code>. 不上行中央, 只你能看.
        </div>

        {/* 搜索条 */}
        <div style={{ display: "flex", flexDirection: "column", gap: 6, marginBottom: 12 }}>
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") onQuery(); }}
            placeholder="搜关键字 (例 资质审核 / EIS / 客户 X)"
            style={inputStyle}
          />
          <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
            <select
              value={daysBack}
              onChange={(e) => setDaysBack(Number(e.target.value))}
              style={{ ...inputStyle, flex: "0 0 auto" }}
            >
              <option value={7}>过去 7 天</option>
              <option value={30}>过去 30 天</option>
              <option value={90}>过去 90 天</option>
              <option value={365}>过去 1 年</option>
            </select>
            <button onClick={onQuery} style={btn("primary")}>
              {listLoading ? "查询中…" : "session 级"}
            </button>
            <button onClick={onFullTextSearch} disabled={!q.trim()} style={btn("ghost")}>
              {searchLoading ? "搜索中…" : "命中行级"}
            </button>
          </div>
          <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
            session 级 = 整个会话含关键字; 命中行级 = 显示具体匹配行 (可定位上下文)
          </div>
        </div>

        {listError && (
          <div style={errBoxStyle}>
            <div style={{ fontWeight: 600, marginBottom: 6 }}>请求失败</div>
            <div style={{ fontFamily: "monospace", fontSize: 12 }}>{listError}</div>
            <div style={{ marginTop: 8, fontSize: 12 }}>
              gateway 没重启 / 端口不通 / OIDC token 过期 — 详见上面错误信息.
            </div>
          </div>
        )}

        {/* 命中行模式 */}
        {searchMatches !== null && (
          <div style={{ marginBottom: 8 }}>
            <div style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: 6 }}>
              命中 {searchMatches.length} 行, 点跳到对应 session
            </div>
            {searchMatches.length === 0 && (
              <div style={{ padding: 12, textAlign: "center", color: "var(--text-muted)" }}>
                没找到 — 试试换关键词 / 放宽时间
              </div>
            )}
            {searchMatches.map((m, i) => (
              <div
                key={i}
                onClick={() => onSelectSession(m.session_id)}
                style={{
                  padding: 8,
                  border: "1px solid var(--border)",
                  borderRadius: 6,
                  marginBottom: 6,
                  cursor: "pointer",
                  background: selectedId === m.session_id ? "var(--bg-elev)" : "transparent",
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between" }}>
                  <span style={roleBadge(m.role)}>{m.role}</span>
                  <span style={{ fontSize: 10, color: "var(--text-muted)" }}>
                    {m.created_at ? new Date(m.created_at * 1000).toLocaleString("zh-CN") : ""}
                  </span>
                </div>
                <div style={{ fontSize: 12, marginTop: 4, lineHeight: 1.5 }}>
                  {highlightMatch(m.snippet, q)}
                </div>
              </div>
            ))}
          </div>
        )}

        {/* session 级模式 */}
        {searchMatches === null && (
          <>
            {listLoading && <div style={{ color: "var(--text-muted)" }}>加载中…</div>}
            {!listLoading && sessions.length === 0 && !listError && (
              <div style={{ padding: 16, textAlign: "center", color: "var(--text-muted)" }}>
                空 — 试试放宽时间窗口, 或者你 hermes 还没起过 chat
              </div>
            )}
            {!listLoading && sessions.length > 0 && (
              <>
                <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 6 }}>
                  共 {total.toLocaleString()} 条, 当前 {page * PAGE_SIZE + 1}–
                  {page * PAGE_SIZE + sessions.length}
                </div>
                {sessions.map((s) => (
                  <SessionListItem
                    key={s.id}
                    session={s}
                    selected={selectedId === s.id}
                    onClick={() => onSelectSession(s.id)}
                  />
                ))}
                {/* 分页 */}
                <div style={{ display: "flex", gap: 8, marginTop: 12, alignItems: "center" }}>
                  <button
                    onClick={() => setPage((p) => Math.max(0, p - 1))}
                    disabled={page === 0}
                    style={btn("ghost")}
                  >
                    ← 上一页
                  </button>
                  <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
                    {page + 1} / {totalPages}
                  </span>
                  <button
                    onClick={() => setPage((p) => p + 1)}
                    disabled={page + 1 >= totalPages}
                    style={btn("ghost")}
                  >
                    下一页 →
                  </button>
                </div>
              </>
            )}
          </>
        )}
      </Card>

      {/* 右: 详情 */}
      <Card title={detail ? `会话: ${detail.title || detail.id}` : "选个会话看详情"}>
        {!selectedId && (
          <div style={{ color: "var(--text-muted)", padding: 24, textAlign: "center" }}>
            ← 左侧选一个 session, 这里看完整 messages
          </div>
        )}
        {selectedId && detailLoading && (
          <div style={{ color: "var(--text-muted)" }}>加载中…</div>
        )}
        {selectedId && detailError && (
          <div style={errBoxStyle}>
            <div style={{ fontFamily: "monospace", fontSize: 12 }}>{detailError}</div>
          </div>
        )}
        {detail && (
          <>
            <div style={{ marginBottom: 12, fontSize: 12, color: "var(--text-muted)" }}>
              <div>开始: {new Date(detail.started_at * 1000).toLocaleString("zh-CN")}</div>
              <div>消息: {detail.messages_returned} / 共 {detail.message_count} 条</div>
              <div>
                ID: <code>{detail.id}</code>
              </div>
              {detail.messages_returned < detail.message_count && (
                <div style={{ color: "#f59e0b", marginTop: 4 }}>
                  ⚠️ 长会话已截断, 只显示前 {detail.messages_returned} 条
                </div>
              )}
            </div>
            <div style={{ overflowY: "auto", maxHeight: "calc(100vh - 250px)" }}>
              {detail.messages.map((m) => (
                <MessageBubble key={m.id} message={m} />
              ))}
            </div>
          </>
        )}
      </Card>
    </div>
  );
}

function SessionListItem({
  session,
  selected,
  onClick,
}: {
  session: SessionRow;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <div
      onClick={onClick}
      style={{
        padding: 8,
        border: "1px solid " + (selected ? "var(--accent)" : "var(--border)"),
        borderRadius: 6,
        marginBottom: 6,
        cursor: "pointer",
        background: selected ? "var(--bg-elev)" : "transparent",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
        <span style={{ fontSize: 13, fontWeight: 500 }}>
          {session.title || "(无标题)"}
        </span>
        <span style={{ fontSize: 10, color: "var(--text-muted)" }}>
          {fmtRelTime(session.started_at)}
        </span>
      </div>
      <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 4 }}>
        {session.message_count} 条消息
      </div>
      {session.first_user_msg && (
        <div style={{ fontSize: 12, color: "var(--text-muted)", lineHeight: 1.4 }}>
          → {session.first_user_msg}
        </div>
      )}
    </div>
  );
}

function MessageBubble({ message }: { message: { role: string; content: string; created_at: number } }) {
  const isUser = message.role === "user";
  const isAssistant = message.role === "assistant";
  return (
    <div style={{ display: "flex", marginBottom: 12, justifyContent: isUser ? "flex-end" : "flex-start" }}>
      <div
        style={{
          maxWidth: "85%",
          padding: 10,
          borderRadius: 8,
          background: isUser
            ? "var(--accent)"
            : isAssistant
            ? "var(--bg-elev)"
            : "#fef3c7",
          color: isUser ? "white" : "var(--text)",
          fontSize: 13,
          lineHeight: 1.6,
        }}
      >
        <div style={{ fontSize: 10, opacity: 0.8, marginBottom: 4 }}>
          {message.role}
          {message.created_at > 0 && (
            <span style={{ marginLeft: 8 }}>
              {new Date(message.created_at * 1000).toLocaleString("zh-CN")}
            </span>
          )}
        </div>
        <div style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
          {message.content}
        </div>
      </div>
    </div>
  );
}

function fmtRelTime(ts: number): string {
  if (!ts) return "";
  const diff = (Date.now() / 1000 - ts);
  if (diff < 60) return "刚才";
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  if (diff < 30 * 86400) return `${Math.floor(diff / 86400)} 天前`;
  return new Date(ts * 1000).toLocaleDateString("zh-CN");
}

function highlightMatch(text: string, q: string) {
  if (!q.trim()) return text;
  const parts = text.split(new RegExp(`(${escapeRegex(q)})`, "i"));
  return parts.map((p, i) =>
    p.toLowerCase() === q.toLowerCase() ? (
      <mark key={i} style={{ background: "#fef08a", color: "#713f12" }}>{p}</mark>
    ) : (
      <span key={i}>{p}</span>
    ),
  );
}

function escapeRegex(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function roleBadge(role: string): React.CSSProperties {
  const color = role === "user" ? "#3b82f6" : role === "assistant" ? "#10b981" : "#6b7280";
  return {
    display: "inline-block",
    padding: "2px 6px",
    background: color + "22",
    color,
    borderRadius: 4,
    fontSize: 10,
    fontWeight: 500,
  };
}

const inputStyle: React.CSSProperties = {
  background: "var(--bg-elev)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius-sm)",
  padding: "6px 10px",
  color: "var(--text)",
  fontSize: 13,
  width: "100%",
  boxSizing: "border-box",
};

const errBoxStyle: React.CSSProperties = {
  padding: 10,
  background: "#fef2f2",
  border: "1px solid #fecaca",
  borderRadius: 6,
  color: "#991b1b",
  fontSize: 12,
};

function btn(variant: "primary" | "ghost"): React.CSSProperties {
  return {
    padding: "5px 10px",
    borderRadius: "var(--radius-sm)",
    border: "1px solid " + (variant === "primary" ? "var(--accent)" : "var(--border)"),
    background: variant === "primary" ? "var(--accent)" : "transparent",
    color: variant === "primary" ? "white" : "var(--text)",
    cursor: "pointer",
    fontSize: 12,
  };
}
