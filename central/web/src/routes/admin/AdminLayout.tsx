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
 * **整个后台是一屏, 滚动交给数据区** (8/1 改)。这个壳自己 flex:1 + minHeight:0
 * 占满 <main>, 右侧内容列是 flex column, 页面把该滚的那一块标成 `fill`。
 *
 * 原来是整个 <main> 滚: 一翻页, 页面标题、筛选条、分页器全跟着滚出屏幕 ——
 * 而这三样恰恰是看日志时最需要一直在手边的。侧栏当时靠 `position: sticky`
 * 单独钉住, 那只是给一个本不该滚的东西打的补丁。现在没有外层滚动了,
 * sticky 也就不需要了。
 *
 * **权限判定复用 roleAllows。** 侧栏显不显示某一项, 跟那一项对应页面的
 * RoleGate 用的是同一个函数。各写一套的话会出现"菜单里有、点进去 403",
 * 而这种不一致没有任何东西会拦住。
 */

import type { ReactNode } from "react";

import { roleAllows } from "../../components/RoleGate";
import { SideNavLayout } from "../../components/SideNav";
import { useAuthStore } from "../../store/auth";
import { GROUP_ORDER, ROUTES, toHref } from "./navConfig";

// 11/09: 导航壳本身 (分组侧栏 + 内容列 + 最长前缀高亮) 抽到 components/SideNav.tsx,
// 市场 (/market) 也用它 —— 两边长得一样。这里只剩控制台自己的事: 按权限挑出能看的项。
// "/admin" 本身只在完全相等时高亮, 否则它是所有子路径的前缀 (顶栏原本就栽在这上面)。
export function AdminLayout({ children }: { children: ReactNode }) {
  const me = useAuthStore((s) => s.me);

  // 权限过滤跟目标页的 RoleGate 同源 —— 都读 navConfig 里那条 require,
  // 所以"菜单里有、点进去 403"在结构上不可能发生。
  const allowed = ROUTES.filter((r) =>
    me ? roleAllows(me.role, [...r.require]) : false,
  );

  const groups = GROUP_ORDER.map((title) => ({
    title,
    items: allowed
      .filter((r) => r.group === title)
      .map((r) => ({ href: toHref(r.path), label: r.label })),
  }));

  return (
    <SideNavLayout ariaLabel="后台导航" groups={groups} exact={["/admin"]}>
      {children}
    </SideNavLayout>
  );
}
