/** 顶部三 tab 切换条 + BL-E15 专注模式按钮 */

import { useUIStore, type TabId } from "../store/ui";
import { useFocusStore } from "../store/focus";

// 注: "会话" tab 已并入 "工作台" 的左侧 sidebar (P0-3.1), 这里不再列出
// BL-CONSOLE-TAB-KILL (5/16): 控制台 tab 砍, 90% 跟仪表盘"本地服务"卡重叠 + log
// 对一般员工无用. 启停操作挪到 ServicesCard. 真要看 log 走 ~/Library/Logs/.
// BL-TAB-RENAME (5/16): "对话" → "工作台". 跟 ChatGPT/Claude 区分定位:
// catfish 是数字员工不是聊天工具, 员工在这干活. 跟 "仪表盘"形成"做事 vs 看事" 对偶.
const TABS: { id: TabId; label: string }[] = [
  { id: "chat", label: "工作台" },
  // { id: "console", label: "控制台" },  // 5/16 砍
  { id: "dashboard", label: "仪表盘" },
];

export default function TabBar() {
  const activeTab = useUIStore((s) => s.activeTab);
  const setActiveTab = useUIStore((s) => s.setActiveTab);
  const enterFocus = useFocusStore((s) => s.toggle);

  return (
    <nav
      style={{
        display: "flex",
        gap: 0,
        borderBottom: "1px solid var(--catfish-border)",
        background: "var(--catfish-bg-elevated)",
        paddingLeft: "var(--space-4)",
        paddingRight: "var(--space-3)",
      }}
    >
      <div style={{ display: "flex", flex: 1 }}>
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
      </div>

      {/* BL-E15: 专注模式入口, 不知道快捷键的员工也能用. 跟 tab 视觉分离 */}
      <button
        type="button"
        onClick={enterFocus}
        title="进入专注模式 (Cmd+Shift+F)"
        style={{
          alignSelf: "center",
          padding: "4px 10px",
          background: "transparent",
          border: "1px solid var(--catfish-border)",
          borderRadius: 4,
          fontSize: 11,
          color: "var(--catfish-text-muted)",
          cursor: "pointer",
          fontFamily: "inherit",
        }}
      >
        ⏸ 专注
      </button>
    </nav>
  );
}
