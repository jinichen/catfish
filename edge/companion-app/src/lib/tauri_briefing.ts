/**
 * 早安与日程 · Reminders / journal context / 决策 / 日历 / 邮件摘要
 *
 * 2026-08-15 从 lib/tauri.ts 切出来 (1309 行超限)。纯搬迁, 逻辑一行未改。
 * 边界照抄原文件里作者早就画好的 `// ── xxx ──` 分节, 不是我另起的划分。
 *
 * lib/tauri.ts 现在是 barrel, 只做 re-export —— 62 个调用方一行没动。
 */

import { invoke as rawInvoke } from "@tauri-apps/api/core";
import { toolBridgeCallTool } from "./tauri_services";

// ── 用户任务库 · Reminders.app 同步 ──────────────────────
// 9/8: 本机任务库是用户行动的事实源；macOS 上首次读取会导入 Reminders。
// 早安不再直接把周报或旧 current_todos.md 当作待办来源。

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


// ── /goal 已 deprecated 5/26 ─────────────────────────────
// BL-BRIEFING-GOAL-INPUT (5/20) 的 BriefingCard 🎯 输入框其实从未 ship UI 闭环
// (Tauri 后端 + 这 3 个 wrapper 写了, 但 React 组件没接). 5/26 鸿波拍板砍 —
// hermes 0.14 原生 /goal + /subgoal (#25449) 替代. 员工在 chat 直接输 /goal xxx.
// 3 个 export 删除, Tauri 后端改 stub 返 error 防回归.

export interface ReminderTodo {
  text: string;
  line: number; // Reminders 没有 markdown 行号，固定 0
  source: "reminder";
  section: string; // Reminders 清单名
  is_priority?: boolean;
  origin?: "reminders";
  reminder_id?: string;
  due_date_iso?: string | null;
  priority?: number;
  body?: string;
}

interface ReminderToolItem {
  task_id?: unknown;
  id?: unknown;
  title?: unknown;
  source?: unknown;
  source_id?: unknown;
  reminder_id?: unknown;
  list_name?: unknown;
  due_date_iso?: unknown;
  priority?: unknown;
  body?: unknown;
}

interface ReminderToolResult {
  ok?: boolean;
  error?: string;
  reminders?: ReminderToolItem[];
  tasks?: ReminderToolItem[];
}

/** 读取全部未完成任务，复用早安诊断解析链；截止时间只用于排序。 */
export async function remindersWeekFetch(): Promise<string> {
  const dispatched = await toolBridgeCallTool("catfish_list_tasks", {
    scope: "active",
    include_completed: false,
    limit: 100,
  });
  if (!dispatched.ok) {
    throw new Error(dispatched.error || "Reminders 工具调用失败");
  }

  const result = dispatched.result as ReminderToolResult | null;
  if (!result?.ok) {
    throw new Error(result?.error || "读取 Reminders 失败");
  }
  if (!Array.isArray(result.tasks)) {
    throw new Error("任务库返回格式异常：tasks 不是数组");
  }

  const todos: ReminderTodo[] = result.tasks
    .filter((item) => typeof item.title === "string" && item.title.trim().length > 0)
    .map((item) => {
      const priority = typeof item.priority === "number" ? item.priority : 0;
      return {
        text: (item.title as string).trim(),
        line: 0,
        source: "reminder",
        section: typeof item.list_name === "string" ? item.list_name : "Reminders",
        is_priority: priority >= 1 && priority <= 3,
        origin: "reminders",
        reminder_id: typeof item.reminder_id === "string"
          ? item.reminder_id
          : typeof item.source_id === "string" && item.source === "reminders"
            ? item.source_id
            : typeof item.id === "string" ? item.id : undefined,
        due_date_iso: typeof item.due_date_iso === "string" ? item.due_date_iso : null,
        priority,
        body: typeof item.body === "string" ? item.body : "",
      };
    });
  return JSON.stringify(todos);
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
  //   不当 TODO — 用户待办严格走本机任务库，macOS Reminders 只是同步投影.
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

// BL-CALENDAR-WEEK (5/20): 本自然周 events (周一 0 点 — 下周一 0 点). 同 5min 缓存.
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
export interface EmailSourceDiscovery {
  platform: string;
  ready_client: "outlook-win" | "foxmail-win" | null;
  selected_client?: "outlook-win" | "foxmail-win" | null;
  sources: Array<{
    client: string;
    status: "ready" | "unavailable" | "unsupported";
    accounts: Array<{ name: string; address: string; is_default: boolean; client: string }>;
    root: string | null;
    reason: string | null;
  }>;
}
export const emailSourcesDiscover = () =>
  rawInvoke<string>("email_sources_discover");
export const emailSourcePickDirectory = () =>
  rawInvoke<string | null>("email_source_pick_directory");
export const emailSourceSelect = (client: string, root?: string) =>
  rawInvoke<void>("email_source_select", { client, root: root ?? null });
// BL-COMPANION-EMAIL-TAB (5/18): 邮件 tab 用的全列表 + 读单封 + 起草 + 评级 map.
// P3.5.204.b (7/9): folder 参数支持 (默认 Inbox, EmailTab 传 "Sent" 拉发件箱
// 补 isReplied 数据源, 让 replied badge 能对回复过的收件邮件正确显示).
export const emailListFetch = (unreadOnly: boolean, limit?: number, folder?: string) =>
  rawInvoke<string>("email_list_fetch", {
    unreadOnly,
    limit: limit ?? null,
    folder: folder ?? null,
  });
export const emailReadMessage = (id: string, options?: { markRead?: boolean }) =>
  rawInvoke<string>("email_read_message", {
    id,
    mark_read: options?.markRead ?? null,
  });
// P3.5.204.c (7/9 鸿波 catch "客户端还没同步的邮件在鲶鱼里无法激活客户端去同步"):
// 触发 catfish-email check → Apple Mail 立即 IMAP/POP fetch. EmailTab 刷新按钮
// 先 check 再 refetch. account 空 = 全账号.
export const emailCheckNew = (account?: string) =>
  rawInvoke<string>("email_check_new", { account: account ?? null });
/** ~/Library/Mail 读不读得到 —— "ok" | "no_access" | "n/a"。
 *
 *  8/8: `no_access` 意味着 Apple Mail 里有账号但**进程没权限读**, 表现是邮件页
 *  少了几个邮箱而界面上什么都不说 (鸿波实撞: 授权前 1 个账号, 授权后 5 个)。
 *  这条只用来挂提示, 不影响取数 —— 取数那边已经会安静降级到 Foxmail。 */
export const emailMailDirStatus = () =>
  rawInvoke<string>("email_mail_dir_status");
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

/** 回复拟稿用：本地解析附件预览，原始附件不进入上传目录。 */
export interface EmailAttachmentPreview {
  filename: string;
  ext: string;
  kind: string;
  preview_text: string;
  preview_chars: number;
  meta: Record<string, unknown>;
  kept_path: string;
  parsed_text_path?: string;
}
export const emailAttachmentPreview = (id: string, filename: string) =>
  rawInvoke<EmailAttachmentPreview>("email_attachment_preview", { id, filename });
