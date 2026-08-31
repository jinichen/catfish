/** 顶部导航 (BL-ARCH1 5/10, 7/30 收敛到 2 项).
 *
 * 全员看到「市场」, manager+ 多一个「控制台」。
 *
 * 7/30 之前是 5 项: 市场 / 部门 / 审计 / Admin / 系统。问题不在数量, 在于
 * 后 4 项其实是同一件事的 4 个入口 —— 都是管理动作, 而且相互嵌套
 * (系统在 Admin 里面, /admin 是 /admin/system 的前缀, 于是两个 tab 同时高亮)。
 * 并进 /admin 之后, 顶栏只回答"我在门户的哪一块", 具体去哪一页交给左侧栏。
 * 顶栏和侧栏各管一层, 不再互相重复。
 */

import { useState } from "react";
import { Link, useLocation } from "react-router-dom";

import { useAuthStore } from "../store/auth";
import { logout } from "../lib/auth";
import { PasswordDialog } from "../routes/PasswordPage";

export function NavBar() {
  const me = useAuthStore((s) => s.me);
  const location = useLocation();
  const [passwordOpen, setPasswordOpen] = useState(false);
  const isManagerOrAbove =
    me?.role === "manager" || me?.role === "admin" || me?.role === "sysadmin";

  const links: Array<{ to: string; label: string; show: boolean }> = [
    { to: "/market", label: "📦 市场", show: true },
    { to: "/admin", label: "⚙️ 控制台", show: isManagerOrAbove },
  ];

  // 最长前缀命中 —— 见下面 map 里的说明。这里算一次, 免得每项各判一遍。
  const activeTab = links
    .filter((l) => l.show)
    .filter(
      (l) =>
        location.pathname === l.to || location.pathname.startsWith(l.to + "/"),
    )
    .reduce<string | null>(
      (best, l) => (best === null || l.to.length > best.length ? l.to : best),
      null,
    );

  return (
    <nav
      style={{
        // P3.5.26.3 (6/17 鸿波"页面固定一屏高度"): App.tsx 真用 flex column + main
        // overflow 让 NavBar 自然 flex: 0 stay top, 不需要 sticky. 整页 viewport
        // 1 屏, scrollbar 只在 main 内.
        background: "var(--bg-elev)",
        borderBottom: "1px solid var(--border)",
        padding: "var(--space-3) var(--space-4)",
        display: "flex",
        alignItems: "center",
        gap: "var(--space-4)",
      }}
    >
      {/* BL-ARCH1 P3 (5/10): LOGO 跨端统一 — 跟 Companion 同 catfish-logo.svg
          (鸿波: "LOGO 要统一, 现在这个页面 LOGO 不对"). 之前是 🐟 emoji 跟
          Companion / Dock 不一致. catfish-web/public/catfish-logo.svg 跟
          edge/companion-app/public/ 同 source, 改设计两边同步 (P4 可抽 brand 包). */}
      <Link
        to="/"
        style={{
          fontWeight: 600,
          fontSize: 16,
          color: "var(--text)",
          display: "flex",
          alignItems: "center",
          gap: 8,
          textDecoration: "none",
        }}
      >
        <img
          src="/catfish-logo.svg"
          alt=""
          width={24}
          height={24}
          style={{ display: "block" }}
        />
        鲶鱼 · 中央门户
      </Link>
      <div style={{ display: "flex", gap: "var(--space-3)", flex: 1 }}>
        {links
          .filter((l) => l.show)
          .map((l) => {
            // 7/30: 原来是逐个 startsWith, 于是在 /admin/system 上「Admin」和
            // 「系统」**同时高亮** —— /admin 是 /admin/system 的前缀。
            // 改成最长前缀命中: 只有匹配得最具体的那一项算当前位置。
            const isActive = activeTab === l.to;
            return (
              <Link
                key={l.to}
                to={l.to}
                style={{
                  color: isActive ? "var(--accent)" : "var(--text)",
                  fontWeight: isActive ? 500 : "normal",
                }}
              >
                {l.label}
              </Link>
            );
          })}
      </div>
      {me && (
        <>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: "var(--space-2)",
              fontSize: 12,
              color: "var(--text-muted)",
            }}
          >
            <span>
              {me.email}
              {me.role !== "employee" && (
                <span
                  style={{
                    marginLeft: "var(--space-1)",
                    background: "var(--accent)",
                    color: "white",
                    padding: "1px 6px",
                    borderRadius: "var(--radius-sm)",
                    fontSize: 10,
                  }}
                >
                  {me.role}
                </span>
              )}
            </span>
            <button
              type="button"
              onClick={() => setPasswordOpen(true)}
              style={{
                border: 0,
                padding: 0,
                background: "transparent",
                color: "var(--accent)",
                cursor: "pointer",
                fontSize: 12,
              }}
            >
              修改密码
            </button>
            <button
              type="button"
              onClick={() => void logout()}
              style={{
                background: "transparent",
                border: "1px solid var(--border)",
                borderRadius: "var(--radius-sm)",
                padding: "4px 10px",
                cursor: "pointer",
                fontSize: 12,
              }}
            >
              退登
            </button>
          </div>
          {(passwordOpen || me.must_change_password) && (
            <PasswordDialog
              forced={me.must_change_password}
              onClose={() => setPasswordOpen(false)}
            />
          )}
        </>
      )}
    </nav>
  );
}
