/** /admin — Admin 后台 (admin only) (BL-ARCH1 5/10).
 *
 * 全公司聚合 / 用户管理 / 配额规则 / billing.
 * P0 范围: 全局聚合 + 用户列表 (read-only) + 跳转链接.
 * P1: dev_users 编辑 / users.yaml 编辑 / billing 月报.
 */

import { useEffect, useState, type ReactNode } from "react";
import { Navigate, Routes, Route } from "react-router-dom";

import { Card } from "../components/Card";
import {
  BTN,
  BTN_PRIMARY,
  DataTable,
  Section,
  Tabs,
  Toolbar,
} from "../components/DataTable";
import {
  costRMB,
  fmtRMB,
  getModelDisplay,
  isCostEstimated,
  isKnownModel,
  splitDisplayName,
  totalCostRMB,
} from "../lib/modelDisplay";
import { RoleGate, roleAllows } from "../components/RoleGate";
import { useAuthStore } from "../store/auth";
import {
  fetchAuditEvents,
  fetchGlobalAudit,
  type AuditEvent,
  type AuditEventsResponse,
  type GlobalAudit,
} from "../lib/me";
import { UsersPage } from "./admin/UsersPage";
import { SystemPage } from "./admin/SystemPage";
// BL-Q3-FACT P0 MVP Day 2 (5/10): 事实补丁系统 UI
import { FactsPage } from "./admin/FactsPage";
// BL-RBAC-DAY7 (5/17): 4 维 RBAC dept 管理
import { AccessPage } from "./admin/AccessPage";
// 6/7 BL-MANIFESTO-ADVISORY-PHASE2: Advisory 管理 (sysadmin only)
import { AdvisoryPage } from "./admin/AdvisoryPage";
// P3.5.60 (6/22 鸿波 catch "继续完成"): 全公司 LLM 性能仪表
import { PerfPage } from "./admin/PerfPage";
// P3.5.93 (6/23 鸿波): /admin/quota 真编辑 UI, 替原 AdminQuota P0 placeholder.
// 一并治 AccessPage 部门 quota 6 周 dead UI (gateway 不读 identity-server).
import { AdminLayout } from "./admin/AdminLayout";
// 7/30 第二步: /audit 和 /manager 从顶层搬进 /admin, 页面本体原样复用
import { AuditPage } from "./AuditPage";
import { ManagerPage } from "./ManagerPage";
import { ROUTES, routePattern, toHref, type AdminPath } from "./admin/navConfig";
import { ModelConfigPage } from "./admin/ModelConfigPage";
import { QuotaConfigPage } from "./admin/QuotaConfigPage";

/** 每条路由对应的组件.
 *
 * 类型是 Record<AdminPath, ReactNode> —— navConfig 的 ROUTES 里加了一条却
 * 忘了在这里配组件, **是编译错误**。反过来配了不存在的路径也是编译错误。
 */
const ELEMENTS: Record<AdminPath, ReactNode> = {
  "": <AdminHome />,
  models: <ModelConfigPage />,
  quota: <QuotaConfigPage />,
  access: <AccessPage />,
  users: <UsersPage />,
  departments: <ManagerPage />,
  audit: <AuditPage />,
  perf: <PerfPage />,
  "quota/events": <AdminQuotaEvents />,
  system: <SystemPage />,
  facts: <FactsPage />,
  advisory: <AdvisoryPage />,
};

/** /admin 落地时按角色分流.
 *
 * 今日概况是全公司聚合, 后端 /api/audit/global 是 _require_admin —— manager
 * 会拿 403, 看到的只能是一句"没权限"。
 *
 * (第三步之前更糟: fetchGlobalAudit 吞异常返 null, 而页面把 null 当"还在加载",
 *  于是 manager 会**永远停在"加载中…"**。那个已经在 AdminHome 里修掉了,
 *  但把 manager 送到一个他注定看不了的页面依然没道理。)
 *
 * 所以 manager 落到 /admin 时直接送去他第一个能看的页面。
 */
