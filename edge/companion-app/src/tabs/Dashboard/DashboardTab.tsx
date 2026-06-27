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
import LocalSearchScopeCard from "./LocalSearchScopeCard"; // P3.5.126 (6/26 鸿波 catch): local_search 索引目录 UI
import QuotaCard from "./QuotaCard";
// P3.5.59 (6/22 鸿波 catch "无法知道性能状态"): 综合 LLM + tool perf 一站式
import PerfCard from "./components/PerfCard";
// BL-SESSION-CLEANUP-KILL (5/16 鸿波 '没意义砍了吧'): SessionCleanupCard 砍.
// 治标不治本 (删 13 个还有 291 个) + 客户 demo 负面信号 + 跟 Curator 重叠.
// .tsx 文件保留 (跟 ARCH2 砍 7 张卡同模式), 真要回滚改 1 行 import 就回.
// 未来根治: sidebar 加 FTS5 搜索框替代 (P2).
// import { SessionCleanupCard } from "./SessionCleanupCard";
import CatalogCard from "./CatalogCard";
import SkillsMcpCard from "./SkillsMcpCard";
// P3.3.18 (6/10): 部门 wiki publish 市场, 跟 SkillsMcpCard 并列 (manifesto 公理 2 例外)
import WikiHubCard from "./WikiHubCard";
// 6/2 BL-SKILLS-CARD-SPLIT (鸿波 6/2 凌晨拍 方案 C): MySkillsCard 显员工自己 RecMode
// 录的 + propose_skill 落地的 skill (~/.catfish/skills/), 含共享按钮占位. 主卡, 上.
// SkillsMcpCard 改成只显内置+装的, 次卡, 下. 配套 catfish 5 大卖点之 "员工自助生成
// + 共享" 真兑现 UI.
import MySkillsCard from "./MySkillsCard";
// P3.5.1 (6/15 鸿波 Dream Engine): 员工主动触发 long-term 蒸馏 (跟 picker model)
import DreamCard from "./DreamCard";
// P3.3.31 (6/12): 5/10 BL-ARCH2 砍后又加回 — 员工心智里"别人分享的 skill"
//   就应该在仪表盘"技能" section 找, 不是顶部 banner. SkillsHubCard 完整保留没 rm.
import SkillsHubCard from "./SkillsHubCard";
// BL-LEARN-SECTION-KILL (5/16): 整个"学习/改进" section 砍 (两张卡都对一般员工无效).
// - LearningCard: 4 个数字里 3 个是开发者维度 (tool_calls / ship_skill / token=0 bug),
//   只"对话 24 次"对员工有点用 — ROI 太低不值留卡
// - SkillRevisionCard: 99% 时间"暂时无待处理", 同 Curator 模式无内容
// backend (LearningAggregator / SkillRevision) 仍跑, 真要看走"控制台"tab.
// import LearningCard from "./LearningCard";
// import SkillRevisionCard from "./SkillRevisionCard";
// BL-COMPANION-EMAIL-DIGEST-REMOVE-FROM-DASHBOARD (5/18): EmailDigestCard 挪到独立
// 邮件 tab (📧 邮件) 成 single source of truth, dashboard 这张删防数据漂移.
// 源码 EmailDigestCard.tsx 留作 git 历史 / 万一回滚.
// import EmailDigestCard from "./EmailDigestCard";
// BL-COMPANION-DAILY-BRIEFING-MVP step1.5 (5/20 鸿波): BriefingCard 5/20 早上加在
// Dashboard 顶, 下午改成独立 "早安" tab (tabs/Briefing/BriefingTab.tsx). Dashboard
// 解耦"系统状态" (我的画像 / 服务 / 配额 / 设置) vs "今日要事" (邮件 / 日历 / 工作计划).
// BriefingCard.tsx 仍保留作可复用 component, 当前只 BriefingTab 引用.
// import BriefingCard from "./BriefingCard";
import ProactiveCard from "./ProactiveCard";
import CronJobsCard from "./CronJobsCard";  // P3.5.105 (6/25 鸿波 catch "定时任务跑没跑结果如何都看不到")
import AgentPrefsCard from "./AgentPrefsCard";
import ServerConfigCard from "./ServerConfigCard"; // P28 (6/5 鸿波): UI 改 gateway URL/token
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
// BL-TASKS-CARD-KILL (6/1 鸿波): TasksCard 从 Dashboard 删. 5 个用户场景全有
// 别处 UX 覆盖 — ChatSidebar ⌛ (5/30 在跑任务 affordance), macOS 系统通知
// (5/30 完成/失败), LLM 工具 catfish_task_list/_status/_retry (历史查询/重试).
// Dashboard 占黄金位是冗余. 文件留着 git history 备用, tasks_history_read
// Rust command 留着 (LLM 工具可能间接用).
// import TasksCard from "./TasksCard";
// BL-CURATOR-CARD-KILL (5/16): hermes 0.12 Curator daemon 透明性卡, 但价值低:
// 1. catfish 自家 skill 在 catfish/skills/ 物理隔离, Curator 不动
// 2. ~/.hermes/skills/ 员工基本不存东西, 99% 时间"无变化"
// 3. 唯一操作"关闭自动整理"员工不知道也不会用
// backend daemon 不动 (hermes 自家 housekeeping). 真要看状态去"控制台" tab.
// import CuratorCard from "./CuratorCard";
import CollapsibleSection from "./CollapsibleSection";
import WebPortalLink from "./WebPortalLink";
// BL-EMPLOYEE-PRIVACY-VERIFICATION (#77, 5/25): 员工自查"中央存了我啥 / 本机存了啥".
// 跟 #76 (CLI privacy-audit) / #79 (gateway /api/audit/me) / #78 (员工 doc) 配套.
// 单独 section 让员工一眼看到"我能自验"信号 — 透明性 = 信任卖点.
import PrivacyCard from "./PrivacyCard";
// 6/2 BL-DASHBOARD-DROP-RECORDINGS-CARD (鸿波 6/2 凌晨): RecordingsCard 整卡删.
// 前提是 #17 BL-RECMODE-AUTO-CLEAN-RAW 默认 skill 生成完自动清原料 → 录屏目录
// 99% 时间空 / 只剩 KB 级 meta + skill_draft, 卡 99% 空着 = UI noise. PrivacyCard
// 🟢 本机存储区加 1 行说明 (路径仍指向 ~/.catfish/recordings/, Finder/CLI 还能用).
// import RecordingsCard from "./RecordingsCard";  // 删 ← 历史: 5/25 #75 ship
// BL-WECHAT-CATFISH-BIND v1 (5/26 鸿波): WeChat ↔ catfish 员工 email 绑定状态卡.
// 跟隐私同 section — IM 平台用户 ↔ 真员工 隔离也是隐私红线 (防 100 个 WeChat
// 用户记忆串到一个虚拟员工身上).
import WeChatBindingCard from "./WeChatBindingCard";
// 6/8 BL-EMPLOYEE-SELF-SERVE A4 Phase 2: 数据外发记录独立卡 (filter / paginate /
// 导出 CSV). 跟 PrivacyCard 同 section, 因为它是"员工自查中央实际收到什么"的核心
// 落地, 是公理 2 (数据零出端) 的**可证明 enforcement** 层.
import OutboundLogCard from "./OutboundLogCard";
import AuditExportCard from "./AuditExportCard";  // P3.3.54 (6/12 鸿波): 审计员看的 xlsx 导出
import AuditViewCard from "./AuditViewCard";  // P3.3.55 (6/12 鸿波): 审计员现场看的 tab
import TodayDraftsCard from "./TodayDraftsCard";  // P3.3.62 (6/13 鸿波): advisor 起草 → Mail.app Drafts 链路
import { useUIStore } from "../../store/ui";  // P3.3.55 (6/12): auditViewEnabled 开关
// 6/8 BL-PRIVACY-SECTION-TABS: 隐私 section 内 3 卡 → 3 tabs (空间 +25%).
import SectionTabs from "./SectionTabs";

