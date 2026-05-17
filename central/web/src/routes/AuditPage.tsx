/** /audit — 审计大查询 (manager + admin) (BL-ARCH1 5/10).
 *
 * 改造历史:
 *   BL-ARCH1 (5/10):     P0 初版 — 4 stat 卡 + 3 表格
 *   BL-AUDIT-P0-FIX:     修数据信任 bug + (未分组) 桶
 *   BL-AUDIT-P0-FIX-V2:  sysadmin 也算全公司视角
 *   BL-AUDIT-UX-P0 (5/17): UX 工程师化重做 — KPI hero / 模型 friendly /
 *                          inline bar / 异常告警占位
 */

import { useEffect, useMemo, useState, type ReactNode } from "react";

import { Card } from "../components/Card";
import { RoleGate } from "../components/RoleGate";
import { costRMB, fmtRMB, getModelDisplay, totalCostRMB } from "../lib/modelDisplay";
import {
  fetchGlobalAudit,
  type AuditFilter,
  type GlobalAudit,
} from "../lib/me";

function fmtTokens(n: number): string {
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}K`;
  return `${(n / 1_000_000).toFixed(2)}M`;
}

const TIME_WINDOWS = [
  { hours: 24, label: "24h" },
  { hours: 168, label: "7d" },
  { hours: 720, label: "30d" },
];

export function AuditPage() {
  const [audit, setAudit] = useState<GlobalAudit | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  // BL-AUDIT-UX-P1: 时间窗状态, 默认 24h
  const [sinceHours, setSinceHours] = useState<number>(24);
  // BL-AUDIT-UX-P2: drill-down filter (一次 1 维, 多维是 P3)
  const [filter, setFilter] = useState<AuditFilter>({});

  useEffect(() => {
    setLoading(true);
    setError(null);
    fetchGlobalAudit(sinceHours, filter)
      .then((a) => {
        if (!a) setError("拉取 audit 失败 (没权限或后端报错)");
        else setAudit(a);
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, [sinceHours, filter]);

  // BL-AUDIT-UX-P2: 一次只允许 1 个 filter 维度. 点新行 → 替换 (不叠加).
  const setSingleFilter = (next: AuditFilter) => setFilter(next);
  const clearFilter = () => setFilter({});

  return (
    <RoleGate require={["manager", "admin"]}>
      <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
        {error && (
          <Card title="错误">
            <div style={{ color: "var(--status-err)" }}>{error}</div>
          </Card>
        )}
        {loading && !audit && <div>加载中…</div>}
        {audit && (
          <>
            {/* ── 标题区 + 工具栏 (BL-AUDIT-UX-P1: 时间窗切换 + CSV 导出) ── */}
            <PageHeader
              audit={audit}
              sinceHours={sinceHours}
              onChangeWindow={setSinceHours}
              loading={loading}
            />

            {/* ── BL-AUDIT-UX-P2: 当前 filter pill chip (仅有 filter 时显) ── */}
            <FilterPillBar filter={filter} onClear={clearFilter} />

            {/* ── 异常告警条 (BL-AUDIT-UX-P0 占位, P1 backend 出 trend 后实数) ── */}
            <AnomalyBanner audit={audit} />

            {/* ── KPI 区: 主指标 hero + 3 个辅助 ── */}
            <KPIHero audit={audit} />

            {/* BL-AUDIT-INTERNAL-SPLIT (5/17): internal loopback 单独透明度. */}
            {(audit.internal_tokens ?? 0) > 0 && (
              <InternalLoopbackCard audit={audit} />
            )}

            {/* ── 数据对账 (BL-AUDIT-P0-FIX, 数字不一致时显, 一致时静默) ── */}
            <DataReconciliation audit={audit} />

            {/* ── 按模型 (横向 bar + 比例 % + 友好名 + 颜色) ── */}
            <ModelBreakdownCard
              audit={audit}
              onClickRow={(model) => setSingleFilter({ model })}
              activeModel={filter.model ?? null}
            />

            {/* ── 按部门 (横向 bar) ── */}
            <DeptBreakdownCard
              audit={audit}
              onClickRow={(dept) => setSingleFilter({ dept })}
              activeDept={filter.dept ?? null}
            />

            {/* ── 按员工 (横向 bar, top N) ── */}
            <UserBreakdownCard
              audit={audit}
              onClickRow={(user_email) => setSingleFilter({ user_email })}
              activeUser={filter.user_email ?? null}
            />
          </>
        )}
      </div>
    </RoleGate>
  );
}

/** BL-AUDIT-UX-P2: 当前 filter pill, 有 filter 时显示 + 一键清除. */
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

function windowLabelOf(hours: number): string {
  if (hours <= 24) return `${hours} 小时`;
  if (hours <= 168) return `${hours / 24} 天`;
  return `${hours / 24} 天`;
}

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
function auditToCsv(audit: GlobalAudit): string {
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

function csvRow(cells: string[]): string {
  return cells
    .map((c) => {
      if (/[,"\n]/.test(c)) {
        return `"${c.replace(/"/g, '""')}"`;
      }
      return c;
    })
    .join(",");
}

