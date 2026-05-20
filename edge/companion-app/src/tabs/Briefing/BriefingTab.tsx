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
        // 居中容器, max-width 防过宽屏看着空, padding 跟其他 tab 一致
        maxWidth: 720,
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
