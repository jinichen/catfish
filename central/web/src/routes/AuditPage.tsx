/** /audit — 审计大查询 (manager + admin) (BL-ARCH1 5/10).
 *
 * P0: 列全公司今日 audit 数据 (audit by_user / by_model). admin 看全部, manager
 * 看本部门. 复用 fetchGlobalAudit (gateway 内部判 role 返过滤).
 */

import { useEffect, useState } from "react";

import { Card } from "../components/Card";
import { RoleGate } from "../components/RoleGate";
import {
  fetchGlobalAudit,
  type GlobalAudit,
} from "../lib/me";

function fmtTokens(n: number): string {
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}K`;
  return `${(n / 1_000_000).toFixed(2)}M`;
}

export function AuditPage() {
  const [audit, setAudit] = useState<GlobalAudit | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchGlobalAudit()
      .then((a) => {
        if (!a) setError("拉取 audit 失败 (没权限或后端报错)");
        else setAudit(a);
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  return (
    <RoleGate require={["manager", "admin"]}>
      <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
        {error && (
          <Card title="错误">
            <div style={{ color: "var(--status-err)" }}>{error}</div>
          </Card>
        )}
        {!audit && !error && <div>加载中…</div>}
        {audit && (
          <>
            <Card title="今日总览">
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(4, 1fr)",
                  gap: "var(--space-3)",
                }}
              >
                <Stat label="总请求" value={audit.request_count.toLocaleString()} />
                <Stat label="总 tokens" value={fmtTokens(audit.total_tokens)} />
                <Stat label="活跃员工" value={audit.active_users} />
                <Stat label="活跃部门" value={audit.active_departments} />
              </div>
              <div
                style={{
                  marginTop: "var(--space-3)",
                  fontSize: 11,
                  color: "var(--text-muted)",
                }}
              >
                数据范围: 最近 24h. 视角: {audit.viewer_role} (manager 看本部门, admin 全公司)
              </div>
            </Card>

            <Card title={`按模型 (${audit.by_model.length} 模型)`}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                <thead>
                  <tr style={{ borderBottom: "1px solid var(--border)" }}>
                    <th style={{ textAlign: "left", padding: "6px 8px" }}>模型</th>
                    <th style={{ textAlign: "right", padding: "6px 8px" }}>请求</th>
                    <th style={{ textAlign: "right", padding: "6px 8px" }}>tokens</th>
                  </tr>
                </thead>
                <tbody>
                  {audit.by_model.map((m) => (
                    <tr
                      key={m.model}
                      style={{ borderBottom: "1px solid var(--bg-secondary)" }}
                    >
                      <td style={{ padding: "4px 8px", fontFamily: "var(--font-mono)" }}>
                        {m.model}
                      </td>
                      <td style={{ padding: "4px 8px", textAlign: "right" }}>{m.count}</td>
                      <td style={{ padding: "4px 8px", textAlign: "right" }}>
                        {fmtTokens(m.total_tokens)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>

            {audit.by_department && audit.by_department.length > 0 && (
              <Card title={`按部门 (${audit.by_department.length} 部门)`}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                  <thead>
                    <tr style={{ borderBottom: "1px solid var(--border)" }}>
                      <th style={{ textAlign: "left", padding: "6px 8px" }}>部门</th>
                      <th style={{ textAlign: "right", padding: "6px 8px" }}>请求</th>
                      <th style={{ textAlign: "right", padding: "6px 8px" }}>tokens</th>
                    </tr>
                  </thead>
                  <tbody>
                    {audit.by_department.map((d) => (
                      <tr
                        key={d.department}
                        style={{ borderBottom: "1px solid var(--bg-secondary)" }}
                      >
                        <td style={{ padding: "4px 8px" }}>{d.department || "(无)"}</td>
                        <td style={{ padding: "4px 8px", textAlign: "right" }}>
                          {d.count}
                        </td>
                        <td style={{ padding: "4px 8px", textAlign: "right" }}>
                          {fmtTokens(d.total_tokens)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </Card>
            )}

            <Card title={`按员工 (top ${Math.min(audit.by_user.length, 50)})`}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                <thead>
                  <tr style={{ borderBottom: "1px solid var(--border)" }}>
                    <th style={{ textAlign: "left", padding: "6px 8px" }}>员工</th>
                    <th style={{ textAlign: "left", padding: "6px 8px" }}>部门</th>
                    <th style={{ textAlign: "right", padding: "6px 8px" }}>请求</th>
                    <th style={{ textAlign: "right", padding: "6px 8px" }}>tokens</th>
                  </tr>
                </thead>
                <tbody>
                  {audit.by_user.slice(0, 50).map((u) => (
                    <tr
                      key={u.user_email}
                      style={{ borderBottom: "1px solid var(--bg-secondary)" }}
                    >
                      <td style={{ padding: "4px 8px" }}>{u.user_email}</td>
                      <td style={{ padding: "4px 8px", color: "var(--text-muted)" }}>
                        {u.department}
                      </td>
                      <td style={{ padding: "4px 8px", textAlign: "right" }}>
                        {u.count}
                      </td>
                      <td style={{ padding: "4px 8px", textAlign: "right" }}>
                        {fmtTokens(u.total_tokens)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>
          </>
        )}
      </div>
    </RoleGate>
  );
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div>
      <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 2 }}>
        {label}
      </div>
      <div style={{ fontSize: 22, fontWeight: 600 }}>{value}</div>
    </div>
  );
}
