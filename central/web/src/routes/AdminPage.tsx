/** /admin — Admin 后台 (admin only) (BL-ARCH1 5/10).
 *
 * 全公司聚合 / 用户管理 / 配额规则 / billing.
 * P0 范围: 全局聚合 + 用户列表 (read-only) + 跳转链接.
 * P1: dev_users 编辑 / users.yaml 编辑 / billing 月报.
 */

import { useEffect, useState } from "react";
import { Routes, Route, Link } from "react-router-dom";

import { Card, Row } from "../components/Card";
import { RoleGate } from "../components/RoleGate";
import {
  fetchAuditEvents,
  fetchGlobalAudit,
  fetchGlobalQuota,
  type AuditEvent,
  type AuditEventsResponse,
  type GlobalAudit,
  type GlobalQuota,
} from "../lib/me";
import { UsersPage } from "./admin/UsersPage";
import { SystemPage } from "./admin/SystemPage";
// BL-Q3-FACT P0 MVP Day 2 (5/10): 事实补丁系统 UI
import { FactsPage } from "./admin/FactsPage";
// BL-RBAC-DAY7 (5/17): 4 维 RBAC dept 管理
import { AccessPage } from "./admin/AccessPage";
// 6/7 BL-MANIFESTO-ADVISORY-PHASE2: Advisory 管理 (sysadmin only)
import { AdvisoryPage } from "./admin/AdvisoryPage";
// P3.5.60 (6/22 鸿波 catch "继续完成"): 全公司 LLM 性能仪表
import { PerfPage } from "./admin/PerfPage";
// P3.5.93 (6/23 鸿波): /admin/quota 真编辑 UI, 替原 AdminQuota P0 placeholder.
// 一并治 AccessPage 部门 quota 6 周 dead UI (gateway 不读 identity-server).
import { ModelConfigPage } from "./admin/ModelConfigPage";
import { QuotaConfigPage } from "./admin/QuotaConfigPage";

export function AdminPage() {
  // BL-ARCH1 P1 (5/10): admin 默认能进, sysadmin 看 system. /admin/users 内部不再
  // 强制 admin (sysadmin / admin 都进, 上游 admin_router 按 role 过滤 sysadmin 行).
  return (
    <RoleGate require={["admin", "sysadmin"]}>
      <Routes>
        <Route index element={<AdminHome />} />
        <Route path="users/*" element={<UsersPage />} />
        <Route path="access/*" element={<AccessPage />} />
        <Route path="advisory" element={<AdvisoryPage />} />
        <Route path="system/*" element={<SystemPage />} />
        <Route path="facts/*" element={<FactsPage />} />
        {/* P3.5.93 (6/23 鸿波): /admin/quota 改用 QuotaConfigPage (sysadmin 真编辑).
            老 AdminQuota static placeholder 函数已无路径引用, 可以砍但保 dead code
            等下个 sprint 清, 不在 P3.5.93 scope. */}
        <Route path="quota" element={<QuotaConfigPage />} />
        {/* 7/30: 模型增删改. 在这之前只能编辑 models.yaml 再重启, 而那个文件
            在容器里是只读挂载 —— 客户现场根本没有改模型这条路. */}
        <Route path="models" element={<ModelConfigPage />} />
        {/* BL-ADMIN-AUDIT (5/12 鸿波): 逐条 audit 历史 */}
        <Route path="quota/events" element={<AdminQuotaEvents />} />
        {/* P3.5.60 (6/22 鸿波): 全公司 LLM 性能仪表 (latency p50/p95/p99) */}
        <Route path="perf" element={<PerfPage />} />
        <Route path="billing" element={<AdminBilling />} />
      </Routes>
    </RoleGate>
  );
}

