/** P3.3.7 Phase 2 (6/10 鸿波): task-scoped chat 持久化 TS wrapper.
 *
 * Tauri commands 走 ~/.catfish/task_chat/<key>.jsonl. 详见
 * src-tauri/src/commands/task_chat.rs.
 *
 * task_key = sanitize(task.title), 跨天同标题 task 共享 chat (LLM 重生成同标题
 * task 时, 之前的 chat 历史仍能拉回来).
 */

import { invoke as rawInvoke } from "@tauri-apps/api/core";

export interface TaskChatMsg {
  role: "user" | "assistant" | "system";
  content: string;
  ts: string;
}

/** 读 task chat 全部历史. 没历史返空 []. */
export const taskChatGet = (taskKey: string) =>
  rawInvoke<TaskChatMsg[]>("task_chat_get", { taskKey });

/** Append 一条到 task chat. 自动填 ts. */
export const taskChatAppend = (taskKey: string, role: TaskChatMsg["role"], content: string) =>
  rawInvoke<void>("task_chat_append", { taskKey, role, content });

/** 清掉 task chat (rm file). 给"重新开始" 按钮用. */
export const taskChatClear = (taskKey: string) =>
  rawInvoke<void>("task_chat_clear", { taskKey });
