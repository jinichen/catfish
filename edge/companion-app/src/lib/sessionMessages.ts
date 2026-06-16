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
 * P3.5.8 (6/16 鸿波 BL-FILE-SESSION-INDEX-V1 Phase 2): 加 async 版
 * loadSessionMessagesAsChatAsync — resume 时 query attachments.db, 对每条 image
 * attachment 调 attachment_load_base64 拿 base64, 填回 message.attachments.
 * 让 chatWire.toWire 走 multipart 分支带 image_url 给上游 vision LLM. 同步版
 * 保留 (兼容 useTaskChat / 老调用方; 它们用不上 image resume).
 *
 * 单独 file (不进 lib/chat.ts) 避 cyclic: lib/chat.ts 已 import store/chat.ts
 */

import { invoke } from "@tauri-apps/api/core";
import type { SessionDetail, SessionMessage } from "../types/session";
import type { Attachment, ChatMessage, ToolCall } from "../types/chat";

/** 对齐 store/chat.ts SessionAttachment + Rust commands/attachments.rs AttachmentRow.
 *  这里 inline 重声明避 cyclic (store/chat.ts → import sessionMessages.ts).
 *  schema 对得上 attachment_list_by_session 返的 rows. */
interface SessionAttachmentRow {
  id: string;
  userId: string;
  sessionId: string;
  messageId: string;
  kind: string;             // "image" | "file" | "audio"
  fileKind: string | null;
  name: string;
  mimeType: string | null;
  sizeBytes: number | null;
  keptPath: string | null;
  parsedTextPath: string | null;
  meta: string | null;
  createdAt: number;
}

interface AttachmentLoadBase64Result {
  base64: string;
  sizeBytes: number;
}

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
 *
 *  P3.5.8: 这是同步版, **不还原 image base64** (历史 image attachments 会失踪).
 *  需要历史看图的调用方走 loadSessionMessagesAsChatAsync.
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

// ─── P3.5.8 Phase 2: async resume + image base64 还原 ─────────────────

/** db.message.id (SessionMessage.id) → attachment_record 写入时存的 messageId 字段.
 *
 *  useChat persistMessage 写 state.db messages 表后, 拿到 rowid 当 messageId
 *  传给 attachment_record. resume 时 SessionMessage.id 是 state.db rowid,
 *  attachments.db 里 messageId 也是同一个 rowid → 直接 join.
 *
 *  但 dbMessageToChat 把 SessionMessage.id 加了 "db-" 前缀 (line "id: `db-${m.id}`"),
 *  这里 join 时用原始 m.id (不带前缀).
 */
function indexAttachmentsByMessageId(
  rows: SessionAttachmentRow[],
): Map<string, SessionAttachmentRow[]> {
  const idx = new Map<string, SessionAttachmentRow[]>();
  for (const r of rows) {
    if (!r.messageId) continue;
    const list = idx.get(r.messageId) ?? [];
    list.push(r);
    idx.set(r.messageId, list);
  }
  return idx;
}

/** 把一行 SessionAttachmentRow 转 Attachment, image kind 拉 base64.
 *
 *  file kind 不拉 base64 (file 走 preview-only mode, base64 没意义). 仅恢复
 *  metadata 让 UI chip 显示. file 真要给 LLM 看走 catfish_read_attachment.
 *
 *  失败 (文件不存在 / 路径不在白名单 / size 超 20MB) → 返 null, caller 跳过.
 */
