/** Admin 全局聚合卡 — 五一 sprint 5/2 RBAC.
 *
 * 数据: GET /api/quota/global + /api/audit/global
 * 内容:
 *   - 全员请求数 / 总 token / 活跃员工 / 活跃部门 (4 数字)
 *   - top 5 部门 (按 token)
 *   - top 5 模型分布
 *   - top 10 员工 (跨部门)
 *
 * RBAC: admin only — manager / employee 不显示这张卡 (DashboardTab role 控制).
 *
 * 30s 自动刷新.
 */

import { useEffect, useState } from "react";

import {
  fetchGlobalAudit,
  fetchGlobalQuota,
  type GlobalAudit,
  type GlobalQuota,
} from "../../lib/me";
import { formatTokens } from "../../lib/format";

const POLL_MS = 30_000;

export default function AdminGlobalCard() {
  const [quota, setQuota] = useState<GlobalQuota | null>(null);
  const [audit, setAudit] = useState<GlobalAudit | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const [q, a] = await Promise.all([fetchGlobalQuota(), fetchGlobalAudit()]);
        if (!cancelled) {
          setQuota(q);
          setAudit(a);
          if (!q && !a) setError("admin 端点拉空, 看 gateway log");
          else setError(null);
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      }
    };
    void load();
    const t = window.setInterval(() => void load(), POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(t);
    };
  }, []);

  return (
    <div
      style={{
        background: "var(--catfish-bg-elevated)",
        border: "1px solid var(--catfish-border)",
        borderRadius: "var(--radius-md)",
        padding: "var(--space-4)",
        gridColumn: "1 / -1",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          gap: "var(--space-2)",
          marginBottom: "var(--space-3)",
        }}
      >
        <h3 style={{ margin: 0 }}>🌐 全局聚合</h3>
        <span style={{ fontSize: 11, color: "var(--catfish-text-muted)" }}>
          管理员视图 · 今日全员 · 30s 刷新
        </span>
      </div>

      {error && !audit && (
        <div style={{ fontSize: 13, color: "var(--catfish-text-muted)" }}>
          {error}
        </div>
      )}

      {!audit && !error && (
        <div style={{ height: 50, background: "var(--catfish-border)", opacity: 0.4, borderRadius: 4 }} />
      )}

      {audit && audit.request_count === 0 && (
        <div style={{ fontSize: 13, color: "var(--catfish-text-muted)" }}>
          今日全员暂无活动 — 等员工开聊后这里会有数据
        </div>
      )}

      {audit && audit.request_count > 0 && (
        <>
          {/* 4 数字 */}
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(4, 1fr)",
              gap: "var(--space-3)",
              marginBottom: "var(--space-4)",
            }}
          >
            <Stat label="全员请求" value={String(audit.request_count)} />
            <Stat label="总 token" value={formatTokens(audit.total_tokens)} />
            <Stat label="活跃员工" value={String(audit.active_users)} />
            <Stat label="活跃部门" value={String(audit.active_departments)} />
          </div>

          {/* 部门分布 */}
          {audit.by_department.length > 0 && (
            <div style={{ marginBottom: "var(--space-4)" }}>
              <div
                style={{
                  fontSize: 12,
                  color: "var(--catfish-text-muted)",
                  marginBottom: "var(--space-2)",
                }}
              >
                部门分布 (top {Math.min(5, audit.by_department.length)}):
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                {audit.by_department.slice(0, 5).map((d) => (
                  <Row
                    key={d.department}
                    label={d.department}
                    count={d.count}
                    tokens={d.total_tokens}
                    max={audit.by_department[0]?.total_tokens || 1}
                  />
                ))}
              </div>
            </div>
          )}

          {/* 模型分布 */}
          {audit.by_model.length > 0 && (
            <div style={{ marginBottom: "var(--space-4)" }}>
              <div
                style={{
                  fontSize: 12,
                  color: "var(--catfish-text-muted)",
                  marginBottom: "var(--space-2)",
                }}
              >
                模型分布 (top {Math.min(5, audit.by_model.length)}):
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                {audit.by_model.slice(0, 5).map((m) => (
                  <Row
                    key={m.model}
                    label={m.model}
                    count={m.count}
                    tokens={m.total_tokens}
                    max={audit.by_model[0]?.total_tokens || 1}
                  />
                ))}
              </div>
            </div>
          )}

          {/* 跨部门 top 员工 */}
          {audit.by_user.length > 0 && (
            <div>
              <div
                style={{
                  fontSize: 12,
                  color: "var(--catfish-text-muted)",
                  marginBottom: "var(--space-2)",
                }}
              >
                Top {Math.min(10, audit.by_user.length)} 员工 (跨部门):
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                {audit.by_user.slice(0, 10).map((u) => (
                  <Row
                    key={u.user_email}
                    label={`${u.user_email} · ${u.department || "—"}`}
                    count={u.count}
                    tokens={u.total_tokens}
                    max={audit.by_user[0]?.total_tokens || 1}
                  />
                ))}
              </div>
            </div>
          )}
        </>
      )}

      {quota && quota.top_departments.length > 0 && (
        <div style={{ marginTop: "var(--space-3)", fontSize: 11, color: "var(--catfish-text-muted)" }}>
          quota 用量同 audit (来源 quota_events PG 表) · 改单部门限额走对应部门卡片
        </div>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div style={{ fontSize: 11, color: "var(--catfish-text-muted)", marginBottom: 2 }}>
        {label}
      </div>
      <div style={{ fontSize: 22, fontWeight: 600, fontFamily: "var(--font-mono)" }}>
        {value}
      </div>
    </div>
  );
}

function Row({
  label,
  count,
  tokens,
  max,
}: {
  label: string;
  count: number;
  tokens: number;
  max: number;
}) {
  const pct = max === 0 ? 0 : (tokens / max) * 100;
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "1fr 60px 90px",
        gap: 8,
        fontSize: 12,
        alignItems: "center",
      }}
    >
      <span
        style={{
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
          fontFamily: "var(--font-mono)",
        }}
        title={label}
      >
        {label}
      </span>
      <span
        style={{
          textAlign: "right",
          color: "var(--catfish-text-muted)",
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {count} 次
      </span>
      <span
        style={{
          textAlign: "right",
          color: "var(--catfish-text-muted)",
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {formatTokens(tokens)} tok
      </span>
      <span
        style={{
          gridColumn: "1 / -1",
          height: 3,
          background: "var(--catfish-border)",
          borderRadius: 2,
          overflow: "hidden",
          marginTop: 2,
        }}
      >
        <span
          style={{
            display: "block",
            height: "100%",
            width: `${pct}%`,
            background: "var(--catfish-cyan)",
          }}
        />
      </span>
    </div>
  );
}