export default function DashboardTab() {
  // BL-ARCH2 (5/10): role 不再决定 Dashboard 卡片, manager/admin 也走 web 看管理.
  // 顶部 WebPortalLink 按 role 显示锚点; useMe 在内部用, 这里不再分支.
  // P3.3.55 (6/12 鸿波): 审计视图 tab 条件渲染 — PrivacyCard toggle 控
  const auditViewEnabled = useUIStore((s) => s.auditViewEnabled);

  return (
    <>
      {/* P3.5.120 (6/25 鸿波 catch "中央门户应固定 + B 路径重做架构"):
          WebPortalLink 真**移出 grid**, 真**.app-main 直接子全宽 sticky toolbar**.
          真**全宽 sticky 真**视觉**: 真**贴 tab 栏底 + 全 .app-main 内宽 covered**,
          真**0 漏出** (P3.5.118 真因: 它在 grid 1600 居中, 两侧空白透下方内容).
          真**inner content 真 maxWidth 1600 居中** 跟下方 7 个 section 节奏一致. */}
      <WebPortalLink />
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

      {/* 第一组: 今日 — 主动闲聊 (高频, 默认开, 顶部).
          5/18 BL-COMPANION-EMAIL-DIGEST-REMOVE-FROM-DASHBOARD: 邮件简报挪 📧 邮件 tab.
          5/20 BL-COMPANION-DAILY-BRIEFING-MVP (step1.5): 早安播报挪独立"早安" tab.
          6/1 BL-TASKS-CARD-KILL (鸿波): TasksCard 删 — ChatSidebar ⌛ + macOS
          通知 + LLM 工具已覆盖所有用户场景, Dashboard 上是冗余. */}
      <CollapsibleSection
        id="today"
        title="🔥 今日"
        count={2}
      >
        <ProactiveCard />
        {/* P3.5.105 (6/25 鸿波): 定时任务监控 — 跑没跑 / 失败信息 / 历史输出 */}
        <CronJobsCard />
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

      {/* P28 (6/5 鸿波): 服务器配置 — gateway URL + internal token.
          商用部署员工不会 vim ~/.catfish/*.yaml, 这卡 UI 改完写回 yaml +
          提示重启 hermes/Companion. 默认收 (大部分时间不动). */}
      <CollapsibleSection
        id="server"
        title="🌐 服务器配置"
        count={1}
        defaultCollapsed
      >
        <ServerConfigCard />
      </CollapsibleSection>

      {/* BL-EMPLOYEE-PRIVACY-VERIFICATION (#77, 5/25):
          隐私 + 本机数据主权一组. 默认开 — 透明性卖点要让员工立刻看到, 不藏折叠下.
          PrivacyCard: 中央存了啥 metadata + 本机数据归属表 (含录屏路径) + quota 进度.
          6/2 BL-DASHBOARD-DROP-RECORDINGS-CARD (鸿波): 删 RecordingsCard, 录屏说明
          1 行挪进 PrivacyCard. count 3→2. */}
      <CollapsibleSection
        id="privacy"
        title="🔒 隐私 / 本机数据"
        count={3}
      >
        {/* 6/8 BL-PRIVACY-SECTION-TABS (鸿波 6/8): 3 卡 → 3 tabs.
            空间利用率 +25%, 切换更清晰. PrivacyCard 默认 (透明卖点立刻看到).
            PrivacyCard 内 "↓ 我的数据外发记录" 按钮直接切到外发 tab (不再 scroll). */}
        <SectionTabs
          storageKey="privacy"
          defaultKey="status"
          tabs={[
            { key: "status", label: "🔒 隐私状态", render: () => <PrivacyCard /> },
            { key: "outbound", label: "📊 数据外发记录", render: () => <OutboundLogCard /> },
            // P3.3.54 (6/12 鸿波): 审计导出 — 给信合规 / 内审 / 党办手动交付
            { key: "audit-export", label: "📦 审计导出", render: () => <AuditExportCard /> },
            // P3.3.55 (6/12 鸿波): 审计视图 tab — 默认关 (PrivacyCard toggle 开)
            ...(auditViewEnabled
              ? [{ key: "audit-view", label: "🔍 审计视图", render: () => <AuditViewCard /> }]
              : []),
            { key: "wechat", label: "💬 微信接入", render: () => <WeChatBindingCard /> },
          ]}
        />
      </CollapsibleSection>

      {/* 第三组: 鲶鱼对你的认识 — 透明性 (默认开, 员工要能看清楚被学了什么)
          5/16 砍 MemoryHistoryCard + FeedbackSummaryCard 后 5 → 3. */}
      <CollapsibleSection
        id="rel"
        title="🐟 鲶鱼对你的认识"
        count={6}
      >
        {/* 6/8 BL-REL-SECTION-TABS (鸿波 6/8): 4 卡 → 4 tabs.
            P3.3.62 (6/13 鸿波): +1 tab "🤖 今日 AI 草稿" — 草稿是鲶鱼为你产出的物,
            主题贴 "鲶鱼对你的认识 / 产出". reply 类一键放 Mail.app Drafts, 闭三·沟通环.
            default = 小鲶对你的印象 (5 卡里"看时间感 + 整体关系"最直观).
            P3.5.1 (6/15 鸿波): +1 tab "🌙 Dream Engine" — 员工主动触发 long-term 蒸馏.
            跟现有 distilled_facts / employee_journal 同源 (画像基底), 同 section. */}
        <SectionTabs
          storageKey="rel"
          defaultKey="impression"
          tabs={[
            { key: "impression", label: "📈 小鲶对你的印象", render: () => <RelationCard /> },
            { key: "memory", label: "🧠 我的记忆", render: () => <HermesMemoryCard /> },
            { key: "profile", label: "👤 小鲶对你的画像", render: () => <UserProfileCard /> },
            { key: "style", label: "✍️ 你的文书风格", render: () => <StyleFingerprintCard /> },
            { key: "drafts", label: "🤖 今日 AI 草稿", render: () => <TodayDraftsCard /> },
            { key: "dream", label: "🌙 Dream Engine", render: () => <DreamCard /> },
          ]}
        />
      </CollapsibleSection>

      {/* 6/8 BL-SKILLS-SECTION-SPLIT (鸿波 6/8): 技能独立成一级 section.
          原跟服务/配额混 (5 tabs), 现拆 — skill 是员工核心创作物 (manifesto 公理 1
          员工主权落地), 应跟"服务/配额" 运维类区分. 默认开 (常看, 录完 → 共享 / 排错
          看是不是装上了). 2 tab: 我录的 / 已装的 — 边界明示来源 (上一段
          BL-SKILL-CARD-DISAMBIG 同步改了 2 卡 h3 + 来源 path badge). */}
      <CollapsibleSection
        id="skills"
        title="🎬 技能"
        count={2}
      >
        <SectionTabs
          storageKey="skills"
          defaultKey="my-skills"
          tabs={[
            // P3.3.24 (6/11): "我录的技能" 已名实不副 — 4 条 skill 里 RecMode 录 0,
            //   3 个部门 publish + 1 个 zip 装. 改"我的技能" — 表"个人本机管", 不绑生成方式.
            // P3.3.32 (6/12): 删 "(本机)" / "(可直接用)" — 两边都装本机都可用,
            //   括号误导. 区别只是"谁装的", 副文案在卡内说.
            // P3.3.35 (6/12): "已装技能库" → "技能库" — "已装" 字眼跟"技能库"重复,
            //   跟"我的技能" 对仗 ("我的" vs "库"=系统的)
            { key: "my-skills", label: "🧰 我的技能", render: () => <MySkillsCard /> },
            { key: "installed", label: "📦 技能库", render: () => <SkillsMcpCard /> },
            // P3.3.31 (6/12): 加回 SkillsHubCard — "别人分享的 skill" 在心智里
            //   就该在仪表盘技能 section 找, 不是顶部 banner 跳出去 catfish-web.
            //   原 5/10 BL-ARCH2 砍错了 (当时跟 manager 管理类一起挪走), 现在补.
            // P3.3.38 (6/12): "中央市场 (别人分享的)" → "内部技能分享" — 不再"市场"
            //   (商业感), 强调"内部分享". "部门 wiki" → "部门知识库" 中文化.
            { key: "hub", label: "🌐 内部技能分享", render: () => <SkillsHubCard /> },
            // P3.3.18 (6/10): 部门 wiki 跟技能在概念上都是"部门 share 资源", 同 section.
            { key: "wiki-hub", label: "📚 部门知识库", render: () => <WikiHubCard /> },
          ]}
        />
      </CollapsibleSection>

      {/* 第五组: 服务 / 配额 — 砍 my-skills/installed 2 tab 后留 3 tab.
          gateway / quota / catalog 都是运维 / 配额类, 跟"技能 (员工创作)"
          完全不同概念, 拆开. 默认收 (员工不常看, 排错才看). */}
      <CollapsibleSection
        id="services"
        title="⚙️ 服务 / 配额"
        defaultCollapsed
        count={5}
      >
        <SectionTabs
          storageKey="services"
          defaultKey="status"
          tabs={[
            { key: "status", label: "🌐 服务状态", render: () => <ServicesCard /> },
            { key: "quota", label: "📊 我的配额", render: () => <QuotaCard /> },
            { key: "catalog", label: "🧩 可用模型", render: () => <CatalogCard /> },
            // P3.5.59 (6/22 鸿波 catch "无法知道性能状态"): 综合 LLM perf
            // (gateway audit) + tool dispatch perf (tool-bridge audit) 一站式.
            // 数据 100% 本机 fs 直读, 不走网络.
            { key: "perf", label: "📈 性能统计", render: () => <PerfCard /> },
            // P3.5.126 (6/26 鸿波 catch "local_search 目录设置 UI 找不到"):
            // 直接 UI 加/删索引目录, 不用 vim ~/.catfish/search-scope.yaml.
            { key: "search-scope", label: "📂 搜索范围", render: () => <LocalSearchScopeCard /> },
          ]}
        />
        {/* <CuratorCard /> — 5/16 砍 (BL-CURATOR-CARD-KILL) */}
        {/* BL-SESSION-CLEANUP-KILL (5/16): SessionCleanupCard 已砍. */}
      </CollapsibleSection>

      {/* 第五组 "📚 学习/改进" 5/16 整组砍 (BL-LEARN-SECTION-KILL):
          LearningCard 数字 3/4 是开发者维度 (tool_calls / ship_skill / token=0 bug),
          SkillRevisionCard 99% 无待处理. backend 仍跑, 真要看走控制台 tab. */}
      </div>
    </>
  );
}
