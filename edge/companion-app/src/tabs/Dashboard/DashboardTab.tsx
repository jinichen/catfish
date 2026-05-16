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

// BL-IDENTITY-CARD-KILL (5/16): IdentityCard 6 项全只读纯诊断 (员工名 / SOUL symlink 路径 /
// 活动会话 hash / 鲶鱼版本号 / 皮肤 / 默认模型), 员工读完无操作可做, 是给开发者 debug 看的.
// 砍卡, "我自己" section 整合成只剩 AgentPrefsCard. 版本号挪到 AgentPrefsCard 底部小字.
// 诊断信息员工真需要时去"控制台"tab 看, 不污染 Dashboard.
// import IdentityCard from "./IdentityCard";
import ServicesCard from "./ServicesCard";
import QuotaCard from "./QuotaCard";
// BL-SESSION-CLEANUP-KILL (5/16 鸿波 '没意义砍了吧'): SessionCleanupCard 砍.
// 治标不治本 (删 13 个还有 291 个) + 客户 demo 负面信号 + 跟 Curator 重叠.
// .tsx 文件保留 (跟 ARCH2 砍 7 张卡同模式), 真要回滚改 1 行 import 就回.
// 未来根治: sidebar 加 FTS5 搜索框替代 (P2).
// import { SessionCleanupCard } from "./SessionCleanupCard";
import CatalogCard from "./CatalogCard";
import SkillsMcpCard from "./SkillsMcpCard";
// BL-LEARN-SECTION-KILL (5/16): 整个"学习/改进" section 砍 (两张卡都对一般员工无效).
// - LearningCard: 4 个数字里 3 个是开发者维度 (tool_calls / ship_skill / token=0 bug),
//   只"对话 24 次"对员工有点用 — ROI 太低不值留卡
// - SkillRevisionCard: 99% 时间"暂时无待处理", 同 Curator 模式无内容
// backend (LearningAggregator / SkillRevision) 仍跑, 真要看走"控制台"tab.
// import LearningCard from "./LearningCard";
// import SkillRevisionCard from "./SkillRevisionCard";
import ProactiveCard from "./ProactiveCard";
import AgentPrefsCard from "./AgentPrefsCard";
import RelationCard from "./RelationCard";
import HermesMemoryCard from "./HermesMemoryCard";  // BL-DASHBOARD-HERMES-MEMORY-CARD (5/16)
// BL-MEMORY-HISTORY-KILL (5/16): catfish_remember 5/16 A 黑名单后, session_facts.json
// 不再被写, 这张卡数据已冻结 + 容易误导员工以为"小鲶还记着这些". 砍卡, 让员工知道
// 真活的 fact 存储是 hermes memory (~/.hermes/memories/USER.md + MEMORY.md).
// 关联: BL-MEMORY-CATFISH-REMEMBER-BLACKLIST, BL-MEMORY-BRIDGE-STORE, SOUL Memory 写入纪律段.
// import MemoryHistoryCard from "./MemoryHistoryCard";
// BL-FEEDBACK-CARD-KILL (5/16): FeedbackSummaryCard 是"透明性卡"但价值低 —
// 反馈生效路径在 backend (inject_feedback provider 注入 system prompt 让 LLM 知道员工
// 不喜欢啥), 员工不需要看见这个机制 (像看到自己脑子里的潜意识似的, 反而怪).
// 单条不能撤回 / 改, 只有"清空反馈"按钮, 操作性也差.
// 入口 (ChatMessage 每条下方 👍👎✏️改 按钮) 保留, 反馈仍生效, 只砍 Dashboard 展示.
// import FeedbackSummaryCard from "./FeedbackSummaryCard";
import UserProfileCard from "./UserProfileCard";
import StyleFingerprintCard from "./StyleFingerprintCard";
import TasksCard from "./TasksCard";
// BL-CURATOR-CARD-KILL (5/16): hermes 0.12 Curator daemon 透明性卡, 但价值低:
// 1. catfish 自家 skill 在 catfish/skills/ 物理隔离, Curator 不动
// 2. ~/.hermes/skills/ 员工基本不存东西, 99% 时间"无变化"
// 3. 唯一操作"关闭自动整理"员工不知道也不会用
// backend daemon 不动 (hermes 自家 housekeeping). 真要看状态去"控制台" tab.
// import CuratorCard from "./CuratorCard";
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

      {/* 第二组: 小鲶设置 — 鲶鱼名 / 人设 / 桌宠. 5/16 砍 IdentityCard (BL-IDENTITY-CARD-KILL)
          整合后只剩 1 卡, section 标题从"我自己"改"小鲶设置"消歧义 (原"我自己"模棱两可
          — 讲员工还是讲鲶鱼). */}
      <CollapsibleSection
        id="me"
        title="🐟 小鲶设置"
        count={1}
      >
        <AgentPrefsCard />
      </CollapsibleSection>

      {/* 第三组: 鲶鱼对你的认识 — 透明性 (默认开, 员工要能看清楚被学了什么)
          5/16 砍 MemoryHistoryCard + FeedbackSummaryCard 后 5 → 3. */}
      <CollapsibleSection
        id="rel"
        title="🐟 鲶鱼对你的认识"
        count={4}
      >
        <RelationCard />
        <HermesMemoryCard />  {/* BL-DASHBOARD-HERMES-MEMORY-CARD (5/16): hermes 真活 memory */}
        {/* <MemoryHistoryCard /> — 5/16 砍 (BL-MEMORY-HISTORY-KILL) */}
        <UserProfileCard />
        <StyleFingerprintCard />
        {/* <FeedbackSummaryCard /> — 5/16 砍 (BL-FEEDBACK-CARD-KILL) */}
      </CollapsibleSection>

      {/* 第四组: 服务 — gateway / quota / catalog / 我装的 skill+mcp / curator (默认收) */}
      <CollapsibleSection
        id="services"
        title="⚙️ 服务 / 配额"
        defaultCollapsed
        count={4}
      >
        <ServicesCard />
        <QuotaCard />
        <CatalogCard />
        {/* SkillsMcpCard = 我装的 skill / mcp 列表 (跟广场浏览不同, 留这里).
            广场: catfish-web /skills /mcp. */}
        <SkillsMcpCard />
        {/* <CuratorCard /> — 5/16 砍 (BL-CURATOR-CARD-KILL), 见 import 段注释 */}
        {/* BL-SESSION-CLEANUP-KILL (5/16): SessionCleanupCard 已砍.
            原因: 治标不治本 / 跟 Curator 重叠 / 客户 demo 负面信号.
            未来: sidebar 加 FTS5 搜索框替代 (P2). */}
      </CollapsibleSection>

      {/* 第五组 "📚 学习/改进" 5/16 整组砍 (BL-LEARN-SECTION-KILL):
          LearningCard 数字 3/4 是开发者维度 (tool_calls / ship_skill / token=0 bug),
          SkillRevisionCard 99% 无待处理. backend 仍跑, 真要看走控制台 tab. */}
    </div>
  );
}
