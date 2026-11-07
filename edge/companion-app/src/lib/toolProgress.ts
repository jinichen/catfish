/** 11/07: hermes.tool.progress 事件 → 对话右侧栏 (tabs/Chat/artifacts/useArtifactAutoOpen.ts) 要的形状。
 *  hermes 自己跑的工具在回答进行中只有这个开始 / 结束信号, 消息里的 tool_calls 要等这一轮结束从库里重读才有。 */

/** 真工具的开始 / 结束才转; 插件的进度条、审批、怪数据返回 null */
export function toolProgressDetail(ev: unknown, sessionId: string | null) {
  const e = ev as { tool?: unknown; status?: unknown; label?: unknown; toolCallId?: unknown } | null;
  if (!e || typeof e.tool !== "string" || e.tool === "catfish-lifecycle") return null;
  if (e.status !== "running" && e.status !== "completed") return null;
  return {
    tool: e.tool,
    status: e.status as "running" | "completed",
    label: typeof e.label === "string" ? e.label : "",
    toolCallId: typeof e.toolCallId === "string" ? e.toolCallId : "",
    session_id: sessionId,
  };
}
