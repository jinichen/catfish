/** P3.3.55 (6/12 鸿波 "信合规审计闭环"): 审计员视图 Tab.
 *
 * 跟 P3.3.54 (审计导出) 区别:
 *   - 导出 = 生成 xlsx 给审计员拷走
 *   - 视图 = 审计员现场点开看 + 现场跑哈希校验
 *
 * 数据源 3 个 (跟 P3.3.54 一致):
 *   - decisions.jsonl (含 sha256, hash chain)
 *   - ~/.hermes/.catfish_audit.jsonl (工具调用)
 *   - ~/.catfish/outbound_log.db (HTTP 外发)
 *
 * 校验: 仅 decisions.jsonl (P3.3.51 唯一有 chain 的, 鸿波 6/12 拍板).
 *
 * 跟 manifesto 公理 1 (员工主权): tab 默认 hide, 员工 PrivacyCard toggle 才显.
 */

import * as React from "react";

import {
  auditDecisionsRawRead,
  auditHermesJsonlRead,
  auditChainVerify,
  auditExportXlsx,
  transparentLogQuery,
  type ToolCallRow,
  type ChainVerifyReport,
  type TransparentLogEntry,
} from "../../lib/tauri";

type DataSource = "decisions" | "tool_calls" | "outbound";

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}
function firstDayOfMonth(): string {
  const d = new Date();
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-01`;
}
function today(): string {
  const d = new Date();
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
}

const PAGE_SIZE = 50;

export default function AuditViewCard() {
  const [fromDate, setFromDate] = React.useState(firstDayOfMonth());
  const [toDate, setToDate] = React.useState(today());
  const [source, setSource] = React.useState<DataSource>("decisions");

  // 当前数据
  const [decisions, setDecisions] = React.useState<Record<string, unknown>[]>([]);
  const [toolCalls, setToolCalls] = React.useState<ToolCallRow[]>([]);
  const [outbound, setOutbound] = React.useState<TransparentLogEntry[]>([]);

  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [expandedRow, setExpandedRow] = React.useState<number | null>(null);
  const [page, setPage] = React.useState(0);

  // 校验状态
  const [verifyReport, setVerifyReport] = React.useState<ChainVerifyReport | null>(null);
  const [verifyRunning, setVerifyRunning] = React.useState(false);

  // 导出状态
  const [exportRunning, setExportRunning] = React.useState(false);
  const [exportMsg, setExportMsg] = React.useState<string | null>(null);

  const fromIso = `${fromDate}T00:00:00.000Z`;
  const toIso = `${toDate}T23:59:59.999Z`;

  const load = React.useCallback(async () => {
    setLoading(true);
    setError(null);
    setExpandedRow(null);
    setPage(0);
    try {
      if (source === "decisions") {
        const d = await auditDecisionsRawRead(fromIso, toIso, 5000);
        setDecisions(d);
      } else if (source === "tool_calls") {
        const t = await auditHermesJsonlRead(fromIso, toIso, 5000);
        setToolCalls(t);
      } else if (source === "outbound") {
        const r = await transparentLogQuery(fromIso, undefined, undefined, 5000, 0);
        setOutbound(r.entries.filter((e) => e.tsRequest <= toIso));
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [source, fromIso, toIso]);

  React.useEffect(() => {
    void load();
  }, [load]);

  const handleVerify = async () => {
    setVerifyRunning(true);
    setVerifyReport(null);
    try {
      const homePath = await import("@tauri-apps/api/path").then((m) => m.homeDir());
      const path = `${homePath}/.catfish/decisions.jsonl`;
      const report = await auditChainVerify(path);
      setVerifyReport(report);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setVerifyRunning(false);
    }
  };

  const handleExport = async () => {
    setExportRunning(true);
    setExportMsg(null);
    try {
      const r = await auditExportXlsx(
        fromIso,
        toIso,
        true, // 全 3 sheet 都导, 审计员现场要全套
        true,
        true,
      );
      setExportMsg(`✓ 已生成 (${(r.bytesWritten / 1024).toFixed(1)} KB) ${r.outputPath}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setExportRunning(false);
    }
  };

  // 当前数据总行数 + 当前页
  const total =
    source === "decisions"
      ? decisions.length
      : source === "tool_calls"
        ? toolCalls.length
        : outbound.length;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const startIdx = page * PAGE_SIZE;
  const endIdx = Math.min(total, startIdx + PAGE_SIZE);

  return (
    <div style={{ padding: 12 }}>
      {/* 顶部水印 */}
      <div
        style={{
          background: "rgba(31,78,121,0.06)",
          border: "1px dashed rgba(31,78,121,0.3)",
          borderRadius: 4,
          padding: "6px 10px",
          fontSize: 11,
          color: "#1F4E79",
          marginBottom: 10,
        }}
      >
        🔍 审计视图 · 数据本机, 不出端, 员工主权 · 审计员现场可点开 + 跑哈希校验
      </div>

      {/* 时间范围 + 数据源 */}
      <div
        style={{
          display: "flex",
          gap: 10,
          alignItems: "center",
          marginBottom: 10,
          flexWrap: "wrap",
          fontSize: 12,
        }}
      >
        <label>
          从{" "}
          <input
            type="date"
            value={fromDate}
            onChange={(e) => setFromDate(e.target.value)}
            disabled={loading}
            style={{ padding: "2px 6px", fontSize: 12 }}
          />
        </label>
        <label>
          到{" "}
          <input
            type="date"
            value={toDate}
            onChange={(e) => setToDate(e.target.value)}
            disabled={loading}
            style={{ padding: "2px 6px", fontSize: 12 }}
          />
        </label>
        <select
          value={source}
          onChange={(e) => setSource(e.target.value as DataSource)}
          disabled={loading}
          style={{ padding: "2px 6px", fontSize: 12 }}
        >
          <option value="decisions">决策留痕</option>
          <option value="tool_calls">工具调用</option>
          <option value="outbound">数据外发</option>
        </select>
        <button
          type="button"
          onClick={() => void load()}
          disabled={loading}
          style={{
            padding: "3px 10px",
            fontSize: 12,
            background: "transparent",
            border: "1px solid var(--catfish-border)",
            borderRadius: 3,
            cursor: loading ? "default" : "pointer",
          }}
        >
          {loading ? "加载中..." : "🔄 刷新"}
        </button>
      </div>

      {/* 顶部按钮 — 校验哈希链 + 导出 */}
      <div style={{ display: "flex", gap: 10, marginBottom: 10, fontSize: 12 }}>
        <button
          type="button"
          onClick={() => void handleVerify()}
          disabled={verifyRunning}
          style={{
            padding: "4px 12px",
            fontSize: 12,
            background: "var(--catfish-cyan)",
            color: "white",
            border: "none",
            borderRadius: 3,
            cursor: verifyRunning ? "default" : "pointer",
            opacity: verifyRunning ? 0.6 : 1,
          }}
        >
          {verifyRunning ? "校验中..." : "🔐 校验哈希链 (decisions.jsonl)"}
        </button>
        <button
          type="button"
          onClick={() => void handleExport()}
          disabled={exportRunning}
          style={{
            padding: "4px 12px",
            fontSize: 12,
            background: "transparent",
            border: "1px solid var(--catfish-cyan)",
            color: "var(--catfish-cyan)",
            borderRadius: 3,
            cursor: exportRunning ? "default" : "pointer",
            opacity: exportRunning ? 0.6 : 1,
          }}
        >
          {exportRunning ? "导出中..." : "📦 导出当前视图为 xlsx"}
        </button>
      </div>

      {/* 校验结果 */}
      {verifyReport && (
        <div
          style={{
            marginBottom: 10,
            padding: "8px 12px",
            background: verifyReport.ok ? "rgba(22,163,74,0.05)" : "rgba(220,38,38,0.05)",
            border: `1px solid ${verifyReport.ok ? "rgba(22,163,74,0.3)" : "rgba(220,38,38,0.3)"}`,
            borderRadius: 4,
            fontSize: 12,
          }}
        >
          <div
            style={{
              fontWeight: 600,
              color: verifyReport.ok ? "#16A34A" : "var(--status-err)",
              marginBottom: 4,
            }}
          >
            {verifyReport.ok ? "✓ 哈希链完整" : `✗ 哈希链已被篡改`}
          </div>
          <div style={{ fontSize: 11, color: "var(--catfish-text-muted)" }}>
            校验 {verifyReport.verifiedLines} / {verifyReport.totalLines} 行
            {verifyReport.brokenAt
              ? ` · 断裂位置: 第 ${verifyReport.brokenAt} 行 (${verifyReport.brokenReason})`
              : ""}
          </div>
          <div style={{ fontSize: 10, color: "var(--catfish-text-muted)", marginTop: 4 }}>
            起头 sha256: <code>{verifyReport.chainFirstSha256.slice(0, 20)}…</code>
            <br />
            末尾 sha256: <code>{verifyReport.chainLastSha256.slice(0, 20)}…</code>
          </div>
        </div>
      )}

      {/* 导出消息 */}
      {exportMsg && (
        <div
          style={{
            marginBottom: 10,
            padding: "6px 10px",
            background: "rgba(22,163,74,0.05)",
            border: "1px solid rgba(22,163,74,0.3)",
            borderRadius: 4,
            fontSize: 11,
            color: "#16A34A",
          }}
        >
          {exportMsg}
        </div>
      )}

      {/* 错误 */}
      {error && (
        <div
          style={{
            marginBottom: 10,
            padding: "6px 10px",
            background: "rgba(220,38,38,0.05)",
            border: "1px solid rgba(220,38,38,0.3)",
            borderRadius: 4,
            fontSize: 12,
            color: "var(--status-err)",
          }}
        >
          🚫 {error}
        </div>
      )}

      {/* 表格 */}
      <div
        style={{
          fontSize: 11,
          color: "var(--catfish-text-muted)",
          marginBottom: 6,
        }}
      >
        共 {total} 条 · 第 {page + 1} / {totalPages} 页
      </div>

      <div style={{ border: "1px solid var(--catfish-border)", borderRadius: 4, overflow: "auto" }}>
        {source === "decisions" && (
          <DecisionsTable
            rows={decisions.slice(startIdx, endIdx)}
            startIdx={startIdx}
            expandedRow={expandedRow}
            onExpand={(i) => setExpandedRow(expandedRow === i ? null : i)}
          />
        )}
        {source === "tool_calls" && (
          <ToolCallsTable
            rows={toolCalls.slice(startIdx, endIdx)}
            startIdx={startIdx}
            expandedRow={expandedRow}
            onExpand={(i) => setExpandedRow(expandedRow === i ? null : i)}
          />
        )}
        {source === "outbound" && (
          <OutboundTable
            rows={outbound.slice(startIdx, endIdx)}
            startIdx={startIdx}
            expandedRow={expandedRow}
            onExpand={(i) => setExpandedRow(expandedRow === i ? null : i)}
          />
        )}
      </div>

      {/* 分页 */}
      {totalPages > 1 && (
        <div style={{ display: "flex", gap: 6, marginTop: 10, fontSize: 12, alignItems: "center" }}>
          <button
            type="button"
            disabled={page === 0}
            onClick={() => setPage((p) => Math.max(0, p - 1))}
            style={{ padding: "3px 10px", fontSize: 12 }}
          >
            ← 上一页
          </button>
          <span style={{ color: "var(--catfish-text-muted)" }}>
            {page + 1} / {totalPages}
          </span>
          <button
            type="button"
            disabled={page >= totalPages - 1}
            onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
            style={{ padding: "3px 10px", fontSize: 12 }}
          >
            下一页 →
          </button>
        </div>
      )}
    </div>
  );
}

