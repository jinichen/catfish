/** 主导航栏 + BL-E15 专注模式按钮 */

import type { Icon } from "@phosphor-icons/react";
import {
  BookOpenText,
  ChartBar,
  EnvelopeSimple,
  CornersIn,
  SquaresFour,
  SunHorizon,
} from "@phosphor-icons/react";
import { useUIStore, type TabId } from "../store/ui";
import { useFocusStore } from "../store/focus";
import { useAgentStore } from "../store/agent";

// 注: "会话" tab 已并入 "工作台" 的左侧 sidebar (P0-3.1), 这里不再列出
// BL-CONSOLE-TAB-KILL (5/16): 控制台 tab 砍, 90% 跟仪表盘"本地服务"卡重叠 + log
// 对一般员工无用. 启停操作挪到 ServicesCard. 真要看 log 走 ~/Library/Logs/.
// BL-TAB-RENAME (5/16): "对话" → "工作台". 跟 ChatGPT/Claude 区分定位:
// catfish 是数字员工不是聊天工具, 员工在这干活. 跟 "仪表盘"形成"做事 vs 看事" 对偶.
const TABS: { id: TabId; label: string; icon: Icon }[] = [
  // 5/20 BL-COMPANION-TAB-ORDER-SWAP: 早安 ↔ 工作台 互换. 早安放最左, 开窗第一眼看 today.
  // 默认 activeTab 仍 chat (员工核心动作还是工作台对话), 只是排版上早安更显眼.
  { id: "briefing", label: "早安", icon: SunHorizon },
  { id: "chat", label: "工作台", icon: SquaresFour },
  // 5/18 BL-COMPANION-EMAIL-TAB: 跟 工作台/仪表盘 纯文字对齐, 不加 📧 emoji
  // (单独加图标视觉不一致, 鸿波 5/18 反馈)
  { id: "email", label: "邮件", icon: EnvelopeSimple },
  // { id: "console", label: "控制台" },  // 5/16 砍
  // BL-CATFISH-WIKI-MODE P3.3 (6/4): 知识体系 tab — 跟其它 tab 纯文字对齐, 无 emoji
  // 6/9 鸿波: 知识体系 ↔ 仪表盘 互换. 知识体系日常翻看比仪表盘多, 放中段.
  //          仪表盘 (本地服务 / 配额 / 隐私 / 技能) 是配置看不勤, 放最右当"设置".
  { id: "wiki", label: "知识体系", icon: BookOpenText },
  { id: "dashboard", label: "仪表盘", icon: ChartBar },
];

export default function TabBar() {
  const activeTab = useUIStore((s) => s.activeTab);
  const setActiveTab = useUIStore((s) => s.setActiveTab);
  const enterFocus = useFocusStore((s) => s.toggle);
  const agentName = useAgentStore((s) => s.name);

  return (
    <nav className="app-rail" aria-label="主导航">
      <div className="app-rail__brand" title="鲶鱼 Companion">
        <img src="/catfish-avatar.svg" alt="" />
      </div>

      <div className="app-rail__nav">
        {TABS.map((t) => {
          const active = activeTab === t.id;
          const IconComponent = t.icon;
          return (
            <button
              key={t.id}
              type="button"
              onClick={() => setActiveTab(t.id)}
              className="app-rail__item"
              data-active={active || undefined}
              aria-current={active ? "page" : undefined}
            >
              <IconComponent size={21} weight={active ? "fill" : "regular"} aria-hidden="true" />
              <span>{t.label}</span>
            </button>
          );
        })}
      </div>

      <div className="app-rail__footer">
        <button
          type="button"
          onClick={enterFocus}
          title="进入专注模式 (Cmd+Shift+F)"
          className="app-rail__item app-rail__item--quiet"
        >
          <CornersIn size={20} aria-hidden="true" />
          <span>专注</span>
        </button>
        <div className="app-rail__identity" title={`当前数字副手：${agentName}`}>
          <img src="/catfish-avatar.svg" alt="" />
          <span>{agentName}</span>
        </div>
      </div>
    </nav>
  );
}
