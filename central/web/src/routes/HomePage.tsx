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
  const isAdmin = me.role === "admin" || me.role === "sysadmin";
  const isSysadmin = me.role === "sysadmin";

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
      <Card
        title={
          // BL-ARCH1 P3 (5/10): 标题加 logo, 跟 NavBar 一致, 不再 emoji 拼字符串
          <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
            <img
              src="/catfish-logo.svg"
              alt=""
              width={20}
              height={20}
              style={{ display: "block" }}
            />
            欢迎, {me.email.split("@")[0]}
          </span>
        }
      >
        <div style={{ color: "var(--text-muted)", fontSize: 13 }}>
          <p>
            这是<b>鲶鱼中央门户</b> — 只看跨员工 / 跨部门 / 管理类功能.
            日常对话 / 我自己的配额 / 会话历史 / 个性化数据请在桌面 Companion app 里看.
          </p>
          <p>
            你的角色: <b>{me.role}</b>
            {me.department && (
              <>
                {" · "}部门: <b>{me.department}</b>
              </>
            )}
            {me.managed_departments.length > 0 && (
              <>
                {" · "}管的部门: <b>{me.managed_departments.join(", ")}</b>
              </>
            )}
          </p>
        </div>
      </Card>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))",
          gap: "var(--space-3)",
        }}
      >
        {/* BL-CENTRAL-WEB-PURGE-MEPAGE (5/17): "我的概览" tile 砍, /me 整页废.
            员工自查身份 / 配额 / 画像 / skill 列表 → 桌面 Companion app. */}
        <NavTile
          to="/skills"
          icon="🛠️"
          title="Skills Hub"
          desc="全公司 skill 市场 · 浏览 / publish / 评分"
        />
        <NavTile
          to="/mcp"
          icon="🔌"
          title="MCP 连接器市场"
          desc="Jira / GitLab / 飞书 等 60+ 工具"
        />
        {isManagerOrAdmin && (
          <NavTile
            to="/manager"
            icon="👥"
            title="部门视图"
            desc="本部门用量 / 员工 top / 调配额"
          />
        )}
        {isManagerOrAdmin && (
          <NavTile
            to="/audit"
            icon="📜"
            title="审计查询"
            desc="历史调用 · 按用户 / 模型 / 时间筛"
          />
        )}
        {isAdmin && (
          <NavTile
            to="/admin"
            icon="⚙️"
            title="Admin 后台"
            desc="用户 / 配额 / billing / dev_users"
          />
        )}
        {isSysadmin && (
          <NavTile
            to="/admin/system"
            icon="🔐"
            title="系统管理"
            desc="服务状态 / 操作审计 / 危险操作"
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