function AdminIndex() {
  const me = useAuthStore((s) => s.me);
  if (me && roleAllows(me.role, ["admin", "sysadmin"])) return <AdminHome />;
  const first = ROUTES.find(
    (r) => r.path !== "" && me && roleAllows(me.role, [...r.require]),
  );
  return <Navigate to={first ? toHref(first.path) : "/"} replace />;
}

export function AdminPage() {
  // 7/30 第二步: 外层守卫从 admin+ 降到 manager+ —— /audit 和 /manager 搬进来
  // 之后, manager 也要能进这个壳。
  //
  // ⚠ 降之前有 6 个页面自己没守卫、全靠这一道拦着 (UsersPage / AccessPage /
  //   PerfPage / AdminQuotaEvents / AdminBilling / AdminHome)。现在每条路由
  //   各自套 RoleGate, require 来自 navConfig 那份单一事实源, 跟侧栏同源。
  return (
    <RoleGate require={["manager", "admin"]}>
      <AdminLayout>
        <Routes>
          {ROUTES.map((r) => (
            <Route
              key={r.path || "index"}
              {...(r.path === "" ? { index: true } : {})}
              path={routePattern(r)}
              element={
                r.path === "" ? (
                  <AdminIndex />
                ) : (
                  <RoleGate require={[...r.require]}>{ELEMENTS[r.path]}</RoleGate>
                )
              }
            />
          ))}
          {/* 兜底。没有这一条时, 不匹配任何路由的 /admin/xxx 会渲染 null ——
              左边侧栏还在, 右边整块空白, 没有任何文字。7/30 摘掉
              /admin/billing 之后立刻踩到: 客户书签和浏览器历史还会命中它。
              退到 index (再由 AdminIndex 按角色分流), 而不是原地留一片空白。 */}
          <Route path="*" element={<Navigate to="/admin" replace />} />
        </Routes>
      </AdminLayout>
    </RoleGate>
  );
}

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
function AdminHome() {
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
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
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
    </div>
  );
}

/** 环比.
 *
 * 上期为 0 时不显示 —— "从 0 涨到 5" 算成 +∞% 还是 +500% 都没有意义。
 *
 * `upIsGood` 必须逐个指定, 不能统一"涨=橙色警告": 总 tokens 涨是成本上升,
 * 活跃员工涨是推广见效。同一个配色套在四个指标上, 会把"活跃员工掉了 30%"
 * 画成绿色的正常。 */
function Delta({
  now,
  prev,
  upIsGood,
}: {
  now: number;
  prev?: number;
  upIsGood: boolean;
}) {
  if (prev == null || prev === 0) return null;
  const pct = ((now - prev) / prev) * 100;
  if (Math.abs(pct) < 0.5)
    return <span style={{ fontSize: 11, color: "var(--text-muted)" }}>持平</span>;
  const up = pct > 0;
  const good = up === upIsGood;
  return (
    <span
      style={{ fontSize: 11, color: good ? "var(--text-muted)" : "var(--status-warn)" }}
      title={`上期 ${prev.toLocaleString()}`}
    >
      {up ? "↑" : "↓"}
      {Math.abs(pct).toFixed(0)}%
    </span>
  );
}

/** 后端 by_model / by_department 是 `ORDER BY tokens DESC LIMIT 20`, by_user 是 LIMIT 50。 */
const BREAKDOWN_LIMIT = 20;

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
        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            alignItems: "stretch",
            gap: 0,
          }}
        >
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
        </div>
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
      <Section>
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
            rows={a.by_department}
            rowKey={(d) => d.department}
            empty="窗口内没有部门产生调用"
            footer={hint(a.by_department.length, BREAKDOWN_LIMIT, "个部门")}
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
            rows={a.by_model}
            rowKey={(m) => m.model}
            empty="窗口内没有模型被调用"
            footer={hint(a.by_model.length, BREAKDOWN_LIMIT, "个模型")}
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
            rows={a.by_user}
            rowKey={(u) => `${u.user_email}|${u.department}`}
            empty="窗口内没有员工产生调用"
            footer={hint(a.by_user.length, 50, "人")}
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

