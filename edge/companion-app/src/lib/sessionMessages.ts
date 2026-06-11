/** Session messages ↔ ChatMessage 转换 helper.
 *
 * 抽自 store/chat.ts (P3.3.19 C Phase 2b, 6/11), 给 useTaskChat / useChat 共享.
 *
 * 关键 logic (P27.3 真根因 fix, 6/5 marathon 25h debug 学到):
 * - SessionMessage.toolCalls 是 JSON 字符串, 反序列化成 ToolCall[]
 * - tool role 消息的 content 是 result 字符串, 历史加载时必须 join 回
 *   对应 assistant.tool_calls[i].result (按 tool_call_id 索引)
 * - 否则 ChatToolCall 渲染时 call.result === undefined → 显 (空) →
 *   approval button regex match 空字符串 fail → button 不弹
 *
 * 单独 file (不进 lib/chat.ts) 避 cyclic: lib/chat.ts 已 import store/chat.ts
 */

import type { SessionDetail, SessionMessage } from "../types/session";
import type { ChatMessage, ToolCall } from "../types/chat";

function safeParseArgs(raw: unknown): Record<string, unknown> {
  if (raw == null) return {};
  if (typeof raw === "object") return raw as Record<string, unknown>;
  if (typeof raw === "string") {
    try {
      const v = JSON.parse(raw);
      return v && typeof v === "object" ? (v as Record<string, unknown>) : {};
    } catch {
      return {};
    }
  }
  return {};
}

/** SessionMessage (DB 行) -> ChatMessage (UI 运行时) 单条映射.
 *
 *  - tool_calls JSON 反序列化成 ToolCall[], status 一律 "done" (历史已完成)
 *  - 不解析 tool 角色消息的 result 字段, 直接当 content 显示
 *  - 注: tool result 回填到 assistant.tool_calls[i].result 是
 *    loadSessionMessagesAsChat 第二遍做的 (P27.3)
 */
export function dbMessageToChat(m: SessionMessage): ChatMessage {
  let toolCalls: ToolCall[] | undefined;
  if (m.toolCalls) {
    try {
      const parsed = JSON.parse(m.toolCalls);
      if (Array.isArray(parsed)) {
        toolCalls = parsed.map((tc, i) => ({
          id: String(tc.id ?? `historical-${m.id}-${i}`),
          name: String(tc.function?.name ?? tc.name ?? "(unknown)"),
          args: safeParseArgs(tc.function?.arguments ?? tc.arguments),
          status: "done" as const,
        }));
      }
    } catch {
      // 解析坏了不致命, 历史记录里有畸形 tool_calls 就忽略
    }
  }
  const role = (["user", "assistant", "system", "tool"] as const).includes(
    m.role as never,
  )
    ? (m.role as ChatMessage["role"])
    : "assistant";
  return {
    id: `db-${m.id}`,
    role,
    content: m.content,
    tool_calls: toolCalls,
    tool_call_id: m.toolCallId,
    ts: m.timestamp,
    status: "done",
  };
}

/** 整 session 全 messages 转 ChatMessage[], 含 tool result join (P27.3).
 *
 *  调用方: useChat.loadSession (store action) / useTaskChat mount (P3.3.19) /
 *  其他需要 resume 一条 hermes session 的地方.
 */
export function loadSessionMessagesAsChat(detail: SessionDetail): ChatMessage[] {
  const mapped = detail.messages.map(dbMessageToChat);
  // 第二遍: tool_call_id → content 索引, 回填 assistant.tool_calls[i].result
  const toolResultByCallId = new Map<string, string>();
  for (const m of mapped) {
    if (m.role === "tool" && m.tool_call_id) {
      toolResultByCallId.set(m.tool_call_id, m.content);
    }
  }
  for (const m of mapped) {
    if (m.role === "assistant" && m.tool_calls?.length) {
      for (const tc of m.tool_calls) {
        const result = toolResultByCallId.get(tc.id);
        if (result !== undefined) {
          tc.result = result;
        }
      }
    }
  }
  return mapped;
}