function pctChange(curr: number, prev: number | undefined): string {
  if (prev === undefined || prev === null || prev === 0) return "";
  const delta = ((curr - prev) / prev) * 100;
  const sign = delta >= 0 ? "+" : "";
  return `${sign}${delta.toFixed(1)}%`;
}

/** BL-AUDIT-UX-P0: 异常告警条占位 (放最顶, 第一时间抓眼).
 *
 * 当前数据无 trend 字段, 这个组件**永远 return null**. P1 后端在
 * GlobalAudit 加 `anomalies: [{user, dept, model, ratio, threshold}]` 后,
 * 这里直接 map 出红/黄条 — 已经准备好接口位置.
 */
function AnomalyBanner({ audit: _audit }: { audit: GlobalAudit }) {
  // P1 占位: backend 加 anomalies 后这里就有实数据
  const anomalies: { kind: string; text: string }[] = [];
  if (anomalies.length === 0) return null;
  return (
    <Card title="⚠️ 异常告警">
      <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>
        {anomalies.map((a, i) => (
          <li key={i} style={{ color: "var(--status-warn, #b45309)" }}>
            {a.text}
          </li>
        ))}
      </ul>
    </Card>
  );
}

/** BL-AUDIT-UX-P0: KPI 主指标 hero.
 *
 * - 总 tokens 是主指标 (烧钱的): 36pt 大字 + 成本估算
 * - 总请求 / 活跃员工 / 活跃部门 是辅助: 18pt 中字, 横向并列
 */
