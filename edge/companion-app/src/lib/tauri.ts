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
// BL-TODO-BRIDGE-STORE (5/16): sessionId 可选, 用于 per-session stateful tool 注入
// (hermes todo 工具按 session 各自 TodoStore). 不传 → backend 走 __default__ 全局 store.
export const toolBridgeCallTool = (
  name: string,
  args: Record<string, unknown>,
  sessionId?: string,
) =>
  rawInvoke<ToolCallResult>("tool_bridge_call_tool", {
    name,
    args,
    sessionId,  // Tauri 命令 camelCase ↔ Rust snake_case 自动转换
  });

// BL-DASHBOARD-HERMES-MEMORY-CARD (5/16): 读 hermes 0.13 真活 memory 文件
export interface HermesMemoryView {
  user_entries: string[];
  memory_entries: string[];
  total_bytes: number;
  user_char_limit: number;
  memory_char_limit: number;
  user_file_path: string;
  memory_file_path: string;
}
export const hermesMemoryRead = () =>
  rawInvoke<HermesMemoryView>("hermes_memory_read");

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
export const petIsVisible = () => rawInvoke<boolean>("pet_is_visible");

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

// ── email digest (BL-COMPANION-EMAIL-DIGEST 5/18) ─────────
// shell out catfish-email CLI, 返 raw JSON 字符串. 前端 JSON.parse 自取字段.
export const emailDigestFetch = (limit?: number) =>
  rawInvoke<string>("email_digest_fetch", { limit: limit ?? null });
export const emailAccountsFetch = () =>
  rawInvoke<string>("email_accounts_fetch");
// BL-COMPANION-EMAIL-TAB (5/18): 邮件 tab 用的全列表 + 读单封 + 起草 + 评级 map.
export const emailListFetch = (unreadOnly: boolean, limit?: number) =>
  rawInvoke<string>("email_list_fetch", { unreadOnly, limit: limit ?? null });
export const emailReadMessage = (id: string) =>
  rawInvoke<string>("email_read_message", { id });
// BL-EMAIL-MARK-READ (5/18): 单独标已读 / 反向标未读. CLI read 默认已自动标,
// 这个 wrapper 是给右键 "标已读" / 批量场景用 (不读正文).
export const emailMarkRead = (id: string, read: boolean = true) =>
  rawInvoke<string>("email_mark_read", { id, read });
// BL-EMAIL-DELETE (5/18): 把邮件移到客户端 Trash 文件夹 (软删, 不彻底).
// Apple Mail 支持; Foxmail Mac 不支持 (Promise reject 含友好提示).
export const emailDeleteMessage = (id: string) =>
  rawInvoke<string>("email_delete_message", { id });
export interface CreateDraftArgs {
  to: string;
  subject: string;
  body: string;
  cc?: string;
  bcc?: string;
  inReplyTo?: string;
  account?: string;
}
export const emailCreateDraft = (args: CreateDraftArgs) =>
  rawInvoke<string>("email_create_draft", {
    to: args.to,
    cc: args.cc ?? null,
    bcc: args.bcc ?? null,
    subject: args.subject,
    body: args.body,
    inReplyTo: args.inReplyTo ?? null,
    account: args.account ?? null,
  });
/** id → "急" | "中" | "低" map, scheduler 后台评级缓存. */
export const emailUrgencyMap = () =>
  rawInvoke<Record<string, string>>("email_urgency_map");

/** BL-EMAIL-URGENCY-BADGE (5/18): 前端主动评级一批邮件 (历史邮件也能评).
 * 已 cache 的跳过, 只评新 id. 返完整 cache map.
 * 性能: 一次评 batch (LLM 调一次), 前端最好 batch <=30 避免 prompt 太长. */
export const emailClassifyNow = (
  items: Array<{
    id: string;
    subject: string;
    sender: string;
    account?: string;
    date?: string;
    is_read?: boolean;
  }>,
) => rawInvoke<Record<string, string>>("email_classify_now", { items });

/** catfish-email list --json 单条 message 的 schema. 字段跟 base.py Message dataclass 对齐. */
export interface EmailDigestItem {
  id: string;
  account: string;
  folder: string;
  subject: string;
  sender: string;
  date: string;          // ISO-8601 UTC
  is_read: boolean;
  has_attachments: boolean;
  body_text: string;     // list 场景为 snippet
}
/** catfish-email accounts --json 单条 schema.
 * BL-EMAIL-MULTI-CLIENT (5/18): 多客户端时每个 account 多了 client 字段标识来源.
 */
export interface EmailAccountItem {
  name: string;
  address: string;
  is_default: boolean;
  client?: string;  // apple_mail / foxmail_mac, 老 CLI 输出可能没这字段
}

// ── 桌宠跨窗通信 (5/6: Tauri 跨 webview event 不通, 走 Rust polling buffer) ─
export interface PetEmitBubbleDiag {
  pet_window_exists: boolean;
  pet_visible: boolean;
  /** 已 push 到 polling buffer, pet.tsx 300ms 内拉走 */
  queued: boolean;
}
/** 主窗调: 桌宠头顶冒气泡, 8s 后自动收. */
export const petEmitBubble = (text: string, agentName?: string) =>
  rawInvoke<PetEmitBubbleDiag>("pet_emit_bubble", { text, agentName });
/** 主窗调: 切桌宠 4 状态 (idle / thinking / running / done). */
export const petEmitStatus = (status: "idle" | "thinking" | "running" | "done") =>
  rawInvoke<void>("pet_emit_status", { status });

// ── file (Phase 2 优雅下载: skill 生成的文件,在 Finder 打开/显示) ───
/** 在 Finder/资源管理器里高亮选中文件 (macOS: open -R). */
export const revealInFinder = (path: string) =>
  rawInvoke<void>("reveal_in_finder", { path });
/** 用系统默认 app 打开文件 (macOS: open <path>). */
export const openFile = (path: string) =>
  rawInvoke<void>("open_file", { path });
