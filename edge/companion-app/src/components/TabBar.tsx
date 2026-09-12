/** 主导航栏。
 *
 * 9/12: 底部「专注」按钮 (BL-E15, 5/3) 连同专注模式一起删了 —— 它只是把界面换成
 * 一张伪 IDE 假日志图, 不屏蔽任何通知/调度, 5/4 后没人动过; 对政企客户是信任风险。
 */

import type { Icon } from "@phosphor-icons/react";
import {
  BookOpenText,
  ChartBar,
  EnvelopeSimple,
  Handshake,
  SquaresFour,
  SunHorizon,
} from "@phosphor-icons/react";
import { useUIStore, type TabId } from "../store/ui";
import { useAgentStore } from "../store/agent";
import { pendingCount, useRoomLink } from "../lib/roomLinkStore";

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
  // P50 (9/10): 横向协同 — 请同事的小鲶帮忙 / 等我点头. 有待点头的事时显红点数.
  { id: "collab", label: "协同", icon: Handshake },
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
  const agentName = useAgentStore((s) => s.name);
  // 订阅即开始轮询邮筒 + P49 探针 —— 导航常驻, 所以员工不进「协同」页也能看到红点。
  const collabPending = pendingCount(useRoomLink());

  return (
    <nav className="app-rail" aria-label="主导航">
      <div className="app-rail__brand" title="鲶鱼 Companion">
        <img src="/catfish-logo.svg" alt="" />
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
              {t.id === "collab" && collabPending > 0 && (
                <span className="app-rail__badge" aria-label={`${collabPending} 条等你点头`}>
                  {collabPending > 9 ? "9+" : collabPending}
                </span>
              )}
            </button>
          );
        })}
      </div>

      <div className="app-rail__footer">
        <div className="app-rail__identity" title={`当前数字副手：${agentName}`}>
          <img src="/catfish-avatar.svg" alt="" />
          <span>{agentName}</span>
        </div>
      </div>
    </nav>
  );
}
