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
import { config } from "./env";
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

/** P44 (6/5 鸿波 marathon) / P44.4 真根因 fix (6/6): Companion ApprovalButton
 *  onClick 调这个 — fetch hermes API server POST /v1/sessions/{sid}/approval
 *  (plugin P15.2 加的 route). 走 hermes daemon 进程内 resolve_gateway_approval,
 *  跟 P15 _approval_notify 在同一进程, _gateway_queues dict 共享.
 *
 *  老实现走 tool-bridge socket — 跨进程, _gateway_queues 是空 dict, resolve
 *  返 0, block 不解. 已改 fetch 模式.
 *
 *  session_key 从 SSE event `hermes.tool.progress` (status=approval_pending) 的
 *  approval_session_key 字段拿. choice ∈ "once"|"session"|"always"|"deny". */
export const toolBridgeChatApproval = async (
  sessionKey: string,
  choice: "once" | "session" | "always" | "deny",
): Promise<{ resolved?: number; choice?: string }> => {
  const url = `${config.backendUrl}/v1/sessions/${encodeURIComponent(sessionKey)}/approval`;
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (config.useHermes && config.hermesAuthHeader) {
    headers["Authorization"] = config.hermesAuthHeader;
  }
  const resp = await fetch(url, {
    method: "POST",
    headers,
    body: JSON.stringify({ choice }),
  });
  if (!resp.ok) {
    const text = await resp.text().catch(() => "");
    throw new Error(`chat_approval HTTP ${resp.status}: ${text.slice(0, 200)}`);
  }
  return (await resp.json()) as { resolved?: number; choice?: string };
};

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
// P3.3.49 (6/12): Rust 端直接改 ~/.hermes/memories/{USER,MEMORY}.md, 绕过
// hermes memory_tool silent fail. byte-exact match + trim() fallback.
export const hermesMemoryRemove = (target: "user" | "memory", entryText: string) =>
  rawInvoke<void>("hermes_memory_remove", { target, entryText });

// P3.3.54 (6/12 鸿波): 审计员看的 xlsx 多 sheet 导出.
export interface AuditExportResult {
  outputPath: string;
  bytesWritten: number;
  decisionsCount: number;
  toolCallsCount: number;
  outboundCount: number;
  chainOk: boolean;
  chainBrokenReason: string | null;
}
export const auditExportXlsx = (
  fromTs: string | null,
  toTs: string | null,
  includeDecisions: boolean,
  includeToolCalls: boolean,
  includeOutbound: boolean,
) =>
  rawInvoke<AuditExportResult>("audit_export_xlsx", {
    fromTs,
    toTs,
    includeDecisions,
    includeToolCalls,
    includeOutbound,
  });

// P3.3.58 (6/12 鸿波): 钓鱼邮件识别 — 批量查邮件 phishing 扫描结果.
export type PhishingSeverity = "high" | "medium" | "low" | "none";
export interface PhishingFlag {
  ruleId: string;
  severity: PhishingSeverity;
  category: string;
  reason: string;
  matchedText?: string | null;
}
export interface PhishingScanResult {
  messageId: string;
  scannedAt: string;
  flags: PhishingFlag[];
  highestSeverity: PhishingSeverity;
  llmVerdict?: string | null;
  llmReason?: string | null;
}
export const emailPhishingGet = (ids: string[]) =>
  rawInvoke<Record<string, PhishingScanResult>>("email_phishing_get", { ids });

/** P3.3.58 段 2B (6/12 鸿波): 前端 trigger 钓鱼扫描. 跟 emailClassifyNow 同款.
 *  items 跟 email_classify_now 同 shape (id/subject/sender/account/date/is_read).
 *  返完整 store snapshot. 已扫的跳过, 新 id 走 light scan + LLM batch.
 */
export const emailPhishingScanNow = (
  items: Array<{
    id: string;
    subject: string;
    sender: string;
    account?: string;
    date?: string;
    is_read?: boolean;
  }>,
) => rawInvoke<Record<string, PhishingScanResult>>("email_phishing_scan_now", { items });

