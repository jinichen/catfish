/** AuditPage 纯函数 helpers — 抽自 AuditPage.tsx (5/20 拆分).
 *
 * fmtTokens / windowLabelOf / pctChange / auditToCsv / csvRow / _palette / TIME_WINDOWS.
 */

import { costRMB, fmtRMB, getModelDisplay } from "../../lib/modelDisplay";
import type { GlobalAudit } from "../../lib/me";
void costRMB;  // helpers 内部 auditToCsv 用; 此 import 仅 ts strict 必要

export const TIME_WINDOWS = [
  { hours: 24, label: "24h" },
  { hours: 168, label: "7d" },
  { hours: 720, label: "30d" },
];

export function fmtTokens(n: number): string {
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}K`;
  return `${(n / 1_000_000).toFixed(2)}M`;
}

export function windowLabelOf(hours: number): string {
  const w = TIME_WINDOWS.find((x) => x.hours === hours);
  return w ? w.label : `${hours}h`;
}

export function pctChange(curr: number, prev: number | undefined): string {
  if (prev === undefined || prev === 0) return "—";
  const change = ((curr - prev) / prev) * 100;
  const sign = change > 0 ? "+" : "";
  return `${sign}${change.toFixed(1)}%`;
}

export function auditToCsv(audit: GlobalAudit): string {
  const lines: string[] = [];
  const sinceIso = new Date(audit.since_ms).toISOString();
  lines.push(`# Catfish LLM 使用审计 · 最近 ${audit.since_hours}h`);
  lines.push(`# 起始时间, ${sinceIso}`);
  lines.push(`# 视角, ${audit.viewer_role}`);
  lines.push("");
  lines.push("## KPI");
  lines.push("指标,本期,上期,变化%");
  lines.push(
    csvRow([
      "总请求",
      String(audit.request_count),
      String(audit.previous_request_count ?? ""),
      pctChange(audit.request_count, audit.previous_request_count),
    ]),
  );
  lines.push(
    csvRow([
      "总 tokens",
      String(audit.total_tokens),
      String(audit.previous_total_tokens ?? ""),
      pctChange(audit.total_tokens, audit.previous_total_tokens),
    ]),
  );
  lines.push(
    csvRow([
      "活跃员工",
      String(audit.active_users),
      String(audit.previous_active_users ?? ""),
      pctChange(audit.active_users, audit.previous_active_users),
    ]),
  );
  lines.push("");
  lines.push("## 按模型");
  lines.push("模型,catalog ID,请求,tokens,RMB 估算");
  for (const m of audit.by_model) {
    const md = getModelDisplay(m.model);
    lines.push(
      csvRow([
        md.friendly,
        m.model,
        String(m.count),
        String(m.total_tokens),
        fmtRMB((m.total_tokens / 1000) * 0.0015).replace("≈ ", ""),
      ]),
    );
  }
  lines.push("");
  lines.push("## 按部门");
  lines.push("部门,请求,tokens");
  for (const d of audit.by_department ?? []) {
    lines.push(csvRow([d.department, String(d.count), String(d.total_tokens)]));
  }
  lines.push("");
  lines.push("## 按员工");
  lines.push("员工,部门,请求,tokens");
  for (const u of audit.by_user) {
    lines.push(csvRow([u.user_email, u.department, String(u.count), String(u.total_tokens)]));
  }
  return lines.join("\n");
}

export function csvRow(cells: string[]): string {
  return cells
    .map((c) => {
      if (/[,"\n]/.test(c)) {
        return `"${c.replace(/"/g, '""')}"`;
      }
      return c;
    })
    .join(",");
}

export function _palette(i: number): string {
  const C = [
    "#5fa8d3", // blue
    "#76b900", // green
    "#7c3aed", // violet
    "#f59e0b", // amber
    "#ef4444", // red
    "#06b6d4", // cyan
    "#ec4899", // pink
    "#8b5cf6", // purple
  ];
  return C[i % C.length];
}

/**
 * BL-AUDIT-P0-FIX (5/17): 数据对账 banner.
 * 全对得上 — 静默不显示. 一致性 sanity check.
 */
