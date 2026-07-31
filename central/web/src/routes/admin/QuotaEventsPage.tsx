/** /admin/quota/events —— 逐条配额日志.
 *
 * 7/30 从 AdminPage.tsx 拆出来 (CLAUDE.md 军规 §1)。见 AdminHome.tsx 文件头。
 *
 * ⚠ 这个文件里还留着一套自己的 thStyle / tdStyle / btnStyle / StatusBadge ——
 * 也就是 DataTable.tsx 文件头说的"6 份徽章 5 份按钮"里的一份。这一轮没换,
 * 因为换它要连带重排这张 10 列的表, 跟"拆文件"不是一件事, 混在一起
 * review 不清。下次动这一页时顺手换掉。
 */

import { useEffect, useState } from "react";

import { Card } from "../../components/Card";
import {
  fetchAuditEvents,
  type AuditEvent,
  type AuditEventsResponse,
} from "../../lib/me";

// BL-ADMIN-AUDIT (5/12 鸿波): /admin/quota/events — 逐条 audit 历史 + 筛选 + 分页 + CSV.
//
// 跟 /admin/quota 配额规则 read-only 文本互补 (一个看规则, 一个看实际数据).
// API: GET /api/audit/events (gateway, RBAC admin only — sysadmin 走 is_admin() 通过).