function KPIHero({ audit }: { audit: GlobalAudit }) {
  // BL-AUDIT-UX-P1: 精算 RMB — 按 model 单价加权
  const totalRMB = useMemo(() => totalCostRMB(audit.by_model), [audit.by_model]);
  const windowLabel = windowLabelOf(audit.since_hours);

  return (
    <div
      style={{
        background: "var(--bg-elev)",
        border: "1px solid var(--border)",
        borderRadius: "var(--radius-md)",
        padding: "var(--space-4)",
      }}
    >
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1.6fr 1fr 1fr 1fr",
          gap: "var(--space-4)",
          alignItems: "stretch",
        }}
      >
        {/* Hero: 总 tokens */}
        <div
          style={{
            borderRight: "1px solid var(--border)",
            paddingRight: "var(--space-4)",
          }}
        >
          <div
            style={{
              fontSize: 11,
              color: "var(--text-muted)",
              textTransform: "uppercase",
              letterSpacing: 0.6,
              marginBottom: 4,
            }}
          >
            💰 总 tokens · {windowLabel}
          </div>
          <div
            style={{
              fontSize: 40,
              fontWeight: 600,
              lineHeight: 1.1,
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {fmtTokens(audit.total_tokens)}
          </div>
          <div style={{ marginTop: 6, display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
            <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
              {fmtRMB(totalRMB)} 估算
            </span>
            <Trend
              curr={audit.total_tokens}
              prev={audit.previous_total_tokens}
              hint="跟上一个等长窗口对照"
            />
          </div>
        </div>

        {/* 辅 1: 总请求 */}
        <SubStat
          label="📊 总请求"
          value={audit.request_count.toLocaleString()}
          trend={
            <Trend
              curr={audit.request_count}
              prev={audit.previous_request_count}
              hint="跟上一个等长窗口对照"
            />
          }
        />
        {/* 辅 2: 活跃员工 */}
        <SubStat
          label="👤 活跃员工"
          value={audit.active_users.toLocaleString()}
          trend={
            <Trend
              curr={audit.active_users}
              prev={audit.previous_active_users}
              hint="跟上一个等长窗口对照"
            />
          }
        />
        {/* 辅 3: 活跃部门 */}
        <SubStat
          label="🏢 活跃部门"
          value={audit.active_departments.toLocaleString()}
          hint="只数有部门归属的员工"
          trend={
            <Trend
              curr={audit.active_departments}
              prev={audit.previous_active_departments}
              hint="跟上一个等长窗口对照"
            />
          }
        />
      </div>
    </div>
  );
}

/** BL-AUDIT-UX-P1: ↑12% / ↓8% trend pill — 绿色升 / 红色降 / 灰色持平.
 *  prev=0 或 undefined 时不显示 (没参照系). */
function Trend({
  curr,
  prev,
  hint,
}: {
  curr: number;
  prev: number | undefined;
  hint?: string;
}) {
  if (prev === undefined || prev === null) return null;
  // 上期 0 + 本期 > 0 → "新增" (无法算 %); 上期 0 + 本期 0 → 没变化, 不显示
  if (prev === 0) {
    if (curr === 0) return null;
    return (
      <span
        title={hint}
        style={{
          fontSize: 11,
          color: "var(--status-warn, #b45309)",
          fontVariantNumeric: "tabular-nums",
        }}
      >
        新增
      </span>
    );
  }
  const delta = ((curr - prev) / prev) * 100;
  if (Math.abs(delta) < 0.5) {
    return (
      <span
        title={hint}
        style={{
          fontSize: 11,
          color: "var(--text-muted)",
        }}
      >
        持平
      </span>
    );
  }
  const up = delta > 0;
  return (
    <span
      title={hint}
      style={{
        fontSize: 11,
        color: up ? "#16a34a" : "#dc2626",
        fontVariantNumeric: "tabular-nums",
        fontWeight: 500,
      }}
    >
      {up ? "↑" : "↓"}{Math.abs(delta).toFixed(1)}%
    </span>
  );
}

function SubStat({
  label,
  value,
  hint,
  trend,
}: {
  label: string;
  value: string;
  hint?: string;
  /** BL-AUDIT-UX-P1: 可选 trend pill, 例如 <Trend curr=X prev=Y /> */
  trend?: ReactNode;
}) {
  return (
    <div>
      <div
        style={{
          fontSize: 11,
          color: "var(--text-muted)",
          textTransform: "uppercase",
          letterSpacing: 0.6,
          marginBottom: 4,
          display: "flex",
          alignItems: "center",
          gap: 4,
        }}
      >
        {label}
        {hint && (
          <span style={{ cursor: "help" }} title={hint}>
            ⓘ
          </span>
        )}
      </div>
      <div
        style={{
          fontSize: 24,
          fontWeight: 600,
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {value}
      </div>
      {trend && <div style={{ marginTop: 2 }}>{trend}</div>}
    </div>
  );
}

function InternalLoopbackCard({ audit }: { audit: GlobalAudit }) {
  return (
    <Card title="🔁 Gateway 内部循环消耗">
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gap: "var(--space-3)",
        }}
      >
        <SubStat
          label="内部请求"
          value={(audit.internal_request_count ?? 0).toLocaleString()}
        />
        <SubStat
          label="内部 tokens"
          value={fmtTokens(audit.internal_tokens ?? 0)}
          hint="summarizer / proactive / 5 维 inject 等 gateway 自调消耗. 跟员工业务无关, 但占真实 LLM 账单."
        />
      </div>
    </Card>
  );
}

/** BL-AUDIT-UX-P0: 按模型 — 横向 bar + 比例 % + 友好名 + 颜色.
 *  BL-AUDIT-UX-P1: + RMB 列 (按 model 单价精算)
 *  BL-AUDIT-UX-P2: + onClickRow drill-down, activeModel 高亮 */
function ModelBreakdownCard({
  audit,
  onClickRow,
  activeModel,
}: {
  audit: GlobalAudit;
  onClickRow: (model: string) => void;
  activeModel: string | null;
}) {
  const total = audit.by_model.reduce((s, m) => s + m.total_tokens, 0);
  return (
    <Card title="按模型用量">
      <Table
        showRmbColumn
        rows={audit.by_model.map((m) => {
          const md = getModelDisplay(m.model);
          const pct = total > 0 ? (m.total_tokens / total) * 100 : 0;
          return {
            key: m.model,
            onClick: () => onClickRow(m.model),
            active: activeModel === m.model,
            label: (
              <span
                style={{ display: "inline-flex", alignItems: "center", gap: 8 }}
                title={m.model}
              >
                <span>{md.dotEmoji}</span>
                <span style={{ fontWeight: 500 }}>{md.friendly}</span>
                <span
                  style={{
                    fontSize: 11,
                    color: "var(--text-muted)",
                    padding: "1px 6px",
                    background: "var(--bg-elev)",
                    borderRadius: 8,
                  }}
                >
                  {md.tier}
                </span>
              </span>
            ),
            count: m.count,
            tokens: m.total_tokens,
            rmb: costRMB(m.model, m.total_tokens),
            pct,
            barColor: md.color,
          };
        })}
      />
    </Card>
  );
}

function DeptBreakdownCard({
  audit,
  onClickRow,
  activeDept,
}: {
  audit: GlobalAudit;
  onClickRow: (dept: string) => void;
  activeDept: string | null;
}) {
  if (!audit.by_department || audit.by_department.length === 0) return null;
  const total = audit.by_department.reduce((s, d) => s + d.total_tokens, 0);
  return (
    <Card title="按部门用量">
      <Table
        rows={audit.by_department.map((d, i) => {
          const isUnassigned = d.department === "(未分组)";
          const pct = total > 0 ? (d.total_tokens / total) * 100 : 0;
          return {
            key: d.department,
            onClick: () => onClickRow(d.department),
            active: activeDept === d.department,
            label: (
              <span
                style={{
                  fontStyle: isUnassigned ? "italic" : "normal",
                  color: isUnassigned ? "var(--text-muted)" : undefined,
                }}
                title={
                  isUnassigned
                    ? "该桶里员工没绑部门 (dev_token / 老员工 / OIDC 缺 dept claim). Day 8 客户接入手册要求 SSO 必传 dept."
                    : undefined
                }
              >
                {d.department}
              </span>
            ),
            count: d.count,
            tokens: d.total_tokens,
            pct,
            barColor: _palette(i),
          };
        })}
      />
    </Card>
  );
}

function UserBreakdownCard({
  audit,
  onClickRow,
  activeUser,
}: {
  audit: GlobalAudit;
  onClickRow: (user_email: string) => void;
  activeUser: string | null;
}) {
  if (audit.by_user.length === 0) {
    return (
      <Card title="按员工用量">
        <div style={{ color: "var(--text-muted)", fontSize: 13 }}>
          本期暂无员工业务请求.
        </div>
      </Card>
    );
  }
  const total = audit.by_user.reduce((s, u) => s + u.total_tokens, 0);
  const capped = audit.by_user.length >= 50;
  return (
    <Card
      title={
        <span>
          按员工用量
          {capped && (
            <span
              style={{
                marginLeft: 8,
                fontSize: 11,
                color: "var(--text-muted)",
                fontWeight: 400,
              }}
            >
              (top 50 上限)
            </span>
          )}
        </span>
      }
    >
      <Table
        showExtraColumn="部门"
        rows={audit.by_user.map((u, i) => {
          const isUnassigned = u.user_email === "(未分组员工)";
          const pct = total > 0 ? (u.total_tokens / total) * 100 : 0;
          return {
            key: `${u.user_email}::${u.department}`,
            onClick: () => onClickRow(u.user_email),
            active: activeUser === u.user_email,
            label: (
              <span
                style={{
                  fontStyle: isUnassigned ? "italic" : "normal",
                  color: isUnassigned ? "var(--text-muted)" : undefined,
                }}
                title={
                  isUnassigned
                    ? "该桶里请求源没拿到 user_email (dev_token / OIDC 缺 email claim)."
                    : undefined
                }
              >
                {u.user_email}
              </span>
            ),
            extra: u.department,
            count: u.count,
            tokens: u.total_tokens,
            pct,
            barColor: _palette(i),
          };
        })}
      />
    </Card>
  );
}

/** BL-AUDIT-UX-P0: 通用表格组件 + 内嵌横向 bar.
 *
 * 每行结构:
 *   [label........]  [extra (可选)] [横向 bar with %]  [请求数]  [tokens]  [RMB?]
 *
 * BL-AUDIT-UX-P1: + showRmbColumn 可选 — 模型表用 (按 model 精算), 部门 / 员工表
 * 不显示 (需要后端把 tokens × model 拆出来才能精算).
 */
function Table({
  rows,
  showExtraColumn,
  showRmbColumn,
}: {
  rows: {
    key: string;
    label: ReactNode;
    extra?: string;
    count: number;
    tokens: number;
    rmb?: number;
    pct: number;
    barColor: string;
    /** BL-AUDIT-UX-P2: 行点击 drill down. 不传 = 行不可点 (光标不变) */
    onClick?: () => void;
    /** BL-AUDIT-UX-P2: 高亮当前 filter 中的那一行 */
    active?: boolean;
  }[];
  showExtraColumn?: string;
  showRmbColumn?: boolean;
}) {
  return (
    <table
      style={{
        width: "100%",
        borderCollapse: "collapse",
        fontSize: 13,
        fontVariantNumeric: "tabular-nums",
      }}
    >
      <thead>
        <tr style={{ borderBottom: "1px solid var(--border)" }}>
          <th style={{ textAlign: "left", padding: "6px 8px", width: "30%" }}>
            名称
          </th>
          {showExtraColumn && (
            <th style={{ textAlign: "left", padding: "6px 8px", width: "20%" }}>
              {showExtraColumn}
            </th>
          )}
          <th style={{ textAlign: "left", padding: "6px 8px" }}>占比</th>
          <th style={{ textAlign: "right", padding: "6px 8px", width: 80 }}>
            请求
          </th>
          <th style={{ textAlign: "right", padding: "6px 8px", width: 90 }}>
            tokens
          </th>
          {showRmbColumn && (
            <th style={{ textAlign: "right", padding: "6px 8px", width: 90 }}>
              RMB 估算
            </th>
          )}
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr
            key={r.key}
            onClick={r.onClick}
            style={{
              borderBottom: "1px solid var(--bg-secondary)",
              cursor: r.onClick ? "pointer" : "default",
              background: r.active
                ? "color-mix(in srgb, var(--accent) 12%, transparent)"
                : "transparent",
              transition: "background 0.1s",
            }}
            onMouseEnter={(e) => {
              if (r.onClick && !r.active) {
                e.currentTarget.style.background = "var(--bg-elev)";
              }
            }}
            onMouseLeave={(e) => {
              if (r.onClick && !r.active) {
                e.currentTarget.style.background = "transparent";
              }
            }}
            title={r.onClick ? "点击筛选只看这一行" : undefined}
          >
            <td style={{ padding: "8px" }}>{r.label}</td>
            {showExtraColumn && (
              <td
                style={{
                  padding: "8px",
                  color: "var(--text-muted)",
                  fontSize: 12,
                }}
              >
                {r.extra}
              </td>
            )}
            <td style={{ padding: "8px" }}>
              <Bar pct={r.pct} color={r.barColor} />
            </td>
            <td style={{ padding: "8px", textAlign: "right" }}>
              {r.count.toLocaleString()}
            </td>
            <td style={{ padding: "8px", textAlign: "right", fontWeight: 500 }}>
              {fmtTokens(r.tokens)}
            </td>
            {showRmbColumn && (
              <td
                style={{
                  padding: "8px",
                  textAlign: "right",
                  color: "var(--text-muted)",
                  fontSize: 12,
                }}
              >
                {r.rmb !== undefined ? fmtRMB(r.rmb).replace("≈ ", "") : "—"}
              </td>
            )}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** 内嵌横向条 — 比例 % + 染色. */
function Bar({ pct, color }: { pct: number; color: string }) {
  const w = Math.max(0, Math.min(100, pct));
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
      }}
    >
      <div
        style={{
          flex: 1,
          height: 8,
          background: "var(--bg-elev)",
          borderRadius: 4,
          overflow: "hidden",
        }}
      >
        <div
          style={{
            width: `${w}%`,
            height: "100%",
            background: color,
            transition: "width 0.3s ease",
          }}
        />
      </div>
      <span
        style={{
          fontSize: 11,
          color: "var(--text-muted)",
          minWidth: 36,
          textAlign: "right",
        }}
      >
        {pct.toFixed(1)}%
      </span>
    </div>
  );
}

/** 给部门 / 员工分组用的备用调色板 (前 8 个清亮, 后面回滚). */
function _palette(i: number): string {
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
function DataReconciliation({ audit }: { audit: GlobalAudit }) {
  const modelSum = audit.by_model.reduce((s, m) => s + m.count, 0);
  const deptSum = audit.by_department?.reduce((s, d) => s + d.count, 0) ?? 0;
  const userSum = audit.by_user.reduce((s, u) => s + u.count, 0);

  const modelOk = modelSum === audit.request_count;
  const deptOk = deptSum === audit.request_count;
  const userOk = userSum === audit.request_count;

  if (modelOk && deptOk && userOk) return null;

  const rows = [
    { label: "按模型加和", sum: modelSum, ok: modelOk },
    { label: "按部门加和", sum: deptSum, ok: deptOk },
    { label: "按员工加和", sum: userSum, ok: userOk },
  ];

  return (
    <Card title="⚠️ 数据对账 (部分分组加和 ≠ 总数)">
      <div style={{ fontSize: 12, lineHeight: 1.6 }}>
        总请求 <strong>{audit.request_count.toLocaleString()}</strong>, 但:
        <ul style={{ margin: "6px 0 6px 18px", padding: 0 }}>
          {rows.map((r) => (
            <li
              key={r.label}
              style={{
                color: r.ok ? "inherit" : "var(--status-warn, #b45309)",
              }}
            >
              {r.label} = <strong>{r.sum.toLocaleString()}</strong>{" "}
              {r.ok
                ? "✓"
                : `✗ (差 ${(audit.request_count - r.sum).toLocaleString()})`}
            </li>
          ))}
        </ul>
        <div style={{ color: "var(--text-muted)", fontSize: 11 }}>
          典型原因: 老 audit 数据 user_email / department 缺 OIDC claim → 进 "(未分组)"
          桶 (本表已显式列出).
        </div>
      </div>
    </Card>
  );
}
