/** 早安播报 tab — BL-COMPANION-DAILY-BRIEFING-MVP step1 (5/20 鸿波).
 *
 * 开窗第一眼看今日总览的独立 tab. 跟 Dashboard 解耦 — Dashboard 偏"系统状态"
 * (我的画像 / 服务 / 配额 / 设置), 早安播报偏"今日要事" (邮件 / 日历 / 工作计划).
 *
 * tab 容器 = BriefingCard 居中 + max-width 控制 + padding. 卡片样式保留.
 * 等 step2-5 接入 (calendar / journal TODO / LLM rank) 后再考虑做"展开版" 布局.
 */

import BriefingCard from "../Dashboard/BriefingCard";

export default function BriefingTab() {
  return (
    <div
      style={{
        // P3.3.6 (6/10): 两栏 layout 需要更宽, max-width 720 → 980
        maxWidth: 980,
        margin: "0 auto",
        padding: "var(--space-4)",
        width: "100%",
        boxSizing: "border-box",
      }}
    >
      <BriefingCard />
    </div>
  );
}
