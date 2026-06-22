/** /admin/perf — 全公司 LLM 性能仪表 (P3.5.60, 6/22 鸿波 catch "继续完成").
 *
 * 跟 /audit 区别:
 *   - /audit 走 quota_events → 看 token / cost / 配额, 没 latency
 *   - /admin/perf 走 gateway_audit → 看 latency p50/p95/p99 + ttft, 看慢
 *
 * 数据合同: GET /api/audit/global/perf (admin only, app.py).
 * 后端聚合: catfish_gateway.metrics.query_perf_summary_global, PG 主路径 +
 *           JSONL fallback. 跟 PerfCard (Companion 端) 一致, 不是分散逻辑.
 *
 * 设计: 复用 AuditKPI 的 Card / Stat 风格, 不引新组件. 时间窗 (24h/7d/30d)
 *       + 模型/部门 drill-down filter. 跟 audit 同一套 UX. 不做 chart (recharts
 *       重), 拿 table + grid 表达. 后期要曲线 BL-PERF-CHARTS 再加.
 */

import { useEffect, useState } from "react";

import { Card } from "../../components/Card";
import { fetchGlobalPerf, type GlobalPerf } from "../../lib/me";

const TIME_WINDOWS = [
  { hours: 24, label: "24h" },
  { hours: 168, label: "7d" },
  { hours: 720, label: "30d" },
];

function fmtMs(v: number | null | undefined): string {
  if (v == null) return "—";
  if (v < 1000) return `${Math.round(v)} ms`;
  return `${(v / 1000).toFixed(2)} s`;
}

