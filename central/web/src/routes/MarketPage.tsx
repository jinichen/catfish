/** /market — 资源市场 (BL-CENTRAL-WEB-CONSOLIDATE 5/17 鸿波).
 *
 * 整合 Skills Hub + MCP 连接器市场为单一入口 — 它们本质同类 (LLM plugin
 * marketplace), 员工脑子里没"skill vs mcp"区分, 都是"找能给 LLM 用的工具".
 *
 * 内部 2 个 sub-tab:
 *   /market/skills/* — 技能脚本市场 (原 /skills, 复用 SkillsHubPage)
 *   /market/mcp/*    — 连接器市场 (原 /mcp, 复用 McpMarketPage)
 *
 * 未来扩展 (不在本 sprint): prompts / personas / 模板 也归 /market 下.
 */
import { Link, Navigate, Route, Routes, useLocation } from "react-router-dom";

import { Card } from "../components/Card";
import { SkillsHubPage } from "./SkillsHubPage";
import { McpMarketPage } from "./McpMarketPage";

const TABS = [
  { to: "/market/skills", label: "🛠️ Skills", desc: "技能脚本" },
  { to: "/market/mcp", label: "🔌 MCP", desc: "连接器" },
];

function MarketLayout({ children }: { children: React.ReactNode }) {
  const loc = useLocation();
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
      <Card title="📦 市场 · LLM 工具与资源">
        <div
          style={{
            display: "flex",
            gap: "var(--space-3)",
            marginTop: "var(--space-2)",
            borderBottom: "1px solid var(--border)",
            paddingBottom: 0,
          }}
        >
          {TABS.map((t) => {
            const active =
              loc.pathname === t.to || loc.pathname.startsWith(t.to + "/");
            return (
              <Link
                key={t.to}
                to={t.to}
                style={{
                  padding: "8px 16px",
                  fontSize: 14,
                  fontWeight: active ? 600 : 400,
                  color: active ? "var(--accent)" : "var(--text)",
                  borderBottom: active
                    ? "2px solid var(--accent)"
                    : "2px solid transparent",
                  marginBottom: "-1px",
                  textDecoration: "none",
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  gap: 2,
                }}
              >
                <span>{t.label}</span>
                <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
                  {t.desc}
                </span>
              </Link>
            );
          })}
        </div>
      </Card>
      {children}
    </div>
  );
}

export function MarketPage() {
  return (
    <Routes>
      {/* 默认进 Skills tab */}
      <Route index element={<Navigate to="/market/skills" replace />} />
      <Route
        path="skills/*"
        element={
          <MarketLayout>
            <SkillsHubPage />
          </MarketLayout>
        }
      />
      <Route
        path="mcp/*"
        element={
          <MarketLayout>
            <McpMarketPage />
          </MarketLayout>
        }
      />
    </Routes>
  );
}
