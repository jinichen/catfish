/** /admin 首页 —— 全公司概况.
 *
 * 7/30 从 AdminPage.tsx 拆出来 (CLAUDE.md 军规 §1: ≥800 行红线)。
 * 拆之前 AdminPage.tsx 是 865 行 —— 这一轮改造把它从 563 推过了红线,
 * 而军规第 2 条要求"改任何 .tsx 文件后都跑一遍 check_file_sizes.sh",
 * 我这两天一次都没跑。
 *
 * 拆法按军规 §3: 这一族 (AdminHome / AdminHomeBody / Delta / Stat /
 * fmtTokens / hint) 整体搬过来 (8/1: LIMIT 常量已挪到 lib/me 的 AUDIT_TOP_N),
 * 不拆两半。AdminPage.tsx 顶部 re-export AdminHome 保 import 兼容 ——
 * 虽然当前只有它自己在用, 但协议就是协议。
 */

import { useEffect, useState } from "react";
import { PageShell } from "../../components/PageShell";

import {
  DataTable,
  Section,
  Tabs,
  Toolbar,
  BTN,
  BTN_PRIMARY,
} from "../../components/DataTable";
import { Delta, Stat, StatBand } from "../../components/StatBand";
import {
  costRMB,
  fmtRMB,
  getModelDisplay,
  isCostEstimated,
  isKnownModel,
  splitDisplayName,
  totalCostRMB,
} from "../../lib/modelDisplay";
import { AUDIT_TOP_N, fetchGlobalAudit, type GlobalAudit } from "../../lib/me";

