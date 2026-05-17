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
            {/* BL-AUDIT-P0-FIX (5/17): 标题口径统一 — "员工 LLM 使用 · 最近 24h",
                不再"今日总览"vs"最近 24h" 互打架. 视角说明压到副标题 1 行内.
                "员工业务/排除内部循环" 详情塞到 ⓘ 旁的 tooltip 提示文案.

                BL-AUDIT-P0-FIX-V2 (5/17): role 不止 "admin", sysadmin 也是全公司
                视角. 4 个 role: sysadmin/admin/manager/employee — 前两个看全公司,
                manager 看本部门, employee 拿不到这个端点 (RoleGate 拦). */}
            <Card
              title={
                audit.viewer_role === "admin" || audit.viewer_role === "sysadmin"
                  ? "员工 LLM 使用 · 最近 24h · 全公司"
                  : "员工 LLM 使用 · 最近 24h · 本部门"
              }
            >
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
                <Stat
                  label="活跃部门"
                  value={audit.active_departments}
                  hint="只数有部门归属的员工"
                />
              </div>
              <div
                style={{
                  marginTop: "var(--space-3)",
                  fontSize: 11,
                  color: "var(--text-muted)",
                }}
                title="此处不含 gateway 内部循环 (summarizer / proactive / 5 维 inject 等), 见下方"
              >
                <span style={{ cursor: "help" }}>ⓘ 不含 gateway 内部循环消耗</span>
              </div>
            </Card>

            {/* BL-AUDIT-INTERNAL-SPLIT (5/17): internal loopback 单独显示, 给 sysadmin 看透明度.
                数据 = gateway 自己跑的 summarizer / distill / 5 维 inject 等内部循环消耗.
                跟员工业务无关, 但是真消耗 token (上游 LLM 计费). */}
            {(audit.internal_tokens ?? 0) > 0 && (
              <Card title="Gateway 内部循环消耗 (audit 透明度)">
                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "repeat(2, 1fr)",
                    gap: "var(--space-3)",
                  }}
                >
                  <Stat
                    label="内部请求"
                    value={(audit.internal_request_count ?? 0).toLocaleString()}
                  />
                  <Stat
                    label="内部 tokens"
                    value={fmtTokens(audit.internal_tokens ?? 0)}
                  />
                </div>
                <div
                  style={{
                    marginTop: "var(--space-3)",
                    fontSize: 11,
                    color: "var(--text-muted)",
                  }}
                >
                  来源: <code>internal:gateway-loopback</code> / <code>internal:summarizer</code> 等.
                  gateway 自己跑 session summarize / proactive task / 5 维 memory inject 时消耗.
                  <br />
                  跟员工业务**无关**, 但占真实账单 token. 想优化看 BL-CACHE-AUDIT (#76) +
                  压缩 inject (#76 后续).
                </div>
              </Card>
            )}

            <Card title={`按模型 · ${audit.by_model.length} 项`}>
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
                      <td style={{ padding: "4px 8px", textAlign: "right" }}>
                        {m.count.toLocaleString()}
                      </td>
                      <td style={{ padding: "4px 8px", textAlign: "right" }}>
                        {fmtTokens(m.total_tokens)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>

            {/* BL-AUDIT-P0-FIX (5/17): 数据对账提示.
                ① "(未分组)" 桶 backend 已加, 不再吞 dept 空的员工 → 总数对得上.
                ② 校验各表加和 == 总数, 不一致 显式提示 (sanity check). */}
            <DataReconciliation audit={audit} />

            {audit.by_department && audit.by_department.length > 0 && (
              <Card title={`按部门 · ${audit.by_department.length} 项`}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                  <thead>
                    <tr style={{ borderBottom: "1px solid var(--border)" }}>
                      <th style={{ textAlign: "left", padding: "6px 8px" }}>部门</th>
                      <th style={{ textAlign: "right", padding: "6px 8px" }}>请求</th>
                      <th style={{ textAlign: "right", padding: "6px 8px" }}>tokens</th>
                    </tr>
                  </thead>
                  <tbody>
                    {audit.by_department.map((d) => {
                      const isUnassigned = d.department === "(未分组)";
                      return (
                        <tr
                          key={d.department}
                          style={{ borderBottom: "1px solid var(--bg-secondary)" }}
                        >
                          <td
                            style={{
                              padding: "4px 8px",
                              fontStyle: isUnassigned ? "italic" : "normal",
                              color: isUnassigned ? "var(--text-muted)" : undefined,
                            }}
                            title={
                              isUnassigned
                                ? "该桶里的员工没绑部门 (dev_token / 老员工 / OIDC 缺 dept claim). 接 Day 8 客户接入手册要求 SSO 必传 dept."
                                : undefined
                            }
                          >
                            {d.department}
                          </td>
                          <td style={{ padding: "4px 8px", textAlign: "right" }}>
                            {d.count.toLocaleString()}
                          </td>
                          <td style={{ padding: "4px 8px", textAlign: "right" }}>
                            {fmtTokens(d.total_tokens)}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </Card>
            )}

            <Card
              title={
                audit.by_user.length === 0
                  ? "按员工 · 无数据"
                  : `按员工 · ${audit.by_user.length} 项${audit.by_user.length >= 50 ? " (上限)" : ""}`
              }
            >
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
                  {audit.by_user.map((u) => {
                    const isUnassigned = u.user_email === "(未分组员工)";
                    return (
                      <tr
                        key={`${u.user_email}::${u.department}`}
                        style={{ borderBottom: "1px solid var(--bg-secondary)" }}
                      >
                        <td
                          style={{
                            padding: "4px 8px",
                            fontStyle: isUnassigned ? "italic" : "normal",
                            color: isUnassigned ? "var(--text-muted)" : undefined,
                          }}
                          title={
                            isUnassigned
                              ? "该桶里的请求来源没拿到 user_email (dev_token / OIDC 缺 email claim)."
                              : undefined
                          }
                        >
                          {u.user_email}
                        </td>
                        <td style={{ padding: "4px 8px", color: "var(--text-muted)" }}>
                          {u.department}
                        </td>
                        <td style={{ padding: "4px 8px", textAlign: "right" }}>
                          {u.count.toLocaleString()}
                        </td>
                        <td style={{ padding: "4px 8px", textAlign: "right" }}>
                          {fmtTokens(u.total_tokens)}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </Card>
          </>
        )}
      </div>
    </RoleGate>
  );
}

function Stat({
  label,
  value,
  hint,
}: {
  label: string;
  value: string | number;
  hint?: string;
}) {
  return (
    <div>
      <div
        style={{
          fontSize: 11,
          color: "var(--text-muted)",
          marginBottom: 2,
        }}
        title={hint}
      >
        {label}
        {hint && (
          <span style={{ marginLeft: 4, cursor: "help" }} aria-label={hint}>
            ⓘ
          </span>
        )}
      </div>
      <div style={{ fontSize: 22, fontWeight: 600 }}>{value}</div>
    </div>
  );
}

/**
 * BL-AUDIT-P0-FIX (5/17): 数据对账 banner.
 *
 * 用途: 把 backend 返的总数 vs 各分组加和 比对一遍, 不一致就显式提示, 阻止
 * "总数 405 但 按部门加和 117" 这种"数据消失"印象损 audit 信任.
 *
 * 数据可信 = audit 类页面的生命线 (PM 视角). 任何细小不一致都被客户记 1 笔.
 */
function DataReconciliation({ audit }: { audit: GlobalAudit }) {
  const modelSum = audit.by_model.reduce((s, m) => s + m.count, 0);
  const deptSum = audit.by_department?.reduce((s, d) => s + d.count, 0) ?? 0;
  const userSum = audit.by_user.reduce((s, u) => s + u.count, 0);

  const modelOk = modelSum === audit.request_count;
  const deptOk = deptSum === audit.request_count;
  const userOk = userSum === audit.request_count;

  // 全对得上 — 不显示 banner (静默 OK 不打扰)
  if (modelOk && deptOk && userOk) {
    return null;
  }

  // 至少 1 个对不上 — 显式提示, 帮 sysadmin 排查 backend SQL
  const rows: { label: string; sum: number; ok: boolean }[] = [
    { label: "按模型加和", sum: modelSum, ok: modelOk },
    { label: "按部门加和", sum: deptSum, ok: deptOk },
    { label: "按员工加和", sum: userSum, ok: userOk },
  ];

  return (
    <Card title="⚠️ 数据对账 (部分分组加和 ≠ 总数)">
      <div style={{ fontSize: 12, lineHeight: 1.6 }}>
        总请求 <strong>{audit.request_count.toLocaleString()}</strong>, 但:
        <ul style={{ margin: "6px 0 6px 18px", padding: 0 }}>
          {rows.map((r) => (
            <li key={r.label} style={{ color: r.ok ? "inherit" : "var(--status-warn, #b45309)" }}>
              {r.label} = <strong>{r.sum.toLocaleString()}</strong>{" "}
              {r.ok ? "✓" : `✗ (差 ${(audit.request_count - r.sum).toLocaleString()})`}
            </li>
          ))}
        </ul>
        <div style={{ color: "var(--text-muted)", fontSize: 11 }}>
          典型原因: 老 audit 数据 user_email / department 缺 OIDC claim → 进 "(未分组)"
          桶 (本表已显式列出). 跑 <code>POST /api/admin/audit/backfill</code> 回填可清零差额.
        </div>
      </div>
    </Card>
  );
}
