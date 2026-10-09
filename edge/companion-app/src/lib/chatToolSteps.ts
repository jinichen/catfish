/** 对话里的"后台动作"折叠逻辑 —— 纯函数, ChatPanel / ChatToolSteps 用。
 *
 * 10/10 鸿波: 一轮长任务里 hermes 每调一次工具就是一条只有 tool_calls、
 * 没有正文的 assistant 消息, 每条都带头像独占一行, 三五个 skill_manage
 * 卡片把真正的回答挤到下面, 内容和背景动作混在一起。
 *
 * 做法: 一轮回答里的工具调用并到同一个气泡, 渲染成一行 "后台动作 · N 步",
 * 默认收起。需要人看或动手的步骤 (等批准、要密码、出错、正在跑) 收起时也
 * 整张卡片露出 —— P27.2 踩过折叠后按钮看不到卡死的坑; 产出的文件只露入口。
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

/** 收起状态下也要整张卡片露出的步骤: 员工得看见进度/错误, 或得动手 (批准、填密码) */
export function toolCallNeedsCard(call: ToolCall): boolean {
  if (call.status !== "done") return true; // pending / running / error
  const resultStr = toolResultString(call);
  return (
    isApprovalPending(resultStr) ||
    parseNeedsCredential(resultStr, call.name) !== null
  );
}

/** 小鲶自己的草稿脚本、中间文件 —— 员工用不上, 不冒文件入口 */
const SCRATCH_PATH_RE = /^(\/private)?\/tmp\/|^\/var\/folders\/|[\\/]Temp[\\/]/;

/** 收起时只露文件入口, 不露卡片 (10/10: write_file 写 /tmp 脚本整张卡片撑开过) */
export function collectStepFiles(calls: ToolCall[]): string[] {
  const seen = new Set<string>();
  for (const c of calls) {
    if (c.status !== "done" || toolCallNeedsCard(c)) continue;
    for (const p of extractFilePaths(toolResultString(c))) {
      if (!SCRATCH_PATH_RE.test(p)) seen.add(p);
    }
  }
  return [...seen];
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

/** 纯工具调用的 assistant 消息并进紧挨着的上一条 assistant 消息 (有没有正文都并),
 *  一轮回答只占一个头像; 用户说话才断开。tool / system 消息本来就不渲染,
 *  夹在中间不打断, 也不输出。 */
export function groupToolSteps(messages: ChatMessage[]): ToolStepGroup[] {
  const out: ToolStepGroup[] = [];
  let open: ToolStepGroup | null = null;
  for (const m of messages) {
    if (m.role === "tool" || m.role === "system") continue;
    if (isToolOnly(m) && open) {
      open.msg = {
        ...open.msg,
        tool_calls: [...(open.msg.tool_calls ?? []), ...(m.tool_calls ?? [])],
        status: m.status,
      };
      open.memberIds.push(m.id);
      continue;
    }
    const g: ToolStepGroup = { msg: m, memberIds: [m.id] };
    out.push(g);
    open = m.role === "assistant" && m.status !== "error" ? g : null;
  }
  return out;
}

export function summarizeToolNames(calls: ToolCall[], max = 3): string {
  const counts = new Map<string, number>();
  for (const c of calls) counts.set(c.name, (counts.get(c.name) ?? 0) + 1);
  const parts = [...counts].map(([n, k]) => (k > 1 ? `${n} ×${k}` : n));
  return parts.length > max
    ? `${parts.slice(0, max).join("、")} 等`
    : parts.join("、");
}
