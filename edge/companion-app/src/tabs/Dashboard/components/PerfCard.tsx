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
// P3.5.59 Phase 2 (6/22 鸿波 catch "把中央端完成"): 中央 gateway 拿 LLM perf
// (走 gateway_audit 表 latency 分位). dev 模式 gateway 在本机也工作 (JSONL fallback).
import { fetchMyLlmPerf, type RemoteLlmPerfSummary } from "../../../lib/me";

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
  // P3.5.59 Phase 1: 本机 audit.rs 兜底 (gateway 在本机 dev 时有数据).
  const { summary: localSummary } = useAudit();
  // P3.5.59 Phase 2: 中央 gateway /api/audit/me/perf — 生产 SaaS 走这条.
  // 拿到就用 remote (有 latency 分位); 拿不到 (无网/未登录/endpoint 不存在) fallback 用 local.
  const [llmPerf, setLlmPerf] = useState<RemoteLlmPerfSummary | null>(null);
  const [llmError, setLlmError] = useState<string | null>(null);
  const [toolPerf, setToolPerf] = useState<ToolPerfSummary | null>(null);
  const [toolError, setToolError] = useState<string | null>(null);
  const [windowHours, setWindowHours] = useState<number>(DEFAULT_WINDOW_HOURS);

  // tool perf (本机 jsonl) — 跟时间窗联动
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

  // P3.5.59 Phase 2: LLM perf (中央 gateway) — 跟时间窗联动
  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        // window_hours=0 不切窗 — endpoint 上限 720, 0 视作"很久" 用 720
        const hours = windowHours === 0 ? 720 : windowHours;
        const s = await fetchMyLlmPerf(hours);
        if (!cancelled) {
          setLlmPerf(s);
          setLlmError(null);
        }
      } catch (e) {
        if (!cancelled) {
          setLlmError(String(e));
          // 不清 llmPerf — 让上次成功值留着, 切回时网恢复继续显
        }
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

      {/* ─── Section 1: LLM call ─────────────────────────────────────
          P3.5.59 双源:
          - remote (中央 /api/audit/me/perf 走 gateway_audit 表): 真生产数据 +
            完整 latency 分位 (p50/p95/p99). 优先用.
          - local (本机 audit.rs 读 ~/.catfish/gateway_audit.jsonl): dev 模式
            gateway 在本机时兜底.
          - 双源都拿不到 (鸿波本机 dev gateway 在中央 + 没登录): 显友好提示. */}
      <div style={{ marginBottom: 16 }}>
        <div
          style={{
            fontWeight: 600,
            marginBottom: 8,
            color: "var(--catfish-text)",
            fontSize: 13,
            display: "flex",
            alignItems: "center",
            gap: 8,
          }}
        >
          🤖 LLM 调用性能
          {llmPerf && (
            <span
              style={{
                fontSize: 10,
                padding: "1px 6px",
                borderRadius: 3,
                background: llmPerf.source === "pg"
                  ? "rgba(59, 130, 246, 0.1)"
                  : "rgba(168, 162, 158, 0.15)",
                color: llmPerf.source === "pg"
                  ? "rgb(30, 64, 175)"
                  : "var(--catfish-text-muted)",
              }}
              title={
                llmPerf.source === "pg"
                  ? "中央 PG (生产 SaaS 数据)"
                  : llmPerf.source === "jsonl"
                    ? "中央 JSONL fallback (dev / 私有部署没 PG 时)"
                    : "无数据 (gateway 还没记录或时间窗内空)"
              }
            >
              {llmPerf.source === "pg" ? "🏢 中央 PG" : llmPerf.source === "jsonl" ? "📄 中央 JSONL" : "无数据"}
            </span>
          )}
          {!llmPerf && localSummary && (
            <span
              style={{
                fontSize: 10,
                padding: "1px 6px",
                borderRadius: 3,
                background: "rgba(168, 162, 158, 0.15)",
                color: "var(--catfish-text-muted)",
              }}
              title="本机 audit.rs 读 ~/.catfish/gateway_audit.jsonl (gateway 在本机 dev 时)"
            >
              💻 本机 fallback
            </span>
          )}
        </div>

        {llmError && !llmPerf && (
          <div style={{ color: "var(--status-err)", fontSize: 11, marginBottom: 6 }}>
            ✗ 中央 /api/audit/me/perf 调用失败: {llmError}
            <br />
            <span style={{ opacity: 0.7 }}>
              (未登录 / gateway 不可达 / endpoint 不存在; 下方显本机 jsonl
              fallback 数据如果有)
            </span>
          </div>
        )}

        {/* 优先 remote llmPerf */}
        {llmPerf && llmPerf.request_count > 0 && (
          <>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(110px, 1fr))",
                gap: 8,
              }}
            >
              <StatBox label="总请求" value={String(llmPerf.request_count)} />
              <StatBox
                label="成功率"
                value={
                  llmPerf.request_count > 0
                    ? formatPct(llmPerf.ok_count / llmPerf.request_count)
                    : "—"
                }
                accent={
                  llmPerf.request_count > 0 &&
                  llmPerf.error_count / llmPerf.request_count > 0.1
                    ? "warn"
                    : undefined
                }
              />
              <StatBox
                label="错误数"
                value={String(llmPerf.error_count)}
                accent={llmPerf.error_count > 0 ? "warn" : undefined}
              />
              <StatBox label="延迟 p50" value={formatMs(llmPerf.latency_p50_ms)} />
              <StatBox label="延迟 p95" value={formatMs(llmPerf.latency_p95_ms)} />
              <StatBox label="延迟 p99" value={formatMs(llmPerf.latency_p99_ms)} />
              <StatBox label="TTFT p50" value={formatMs(llmPerf.ttft_p50_ms)} />
              <StatBox label="TTFT p95" value={formatMs(llmPerf.ttft_p95_ms)} />
              <StatBox
                label="总 tokens"
                value={llmPerf.total_tokens.toLocaleString()}
              />
            </div>
            {llmPerf.by_model.length > 0 && (
              <div
                style={{
                  marginTop: 8,
                  fontSize: 11,
                  color: "var(--catfish-text-muted)",
                }}
              >
                <span style={{ marginRight: 8 }}>模型用量 (p50 latency):</span>
                {llmPerf.by_model.slice(0, 4).map((m) => {
                  const isPrivate = m.model.startsWith("catfish-private-");
                  return (
                    <span
                      key={m.model}
                      style={{
                        display: "inline-block",
                        marginRight: 8,
                        padding: "2px 6px",
                        background: isPrivate
                          ? "rgba(34, 197, 94, 0.1)"
                          : "rgba(59, 130, 246, 0.1)",
                        color: isPrivate ? "rgb(21, 128, 61)" : "rgb(30, 64, 175)",
                        borderRadius: 3,
                      }}
                      title={isPrivate ? "私有 (内网, 数据不出端)" : "公网"}
                    >
                      {isPrivate ? "🔒" : "🌐"} {m.model} ({m.count} ·{" "}
                      {formatMs(m.p50_ms)})
                    </span>
                  );
                })}
              </div>
            )}
          </>
        )}

        {/* remote 拿到了但 request_count=0 — 时间窗内没数据 */}
        {llmPerf && llmPerf.request_count === 0 && (
          <div style={{ color: "var(--catfish-text-muted)", fontSize: 12 }}>
            🐠 此时间窗内你没有 LLM 调用记录 (中央 gateway_audit 表查到 0 条).
          </div>
        )}

        {/* remote 完全没拿到 (网络挂 / 未登录) → fallback 本机 jsonl */}
        {!llmPerf && localSummary && localSummary.request_count > 0 && (
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(110px, 1fr))",
              gap: 8,
            }}
          >
            <StatBox label="总请求" value={String(localSummary.request_count)} />
            <StatBox
              label="成功率"
              value={formatPct(
                localSummary.ok_count / localSummary.request_count,
              )}
            />
            <StatBox label="错误数" value={String(localSummary.error_count)} />
            <StatBox label="TTFT p50" value={formatMs(localSummary.ttft_p50_ms)} />
            <StatBox label="TTFT p95" value={formatMs(localSummary.ttft_p95_ms)} />
            <StatBox
              label="总 tokens"
              value={localSummary.total_tokens.toLocaleString()}
            />
          </div>
        )}

        {/* 双源都空 */}
        {!llmPerf && (!localSummary || localSummary.request_count === 0) && !llmError && (
          <div style={{ color: "var(--catfish-text-muted)", fontSize: 12 }}>
            🐠 暂无 LLM 调用数据 (中央 + 本机都空; gateway 还没运行过或时间窗内空).
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
