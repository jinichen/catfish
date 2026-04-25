/** 仪表盘 tab —— 4 张卡片 + 1 张跨行的 self-evolution 卡 */

import IdentityCard from "./IdentityCard";
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
      <QuotaCard />
      <CatalogCard />
      <SkillsMcpCard />
      <LearningCard />
    </div>
  );
}
