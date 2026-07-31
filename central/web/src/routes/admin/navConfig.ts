/** 后台路由 + 侧栏的**单一事实源** (7/30 第二步).
 *
 * ## 为什么必须合成一份
 *
 * 这一步把 /audit 和 /manager 并进 /admin, 所以 AdminPage 外层那道 RoleGate
 * 得从 admin+ 降到 manager+。而降之前查出来: 有 6 个页面**自己没有守卫**,
 * 全靠那道外层拦着 ——
 *
 *     UsersPage / AccessPage / PerfPage / AdminQuotaEvents /
 *     AdminBilling / AdminHome        (AdminBilling 已于 7/30 第三步删除)
 *
 * 一降就全部对 manager 敞开。逐个补守卫能修, 但"守卫写在别处"这种耦合正是
 * 问题本身: 下一个加页面的人不会知道自己必须补一道。
 *
 * 所以每条路由在这里声明 require, 侧栏过滤和 <Route> 守卫都从这一份生成:
 *   · 侧栏项和页面守卫同源 → 不可能出现"菜单里有、点进去 403"
 *   · 漏声明 require 是类型错误 → 不可能忘了加守卫
 *   · 漏给某条路由配组件也是类型错误 (见下面 AdminPath)
 *
 * 元素本身**不放这里** —— AdminHome / AdminQuotaEvents 定义在
 * AdminPage.tsx 里, ManagerPage / AuditPage 在 routes/ 下, 搬进来会绕成
 * 循环依赖。改成由 AdminPage 提供一张 Record<AdminPath, ReactNode>, TS 保证
 * 它覆盖所有路径。
 */

import type { Role } from "../../lib/me";

const ADMIN = ["admin", "sysadmin"] as const;
const MANAGER = ["manager", "admin"] as const;
const SYSADMIN = ["sysadmin"] as const;

/** 分组渲染顺序. 侧栏按这个走, 不按 ROUTES 里的出现顺序. */
export const GROUP_ORDER = ["总览", "接入", "人员", "观测", "系统"] as const;

/** 路由表.
 *
 * require 逐个核对过各页面原本的守卫 (7/30):
 *   Advisory / ModelConfig / QuotaConfig / System → sysadmin
 *   Facts → admin+                Audit / Departments → manager+
 *   其余原本靠 AdminPage 外层的 admin+ —— 现在显式写出来
 *
 * nested: 页面内部还有子路由, <Route path> 要带 /*
 */
export const ROUTES = [
  {
    path: "",
    label: "今日概况",
    group: "总览",
    // 全公司聚合。后端 /api/quota/global 与 /api/audit/global 都是
    // _require_admin, manager 会拿 403 —— 而前端那两个 fetch 吞异常返 null,
    // 页面会**永远停在"加载中…"**。所以这里必须 admin+, 且 manager 落到
    // /admin 时得重定向走 (见 AdminPage 的 index 路由)。
    require: ADMIN,
  },
  { path: "models", label: "模型", group: "接入", require: SYSADMIN },
  { path: "quota", label: "配额", group: "接入", require: SYSADMIN },
  { path: "access", label: "部门权限", group: "接入", require: ADMIN, nested: true },
  { path: "users", label: "用户", group: "人员", require: ADMIN, nested: true },
  {
    path: "departments",
    label: "我管的部门",
    group: "人员",
    // 7/30 从顶层 /manager 搬来
    require: MANAGER,
    nested: true,
  },
  {
    path: "audit",
    label: "用量审计",
    group: "观测",
    // 7/30 从顶层 /audit 搬来
    require: MANAGER,
  },
  { path: "perf", label: "性能", group: "观测", require: ADMIN },
  // 7/30: 「成本」(billing) 从侧栏摘掉了。那一页的内容是
  //   "P1 实现. 设计: 按月统计 token 用量 / 按部门分摊成本 / 导出 PDF / …"
  // —— 一份我们自己的设计备忘录, 被当成活路由渲染给客户看。
  // 客户点进去看到"这个功能还没做, 以下是我们的计划", 比压根没有这一项更糟。
  // 组件也一并删了 —— AdminPath 是从这张表推出来的联合类型, 摘掉一行之后
  // AdminPage 的 ELEMENTS 再留着 billing 就是编译错误 (这正是第二步想要的
  // 效果: 表和实现不可能各走各的)。设计条目见 docs/BACKLOG.md BL-CENTRAL-BILLING。
  { path: "quota/events", label: "配额日志", group: "观测", require: ADMIN },
  { path: "system", label: "服务状态", group: "系统", require: SYSADMIN, nested: true },
  { path: "facts", label: "政策同步", group: "系统", require: ADMIN, nested: true },
  { path: "advisory", label: "公告发布", group: "系统", require: SYSADMIN },
] as const;

/** 所有合法路径的联合类型. AdminPage 的元素表必须覆盖它, 少一个就编译不过。 */
export type AdminPath = (typeof ROUTES)[number]["path"];

export interface AdminRoute {
  path: AdminPath;
  label: string;
  group: string;
  require: readonly Role[];
  nested?: boolean;
}

/** <Route path> 该写什么.
 *
 * 单独抽出来是因为 ROUTES 上了 as const —— 联合类型里没写 nested 的那几项
 * 根本没有 nested 这个属性, 直接 r.nested 是编译错误。收进函数参数
 * ({ nested?: boolean }) 就都能过, 顺便让 "nested 要带 /*" 这条规则跟
 * nested 的声明待在同一个文件里。
 *
 * index 路由 (path: "") 返 undefined —— 由 <Route index> 承担。
 */
export function routePattern(r: { path: string; nested?: boolean }): string | undefined {
  if (!r.path) return undefined;
  return r.nested ? `${r.path}/*` : r.path;
}

/** 绝对路径. "" → "/admin", "models" → "/admin/models" */
export function toHref(path: string): string {
  return path ? `/admin/${path}` : "/admin";
}

/** 老 URL → 新 URL. 第二步搬了两个顶层路由, 老链接要能落地.
 *
 * 为什么必须有: Companion 的 WebPortalLink 硬编码了 /audit 和 /manager
 * (tabs/Dashboard/WebPortalLink.tsx), 而 App.tsx 末尾有
 * `<Route path="*" element={<Navigate to="/" replace />} />` ——
 * 没有重定向的话, 老版 Companion 点「审计」会**静默跳到首页**, 不报错、
 * 不提示。Companion 这次也一起改了, 但装了旧版的机器不会立刻升级。
 */
export const LEGACY_REDIRECTS: ReadonlyArray<{ from: string; to: string }> = [
  { from: "/audit", to: "/admin/audit" },
  { from: "/manager", to: "/admin/departments" },
];