// ─── Tables ──────────────────────────────────────────────────────────

const thStyle: React.CSSProperties = {
  background: "#1F4E79",
  color: "white",
  padding: "6px 8px",
  fontSize: 11,
  textAlign: "left",
  fontWeight: 600,
};
const tdStyle: React.CSSProperties = {
  padding: "5px 8px",
  fontSize: 11,
  borderBottom: "1px solid var(--catfish-border)",
};

function DecisionsTable({
  rows,
  startIdx,
  expandedRow,
  onExpand,
}: {
  rows: Record<string, unknown>[];
  startIdx: number;
  expandedRow: number | null;
  onExpand: (i: number) => void;
}) {
  return (
    <table style={{ width: "100%", borderCollapse: "collapse" }}>
      <thead>
        <tr>
          <th style={thStyle}>时间</th>
          <th style={thStyle}>记录类型</th>
          <th style={thStyle}>任务标题</th>
          <th style={thStyle}>新状态</th>
          <th style={thStyle}>sha256</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => {
          const idx = startIdx + i;
          const isExp = expandedRow === idx;
          const sha = String(r.sha256 ?? "").slice(0, 20);
          return (
            <React.Fragment key={idx}>
              <tr
                onClick={() => onExpand(idx)}
                style={{ cursor: "pointer", background: isExp ? "rgba(31,78,121,0.05)" : "transparent" }}
              >
                <td style={tdStyle}>{String(r.ts ?? "")}</td>
                <td style={tdStyle}>{String(r.recordKind ?? "")}</td>
                <td style={tdStyle}>{String(r.taskTitle ?? "")}</td>
                <td style={tdStyle}>{String(r.newStatus ?? "")}</td>
                <td style={tdStyle}>
                  <code style={{ fontSize: 10 }}>{sha}…</code>
                </td>
              </tr>
              {isExp && (
                <tr>
                  <td colSpan={5} style={{ ...tdStyle, background: "var(--catfish-bg-elevated)" }}>
                    <pre
                      style={{
                        fontSize: 10,
                        margin: 0,
                        maxHeight: 200,
                        overflow: "auto",
                        whiteSpace: "pre-wrap",
                        color: "var(--catfish-text)",
                        fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
                      }}
                    >
                      {JSON.stringify(r, null, 2)}
                    </pre>
                  </td>
                </tr>
              )}
            </React.Fragment>
          );
        })}
      </tbody>
    </table>
  );
}

