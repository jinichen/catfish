/** P3.5.17 (6/17 鸿波) — context 上下文占用顶部横条警告.
 *
 * # 为啥需要
 *
 * 5/13 BL-CONTEXT-COUNTER 加的 ContextCounter (ChatTab.tsx:297 状态栏角落小标签)
 * 已经显 prompt_tokens / context_window 三档颜色. 但鸿波 6/16 实跑撞 304K / 128K
 * (238% overflow), **没注意到状态栏小标签** — 状态栏在 chat 输入框旁边, 长任务
 * 跑时员工眼睛盯着会话内容, 角落标签变红没人看. 撞 SSE friendly error 才反应
 * "原来 context 已经爆了".
 *
 * # 改法
 *
 * 跟 AuthBanner / AdvisoryBanner 同款顶部横条 — 醒目位置, 不容易忽略.
 *
 * # 显示规则 (按优先级, 红 > 黄 > 不显)
 *
 *   pct < 80%   → 不显 (常态, 不打扰)
 *   pct ≥ 80%   → 黄色 ⚠ "接近上限, 建议长任务跑完后 Cmd+N 新建会话"
 *   pct ≥ 95%   → 红色 🚨 "已超 95%, gateway 可能 truncate 输入,
 *                  立刻 Cmd+N / 切 catfish-public-gemini-pro (2M)"
 *   pct ≥ 100%  → 红色 🚨 "已 overflow, 上游 LLM 会 silent truncate, 必须新建"
 *
 * # 跟 ContextCounter 共存
 *
 * 不删 ContextCounter — 状态栏小标签**常态可见**, 让员工随时知道占用. Banner
 * 只在 80%+ 时 pop 顶部 — 这两个是 layered 设计 (常态信号 vs 告警信号).
 *
 * # 数据来源
 *
 * 跟 ContextCounter 同 — useChatStore.lastPromptTokens (useChat.onDone 写) +
 * useCatalog 找当前 model 的 context_window. 切会话 lastPromptTokens=null,
 * banner 自动消失.
 *
 * # 红线
 *
 * UI 不能 **自动切模型** (会撞 5/15 BL-FALLBACK-CAP-SCOPE 红线 —
 * private prompt 不静默飞 public). 只**提示员工自己切**, 保持员工主权.
 */

import { useChatStore } from "../store/chat";
import { useCatalog } from "../hooks/useCatalog";

const BANNER_BASE: React.CSSProperties = {
  borderBottom: "1px solid",
  fontSize: 12,
  padding: "6px 16px",
  textAlign: "center" as const,
};

export default function ContextOverflowBanner() {
  const lastPromptTokens = useChatStore((s) => s.lastPromptTokens);
  const model = useChatStore((s) => s.model);
  const { catalog } = useCatalog();

  if (lastPromptTokens == null) return null;

  const modelInfo = catalog?.models.find((m) => m.id === model);
  const contextWindow = modelInfo?.context_window ?? 0;
  if (contextWindow <= 0) return null;

  const pct = (lastPromptTokens / contextWindow) * 100;

  // < 80% → 不显, 不打扰员工 (ContextCounter 状态栏角落仍显)
  if (pct < 80) return null;

  // ≥ 100% → 红色 overflow
  if (pct >= 100) {
    return (
      <div
        style={{
          ...BANNER_BASE,
          background: "rgba(220, 38, 38, 0.18)",
          borderBottomColor: "rgba(220, 38, 38, 0.45)",
          color: "#dc2626",
          fontWeight: 600,
        }}
      >
        🚨 当前 prompt {Math.round(lastPromptTokens / 1000)}K / {Math.round(contextWindow / 1000)}K
        ({pct.toFixed(0)}%) 已超 context_window — 上游 LLM 会 silent truncate, 长任务可能"谎报生成".
        立刻 <strong>Cmd+N</strong> 新建会话, 或切 <strong>catfish-public-gemini-pro</strong> (2M).
      </div>
    );
  }

  // 95-100% → 红色 near-overflow
  if (pct >= 95) {
    return (
      <div
        style={{
          ...BANNER_BASE,
          background: "rgba(220, 38, 38, 0.12)",
          borderBottomColor: "rgba(220, 38, 38, 0.35)",
          color: "#dc2626",
          fontWeight: 600,
        }}
      >
        🚨 prompt 已用 {pct.toFixed(0)}% ({Math.round(lastPromptTokens / 1000)}K /
        {" "}{Math.round(contextWindow / 1000)}K) — 即将撞 overflow.
        建议 <strong>Cmd+N</strong> 新建, 或 <strong>/compress</strong> 当前主题压缩,
        或切 <strong>catfish-public-gemini-pro</strong>.
      </div>
    );
  }

  // 80-95% → 黄色 warning
  return (
    <div
      style={{
        ...BANNER_BASE,
        background: "rgba(217, 119, 6, 0.12)",
        borderBottomColor: "rgba(217, 119, 6, 0.35)",
        color: "#d97706",
      }}
    >
      ⚠ prompt 已用 {pct.toFixed(0)}% ({Math.round(lastPromptTokens / 1000)}K /
      {" "}{Math.round(contextWindow / 1000)}K) — 接近 context 上限.
      长任务跑完后建议 <strong>Cmd+N</strong> 新建, 或用 <strong>/compress</strong> 保留主题.
    </div>
  );
}