function fmtTokens(n: number): string {
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}K`;
  return `${(n / 1_000_000).toFixed(2)}M`;
}

function AdminHome() {
  const [globalQ, setGlobalQ] = useState<GlobalQuota | null>(null);
  const [globalA, setGlobalA] = useState<GlobalAudit | null>(null);

  useEffect(() => {
    fetchGlobalQuota().then(setGlobalQ);
    fetchGlobalAudit().then(setGlobalA);
  }, []);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
      <Card title="全公司今日">
        {!globalA && <div>加载中…</div>}
        {globalA && (
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(4, 1fr)",
              gap: "var(--space-3)",
            }}
          >
            <Stat label="总请求" value={globalA.request_count.toLocaleString()} />
            <Stat label="总 tokens" value={fmtTokens(globalA.total_tokens)} />
            <Stat label="活跃员工" value={globalA.active_users} />
            <Stat label="活跃部门" value={globalA.active_departments} />
          </div>
        )}
      </Card>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))",
          gap: "var(--space-3)",
        }}
      >
        <NavTile to="/admin/users" icon="👥" title="用户管理" desc="创建 / 改 role / 锁 / 删 / 重置密码" />
        {/* BL-RBAC-DAY7 (5/17): 4 维 RBAC dept 配置 */}
        <NavTile to="/admin/access" icon="🔑" title="部门 RBAC" desc="模型 / 工具 / 技能 / 配额 — 按部门配置" />
        {/* BL-Q3-FACT P0 MVP (5/10): 事实补丁系统 — 政策变更自动同步到员工 skill */}
        <NavTile to="/admin/facts" icon="📋" title="政策同步 (FACT)" desc="政策变更 → 找受影响 skill → 生成 patch" />
        <NavTile to="/admin/advisory" icon="🛡" title="Advisory 管理 (sysadmin)" desc="publish + revoke. pull-based, 跟 fleet 强 push 反向" />
        <NavTile to="/admin/models" icon="🧠" title="模型配置 (sysadmin)" desc="增删改模型 / 上游接入 / 能力标记" />
        <NavTile to="/admin/quota" icon="🎯" title="配额规则" desc="defaults / per_model / per_dept" />
        {/* BL-ADMIN-AUDIT (5/12 鸿波): 逐条 quota / audit 历史日志 */}
        <NavTile to="/admin/quota/events" icon="📊" title="Quota 历史日志" desc="逐条 + 4 维筛选 + 分页 + CSV" />
        {/* P3.5.60 (6/22 鸿波): 全公司 LLM 性能仪表 */}
        <NavTile to="/admin/perf" icon="📈" title="LLM 性能仪表" desc="latency p50/p95/p99 · by model · by dept" />
        <NavTile to="/admin/billing" icon="💰" title="Billing" desc="月报 / 按部门成本分摊" />
        <NavTile to="/audit" icon="📜" title="审计大查询" desc="跨员工 / 跨部门" />
        {/* sysadmin only — 用 RoleGate 包还是放这里都行, 这里直接靠 NavBar tab 区分 */}
        <NavTile to="/admin/system" icon="🔐" title="系统管理 (sysadmin)" desc="服务状态 / 危险操作 / 操作审计" />
      </div>

      {globalQ && globalQ.top_departments.length > 0 && (
        <Card title="部门 token 用量 top 5 (今日)">
          {globalQ.top_departments.slice(0, 5).map((d) => (
            <Row
              key={d.department}
              label={d.department}
              value={`${d.request_count} 请求 · ${fmtTokens(d.tokens_used)} tok`}
            />
          ))}
        </Card>
      )}

      {globalA && globalA.by_model.length > 0 && (
        <Card title="模型用量 (今日)">
          {globalA.by_model.map((m) => (
            <Row
              key={m.model}
              label={m.model}
              value={`${m.count} 次 · ${fmtTokens(m.total_tokens)} tok`}
            />
          ))}
        </Card>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div>
      <div
        style={{
          fontSize: 11,
          color: "var(--text-muted)",
          marginBottom: 2,
        }}
      >
        {label}
      </div>
      <div style={{ fontSize: 22, fontWeight: 600 }}>{value}</div>
    </div>
  );
}

function NavTile({
  to,
  icon,
  title,
  desc,
}: {
  to: string;
  icon: string;
  title: string;
  desc: string;
}) {
  return (
    <Link
      to={to}
      style={{
        background: "var(--bg-elev)",
        border: "1px solid var(--border)",
        borderRadius: "var(--radius-md)",
        padding: "var(--space-3)",
        color: "var(--text)",
        textDecoration: "none",
        display: "flex",
        gap: "var(--space-3)",
        alignItems: "flex-start",
      }}
      onMouseEnter={(e) => (e.currentTarget.style.borderColor = "var(--accent)")}
      onMouseLeave={(e) => (e.currentTarget.style.borderColor = "var(--border)")}
    >
      <div style={{ fontSize: 22 }}>{icon}</div>
      <div>
        <div style={{ fontWeight: 500 }}>{title}</div>
        <div style={{ fontSize: 11, color: "var(--text-muted)" }}>{desc}</div>
      </div>
    </Link>
  );
}

// AdminUsers 旧 inline 版本删了 (BL-ARCH1 P1, 被 routes/admin/UsersPage.tsx 完整 CRUD 替代).

// P3.5.93 (6/23 鸿波): 老 AdminQuota P0 placeholder 函数砍 — /admin/quota
// 已切到 QuotaConfigPage 真编辑. 文本里写的 "P1 加 web 编辑 UI" 6 周后真做了.

function AdminBilling() {
  return (
    <Card title="Billing 月报">
      <div style={{ color: "var(--text-muted)", fontSize: 13 }}>
        <p>P1 实现. 设计:</p>
        <ul style={{ paddingLeft: 20 }}>
          <li>按月统计 token 用量 / 请求次数 / 模型分布</li>
          <li>按部门 / 项目分摊成本</li>
          <li>导出 PDF / Excel</li>
          <li>同期对比 (本月 vs 上月)</li>
        </ul>
      </div>
    </Card>
  );
}

// BL-ADMIN-AUDIT (5/12 鸿波): /admin/quota/events — 逐条 audit 历史 + 筛选 + 分页 + CSV.
//
// 跟 /admin/quota 配额规则 read-only 文本互补 (一个看规则, 一个看实际数据).
// API: GET /api/audit/events (gateway, RBAC admin only — sysadmin 走 is_admin() 通过).

function AdminQuotaEvents() {
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
