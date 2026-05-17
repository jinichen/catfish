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
              欢迎, {me.email.split("@")[0]}
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
