/**
 * BL-TASK-ASSESS-3-UI (5/15 鸿波"客户端要评估完成情况"):
 * assistant message 上的"嘴炮断言"badge + 催继续按钮.
 *
 * # 触发
 *
 * 仅当 assistant 消息的 _promise_check.is_promise_only=true 时渲染.
 * 即: 模型文字说了"已生成"但 cum_has_tool_call=false + skill_guard 命中 + 还没真进过 skill 入口.
 *
 * # UI
 *
 * 红色警告条:
 *   ⚠ 模型说生成了 ~/.catfish/output/x.html, 但这一轮没真调任何工具 — 检查文件是否存在
 *   [⏩ 催它继续 (剩 N 次)]
 *
 * 点"催它继续"发一条 user message "继续" (跟员工手动点效果一样),
 * 同时把 nudge_count++. 用完 3 次 → 按钮变灰 + 提示"模型反复嘴炮, 换模型或自己接手".
 */

import { describePromiseCheck } from "../../lib/promiseCheck";
import { useChatStore } from "../../store/chat";
import type { ChatMessage } from "../../types/chat";

const MAX_NUDGES = 3;

interface Props {
  msg: ChatMessage;
  /** 触发"催继续" — 走跟 user 手动发"继续"完全一样的路径, 不走 gateway 重试 */
  onNudge: () => void;
}

export default function PromiseCheckBadge({ msg, onNudge }: Props) {
  const check = msg._promise_check;
  if (!check?.is_promise_only) return null;

  const remaining = MAX_NUDGES - check.nudge_count;
  const exhausted = remaining <= 0;
  const text = describePromiseCheck({
    is_promise_only: check.is_promise_only,
    promised_paths: check.promised_paths,
  });

  return (
    <div
      style={{
        marginTop: "var(--space-2)",
        padding: "var(--space-2) var(--space-3)",
        background: "var(--catfish-bg)",
        border: "1px solid var(--status-warn, #ffa500)",
        borderRadius: "var(--radius-sm)",
        fontSize: 12,
        color: "var(--status-warn, #ffa500)",
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: "var(--space-3)",
        flexWrap: "wrap",
      }}
    >
      <span style={{ flex: 1, minWidth: 200 }}>{text}</span>
      {exhausted ? (
        <span
          style={{
            fontSize: 11,
            opacity: 0.75,
            fontStyle: "italic",
          }}
          title="3 次内还嘴炮, 换 Gemini Pro 或自己接手, 不要再催了"
        >
          ⚠ 反复嘴炮, 换模型或自己接手
        </span>
      ) : (
        <button
          onClick={() => {
            // 标记 nudge 计数 + 1, store 处理
            useChatStore.getState().incrementPromiseNudge(msg.id);
            onNudge();
          }}
          style={{
            padding: "var(--space-1) var(--space-3)",
            border: "1px solid var(--status-warn, #ffa500)",
            borderRadius: "var(--radius-sm)",
            background: "transparent",
            color: "var(--status-warn, #ffa500)",
            fontSize: 12,
            fontWeight: 500,
            cursor: "pointer",
            whiteSpace: "nowrap",
          }}
          title="发一条'继续'催它真调工具. 不走 gateway 重试, 跟你手动点效果一样."
        >
          ⏩ 催它继续 ({remaining}/{MAX_NUDGES})
        </button>
      )}
    </div>
  );
}
