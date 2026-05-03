/** Manager / Admin 看本部门 Audit 聚合 — 五一 sprint 5/2 RBAC.
 *
 * 数据: GET /api/audit/department/{dept}
 * 内容:
 *   - 部门总请求数 / 总 token (今日 24h)
 *   - 模型分布 (top 5)
 *   - 员工分布 (top 10)
 *
 * 跟全局 AuditCard 区别: 这个只看 manager 自己的部门, 数据从 quota_events 算出.
 */

import { useEffect, useState } from "react";

import { fetchDepartmentAudit, type DepartmentAudit } from "../../lib/me";
import { formatTokens } from "../../lib/format";

interface Props {
  department: string;
}

export default function DepartmentAuditCard({ department }: Props) {
  const [data, setData] = useState<DepartmentAudit | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const a = await fetchDepartmentAudit(department);
        if (!cancelled) {
          setData(a);
          setError(null);
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      }
    };
    void load();
    const t = window.setInterval(() => void load(), 30_000);
    return () => {
      cancelled = true;
      window.clearInterval(t);
    };
  }, [department]);

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
      <div style={{ display: "flex", alignItems: "baseline", gap: "var(--space-2)", marginBottom: "var(--space-3)" }}>
        <h3 style={{ margin: 0 }}>📊 部门审计 · {department}</h3>
        <span style={{ fontSize: 11, color: "var(--catfish-text-muted)" }}>
          经理视图 · 今日聚合 · 30s 刷新
        </span>
      </div>

      {error && <div style={{ fontSize: 13, color: "var(--catfish-text-muted)" }}>读取失败: {error}</div>}
      {!data && !error && <div style={{ height: 40, background: "var(--catfish-border)", opacity: 0.4, borderRadius: 4 }} />}

      {data && data.request_count === 0 && (
        <div style={{ fontSize: 13, color: "var(--catfish-text-muted)" }}>
          今日部门暂无活动 — 等部门成员开聊后这里会有数据
        </div>
      )}

      {data && data.request_count > 0 && (
        <>
          {/* 总览 4 个数字 */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: "var(--space-3)", marginBottom: "var(--space-4)" }}>
            <Stat label="部门请求" value={String(data.request_count)} />
            <Stat label="总 token" value={formatTokens(data.total_tokens)} />
            <Stat label="活跃员工" value={String(data.by_user.length)} />
            <Stat label="使用模型" value={String(data.by_model.length)} />
          </div>

          {/* 模型分布 */}
          <div style={{ marginBottom: "var(--space-4)" }}>
            <div style={{ fontSize: 12, color: "var(--catfish-text-muted)", marginBottom: "var(--space-2)" }}>
              模型分布 (top {Math.min(5, data.by_model.length)}):
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              {data.by_model.slice(0, 5).map((m) => (
                <Row key={m.model} label={m.model} count={m.count} tokens={m.total_tokens} max={data.by_model[0]?.total_tokens || 1} />
              ))}
            </div>
          </div>

          {/* 员工分布 */}
          <div>
            <div style={{ fontSize: 12, color: "var(--catfish-text-muted)", marginBottom: "var(--space-2)" }}>
              员工分布 (top {Math.min(10, data.by_user.length)}):
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              {data.by_user.slice(0, 10).map((u) => (
                <Row key={u.user_email} label={u.user_email} count={u.count} tokens={u.total_tokens} max={data.by_user[0]?.total_tokens || 1} />
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div style={{ fontSize: 11, color: "var(--catfish-text-muted)", marginBottom: 2 }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 600, fontFamily: "var(--font-mono)" }}>{value}</div>
    </div>
  );
}

function Row({ label, count, tokens, max }: { label: string; count: number; tokens: number; max: number }) {
  const pct = max === 0 ? 0 : (tokens / max) * 100;
  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 60px 90px", gap: 8, fontSize: 12, alignItems: "center" }}>
      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontFamily: "var(--font-mono)" }} title={label}>
        {label}
      </span>
      <span style={{ textAlign: "right", color: "var(--catfish-text-muted)", fontVariantNumeric: "tabular-nums" }}>
        {count} 次
      </span>
      <span style={{ textAlign: "right", color: "var(--catfish-text-muted)", fontVariantNumeric: "tabular-nums" }}>
        {formatTokens(tokens)} tok
      </span>
      <span style={{ gridColumn: "1 / -1", height: 3, background: "var(--catfish-border)", borderRadius: 2, overflow: "hidden", marginTop: 2 }}>
        <span style={{ display: "block", height: "100%", width: `${pct}%`, background: "var(--catfish-cyan)" }} />
      </span>
    </div>
  );
}
