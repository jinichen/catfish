/** BriefingCard helpers — 抽自原 BriefingCard.tsx (5/20 拆分超 800 行规则).
 *
 * 纯函数 + format/summary helper, 不带 UI 渲染.
 * 渲染组件在隔壁: EmailsDetail.tsx / EventsDetail.tsx / TodosDetail.tsx /
 * BriefingRow.tsx / GoalInput.tsx.
 */

import type { CalendarEvent, EmailDigestItem, JournalTodo } from "../../../lib/tauri";

// ── 时段问候 ────────────────────────────────────────────────────

export function getGreeting(): string {
  const h = new Date().getHours();
  if (h < 6) return "夜深了, 早点休息";
  if (h < 11) return "早上好";
  if (h < 14) return "中午好";
  if (h < 18) return "下午好";
  if (h < 22) return "晚上好";
  return "夜深了";
}

// ── 格式化 ─────────────────────────────────────────────────────

/** ISO → "HH:MM" 时区按本机. */
export function formatTime(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleTimeString("zh-CN", {
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    });
  } catch {
    return "";
  }
}

/** ISO start/end → "5月20日 周二 14:00 — 15:30 (1.5h)" */
export function formatFullDateRange(startISO: string, endISO: string): string {
  try {
    const s = new Date(startISO);
    const e = new Date(endISO);
    const dateLabel = s.toLocaleDateString("zh-CN", {
      month: "long",
      day: "numeric",
      weekday: "short",
    });
    const sTime = s.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false });
    const eTime = e.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false });
    const durMin = Math.round((e.getTime() - s.getTime()) / 60000);
    const durLabel = durMin >= 60
      ? `${Math.floor(durMin / 60)}h${durMin % 60 ? ` ${durMin % 60}min` : ""}`
      : `${durMin}min`;
    return `${dateLabel} ${sTime} — ${eTime} (${durLabel})`;
  } catch {
    return `${startISO} — ${endISO}`;
  }
}

/** "2026-05-21" → "明天 (5月21日 周四)" / 远日期返 "5月25日 周一". */
export function formatDayLabel(day: string): string {
  try {
    const target = new Date(day + "T00:00:00");
    const now = new Date();
    now.setHours(0, 0, 0, 0);
    const diffDays = Math.round((target.getTime() - now.getTime()) / (1000 * 60 * 60 * 24));
    const ymd = target.toLocaleDateString("zh-CN", {
      month: "long",
      day: "numeric",
      weekday: "short",
    });
    if (diffDays === 0) return `今天 (${ymd})`;
    if (diffDays === 1) return `明天 (${ymd})`;
    if (diffDays === 2) return `后天 (${ymd})`;
    if (diffDays > 0 && diffDays < 7) return `${diffDays} 天后 (${ymd})`;
    return ymd;
  } catch {
    return day;
  }
}

/** "张三 <zhang@x.com>" → "张三". 没显示名 → 邮箱 @ 前部分. */
export function extractSenderName(sender: string): string {
  if (!sender) return "(未知)";
  const m = sender.match(/^([^<]+?)\s*<.+>$/);
  if (m) return m[1].trim();
  if (sender.includes("@")) return sender.split("@")[0];
  return sender;
}

// ── 4 行总览 summary ───────────────────────────────────────────

