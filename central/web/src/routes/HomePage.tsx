/** 首页 — 按 role 给入口推荐 (BL-ARCH1 5/10, P3 5/10 LOGO + sysadmin). */

import { Link } from "react-router-dom";

import { useAuthStore } from "../store/auth";
import { Card } from "../components/Card";

export function HomePage() {
  const me = useAuthStore((s) => s.me);
  if (!me) return null;

  // BL-ARCH1 P3 (5/10): role 检查含 sysadmin (跟 NavBar / RoleGate 一致).
  const isManagerOrAdmin =
    me.role === "manager" || me.role === "admin" || me.role === "sysadmin";

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
      {/* BL-HOMEPAGE-DEDUPE (5/17 鸿波): 删 Hero 卡 bullet 列表 — 跟下面 NavTile
          完全冗余 (顶部 nav + Hero bullet + NavTile 三处 echo 同 5 项, 3 倍 noise).
          NavTile 是真正的可点入口, 留. Hero 收紧成 welcome + Companion 提示 + role.
          视觉重点从"中央门户能做啥"挪到"个人功能去 Companion". */}
      <Card
        title={
          <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
            <img
              src="/catfish-logo.svg"
              alt=""
              width={24}
              height={24}
              style={{ display: "block" }}
            />
            <span style={{ fontSize: 18 }}>
              {/* P3.5.79+ (7/23 达华 POC catch): 加防御 · gateway /api/me 若返
                  email=undefined (identity 元数据缺 · A1-API-ME-FIX fallback 走
                  service token 且 sub 非 email 格式) 会 .split 崩全页. 优雅
                  degrade 到用 role / "用户" 兜底 · 别让员工看白屏. */}
              欢迎, {me.email?.split("@")[0] ?? me.email ?? me.role ?? "用户"}
            </span>
          </span>
        }
      >
        <div style={{ color: "var(--text)", fontSize: 14, lineHeight: 1.6 }}>
          <div
            style={{
              padding: "var(--space-3)",
              background: "var(--bg-elev)",
              border: "1px solid var(--border)",
              borderRadius: "var(--radius-sm)",
              fontSize: 13,
              color: "var(--text-muted)",
            }}
          >
            💡 <b>日常对话 / 我的配额 / 会话历史 / 画像 / 印象 / 个性化设置</b>{" "}
            请打开桌面 <b>Companion app</b>. 中央门户故意做薄, 员工数据不离本机.
          </div>
          <div
            style={{
              marginTop: "var(--space-3)",
              fontSize: 11,
              color: "var(--text-muted)",
            }}
          >
            你的角色 <b>{me.role}</b>
            {me.department && (
              <>
                {" · "}部门 <b>{me.department}</b>
              </>
            )}
            {me.managed_departments.length > 0 && (
              <>
                {" · "}管 <b>{me.managed_departments.join(", ")}</b>
              </>
            )}
          </div>
        </div>
      </Card>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))",
          gap: "var(--space-3)",
        }}
      >
        <NavTile
          to="/market"
          icon="📦"
          title="资源市场"
          desc="Skills (技能脚本) · MCP (连接器) · 全公司共享"
        />
        {/* 7/30: 原来这里是「部门 / 审计 / Admin / 系统」四块, 跟顶栏那 5 项
            一字不差地重复了一遍 —— 而且后三块其实都在 /admin 底下。合成一个
            入口, 里面去哪由左侧栏决定。 */}
        {isManagerOrAdmin && (
          <NavTile
            to="/admin"
            icon="⚙️"
            title="管理控制台"
            desc="模型 / 配额 / 用户 / 审计 / 系统状态"
          />
        )}
      </div>
    </div>
  );
}

function NavTile({
  to,
  icon,
  title,
  desc,
}: {
  to: string;
  icon: string;
  title: string;
  desc: string;
}) {
  return (
    <Link
      to={to}
      style={{
        background: "var(--bg-elev)",
        border: "1px solid var(--border)",
        borderRadius: "var(--radius-md)",
        padding: "var(--space-4)",
        color: "var(--text)",
        textDecoration: "none",
        transition: "border-color 0.1s",
        display: "flex",
        gap: "var(--space-3)",
        alignItems: "flex-start",
      }}
      onMouseEnter={(e) => (e.currentTarget.style.borderColor = "var(--accent)")}
      onMouseLeave={(e) => (e.currentTarget.style.borderColor = "var(--border)")}
    >
      <div style={{ fontSize: 24 }}>{icon}</div>
      <div>
        <div style={{ fontWeight: 500, marginBottom: 2 }}>{title}</div>
        <div style={{ fontSize: 12, color: "var(--text-muted)" }}>{desc}</div>
      </div>
    </Link>
  );
}