function Stat({
  label,
  value,
  delta,
  hint,
}: {
  label: string;
  value: string | number;
  delta?: ReactNode;
  hint?: string;
}) {
  return (
    <div
      style={{
        // 竖线分隔而不是靠间距 —— 靠间距的话在宽屏上要么挤在一起
        // 要么散开, 分隔线让每个指标的边界固定。
        padding: "0 20px",
        borderRight: "1px solid var(--border-soft)",
        minWidth: 96,
      }}
    >
      <div
        style={{
          fontSize: 11,
          color: "var(--text-muted)",
          marginBottom: 2,
        }}
      >
        {label}
      </div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 5 }}>
        <span style={{ fontSize: 20, fontWeight: 600, fontVariantNumeric: "tabular-nums" }}>
          {value}
        </span>
        {delta}
      </div>
      {hint ? (
        <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 1 }}>{hint}</div>
      ) : null}
    </div>
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

// BL-ADMIN-AUDIT (5/12 鸿波): /admin/quota/events — 逐条 audit 历史 + 筛选 + 分页 + CSV.
//
// 跟 /admin/quota 配额规则 read-only 文本互补 (一个看规则, 一个看实际数据).
// API: GET /api/audit/events (gateway, RBAC admin only — sysadmin 走 is_admin() 通过).

