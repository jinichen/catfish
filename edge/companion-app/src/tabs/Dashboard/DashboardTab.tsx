/** 仪表盘 tab — 5/10 BL-ARCH2 瘦身版 (鸿波架构反思).
 *
 * 历史:
 *   5/7  BL-D-DASH:  18 张卡分组折叠
 *   5/8  BL-FIX19~22: UI 抗溢出 / 全宽对齐
 *   5/9  BL-D2/D3:   加 SkillsHub + McpRegistry 浏览卡 (后又被砍)
 *   5/10 BL-ARCH1:   catfish-web 中央门户 ship
 *   5/10 BL-ARCH2:   ✂ 砍 7 张管理类卡, 留 14 张 "我的" 视角
 *
 * 砍掉 (挪去 catfish-web /admin / /audit / /manager / /skills / /mcp):
 *   ❌ McpRegistryCard         → /mcp        市场浏览
 *   ❌ SkillsHubCard           → /skills     市场浏览
 *   ❌ SkillAuditCard          → /admin      跨员工 skill 评分聚合
 *   ❌ AuditCard               → /audit      历史大查询 (跨员工)
 *   ❌ DepartmentQuotaCard     → /manager    部门 quota (manager+)
 *   ❌ DepartmentAuditCard     → /manager    部门 audit (manager+)
 *   ❌ AdminGlobalCard         → /admin      全公司聚合
 *
 * 留下 (14 张, 全 "我的" 视角, 离线友好):
 *   今日:    Proactive + Tasks
 *   我自己:  Identity + AgentPrefs
 *   我的画像:Relation + MemoryHistory + UserProfile + StyleFingerprint + Feedback
 *   服务:    Services + Quota + Catalog + SkillsMcp (我装的) + Curator
 *   学习:    Learning + SkillRevision (我提的改进)
 *
 * 视图分层:
 *   全员 14 张卡都看 (manager / admin / sysadmin 也走 web 看管理类).
 *   顶部 WebPortalLink banner 按 role 过滤 web 锚点.
 *
 * 注: 7 张被砍的卡 .tsx 文件保留在仓库 (Tab 不再 import), 给 ARCH3 / 回滚留路.
 */

import IdentityCard from "./IdentityCard";
import ServicesCard from "./ServicesCard";
import QuotaCard from "./QuotaCard";
import CatalogCard from "./CatalogCard";
import SkillsMcpCard from "./SkillsMcpCard";
import LearningCard from "./LearningCard";
import SkillRevisionCard from "./SkillRevisionCard";
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
import WebPortalLink from "./WebPortalLink";

export default function DashboardTab() {
  // BL-ARCH2 (5/10): role 不再决定 Dashboard 卡片, manager/admin 也走 web 看管理.
  // 顶部 WebPortalLink 按 role 显示锚点; useMe 在内部用, 这里不再分支.

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "1fr",
        // BL-FIX20 (5/8): 0 gap, 各 section 自带 marginTop, 跟新标题样式更协调
        gap: 0,
        // 全宽控制, 防过宽屏看着空 (1400px 内容居中, 留两侧空气)
        maxWidth: 1600,
        margin: "0 auto",
        width: "100%",
      }}
    >
      {/* BL-ARCH2 (5/10): 顶部 banner — "去中央门户 →" 按 role 显示锚点 */}
      <WebPortalLink />

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

      {/* 第四组: 服务 — gateway / quota / catalog / 我装的 skill+mcp / curator (默认收) */}
      <CollapsibleSection
        id="services"
        title="⚙️ 服务 / 配额"
        defaultCollapsed
        count={5}
      >
        <ServicesCard />
        <QuotaCard />
        <CatalogCard />
        {/* SkillsMcpCard = 我装的 skill / mcp 列表 (跟广场浏览不同, 留这里).
            广场: catfish-web /skills /mcp. */}
        <SkillsMcpCard />
        {/* 5/7 BL-CR: Curator 集成 — 老脚本自动整理 (hermes 0.12 自带) */}
        <CuratorCard />
      </CollapsibleSection>

      {/* 第五组: 学习 — 我的 learning + 我提的 skill 改进 (默认收).
          BL-ARCH2: SkillAuditCard (跨员工 skill 评分聚合) / AuditCard (历史大查询)
          已挪去 web /admin / /audit. */}
      <CollapsibleSection
        id="learn"
        title="📚 学习 / 改进"
        defaultCollapsed
        count={2}
      >
        <LearningCard />
        {/* BL-MM14 / MM15 (5/8): skill 改进提议 + 有效性跟踪 */}
        <SkillRevisionCard />
      </CollapsibleSection>
    </div>
  );
}
