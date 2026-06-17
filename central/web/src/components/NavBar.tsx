/** 顶部导航 (BL-ARCH1 5/10).
 *
 * 按 role 显示不同链接:
 *   - 全员: 我的 / Skills Hub / MCP 市场
 *   - manager+: + 部门
 *   - admin: + Admin 后台
 */

import { Link, useLocation } from "react-router-dom";

import { useAuthStore } from "../store/auth";
import { logout } from "../lib/auth";

export function NavBar() {
  const me = useAuthStore((s) => s.me);
  const location = useLocation();
  const isManagerOrAbove =
    me?.role === "manager" || me?.role === "admin" || me?.role === "sysadmin";
  const isAdminOrAbove = me?.role === "admin" || me?.role === "sysadmin";
  const isSysadmin = me?.role === "sysadmin";

  // BL-CENTRAL-WEB-CONSOLIDATE (5/17 鸿波): 5 项 nav 按权限阶梯排列, emoji 全统一.
  // 普通员工看到 📦 市场 (Skills + MCP 合一); manager+ 加 👥 部门 + 📜 审计;
  // admin+ 加 ⚙️ Admin; sysadmin 加 🔐 系统.
  //
  // 之前删的 "我的" / "📚 会话" / "📊 看板" 都是个人数据展示, 违
  // BL-CENTRAL-EDGE-BOUNDARY. 员工自查 → 桌面 Companion app.
  const links: Array<{ to: string; label: string; show: boolean }> = [
    { to: "/market", label: "📦 市场", show: true },
    { to: "/manager", label: "👥 部门", show: isManagerOrAbove },
    { to: "/audit", label: "📜 审计", show: isManagerOrAbove },
    { to: "/admin", label: "⚙️ Admin", show: isAdminOrAbove },
    { to: "/admin/system", label: "🔐 系统", show: isSysadmin },
  ];

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
          .map((l) => (
            <Link
              key={l.to}
              to={l.to}
              style={{
                color:
                  location.pathname.startsWith(l.to)
                    ? "var(--accent)"
                    : "var(--text)",
                fontWeight:
                  location.pathname.startsWith(l.to) ? 500 : "normal",
              }}
            >
              {l.label}
            </Link>
          ))}
      </div>
      {me && (
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
      )}
    </nav>
  );
}
