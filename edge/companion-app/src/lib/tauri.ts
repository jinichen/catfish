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
export const journalMarkTodoDone = (line: number, textHint: string) =>
  rawInvoke<string>("journal_mark_todo_done", { line, textHint });

export const journalDeleteTodo = (line: number, textHint: string) =>
  rawInvoke<string>("journal_delete_todo", { line, textHint });

// ── /goal 已 deprecated 5/26 ─────────────────────────────
// BL-BRIEFING-GOAL-INPUT (5/20) 的 BriefingCard 🎯 输入框其实从未 ship UI 闭环
// (Tauri 后端 + 这 3 个 wrapper 写了, 但 React 组件没接). 5/26 鸿波拍板砍 —
// hermes 0.14 原生 /goal + /subgoal (#25449) 替代. 员工在 chat 直接输 /goal xxx.
// 3 个 export 删除, Tauri 后端改 stub 返 error 防回归.

export const journalAddTodo = (text: string, section?: string) =>
  rawInvoke<string>("journal_add_todo", { text, section: section ?? null });

export interface JournalTodo {
  text: string;
  line: number;        // 1-based 行号
  source: "checkbox" | "inline";
  section: string;     // 所在段标题
  // 5/21 加: 员工 markdown 里 ⭐ / 🔝 / "重点:" 前缀 → is_priority=true
  // text 字段已去除前缀, UI 看到 is_priority 自己打 ⭐ 标. 早安播报 priority 项排序置顶.
  // 后端 Rust 端 (src-tauri/src/commands/journal.rs) 跟着加 serde 字段, 没加就 undefined.
  is_priority?: boolean;
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