// P3.3.53 (6/13 鸿波): 政治敏感规则引擎 — yaml 可配置, 默认关.
// 集团信安 / 党办下发 keywords_l1 / keywords_l2 / regex_patterns 后, 员工
// 打开邮件 detail pane 时本机扫. 默认 enabled=false 时 engineEnabled=false 返,
// 前端不显 badge / 红条.
export type PoliticalSeverity = "high" | "medium" | "low" | "none";
export type PoliticalFlagLevel = "l1_redline" | "l2_sensitive" | "regex";
export interface PoliticalFlag {
  ruleId: string;
  level: PoliticalFlagLevel;
  severity: PoliticalSeverity;
  matchedLabel: string;     // 关键词脱敏 (前 4 字 + 长度) 防日志回显
  excerpt?: string | null;  // 命中位置上下文 (前后 20 字)
}
export interface PoliticalScanResult {
  messageId: string;
  scannedAt: string;
  flags: PoliticalFlag[];
  highestSeverity: PoliticalSeverity;
  llmVerdict?: string | null;
  llmReason?: string | null;
  /** 引擎是否开启. false → flags 必空, UI 不挂 badge */
  engineEnabled: boolean;
}

/** detail pane 打开邮件时调 — 拿到 body 后扫. 已扫过的 id 直接返缓存. */
export const emailPoliticalScanNow = (
  id: string,
  subject: string,
  sender: string,
  body: string,
) => rawInvoke<PoliticalScanResult>("email_political_scan_now", {
  id, subject, sender, body,
});

/** 批量查已扫的结果 (从 POLITICAL_STORE), 没扫过的 id 不出现在返 map. */
export const emailPoliticalGet = (ids: string[]) =>
  rawInvoke<Record<string, PoliticalScanResult>>("email_political_get", { ids });

// P3.4.1 (6/13 鸿波): mcp OAuth token 本机存. callback 拿到 token 后立即落
// ~/.catfish/mcp/oauth-tokens/<token_ref_local> 文件 0600. 中央 0 持有.
export interface McpOauthTokenSaveResult {
  absPath: string;
  bytes: number;
}
export const mcpOauthTokenSave = (tokenRefLocal: string, accessToken: string) =>
  rawInvoke<McpOauthTokenSaveResult>("mcp_oauth_token_save", {
    tokenRefLocal,
    accessToken,
  });
export const mcpOauthTokenDelete = (tokenRefLocal: string) =>
  rawInvoke<void>("mcp_oauth_token_delete", { tokenRefLocal });

// P3.3.55 (6/12 鸿波): 审计视图 Tab 拿 raw jsonl row 列表.
export interface ToolCallRow {
  ts: string;
  tool: string;
  ok: boolean;
  error: string | null;
  latencyMs: number;
  argsPreview: string;
}
export const auditDecisionsRawRead = (
  fromTs: string | null,
  toTs: string | null,
  limit: number,
) => rawInvoke<Record<string, unknown>[]>("audit_decisions_raw_read", { fromTs, toTs, limit });

export const auditHermesJsonlRead = (
  fromTs: string | null,
  toTs: string | null,
  limit: number,
) => rawInvoke<ToolCallRow[]>("audit_hermes_jsonl_read", { fromTs, toTs, limit });

// P3.3.55: 校验哈希链
export interface ChainVerifyReport {
  ok: boolean;
  totalLines: number;
  expectedCount: number;
  verifiedLines: number;
  brokenAt: number | null;
  brokenReason: string | null;
  chainFirstSha256: string;
  chainLastSha256: string;
}
export const auditChainVerify = (jsonlPath: string) =>
  rawInvoke<ChainVerifyReport>("audit_chain_verify", { jsonlPath });

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

// ── BL-CATFISH-WIKI-MODE P1.2.2: chat 真 💾 button → wiki/queries/ 写盘 ──
export interface WikiQueryWriteResult {
  path: string;
  bytes: number;
}

