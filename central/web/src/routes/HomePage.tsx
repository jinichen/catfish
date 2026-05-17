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
      {/* BL-CENTRAL-WEB-CONSOLIDATE (5/17 鸿波): Hero 改造 — 讲清"中央门户瘦, 边缘
          Companion 厚"的叙事. 防员工登进来 nav 看着稀觉得"啥也没". */}
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
            <span style={{ fontSize: 18 }}>鲶鱼中央门户</span>
            <span
              style={{
                fontSize: 12,
                color: "var(--text-muted)",
                fontWeight: 400,
              }}
            >
              · 跨员工管理 + 共享市场
            </span>
          </span>
        }
      >
        <div
          style={{ color: "var(--text)", fontSize: 14, lineHeight: 1.7 }}
        >
          <div style={{ marginBottom: "var(--space-3)" }}>
            欢迎 <b>{me.email.split("@")[0]}</b>. 中央门户只看 3 类事:
          </div>
          <ul
            style={{
              paddingLeft: 0,
              listStyle: "none",
              margin: 0,
              display: "flex",
              flexDirection: "column",
              gap: 4,
            }}
          >
            <li>📦 跨员工 / 跨部门 <b>资源市场</b> — Skills / MCP / 共享工具</li>
            {isManagerOrAdmin && (
              <li>👥📜 <b>部门管理 + 审计</b> — 本部门用量 / top 员工 / 历史调用</li>
            )}
            {isAdmin && (
              <li>⚙️ <b>Admin 后台</b> — 用户 / 配额 / billing / 部门 RBAC</li>
            )}
            {isSysadmin && (
              <li>🔐 <b>系统管理</b> — 服务状态 / 操作审计 / 危险操作</li>
            )}
          </ul>
          <div
            style={{
              marginTop: "var(--space-4)",
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
