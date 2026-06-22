/** P3.5.59 (6/22 鸿波 catch "现在无法知道我们的性能的状态"):
 *  性能统计卡 — 综合两个数据源:
 *  - useAudit: gateway LLM call perf (TTFT p50/p95, request count, model usage)
 *  - fetchToolPerfSummary: tool-bridge tool dispatch perf (per-tool count/success/p50/p95/p99)
 *
 *  数据 100% 员工本机 jsonl 直读, 不走网络. 符合数据零出端红线.
 *
 *  现状跟 AuditViewCard 区别: AuditViewCard 只看 LLM (gateway audit), 这里多看
 *  tool-bridge dispatch 那一半, 一站式聚合.
 */

import { useEffect, useState } from "react";

import { useAudit } from "../../../hooks/useAudit";
import { fetchToolPerfSummary, type ToolPerfSummary } from "../../../lib/tool_perf";

const POLL_MS = 30_000;
const DEFAULT_WINDOW_HOURS = 24;

/** 把秒数格成 "刚刚" / "N 分钟前" / "N 小时前" — 数据新鲜度展示 */
function formatFreshness(secs: number): string {
  if (secs < 0) return "无数据";
  if (secs < 60) return `${secs}s 前`;
  if (secs < 3600) return `${Math.floor(secs / 60)} 分钟前`;
  if (secs < 86400) return `${Math.floor(secs / 3600)} 小时前`;
  return `${Math.floor(secs / 86400)} 天前`;
}

