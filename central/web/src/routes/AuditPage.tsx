/** /audit — 审计大查询 (manager + admin) (BL-ARCH1 5/10).
 *
 * 改造历史:
 *   BL-ARCH1 (5/10):     P0 初版 — 4 stat 卡 + 3 表格
 *   BL-AUDIT-P0-FIX:     修数据信任 bug + (未分组) 桶
 *   BL-AUDIT-P0-FIX-V2:  sysadmin 也算全公司视角
 *   BL-AUDIT-UX-P0 (5/17): UX 工程师化重做 — KPI hero / 模型 friendly /
 *                          inline bar / 异常告警占位
 */

import { useEffect, useState } from "react";

import { Card } from "../components/Card";
import { RoleGate } from "../components/RoleGate";
import {
  fetchGlobalAudit,
  type AuditFilter,
  type GlobalAudit,
} from "../lib/me";


// 5/20 拆 1090 → ~131: helpers + 4 子文件抽到 audit/
import { AnomalyBanner, KPIHero } from "./audit/AuditKPI";
import {
  DeptBreakdownCard,
  InternalLoopbackCard,
  ModelBreakdownCard,
  UserBreakdownCard,
} from "./audit/AuditBreakdowns";
import { FilterPillBar, PageHeader } from "./audit/AuditControls";
import DataReconciliation from "./audit/DataReconciliation";

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
            {/* P3.5.26.1 (6/17 鸿波"滚动到底再往上抖动"): 改 sticky 不用 negative
                coord. 原 commit 6ebdce7 用 top: -16px + marginLeft/Right/Top: -16px
                抵消 main padding 真**已知 Webkit sticky bug** — sticky 元素负 top +
                负 margin 当 scroll 真临界 (overscroll bounce / 边界 reflow) 时
                browser 重 compute bounding box 触发 repaint 抖动.
                修法: top: 0 + 真**不抵消** main padding. sticky 真**贴 main 真
                有效 scroll viewport** (在 main padding-top 之下 16px gap),
                视觉上头部不贴 NavBar 真底, 但真**稳定不抖**. trade off 接受. */}
            <div
              style={{
                position: "sticky",
                top: 0,
                zIndex: 5,
                background: "var(--bg)",
                paddingBottom: "var(--space-3)",
                borderBottom: "1px solid var(--border)",
                display: "flex",
                flexDirection: "column",
                gap: "var(--space-3)",
              }}
            >
              {/* ── 标题区 + 工具栏 (BL-AUDIT-UX-P1: 时间窗切换 + CSV 导出) ── */}
              <PageHeader
                audit={audit}
                sinceHours={sinceHours}
                onChangeWindow={setSinceHours}
                loading={loading}
              />

              {/* ── BL-AUDIT-UX-P2: 当前 filter pill chip (仅有 filter 时显) ── */}
              <FilterPillBar filter={filter} onClear={clearFilter} />
            </div>

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