function fmtTokens(n: number): string {
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}K`;
  return `${(n / 1_000_000).toFixed(2)}M`;
}

function fmtPct(n: number): string {
  return `${(n * 100).toFixed(1)}%`;
}

/** Health rating — 跟 PerfCard 一致, 给 KPI 上色. */
function ratingP95(p95: number | null): "good" | "warn" | "bad" {
  if (p95 == null) return "good";
  if (p95 < 5_000) return "good";
  if (p95 < 30_000) return "warn";
  return "bad";
}

function colorOfRating(r: "good" | "warn" | "bad"): string {
  return r === "good"
    ? "var(--status-ok, #15803d)"
    : r === "warn"
      ? "var(--status-warn, #b45309)"
      : "var(--status-bad, #b91c1c)";
}

function Stat({
  label,
  value,
  hint,
  color,
}: {
  label: string;
  value: string;
  hint?: string;
  color?: string;
}) {
  return (
    <div>
      <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 2 }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 600, color: color ?? "var(--text)" }}>{value}</div>
      {hint && (
        <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>{hint}</div>
      )}
    </div>
  );
}

function SourceBadge({ source }: { source: GlobalPerf["source"] }) {
  const map: Record<GlobalPerf["source"], { txt: string; bg: string }> = {
    pg: { txt: "🏢 中央 PG", bg: "#dcfce7" },
    jsonl: { txt: "📄 中央 JSONL", bg: "#fef9c3" },
    none: { txt: "⚠️ 无数据", bg: "#fee2e2" },
  };
  const m = map[source];
  return (
    <span
      style={{
        fontSize: 11,
        background: m.bg,
        color: "#374151",
        padding: "2px 8px",
        borderRadius: 4,
      }}
    >
      {m.txt}
    </span>
  );
}

export function PerfPage() {
  const [hours, setHours] = useState<number>(24);
  const [modelFilter, setModelFilter] = useState<string>("");
  const [deptFilter, setDeptFilter] = useState<string>("");
  const [perf, setPerf] = useState<GlobalPerf | null>(null);
  const [loading, setLoading] = useState<boolean>(false);
  // P3.5.60.1 (6/22 鸿波 catch "是太慢还是没有数据"): 不静默吞 error,
  // loading / error / empty 三态各自展示, 用户能立刻区分.
  const [error, setError] = useState<{ status?: number; message: string } | null>(null);
  const [fetchedAt, setFetchedAt] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    const startMs = Date.now();
    fetchGlobalPerf(hours, {
      model: modelFilter || null,
      dept: deptFilter || null,
    }).then((r) => {
      if (cancelled) return;
      setPerf(r.data);
      setError(r.error);
      setLoading(false);
      setFetchedAt(Date.now() - startMs);
    });
    return () => {
      cancelled = true;
    };
  }, [hours, modelFilter, deptFilter]);

  const successRate = perf && perf.request_count > 0 ? perf.ok_count / perf.request_count : 0;
  const rating = ratingP95(perf?.latency_p95_ms ?? null);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
      {/* 控制条: 时间窗 + filter pill */}
      <Card
        title="📈 LLM 性能仪表"
        action={perf && <SourceBadge source={perf.source} />}
      >
        <div
          style={{
            display: "flex",
            gap: "var(--space-3)",
            flexWrap: "wrap",
            alignItems: "center",
          }}
        >
          <div>
            <span style={{ fontSize: 12, color: "var(--text-muted)", marginRight: 6 }}>
              时间窗:
            </span>
            {TIME_WINDOWS.map((w) => (
              <button
                key={w.hours}
                onClick={() => setHours(w.hours)}
                style={{
                  padding: "4px 10px",
                  marginRight: 4,
                  border: "1px solid var(--border)",
                  borderRadius: 4,
                  background:
                    hours === w.hours ? "var(--accent)" : "var(--bg-elev)",
                  color: hours === w.hours ? "#fff" : "var(--text)",
                  cursor: "pointer",
                  fontSize: 12,
                }}
              >
                {w.label}
              </button>
            ))}
          </div>
          <div>
            <span style={{ fontSize: 12, color: "var(--text-muted)", marginRight: 6 }}>
              model:
            </span>
            <input
              value={modelFilter}
              onChange={(e) => setModelFilter(e.target.value)}
              placeholder="全部"
              style={{
                padding: "3px 8px",
                border: "1px solid var(--border)",
                borderRadius: 4,
                fontSize: 12,
                width: 180,
              }}
            />
          </div>
          <div>
            <span style={{ fontSize: 12, color: "var(--text-muted)", marginRight: 6 }}>
              department:
            </span>
            <input
              value={deptFilter}
              onChange={(e) => setDeptFilter(e.target.value)}
              placeholder="全部"
              style={{
                padding: "3px 8px",
                border: "1px solid var(--border)",
                borderRadius: 4,
                fontSize: 12,
                width: 140,
              }}
            />
          </div>
          {loading && (
            <span style={{ fontSize: 12, color: "var(--text-muted)" }}>加载中…</span>
          )}
          {!loading && fetchedAt != null && perf && !error && (
            <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
              · {fetchedAt}ms
            </span>
          )}
        </div>
      </Card>

      {/* P3.5.60.1 (6/22 鸿波 catch "是太慢还是没有数据"): 3 态明确展示, 不再静默. */}
      {loading && !perf && !error && (
        <Card title="">
          <div style={{ color: "var(--text-muted)", textAlign: "center", padding: 24 }}>
            🔄 正在拉中央 audit, 长窗口 (7d/30d) PG PERCENTILE_CONT 几秒…
          </div>
        </Card>
      )}

      {error && (
        <Card title="❌ 请求失败">
          <div style={{ padding: 12 }}>
            <div style={{ fontSize: 13, color: "var(--status-bad, #b91c1c)", marginBottom: 8 }}>
              {error.status === 404
                ? "endpoint /api/audit/global/perf 没注册 (404) — gateway 进程没重启? 这是 P3.5.60 新加 endpoint, 老 gateway 没这路由. 重启 8999 进程加载新 endpoint."
                : error.status === 401
                  ? "401 未认证 — 需要 admin/sysadmin role 才能看 /admin/perf."
                  : error.status === 403
                    ? "403 权限不够 — RBAC 卡了, 看 _require_admin 通过没."
                    : error.status
                      ? `HTTP ${error.status} — 中央 gateway 报错, 看 /var/log/catfish-gateway/ 或 docker logs catfish-gateway`
                      : `网络 / fetch 失败: ${error.message.slice(0, 200)}`}
            </div>
            <details style={{ fontSize: 11, color: "var(--text-muted)" }}>
              <summary style={{ cursor: "pointer" }}>真实错误 (devtool 用)</summary>
              <pre
                style={{
                  background: "var(--bg-elev)",
                  padding: 8,
                  borderRadius: 4,
                  marginTop: 6,
                  overflowX: "auto",
                  whiteSpace: "pre-wrap",
                }}
              >
                status: {error.status ?? "(none)"}
                {"\n"}
                message: {error.message}
              </pre>
            </details>
          </div>
        </Card>
      )}

      {/* KPI grid */}
      {perf && !error && (
        <Card title="核心指标">
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
              gap: "var(--space-3)",
            }}
          >
            <Stat
              label="总调用"
              value={perf.request_count.toLocaleString()}
              hint={`成功 ${perf.ok_count.toLocaleString()} · 错 ${perf.error_count.toLocaleString()}`}
            />
            <Stat
              label="成功率"
              value={fmtPct(successRate)}
              color={
                successRate >= 0.99
                  ? "var(--status-ok, #15803d)"
                  : successRate >= 0.9
                    ? "var(--status-warn, #b45309)"
                    : "var(--status-bad, #b91c1c)"
              }
            />
            <Stat label="总 tokens" value={fmtTokens(perf.total_tokens)} />
            <Stat
              label="活跃员工"
              value={String(perf.active_users)}
              hint={`${perf.active_departments} 部门`}
            />
            <Stat
              label="Latency p50"
              value={fmtMs(perf.latency_p50_ms)}
              color={colorOfRating(rating)}
            />
            <Stat
              label="Latency p95"
              value={fmtMs(perf.latency_p95_ms)}
              color={colorOfRating(rating)}
            />
            <Stat
              label="Latency p99"
              value={fmtMs(perf.latency_p99_ms)}
              color={colorOfRating(rating)}
            />
            <Stat label="TTFT p50" value={fmtMs(perf.ttft_p50_ms)} />
            <Stat label="TTFT p95" value={fmtMs(perf.ttft_p95_ms)} />
          </div>
        </Card>
      )}

      {/* By model 表 */}
      {perf && perf.by_model.length > 0 && (
        <Card title={`按模型 (top ${perf.by_model.length})`}>
          <div style={{ overflowX: "auto" }}>
            <table style={tableStyle}>
              <thead>
                <tr>
                  <th style={thStyle}>Model</th>
                  <th style={thRightStyle}>调用</th>
                  <th style={thRightStyle}>错误</th>
                  <th style={thRightStyle}>错误率</th>
                  <th style={thRightStyle}>p50</th>
                  <th style={thRightStyle}>p99</th>
                  <th style={thRightStyle}>tokens</th>
                </tr>
              </thead>
              <tbody>
                {perf.by_model.map((m) => {
                  const errRate = m.count > 0 ? m.error_count / m.count : 0;
                  return (
                    <tr key={m.model}>
                      <td style={tdStyle}>
                        <button
                          onClick={() => setModelFilter(m.model)}
                          style={linkBtn}
                          title="点击 filter 该模型"
                        >
                          {m.model || "(未知)"}
                        </button>
                      </td>
                      <td style={tdRightStyle}>{m.count.toLocaleString()}</td>
                      <td style={tdRightStyle}>{m.error_count}</td>
                      <td
                        style={{
                          ...tdRightStyle,
                          color: errRate > 0.05 ? "var(--status-bad, #b91c1c)" : undefined,
                        }}
                      >
                        {fmtPct(errRate)}
                      </td>
                      <td style={tdRightStyle}>{fmtMs(m.p50_ms)}</td>
                      <td style={tdRightStyle}>{fmtMs(m.p99_ms)}</td>
                      <td style={tdRightStyle}>{fmtTokens(m.total_tokens)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {/* By department 表 */}
      {perf && perf.by_department.length > 0 && (
        <Card title={`按部门 (top ${perf.by_department.length})`}>
          <div style={{ overflowX: "auto" }}>
            <table style={tableStyle}>
              <thead>
                <tr>
                  <th style={thStyle}>Department</th>
                  <th style={thRightStyle}>员工</th>
                  <th style={thRightStyle}>调用</th>
                  <th style={thRightStyle}>错误</th>
                  <th style={thRightStyle}>p50</th>
                  <th style={thRightStyle}>p99</th>
                  <th style={thRightStyle}>tokens</th>
                </tr>
              </thead>
              <tbody>
                {perf.by_department.map((d) => (
                  <tr key={d.department}>
                    <td style={tdStyle}>
                      <button
                        onClick={() => setDeptFilter(d.department)}
                        style={linkBtn}
                        title="点击 filter 该部门"
                      >
                        {d.department}
                      </button>
                    </td>
                    <td style={tdRightStyle}>{d.active_users}</td>
                    <td style={tdRightStyle}>{d.count.toLocaleString()}</td>
                    <td style={tdRightStyle}>{d.error_count}</td>
                    <td style={tdRightStyle}>{fmtMs(d.p50_ms)}</td>
                    <td style={tdRightStyle}>{fmtMs(d.p99_ms)}</td>
                    <td style={tdRightStyle}>{fmtTokens(d.total_tokens)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {/* 空状态 — perf 是真 0, 不是 fetch 失败 (失败上面 error Card 接管了) */}
      {perf && !error && perf.request_count === 0 && !loading && (
        <Card title="📭 这窗口内没数据">
          <div style={{ color: "var(--text-muted)", padding: 16 }}>
            <p style={{ margin: "0 0 8px 0" }}>
              {perf.source === "none"
                ? "中央 gateway_audit 表 + JSONL 都空 (新部署 / 还没员工跑过 LLM)."
                : `${perf.source === "pg" ? "PG gateway_audit" : "JSONL audit"} 这窗口内没记录.`}
            </p>
            <p style={{ margin: 0, fontSize: 12 }}>
              试: 拉长窗口 (7d/30d), 砍 filter, 或先去 Companion 跟小鲶聊几句让 audit 累点数据.
            </p>
          </div>
        </Card>
      )}

      {/* schema note: 数据零出端透明声明 */}
      {perf?.schema_note && (
        <div
          style={{
            fontSize: 11,
            color: "var(--text-muted)",
            padding: "var(--space-2)",
            borderLeft: "3px solid var(--border)",
            paddingLeft: "var(--space-3)",
          }}
        >
          {perf.schema_note}
        </div>
      )}
    </div>
  );
}

const tableStyle: React.CSSProperties = {
  width: "100%",
  borderCollapse: "collapse",
  fontSize: 13,
};

const thStyle: React.CSSProperties = {
  textAlign: "left",
  padding: "6px 8px",
  borderBottom: "1px solid var(--border)",
  fontSize: 11,
  color: "var(--text-muted)",
  fontWeight: 500,
};

const thRightStyle: React.CSSProperties = {
  ...thStyle,
  textAlign: "right",
};

const tdStyle: React.CSSProperties = {
  padding: "6px 8px",
  borderBottom: "1px solid var(--border)",
};

const tdRightStyle: React.CSSProperties = {
  ...tdStyle,
  textAlign: "right",
  fontVariantNumeric: "tabular-nums",
};

const linkBtn: React.CSSProperties = {
  background: "none",
  border: "none",
  color: "var(--accent, #2563eb)",
  cursor: "pointer",
  padding: 0,
  fontSize: 13,
  textDecoration: "underline",
  textUnderlineOffset: 2,
};