export const wikiSaveChatMessage = (args: {
  date: string; // YYYY-MM-DD
  time: string; // HH:MM
  sessionId: string;
  messageId: string;
  userMessage: string;
  assistantResponse: string;
}) =>
  rawInvoke<WikiQueryWriteResult>("wiki_save_chat_message", {
    date: args.date,
    time: args.time,
    sessionId: args.sessionId,
    messageId: args.messageId,
    userMessage: args.userMessage,
    assistantResponse: args.assistantResponse,
  });

// ── BL-CATFISH-WIKI-MODE P3.3.2: wiki read API ──
export interface WikiFileInfo {
  rel_path: string;
  kind: "entity" | "concept" | "query";
  slug: string;
  title: string;
  subtype: string | null;
  tags: string[];
  related: string[];
  sources: string[];
  size_bytes: number;
  mtime: number;
}

export interface WikiFileFull {
  info: WikiFileInfo;
  content: string;
  frontmatter: string;
  body: string;
}

// P37 (6/5 鸿波): wiki 全文搜索 — BM25 + title/tag scoring
export interface WikiSearchHit {
  rel_path: string;
  title: string;
  kind: string;
  score: number;
  snippet: string;
  matched_in: string[];
}

export const wikiSearchText = (query: string) =>
  rawInvoke<WikiSearchHit[]>("wiki_search_text", { query });

// P38 (6/5 鸿波): wiki 语义搜索 (本机 BGE-M3 ONNX). model 未装时 hits=[] + message 提示装法.
export interface WikiSemanticHit {
  rel_path: string;
  title: string;
  kind: string;
  score: number;
  snippet: string;
}
export interface WikiSemanticResult {
  hits: WikiSemanticHit[];
  model_loaded: boolean;
  indexed_count: number;
  message: string;
}

export const wikiSearchSemantic = (query: string, topK?: number) =>
  rawInvoke<WikiSemanticResult>("wiki_search_semantic", { query, topK });

export const wikiListFiles = () => rawInvoke<WikiFileInfo[]>("wiki_list_files");
export const wikiReadFile = (relPath: string) =>
  rawInvoke<WikiFileFull>("wiki_read_file", { relPath });

// P3.3.18 Phase 4 P2 (6/10): 卸载本机部门 wiki 副本 (软删 → wiki-shared/.trash/)
export const wikiUninstallShared = (relPath: string) =>
  rawInvoke<WikiWriteResult>("wiki_uninstall_shared", { relPath });

// P3.3.18 Phase 4 P2 (6/10): 敏感词文件 onboarding (catfish_wiki_publish 扫用)
export interface SensitiveTermsCheck {
  exists: boolean;
  path: string;
  created: boolean;  // true = 本次刚创建模板
}
export const wikiSensitiveTermsEnsure = () =>
  rawInvoke<SensitiveTermsCheck>("wiki_sensitive_terms_ensure");

// P3.3.18 Phase 4 (6/10): 扫 ~/.catfish/wiki-shared/ 已装部门 wiki
export interface InstalledWikiSharedInfo {
  relPath: string;          // wiki-shared/dept/<name>/<file_id>.md (相对 ~/.catfish/)
  namespace: string;        // dept/finance
  fileId: string;           // hub 分配的 UUID
  title: string;
  kind: string;             // entity | concept | query
  publishedBy: string;
  publishedAt: string;      // ISO-8601 or ""
  installedAt: string;      // ISO-8601 or ""
  sizeBytes: number;
}
export const listInstalledWikiShared = () =>
  rawInvoke<InstalledWikiSharedInfo[]>("list_installed_wiki_shared");

// ── BL-CATFISH-WIKI-MODE P3.3.7: wiki write API ──
export interface WikiWriteResult {
  rel_path: string;
  bytes: number;
  created: boolean;
}

export const wikiCreateEntityOrConcept = (args: {
  kind: "entity" | "concept";
  title: string;
  subtype: string;
  tags: string[];
  related: string[];
  body: string;
}) => rawInvoke<WikiWriteResult>("wiki_create_entity_or_concept", args);

