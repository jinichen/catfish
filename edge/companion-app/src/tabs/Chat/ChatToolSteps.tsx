/** 一组后台动作 —— 默认收起成一行 "后台动作 · N 步", 点开看每一步。
 *  收起时仍露出需要人看/动手的步骤 (见 lib/chatToolSteps.ts)。 */

import { useState } from "react";
import type { ToolCall } from "../../types/chat";
import ChatToolCall from "./ChatToolCall";
import { summarizeToolNames, toolCallNeedsAttention } from "../../lib/chatToolSteps";

interface Props {
  calls: ToolCall[];
  /** 这一组还在流式输出中 */
  active?: boolean;
}

export default function ChatToolSteps({ calls, active = false }: Props) {
  const [open, setOpen] = useState(false);
  const running = active || calls.some((c) => c.status === "running" || c.status === "pending");
  const errors = calls.filter((c) => c.status === "error").length;
  const visible = open ? calls : calls.filter(toolCallNeedsAttention);

  return (
    <div className={`toolsteps${open ? " toolsteps--open" : ""}`}>
      <button
        type="button"
        className="toolsteps__summary"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        <span className="toolsteps__caret">▶</span>
        <span className="toolsteps__label">
          {running ? "正在处理" : "后台动作"} · {calls.length} 步
        </span>
        <span className="toolsteps__names">{summarizeToolNames(calls)}</span>
        {errors > 0 && <span className="toolsteps__err">{errors} 步出错</span>}
      </button>
      {visible.length > 0 && (
        <div className="toolsteps__list">
          {visible.map((tc) => (
            <ChatToolCall key={tc.id} call={tc} />
          ))}
        </div>
      )}
    </div>
  );
}
