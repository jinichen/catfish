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
import { GROUP_ORDER, ROUTES, toHref } from "./navConfig";

/** 当前路径命中哪一项.
 *
 * 最长前缀匹配, 不是逐个 startsWith —— /admin/quota 和 /admin/quota/events
 * 是前缀关系, 逐个试的话在 events 页上两项会同时高亮。
 * "/admin" 本身只在完全相等时命中, 否则它会匹配所有子路径。
 * (顶栏原本就栽在这上面: /admin 是 /admin/system 的前缀, 两个 tab 同时亮。)
 */
function activeHref(pathname: string, hrefs: string[]): string | null {
  let best: string | null = null;
  for (const h of hrefs) {
    const hit =
      h === "/admin"
        ? pathname === "/admin" || pathname === "/admin/"
        : pathname === h || pathname.startsWith(h + "/");
    if (hit && (best === null || h.length > best.length)) best = h;
  }
  return best;
}

export function AdminLayout({ children }: { children: ReactNode }) {
  const me = useAuthStore((s) => s.me);
  const { pathname } = useLocation();

  // 权限过滤跟目标页的 RoleGate 同源 —— 都读 navConfig 里那条 require,
  // 所以"菜单里有、点进去 403"在结构上不可能发生。
  const allowed = ROUTES.filter((r) =>
    me ? roleAllows(me.role, [...r.require]) : false,
  );

  const visible = GROUP_ORDER.map((title) => ({
    title,
    items: allowed.filter((r) => r.group === title),
  })).filter((g) => g.items.length > 0);

  const active = activeHref(
    pathname,
    allowed.map((r) => toHref(r.path)),
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
              const href = toHref(it.path);
              const isActive = active === href;
              return (
                <Link
                  key={href}
                  to={href}
                  style={{
                    display: "block",
                    padding: "5px 8px",
                    borderRadius: 6,
                    textDecoration: "none",
                    color: isActive ? "var(--accent)" : "var(--text)",
                    background: isActive ? "var(--bg-elev)" : "transparent",
                    fontWeight: isActive ? 600 : 400,
                  }}
                >
                  {it.label}
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
