/** 后台导航壳 (7/30) —— 三区布局的第一步.
 *
 * ## 在解决什么
 *
 * 改之前要到「模型配置」得点两次: 顶栏 Admin → /admin 页上的磁贴。而中间那一页
 * 除了当菜单没有别的职责。三个具体症状:
 *
 *   · **中间夹了一层纯菜单页。** /admin 上 11 个磁贴, 本质就是一份左侧菜单,
 *     只是被渲染成了一个页面。
 *   · **入口重复。** /admin/system 既是顶栏项又是 /admin 磁贴; SystemPage 里
 *     还有个「快捷入口」区又重复了三个。需要在多处放同样的入口, 通常就是
 *     缺一个常驻导航的信号。
 *   · **进了子页就不知道在哪。** 左边没有任何东西标明当前位置, 想去隔壁的
 *     配额规则得先退回 /admin。
 *
 * ## 这一步只做导航, 不动任何子页
 *
 * 有意把范围压到最小: 抽出这个壳, 把磁贴变成左栏, 子页原样塞进右区。
 * 信息架构重组 (合并观测类几项 / 权限影响可见性) 和内容密度改造是后面的步骤,
 * 那两步会动 URL 和页面内部, 风险量级不同, 不该跟这一步混在一起。
 *
 * ## 两个实现上的要点
 *
 * **侧栏必须 sticky。** App.tsx 的布局是 height:100vh 的 flex column, 其中
 * <main> 才是滚动容器 (overflowY:auto)。侧栏作为它的子元素, 不加 sticky 会
 * 随内容一起滚走 —— 那就退化成了"页面顶部的一段链接", 常驻导航的意义没了。
 *
 * **权限判定复用 roleAllows。** 侧栏显不显示某一项, 跟那一项对应页面的
 * RoleGate 用的是同一个函数。各写一套的话会出现"菜单里有、点进去 403",
 * 而这种不一致没有任何东西会拦住。
 */

import type { ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";

import { roleAllows } from "../../components/RoleGate";
import { useAuthStore } from "../../store/auth";
import type { Role } from "../../lib/me";

interface NavItem {
  to: string;
  label: string;
  /** 看得到这一项需要的最低权限. 必须跟目标页自己的 RoleGate 一致 —— 见文件头 */
  require: Role | Role[];
  /** 目标不在 /admin 下, 点了会离开这个壳 */
  external?: boolean;
}

interface NavGroup {
  title: string;
  items: NavItem[];
}

/** 分组按**任务**, 不按权限.
 *
 * 改之前的分法混了两个维度: 顶栏的「市场 / 部门 / 审计」是功能域, 而
 * 「Admin / 系统」是权限等级。所以「审计」在顶栏, 而同属观测的
 * 「Quota 历史日志 / 性能仪表 / Billing」却在 Admin 里 —— 只因为权限不同
 * 被拆到了两处。
 *
 * 这一步先把 /admin 下的项按任务归好组; 跨顶栏的合并留到第二步 (要动 URL)。
 * 「审计大查询」暂时以外链形式放在观测组里, 免得这一组看起来缺一块。
 *
 * 每一项的 require 都核对过对应页面的 RoleGate (7/30):
 *   AdvisoryPage / ModelConfigPage / QuotaConfigPage / SystemPage → sysadmin
 *   FactsPage → admin+sysadmin        AuditPage → manager+admin
 *   其余走 AdminPage 外层的 admin+sysadmin
 */
const GROUPS: NavGroup[] = [
  {
    title: "总览",
    items: [{ to: "/admin", label: "今日概况", require: ["admin", "sysadmin"] }],
  },
  {
    title: "接入",
    items: [
      { to: "/admin/models", label: "模型", require: ["sysadmin"] },
      { to: "/admin/quota", label: "配额", require: ["sysadmin"] },
      { to: "/admin/access", label: "部门权限", require: ["admin", "sysadmin"] },
    ],
  },
  {
    title: "人员",
    items: [{ to: "/admin/users", label: "用户", require: ["admin", "sysadmin"] }],
  },
  {
    title: "观测",
    items: [
      { to: "/admin/perf", label: "性能", require: ["admin", "sysadmin"] },
      { to: "/admin/billing", label: "成本", require: ["admin", "sysadmin"] },
      {
        to: "/admin/quota/events",
        label: "配额日志",
        require: ["admin", "sysadmin"],
      },
      {
        to: "/audit",
        label: "用量审计",
        require: ["manager", "admin"],
        external: true,
      },
    ],
  },
  {
    title: "系统",
    items: [
      { to: "/admin/system", label: "服务状态", require: ["sysadmin"] },
      { to: "/admin/facts", label: "政策同步", require: ["admin", "sysadmin"] },
      { to: "/admin/advisory", label: "公告发布", require: ["sysadmin"] },
    ],
  },
];

/** 当前路径命中哪一项.
 *
 * 用最长前缀匹配而不是 startsWith 逐个试 —— /admin/quota 和
 * /admin/quota/events 是前缀关系, 逐个试的话在 events 页上两项会同时高亮。
 * /admin 本身只在完全相等时命中, 否则它会匹配所有子路径。
 */
function activeTo(pathname: string, all: NavItem[]): string | null {
  let best: string | null = null;
  for (const it of all) {
    const hit =
      it.to === "/admin"
        ? pathname === "/admin" || pathname === "/admin/"
        : pathname === it.to || pathname.startsWith(it.to + "/");
    if (hit && (best === null || it.to.length > best.length)) best = it.to;
  }
  return best;
}

export function AdminLayout({ children }: { children: ReactNode }) {
  const me = useAuthStore((s) => s.me);
  const { pathname } = useLocation();

  const visible = GROUPS.map((g) => ({
    ...g,
    items: g.items.filter((it) => (me ? roleAllows(me.role, it.require) : false)),
  })).filter((g) => g.items.length > 0);

  const active = activeTo(
    pathname,
    visible.flatMap((g) => g.items),
  );

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "168px minmax(0, 1fr)",
        gap: "var(--space-4)",
        alignItems: "start",
      }}
    >
      <nav
        aria-label="后台导航"
        style={{
          // 见文件头: <main> 才是滚动容器, 不 sticky 侧栏会随内容滚走
          position: "sticky",
          top: 0,
          display: "flex",
          flexDirection: "column",
          gap: 2,
          fontSize: 13,
        }}
      >
        {visible.map((g) => (
          <div key={g.title} style={{ marginBottom: 10 }}>
            <div
              style={{
                fontSize: 11,
                color: "var(--text-muted)",
                padding: "4px 8px",
                letterSpacing: 0.3,
              }}
            >
              {g.title}
            </div>
            {g.items.map((it) => {
              const isActive = active === it.to;
              return (
                <Link
                  key={it.to}
                  to={it.to}
                  style={{
                    display: "block",
                    padding: "5px 8px",
                    borderRadius: 6,
                    textDecoration: "none",
                    color: isActive ? "var(--accent, #0d9488)" : "var(--text)",
                    background: isActive ? "var(--bg-elev)" : "transparent",
                    fontWeight: isActive ? 600 : 400,
                  }}
                >
                  {it.label}
                  {it.external ? (
                    <span
                      style={{ marginLeft: 4, fontSize: 10, color: "var(--text-muted)" }}
                      title="不在后台内, 点了会离开"
                    >
                      ↗
                    </span>
                  ) : null}
                </Link>
              );
            })}
          </div>
        ))}
      </nav>

      <div style={{ minWidth: 0 }}>{children}</div>
    </div>
  );
}
