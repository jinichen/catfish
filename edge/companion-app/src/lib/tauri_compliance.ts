/**
 * 合规与身份 · 反馈 / 身份 / 员工自助 / 数据外发日志 / 审计 / 自我进化 /
 * 认证 / identity bundle
 *
 * 2026-08-15 从 lib/tauri.ts 切出来 (1309 行超限)。纯搬迁, 逻辑一行未改。
 * 边界照抄原文件里作者早就画好的 `// ── xxx ──` 分节, 不是我另起的划分。
 *
 * lib/tauri.ts 现在是 barrel, 只做 re-export —— 62 个调用方一行没动。
 */

import { invoke as rawInvoke } from "@tauri-apps/api/core";

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


// ── identity ─────────────────────────────────────────────
import type {
  IdentityInfo,
  SkillNamespace,
  McpServerEntry,
} from "../types/identity";
export const fetchIdentity = () => rawInvoke<IdentityInfo>("identity_info");
// 6/2 BL-SKILLS-CARD-SPLIT (鸿波): 拆 2 个 fetcher.
//   fetchMySkills        → 扫 ~/.catfish/skills/ (员工自己 RecMode + propose_skill 生成的)
//   fetchInstalledSkills → 扫 catfish 仓库 skills/ + ~/.hermes/skills/ (内置 + 装的)
//   (7/17 BL-DEADCODE-SWEEP: 老 fetchSkills / list_skills 死链已删)
export const fetchMySkills = () => rawInvoke<SkillNamespace[]>("list_my_skills");
export const fetchInstalledSkills = () => rawInvoke<SkillNamespace[]>("list_installed_skills");
export const fetchMcpServers = () =>
  rawInvoke<McpServerEntry[]>("list_mcp_servers");

// ── 6/8 BL-EMPLOYEE-SELF-SERVE A1+A2+A4 ──────────────────────
//
// 跟 manifesto 公理 1 (员工主权) 一致 — IT 无远程触发能力, 员工通过 catfish UI
// 自己点 button 才执行. spec: docs/EMPLOYEE-SELF-SERVE-TOOLS-SPEC.md

export interface ResetSummary {
  conversationsDeleted: number;
  recordingsDeleted: number;
  wikiFilesDeleted: number;
  skillsDeleted: number;
  bytesFreedTotal: number;
  trashPath: string; // empty if preview
}

/** A1: 重置 — preview (不真删, 只算数). */
export const selfServePreviewReset = () =>
  rawInvoke<ResetSummary>("self_serve_preview_reset");

/** A1: 重置 — 真执行. confirmation 必须是 "我确认" 才行. */
export const selfServeExecuteReset = (confirmation: string) =>
  rawInvoke<ResetSummary>("self_serve_execute_reset", { confirmation });

/** A1: 重置 — 5s 内 undo (trash 还在). 失败 = trash 已过期或目标位置已有数据. */
export const selfServeRestoreReset = (trashPath: string) =>
  rawInvoke<void>("self_serve_restore_reset", { trashPath });

export interface ExportOptions {
  includeConversations: boolean;
  includeRecordings: boolean;
  includeWiki: boolean;
  includeSkills: boolean;
  includeStrategicDocs: boolean;
  includeConfig: boolean;
}

export interface ExportResult {
  outputPath: string;
  bytesWritten: number;
  filesIncluded: number;
}

/** A2: 导出 — 打包 ~/.catfish/ 子目录到 .tar.gz. outputPath 由员工 Tauri dialog 选. */
export const selfServeExportData = (
  options: ExportOptions,
  outputPath: string,
) => rawInvoke<ExportResult>("self_serve_export_data", { options, outputPath });

// ── A4: 数据外发日志 (transparent log) ──────────────────────

export interface TransparentLogRecord {
  method: string;
  url: string;
  requestBody?: string;
  status?: number;
  responseBytes?: number;
  responseSummary?: string;
  error?: string;
  category?: string;
}