function AdminQuotaEvents() {
  const PAGE_SIZE = 50;
  const [data, setData] = useState<AuditEventsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  // 4 维筛选 + 时间窗口
  const [hoursBack, setHoursBack] = useState(24);
  const [dept, setDept] = useState("");
  const [userFilter, setUserFilter] = useState("");
  const [model, setModel] = useState("");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(0);

  const load = () => {
    setLoading(true);
    setError(null);
    const since_ms = Date.now() - hoursBack * 3600 * 1000;
    fetchAuditEvents({
      since_ms,
      dept: dept || undefined,
      user_filter: userFilter || undefined,
      model: model || undefined,
      status: status || undefined,
      limit: PAGE_SIZE,
      offset: page * PAGE_SIZE,
    })
      .then((r) => {
        if (r.ok) {
          setData(r.data);
        } else {
          setData(null);
          setError(r.error);
        }
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
    // 不进 deps — 手动 "查询" 按钮触发, 防一边输一边狂查 PG
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page]);

  const totalPages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;

  const exportCSV = () => {
    if (!data || data.events.length === 0) return;
    const header = [
      "ts", "user", "department", "model", "tokens_in", "tokens_out",
      "tokens_total", "latency_ms", "ttft_ms", "status", "error",
    ].join(",");
    const rows = data.events.map((e) => [
      new Date(e.ts * 1000).toISOString(),
      csvEscape(e.user),
      csvEscape(e.department),
      csvEscape(e.model),
      e.prompt_tokens,
      e.completion_tokens,
      e.total_tokens,
      e.latency_ms,
      e.ttft_ms ?? "",
      csvEscape(e.status),
      csvEscape(e.error ?? ""),
    ].join(","));
    const blob = new Blob([header + "\n" + rows.join("\n")], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `audit-events-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)" }}>
      <Card title="Quota 历史日志 (逐条)">
        <div style={{ color: "var(--text-muted)", fontSize: 12, marginBottom: 8 }}>
          直接读 gateway_audit 表. 跟 /admin/quota (规则) 互补 — 这里看实际数据,
          那里看配置. RBAC 严格 admin only (sysadmin 通过).
        </div>

        {/* 筛选条 */}
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 12 }}>
          <FilterField label="时间范围 (小时)">
            <select
              value={hoursBack}
              onChange={(e) => setHoursBack(Number(e.target.value))}
              style={inputStyle}
            >
              <option value={1}>过去 1h</option>
              <option value={6}>过去 6h</option>
              <option value={24}>过去 24h</option>
              <option value={72}>过去 3 天</option>
              <option value={168}>过去 7 天</option>
              <option value={720}>过去 30 天</option>
            </select>
          </FilterField>
          <FilterField label="部门">
            <input
              value={dept}
              onChange={(e) => setDept(e.target.value)}
              placeholder="例 engineering"
              style={inputStyle}
            />
          </FilterField>
          <FilterField label="员工 email">
            <input
              value={userFilter}
              onChange={(e) => setUserFilter(e.target.value)}
              placeholder="例 alice@ffcs.cn"
              style={inputStyle}
            />
          </FilterField>
          <FilterField label="模型">
            <input
              value={model}
              onChange={(e) => setModel(e.target.value)}
              placeholder="例 catfish-private-main"
              style={inputStyle}
            />
          </FilterField>
          <FilterField label="状态">
            <select
              value={status}
              onChange={(e) => setStatus(e.target.value)}
              style={inputStyle}
            >
              <option value="">全部</option>
              <option value="ok">ok</option>
              <option value="error">error</option>
              <option value="interrupted_resumed">interrupted_resumed</option>
            </select>
          </FilterField>
          <div style={{ display: "flex", alignItems: "flex-end", gap: 8 }}>
            <button
              onClick={() => { setPage(0); load(); }}
              style={btnStyle("primary")}
            >
              {loading ? "查询中…" : "查询"}
            </button>
            <button
              onClick={exportCSV}
              disabled={!data || data.events.length === 0}
              style={btnStyle("ghost")}
            >
              导出 CSV
            </button>
          </div>
        </div>

        {/* 结果 */}
        {loading && <div style={{ color: "var(--text-muted)" }}>加载中…</div>}
        {!loading && error && (
          <div
            style={{
              padding: 12,
              background: "#fef2f2",
              border: "1px solid #fecaca",
              borderRadius: 6,
              color: "#991b1b",
              fontSize: 13,
              lineHeight: 1.5,
            }}
          >
            <div style={{ fontWeight: 600, marginBottom: 6 }}>请求失败</div>
            <div style={{ fontFamily: "monospace", fontSize: 12 }}>{error}</div>
            <div style={{ marginTop: 10, color: "#7f1d1d" }}>
              常见原因:
              <ul style={{ paddingLeft: 20, margin: "4px 0 0 0" }}>
                <li><b>404 not found</b>: gateway 还没重启 — 新 endpoint <code>/api/audit/events</code> 是这次加的, 跑 <code>pkill -f catfish_gateway && sleep 3</code> 然后重启 gateway</li>
                <li><b>403 forbidden</b>: 当前 role 不是 admin / sysadmin (理论上你 sysadmin 不会撞到)</li>
                <li><b>401 unauthorized</b>: OIDC token 过期, 退出重登</li>
                <li><b>network error</b>: gateway 进程没起 / 端口换了</li>
              </ul>
            </div>
          </div>
        )}
        {!loading && !error && data && data.events.length === 0 && (
          <div style={{ color: "var(--text-muted)", padding: 16, textAlign: "center" }}>
            没有匹配的事件 — 试试放宽时间范围 / 清空筛选
          </div>
        )}
        {!loading && !error && data && data.events.length > 0 && (
          <>
            <div style={{ color: "var(--text-muted)", fontSize: 12, marginBottom: 6 }}>
              共 {data.total.toLocaleString()} 条, 当前 {page * PAGE_SIZE + 1}–
              {page * PAGE_SIZE + data.events.length} 条
            </div>
            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                <thead>
                  <tr style={{ borderBottom: "1px solid var(--border)" }}>
                    <th style={thStyle}>时间</th>
                    <th style={thStyle}>员工</th>
                    <th style={thStyle}>部门</th>
                    <th style={thStyle}>模型</th>
                    <th style={{ ...thStyle, textAlign: "right" }}>in</th>
                    <th style={{ ...thStyle, textAlign: "right" }}>out</th>
                    <th style={{ ...thStyle, textAlign: "right" }}>tot</th>
                    <th style={{ ...thStyle, textAlign: "right" }}>延迟 ms</th>
                    <th style={thStyle}>状态</th>
                    <th style={thStyle}>错误</th>
                  </tr>
                </thead>
                <tbody>
                  {data.events.map((e: AuditEvent, i) => (
                    <tr key={i} style={{ borderBottom: "1px solid var(--border)" }}>
                      <td style={tdStyle}>{new Date(e.ts * 1000).toLocaleString("zh-CN")}</td>
                      <td style={tdStyle}>{e.user}</td>
                      <td style={tdStyle}>{e.department}</td>
                      <td style={tdStyle}>{e.model}</td>
                      <td style={{ ...tdStyle, textAlign: "right" }}>{e.prompt_tokens}</td>
                      <td style={{ ...tdStyle, textAlign: "right" }}>{e.completion_tokens}</td>
                      <td style={{ ...tdStyle, textAlign: "right", fontWeight: 500 }}>{e.total_tokens}</td>
                      <td style={{ ...tdStyle, textAlign: "right" }}>{Math.round(e.latency_ms)}</td>
                      <td style={tdStyle}>
                        <StatusBadge status={e.status} />
                      </td>
                      <td style={{ ...tdStyle, color: "var(--text-muted)", maxWidth: 280, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                        title={e.error}
                      >
                        {e.error ?? ""}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {/* 分页 */}
            <div style={{ display: "flex", gap: 8, marginTop: 12, alignItems: "center" }}>
              <button
                onClick={() => setPage((p) => Math.max(0, p - 1))}
                disabled={page === 0}
                style={btnStyle("ghost")}
              >
                ← 上一页
              </button>
              <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
                第 {page + 1} / {totalPages} 页
              </span>
              <button
                onClick={() => setPage((p) => p + 1)}
                disabled={page + 1 >= totalPages}
                style={btnStyle("ghost")}
              >
                下一页 →
              </button>
            </div>
          </>
        )}
      </Card>
    </div>
  );
}

function csvEscape(s: string | number): string {
  const v = String(s);
  if (v.includes(",") || v.includes('"') || v.includes("\n")) {
    return '"' + v.replace(/"/g, '""') + '"';
  }
  return v;
}

function FilterField({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <span style={{ fontSize: 11, color: "var(--text-muted)" }}>{label}</span>
      {children}
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const color =
    status === "ok"
      ? "#10b981"
      : status === "interrupted_resumed"
      ? "#f59e0b"
      : "#ef4444";
  return (
    <span
      style={{
        display: "inline-block",
        padding: "2px 8px",
        borderRadius: 4,
        background: color + "22",
        color,
        fontSize: 11,
        fontWeight: 500,
      }}
    >
      {status}
    </span>
  );
}

const inputStyle: React.CSSProperties = {
  background: "var(--bg-elev)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius-sm)",
  padding: "4px 8px",
  color: "var(--text)",
  fontSize: 12,
  minWidth: 160,
};

const thStyle: React.CSSProperties = {
  padding: "6px 10px",
  textAlign: "left",
  fontWeight: 500,
  color: "var(--text-muted)",
  fontSize: 11,
  whiteSpace: "nowrap",
};

const tdStyle: React.CSSProperties = {
  padding: "6px 10px",
  whiteSpace: "nowrap",
};

function btnStyle(variant: "primary" | "ghost"): React.CSSProperties {
  return {
    padding: "6px 14px",
    borderRadius: "var(--radius-sm)",
    border: "1px solid " + (variant === "primary" ? "var(--accent)" : "var(--border)"),
    background: variant === "primary" ? "var(--accent)" : "transparent",
    color: variant === "primary" ? "white" : "var(--text)",
    cursor: "pointer",
    fontSize: 12,
  };
}
