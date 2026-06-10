/** P3.3.7 Phase 2 (6/10 鸿波): task-scoped chat 持久化 TS wrapper.
 *
 * Tauri commands 走 ~/.catfish/task_chat/<key>.jsonl. 详见
 * src-tauri/src/commands/task_chat.rs.
 *
 * task_key:
 *   - P3.3.9+: task.taskUid (LLM 生成 6 字符稳定 uid, 跨 refresh 不漂)
 *   - P3.3.9 之前: sanitize(task.title) — 老 jsonl 文件名 (DetailPane 有 fallback)
 *
 * P3.3.11 (6/10) schema 扩:
 *   - role 加 "tool"
 *   - 加 toolCalls?: ToolCall[] (assistant 消息携带的 tool 调用列表)
 *   - 加 toolCallId?: string (tool 角色消息关联到 assistant 的 tool_calls[i].id)
 *   重启 / 切 task 重新进 detail pane 时, tool 卡片 + tool 结果都能还原.
 */

import { invoke as rawInvoke } from "@tauri-apps/api/core";
import type { ToolCall } from "../types/chat";

export interface TaskChatMsg {
  role: "user" | "assistant" | "tool" | "system";
  content: string;
  ts: string;
  /** P3.3.11: assistant 消息的 tool calls (id / name / args / status / result). */
  toolCalls?: ToolCall[];
  /** P3.3.11: tool 角色消息关联到的 assistant.tool_calls[i].id. */
  toolCallId?: string;
}

/** 读 task chat 历史. 没历史返空 [].
 *  P3.3.14 (6/10): limit > 0 时只返最近 N 条 (DetailPane load 用 200 防 UI 卡;
 *  advisor summary 拉时用全部 — 老脉络才有意义). */
export const taskChatGet = (taskKey: string, limit?: number) =>
  rawInvoke<TaskChatMsg[]>("task_chat_get", { taskKey, limit });

/** Append 一条到 task chat. 自动填 ts.
 *  P3.3.11: 加 toolCalls / toolCallId 可选, 默认 undefined 写老 schema (前向兼容). */
export const taskChatAppend = (
  taskKey: string,
  role: TaskChatMsg["role"],
  content: string,
  opts?: { toolCalls?: ToolCall[]; toolCallId?: string },
) =>
  rawInvoke<void>("task_chat_append", {
    taskKey,
    role,
    content,
    toolCalls: opts?.toolCalls,
    toolCallId: opts?.toolCallId,
  });

/** 清掉 task chat (rm file). 给"重新开始" 按钮用. */
export const taskChatClear = (taskKey: string) =>
  rawInvoke<void>("task_chat_clear", { taskKey });

/** P3.3.12 (6/10): jsonl 文件 size (字节). 不存在返 0.
 *  advisor task chat summary cache 用 — size 一致 → 没新消息 → 复用 cached summary. */
export const taskChatSize = (taskKey: string) =>
  rawInvoke<number>("task_chat_size", { taskKey });
