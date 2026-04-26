/** 顶部三 tab 切换条。 */

import { useUIStore, type TabId } from "../store/ui";

// 注: "会话" tab 已并入 "对话" 的左侧 sidebar (P0-3.1), 这里不再列出
const TABS: { id: TabId; label: string }[] = [
  { id: "chat", label: "对话" },
  { id: "console", label: "控制台" },
  { id: "dashboard", label: "仪表盘" },
];

export default function TabBar() {
  const activeTab = useUIStore((s) => s.activeTab);
  const setActiveTab = useUIStore((s) => s.setActiveTab);

  return (
    <nav
      style={{
        display: "flex",
        gap: 0,
        borderBottom: "1px solid var(--catfish-border)",
        background: "var(--catfish-bg-elevated)",
        paddingLeft: "var(--space-4)",
      }}
    >
      {TABS.map((t) => {
        const active = activeTab === t.id;
        return (
          <button
            key={t.id}
            onClick={() => setActiveTab(t.id)}
            style={{
              padding: "var(--space-3) var(--space-4)",
              border: "none",
              background: "transparent",
              fontSize: 13,
              color: active
                ? "var(--catfish-cyan-dim)"
                : "var(--catfish-text-muted)",
              borderBottom: active
                ? "2px solid var(--catfish-cyan)"
                : "2px solid transparent",
              fontWeight: active ? 600 : 400,
              transition: "color 0.15s",
            }}
          >
            {t.label}
          </button>
        );
      })}
    </nav>
  );
}