function fmtTokens(n: number): string {
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}K`;
  return `${(n / 1_000_000).toFixed(2)}M`;
}

/** /admin 首页 —— 全公司概况 (7/30 第三步重写).
 *
 * ## 改之前的问题
 *
 * 部门和模型两块用 `<Row>` 竖排, 一行只放一条 `"12 请求 · 3.4M tok"` 的
 * **拼接字符串** —— 不能排序、不能对齐、不能看占比。整屏总信息量约
 * 4 个 KPI + 5 个部门 + 10 个模型 = 19 条事实, 这是全仓密度最低的数据表示。
 * 而这是每个管理员登录后看到的第一页。
 *
 * ## 三处不只是排版的修正
 *
 * **1. 少一次 API 调用。** 原来同时调 /api/quota/global 和 /api/audit/global,
 * 用前者的 top_departments 画部门表。两者都查 quota_events、都
 * GROUP BY department ORDER BY tokens DESC, 但**不是同一份数据**, 三处不同
 * (都是 by_department 更对):
 *
 *   · top_departments 不排除 internal:* 循环调用, by_department 排除
 *     —— 前者把 gateway 自己烧的 token 算进部门头上
 *   · top_departments 带 `AND department <> ''`, 把无部门的调用**整个丢掉**;
 *     by_department COALESCE 成一个 (未分组) 桶, 数对得上总数
 *   · top_departments 的窗口写死 24h; by_department 跟随 since_hours
 *
 * 所以这里改用 by_department 不只是"少调一次", 数字本身也更准。
 *
 * **2. 标题不再说谎。** 原来写死"今日", 而 /api/audit/global 默认是
 * **24 小时滚动窗**, 页面上也没有任何时间选择器。后端本来就收 since_hours
 * (1-720), AuditPage 早就在用。现在这里也给出选择器, 标题跟着窗口变。
 *
 * **3. 拉取失败不再假装在加载。** fetchGlobalAudit 吞异常返 null, 原来的
 * `{!globalA && <div>加载中…</div>}` 会让失败**永远停在"加载中…"**。
 * manager 误入这页时必然如此 (后端是 _require_admin)。现在区分三态。
 * 注: lib/me.ts 里 6/22 已经为 perf 链引入了 FetchResult 模式治同一个病,
 * 当时注明"fetchGlobalAudit 老接口不动 (向后兼容)" —— 这里在调用侧补上。
 */
export function AdminHome() {
  const [audit, setAudit] = useState<GlobalAudit | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [hours, setHours] = useState(24);

  useEffect(() => {
    let alive = true;
    setAudit(null);
    setErr(null);
    fetchGlobalAudit(hours)
      .then((a) => {
        if (!alive) return;
        if (a) setAudit(a);
        else setErr("拉取失败 —— 没权限, 或 gateway 报错。");
      })
      .catch((e) => alive && setErr(e instanceof Error ? e.message : String(e)));
    return () => {
      alive = false;
    };
  }, [hours]);

  const windowLabel = hours === 24 ? "24 小时" : hours === 168 ? "7 天" : "30 天";

  return (
    <PageShell scroll="data">
      <Toolbar title="全公司概况">
        <span style={{ color: "var(--text-muted)", fontSize: 11 }}>近 {windowLabel}</span>
        {([
          [24, "24 小时"],
          [168, "7 天"],
          [720, "30 天"],
        ] as const).map(([h, label]) => (
          <button
            key={h}
            style={h === hours ? BTN_PRIMARY : BTN}
            onClick={() => setHours(h)}
          >
            {label}
          </button>
        ))}
      </Toolbar>

      {err ? (
        <Section>
          <div style={{ fontSize: 12, color: "var(--status-err)" }}>{err}</div>
        </Section>
      ) : !audit ? (
        <Section>
          <div style={{ fontSize: 12, color: "var(--text-muted)" }}>加载中…</div>
        </Section>
      ) : (
        <AdminHomeBody a={audit} />
      )}
    </PageShell>
  );
}

/** 环比.
 *
 * 上期为 0 时不显示 —— "从 0 涨到 5" 算成 +∞% 还是 +500% 都没有意义。
 *
 * `upIsGood` 必须逐个指定, 不能统一"涨=橙色警告": 总 tokens 涨是成本上升,
 * 活跃员工涨是推广见效。同一个配色套在四个指标上, 会把"活跃员工掉了 30%"
 * 画成绿色的正常。 */
// 8/1: Stat / Delta 挪到 components/StatBand.tsx —— 审计页有一份算法
// 不一样的副本 (总 tokens 涨在这页是橙色警告, 在那页是绿色好消息)。
// 理由见 StatBand.tsx 文件头。

type Breakdown = "dept" | "model" | "user";

/** 表末尾那行说明. 后端对每张表都有 LIMIT, 但页面上看不出来 ——
 *  20 个部门时标题写 (20), 用户没法知道是"正好 20 个"还是"被截到 20 个",
 *  而占比列也就永远加不到 100%。 */
function hint(n: number, limit: number, unit: string): string | undefined {
  return n >= limit ? `只显示用量最高的 ${limit} ${unit}，占比之和会不足 100%` : undefined;
}

function AdminHomeBody({ a }: { a: GlobalAudit }) {
  const [tab, setTab] = useState<Breakdown>("dept");
  // 分母用 a.total_tokens 而不是各行之和 —— 两者同一个 SQL 事务、同一套 WHERE,
  // 口径一致。差别只在上面那个 LIMIT 20: 超过 20 个时占比之和会不足 100%,
  // 这正是标题里 "top" 想说明的事。
  const pct = (n: number) => {
    if (!a.total_tokens) return "-";
    const p = (n / a.total_tokens) * 100;
    // 非零但不足 1% 的显示 "<1%" 而不是 "0%" —— 旁边明明挂着 8.0K tokens,
    // 却写 0%, 看起来像算错了。
    if (p > 0 && p < 1) return "<1%";
    return `${p.toFixed(0)}%`;
  };

  return (
    <>
      <Section>
        {/* 横向指标带, 靠左排, 之间用竖线分隔。
            原来是 `repeat(auto-fit, minmax(120px,1fr))` —— 4 个指标在 1400px
            宽屏上被平均拉开成每格 350px, 数字贴在各自格子左边缘,
            中间三大片空白, 看起来像没做完。 */}
        <StatBand>
          <Stat label="总请求" value={a.request_count.toLocaleString()}
                delta={<Delta now={a.request_count} prev={a.previous_request_count} upIsGood={false} />} />
          <Stat label="总 tokens" value={fmtTokens(a.total_tokens)}
                delta={<Delta now={a.total_tokens} prev={a.previous_total_tokens} upIsGood={false} />} />
          <Stat label="活跃员工" value={a.active_users}
                delta={<Delta now={a.active_users} prev={a.previous_active_users} upIsGood />} />
          <Stat label="活跃部门" value={a.active_departments}
                delta={<Delta now={a.active_departments} prev={a.previous_active_departments} upIsGood />} />
          {/* 成本 —— 管理员真正被问到的那个数, 而它一直没在概览上出现过。
              单价 7/30 起可以在「模型」页逐个填 (price_per_1k_tokens);
              没填的模型走兜底价, 所以下面标"含估算"。 */}
          <Stat
            label="成本"
            value={fmtRMB(totalCostRMB(a.by_model))}
            hint={
              a.by_model.some((m) => isCostEstimated(m.model))
                ? "含估算 · 到「模型」页填单价"
                : undefined
            }
          />
        </StatBand>
        {/* gateway 自身的循环消耗 (总结 / 主动提醒 / 注入等), 不算员工业务。
            原来这个数只在审计页有一整张 Card, 首页完全不提 —— 于是"我们自己
            烧了多少"这件事在概览层面是不可见的。一行脚注够了。 */}
        {a.internal_request_count ? (
          <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 6 }}>
            另有系统内部调用 {a.internal_request_count.toLocaleString()} 次 ·{" "}
            {fmtTokens(a.internal_tokens ?? 0)} tok（总结 / 主动提醒等，不计入上面的员工业务）
          </div>
        ) : null}
      </Section>

      {/* 7/30 二改: 三张表从并排改成分段切换.
          并排时每张只分到约 470px, 而 4-5 列里还有模型显示名和邮箱这种长文本
          —— 结果是**每个框内部各自横向滚动**, 左边的列被切掉半个字, 表头和
          数据错位。这比多点一下糟得多。
          这三张回答的是同一个问题的三个切面 (这些 token 花在哪), 正是分段
          切换该用的场景。真要横向交叉比对, 去「用量审计」下钻。 */}
      <Section fill>
        <div style={{ marginBottom: 6 }}>
          <Tabs
            active={tab}
            onChange={setTab}
            tabs={[
              { key: "dept", label: `按部门 ${a.by_department.length}` },
              { key: "model", label: `按模型 ${a.by_model.length}` },
              { key: "user", label: `按员工 ${a.by_user.length}` },
            ]}
          />
        </div>

        {tab === "dept" && (
          <DataTable
            fill
            rows={a.by_department}
            rowKey={(d) => d.department}
            empty="窗口内没有部门产生调用"
            footer={hint(a.by_department.length, AUDIT_TOP_N.department, "个部门")}
            columns={[
              { header: "部门", cell: (d) => d.department },
              { header: "请求", align: "right", width: 90, cell: (d) => d.count.toLocaleString() },
              { header: "tokens", align: "right", width: 110, cell: (d) => fmtTokens(d.total_tokens) },
              {
                header: "占比",
                align: "right",
                width: 70,
                cell: (d) => (
                  <span style={{ color: "var(--text-muted)" }}>{pct(d.total_tokens)}</span>
                ),
              },
            ]}
          />
        )}

        {tab === "model" && (
          <DataTable
            fill
            rows={a.by_model}
            rowKey={(m) => m.model}
            empty="窗口内没有模型被调用"
            footer={hint(a.by_model.length, AUDIT_TOP_N.model, "个模型")}
            columns={[
              {
                header: "模型",
                // 显示名走 modelDisplay —— 客户在「模型」页改了显示名, 这里
                // 立刻跟着变, 而不是把 catalog ID 摆给老板看。
                // 全宽之后放得下完整的 `模型名 · 说明`, 说明那半截压成灰色。
                cell: (m) => {
                  // 认不出来就显示原始 ID —— 兜底 friendly 是固定的"未知模型",
                  // 几个自建模型会全塌成同一行文字。
                  if (!isKnownModel(m.model)) {
                    return <code style={{ fontSize: 11 }}>{m.model || "(未知)"}</code>;
                  }
                  const d = getModelDisplay(m.model);
                  const { name, note } = splitDisplayName(d.friendly);
                  return (
                    <span title={m.model}>
                      {d.dotEmoji} {name}
                      {note ? (
                        <span style={{ color: "var(--text-muted)" }}> · {note}</span>
                      ) : null}
                    </span>
                  );
                },
              },
              { header: "请求", align: "right", width: 90, cell: (m) => m.count.toLocaleString() },
              { header: "tokens", align: "right", width: 110, cell: (m) => fmtTokens(m.total_tokens) },
              {
                header: "成本",
                align: "right",
                width: 110,
                cell: (m) => (
                  <span
                    style={{ color: isCostEstimated(m.model) ? "var(--text-muted)" : undefined }}
                    title={isCostEstimated(m.model) ? "没配单价, 按兜底价估的" : undefined}
                  >
                    {fmtRMB(costRMB(m.model, m.total_tokens))}
                  </span>
                ),
              },
              {
                header: "占比",
                align: "right",
                width: 70,
                cell: (m) => (
                  <span style={{ color: "var(--text-muted)" }}>{pct(m.total_tokens)}</span>
                ),
              },
            ]}
          />
        )}

        {tab === "user" && (
          <DataTable
            fill
            rows={a.by_user}
            rowKey={(u) => `${u.user_email}|${u.department}`}
            empty="窗口内没有员工产生调用"
            footer={hint(a.by_user.length, AUDIT_TOP_N.user, "人")}
            columns={[
              { header: "员工", cell: (u) => u.user_email },
              {
                // 后端是 GROUP BY (员工, 部门), 所以同一个人在两个部门会出两行。
                // 不显示部门的话看起来就是"同一个邮箱重复了两次", 像 bug。
                // 这也解释了为什么"活跃员工"数 (COUNT DISTINCT email) 可能
                // 小于这张表的行数。
                header: "部门",
                width: 160,
                cell: (u) => (
                  <span style={{ color: "var(--text-muted)" }}>
                    {u.department || "(未分组)"}
                  </span>
                ),
              },
              { header: "请求", align: "right", width: 90, cell: (u) => u.count.toLocaleString() },
              { header: "tokens", align: "right", width: 110, cell: (u) => fmtTokens(u.total_tokens) },
              {
                header: "占比",
                align: "right",
                width: 70,
                cell: (u) => (
                  <span style={{ color: "var(--text-muted)" }}>{pct(u.total_tokens)}</span>
                ),
              },
            ]}
          />
        )}
      </Section>
    </>
  );
}


// AdminUsers 旧 inline 版本删了 (BL-ARCH1 P1, 被 routes/admin/UsersPage.tsx 完整 CRUD 替代).

// P3.5.93 (6/23 鸿波): 老 AdminQuota P0 placeholder 函数砍 — /admin/quota
// 已切到 QuotaConfigPage 真编辑. 文本里写的 "P1 加 web 编辑 UI" 6 周后真做了.

// 7/30: AdminBilling 删了。它的正文是
//   "P1 实现. 设计: 按月统计 token 用量 / 按部门分摊成本 / 导出 PDF / 同期对比"
// —— 我们自己的设计备忘录, 被当成活路由渲染给客户看, 还占着侧栏「观测 → 成本」
// 一个位置。客户点进去看到"这个功能还没做, 以下是我们的计划", 比压根没有
// 这一项更糟。设计条目挪进 docs/BACKLOG.md (BL-CENTRAL-BILLING), 真做的时候
// 在 navConfig.ts 加回一行、这里加回一个组件即可。

