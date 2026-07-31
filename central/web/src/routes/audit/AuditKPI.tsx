/** 审计页顶部的指标带 — 抽自 AuditPage.tsx (5/20 拆分).
 *
 * 8/1 重做: 这一排跟概览页显示的是同一组数, 现在也用同一个组件
 * (components/StatBand)。原来两页各写一份, 结果连涨跌的颜色语义都不一样
 * —— 详见 StatBand.tsx 文件头。
 */

import { useMemo } from "react";

import { Delta, Stat, StatBand } from "../../components/StatBand";
import type { GlobalAudit } from "../../lib/me";
import { fmtRMB, isCostEstimated, totalCostRMB } from "../../lib/modelDisplay";
import { fmtTokens, windowLabelOf } from "./helpers";

function KPIHero({ audit }: { audit: GlobalAudit }) {
  // BL-AUDIT-UX-P1: 精算 RMB — 按 model 单价加权
  const totalRMB = useMemo(() => totalCostRMB(audit.by_model), [audit.by_model]);
  const windowLabel = windowLabelOf(audit.since_hours);

  return (
    <StatBand>
      {/* 主指标给 tokens 而不是请求数 —— 这一页是拿来查"钱花在哪"的,
          而请求数跟花费不成比例 (一次长上下文顶几百次短问答)。 */}
      <Stat
        hero
        label={`总 tokens · ${windowLabel}`}
        value={fmtTokens(audit.total_tokens)}
        delta={
          <Delta
            now={audit.total_tokens}
            prev={audit.previous_total_tokens}
            upIsGood={false}
          />
        }
      />
      <Stat
        label="成本"
        value={fmtRMB(totalRMB)}
        hint={
          audit.by_model.some((m) => isCostEstimated(m.model))
            ? "含估算 · 到「模型」页填单价"
            : undefined
        }
      />
      <Stat
        label="总请求"
        value={audit.request_count.toLocaleString()}
        delta={
          <Delta
            now={audit.request_count}
            prev={audit.previous_request_count}
            upIsGood={false}
          />
        }
      />
      <Stat
        label="活跃员工"
        value={audit.active_users.toLocaleString()}
        delta={
          <Delta
            now={audit.active_users}
            prev={audit.previous_active_users}
            upIsGood
          />
        }
      />
      <Stat
        label="活跃部门"
        value={audit.active_departments.toLocaleString()}
        hint="只数有部门归属的员工"
        delta={
          <Delta
            now={audit.active_departments}
            prev={audit.previous_active_departments}
            upIsGood
          />
        }
      />
    </StatBand>
  );
}

export { KPIHero };
