/** Context usage counter — 状态栏显示 prompt_tokens / context_window.
 *
 * BL-CONTEXT-COUNTER (5/13): 借鉴 Hermes 0.13 状态栏 context counter.
 * 配合 5/13 早上加的 _is_context_overflowed 监控, 让员工自己看到当前 prompt
 * 烧到 context 多少 — 接近上限主动 /compress 或 Cmd+N, 不撞 truncate.
 *
 * 颜色阈值:
 *   < 50%   绿 (从容)
 *   50-80%  灰 (常态)
 *   80-95%  黄 (告警, 该考虑新建会话)
 *   ≥ 95%   红 (即将撞 overflow, 立刻 Cmd+N / 切大模型)
 *
 * 数据来源: useChatStore.lastPromptTokens (useChat.onDone 写) +
 *           useCatalog 找当前 model 的 context_window.
 *
 * 没数据时 (新 session 还没发过) 不渲染.
 */

import { useChatStore } from "../../store/chat";
import { useCatalog } from "../../hooks/useCatalog";

function formatTokens(n: number): string {
  if (n < 1000) return String(n);
  if (n < 100_000) return (n / 1000).toFixed(1) + "K";
  return Math.round(n / 1000) + "K";
}

export default function ContextCounter() {
  const lastPromptTokens = useChatStore((s) => s.lastPromptTokens);
  const model = useChatStore((s) => s.model);
  const { catalog } = useCatalog();

  if (lastPromptTokens == null) return null;

  // 找 model 的 context_window
  const modelInfo = catalog?.models.find((m) => m.id === model);
  const contextWindow = modelInfo?.context_window ?? 0;
  if (contextWindow <= 0) return null;

  const pct = (lastPromptTokens / contextWindow) * 100;

  // 颜色档位
  let color: string;
  let bg: string;
  if (pct >= 95) {
    color = "white";
    bg = "var(--status-err, #dc2626)";  // 红
  } else if (pct >= 80) {
    color = "var(--status-warn, #d97706)";
    bg = "rgba(217, 119, 6, 0.12)";  // 黄底
  } else if (pct < 50) {
    color = "var(--catfish-cyan)";
    bg = "transparent";
  } else {
    color = "var(--catfish-text-muted)";
    bg = "transparent";
  }

  const tip = [
    `当前 prompt: ${lastPromptTokens.toLocaleString()} / ${contextWindow.toLocaleString()} tokens`,
    `占用 ${pct.toFixed(0)}%`,
    pct >= 95
      ? "🚨 即将撞 context overflow, 立刻 Cmd+N 新建 / 切 catfish-public-gemini-pro (2M)"
      : pct >= 80
      ? "⚠️ 接近上限, 长任务跑完后建议 Cmd+N 或 /compress"
      : pct >= 50
      ? "常态使用中"
      : "从容",
  ].join("\n");

  return (
    <span
      title={tip}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        padding: "2px 8px",
        borderRadius: "var(--radius-sm)",
        fontSize: 11,
        fontFamily: "var(--font-mono, ui-monospace, monospace)",
        color,
        background: bg,
        cursor: "help",
        fontWeight: pct >= 80 ? 600 : 400,
      }}
    >
      📏 {formatTokens(lastPromptTokens)}/{formatTokens(contextWindow)}
      <span style={{ opacity: 0.7 }}>· {pct.toFixed(0)}%</span>
    </span>
  );
}
