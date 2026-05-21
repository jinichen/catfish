/** BriefingCard / EventsDetail 用的纯函数 helper, 不带 UI 渲染. */

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

// ── 格式化 (EventsDetail 用) ─────────────────────────────────────

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
