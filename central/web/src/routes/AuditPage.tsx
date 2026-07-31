/** /audit — 审计大查询 (manager + admin) (BL-ARCH1 5/10).
 *
 * 改造历史:
 *   BL-ARCH1 (5/10):     P0 初版 — 4 stat 卡 + 3 表格
 *   BL-AUDIT-P0-FIX:     修数据信任 bug + (未分组) 桶
 *   BL-AUDIT-P0-FIX-V2:  sysadmin 也算全公司视角
 *   BL-AUDIT-UX-P0 (5/17): UX 工程师化重做 — KPI hero / 模型 friendly /
 *                          inline bar / 异常告警占位
 *   8/1: 跟概览页 / 性能页对齐 — 三张表改分段切换, KPI 换共享指标带,
 *        表格换共享 DataTable, 删掉永远不渲染的异常告警占位
 */

import { useEffect, useState } from "react";

import { Section, Tabs } from "../components/DataTable";
import { PageShell, Stale } from "../components/PageShell";
import { RoleGate } from "../components/RoleGate";
import {
  fetchGlobalAudit,
  type AuditFilter,
  type GlobalAudit,
} from "../lib/me";


// 5/20 拆 1090 → ~131: helpers + 4 子文件抽到 audit/
import { KPIHero } from "./audit/AuditKPI";
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
  /** 明细表按哪个维度看。跟概览页/性能页同一套 —— 三个观测页应该一个风格。 */
  const [tab, setTab] = useState<"model" | "dept" | "user">("model");

  useEffect(() => {
    // 8/1 加的 alive 标志。没有它的话, 连着换两次时间窗会出三种错:
    //
    //   · 先发那个的 finally 把 loading 清掉, 而后发的还在飞 ——
    //     压暗提前撤销, 陈旧的数字看起来像是新的
    //   · 响应乱序时后到的是旧窗口的数据, 而按钮已经跳到新窗口
    //   · 先发那个失败 → setError 盖在已经刷新过的数据上, 而界面上
    //     那句"下面显示的还是上一次拉到的数据"这时是假话
    //
    // 三种都不会报错, 表现只是"数字有时候不对" —— 而这恰恰是审计页
    // 最不能有的毛病: 数字一旦被怀疑过, 整页就没人信了。
    let alive = true;
    setLoading(true);
    setError(null);
    fetchGlobalAudit(sinceHours, filter)
      .then((a) => {
        if (!alive) return;
        if (!a) setError("拉取 audit 失败 (没权限或后端报错)");
        else setAudit(a);
      })
      .catch((e) => {
        if (alive) setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [sinceHours, filter]);

  // BL-AUDIT-UX-P2: 一次只允许 1 个 filter 维度. 点新行 → 替换 (不叠加).
  const setSingleFilter = (next: AuditFilter) => setFilter(next);
  const clearFilter = () => setFilter({});

  return (
    <RoleGate require={["manager", "admin"]}>
      <PageShell scroll="data">
        {error && (
          <Section>
            <div style={{ fontSize: 12, color: "var(--status-err)" }}>
              {error}
              {/* 拉取失败时 audit 保持上一次的值, 于是页面上会同时有:
                  报错、新的筛选条、以及**旧的数字**。不说清楚的话, 管理员
                  会拿这些旧数字当成筛选后的结果。 */}
              {audit && "　—— 下面显示的还是上一次拉到的数据。"}
            </div>
          </Section>
        )}
        {loading && !audit && (
          <Section>
            <div style={{ fontSize: 12, color: "var(--text-muted)" }}>加载中…</div>
          </Section>
        )}
        {audit && (
          <>
            {/* P3.5.26.2 (6/17): revert sticky 头部. NavBar 单独 sticky (新),
                audit 内容正常 scroll. KPI / tables / PageHeader 都跟整页一起
                滚, NavBar 永远 top. 简单, 无 sticky 头部跟 KPI 卡层叠/抖动问题. */}

            {/* ── 标题区 + 工具栏 (BL-AUDIT-UX-P1: 时间窗切换 + CSV 导出) ──
                ⚠ 工具栏和筛选条**不包 Stale** —— 它们是"改主意"的出口
                (换时间窗 / 清除筛选)。请求卡住时把出口一起禁掉的话,
                唯一能做的就是刷新整页。 */}
            <PageHeader
              audit={audit}
              sinceHours={sinceHours}
              onChangeWindow={setSinceHours}
              loading={loading}
            />

            {/* ── BL-AUDIT-UX-P2: 当前 filter pill chip (仅有 filter 时显) ── */}
            <FilterPillBar filter={filter} onClear={clearFilter} />

            {/* ── 指标带 + gateway 自身消耗的脚注 (同概览页) ── */}
            <Section>
              <Stale loading={loading}>
                <KPIHero audit={audit} />
                {/* BL-AUDIT-INTERNAL-SPLIT (5/17): internal loopback 单独透明度.
                    放在同一个壳里、但用小字分开 —— 它跟上面那排不是一个口径,
                    独立成块的话又会让人以为是另一组主指标。 */}
                {(audit.internal_tokens ?? 0) > 0 && (
                  <div style={{ marginTop: 6 }}>
                    <InternalLoopbackCard audit={audit} />
                  </div>
                )}
              </Stale>
            </Section>

            {/* ── 数据对账 (BL-AUDIT-P0-FIX, 数字不一致时显, 一致时静默) ── */}
            <Stale loading={loading}>
              <DataReconciliation audit={audit} />
            </Stale>

            {/* 8/1: 三张表原来竖排 —— 各自 5-6 列在 1450px 里只用一半宽,
                而按部门和按员工**每次都在首屏之外**, 看完模型要滚两次。
                这跟概览页和性能页是同一个问题, 那两页 7/30 已经改成分段切换,
                这一页当时排在第 5 位被跳过了, 结果是三个观测页两种风格。

                这三张回答的是同一个问题的三个切面 (这些 token 花在哪),
                正是分段该用的场景。 */}
            <Section fill>
              {/* Tabs **不包 Stale**: 切维度是纯前端的, 不发请求。跟着一起
                  压暗禁点的话, 刷新期间连"我想看部门"都执行不了, 而它本来
                  立刻就能生效。
                  (子元素的 opacity 盖不住祖先的, 所以这件事只能靠不包在
                   里面, 不能靠在里面写 opacity: 1。) */}
              <div style={{ marginBottom: 6 }}>
                <Tabs
                  active={tab}
                  onChange={setTab}
                  tabs={[
                    { key: "model", label: `按模型 ${audit.by_model.length}` },
                    { key: "dept", label: `按部门 ${audit.by_department.length}` },
                    { key: "user", label: `按员工 ${audit.by_user.length}` },
                  ]}
                />
              </div>

              <Stale fill loading={loading}>
                {tab === "model" ? (
                  <ModelBreakdownCard
                    audit={audit}
                    onClickRow={(model) => setSingleFilter({ model })}
                    activeModel={filter.model ?? null}
                  />
                ) : tab === "dept" ? (
                  <DeptBreakdownCard
                    audit={audit}
                    onClickRow={(dept) => setSingleFilter({ dept })}
                    activeDept={filter.dept ?? null}
                  />
                ) : (
                  <UserBreakdownCard
                    audit={audit}
                    onClickRow={(user_email) => setSingleFilter({ user_email })}
                    activeUser={filter.user_email ?? null}
                  />
                )}
              </Stale>
            </Section>
          </>
        )}
      </PageShell>
    </RoleGate>
  );
}

/** BL-AUDIT-UX-P2: 当前 filter pill, 有 filter 时显示 + 一键清除. */
