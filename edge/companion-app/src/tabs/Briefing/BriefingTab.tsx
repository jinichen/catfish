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
        // P3.5.32.8 (6/18 鸿波 catch '外框浪费空间'):
        //   老 maxWidth 980 + padding var(--space-4) → 2204px viewport 只用 44%, 双侧
        //   大量留白 + BriefingCard 卡片样式形成两层 framing.
        //   改: maxWidth 1280 (大屏更撑开), padding 减半 (空间紧凑).
        // P3.3.6 (6/10): 两栏 layout 需要更宽, max-width 720 → 980 (老);
        // P3.5.32.8 (6/18): 980 → 1280
        maxWidth: 1280,
        margin: "0 auto",
        padding: "var(--space-2) var(--space-3)",
        width: "100%",
        boxSizing: "border-box",
        // 8/8 (鸿波 catch '右边内容区底部空间太大'): 把父容器算好的高度传下去。
        //
        // 这一层以前没有高度, 于是最里面的 .briefing-2col 只能自己去猜
        // `calc(100vh - 130px)` —— 那个数字在 6/18 已经从 200 调到过 130,
        // 每次上面加一行、改一次 padding 都会重新对不上, 而且只会**偏保守**
        // (少了会溢出被 overflow:hidden 剪掉, 没人敢往大调), 所以底部一直空一截。
        //
        // 现在的做法: main.app-workspace__main--locked 已经是 flex:1 + min-height:0,
        // 高度本来就是准的; 这一层和下面两层只负责把它原样传下去, 到
        // .briefing-2col 用 flex:1 吃满。链路上任何一环加内容都自动重算, 不用再调数字。
        //
        // min-height: 0 不能省 —— flex item 默认 min-height:auto, 不写的话子元素
        // 撑高时它不肯缩, 内部的 overflow 滚动条就失效, 变成整页被撑长。
        height: "100%",
        minHeight: 0,
        display: "flex",
        flexDirection: "column",
      }}
    >
      <BriefingCard />
    </div>
  );
}
