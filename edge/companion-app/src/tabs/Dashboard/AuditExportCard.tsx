/** P3.3.54 (6/12 鸿波 "信合规审计闭环"): 审计员看的 xlsx 多 sheet 导出卡.
 *
 * - 默认本月 (1 号 - 今天). 员工可改时间范围
 * - 3 个 checkbox (决策留痕 / 工具调用 / 数据外发, 默认全勾)
 * - 文件固定落 ~/.catfish/exports/ (鸿波 6/12 拍板, 不让员工选位置)
 * - 完成弹绿条 + "在 Finder 显示" 按钮
 *
 * 跟 manifesto 公理 1 (员工主权): 员工**主动**生成给审计员, 不是中央 push.
 */

import * as React from "react";

import { auditExportXlsx, type AuditExportResult } from "../../lib/tauri";
import { invoke } from "@tauri-apps/api/core";

// P3.3.54 polish (6/12): 本地时区, 不走 toISOString UTC 漂移
//   bug 现象: PT 时区 6/1 00:00 → toISOString → "2026-05-31..." (UTC 还在 5/31)
//   修法: 手 build YYYY-MM-DD 字符串
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

export default function AuditExportCard() {
  const [fromDate, setFromDate] = React.useState(firstDayOfMonth());
  const [toDate, setToDate] = React.useState(today());
  const [incDecisions, setIncDecisions] = React.useState(true);
  const [incToolCalls, setIncToolCalls] = React.useState(true);
  const [incOutbound, setIncOutbound] = React.useState(true);
  const [running, setRunning] = React.useState(false);
  const [result, setResult] = React.useState<AuditExportResult | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  const handleExport = async () => {
    setError(null);
    setResult(null);
    setRunning(true);
    try {
      // 转 ISO-8601 边界: from 是日期当天 00:00, to 是当天 23:59:59 给比对宽松
      const fromIso = `${fromDate}T00:00:00.000Z`;
      const toIso = `${toDate}T23:59:59.999Z`;
      const r = await auditExportXlsx(
        fromIso,
        toIso,
        incDecisions,
        incToolCalls,
        incOutbound,
      );
      setResult(r);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg);
    } finally {
      setRunning(false);
    }
  };

  const handleRevealInFinder = async () => {
    if (!result) return;
    try {
      // Tauri 2 — shell open. Rust 端的 commands::recordings::recordings_show_in_finder
      // 已经有 "show file in Finder" 模式, 这里复用一个简单 invoke.
      await invoke("open_in_finder", { path: result.outputPath }).catch(async () => {
        // fallback: 用 tauri-plugin-shell 直接 open 父目录
        const { open } = await import("@tauri-apps/plugin-shell");
        const dir = result.outputPath.replace(/\/[^/]+$/, "");
        await open(dir);
      });
    } catch (e) {
      console.warn("[AuditExportCard] reveal in Finder 失败:", e);
    }
  };

  return (
    <div style={{ padding: 12 }}>
      <div style={{ marginBottom: 12 }}>
        <h3 style={{ margin: 0, fontSize: 15 }}>📦 审计导出</h3>
        <div style={{ fontSize: 11, color: "var(--catfish-text-muted)", marginTop: 4 }}>
          按时间范围 + 数据源生成 xlsx, 落 ~/.catfish/exports/. 给信合规 / 内审 / 党办手动交付.
        </div>
      </div>

      {/* 时间范围 */}
      <div style={{ display: "flex", gap: 12, alignItems: "center", marginBottom: 10, flexWrap: "wrap" }}>
        <label style={{ fontSize: 12 }}>
          从{" "}
          <input
            type="date"
            value={fromDate}
            onChange={(e) => setFromDate(e.target.value)}
            disabled={running}
            style={{ marginLeft: 4, padding: "2px 6px", fontSize: 12 }}
          />
        </label>
        <label style={{ fontSize: 12 }}>
          到{" "}
          <input
            type="date"
            value={toDate}
            onChange={(e) => setToDate(e.target.value)}
            disabled={running}
            style={{ marginLeft: 4, padding: "2px 6px", fontSize: 12 }}
          />
        </label>
      </div>

      {/* 数据源 */}
      <div style={{ display: "flex", gap: 16, marginBottom: 12, flexWrap: "wrap", fontSize: 12 }}>
        <label>
          <input
            type="checkbox"
            checked={incDecisions}
            onChange={(e) => setIncDecisions(e.target.checked)}
            disabled={running}
          />{" "}
          决策留痕
        </label>
        <label>
          <input
            type="checkbox"
            checked={incToolCalls}
            onChange={(e) => setIncToolCalls(e.target.checked)}
            disabled={running}
          />{" "}
          工具调用
        </label>
        <label>
          <input
            type="checkbox"
            checked={incOutbound}
            onChange={(e) => setIncOutbound(e.target.checked)}
            disabled={running}
          />{" "}
          数据外发
        </label>
      </div>

      {/* 导出 */}
      <button
        type="button"
        onClick={() => void handleExport()}
        disabled={running || (!incDecisions && !incToolCalls && !incOutbound)}
        style={{
          background: "var(--catfish-cyan)",
          color: "white",
          border: "none",
          borderRadius: 4,
          padding: "6px 14px",
          fontSize: 13,
          cursor: running ? "default" : "pointer",
          opacity: running ? 0.6 : 1,
        }}
      >
        {running ? "导出中..." : "导出 xlsx"}
      </button>

      {/* 错误 */}
      {error && (
        <div
          style={{
            marginTop: 10,
            padding: "6px 10px",
            background: "rgba(220,38,38,0.05)",
            border: "1px solid rgba(220,38,38,0.3)",
            borderRadius: 4,
            fontSize: 12,
            color: "var(--status-err)",
          }}
        >
          🚫 导出失败: {error}
        </div>
      )}

      {/* 成功 */}
      {result && !error && (
        <div
          style={{
            marginTop: 10,
            padding: "8px 12px",
            background: "rgba(22,163,74,0.05)",
            border: "1px solid rgba(22,163,74,0.3)",
            borderRadius: 4,
            fontSize: 12,
          }}
        >
          <div style={{ fontWeight: 600, color: "#16A34A", marginBottom: 4 }}>
            ✓ 已生成 ({(result.bytesWritten / 1024).toFixed(1)} KB)
          </div>
          <div style={{ marginBottom: 4 }}>
            <code style={{ fontSize: 11 }}>{result.outputPath}</code>
          </div>
          <div style={{ color: "var(--catfish-text-muted)", fontSize: 11, marginBottom: 6 }}>
            决策 {result.decisionsCount} 条 · 工具调用 {result.toolCallsCount} 条 ·
            数据外发 {result.outboundCount} 条
            {result.chainOk ? (
              <span style={{ color: "#16A34A" }}> · 哈希链 ✓ 完整</span>
            ) : (
              <span style={{ color: "var(--status-err)" }}>
                {" "}· 哈希链 ✗ {result.chainBrokenReason ?? "异常"}
              </span>
            )}
          </div>
          <button
            type="button"
            onClick={() => void handleRevealInFinder()}
            style={{
              background: "transparent",
              border: "1px solid var(--catfish-border)",
              borderRadius: 3,
              padding: "3px 10px",
              fontSize: 11,
              cursor: "pointer",
            }}
          >
            📂 在 Finder 显示
          </button>
        </div>
      )}
    </div>
  );
}
