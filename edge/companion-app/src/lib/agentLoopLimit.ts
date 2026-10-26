/** 网关「本轮工具调用超过安全上限」(409 agent_loop_limit_exceeded) 的识别与说法。
 *
 * 10/11: 网关按"每条用户消息"计数工具调用 (central/llm-gateway tool_loop_guard.py),
 * 长任务一轮里调得多就会被叫停。这不是故障: 发一条新消息计数就清零, 接着做。
 * hermes 把网关的 409 原样转过来, 以前员工看到的是一大段红色 JSON。
 */

export function isAgentLoopLimit(err: string | undefined | null): boolean {
  return !!err && err.includes("agent_loop_limit_exceeded");
}

/** 从错误里取出网关的上限 (取不到就不提数字, 不编)。 */
export function agentLoopLimitText(err: string): string {
  const m = err.match(/"max_tool_messages"\s*:\s*(\d+)/);
  const n = m ? `(超过 ${m[1]} 次工具调用) ` : "";
  return `这一轮步骤比较多 ${n}, 已自动暂停。点「继续」接着做, 新一轮会重新计数; 打开「自动接力」可以不用手点。`;
}