function formatMs(ms: number | null | undefined): string {
  if (ms == null) return "—";
  if (ms < 1) return "<1ms";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(2)}s`;
}

function formatPct(pct: number): string {
  return `${(pct * 100).toFixed(1)}%`;
}

export default function PerfCard() {
  const { summary: gatewaySummary, error: gatewayError } = useAudit();
  const [toolPerf, setToolPerf] = useState<ToolPerfSummary | null>(null);
  const [toolError, setToolError] = useState<string | null>(null);
  const [windowHours, setWindowHours] = useState<number>(DEFAULT_WINDOW_HOURS);

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const s = await fetchToolPerfSummary(windowHours);
        if (!cancelled) {
          setToolPerf(s);
          setToolError(null);
        }
      } catch (e) {
        if (!cancelled) setToolError(String(e));
      }
    };
    void tick();
    const t = setInterval(tick, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [windowHours]);

  return (
    <div style={{ fontSize: 13, color: "var(--catfish-text)" }}>
      {/* 顶部时间窗切换 */}
      <div
        style={{
          display: "flex",
          gap: 8,
          alignItems: "center",
          marginBottom: 12,
          fontSize: 12,
          color: "var(--catfish-text-muted)",
        }}
      >
        <span>时间窗:</span>
        {[1, 24, 168, 0].map((h) => (
          <button
            key={h}
            type="button"
            onClick={() => setWindowHours(h)}
            style={{
              background: windowHours === h ? "var(--catfish-cyan)" : "transparent",
              color: windowHours === h ? "#fff" : "var(--catfish-text)",
              border: "1px solid var(--catfish-border)",
              borderRadius: 4,
              padding: "2px 8px",
              fontSize: 11,
              cursor: "pointer",
              fontFamily: "inherit",
            }}
          >
            {h === 0 ? "全 tail" : h === 1 ? "1h" : h === 24 ? "24h" : "7d"}
          </button>
        ))}
        <span style={{ marginLeft: "auto" }}>
          数据 100% 本机 fs · 不出端 · 30s 自动刷
        </span>
      </div>

      {/* ─── Section 1: LLM call (gateway audit) ───────────────────── */}
      <div style={{ marginBottom: 16 }}>
        <div
          style={{
            fontWeight: 600,
            marginBottom: 8,
            color: "var(--catfish-text)",
            fontSize: 13,
          }}
        >
          🤖 LLM 调用性能 (gateway audit · 今天)
        </div>
        {gatewayError && (
          <div style={{ color: "var(--status-err)", fontSize: 11 }}>
            ✗ gateway audit 读失败: {gatewayError}
          </div>
        )}
        {gatewaySummary && (
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(110px, 1fr))",
              gap: 8,
            }}
          >
            <StatBox label="总请求" value={String(gatewaySummary.request_count)} />
            <StatBox
              label="成功率"
              value={
                gatewaySummary.request_count > 0
                  ? formatPct(
                      gatewaySummary.ok_count / gatewaySummary.request_count,
                    )
                  : "—"
              }
              accent={
                gatewaySummary.request_count > 0 &&
                gatewaySummary.error_count / gatewaySummary.request_count > 0.1
                  ? "warn"
                  : undefined
              }
            />
            <StatBox
              label="错误数"
              value={String(gatewaySummary.error_count)}
              accent={gatewaySummary.error_count > 0 ? "warn" : undefined}
            />
            <StatBox
              label="TTFT p50"
              value={formatMs(gatewaySummary.ttft_p50_ms)}
            />
            <StatBox
              label="TTFT p95"
              value={formatMs(gatewaySummary.ttft_p95_ms)}
            />
            <StatBox
              label="总 tokens"
              value={gatewaySummary.total_tokens.toLocaleString()}
            />
          </div>
        )}

        {/* 模型用量 */}
        {gatewaySummary && gatewaySummary.by_model.length > 0 && (
          <div style={{ marginTop: 8, fontSize: 11, color: "var(--catfish-text-muted)" }}>
            <span style={{ marginRight: 8 }}>模型用量:</span>
            {gatewaySummary.by_model.slice(0, 4).map((m) => (
              <span
                key={m.model}
                style={{
                  display: "inline-block",
                  marginRight: 8,
                  padding: "2px 6px",
                  background: m.is_private
                    ? "rgba(34, 197, 94, 0.1)"
                    : "rgba(59, 130, 246, 0.1)",
                  color: m.is_private ? "rgb(21, 128, 61)" : "rgb(30, 64, 175)",
                  borderRadius: 3,
                }}
                title={m.is_private ? "私有 (内网, 数据不出端)" : "公网"}
              >
                {m.is_private ? "🔒" : "🌐"} {m.model} ({m.count})
              </span>
            ))}
          </div>
        )}
      </div>

      {/* ─── Section 2: Tool dispatch (tool-bridge audit) ──────────── */}
      <div>
        <div
          style={{
            fontWeight: 600,
            marginBottom: 8,
            color: "var(--catfish-text)",
            fontSize: 13,
          }}
        >
          🔧 工具调用性能 (tool-bridge audit ·{" "}
          {windowHours === 0 ? "全 tail" : `${windowHours}h`})
        </div>
        {toolError && (
          <div style={{ color: "var(--status-err)", fontSize: 11 }}>
            ✗ tool audit 读失败: {toolError}
          </div>
        )}
        {toolPerf && toolPerf.total_calls === 0 && (
          <div style={{ color: "var(--catfish-text-muted)", fontSize: 12 }}>
            🐠 此时间窗没工具调用 (tool-bridge 还没跑过 / 全在更早).
            {toolPerf.lines_scanned > 0 && (
              <span> (scan {toolPerf.lines_scanned} 行)</span>
            )}
          </div>
        )}
        {toolPerf && toolPerf.total_calls > 0 && (
          <>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(110px, 1fr))",
                gap: 8,
                marginBottom: 12,
              }}
            >
              <StatBox label="总调用" value={String(toolPerf.total_calls)} />
              <StatBox
                label="成功率"
                value={formatPct(toolPerf.overall_success_rate)}
                accent={toolPerf.overall_success_rate < 0.9 ? "warn" : undefined}
              />
              <StatBox
                label="错误数"
                value={String(toolPerf.total_error)}
                accent={toolPerf.total_error > 0 ? "warn" : undefined}
              />
              <StatBox label="p50" value={formatMs(toolPerf.overall_p50_ms)} />
              <StatBox label="p95" value={formatMs(toolPerf.overall_p95_ms)} />
              <StatBox label="p99" value={formatMs(toolPerf.overall_p99_ms)} />
            </div>

            {/* per-tool 表格 */}
            <div style={{ fontSize: 11, marginBottom: 4, color: "var(--catfish-text-muted)" }}>
              按 tool 拆分 (前 12, count desc):
            </div>
            <table
              style={{
                width: "100%",
                borderCollapse: "collapse",
                fontSize: 11,
              }}
            >
              <thead>
                <tr style={{ color: "var(--catfish-text-muted)", textAlign: "left" }}>
                  <th style={{ padding: "4px 6px" }}>工具</th>
                  <th style={{ padding: "4px 6px", textAlign: "right" }}>调用</th>
                  <th style={{ padding: "4px 6px", textAlign: "right" }}>成功率</th>
                  <th style={{ padding: "4px 6px", textAlign: "right" }}>p50</th>
                  <th style={{ padding: "4px 6px", textAlign: "right" }}>p95</th>
                  <th style={{ padding: "4px 6px", textAlign: "right" }}>p99</th>
                </tr>
              </thead>
              <tbody>
                {toolPerf.by_tool.slice(0, 12).map((t) => (
                  <tr
                    key={t.tool}
                    style={{ borderTop: "1px solid var(--catfish-border)" }}
                  >
                    <td
                      style={{
                        padding: "4px 6px",
                        fontFamily: "ui-monospace, monospace",
                        wordBreak: "break-all",
                      }}
                    >
                      {t.tool}
                    </td>
                    <td style={{ padding: "4px 6px", textAlign: "right" }}>
                      {t.count}
                    </td>
                    <td
                      style={{
                        padding: "4px 6px",
                        textAlign: "right",
                        color: t.success_rate < 0.9 ? "rgb(220, 80, 60)" : undefined,
                      }}
                    >
                      {formatPct(t.success_rate)}
                    </td>
                    <td style={{ padding: "4px 6px", textAlign: "right" }}>
                      {formatMs(t.p50_ms)}
                    </td>
                    <td style={{ padding: "4px 6px", textAlign: "right" }}>
                      {formatMs(t.p95_ms)}
                    </td>
                    <td style={{ padding: "4px 6px", textAlign: "right" }}>
                      {formatMs(t.p99_ms)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div
              style={{
                marginTop: 8,
                fontSize: 11,
                color: "var(--catfish-text-muted)",
              }}
            >
              最新事件: {formatFreshness(toolPerf.data_freshness_secs)} · 共 scan{" "}
              {toolPerf.lines_scanned} 行 audit (tail 10k)
            </div>
          </>
        )}
      </div>
    </div>
  );
}

/** 小统计盒子 — 跟 Dashboard 其他卡风格一致 */
function StatBox({
  label,
  value,
  accent,
}: {
  label: string;
  value: string;
  accent?: "warn";
}) {
  return (
    <div
      style={{
        padding: "8px 10px",
        background:
          accent === "warn"
            ? "rgba(220, 80, 60, 0.08)"
            : "var(--catfish-bg-elevated)",
        border:
          accent === "warn"
            ? "1px solid rgba(220, 80, 60, 0.3)"
            : "1px solid var(--catfish-border)",
        borderRadius: 4,
      }}
    >
      <div
        style={{
          fontSize: 10,
          color: "var(--catfish-text-muted)",
          marginBottom: 2,
        }}
      >
        {label}
      </div>
      <div
        style={{
          fontSize: 16,
          fontWeight: 600,
          color: accent === "warn" ? "rgb(220, 80, 60)" : "var(--catfish-text)",
        }}
      >
        {value}
      </div>
    </div>
  );
}
