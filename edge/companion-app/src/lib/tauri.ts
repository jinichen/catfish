/**
 * 唯一的 invoke 出口。
 *
 * 任何前端文件需要调 Rust 都从这里 import，绝不直接 `import { invoke } from '@tauri-apps/api'`。
 * 这样：
 *   1. 命令名集中在一处，重命名一次性改完
 *   2. 可以加统一的错误处理 / 日志
 *   3. 类型签名集中，对照 Rust 端 #[tauri::command] 一目了然
 *
 * Rust 命令名和这里的 invoke 字符串必须严格对齐 —— Tauri 的命令名是全局唯一的，
 * 所以三个服务的 start/stop/status 用了 `<service>_<op>` 全限定命名。
 */

import { invoke as rawInvoke } from "@tauri-apps/api/core";
import type { ServiceStatus } from "../types/service";
import type { SessionMeta, SessionDetail } from "../types/session";
import type { CatalogResponse } from "../types/catalog";

// ── gateway ──────────────────────────────────────────────
export const gatewayStart = () => rawInvoke<void>("gateway_start");
export const gatewayStop = () => rawInvoke<void>("gateway_stop");
export const gatewayStatus = () => rawInvoke<ServiceStatus>("gateway_status");
export const gatewayGetDevToken = () =>
  rawInvoke<string>("gateway_get_dev_token");

// ── chrome ───────────────────────────────────────────────
export const chromeLaunch = () => rawInvoke<void>("chrome_launch");
export const chromeKill = () => rawInvoke<void>("chrome_kill");
export const chromeStatus = () => rawInvoke<ServiceStatus>("chrome_status");

// ── local_search ─────────────────────────────────────────
export const localSearchStart = () => rawInvoke<void>("local_search_start");
export const localSearchStop = () => rawInvoke<void>("local_search_stop");
export const localSearchStatus = () =>
  rawInvoke<ServiceStatus>("local_search_status");

// ── tool_bridge ──────────────────────────────────────────
export const toolBridgeStart = () => rawInvoke<void>("tool_bridge_start");
export const toolBridgeStop = () => rawInvoke<void>("tool_bridge_stop");
export const toolBridgeStatus = () =>
  rawInvoke<ServiceStatus>("tool_bridge_status");
export interface ToolInfo {
  name: string;
  description: string;
  /** snake_case 跟 Python tool-bridge 协议对齐;不要改 camelCase 否则解析空 */
  input_schema: Record<string, unknown>;
  emoji: string;
  toolset: string;
  available: boolean;
}
export interface ToolCallResult {
  ok: boolean;
  tool: string;
  result: unknown;
  error: string | null;
}
export const toolBridgeListTools = () =>
  rawInvoke<ToolInfo[]>("tool_bridge_list_tools");
export const toolBridgeCallTool = (name: string, args: Record<string, unknown>) =>
  rawInvoke<ToolCallResult>("tool_bridge_call_tool", { name, args });

// ── health ───────────────────────────────────────────────
export const fetchHealthz = () =>
  rawInvoke<{ status: string; service: string }>("healthz");
export const fetchCatalog = () => rawInvoke<CatalogResponse>("catalog");

// ── logs ─────────────────────────────────────────────────
// 默认 fromEnd=false: 先 dump 老日志, 然后 tail 新增 ——
// 员工切到 LogPanel 立刻能看到内容,不至于面对空白。
// 长期跑的 server 日志通常 <1MB,完整 dump 一次没什么开销。
export const tailLogs = (service: string, fromEnd = false) =>
  rawInvoke<void>("tail", { service, fromEnd });
export const stopTailLogs = (service: string) =>
  rawInvoke<void>("stop_tail", { service });

// ── sessions (read) ──────────────────────────────────────
export const listSessions = () => rawInvoke<SessionMeta[]>("sessions_list");
/** 5/5: sessions_list 受 MAX_SESSIONS=100 限制, 这个返 state.db 真实总行数. */
export const countSessions = () => rawInvoke<number>("sessions_count");
export const getSession = (id: string) =>
  rawInvoke<SessionDetail>("sessions_get", { id });

// ── BL-MM6 feedback (5/5 晚) ───────────────────────────────
export type FeedbackKind = "thumb_up" | "thumb_down" | "edit";

export interface FeedbackEvent {
  ts: number;
  kind: FeedbackKind;
  session_id: string;
  message_id: string;
  preview: string;
  comment?: string;
}

export interface FeedbackSummary {
  total: number;
  thumb_up: number;
  thumb_down: number;
  edit: number;
  recent_negative: FeedbackEvent[];
  file_size_bytes: number;
}

export const feedbackRecord = (args: {
  kind: FeedbackKind;
  sessionId: string;
  messageId: string;
  preview: string;
  comment?: string;
}) =>
  rawInvoke<void>("feedback_record", {
    kind: args.kind,
    sessionId: args.sessionId,
    messageId: args.messageId,
    preview: args.preview,
    comment: args.comment,
  });

export const feedbackSummary = () =>
  rawInvoke<FeedbackSummary>("feedback_summary");

export const feedbackClear = () => rawInvoke<void>("feedback_clear");

// ── BL-E27 spike: 桌宠副窗 ─────────────────────────────────
export const petShow = () => rawInvoke<void>("pet_show");
export const petHide = () => rawInvoke<void>("pet_hide");

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

// ── identity ─────────────────────────────────────────────
import type {
  IdentityInfo,
  SkillNamespace,
  McpServerEntry,
} from "../types/identity";
export const fetchIdentity = () => rawInvoke<IdentityInfo>("identity_info");
export const fetchSkills = () => rawInvoke<SkillNamespace[]>("list_skills");
export const fetchMcpServers = () =>
  rawInvoke<McpServerEntry[]>("list_mcp_servers");

// ── self-evolution ──────────────────────────────────────
import type { TodayLearningStats } from "../types/learning";
export const fetchTodayLearningStats = () =>
  rawInvoke<TodayLearningStats>("learning_today_stats");

// ── audit / telemetry ────────────────────────────────────
import type { AuditSummary } from "../types/audit";
export const fetchAuditSummary = () =>
  rawInvoke<AuditSummary>("audit_summary");

// ── auth (SSO Phase 1C) ──────────────────────────────────
import type { AuthState } from "../types/auth";
export const authWhoami = () => rawInvoke<AuthState>("auth_whoami");
export const authLogin = () => rawInvoke<AuthState>("auth_login");
export const authLogout = () => rawInvoke<void>("auth_logout");
export const authGetAccessToken = () =>
  rawInvoke<string | null>("auth_get_access_token");

// ── system ───────────────────────────────────────────────
export const openTerminal = (cwd?: string) =>
  rawInvoke<void>("open_terminal", { cwd });
export const sendNotification = (title: string, body: string) =>
  rawInvoke<void>("notify", { title, body });

// ── file (Phase 2 优雅下载: skill 生成的文件,在 Finder 打开/显示) ───
/** 在 Finder/资源管理器里高亮选中文件 (macOS: open -R). */
export const revealInFinder = (path: string) =>
  rawInvoke<void>("reveal_in_finder", { path });
/** 用系统默认 app 打开文件 (macOS: open <path>). */
export const openFile = (path: string) =>
  rawInvoke<void>("open_file", { path });
