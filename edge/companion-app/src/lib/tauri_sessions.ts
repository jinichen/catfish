/**
 * 会话 · 列举 / 详情 / 清理, 以及 P3.3.19 的 task ↔ session 关联 sidecar
 *
 * 2026-08-15 从 lib/tauri.ts 切出来 (1309 行超限)。纯搬迁, 逻辑一行未改。
 * 边界照抄原文件里作者早就画好的 `// ── xxx ──` 分节, 不是我另起的划分。
 *
 * lib/tauri.ts 现在是 barrel, 只做 re-export —— 62 个调用方一行没动。
 */

import { invoke as rawInvoke } from "@tauri-apps/api/core";
import type { SessionMeta, SessionDetail } from "../types/session";

// ── sessions (read) ──────────────────────────────────────
export const listSessions = () => rawInvoke<SessionMeta[]>("sessions_list");
/** 5/5: sessions_list 受 MAX_SESSIONS=100 限制, 这个返 state.db 真实总行数. */
export const countSessions = () => rawInvoke<number>("sessions_count");
export const getSession = (id: string) =>
  rawInvoke<SessionDetail>("sessions_get", { id });

// ── sessions (delete) — BL-SESSION-MGMT C (5/15) ──────
/** 软删 — 标 deleted_at, sidebar 立即隐藏. 30 天 grace 期可 restore. */
export const sessionSoftDelete = (id: string) =>
  rawInvoke<void>("session_soft_delete", { id });
/** 恢复软删的 session. */
export const sessionRestore = (id: string) =>
  rawInvoke<void>("session_restore", { id });

export interface BulkDeleteResult {
  sessionIds: string[];  // preview 时全清单, 真删时前 50 个
  total: number;
  maxMessages: number;
  maxAgeHours: number;
}

/** Bulk 软删短 session.
 *  preview=true 只返清单不删 (UI 弹窗预览用), preview=false 真删. */
export const sessionsBulkDeleteShort = (args: {
  maxMessages: number;  // 默认建议 3
  maxAgeHours: number;  // 默认建议 168 (7 天)
  preview: boolean;
}) => rawInvoke<BulkDeleteResult>("sessions_bulk_delete_short", args);


// ── sessions (write) —— Plan C Week 2 持久化 ──
export interface SessionCreateInput {
  model: string;
  title?: string;
  systemPrompt?: string;
}
export interface SessionCreateOutput {
  id: string;
  startedAt: number;
}
export interface MessageAppendInput {
  sessionId: string;
  role: string;
  content: string;
  /** JSON 字符串 (OpenAI tool_calls 格式), 没有则不传 */
  toolCalls?: string;
  toolCallId?: string;
  toolName?: string;
  tokenCount?: number;
  finishReason?: string;
}
export interface SessionFinalizeInput {
  sessionId: string;
  endReason?: string;
  inputTokens?: number;
  outputTokens?: number;
}
export const sessionCreate = (input: SessionCreateInput) =>
  rawInvoke<SessionCreateOutput>("session_create", { input });
export const sessionMessageAppend = (input: MessageAppendInput) =>
  rawInvoke<number>("session_message_append", { input });
export const sessionFinalize = (input: SessionFinalizeInput) =>
  rawInvoke<void>("session_finalize", { input });
export const sessionUpdateTitle = (sessionId: string, title: string) =>
  rawInvoke<void>("session_update_title", { sessionId, title });
export const sessionCheck = (sessionId: string) =>
  rawInvoke<string | null>("session_check", { sessionId });

// ── P3.3.19 (6/11) C 路线 Phase 1: task ↔ session 关联 sidecar ──────────
//
// 不动 hermes 上游 sessions 表, 加 catfish_session_metadata 隔离自家.
// 1 task 可关联 N session (老历史 + 新对话), 查时按 started_at DESC 取 latest.

export interface SessionTaskAssoc {
  sessionId: string;
  taskUid: string | null;
  createdAt: number;
  updatedAt: number;
}

/** 绑 / 改 session 跟 task 的关联. null 取消关联 */
export const sessionSetTaskUid = (sessionId: string, taskUid: string | null) =>
  rawInvoke<void>("session_set_task_uid", { sessionId, taskUid });

/** 查某 session 关联的 task_uid (null = 没绑) */
export const sessionGetTaskUid = (sessionId: string) =>
  rawInvoke<string | null>("session_get_task_uid", { sessionId });

/** 查 task 关联的 latest session_id. None = 这 task 还没 session */
export const sessionGetByTaskUid = (taskUid: string) =>
  rawInvoke<string | null>("session_get_by_task_uid", { taskUid });

/** 列 task 关联的所有 session (按 started_at DESC) */
export const listSessionsByTaskUid = (taskUid: string) =>
  rawInvoke<SessionTaskAssoc[]>("list_sessions_by_task_uid", { taskUid });