export function AdminQuotaEvents() {
  const PAGE_SIZE = 50;
  const [data, setData] = useState<AuditEventsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  // 4 维筛选 + 时间窗口
  const [hoursBack, setHoursBack] = useState(24);
  const [dept, setDept] = useState("");
  const [userFilter, setUserFilter] = useState("");
  const [model, setModel] = useState("");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(0);

  const load = () => {
    setLoading(true);
    setError(null);
    const since_ms = Date.now() - hoursBack * 3600 * 1000;
    fetchAuditEvents({
      since_ms,
      dept: dept || undefined,
      user_filter: userFilter || undefined,
      model: model || undefined,
      status: status || undefined,
      limit: PAGE_SIZE,
      offset: page * PAGE_SIZE,
    })
      .then((r) => {
        if (r.ok) {
          setData(r.data);
        } else {
          setData(null);
          setError(r.error);
        }
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
    // 不进 deps — 手动 "查询" 按钮触发, 防一边输一边狂查 PG
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page]);

  const totalPages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;

  const exportCSV = () => {
    if (!data || data.events.length === 0) return;
    const header = [
      "ts", "user", "department", "model", "tokens_in", "tokens_out",
      "tokens_total", "latency_ms", "ttft_ms", "status", "error",
    ].join(",");
    const rows = data.events.map((e) => [
      new Date(e.ts * 1000).toISOString(),
      csvEscape(e.user),
      csvEscape(e.department),
      csvEscape(e.model),
      e.prompt_tokens,
      e.completion_tokens,
      e.total_tokens,
      e.latency_ms,
      e.ttft_ms ?? "",
      csvEscape(e.status),
      csvEscape(e.error ?? ""),
    ].join(","));
    const blob = new Blob([header + "\n" + rows.join("\n")], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `audit-events-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)" }}>
      <Card title="Quota 历史日志 (逐条)">
        <div style={{ color: "var(--text-muted)", fontSize: 12, marginBottom: 8 }}>
          直接读 gateway_audit 表. 跟 /admin/quota (规则) 互补 — 这里看实际数据,
          那里看配置. RBAC 严格 admin only (sysadmin 通过).
        </div>

        {/* 筛选条 */}
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 12 }}>
          <FilterField label="时间范围 (小时)">
            <select
              value={hoursBack}
              onChange={(e) => setHoursBack(Number(e.target.value))}
              style={inputStyle}
            >
              <option value={1}>过去 1h</option>
              <option value={6}>过去 6h</option>
              <option value={24}>过去 24h</option>
              <option value={72}>过去 3 天</option>
              <option value={168}>过去 7 天</option>
              <option value={720}>过去 30 天</option>
            </select>
          </FilterField>
          <FilterField label="部门">
            <input
              value={dept}
              onChange={(e) => setDept(e.target.value)}
              placeholder="例 engineering"
              style={inputStyle}
            />
          </FilterField>
          <FilterField label="员工 email">
            <input
              value={userFilter}
              onChange={(e) => setUserFilter(e.target.value)}
              placeholder="例 alice@ffcs.cn"
              style={inputStyle}
            />
          </FilterField>
          <FilterField label="模型">
            <input
              value={model}
              onChange={(e) => setModel(e.target.value)}
              placeholder="例 catfish-private-main"
              style={inputStyle}
            />
          </FilterField>
          <FilterField label="状态">
            <select
              value={status}
              onChange={(e) => setStatus(e.target.value)}
              style={inputStyle}
            >
              <option value="">全部</option>
              <option value="ok">ok</option>
              <option value="error">error</option>
              <option value="interrupted_resumed">interrupted_resumed</option>
            </select>
          </FilterField>
          <div style={{ display: "flex", alignItems: "flex-end", gap: 8 }}>
            <button
              onClick={() => { setPage(0); load(); }}
              style={btnStyle("primary")}
            >
              {loading ? "查询中…" : "查询"}
            </button>
            <button
              onClick={exportCSV}
              disabled={!data || data.events.length === 0}
              style={btnStyle("ghost")}
            >
              导出 CSV
            </button>
          </div>
        </div>

        {/* 结果 */}
        {loading && <div style={{ color: "var(--text-muted)" }}>加载中…</div>}
        {!loading && error && (
          <div
            style={{
              padding: 12,
              background: "#fef2f2",
              border: "1px solid #fecaca",
              borderRadius: 6,
              color: "#991b1b",
              fontSize: 13,
              lineHeight: 1.5,
            }}
          >
            <div style={{ fontWeight: 600, marginBottom: 6 }}>请求失败</div>
            <div style={{ fontFamily: "monospace", fontSize: 12 }}>{error}</div>
            <div style={{ marginTop: 10, color: "#7f1d1d" }}>
              常见原因:
              <ul style={{ paddingLeft: 20, margin: "4px 0 0 0" }}>
                <li><b>404 not found</b>: gateway 还没重启 — 新 endpoint <code>/api/audit/events</code> 是这次加的, 跑 <code>pkill -f catfish_gateway && sleep 3</code> 然后重启 gateway</li>
                <li><b>403 forbidden</b>: 当前 role 不是 admin / sysadmin (理论上你 sysadmin 不会撞到)</li>
                <li><b>401 unauthorized</b>: OIDC token 过期, 退出重登</li>
                <li><b>network error</b>: gateway 进程没起 / 端口换了</li>
              </ul>
            </div>
          </div>
        )}
        {!loading && !error && data && data.events.length === 0 && (
          <div style={{ color: "var(--text-muted)", padding: 16, textAlign: "center" }}>
            没有匹配的事件 — 试试放宽时间范围 / 清空筛选
          </div>
        )}
        {!loading && !error && data && data.events.length > 0 && (
          <>
            <div style={{ color: "var(--text-muted)", fontSize: 12, marginBottom: 6 }}>
              共 {data.total.toLocaleString()} 条, 当前 {page * PAGE_SIZE + 1}–
              {page * PAGE_SIZE + data.events.length} 条
            </div>
            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                <thead>
                  <tr style={{ borderBottom: "1px solid var(--border)" }}>
                    <th style={thStyle}>时间</th>
                    <th style={thStyle}>员工</th>
                    <th style={thStyle}>部门</th>
                    <th style={thStyle}>模型</th>
                    <th style={{ ...thStyle, textAlign: "right" }}>in</th>
                    <th style={{ ...thStyle, textAlign: "right" }}>out</th>
                    <th style={{ ...thStyle, textAlign: "right" }}>tot</th>
                    <th style={{ ...thStyle, textAlign: "right" }}>延迟 ms</th>
                    <th style={thStyle}>状态</th>
                    <th style={thStyle}>错误</th>
                  </tr>
                </thead>
                <tbody>
                  {data.events.map((e: AuditEvent, i) => (
                    <tr key={i} style={{ borderBottom: "1px solid var(--border)" }}>
                      <td style={tdStyle}>{new Date(e.ts * 1000).toLocaleString("zh-CN")}</td>
                      <td style={tdStyle}>{e.user}</td>
                      <td style={tdStyle}>{e.department}</td>
                      <td style={tdStyle}>{e.model}</td>
                      <td style={{ ...tdStyle, textAlign: "right" }}>{e.prompt_tokens}</td>
                      <td style={{ ...tdStyle, textAlign: "right" }}>{e.completion_tokens}</td>
                      <td style={{ ...tdStyle, textAlign: "right", fontWeight: 500 }}>{e.total_tokens}</td>
                      <td style={{ ...tdStyle, textAlign: "right" }}>{Math.round(e.latency_ms)}</td>
                      <td style={tdStyle}>
                        <StatusBadge status={e.status} />
                      </td>
                      <td style={{ ...tdStyle, color: "var(--text-muted)", maxWidth: 280, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                        title={e.error}
                      >
                        {e.error ?? ""}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {/* 分页 */}
            <div style={{ display: "flex", gap: 8, marginTop: 12, alignItems: "center" }}>
              <button
                onClick={() => setPage((p) => Math.max(0, p - 1))}
                disabled={page === 0}
                style={btnStyle("ghost")}
              >
                ← 上一页
              </button>
              <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
                第 {page + 1} / {totalPages} 页
              </span>
              <button
                onClick={() => setPage((p) => p + 1)}
                disabled={page + 1 >= totalPages}
                style={btnStyle("ghost")}
              >
                下一页 →
              </button>
            </div>
          </>
        )}
      </Card>
    </div>
  );
}

function csvEscape(s: string | number): string {
  const v = String(s);
  if (v.includes(",") || v.includes('"') || v.includes("\n")) {
    return '"' + v.replace(/"/g, '""') + '"';
  }
  return v;
}

function FilterField({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <span style={{ fontSize: 11, color: "var(--text-muted)" }}>{label}</span>
      {children}
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const color =
    status === "ok"
      ? "#10b981"
      : status === "interrupted_resumed"
      ? "#f59e0b"
      : "#ef4444";
  return (
    <span
      style={{
        display: "inline-block",
        padding: "2px 8px",
        borderRadius: 4,
        background: color + "22",
        color,
        fontSize: 11,
        fontWeight: 500,
      }}
    >
      {status}
    </span>
  );
}

const inputStyle: React.CSSProperties = {
  background: "var(--bg-elev)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius-sm)",
  padding: "4px 8px",
  color: "var(--text)",
  fontSize: 12,
  minWidth: 160,
};

const thStyle: React.CSSProperties = {
  padding: "6px 10px",
  textAlign: "left",
  fontWeight: 500,
  color: "var(--text-muted)",
  fontSize: 11,
  whiteSpace: "nowrap",
};

const tdStyle: React.CSSProperties = {
  padding: "6px 10px",
  whiteSpace: "nowrap",
};

function btnStyle(variant: "primary" | "ghost"): React.CSSProperties {
  return {
    padding: "6px 14px",
    borderRadius: "var(--radius-sm)",
    border: "1px solid " + (variant === "primary" ? "var(--accent)" : "var(--border)"),
    background: variant === "primary" ? "var(--accent)" : "transparent",
    color: variant === "primary" ? "white" : "var(--text)",
    cursor: "pointer",
    fontSize: 12,
  };
}
