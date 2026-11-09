/** 左侧分组导航 + 右侧内容列 —— 控制台 (/admin) 和市场 (/market) 共用 (11/09)。
 *
 * 原来只有控制台有 (admin/AdminLayout.tsx, 7/30), 市场是一张卡片里一排带 emoji 的横向页签,
 * 鸿波 11/09「市场的 UI 是不是应该和控制台的风格一致」。外壳抽到这里, 两边长得一样、行为一样:
 * 一屏布局 (整页不滚, 内容列里由页面决定哪块滚)、最长前缀高亮。
 *
 * 权限过滤不在这里做 —— 调用方传进来的就是这个人能看的项 (控制台按 navConfig 的 require 过滤)。
 */

import type { ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";

export interface SideNavItem {
  href: string;
  label: ReactNode;
  /** 右侧小数字 (待审核数之类); 0 / undefined 不显示 */
  count?: number;
}

export interface SideNavGroup {
  title: string;
  items: SideNavItem[];
}

/** 当前路径命中哪一项.
 *
 * 最长前缀匹配, 不是逐个 startsWith —— /admin/quota 和 /admin/quota/events
 * 是前缀关系, 逐个试的话在 events 页上两项会同时高亮。
 * exact 里的 href 只在完全相等时命中 (控制台的 "/admin" 本身, 否则它会匹配所有子路径)。
 */
export function activeHref(pathname: string, hrefs: string[], exact: string[] = []): string | null {
  let best: string | null = null;
  for (const h of hrefs) {
    const hit = exact.includes(h)
      ? pathname === h || pathname === `${h}/`
      : pathname === h || pathname.startsWith(`${h}/`);
    if (hit && (best === null || h.length > best.length)) best = h;
  }
  return best;
}

export function SideNavLayout({
  ariaLabel,
  groups,
  exact,
  children,
}: {
  ariaLabel: string;
  groups: SideNavGroup[];
  /** 只在完全相等时高亮的 href */
  exact?: string[];
  children: ReactNode;
}) {
  const { pathname } = useLocation();
  const visible = groups.filter((g) => g.items.length > 0);
  const active = activeHref(
    pathname,
    visible.flatMap((g) => g.items.map((i) => i.href)),
    exact,
  );

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "168px minmax(0, 1fr)",
        gap: "var(--space-4)",
        // 右侧内容列要拿到整屏高度, 才轮得到它内部决定"哪一块滚"。
        alignItems: "stretch",
        flex: 1,
        minHeight: 0,
      }}
    >
      <nav
        aria-label={ariaLabel}
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 2,
          fontSize: 13,
          // 侧栏自己比屏幕高时单独滚, 不去动右边。
          overflowY: "auto",
          minHeight: 0,
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
              const isActive = active === it.href;
              return (
                <Link
                  key={it.href}
                  to={it.href}
                  aria-current={isActive ? "page" : undefined}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    gap: 6,
                    padding: "5px 8px",
                    borderRadius: 6,
                    textDecoration: "none",
                    color: isActive ? "var(--accent)" : "var(--text)",
                    background: isActive ? "var(--bg-elev)" : "transparent",
                    fontWeight: isActive ? 600 : 400,
                  }}
                >
                  <span>{it.label}</span>
                  {!!it.count && (
                    <span
                      style={{
                        fontSize: 10,
                        lineHeight: 1.6,
                        padding: "0 5px",
                        borderRadius: 8,
                        background: "var(--status-warn)",
                        color: "#fff",
                        fontWeight: 600,
                      }}
                    >
                      {it.count}
                    </span>
                  )}
                </Link>
              );
            })}
          </div>
        ))}
      </nav>

      {/* 内容列。flex column + minHeight:0 = 给子页面一个高度确定的容器,
          页面把该滚的那一块标 `fill` 就行 (见 DataTable 的 Section)。
          minWidth: 0 —— 不写的话宽表会把整个 grid 撑破。 */}
      <div
        style={{
          minWidth: 0,
          minHeight: 0,
          display: "flex",
          flexDirection: "column",
        }}
      >
        {children}
      </div>
    </div>
  );
}
