/** /admin/system — sysadmin 系统管理 (BL-ARCH1 P1 5/10).
 *
 * 系统级操作, 只 sysadmin 看到. 内容:
 *   - 服务状态 (gateway / identity / mcp / hub / broker 5 个端口)
 *   - 系统配置查看 (OIDC issuer / audience / quotas.yaml)
 *   - 操作审计 (users_audit)
 *   - 危险操作 (重启 / 备份)
 */

import { useEffect, useState } from "react";

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

      {/* 7/30: 这里原本有个「快捷入口」区, 重复了用户管理 / 配额规则 / 审计
          大查询三个入口 —— 它们在 /admin 磁贴上也有一份。同一个入口需要放在
          两处, 通常就是缺一个常驻导航的信号。现在左侧栏是常驻的, 这一区删掉。 */}

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

      {/* BL-HOMEPAGE-DEDUPE follow-up (5/17 鸿波): "危险操作" 卡删除 — 整张卡是
          P1 待加项的纯文字 placeholder, 没真按钮, UI 上是空头支票. 真危险操作
          (重启 service / 改 yaml / dev_users 编辑) 当前走 ssh + 改文件, 不该在
          web UI 引导员工"我能在这做这些事" — 容易误认为有按钮就能点. 等真接入
          后台 API 再加回这张卡, 现在空叫无意义.
          注: doc string 顶部还保留'危险操作 (重启/备份)' 项目说明作为 future
          backlog, 但不渲染卡片. */}
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