export function emailSummary(
  unread: number | null,
  loading: boolean,
  err: string | null,
  emails: EmailDigestItem[] | null,
  urgencyMap: Record<string, string>,
): string {
  if (err) return "拉取失败 (Mail.app 没开 / 没装 CLI / 没权限)";
  if (loading && unread === null) return "同步中…";
  if (unread === null) return "—";
  if (unread === 0) return "🌊 收件箱已清空";

  // BL-COMPANION-EMAIL-DIGEST-STEP2 (5/20): LLM 评级分布
  if (emails && emails.length > 0) {
    const stats = { urgent: 0, medium: 0, low: 0, unrated: 0 };
    for (const m of emails) {
      const u = urgencyMap[m.id];
      if (u === "急" || u?.toLowerCase() === "urgent") stats.urgent++;
      else if (u === "低" || u?.toLowerCase() === "low") stats.low++;
      else if (u === "中" || u?.toLowerCase() === "medium") stats.medium++;
      else stats.unrated++;
    }
    if (stats.urgent + stats.medium + stats.low > 0) {
      const parts: string[] = [];
      if (stats.urgent > 0) parts.push(`🔴 急 ${stats.urgent}`);
      if (stats.medium > 0) parts.push(`🟡 中 ${stats.medium}`);
      if (stats.low > 0) parts.push(`🔵 低 ${stats.low}`);
      if (stats.unrated > 0) parts.push(`未评 ${stats.unrated}`);
      return `未读 ${unread} 封 · ${parts.join(" / ")}`;
    }
    return `未读 ${unread} 封 · 评级中…`;
  }
  return `未读 ${unread} 封`;
}

export function calendarSummary(
  evts: CalendarEvent[] | null,
  loading: boolean,
  err: string | null,
): string {
  if (err) {
    const short = err.length > 60 ? err.slice(0, 60) + "…" : err;
    return `拉取失败: ${short}`;
  }
  if (loading && evts === null) return "同步中…";
  if (evts === null) return "—";
  if (evts.length === 0) return "🍃 今天没排事";
  const next = evts.find((e) => !e.all_day);
  if (!next) return `${evts.length} 件 (都是全天)`;
  const startTime = formatTime(next.start);
  return `${evts.length} 件 · 下一件 ${startTime} ${next.summary}`;
}

/** Rule-based 优先级 (BL-BRIEFING-LLM-RANK step1 fallback).
 * 1. 1h 内日历 → "马上 HH:MM [summary]"
 * 2. 未读邮件 > 0
 * 3. inline TODO (journal "TODO:") 优先, 没就最新 checkbox
 * 没击中 → "今天比较松, 喝杯水?"
 */
export function suggestionSummary(
  unread: number | null,
  events: CalendarEvent[] | null,
  todos: JournalTodo[] | null,
): string {
  const parts: string[] = [];

  if (events && events.length > 0) {
    const now = Date.now();
    const oneHourLater = now + 60 * 60 * 1000;
    const imminent = events.find((e) => {
      if (e.all_day) return false;
      const ts = new Date(e.start).getTime();
      return ts >= now && ts <= oneHourLater;
    });
    if (imminent) {
      parts.push(`一会儿 ${formatTime(imminent.start)} ${imminent.summary}`);
    }
  }

  if ((unread ?? 0) > 0) {
    parts.push(`处理 ${unread} 封邮件`);
  }

  if (todos && todos.length > 0) {
    const inline = todos.find((t) => t.source === "inline");
    if (inline) {
      parts.push(`journal: ${inline.text}`);
    } else if (todos.length > 0) {
      parts.push(`journal: ${todos[todos.length - 1].text}`);
    }
  }

  if (parts.length === 0) {
    return "今天比较松, 喝杯水?";
  }
  return parts.slice(0, 3).join(" → ");
}

export function todoSummary(
  todos: JournalTodo[] | null,
  loading: boolean,
  err: string | null,
): string {
  if (err) {
    const short = err.length > 60 ? err.slice(0, 60) + "…" : err;
    return `读 journal 失败: ${short}`;
  }
  if (loading && todos === null) return "同步中…";
  if (todos === null) return "—";
  if (todos.length === 0) return "🍃 journal 没记未完成事项";
  const regexCount = todos.filter((t) => t.section !== "(LLM 推断)").length;
  const llmCount = todos.length - regexCount;
  const latest = todos[todos.length - 1];
  const llmTag = latest.section === "(LLM 推断)" ? " (LLM 推)" : "";
  if (llmCount > 0) {
    return `${todos.length} 件 (${regexCount} 显式 + ${llmCount} LLM 推) · 最新: ${latest.text}${llmTag}`;
  }
  return `${todos.length} 件未完 · 最新: ${latest.text}`;
}