export interface TransparentLogEntry {
  id: number;
  tsRequest: string;
  tsResponse?: string;
  method: string;
  url: string;
  requestBytes: number;
  responseBytes: number;
  status?: number;
  requestPayloadPreview?: string;
  requestPayloadFullAvailable: boolean;
  responseSummary?: string;
  error?: string;
  category?: string;
}

export interface TransparentLogQueryResult {
  entries: TransparentLogEntry[];
  total: number;
  bytesUploadedTotal: number;
  bytesDownloadedTotal: number;
}

/** A4: 记 1 个 outbound 请求 (me.ts middleware 调). */
export const transparentLogRecord = (req: TransparentLogRecord) =>
  rawInvoke<number>("transparent_log_record", { req });

/** A4: 查询 log (Dashboard outbound log card 用).
 *
 * 6/8 Phase 2: 加 urlFilter (SQL LIKE) + offset 支持分页. 后端 query_blocking
 * 跟 filter 一起算 total / bytes_uploaded_total / bytes_downloaded_total —
 * 分页器算页数跟 stat 一致.
 */
export const transparentLogQuery = (
  since?: string,
  category?: string,
  urlFilter?: string,
  limit?: number,
  offset?: number,
) =>
  rawInvoke<TransparentLogQueryResult>("transparent_log_query", {
    since,
    category,
    urlFilter,
    limit,
    offset,
  });

/** A4: 导出当前 filter 的 log 到 CSV. 返写入行数. */
export const transparentLogExportCsv = (
  outputPath: string,
  since?: string,
  category?: string,
  urlFilter?: string,
) =>
  rawInvoke<number>("transparent_log_export_csv", {
    outputPath,
    since,
    category,
    urlFilter,
  });

/** A4: GC 过期 (9 天前). 启动时跑一次. */
export const transparentLogGc = () => rawInvoke<number>("transparent_log_gc");


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


// ── identity bundle (BL-IDENTITY-INJECT-DECOUPLE 5/26) ────
//
// 给 /v1/chat/completions body 字段 `_catfish_identity_bundle` 透传准备.
// 5/26 audit 砍 gateway 自读 ~/.hermes/SOUL.md 等后, 由 Companion (员工 mac 本地
// 跑, 读自己 fs 合规) 准备好打包传给 gateway. caller 见 lib/chat.ts.

export interface IdentityBundle {
  /** ~/.hermes/SOUL.md (核心人格) */
  soul: string;
  /** ~/.hermes/SOUL_<CUST>.md (客户特定 — env CATFISH_CUSTOMER 默认 FFCS) */
  soul_customer: string;
  /** ~/.hermes/SOUL_BROWSER.md (浏览器场景纪律) */
  soul_browser: string;
  /** ~/.hermes/SOUL_EXECUTE_CODE.md (代码执行场景纪律) */
  soul_execute_code: string;
  /** ~/.hermes/USER.md (用户长期 memory) */
  user_memory: string;
  /** concat 好的 ~/.hermes/memories/*.md (按文件名排序) */
  memory_dir: string;
}

export const fetchIdentityBundle = () =>
  rawInvoke<IdentityBundle>("identity_bundle");

/** BL-JOURNAL-TODO-EDIT-CHAT Stage 1 (5/20): journal CRUD 给 LLM tool calling 用.
 * 双重定位 line + text_hint 防误伤 (员工改 journal 后行号偏移). */
// P3.4.7b (6/15 鸿波): origin 路由 — weekly→current_todos.md, journal→employee_journal.md.
//   None (老 caller 兼容) → Rust 端双文件试. TodosDetail 应该传 t.origin (P3.4.7a 加).
export const journalMarkTodoDone = (
  line: number,
  textHint: string,
  origin?: "weekly" | "journal",
) =>
  rawInvoke<string>("journal_mark_todo_done", { line, textHint, origin: origin ?? null });

export const journalDeleteTodo = (
  line: number,
  textHint: string,
  origin?: "weekly" | "journal",
) =>
  rawInvoke<string>("journal_delete_todo", { line, textHint, origin: origin ?? null });
