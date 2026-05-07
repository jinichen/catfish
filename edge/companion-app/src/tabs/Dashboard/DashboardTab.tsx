/** 仪表盘 tab — 5/7 优化分组折叠 (鸿波反馈"内容太多").
 *
 * 18 张卡分 5-7 组:
 *   "今日" (开): Proactive + Tasks
 *   "我自己" (开): Identity + AgentPrefs
 *   "鲶鱼对你的认识" (开): Relation + Memory + UserProfile + StyleFingerprint + Feedback
 *   "服务" (收): Services + Quota + Catalog + SkillsMcp
 *   "审计/学习" (收): Learning + SkillAudit + Audit
 *   "部门管理" (manager/admin only, 开): DepartmentQuota + DepartmentAudit
 *   "全局" (admin only, 收): AdminGlobal
 *
 * localStorage 记员工偏好.
 *
 * 视图分层 (跟 RBAC 配合):
 *   employee:  前 5 组
 *   manager:   employee + 部门组
 *   admin:     manager + 全局组
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
import MemoryHistoryCard from "./MemoryHistoryCard";
import FeedbackSummaryCard from "./FeedbackSummaryCard";
import UserProfileCard from "./UserProfileCard";
import StyleFingerprintCard from "./StyleFingerprintCard";
import TasksCard from "./TasksCard";
import CuratorCard from "./CuratorCard";
import CollapsibleSection from "./CollapsibleSection";
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
        gridTemplateColumns: "1fr",
        gap: "var(--space-2)",
      }}
    >
      {/* 第一组: 今日 — 主动闲聊 + 后台任务 (高频, 默认开, 顶部) */}
      <CollapsibleSection
        id="today"
        title="🔥 今日"
        count={2}
      >
        <ProactiveCard />
        <TasksCard />
      </CollapsibleSection>

      {/* 第二组: 我自己 — 身份 + 鲶鱼名/人设 (默认开) */}
      <CollapsibleSection
        id="me"
        title="👤 我自己"
        count={2}
      >
        <IdentityCard />
        <AgentPrefsCard />
      </CollapsibleSection>

      {/* 第三组: 鲶鱼对你的认识 — 透明性 (默认开, 员工要能看清楚被学了什么) */}
      <CollapsibleSection
        id="rel"
        title="🐟 鲶鱼对你的认识"
        count={5}
      >
        <RelationCard />
        <MemoryHistoryCard />
        <UserProfileCard />
        <StyleFingerprintCard />
        <FeedbackSummaryCard />
      </CollapsibleSection>

      {/* 第四组: 服务 — gateway / quota / catalog / skills (默认收, 不常看) */}
      <CollapsibleSection
        id="services"
        title="⚙️ 服务 / 配额"
        defaultCollapsed
        count={4}
      >
        <ServicesCard />
        <QuotaCard />
        <CatalogCard />
        <SkillsMcpCard />
        {/* 5/7 BL-CR: Curator 集成 — 老脚本自动整理 (hermes 0.12 自带) */}
        <CuratorCard />
      </CollapsibleSection>

      {/* 第五组: 审计 / 学习 — 历史 + skill audit + tool audit (默认收) */}
      <CollapsibleSection
        id="audit"
        title="📜 审计 / 学习"
        defaultCollapsed
        count={3}
      >
        <LearningCard />
        <SkillAuditCard />
        <AuditCard />
      </CollapsibleSection>

      {/* 第六组: 部门管理 — manager/admin only (默认开, 进 dashboard 是为了管这个) */}
      {isManagerOrAdmin && managedDepts.length > 0 && (
        <CollapsibleSection
          id="dept"
          title="🏢 部门管理"
          count={managedDepts.length * 2}
        >
          {managedDepts.map((dept) => (
            <DepartmentQuotaCard key={`q-${dept}`} department={dept} />
          ))}
          {managedDepts.map((dept) => (
            <DepartmentAuditCard key={`a-${dept}`} department={dept} />
          ))}
        </CollapsibleSection>
      )}
      {isManagerOrAdmin && managedDepts.length === 0 && (
        <ManagerNoDeptHint role={role} />
      )}

      {/* 第七组: 全局 — admin only (默认收) */}
      {role === "admin" && (
        <CollapsibleSection
          id="admin"
          title="🌐 全局聚合"
          defaultCollapsed
          count={1}
        >
          <AdminGlobalCard />
        </CollapsibleSection>
      )}
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
