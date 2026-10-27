/** 网关「本轮工具调用超过安全上限」(409 agent_loop_limit_exceeded) 的识别与说法。
 *
 * 10/11: 网关按"每条用户消息"计数工具调用 (central/llm-gateway tool_loop_guard.py),
 * 长任务一轮里调得多就会被叫停。这不是故障: 发一条新消息计数就清零, 接着做。
 * hermes 把网关的 409 原样转过来, 以前员工看到的是一大段红色 JSON。
 */

export function isAgentLoopLimit(err: string | undefined | null): boolean {
  return !!err && err.includes("agent_loop_limit_exceeded");
}

/** 10/27: hermes 自己的「一轮最多调多少步」(config.yaml agent.max_turns) 到了。
 *
 * hermes 不报错: 它在流里发一条 `hermes.status` (agent/turn_finalizer.py
 * "⚠️ Iteration budget exhausted (60/60) — asking model to summarise"), 再让模型写个小结当回答。
 * 以前员工看到的就是一段小结然后没下文, 像回答断了 (10/27 鸿波 Windows 机 13:36)。
 * 认出这条状态, 回答结束后在下面说清楚是到了上限、点「继续」接着做。 */
export function parseStepLimit(status: string | undefined | null): { used: number; max: number } | null {
  const m = (status || "").match(/Iteration budget exhausted \((\d+)\/(\d+)\)/i);
  return m ? { used: Number(m[1]), max: Number(m[2]) } : null;
}

export function stepLimitText(limit: { used: number; max: number }): string {
  return `这件事步骤比较多, 已经做到一轮的上限 (${limit.max} 步), 上面是小鲶先做的小结。还没做完的话点「继续」接着做。`;
}

/** 从错误里取出网关的上限 (取不到就不提数字, 不编)。 */
export function agentLoopLimitText(err: string): string {
  const m = err.match(/"max_tool_messages"\s*:\s*(\d+)/);
  const n = m ? `(超过 ${m[1]} 次工具调用) ` : "";
  return `这一轮步骤比较多 ${n}, 已自动暂停。点「继续」接着做, 新一轮会重新计数; 打开「自动接力」可以不用手点。`;
}
