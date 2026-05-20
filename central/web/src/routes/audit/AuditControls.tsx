/** AuditPage 控件区 — 抽自 AuditPage.tsx (5/20 拆分).
 *
 * FilterPillBar + PageHeader + TimeWindowToggle + ExportCsvButton.
 */

import type { AuditFilter, GlobalAudit } from "../../lib/me";
import { getModelDisplay } from "../../lib/modelDisplay";
import { TIME_WINDOWS, auditToCsv, windowLabelOf } from "./helpers";


function FilterPillBar({
  filter,
  onClear,
}: {
  filter: AuditFilter;
  onClear: () => void;
}) {
  const hasFilter = !!(filter.model || filter.dept || filter.user_email);
  if (!hasFilter) return null;
  let label = "";
  if (filter.model) {
    const md = getModelDisplay(filter.model);
    label = `模型 = ${md.dotEmoji} ${md.friendly}`;
  } else if (filter.dept) {
    label = `部门 = ${filter.dept}`;
  } else if (filter.user_email) {
    label = `员工 = ${filter.user_email}`;
  }
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        padding: "8px 12px",
        background: "var(--bg-elev)",
        border: "1px dashed var(--accent)",
        borderRadius: "var(--radius-sm)",
        fontSize: 13,
      }}
    >
      <span style={{ color: "var(--text-muted)" }}>🔍 已筛选:</span>
      <span style={{ fontWeight: 500 }}>{label}</span>
      <button
        type="button"
        onClick={onClear}
        style={{
          marginLeft: "auto",
          padding: "2px 10px",
          background: "transparent",
          border: "1px solid var(--border)",
          borderRadius: "var(--radius-sm)",
          color: "var(--text)",
          fontSize: 12,
          cursor: "pointer",
        }}
      >
        ✕ 清除筛选
      </button>
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════
//                          各 section 组件
// ════════════════════════════════════════════════════════════════════

function PageHeader({
  audit,
  sinceHours,
  onChangeWindow,
  loading,
}: {
  audit: GlobalAudit;
  sinceHours: number;
  onChangeWindow: (hours: number) => void;
  loading: boolean;
}) {
  const scope =
    audit.viewer_role === "admin" || audit.viewer_role === "sysadmin"
      ? "全公司视角"
      : "本部门视角";
  const windowLabel = windowLabelOf(audit.since_hours);
  return (
    <div
      style={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "flex-end",
        gap: "var(--space-3)",
        flexWrap: "wrap",
      }}
    >
      <div>
        <h1
          style={{
            fontSize: 24,
            fontWeight: 600,
            margin: 0,
            marginBottom: 4,
          }}
        >
          LLM 使用审计
        </h1>
        <div
          style={{
            fontSize: 13,
            color: "var(--text-muted)",
            display: "flex",
            alignItems: "center",
            gap: 6,
          }}
        >
          最近 {windowLabel} · {scope}
          <span
            title="本表不含 gateway 内部循环消耗 (summarizer / proactive / 5 维 inject). 内部消耗见下方独立卡."
            style={{ cursor: "help" }}
          >
            ⓘ
          </span>
          {loading && (
            <span style={{ color: "var(--text-muted)", fontSize: 11 }}>
              · 加载中…
            </span>
          )}
        </div>
      </div>
      <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
        <TimeWindowToggle value={sinceHours} onChange={onChangeWindow} />
        <ExportCsvButton audit={audit} />
      </div>
    </div>
  );
}

// 5/20 拆分: windowLabelOf 用 ./helpers 那个 (更短). 删本地副本.

function TimeWindowToggle({
  value,
  onChange,
}: {
  value: number;
  onChange: (hours: number) => void;
}) {
  return (
    <div
      role="tablist"
      style={{
        display: "inline-flex",
        background: "var(--bg-elev)",
        border: "1px solid var(--border)",
        borderRadius: "var(--radius-sm)",
        padding: 2,
      }}
    >
      {TIME_WINDOWS.map((w) => {
        const active = value === w.hours;
        return (
          <button
            key={w.hours}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => onChange(w.hours)}
            style={{
              padding: "4px 12px",
              border: "none",
              background: active ? "var(--accent)" : "transparent",
              color: active ? "white" : "var(--text)",
              borderRadius: "var(--radius-sm)",
              fontSize: 12,
              fontWeight: active ? 500 : 400,
              cursor: "pointer",
              transition: "background 0.1s",
            }}
          >
            {w.label}
          </button>
        );
      })}
    </div>
  );
}

function ExportCsvButton({ audit }: { audit: GlobalAudit }) {
  const onExport = () => {
    const csv = auditToCsv(audit);
    const blob = new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    const since = new Date(audit.since_ms).toISOString().slice(0, 10);
    a.download = `audit_${audit.since_hours}h_${since}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };
  return (
    <button
      type="button"
      onClick={onExport}
      style={{
        padding: "4px 12px",
        background: "var(--bg-elev)",
        border: "1px solid var(--border)",
        borderRadius: "var(--radius-sm)",
        color: "var(--text)",
        fontSize: 12,
        cursor: "pointer",
      }}
      title="导出当前视图为 CSV (含模型/部门/员工 三表合一)"
    >
      📥 CSV
    </button>
  );
}

/** BL-AUDIT-UX-P1: 把 audit 三个表合并成一个 CSV (用 section header 分隔). */

export { FilterPillBar, PageHeader, TimeWindowToggle, ExportCsvButton };
