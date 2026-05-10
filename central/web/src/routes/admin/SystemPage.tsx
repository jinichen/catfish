/** /admin/system — sysadmin 系统管理 (BL-ARCH1 P1 5/10).
 *
 * 系统级操作, 只 sysadmin 看到. 内容:
 *   - 服务状态 (gateway / identity / mcp / hub / broker 5 个端口)
 *   - 系统配置查看 (OIDC issuer / audience / quotas.yaml)
 *   - 操作审计 (users_audit)
 *   - 危险操作 (重启 / 备份)
 */

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { Card } from "../../components/Card";
import { RoleGate } from "../../components/RoleGate";
import { adminApi, type UsersAuditEvent } from "../../lib/admin";

export function SystemPage() {
  return (
    <RoleGate require="sysadmin">
      <SystemDashboard />
    </RoleGate>
  );
}


function SystemDashboard() {
  const [audit, setAudit] = useState<UsersAuditEvent[]>([]);
  const [services, setServices] = useState<ServiceStatus[]>([]);

  useEffect(() => {
    adminApi.listAudit(50).then((r) => setAudit(r.events));
    void checkServices().then(setServices);
  }, []);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
      <Card title="🔐 系统管理 (sysadmin)">
        <div style={{ color: "var(--text-muted)", fontSize: 13 }}>
          只 sysadmin 看. 你是系统超级管理员, 这里管 admin 账号 / 系统配置 / 服务状态.
        </div>
      </Card>

      <Card title="服务状态">
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
            gap: "var(--space-2)",
          }}
        >
          {services.map((s) => (
            <div
              key={s.name}
              style={{
                background: "var(--bg-secondary)",
                padding: "var(--space-2) var(--space-3)",
                borderRadius: "var(--radius-sm)",
                fontSize: 13,
              }}
            >
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  marginBottom: 2,
                }}
              >
                <span style={{ fontWeight: 500 }}>{s.name}</span>
                <span
                  style={{
                    color: s.ok ? "var(--status-ok)" : "var(--status-err)",
                    fontSize: 12,
                  }}
                >
                  {s.ok ? "● ok" : "● 不可达"}
                </span>
              </div>
              <div style={{ fontSize: 11, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
                {s.url}
              </div>
            </div>
          ))}
        </div>
      </Card>

      <Card title="快捷入口 (sysadmin only)">
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
            gap: "var(--space-3)",
          }}
        >
          <NavTile to="/admin/users" icon="👥" title="用户管理" desc="创建 admin / 改 role / 锁账号" />
          <NavTile to="/admin/quota" icon="🎯" title="配额规则" desc="quotas.yaml" />
          <NavTile to="/audit" icon="📜" title="审计大查询" desc="跨员工 / 跨部门" />
        </div>
      </Card>

      <Card title={`用户操作审计 (最近 ${audit.length} 条)`}>
        {audit.length === 0 ? (
          <div style={{ color: "var(--text-muted)" }}>没数据</div>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
              <thead>
                <tr style={{ borderBottom: "1px solid var(--border)" }}>
                  <th style={{ textAlign: "left", padding: "4px 8px" }}>时间</th>
                  <th style={{ textAlign: "left", padding: "4px 8px" }}>动作</th>
                  <th style={{ textAlign: "left", padding: "4px 8px" }}>对象</th>
                  <th style={{ textAlign: "left", padding: "4px 8px" }}>操作者</th>
                  <th style={{ textAlign: "left", padding: "4px 8px" }}>详情</th>
                </tr>
              </thead>
              <tbody>
                {audit.map((e) => (
                  <tr
                    key={e.ts_ms + e.target_email}
                    style={{ borderBottom: "1px solid var(--bg-secondary)" }}
                  >
                    <td style={{ padding: "4px 8px", fontFamily: "var(--font-mono)" }}>
                      {new Date(e.ts_ms).toLocaleString("zh-CN", {
                        hour12: false,
                        month: "2-digit",
                        day: "2-digit",
                        hour: "2-digit",
                        minute: "2-digit",
                      })}
                    </td>
                    <td style={{ padding: "4px 8px" }}>
                      <ActionTag action={e.action} />
                    </td>
                    <td style={{ padding: "4px 8px" }}>{e.target_email}</td>
                    <td style={{ padding: "4px 8px", color: "var(--text-muted)" }}>
                      {e.by_email}
                    </td>
                    <td
                      style={{
                        padding: "4px 8px",
                        fontSize: 11,
                        color: "var(--text-muted)",
                        fontFamily: "var(--font-mono)",
                      }}
                    >
                      {Object.keys(e.meta || {}).length > 0
                        ? JSON.stringify(e.meta).slice(0, 80)
                        : "-"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title="⚠️ 危险操作 (sysadmin only)">
        <div style={{ color: "var(--text-muted)", fontSize: 13 }}>
          <p>这些操作会影响整个系统, 谨慎执行. P1 待加:</p>
          <ul style={{ paddingLeft: 20 }}>
            <li>系统服务重启 (gateway / identity / mcp / hub / broker)</li>
            <li>PG 数据库备份 / 恢复</li>
            <li>OIDC 配置热更 (改 audience / 加客户自建 SSO)</li>
            <li>quotas.yaml 编辑 (改 default / per_dept override)</li>
            <li>dev_users 编辑 (多角色测试账号)</li>
            <li>导出全公司 audit 月报 (合规)</li>
          </ul>
          <p style={{ marginTop: "var(--space-3)" }}>
            目前: 改 yaml 还是 ssh 改文件后重启 service.
          </p>
        </div>
      </Card>
    </div>
  );
}


function ActionTag({ action }: { action: string }) {
  const colors: Record<string, string> = {
    create: "var(--status-ok)",
    update: "var(--accent)",
    delete: "var(--status-err)",
    lock: "var(--status-warn)",
    unlock: "var(--status-ok)",
    reset_password: "var(--status-warn)",
  };
  return (
    <span
      style={{
        background: colors[action] || "var(--text-muted)",
        color: "white",
        padding: "1px 8px",
        borderRadius: "var(--radius-sm)",
        fontSize: 11,
      }}
    >
      {action}
    </span>
  );
}


function NavTile({ to, icon, title, desc }: { to: string; icon: string; title: string; desc: string }) {
  return (
    <Link
      to={to}
      style={{
        background: "var(--bg-secondary)",
        border: "1px solid var(--border)",
        borderRadius: "var(--radius-sm)",
        padding: "var(--space-3)",
        color: "var(--text)",
        textDecoration: "none",
        display: "flex",
        gap: "var(--space-2)",
      }}
    >
      <div style={{ fontSize: 22 }}>{icon}</div>
      <div>
        <div style={{ fontWeight: 500 }}>{title}</div>
        <div style={{ fontSize: 11, color: "var(--text-muted)" }}>{desc}</div>
      </div>
    </Link>
  );
}


// ── 服务状态检查 ────────────────────────────────────────────────


interface ServiceStatus {
  name: string;
  url: string;
  ok: boolean;
}

async function checkServices(): Promise<ServiceStatus[]> {
  // 通过 gateway 反代探活. dev 直连本机, prod nginx 反代.
  const { getIdToken } = await import("../../lib/auth");
  const token = (await getIdToken()) || "";

  const services: Array<{ name: string; url: string }> = [
    { name: "Gateway", url: "/api/me" },                         // 200 = gateway 自己 ok + OIDC 通
    { name: "MCP Registry", url: "/v1/mcp/registry" },           // 反代到 :8996
    { name: "Skills Hub", url: "/v1/hub/healthz" },              // 反代到 :8997
    { name: "Identity (admin)", url: "/api/admin/me-as-admin" }, // 反代到 :8998
  ];

  return Promise.all(
    services.map(async (s) => {
      try {
        const r = await fetch(s.url, {
          headers: { Authorization: `Bearer ${token}` },
        });
        return { name: s.name, url: s.url, ok: r.ok };
      } catch {
        return { name: s.name, url: s.url, ok: false };
      }
    }),
  );
}
