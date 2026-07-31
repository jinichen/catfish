/** AuditPage KPI hero 区 — 抽自 AuditPage.tsx (5/20 拆分).
 *
 * AnomalyBanner + KPIHero + Trend + SubStat. KPI 大数 + 上周对比 + 异常告警.
 */

import { useMemo, type ReactNode } from "react";

import { Card } from "../../components/Card";
import type { GlobalAudit } from "../../lib/me";
import { fmtRMB, totalCostRMB } from "../../lib/modelDisplay";
import { fmtTokens, windowLabelOf } from "./helpers";


function AnomalyBanner({ audit: _audit }: { audit: GlobalAudit }) {
  // P1 占位: backend 加 anomalies 后这里就有实数据
  const anomalies: { kind: string; text: string }[] = [];
  if (anomalies.length === 0) return null;
  return (
    <Card title="⚠️ 异常告警">
      <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>
        {anomalies.map((a, i) => (
          <li key={i} style={{ color: "var(--status-warn)" }}>
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
          color: "var(--status-warn)",
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


export { AnomalyBanner, KPIHero, Trend, SubStat };