function ToolCallsTable({
  rows,
  startIdx,
  expandedRow,
  onExpand,
}: {
  rows: ToolCallRow[];
  startIdx: number;
  expandedRow: number | null;
  onExpand: (i: number) => void;
}) {
  return (
    <table style={{ width: "100%", borderCollapse: "collapse" }}>
      <thead>
        <tr>
          <th style={thStyle}>时间</th>
          <th style={thStyle}>工具</th>
          <th style={thStyle}>成功</th>
          <th style={thStyle}>延迟 (ms)</th>
          <th style={thStyle}>错误</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => {
          const idx = startIdx + i;
          const isExp = expandedRow === idx;
          return (
            <React.Fragment key={idx}>
              <tr
                onClick={() => onExpand(idx)}
                style={{ cursor: "pointer", background: isExp ? "rgba(31,78,121,0.05)" : "transparent" }}
              >
                <td style={tdStyle}>{r.ts}</td>
                <td style={tdStyle}>{r.tool}</td>
                <td style={{ ...tdStyle, color: r.ok ? "#16A34A" : "var(--status-err)" }}>
                  {r.ok ? "✓" : "✗"}
                </td>
                <td style={tdStyle}>{r.latencyMs.toFixed(1)}</td>
                <td style={tdStyle}>{r.error ?? ""}</td>
              </tr>
              {isExp && (
                <tr>
                  <td colSpan={5} style={{ ...tdStyle, background: "var(--catfish-bg-elevated)" }}>
                    <pre
                      style={{
                        fontSize: 10,
                        margin: 0,
                        maxHeight: 200,
                        overflow: "auto",
                        whiteSpace: "pre-wrap",
                        color: "var(--catfish-text)",
                        fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
                      }}
                    >
                      {JSON.stringify(r, null, 2)}
                    </pre>
                  </td>
                </tr>
              )}
            </React.Fragment>
          );
        })}
      </tbody>
    </table>
  );
}