export const wikiUpdateFile = (relPath: string, content: string) =>
  rawInvoke<WikiWriteResult>("wiki_update_file", { relPath, content });

/** P3.3.4 (6/9 鸿波): 软删 entity/concept/query → mv 到 wiki/.trash/<ts>-原名.md.
 * list/graph 立即看不到, 想 restore 自己 Finder 把文件 mv 回 entities/. */
export const wikiDeleteFile = (relPath: string) =>
  rawInvoke<WikiWriteResult>("wiki_delete_file", { relPath });

// P28 / P29 (6/5 鸿波): Companion Dashboard 改 gateway/identity URL
// P3.4.1 (6/13 hb): 砍 secret_broker_url — 中央 secret-broker 服务删, OAuth
// token 改 Companion 本机存. ServerConfigCard 不再让员工配 broker URL.
export interface ServerConfig {
  gateway_url: string;
  gateway_token: string;
  token_source: "yaml" | "env" | "none";
  identity_url: string;
  // secret_broker_url 字段砍, 旧 build 兼容靠 optional
  secret_broker_url?: string;  // deprecated, 永远不返
}

export const readServerConfig = () =>
  rawInvoke<ServerConfig>("read_server_config");

export const writeServerConfig = (
  gatewayUrl: string,
  gatewayToken: string,
  identityUrl?: string,
) =>
  rawInvoke<void>("write_server_config", {
    gatewayUrl,
    gatewayToken,
    identityUrl,
  });

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
//   fetchSkills          → 保留作 backward compat (内部转 installed)
export const fetchSkills = () => rawInvoke<SkillNamespace[]>("list_skills");
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

// ── E7 phase 2 (6/6): skill 安装/卸载, MCP 接入/移除 ───────────
//
// 流程:
//   - installSkillFromUrl: 走 `npx -y skills add <url>` (Tauri spawn npx subprocess),
//     返 stdout / stderr / exitCode 让 UI 显安装日志
//   - uninstallSkill: 移到 ~/.catfish/.trash/skills/<ts>/, 返 trashPath + originalPath,
//     UI 5 秒 toast 内可调 restoreSkill 恢复
//   - restoreSkill: undo button onClick, 5 秒内有效, 把 trash 内的 skill 移回原路径
//   - addMcpServer / removeMcpServer: 改 ~/.hermes/config.yaml, 重启 hermes 后生效
//     拒绝员工动 catfish-* 命名 (主链路核心 — 后端 hard reject)

export interface InstallResult {
  success: boolean;
  stdout: string;
  stderr: string;
  exitCode: number | null;
}

export interface UninstallResult {
  trashPath: string;
  originalPath: string;
}

export const installSkillFromUrl = (url: string) =>
  rawInvoke<InstallResult>("install_skill_from_url", { url });

// P3.3.23 (6/11): 装外部 skill zip (ClawHub / Anthropic .skill / 任何 SKILL.md zip).
//   zipBytes 走 Vec<u8> (从 File.arrayBuffer() → Array.from(new Uint8Array(buf))).
//   namespace 默认 "external", 装到 ~/.catfish/skills/<ns>/<slug>/.
//   warnings 含跳过的非白名单文件名 (.exe / .py 等被滤).
export interface InstallSkillFromZipResult {
  success: boolean;
  installedPath: string;
  filesCount: number;
  warnings: string[];
}

export const installSkillFromZip = (zipBytes: number[], namespace?: string) =>
  rawInvoke<InstallSkillFromZipResult>("install_skill_from_zip", {
    zipBytes,
    namespace: namespace ?? null,
  });

export const uninstallSkill = (skillPath: string) =>
  rawInvoke<UninstallResult>("uninstall_skill", { skillPath });

export const restoreSkill = (trashPath: string, originalPath: string) =>
  rawInvoke<void>("restore_skill", { trashPath, originalPath });

export const addMcpServer = (
  name: string,
  command: string,
  args: string[],
) => rawInvoke<void>("add_mcp_server", { name, command, args });

