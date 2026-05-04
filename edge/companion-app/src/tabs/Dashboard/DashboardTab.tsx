/** 仪表盘 tab — 按角色 conditional render (五一 sprint 5/2 RBAC).
 *
 * 视图分层:
 *   employee:  Identity + Services + Quota + Catalog + Skills + Learning + SkillAudit + Audit
 *   manager:   employee 全部 + 每个 managed_department × (DepartmentQuota + DepartmentAudit)
 *   admin:     manager 视图 + (将来) 全员 / 全模型 / 全部门聚合卡 (P2)
 */

import IdentityCard from "./IdentityCard";
import ServicesCard from "./ServicesCard";
import QuotaCard from "./QuotaCard";
import CatalogCard from "./CatalogCard";
import SkillsMcpCard from "./SkillsMcpCard";
import LearningCard from "./LearningCard";
import AuditCard from "./AuditCard";
import SkillAuditCard from "./SkillAuditCard";
import DepartmentQuotaCard from "./DepartmentQuotaCard";
import DepartmentAuditCard from "./DepartmentAuditCard";
import AdminGlobalCard from "./AdminGlobalCard";
import ProactiveCard from "./ProactiveCard";
import AgentPrefsCard from "./AgentPrefsCard";
import RelationCard from "./RelationCard";
import { useMe } from "../../hooks/useMe";

export default function DashboardTab() {
  const { me } = useMe();

  // role 没拿到 (loading / 鉴权失败) → 默认按 employee 渲染, 不卡 UI
  const role = me?.role ?? "employee";
  const managedDepts = me?.managed_departments ?? [];
  const isManagerOrAdmin = role === "manager" || role === "admin";

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(2, 1fr)",
        gap: "var(--space-4)",
      }}
    >
      {/* 主动闲聊 BL-E13: 醒目放第一个, 鼓励员工先聊一句再做事 */}
      <ProactiveCard />

      {/* 全员可见 — 个人维度 */}
      <IdentityCard />
      {/* BL-E11: 改鲶鱼名 + 人设, Onboarding 走完后想改也来这 */}
      <AgentPrefsCard />
      {/* BL-E16: 鲶鱼对你的印象 — 透明可删, 防 creepy */}
      <RelationCard />
      <ServicesCard />
      <QuotaCard />
      <CatalogCard />
      <SkillsMcpCard />
      <LearningCard />
      <SkillAuditCard />
      <AuditCard />

      {/* 经理 / 管理员 — 部门维度 */}
      {isManagerOrAdmin && managedDepts.length === 0 && (
        <ManagerNoDeptHint role={role} />
      )}
      {isManagerOrAdmin &&
        managedDepts.map((dept) => (
          <DepartmentQuotaCard key={`q-${dept}`} department={dept} />
        ))}
      {isManagerOrAdmin &&
        managedDepts.map((dept) => (
          <DepartmentAuditCard key={`a-${dept}`} department={dept} />
        ))}

      {/* 管理员 — 全局聚合 (admin only) */}
      {role === "admin" && <AdminGlobalCard />}
    </div>
  );
}

function ManagerNoDeptHint({ role }: { role: string }) {
  return (
    <div
      style={{
        gridColumn: "1 / -1",
        background: "var(--catfish-bg-elevated)",
        border: "1px dashed var(--catfish-border)",
        borderRadius: "var(--radius-md)",
        padding: "var(--space-4)",
        fontSize: 13,
        color: "var(--catfish-text-muted)",
      }}
    >
      你是 <strong>{role}</strong>, 但 <code>managed_departments</code> 为空 —
      联系 IT 在 catfish-identity 用户配置里加上你管的部门, 这里就会出"部门 quota / 部门审计"卡片.
    </div>
  );
}