async function rowToAttachment(
  row: SessionAttachmentRow,
): Promise<Attachment | null> {
  // 必填: kind, name, mimeType. 没 kind / name 行视为坏数据
  if (!row.kind || !row.name) return null;

  const kind = row.kind === "image" ? "image" : "file";
  const att: Attachment = {
    kind,
    mimeType: row.mimeType ?? "application/octet-stream",
    name: row.name,
    sizeBytes: row.sizeBytes ?? 0,
  };

  if (kind === "file") {
    // file: 只恢复 metadata 让 UI chip 显示 + 让 LLM 知道有这文件
    if (row.fileKind) att.fileKind = row.fileKind;
    if (row.keptPath) att.keptPath = row.keptPath;
    if (row.parsedTextPath) att.parsedTextPath = row.parsedTextPath;
    if (row.meta) {
      try {
        att.meta = JSON.parse(row.meta);
      } catch {
        // meta 坏了不致命
      }
    }
    return att;
  }

  // image: 必须 keptPath 才能拉 base64
  if (!row.keptPath) {
    console.warn(
      "[BL-FILE-SESSION-INDEX-V1 Phase 2] image attachment 无 keptPath, 跳过 base64 还原:",
      row.name,
    );
    return att;  // 至少返 metadata, UI 还能显示 (但 LLM 看不到图)
  }

  try {
    const result = await invoke<AttachmentLoadBase64Result>(
      "attachment_load_base64",
      { keptPath: row.keptPath },
    );
    att.base64 = result.base64;
    if (!att.sizeBytes && result.sizeBytes) {
      att.sizeBytes = result.sizeBytes;
    }
    return att;
  } catch (e) {
    console.warn(
      "[BL-FILE-SESSION-INDEX-V1 Phase 2] attachment_load_base64 失败, image 不可见:",
      row.name,
      e,
    );
    return att;  // metadata 仍返, base64 缺 LLM 看不到
  }
}

/** 整 session 全 messages 转 ChatMessage[], 含 tool result join + image base64 还原.
 *
 *  P3.5.8 (6/16 鸿波 BL-FILE-SESSION-INDEX-V1 Phase 2):
 *  1. 跑同步版 loadSessionMessagesAsChat 拿基础 ChatMessage[]
 *  2. invoke attachment_list_by_session 拿这 session 所有 attachments
 *  3. group by message_id, 对每条 message 关联 attachments
 *  4. 对每个 image kind 调 attachment_load_base64 拉 base64 填回
 *  5. 填回 message.attachments 让 chatWire.toWire 走 multipart
 *
 *  调用方: store/chat.ts loadSession action.
 *
 *  失败 ok: 任一 attachment 读不到, caller 至少有基础 messages (text content
 *  + 占位符 "[📎 1 张图]" 还在). 鲁棒, 不阻塞 session 切换.
 */
export async function loadSessionMessagesAsChatAsync(
  detail: SessionDetail,
): Promise<ChatMessage[]> {
  const mapped = loadSessionMessagesAsChat(detail);

  // 拉 attachments rows. 失败 (db 不存在 / 没权限) 不致命, 返空数组继续.
  let attachmentRows: SessionAttachmentRow[] = [];
  try {
    attachmentRows = await invoke<SessionAttachmentRow[]>(
      "attachment_list_by_session",
      { sessionId: detail.meta.id },
    );
  } catch (e) {
    console.warn(
      "[BL-FILE-SESSION-INDEX-V1 Phase 2] attachment_list_by_session 失败, 跳 image resume:",
      e,
    );
    return mapped;
  }

  if (attachmentRows.length === 0) return mapped;

  // group by messageId
  const byMsgId = indexAttachmentsByMessageId(attachmentRows);

  // 对每条 message 关联 attachments + 还原 base64
  // SessionMessage.id 是 state.db rowid (number / string), dbMessageToChat 加 "db-" 前缀.
  // attachments.db 里 messageId 是当时 useChat 写时拿到的 rowid (没前缀).
  for (const msg of mapped) {
    // msg.id 是 `db-${SessionMessage.id}`, 剥前缀拿原始 id
    const dbId = msg.id.startsWith("db-") ? msg.id.slice(3) : msg.id;
    const rows = byMsgId.get(dbId);
    if (!rows || rows.length === 0) continue;

    const attachments: Attachment[] = [];
    for (const row of rows) {
      const att = await rowToAttachment(row);
      if (att) attachments.push(att);
    }
    if (attachments.length > 0) {
      msg.attachments = attachments;
    }
  }

  return mapped;
}