export const removeMcpServer = (name: string) =>
  rawInvoke<void>("remove_mcp_server", { name });

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

// ── prefs (BL-COMPANION-PREFS-TOGGLES 5/20) ───────────────
// 读 email scheduler 当前 effective 配置 (truth source: ~/.catfish/companion.yaml).
// AgentPrefsCard 显当前 rate_enabled 状态 + 打开 yaml 按钮.
export const emailConfigGet = () =>
  rawInvoke<EmailConfigPublic>("email_config_get");

export interface EmailConfigPublic {
  poll_secs: number;
  rate_enabled: boolean;
  rate_model: string;
  yaml_path: string;
}

// ── journal TODO (BL-JOURNAL-TODO-EXTRACT 5/20) ───────────
// Rust regex 抽 ~/.catfish/employee_journal.md 未完成 TODO. 返 JSON 字符串.
export const journalTodosFetch = () =>
  rawInvoke<string>("journal_todos_fetch");

/** BL-JOURNAL-TODO-EXTRACT step2 (5/20): 读 journal 最近 5KB 给 LLM 抽自然语言 TODO. */
export const journalReadRecent = () =>
  rawInvoke<string>("journal_read_recent");

// ── proactive context (BL-PROACTIVE-DECOUPLE 5/26) ────────
//
// 给 /api/proactive/starter + /api/proactive/contextual header 透传准备:
//   - X-Catfish-Journal-Tail-B64: base64(journal_tail)
//   - X-Catfish-Last-Model:        last_model
//
// 5/26 audit 砍 gateway 自读员工 fs / state.db 后, 由 Companion (员工 mac 本地
// 跑, 读自己 fs 合规) 准备好, 通过 header 传给 gateway. caller 见 lib/me.ts.

export interface ProactiveContextInfo {
  /** ~/.catfish/employee_journal.md 末尾 ~8KB (UTF-8, 切到 char boundary 不切坏中文). */
  journal_tail: string;
  /** hermes state.db 最近 session 用的 model name. 无 → null. */
  last_model: string | null;
}

export const fetchProactiveContext = () =>
  rawInvoke<ProactiveContextInfo>("proactive_context");

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

// ── /goal 已 deprecated 5/26 ─────────────────────────────
// BL-BRIEFING-GOAL-INPUT (5/20) 的 BriefingCard 🎯 输入框其实从未 ship UI 闭环
// (Tauri 后端 + 这 3 个 wrapper 写了, 但 React 组件没接). 5/26 鸿波拍板砍 —
// hermes 0.14 原生 /goal + /subgoal (#25449) 替代. 员工在 chat 直接输 /goal xxx.
// 3 个 export 删除, Tauri 后端改 stub 返 error 防回归.

// P3.4.7b (6/15 鸿波): origin 路由跟 mark/delete 同款 — 默认 weekly (current_todos.md),
//   section 给了隐式走 journal (employee_journal.md 流水帐), 显式 origin 优先.
export const journalAddTodo = (
  text: string,
  section?: string,
  origin?: "weekly" | "journal",
) =>
  rawInvoke<string>("journal_add_todo", {
    text,
    section: section ?? null,
    origin: origin ?? null,
  });

export interface JournalTodo {
  text: string;
  line: number;        // 1-based 行号
  source: "checkbox" | "inline";
  section: string;     // 所在段标题
  // 5/21 加: 员工 markdown 里 ⭐ / 🔝 / "重点:" 前缀 → is_priority=true
  // text 字段已去除前缀, UI 看到 is_priority 自己打 ⭐ 标. 早安播报 priority 项排序置顶.
  // 后端 Rust 端 (src-tauri/src/commands/journal.rs) 跟着加 serde 字段, 没加就 undefined.
  is_priority?: boolean;
  // P3.4.7a (6/15 鸿波): 来源文件 — "weekly" (current_todos.md) | "journal"
  //   (employee_journal.md 流水帐, 向后兼容). UI 可按 origin 分组显 "本周" / "流水帐".
  //   老 Rust 不返该字段时 undefined, UI 容忍.
  origin?: "weekly" | "journal";
}

