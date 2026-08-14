/**
 * 早安与日程 · journal / proactive context / 决策 / 日历 / 邮件摘要
 *
 * 2026-08-15 从 lib/tauri.ts 切出来 (1309 行超限)。纯搬迁, 逻辑一行未改。
 * 边界照抄原文件里作者早就画好的 `// ── xxx ──` 分节, 不是我另起的划分。
 *
 * lib/tauri.ts 现在是 barrel, 只做 re-export —— 62 个调用方一行没动。
 */

import { invoke as rawInvoke } from "@tauri-apps/api/core";

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
// P3.5.204.b (7/9): folder 参数支持 (默认 Inbox, EmailTab 传 "Sent" 拉发件箱
// 补 isReplied 数据源, 让 replied badge 能对回复过的收件邮件正确显示).
export const emailListFetch = (unreadOnly: boolean, limit?: number, folder?: string) =>
  rawInvoke<string>("email_list_fetch", {
    unreadOnly,
    limit: limit ?? null,
    folder: folder ?? null,
  });
export const emailReadMessage = (id: string) =>
  rawInvoke<string>("email_read_message", { id });
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
