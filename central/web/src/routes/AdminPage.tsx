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
  fetchGlobalAudit,
  fetchGlobalQuota,
  type GlobalAudit,
  type GlobalQuota,
} from "../lib/me";
import { UsersPage } from "./admin/UsersPage";
import { SystemPage } from "./admin/SystemPage";
// BL-Q3-FACT P0 MVP Day 2 (5/10): 事实补丁系统 UI
import { FactsPage } from "./admin/FactsPage";

export function AdminPage() {
  // BL-ARCH1 P1 (5/10): admin 默认能进, sysadmin 看 system. /admin/users 内部不再
  // 强制 admin (sysadmin / admin 都进, 上游 admin_router 按 role 过滤 sysadmin 行).
  return (
    <RoleGate require={["admin", "sysadmin"]}>
      <Routes>
        <Route index element={<AdminHome />} />
        <Route path="users/*" element={<UsersPage />} />
        <Route path="system/*" element={<SystemPage />} />
        <Route path="facts/*" element={<FactsPage />} />
        <Route path="quota" element={<AdminQuota />} />
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
        {/* BL-Q3-FACT P0 MVP (5/10): 事实补丁系统 — 政策变更自动同步到员工 skill */}
        <NavTile to="/admin/facts" icon="📋" title="政策同步 (FACT)" desc="政策变更 → 找受影响 skill → 生成 patch" />
        <NavTile to="/admin/quota" icon="🎯" title="配额规则" desc="defaults / per_model / per_dept" />
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

function AdminQuota() {
  return (
    <Card title="配额规则 (P0 read-only)">
      <div style={{ color: "var(--text-muted)", fontSize: 13 }}>
        <p>
          配额规则在 <code>central/llm-gateway/config/quotas.yaml</code>.
          P1 加 web 编辑 UI (defaults / per_model / per_dept / per_user override).
        </p>
        <p>当前默认 (BL-FIX38, 5/10):</p>
        <ul style={{ paddingLeft: 20 }}>
          <li>per_user: 1M tok/min, 10M tok/day</li>
          <li>ceo override: 同上</li>
          <li>engineering / 研发部: tokens_per_day=0 (不限)</li>
        </ul>
      </div>
    </Card>
  );
}

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
