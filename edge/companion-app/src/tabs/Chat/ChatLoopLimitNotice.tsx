/** 网关每轮工具上限叫停 (409 agent_loop_limit_exceeded) 的提示 —— 不是故障, 用提示色不用错误红。
 *  只在最后一条消息上给「继续」: 点了等于发一条"继续", 网关计数清零接着做。 */

import { agentLoopLimitText } from "../../lib/agentLoopLimit";

export default function ChatLoopLimitNotice({ error, canContinue }: { error: string; canContinue: boolean }) {
  return (
    <div
      role="status"
      style={{
        marginTop: "var(--space-2)",
        padding: "var(--space-2) var(--space-3)",
        background: "var(--catfish-cyan-tint)",
        border: "1px solid color-mix(in srgb, var(--catfish-cyan) 30%, transparent)",
        borderRadius: "var(--radius-sm)",
        fontSize: 13,
        color: "var(--catfish-text)",
        display: "flex",
        alignItems: "center",
        gap: "var(--space-3)",
        flexWrap: "wrap",
      }}
    >
      <span style={{ flex: 1, minWidth: 200 }}>⏸ {agentLoopLimitText(error)}</span>
      {canContinue && (
        <button
          type="button"
          className="chat-composer__primary-button"
          onClick={() =>
            window.dispatchEvent(new CustomEvent("catfish:approval-send", { detail: { text: "继续" } }))
          }
        >
          继续
        </button>
      )}
    </div>
  );
}
