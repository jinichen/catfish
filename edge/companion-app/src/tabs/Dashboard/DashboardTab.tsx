/** 仪表盘 tab —— 4 张卡片：身份 / 配额 / 模型清单 / Skills+MCP */

import IdentityCard from "./IdentityCard";
import QuotaCard from "./QuotaCard";
import CatalogCard from "./CatalogCard";
import SkillsMcpCard from "./SkillsMcpCard";

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
    </div>
  );
}
