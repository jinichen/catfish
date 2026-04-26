/** 仪表盘 tab —— Identity + 服务状态 + Quota + Catalog + Skills/MCP + Learning */

import IdentityCard from "./IdentityCard";
import ServicesCard from "./ServicesCard";
import QuotaCard from "./QuotaCard";
import CatalogCard from "./CatalogCard";
import SkillsMcpCard from "./SkillsMcpCard";
import LearningCard from "./LearningCard";

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
    </div>
  );
}