// ── BL-BRIEFING-DECISION (5/21 Phase 5): 综合判断上下文包 ─────────────

export interface SessionBrief {
  id: string;
  title: string;
  startedAt: string;
  firstUserMessage: string;
  messageCount: number;
}

export interface WeeklyReportRef {
  filename: string;
  modifiedAt: string;
}

export interface BriefingContext {
  distilledFacts: string;          // ~/.catfish/distilled_facts.md 全文
  recentSessionBriefs: SessionBrief[];  // 最近 7 天 sessions (Phase 6 扩到周维度)
  // sessionGoal 5/26 删 — hermes 0.14 原生 /goal 替代, advisor 不再读 catfish 这套
  // 5/21 Phase 6 新加 3 个数据源
  workplan: string;                 // ~/.catfish/workplan.md 员工自写本周/本月计划
  projects: string;                 // ~/.catfish/projects.md 项目进度
  weeklyReports: WeeklyReportRef[]; // outputs/ 下 weekly-* 文件 + mtime
  // P3.4.6 (6/15 鸿波): hermes MEMORY 近期 § 段, 当"近期事项 context" 喂 advisor.
  //   不当 TODO — todo 严格走 employee_journal - [ ] checkbox.
  hermesMemoryRecent: string;
}

export const briefingContextFetch = () =>
  rawInvoke<BriefingContext>("briefing_context_fetch", {});

// ── calendar (BL-CALENDAR-INTEGRATION 5/20) ───────────────
// osascript JXA shell out 到 Calendar.app, 返今日 events JSON 字符串.
// 前端 JSON.parse 取 CalendarEvent[] 字段.
//
// BL-CALENDAR-INTEGRATION step2 (5/20): Rust 端 5 分钟内存缓存.
// 默认 (forceRefresh=false) 走缓存; ⟳ 按钮传 true 跳缓存强制刷.
export const calendarTodayFetch = (forceRefresh = false) =>
  rawInvoke<string>("calendar_today_fetch", { forceRefresh });

// BL-CALENDAR-WEEK (5/20): 未来 7 天 events (今天 0 点 — 7 天后). 同 5min 缓存.
export const calendarWeekFetch = (forceRefresh = false) =>
  rawInvoke<string>("calendar_week_fetch", { forceRefresh });

export interface CalendarEvent {
  calendar: string;     // 日历名 (Home / Work / 节假日 ...)
  summary: string;      // 事件标题
  start: string;        // ISO-8601
  end: string;          // ISO-8601
  all_day: boolean;     // 全天事件 (start/end 仍 ISO 但忽略时间)
  location?: string;    // 可选, 没填空字符串
  // BL-COMPANION-BRIEFING-V2 (5/20): 早安播报 v2 单条 event 展开时显
  // 没参会人 / 没描述 / 老 macOS 取不到 → 字段不存在 (JXA 仅在有值时设)
  attendees?: string[];  // 参会人 (displayName 优先, fallback emailAddress)
  description?: string;  // 事件描述 (JXA 端截 500 字)
}

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
// BL-EMAIL-COMPOSE-SEND (5/18): 把 Drafts 草稿真发. 红线: caller 必须人工 confirm.
// AI 不应该绕过 compose panel 直调这个.
export const emailSendMessage = (id: string) =>
  rawInvoke<string>("email_send_message", { id });

/** P3.5.103 (6/24 鸿波 catch '附件不能点'): 导出邮件附件到本地 tmp 文件, 返 path.
 *  前端拿到 path 调 openFile() 系统默认 app 打开 (Preview / Acrobat 等). */
export const emailExportAttachment = (id: string, filename: string) =>
  rawInvoke<string>("email_export_attachment", { id, filename });

// ── P3.5.105 (6/25 鸿波 catch '定时任务跑没跑结果如何都看不到'): cron 监控 ──

