/** AuditPage 控件区 — 抽自 AuditPage.tsx (5/20 拆分).
 *
 * FilterPillBar + PageHeader + TimeWindowToggle + ExportCsvButton.
 */

import { Toolbar } from "../../components/DataTable";
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
        padding: "4px 10px",
        background: "var(--bg-elev)",
        border: "1px dashed var(--accent)",
        borderRadius: "var(--radius-sm)",
        fontSize: 12,
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
  // ⚠ 用 audit.since_hours (**数据实际来自哪个窗口**), 不是 sinceHours
  // (按钮上选中的那个)。刷新期间两者不同, 而这一行紧挨着时间窗按钮 ——
  // 用选中值的话, 界面上会显示"近 7 天"配着一屏 24 小时的数字, 一个字
  // 都看不出不对。所以刷新期间干脆不说是哪个窗口。
  const windowLabel = windowLabelOf(audit.since_hours);
  const stale = audit.since_hours !== sinceHours;
  return (
    // 8/1: 原来是一个 24px 的 <h1>「LLM 使用审计」+ 一行副标题, 占掉 60px。
    // 两个问题:
    //   1. 这一页现在挂在 /admin 下, 侧栏里已经高亮着「用量审计」——
    //      正文再写一遍标题是重复的; 而且两处名字还不一样。
    //   2. 概览页和性能页用的是 Toolbar (13px h3 + 右侧控件) —— 三个观测页
    //      各写各的标题栏, 在侧栏里来回点会看到标题忽大忽小。
    // 改用同一个 Toolbar。窗口和范围挪进右侧那行小字, 一个字没少。
    <Toolbar title="用量审计">
      <span style={{ color: "var(--text-muted)", fontSize: 11 }}>
        {loading && stale ? "换窗口中…" : `近 ${windowLabel}`} · {scope}
        <span
          title="这一页的数字不含 gateway 自身的调用 (总结 / 主动提醒 / 5 维注入)。那部分单列在指标带下面一行。"
          style={{ cursor: "help", marginLeft: 4 }}
        >
          ⓘ
        </span>
        {loading && !stale && " · 刷新中…"}
      </span>
      <TimeWindowToggle value={sinceHours} onChange={onChangeWindow} />
      <ExportCsvButton audit={audit} />
    </Toolbar>
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
