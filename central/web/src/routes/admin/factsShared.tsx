/** /admin/facts 的工具函数和样式 (8/1 拆文件时抽出).
 *
 * FactsPage 原来 753 行, 而这次要给三个操作加确认对话框 (原来是原生
 * confirm), 加完会撞 CLAUDE.md 军规 §1 的 800 行红线。切口是
 * "列表 + 上传" / "详情 + patch", 后者正好是三个确认所在的地方。
 */

import type { CSSProperties } from "react";

import { Badge, type BadgeTone } from "../../components/DataTable";
import type { FactStatus } from "../../lib/facts";


export function fmtSize(b: number): string {
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
  return `${(b / 1024 / 1024).toFixed(1)} MB`;
}

export function fmtTime(ms: number): string {
  if (!ms) return "-";
  const d = new Date(ms);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

export const inputStyle: CSSProperties = {
  padding: "6px 10px",
  fontSize: 13,
  border: "1px solid var(--border)",
  borderRadius: "var(--radius-sm)",
  background: "var(--bg)",
  color: "var(--text)",
};

export const btnPrimary: CSSProperties = {
  background: "var(--accent)",
  color: "white",
  border: "none",
  borderRadius: "var(--radius-sm)",
  padding: "6px 14px",
  fontSize: 12,
  cursor: "pointer",
  fontWeight: 500,
};

export const btnSecondary: CSSProperties = {
  background: "transparent",
  color: "var(--text)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius-sm)",
  padding: "6px 14px",
  fontSize: 12,
  cursor: "pointer",
};

export const btnDanger: CSSProperties = {
  background: "rgba(196, 63, 63, 0.1)",
  color: "#c43f3f",
  border: "1px solid rgba(196, 63, 63, 0.3)",
  borderRadius: "var(--radius-sm)",
  padding: "6px 14px",
  fontSize: 12,
  cursor: "pointer",
};

export const errBox: CSSProperties = {
  background: "rgba(196, 63, 63, 0.08)",
  border: "1px solid rgba(196, 63, 63, 0.3)",
  color: "#c43f3f",
  padding: "var(--space-2) var(--space-3)",
  borderRadius: "var(--radius-sm)",
  fontSize: 12,
  marginTop: 8,
};

export const infoBox: CSSProperties = {
  background: "rgba(45, 138, 135, 0.08)",
  border: "1px solid rgba(45, 138, 135, 0.3)",
  color: "var(--accent)",
  padding: "var(--space-3)",
  borderRadius: "var(--radius-sm)",
  fontSize: 12,
};


/** 事实的处理状态。列表页和详情页都用。 */
export function StatusBadge({ status }: { status: FactStatus }) {
  const MAP: Record<FactStatus, [BadgeTone, string]> = {
    uploaded: ["neutral", "已上传"],
    extracted: ["neutral", "已解析"],
    analyzed: ["accent", "已分析"],
    patches_ready: ["warn", "待审批"],
    approved: ["ok", "已采纳"],
    dismissed: ["neutral", "已撤销"],
  };
  const [tone, label] = MAP[status] ?? (["neutral", status] as [BadgeTone, string]);
  return <Badge tone={tone}>{label}</Badge>;
}