/** ~/.hermes/cron/jobs.json 真 schema (25 字段, nullable 严格按真数据). */
export interface CronJob {
  id: string;
  name: string;
  prompt?: string | null;
  skills: string[];
  skill?: string | null;
  model?: string | null;
  schedule?: { kind?: string; expr?: string; display?: string } | null;
  schedule_display?: string | null;
  repeat?: { times?: number | null; completed?: number } | null;
  enabled: boolean;
  state: string;
  paused_at?: string | null;
  paused_reason?: string | null;
  created_at?: string | null;
  next_run_at?: string | null;
  last_run_at?: string | null;
  last_status?: "ok" | "error" | string | null;
  last_error?: string | null;
  last_delivery_error?: string | null;
  deliver?: string | null;
  // P3.5.106 P27 (6/25): 自动重试 5/10/15 三档状态
  catfish_retry_attempt?: number | null; // 0/1/2/3
  catfish_retry_exhausted?: boolean | null; // 3 次都失败后 true
}

export interface CronOutputMeta {
  timestamp: string;
  size_bytes: number;
  snippet: string;
}

/** 读 ~/.hermes/cron/jobs.json 返 jobs[]. 0 hermes 不影响, 返空 list. */
export const cronJobsList = () => rawInvoke<CronJob[]>("cron_jobs_list");

/** 列单 job 历史 outputs (按 mtime 倒序, limit 默认 10). */
export const cronJobOutputs = (jobId: string, limit?: number) =>
  rawInvoke<CronOutputMeta[]>("cron_job_outputs", { jobId, limit });

/** 读单个 output .md 完整内容. */
export const cronJobOutputRead = (jobId: string, timestamp: string) =>
  rawInvoke<string>("cron_job_output_read", { jobId, timestamp });

/** POST /api/cron/jobs/{id}/pause 走 P26 endpoint (走 hermes Python public function). */
export const cronJobPause = (jobId: string, reason?: string) =>
  rawInvoke<void>("cron_job_pause", { jobId, reason });

/** POST /api/cron/jobs/{id}/resume */
export const cronJobResume = (jobId: string) =>
  rawInvoke<void>("cron_job_resume", { jobId });

/** DELETE /api/cron/jobs/{id} — 真删 jobs.json 条目 + 清 output. */
export const cronJobDelete = (jobId: string) =>
  rawInvoke<void>("cron_job_delete", { jobId });

// BL-COMPANION-HERMES-API-CONFIG (5/19 Phase 2-2A): 暴露 hermes API server 配置
// 给 chat.ts. 不返 key (key 在 Rust 端拼 header, 不发 JS, 防 XSS / 误 log).
// Phase 2-2B 实施 chat.ts 切换时读这个判断走 gateway 还是 hermes.
export interface HermesApiConfigPublic {
  enabled: boolean;
  url: string;
  has_key: boolean;
}
export const hermesApiConfigGet = () =>
  rawInvoke<HermesApiConfigPublic>("hermes_api_config_get");
// BL-COMPANION-CHAT-SWITCH-TO-HERMES (5/19 Phase 2-2B): 拿完整 "Bearer <key>"
// 字符串塞 fetch headers. enabled=false 或没 key → 返 null, caller fallback gateway.
export const hermesApiAuthHeader = () =>
  rawInvoke<string | null>("hermes_api_auth_header");
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
  // P3.5.58 (6/22 鸿波 catch "有回复了为啥还让小鲶处理"): RFC 822 thread chain
  // 三件套, 让 isReplied(msg, list) 算法可靠判定 — ∃ R: R.in_reply_to ==
  // M.message_id OR M.message_id ∈ R.references.split().
  // 老邮件 / 老 Mail.app 版本可能为 undefined, 算法兜空.
  message_id?: string;   // RFC 822 Message-ID, 形如 <abc@x.com>
  in_reply_to?: string;  // RFC 822 In-Reply-To, 直接父级 Message-ID
  references?: string;   // RFC 822 References, 空格分隔的完整祖先链
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
