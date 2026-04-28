/** 仪表盘 tab —— Identity + 服务状态 + Quota + Catalog + Skills/MCP + Learning + Audit */

import IdentityCard from "./IdentityCard";
import ServicesCard from "./ServicesCard";
import QuotaCard from "./QuotaCard";
import CatalogCard from "./CatalogCard";
import SkillsMcpCard from "./SkillsMcpCard";
import LearningCard from "./LearningCard";
import AuditCard from "./AuditCard";

export default function DashboardTab() {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(2, 1fr)",
        gap: "var(--space-4)",
      }}
    >
      <IdentityCard />
      <ServicesCard />
      <QuotaCard />
      <CatalogCard />
      <SkillsMcpCard />
      <LearningCard />
      <AuditCard />
    </div>
  );
}
