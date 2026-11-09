/** /market — 资源市场 (BL-CENTRAL-WEB-CONSOLIDATE 5/17 鸿波).
 *
 * 整合 Skills Hub + MCP 连接器市场为单一入口 — 它们本质同类 (LLM plugin
 * marketplace), 员工脑子里没"skill vs mcp"区分, 都是"找能给 LLM 用的工具".
 *
 *   /market/skills/* — 技能 (SkillsHubPage)
 *   /market/mcp/*    — 连接器 (McpMarketPage)
 *   /market/experts  — 专家包 (10/18): 已上架 / 主管的待审核 / 我提交的
 *   /market/wiki/*   — 部门知识库分享 (11/4): 员工在 Companion 分享的笔记, 看 / 撤回
 *   /market/components — 组件 · 模型与运行包 (10/3, 中央 /components/ 发布的大件)
 *
 * 11/09 鸿波「市场的 UI 是不是应该和控制台的风格一致」: 原来是一张大卡片里一排带 emoji、
 * 带副标题的横向页签, 下面每页各自一摞卡片。现在跟控制台一个外壳 (components/SideNav):
 * 左侧分组导航 + 右侧内容列, 页面用控制台那套 Toolbar / Tabs / Section / DataTable。
 * 主管 / 管理员的待审核数挂在导航项上 (控制台没有这个, 但审核就散在这几类里, 不挂的话
 * 得一页页点进去才知道哪类有人等着)。
 */
import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Navigate, Route, Routes } from "react-router-dom";

import { roleAllows } from "../components/RoleGate";
import { SideNavLayout } from "../components/SideNav";
import { listReviewExperts } from "../lib/experts";
import { listReviewSkills } from "../lib/hub";
import { listReviewSubmissions } from "../lib/mcp";
import { listReviewWikiDocs } from "../lib/wiki";
import { useAuthStore } from "../store/auth";
import { ComponentsMarketPage } from "./ComponentsMarketPage";
import { ExpertsMarketPage } from "./ExpertsMarketPage";
import { McpMarketPage } from "./McpMarketPage";
import { SkillsHubPage } from "./SkillsHubPage";
import { RefreshCounts } from "./marketShared";
import { WikiMarketPage } from "./WikiMarketPage";

type Kind = "skills" | "mcp" | "experts" | "wiki";

function usePendingCounts(reviewer: boolean): [Partial<Record<Kind, number>>, () => void] {
  const [counts, setCounts] = useState<Partial<Record<Kind, number>>>({});
  const load = useCallback(() => {
    if (!reviewer) return;
    // 每类各拉各的: 一个接口挂了 (老中央没有某类审核) 只是那一类不显示数字
    const one = (k: Kind, p: Promise<number>) =>
      p.then((n) => setCounts((c) => ({ ...c, [k]: n }))).catch(() => setCounts((c) => ({ ...c, [k]: undefined })));
    void one("skills", listReviewSkills().then((r) => (r.skills || []).length));
    void one("mcp", listReviewSubmissions().then((r) => (r.submissions || []).length));
    void one("experts", listReviewExperts().then((r) => (r.experts || []).length));
    void one("wiki", listReviewWikiDocs().then((r) => (r.documents || []).length));
  }, [reviewer]);
  useEffect(load, [load]);
  return [counts, load];
}

function MarketLayout({ children }: { children: ReactNode }) {
  const me = useAuthStore((s) => s.me);
  const reviewer = !!me && roleAllows(me.role, "manager");
  const [counts, refresh] = usePendingCounts(reviewer);
  const groups = [
    {
      title: "资源",
      items: [
        { href: "/market/skills", label: "技能", count: counts.skills },
        { href: "/market/mcp", label: "连接器", count: counts.mcp },
        { href: "/market/experts", label: "专家包", count: counts.experts },
        { href: "/market/wiki", label: "知识库", count: counts.wiki },
      ],
    },
    { title: "下载", items: [{ href: "/market/components", label: "组件" }] },
  ];
  return (
    <RefreshCounts.Provider value={refresh}>
      <SideNavLayout ariaLabel="市场导航" groups={groups}>
        {children}
      </SideNavLayout>
    </RefreshCounts.Provider>
  );
}

export function MarketPage() {
  return (
    <MarketLayout>
      <Routes>
        {/* 默认进技能 */}
        <Route index element={<Navigate to="/market/skills" replace />} />
        <Route path="skills/*" element={<SkillsHubPage />} />
        <Route path="mcp/*" element={<McpMarketPage />} />
        <Route path="experts" element={<ExpertsMarketPage />} />
        <Route path="wiki/*" element={<WikiMarketPage />} />
        <Route path="components" element={<ComponentsMarketPage />} />
      </Routes>
    </MarketLayout>
  );
}
