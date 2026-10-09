/** 对话里的"后台动作"折叠逻辑 —— 纯函数, ChatPanel / ChatToolSteps 用。
 *
 * 10/10 鸿波: 一轮长任务里 hermes 每调一次工具就是一条只有 tool_calls、
 * 没有正文的 assistant 消息, 每条都带头像独占一行, 三五个 skill_manage
 * 卡片把真正的回答挤到下面, 内容和背景动作混在一起。
 *
 * 做法: 连续的"只有工具调用"的 assistant 消息合并成一组, 渲染成一行
 * "后台动作 · N 步", 默认收起。需要人看或动手的步骤 (等批准、要密码、
 * 产出文件、出错、正在跑) 收起时也照常露出 —— P27.2 踩过折叠后按钮看不到
 * 卡死的坑。
 */

import type { ChatMessage, ToolCall } from "../types/chat";
import { extractFilePaths } from "./path_detect";
import { parseNeedsCredential } from "./needsCredential";

/** hermes approval pending 的几种返回形状 (含 LLM 翻成中文的), 见 ChatToolCall P27 注释 */
const APPROVAL_PENDING_RE =
  /pending_approval|approval_pending|Asking the user for approval|授权批准|请.{0,4}批准|等审批|等待.{0,4}批准|正在等待.{0,4}批准/i;

export function toolResultString(call: ToolCall): string {
  if (typeof call.result === "string") return call.result;
  if (call.result !== undefined) return JSON.stringify(call.result, null, 2);
  return "";
}

export function isApprovalPending(resultStr: string): boolean {
  return APPROVAL_PENDING_RE.test(resultStr);
}

/** 收起状态下也必须露出的步骤: 员工得看见或得动手 */
export function toolCallNeedsAttention(call: ToolCall): boolean {
  if (call.status !== "done") return true; // pending / running / error
  const resultStr = toolResultString(call);
  return (
    isApprovalPending(resultStr) ||
    parseNeedsCredential(resultStr, call.name) !== null ||
    extractFilePaths(resultStr).length > 0
  );
}

function isToolOnly(m: ChatMessage): boolean {
  return (
    m.role === "assistant" &&
    !m.content?.trim() &&
    m.status !== "error" &&
    (m.tool_calls?.length ?? 0) > 0
  );
}

export interface ToolStepGroup {
  /** 合并后的展示消息: id 取组内第一条, tool_calls 按顺序拼接 */
  msg: ChatMessage;
  /** 组内原始消息 id —— 判断流式光标落在哪一组 */
  memberIds: string[];
}

/** 把连续的纯工具调用 assistant 消息合并。tool / system 消息本来就不渲染,
 *  夹在中间不打断合并, 也不输出。 */
export function groupToolSteps(messages: ChatMessage[]): ToolStepGroup[] {
  const out: ToolStepGroup[] = [];
  let open: ToolStepGroup | null = null;
  for (const m of messages) {
    if (m.role === "tool" || m.role === "system") continue;
    if (isToolOnly(m)) {
      if (open) {
        open.msg = {
          ...open.msg,
          tool_calls: [...(open.msg.tool_calls ?? []), ...(m.tool_calls ?? [])],
          status: m.status,
        };
        open.memberIds.push(m.id);
      } else {
        open = { msg: m, memberIds: [m.id] };
        out.push(open);
      }
      continue;
    }
    open = null;
    out.push({ msg: m, memberIds: [m.id] });
  }
  return out;
}

/** 收起时的摘要: 工具名去重计数, 保持首次出现顺序 */
export function summarizeToolNames(calls: ToolCall[], max = 3): string {
  const counts = new Map<string, number>();
  for (const c of calls) counts.set(c.name, (counts.get(c.name) ?? 0) + 1);
  const parts = [...counts].map(([n, k]) => (k > 1 ? `${n} ×${k}` : n));
  return parts.length > max
    ? `${parts.slice(0, max).join("、")} 等`
    : parts.join("、");
}
