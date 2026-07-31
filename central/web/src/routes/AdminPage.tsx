/** /admin — Admin 后台 (admin only) (BL-ARCH1 5/10).
 *
 * 全公司聚合 / 用户管理 / 配额规则 / billing.
 * P0 范围: 全局聚合 + 用户列表 (read-only) + 跳转链接.
 * P1: dev_users 编辑 / users.yaml 编辑 / billing 月报.
 */

import type { ReactNode } from "react";
import { Navigate, Routes, Route } from "react-router-dom";

import { RoleGate, roleAllows } from "../components/RoleGate";
import { useAuthStore } from "../store/auth";
import { AdminLayout } from "./admin/AdminLayout";
// 7/30 按军规 §1 从本文件拆出去的两族 (本文件曾到 865 行, 红线 800)
import { AdminHome } from "./admin/AdminHome";
import { AdminQuotaEvents } from "./admin/QuotaEventsPage";
import { AccessPage } from "./admin/AccessPage";
import { AdvisoryPage } from "./admin/AdvisoryPage";
import { FactsPage } from "./admin/FactsPage";
import { ModelConfigPage } from "./admin/ModelConfigPage";
import { PerfPage } from "./admin/PerfPage";
import { QuotaConfigPage } from "./admin/QuotaConfigPage";
import { SystemPage } from "./admin/SystemPage";
import { UsersPage } from "./admin/UsersPage";
import { AuditPage } from "./AuditPage";
import { ManagerPage } from "./ManagerPage";
import { ROUTES, routePattern, toHref, type AdminPath } from "./admin/navConfig";

// 军规 §3 re-export 协议: 拆出去的符号在老文件顶部再导出一次, 不破老 caller。
export { AdminHome } from "./admin/AdminHome";
export { AdminQuotaEvents } from "./admin/QuotaEventsPage";

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