function OutboundTable({
  rows,
  startIdx,
  expandedRow,
  onExpand,
}: {
  rows: TransparentLogEntry[];
  startIdx: number;
  expandedRow: number | null;
  onExpand: (i: number) => void;
}) {
  return (
    <table style={{ width: "100%", borderCollapse: "collapse" }}>
      <thead>
        <tr>
          <th style={thStyle}>时间</th>
          <th style={thStyle}>方法</th>
          <th style={thStyle}>URL</th>
          <th style={thStyle}>状态</th>
          <th style={thStyle}>分类</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => {
          const idx = startIdx + i;
          const isExp = expandedRow === idx;
          return (
            <React.Fragment key={idx}>
              <tr
                onClick={() => onExpand(idx)}
                style={{ cursor: "pointer", background: isExp ? "rgba(31,78,121,0.05)" : "transparent" }}
              >
                <td style={tdStyle}>{r.tsRequest}</td>
                <td style={tdStyle}>{r.method}</td>
                <td style={tdStyle}>
                  <code style={{ fontSize: 10 }}>{r.url.length > 60 ? r.url.slice(0, 60) + "…" : r.url}</code>
                </td>
                <td style={tdStyle}>{r.status ?? "—"}</td>
                <td style={tdStyle}>{r.category ?? ""}</td>
              </tr>
              {isExp && (
                <tr>
                  <td colSpan={5} style={{ ...tdStyle, background: "var(--catfish-bg-elevated)" }}>
                    <pre
                      style={{
                        fontSize: 10,
                        margin: 0,
                        maxHeight: 200,
                        overflow: "auto",
                        whiteSpace: "pre-wrap",
                        color: "var(--catfish-text)",
                        fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
                      }}
                    >
                      {JSON.stringify(r, null, 2)}
                    </pre>
                  </td>
                </tr>
              )}
            </React.Fragment>
          );
        })}
      </tbody>
    </table>
  );
}
